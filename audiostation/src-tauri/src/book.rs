//! Loading the audiobook folder, diffing a reload against what is in
//! memory, sweeping orphans, and writing state — the only files this
//! program writes (design §2, §10.2, §10.3).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::types::{
    resolve_in, Book, Chapter, ChapterState, LoadedChapter, MadeFrom, Master, Pronunciation,
    Section, SectionChange, Speech, AUDIO_DIR, BOOK_FILE, ORPHANED_DIR, STATE_DIR,
};

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{} is not the JSON audiostation expects: {e}",
                                                     path.display()))
}

fn load_state(dir: &Path, stem: &str) -> ChapterState {
    let path = dir.join(STATE_DIR).join(format!("{stem}.json"));
    if !path.exists() {
        return ChapterState::default();
    }
    match read_json::<ChapterState>(&path) {
        Ok(s) => s,
        Err(e) => {
            log::warn!("[book] {e} — starting that chapter's state empty");
            ChapterState::default()
        }
    }
}

/// The encoder recipe every stitch uses. Bump when the ffmpeg command
/// changes so every stitched file reads as stale.
pub const STITCH_RECIPE: &str = "v2:44100Hz:mono:192k-cbr:loudnorm(I=-20,TP=-3,LRA=11)";

/// FNV-1a over the chapter's section sequence (ids in order, silence
/// durations) and the recipe. Deterministic across runs and versions,
/// which `DefaultHasher` is not.
pub fn stitch_key(chapter: &Chapter) -> String {
    let mut h: u64 = 0xcbf29ce484222325;
    let mut feed = |bytes: &[u8]| for b in bytes { h ^= *b as u64; h = h.wrapping_mul(0x100000001b3); };
    feed(STITCH_RECIPE.as_bytes());
    for s in &chapter.sections {
        feed(b"\x1f");
        match s {
            Section::Speech(sp) => feed(sp.id.as_bytes()),
            Section::Silence(si) => feed(format!("silence:{}", si.duration_ms).as_bytes()),
        }
    }
    format!("{h:016x}")
}

fn load_chapter(dir: &Path, rel: &str) -> Result<LoadedChapter, String> {
    let chapter: Chapter = read_json(&dir.join(rel))?;
    let state = load_state(dir, &chapter.stem);
    let key = stitch_key(&chapter);
    let mut loaded = LoadedChapter { chapter, state, changes: HashMap::new(), removed: vec![], added: false,
                                     stitch_key: key };
    flags_from_state(&mut loaded, dir);
    Ok(loaded)
}

/// Does any generated file exist for this id, at any quality?
fn has_audio(chapter: &LoadedChapter, dir: &Path, id: &str) -> bool {
    chapter.state.sections.get(id)
        .map(|st| st.audio_files.values().any(|rel| resolve_in(dir, rel).exists()))
        .unwrap_or(false)
}

/// Cold flags: for every speech section without audio, find the most
/// recent take whose recorded text is this text and name what differs
/// from it. Needs no previous load — the state is the memory.
pub fn flags_from_state(chapter: &mut LoadedChapter, dir: &Path) {
    let speech: Vec<Speech> = chapter.speech().into_iter().cloned().collect();
    let mut flags: HashMap<String, SectionChange> = HashMap::new();
    for s in &speech {
        if has_audio(chapter, dir, &s.id) { continue; }
        let f = fold(&s.text);
        let best = chapter.state.sections.iter()
            .filter_map(|(id, st)| st.made_from.as_ref().map(|m| (id, st, m)))
            .filter(|(id, _st, m)| id.as_str() != s.id && fold(&m.text) == f)
            .max_by(|a, b| a.1.generated_at.cmp(&b.1.generated_at));
        if let Some((_id, st, m)) = best {
            flags.insert(s.id.clone(), SectionChange {
                kind: "params".into(), detail: param_diff(m, &MadeFrom::from(s)),
                since: st.generated_at.clone() });
        }
    }
    chapter.changes = flags;
}

/// Read the whole folder. `dir` is `_audio/` (or a folder containing
/// `audiobook.json`).
pub fn load(dir: &Path) -> Result<Master, String> {
    let dir = dir.canonicalize().map_err(|e| format!("{}: {e}", dir.display()))?;
    let book_path = dir.join(BOOK_FILE);
    if !book_path.exists() {
        return Err(format!("{} has no {BOOK_FILE} — open the manuscript's _audio/ folder \
                            after 'authorlm audio export'", dir.display()));
    }
    let book: Book = read_json(&book_path)?;
    if book.schema != crate::types::SCHEMA {
        return Err(format!("{BOOK_FILE} is schema {}; this audiostation reads schema {}",
                           book.schema, crate::types::SCHEMA));
    }
    let mut chapters = Vec::new();
    for c in &book.chapters {
        chapters.push(load_chapter(&dir, &c.file)?);
    }
    let slot = |rel: &Option<String>| -> Result<Option<LoadedChapter>, String> {
        match rel {
            Some(r) => Ok(Some(load_chapter(&dir, r)?)),
            None => Ok(None),
        }
    };
    Ok(Master {
        opening_credits: slot(&book.opening_credits)?,
        closing_credits: slot(&book.closing_credits)?,
        about_author: slot(&book.about_author)?,
        dir,
        book,
        chapters,
        book_changes: vec![],
    })
}

// ---------------------------------------------------------------------------
// State — the only thing written
// ---------------------------------------------------------------------------

pub fn save_state(dir: &Path, chapter: &LoadedChapter) -> Result<(), String> {
    let state_dir = dir.join(STATE_DIR);
    std::fs::create_dir_all(&state_dir).map_err(|e| format!("cannot create state dir: {e}"))?;
    let path = state_dir.join(format!("{}.json", chapter.chapter.stem));
    let json = serde_json::to_string_pretty(&chapter.state)
        .map_err(|e| format!("serialise state: {e}"))?;
    // Write-then-rename so a reader never sees a half-written file.
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, json).map_err(|e| format!("write {}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &path).map_err(|e| format!("rename {}: {e}", path.display()))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Diff — what changed on reload (design §10.3)
// ---------------------------------------------------------------------------

/// Display-only folding for pairing old and new sections: curly quotes
/// and dashes to ASCII, odd spaces to space, zero-width dropped,
/// whitespace collapsed, lowercase. Never feeds an id.
pub fn fold(text: &str) -> String {
    let mapped: String = text.chars().filter_map(|c| match c {
        '\u{2018}' | '\u{2019}' | '\u{02BC}' => Some('\''),
        '\u{201C}' | '\u{201D}' => Some('"'),
        '\u{2010}'..='\u{2015}' | '\u{2212}' => Some('-'),
        '\u{00AD}' | '\u{200B}' | '\u{200C}' | '\u{200D}' | '\u{FEFF}' => None,
        '\u{00A0}' | '\u{202F}' | '\u{2007}' | '\u{2060}' => Some(' '),
        c => Some(c),
    }).collect();
    mapped.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase()
}

/// Each pronunciation rule by name: what was added, what changed from
/// what to what, what was removed.
fn pronunciation_diff(old: &[Pronunciation], new: &[Pronunciation]) -> Vec<String> {
    let before: HashMap<&str, &str> = old.iter().map(|p| (p.term.as_str(), p.say.as_str())).collect();
    let after: HashMap<&str, &str> = new.iter().map(|p| (p.term.as_str(), p.say.as_str())).collect();
    let mut out = Vec::new();
    for p in new {
        match before.get(p.term.as_str()) {
            None => out.push(format!("added {} → {}", p.term, p.say)),
            Some(was) if *was != p.say => out.push(format!("{}: {} → {}", p.term, was, p.say)),
            _ => {}
        }
    }
    for p in old {
        if !after.contains_key(p.term.as_str()) { out.push(format!("removed {}", p.term)); }
    }
    out
}

fn param_diff(old: &MadeFrom, new: &MadeFrom) -> Vec<String> {
    let mut out = Vec::new();
    if old.voice_id != new.voice_id || old.cast != new.cast { out.push("voice".into()); }
    if old.model != new.model { out.push("model".into()); }
    if (old.stability - new.stability).abs() > 1e-9 { out.push("stability".into()); }
    if (old.similarity - new.similarity).abs() > 1e-9 { out.push("similarity".into()); }
    if (old.speed - new.speed).abs() > 1e-9 { out.push("speed".into()); }
    out.extend(pronunciation_diff(&old.pronunciations, &new.pronunciations));
    if out.is_empty() { out.push("parameters".into()); }
    out
}

#[derive(Debug, Clone, Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct ReloadSummary {
    pub changed_sections: usize,
    pub removed_sections: usize,
    pub added_chapters: Vec<String>,
    pub removed_chapters: Vec<String>,
    pub book_changes: Vec<String>,
    /// State entries (takes, stitches) that differ from what was in
    /// memory — the other writer's work (design §5, guard 1).
    pub takes_changed: usize,
}

impl ReloadSummary {
    /// Nothing moved that the window does not already show.
    pub fn is_quiet(&self) -> bool {
        self.changed_sections == 0 && self.removed_sections == 0
            && self.added_chapters.is_empty() && self.removed_chapters.is_empty()
            && self.book_changes.is_empty() && self.takes_changed == 0
    }
}

impl std::fmt::Display for ReloadSummary {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} section(s) changed, {} removed, {} take(s) changed, chapters +{} -{}, book: {}",
               self.changed_sections, self.removed_sections, self.takes_changed,
               self.added_chapters.len(), self.removed_chapters.len(),
               if self.book_changes.is_empty() { "unchanged".to_string() }
               else { self.book_changes.join(", ") })
    }
}

/// How many of the chapter's state entries (and its stitches) differ
/// between two loads: added, dropped, or re-recorded takes.
fn takes_diff(old: &ChapterState, new: &ChapterState) -> usize {
    let mut n = 0usize;
    for (id, st) in &new.sections {
        if old.sections.get(id) != Some(st) { n += 1; }
    }
    n += old.sections.keys().filter(|id| !new.sections.contains_key(*id)).count();
    if old.stitched != new.stitched { n += 1; }
    n
}

fn diff_chapter(old: &LoadedChapter, new: &mut LoadedChapter, dir: &Path) -> (usize, usize) {
    let old_speech: Vec<&Speech> = old.speech();
    let old_by_id: HashMap<&str, &Speech> = old_speech.iter().map(|s| (s.id.as_str(), *s)).collect();
    let mut old_by_fold: HashMap<String, &Speech> = HashMap::new();
    for s in &old_speech {
        old_by_fold.entry(fold(&s.text)).or_insert(*s);
    }
    let new_ids: HashSet<String> = new.chapter.sections.iter().map(|s| s.id().to_string()).collect();
    let new_folds: HashSet<String> = new.speech().iter().map(|s| fold(&s.text)).collect();
    let mut changes: HashMap<String, SectionChange> = HashMap::new();
    // The flags the load derived from the state (what changed since the
    // last TAKE) outrank what this reload can see (what changed since the
    // last LOAD): the state saw every take, this comparison only the
    // previous file.
    let cold = std::mem::take(&mut new.changes);
    // Old sections a "text" change replaced at the same position: they are
    // the before-half of an edit, not a removal.
    let mut replaced: HashSet<String> = HashSet::new();
    let new_speech: Vec<Speech> = new.speech().into_iter().cloned().collect();
    // The summary counts what THIS reload found moved — not flags carried
    // from an earlier reload or derived from the state.
    let mut fresh = 0usize;
    for (i, s) in new_speech.iter().enumerate() {
        if old_by_id.contains_key(s.id.as_str()) {
            // Unchanged. A flag from an earlier reload survives until the
            // id has audio.
            if let Some(flag) = old.changes.get(&s.id) {
                let has_audio = new.state.sections.get(&s.id)
                    .map(|st| st.audio_files.values().any(|rel| crate::types::resolve_in(dir, rel).exists()))
                    .unwrap_or(false);
                if !has_audio {
                    changes.insert(s.id.clone(), flag.clone());
                }
            }
            continue;
        }
        fresh += 1;
        if let Some(prev) = old_by_fold.get(&fold(&s.text)) {
            changes.insert(s.id.clone(), SectionChange {
                kind: "params".into(),
                detail: param_diff(&MadeFrom::from(*prev), &MadeFrom::from(s)),
                since: old.state.sections.get(&prev.id).and_then(|st| st.generated_at.clone()) });
            continue;
        }
        let aligned = old_speech.get(i)
            .filter(|o| !new_ids.contains(&o.id) && !new_folds.contains(&fold(&o.text))
                        && !replaced.contains(&o.id));
        if let Some(o) = aligned {
            replaced.insert(o.id.clone());
            changes.insert(s.id.clone(), SectionChange { kind: "text".into(), detail: vec![], since: None });
        } else {
            changes.insert(s.id.clone(), SectionChange { kind: "new".into(), detail: vec![], since: None });
        }
    }
    for (id, flag) in cold {
        changes.insert(id, flag);
    }
    let removed: Vec<String> = old_speech.iter()
        .filter(|o| !new_ids.contains(&o.id) && !new_folds.contains(&fold(&o.text))
                    && !replaced.contains(&o.id))
        .map(|o| o.text.clone())
        .collect();
    let counts = (fresh, removed.len());
    new.changes = changes;
    new.removed = removed;
    counts
}

fn book_changes(old: &Book, new: &Book) -> Vec<String> {
    let mut out = Vec::new();
    let s = |a: &str, b: &str, name: &str, out: &mut Vec<String>| if a != b { out.push(name.to_string()) };
    s(&old.title, &new.title, "title", &mut out);
    s(&old.subtitle, &new.subtitle, "subtitle", &mut out);
    s(&old.author, &new.author, "author", &mut out);
    s(&old.narrator, &new.narrator, "narrator", &mut out);
    s(&old.publisher, &new.publisher, "publisher", &mut out);
    s(&old.language, &new.language, "language", &mut out);
    s(&old.copyright_holder, &new.copyright_holder, "copyright holder", &mut out);
    if old.copyright_year != new.copyright_year { out.push("copyright year".into()); }
    let order = |b: &Book| b.chapters.iter().map(|c| c.stem.clone()).collect::<Vec<_>>();
    if order(old) != order(new) { out.push("chapter order".into()); }
    if old.opening_credits != new.opening_credits || old.closing_credits != new.closing_credits {
        out.push("credits".into());
    }
    if old.about_author != new.about_author { out.push("about the author".into()); }
    if old.retail_sample != new.retail_sample { out.push("retail sample".into()); }
    if old.cover != new.cover { out.push("cover".into()); }
    if old.pronunciation_dictionary != new.pronunciation_dictionary { out.push("dictionary".into()); }
    if old.cast != new.cast { out.push("cast".into()); }
    if old.model != new.model { out.push("model".into()); }
    if old.quality != new.quality { out.push("quality".into()); }
    if old.paragraph_gap_ms != new.paragraph_gap_ms { out.push("paragraph gap".into()); }
    out
}

/// Fill `new`'s change flags from `old` and return the summary.
pub fn diff(old: &Master, new: &mut Master) -> ReloadSummary {
    let dir = new.dir.clone();
    let mut summary = ReloadSummary { book_changes: book_changes(&old.book, &new.book), ..Default::default() };
    let old_stems: HashSet<&str> = old.all().iter().map(|c| c.chapter.stem.as_str()).collect();
    let mut new_stems: HashSet<String> = HashSet::new();
    for chapter in new.all_mut() {
        let stem = chapter.chapter.stem.clone();
        new_stems.insert(stem.clone());
        match old.find(&stem) {
            Some(prev) => {
                let (changed, removed) = diff_chapter(prev, chapter, &dir);
                summary.changed_sections += changed;
                summary.removed_sections += removed;
                summary.takes_changed += takes_diff(&prev.state, &chapter.state);
            }
            None => {
                chapter.added = true;
                summary.added_chapters.push(stem);
            }
        }
    }
    summary.removed_chapters = old_stems.iter()
        .filter(|s| !new_stems.contains(**s))
        .map(|s| s.to_string())
        .collect();
    summary.removed_chapters.sort();
    new.book_changes = summary.book_changes.clone();
    summary
}

// ---------------------------------------------------------------------------
// Orphans — audio and state whose id no chapter references
// ---------------------------------------------------------------------------

fn is_section_file_id(name: &str) -> Option<&str> {
    let id = name.split('.').next()?;
    if id.len() == 32 && id.chars().all(|c| c.is_ascii_hexdigit()) { Some(id) } else { None }
}

/// Move audio files and state entries for ids no chapter references into
/// `_orphaned/<timestamp>/`. Returns how many files moved.
pub fn sweep_orphans(master: &mut Master) -> Result<usize, String> {
    let dir = master.dir.clone();
    let live: HashSet<String> = master.all().iter()
        .flat_map(|c| c.chapter.sections.iter().map(|s| s.id().to_string()))
        .collect();
    let live_stems: HashSet<String> = master.all().iter().map(|c| c.chapter.stem.clone()).collect();
    let stamp = chrono::Utc::now().format("%Y%m%d_%H%M%S").to_string();
    let orphan_dir = dir.join(ORPHANED_DIR).join(&stamp);
    let mut moved = 0usize;
    let mut park = |path: PathBuf| -> Result<(), String> {
        std::fs::create_dir_all(&orphan_dir).map_err(|e| format!("cannot create orphan dir: {e}"))?;
        let dest = orphan_dir.join(path.file_name().unwrap_or_default());
        std::fs::rename(&path, &dest).map_err(|e| format!("cannot move {}: {e}", path.display()))?;
        moved += 1;
        Ok(())
    };
    let audio_dir = dir.join(AUDIO_DIR);
    if audio_dir.exists() {
        for entry in std::fs::read_dir(&audio_dir).map_err(|e| e.to_string())? {
            let path = entry.map_err(|e| e.to_string())?.path();
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string();
            if let Some(id) = is_section_file_id(&name) {
                if !live.contains(id) { park(path)?; }
            }
        }
    }
    let state_dir = dir.join(STATE_DIR);
    if state_dir.exists() {
        for entry in std::fs::read_dir(&state_dir).map_err(|e| e.to_string())? {
            let path = entry.map_err(|e| e.to_string())?.path();
            let stem = path.file_stem().and_then(|n| n.to_str()).unwrap_or("").to_string();
            if path.extension().map_or(false, |e| e == "json") && !live_stems.contains(&stem) {
                park(path)?;
            }
        }
    }
    // State entries for ids that vanished: drop them (their files, if any,
    // were parked above) and save — EXCEPT an entry that is still the
    // memory of a live, ungenerated paragraph (same text, no take yet):
    // that entry is what lets the card say what changed since the last
    // take, and it goes when that paragraph is generated.
    for chapter in master.all_mut() {
        let before = chapter.state.sections.len();
        let ids: HashSet<String> = chapter.chapter.sections.iter().map(|s| s.id().to_string()).collect();
        let awaiting: HashSet<String> = chapter.speech().into_iter()
            .filter(|s| !chapter.state.sections.get(&s.id)
                .map(|st| st.audio_files.values().any(|rel| resolve_in(&dir, rel).exists()))
                .unwrap_or(false))
            .map(|s| fold(&s.text)).collect();
        chapter.state.sections.retain(|id, st| ids.contains(id)
            || st.made_from.as_ref().map(|m| awaiting.contains(&fold(&m.text))).unwrap_or(false));
        if chapter.state.sections.len() != before {
            save_state(&dir, chapter)?;
        }
    }
    if moved > 0 {
        log::info!("[book] {moved} orphaned file(s) moved to {}", orphan_dir.display());
    }
    Ok(moved)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{SectionState, Silence};

    fn fixture() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../authorlm/tests/fixtures/audiobook/_audio")
    }

    fn temp_copy() -> PathBuf {
        let root = std::env::temp_dir().join(format!("audiostation-test-{}-{}",
            std::process::id(), chrono::Utc::now().timestamp_nanos_opt().unwrap_or(0)));
        copy_dir(&fixture(), &root);
        root
    }

    fn copy_dir(from: &Path, to: &Path) {
        std::fs::create_dir_all(to).unwrap();
        for entry in std::fs::read_dir(from).unwrap() {
            let entry = entry.unwrap();
            let dest = to.join(entry.file_name());
            if entry.file_type().unwrap().is_dir() { copy_dir(&entry.path(), &dest); }
            else { std::fs::copy(entry.path(), dest).unwrap(); }
        }
    }

    #[test]
    fn a_take_the_other_writer_made_counts_as_a_changed_take() {
        // AuthorLM's `audio generate` writes the same state file; the
        // reload after it reports the take so the watcher can tell the
        // window, and our own save (state equal to memory) reports none.
        let old = load(&fixture()).unwrap();
        let mut same = load(&fixture()).unwrap();
        assert_eq!(diff(&old, &mut same).takes_changed, 0);
        assert!(diff(&old, &mut same).is_quiet());
        let mut new = load(&fixture()).unwrap();
        let ch = new.find_mut("kindness").unwrap();
        let first = ch.speech()[0].id.clone();
        ch.state.sections.insert(first, SectionState {
            audio_files: HashMap::from([("mp3_44100_192".to_string(),
                                         "audio/x.mp3_44100_192.mp3".to_string())]),
            request_id: Some("r".into()), generated_at: Some("2026-09-25T00:00:00Z".into()),
            made_from: None });
        let summary = diff(&old, &mut new);
        assert_eq!(summary.takes_changed, 1);
        assert!(!summary.is_quiet());
    }

    #[test]
    fn loads_the_fixture_in_book_order() {
        let m = load(&fixture()).unwrap();
        assert_eq!(m.chapters.iter().map(|c| c.chapter.stem.as_str()).collect::<Vec<_>>(),
                   vec!["sermons", "kindness"]);
        assert!(m.opening_credits.is_some() && m.closing_credits.is_some() && m.about_author.is_some());
        assert_eq!(m.chapters[0].state.sections.len(), 1, "state joined by stem");
        assert_eq!(m.all().len(), 5);
    }

    #[test]
    fn diff_pairs_by_id_then_text_then_position() {
        let old = load(&fixture()).unwrap();
        let mut new = load(&fixture()).unwrap();
        let summary = diff(&old, &mut new);
        assert_eq!(summary.changed_sections, 0);
        assert!(summary.book_changes.is_empty());

        // Same text, different parameters → "params" naming the field.
        let mut new = load(&fixture()).unwrap();
        {
            let ch = new.find_mut("sermons").unwrap();
            if let Some(Section::Speech(s)) = ch.chapter.sections.iter_mut().find(|s| matches!(s, Section::Speech(_))) {
                s.id = "ffffffffffffffffffffffffffffffff".into();
                s.stability = 0.9;
            }
        }
        let summary = diff(&old, &mut new);
        assert_eq!(summary.changed_sections, 1);
        let flag = &new.find("sermons").unwrap().changes["ffffffffffffffffffffffffffffffff"];
        assert_eq!(flag.kind, "params");
        assert_eq!(flag.detail, vec!["stability"]);

        // Different text at the same position → "text"; and the old text is not "removed".
        let mut new = load(&fixture()).unwrap();
        {
            let ch = new.find_mut("sermons").unwrap();
            if let Some(Section::Speech(s)) = ch.chapter.sections.iter_mut().find(|s| matches!(s, Section::Speech(_))) {
                s.id = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee".into();
                s.text = "A different heading".into();
            }
        }
        let summary = diff(&old, &mut new);
        let ch = new.find("sermons").unwrap();
        assert_eq!(ch.changes["eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"].kind, "text");
        assert_eq!(summary.removed_sections, 0);

        let fresh = |id: &str, text: &str| Section::Speech(Speech {
            id: id.into(), kind: "paragraph".into(), level: None, text: text.into(),
            cast: "herdsman".into(), voice_id: "v".into(), voice_name: "V".into(),
            model: "m".into(), stability: 0.5, similarity: 0.75, speed: 1.0,
            pronunciations: vec![], source: serde_json::Value::Null });

        // A section appended with nothing dropped → "new", nothing removed.
        let mut new = load(&fixture()).unwrap();
        {
            let ch = new.find_mut("sermons").unwrap();
            ch.chapter.sections.push(Section::Silence(Silence { id: "s".into(), duration_ms: 700 }));
            ch.chapter.sections.push(fresh("cccccccccccccccccccccccccccccccc", "Brand new closing paragraph."));
        }
        let summary = diff(&old, &mut new);
        assert_eq!(new.find("sermons").unwrap().changes["cccccccccccccccccccccccccccccccc"].kind, "new");
        assert_eq!(summary.removed_sections, 0);

        // The last paragraph dropped and another appended in its place: the
        // same position, different text → "text", and the dropped one is the
        // before-half of that edit rather than a removal.
        let mut new = load(&fixture()).unwrap();
        {
            let ch = new.find_mut("sermons").unwrap();
            // The chapter ends with its tail silence; drop through it to the
            // last paragraph, "A quoted line."
            let mut dropped = ch.chapter.sections.pop().unwrap();
            while matches!(dropped, Section::Silence(_)) { dropped = ch.chapter.sections.pop().unwrap(); }
            assert!(matches!(dropped, Section::Speech(_)));
            ch.chapter.sections.push(fresh("dddddddddddddddddddddddddddddddd", "Brand new closing paragraph."));
        }
        let summary = diff(&old, &mut new);
        let ch = new.find("sermons").unwrap();
        assert_eq!(ch.changes["dddddddddddddddddddddddddddddddd"].kind, "text");
        assert_eq!(summary.removed_sections, 0);

        // A paragraph dropped with nothing in its place → removed, by text.
        let mut shorter = load(&fixture()).unwrap();
        {
            let secs = &mut shorter.find_mut("sermons").unwrap().chapter.sections;
            while matches!(secs.last(), Some(Section::Silence(_))) { secs.pop(); }
            secs.pop();
        }
        let summary = diff(&old, &mut shorter);
        assert_eq!(summary.removed_sections, 1);
        assert_eq!(shorter.find("sermons").unwrap().removed[0], "A quoted line.");

        // Reloading the fixture over `new`: the dropped paragraph comes back
        // at the position the appended one held → "text".
        let mut again = load(&fixture()).unwrap();
        let summary = diff(&new, &mut again);
        assert_eq!(summary.changed_sections, 1);
        let back = again.find("sermons").unwrap();
        let (_id, flag) = back.changes.iter().next().unwrap();
        assert_eq!(flag.kind, "text");
        assert_eq!(summary.removed_sections, 0, "the appended paragraph is the before-half of that edit");
    }

    #[test]
    fn pronunciation_changes_are_named_per_term() {
        let rule = |t: &str, s: &str| Pronunciation { term: t.into(), say: s.into() };
        let old = load(&fixture()).unwrap();
        let old_first = old.find("sermons").unwrap().speech()[0].clone();
        let mut new = load(&fixture()).unwrap();
        {
            let ch = new.find_mut("sermons").unwrap();
            if let Some(Section::Speech(s)) = ch.chapter.sections.iter_mut().find(|s| matches!(s, Section::Speech(_))) {
                s.id = "abababababababababababababababab".into();
                s.pronunciations = vec![rule("Basilides", "basillydeez"), rule("Prohairesis", "pro-HY-ruh-sis")];
            }
        }
        let mut old2 = old.clone();
        if let Some(Section::Speech(s)) = old2.find_mut("sermons").unwrap().chapter.sections.iter_mut()
            .find(|s| s.id() == old_first.id) {
            s.pronunciations = vec![rule("Basilides", "buh-SIL-ih-deez"), rule("Abraxas", "uh-BRAK-suss")];
        }
        diff(&old2, &mut new);
        let flag = &new.find("sermons").unwrap().changes["abababababababababababababababab"];
        assert_eq!(flag.kind, "params");
        assert_eq!(flag.detail, vec!["Basilides: buh-SIL-ih-deez → basillydeez",
                                     "added Prohairesis → pro-HY-ruh-sis",
                                     "removed Abraxas"]);
        assert_eq!(flag.since.as_deref(), Some("2026-01-01T00:00:00Z"),
                   "measured against the fixture's take of the old id");
    }

    #[test]
    fn a_cold_load_names_what_changed_since_the_last_take() {
        // No previous load in memory: the export happened while the app was
        // closed. The state entry of the OLD id remembers what its take was
        // made from; the new id pairs to it by text.
        let root = temp_copy();
        let rule = |t: &str, s: &str| Pronunciation { term: t.into(), say: s.into() };
        let m = load(&root).unwrap();
        let ch = m.find("sermons").unwrap();
        let first = ch.speech()[0].clone();
        assert!(ch.changes.is_empty(), "a fixture with no made_from flags nothing");
        // Remember the take.
        let state_path = root.join(STATE_DIR).join("sermons.json");
        let mut state: ChapterState = read_json(&state_path).unwrap();
        let entry = state.sections.get_mut(&first.id).expect("the fixture's take");
        let mut made = MadeFrom::from(&first);
        made.pronunciations = vec![rule("Basilides", "buh-SIL-ih-deez")];
        entry.made_from = Some(made);
        std::fs::write(&state_path, serde_json::to_string_pretty(&state).unwrap()).unwrap();
        // The export moved the id: the reading changed and the speed moved.
        let chapter_path = root.join("chapters").join("sermons.json");
        let mut chapter: Chapter = read_json(&chapter_path).unwrap();
        if let Some(Section::Speech(s)) = chapter.sections.iter_mut().find(|s| s.id() == first.id) {
            s.id = "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd".into();
            s.speed = 0.92;
            s.pronunciations = vec![rule("Basilides", "basillydeez")];
        }
        std::fs::write(&chapter_path, serde_json::to_string_pretty(&chapter).unwrap()).unwrap();

        let m = load(&root).unwrap();
        let ch = m.find("sermons").unwrap();
        let flag = &ch.changes["cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"];
        assert_eq!(flag.kind, "params");
        assert_eq!(flag.detail, vec!["speed", "Basilides: buh-SIL-ih-deez → basillydeez"]);
        assert_eq!(flag.since.as_deref(), Some("2026-01-01T00:00:00Z"));

        // A reload over that load keeps the cold flag (it knows the take;
        // the reload only knows the previous file).
        let mut again = load(&root).unwrap();
        let summary = diff(&m, &mut again);
        assert_eq!(summary.changed_sections, 0, "nothing moved between the two loads");
        assert_eq!(again.find("sermons").unwrap().changes["cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"].detail,
                   vec!["speed", "Basilides: buh-SIL-ih-deez → basillydeez"]);

        // The sweep keeps the old id's entry while the new id awaits its
        // take: it is the memory the badge reads from.
        let mut swept = load(&root).unwrap();
        sweep_orphans(&mut swept).unwrap();
        let state: ChapterState = read_json(&state_path).unwrap();
        assert!(state.sections.contains_key(&first.id), "the memory survives the sweep");
        let m = load(&root).unwrap();
        assert!(m.find("sermons").unwrap().changes.contains_key("cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"));
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn book_level_changes_are_named() {
        let old = load(&fixture()).unwrap();
        let mut new = load(&fixture()).unwrap();
        new.book.narrator = "Someone Else".into();
        new.book.quality = "mp3_44100_192".into();
        new.book.chapters.reverse();
        let summary = diff(&old, &mut new);
        assert_eq!(summary.book_changes, vec!["narrator", "chapter order", "quality"]);
    }

    #[test]
    fn done_means_the_file_exists_and_state_saves_relative_paths() {
        let root = temp_copy();
        let mut m = load(&root).unwrap();
        let (id, rel) = {
            let ch = m.find("sermons").unwrap();
            let (id, st) = ch.state.sections.iter().next().unwrap();
            (id.clone(), st.audio_files["mp3_44100_128"].clone())
        };
        assert!(!m.find("sermons").unwrap().done(&m.dir, &id, "mp3_44100_128"));
        std::fs::create_dir_all(root.join(AUDIO_DIR)).unwrap();
        std::fs::write(root.join(&rel), b"mp3").unwrap();
        assert!(m.find("sermons").unwrap().done(&m.dir, &id, "mp3_44100_128"));
        {
            let dir = m.dir.clone();
            let ch = m.find_mut("sermons").unwrap();
            ch.state.stitched.insert("mp3_44100_128".into(), "audio/sermons.mp3_44100_128.mp3".into());
            save_state(&dir, ch).unwrap();
        }
        let text = std::fs::read_to_string(root.join(STATE_DIR).join("sermons.json")).unwrap();
        assert!(text.contains("\"audio/sermons.mp3_44100_128.mp3\""));
        assert!(!text.contains(root.to_string_lossy().as_ref()), "paths stay relative");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn orphan_sweep_parks_unreferenced_audio_and_stale_state() {
        let root = temp_copy();
        std::fs::create_dir_all(root.join(AUDIO_DIR)).unwrap();
        std::fs::write(root.join(AUDIO_DIR).join("abababababababababababababababab.mp3_44100_128.mp3"), b"x").unwrap();
        std::fs::write(root.join(AUDIO_DIR).join("sermons.mp3_44100_128.mp3"), b"stitched-stays").unwrap();
        std::fs::write(root.join(STATE_DIR).join("gone.json"), "{}").unwrap();
        let mut m = load(&root).unwrap();
        // A state entry for an id no chapter has.
        m.find_mut("sermons").unwrap().state.sections.insert("cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd".into(), Default::default());
        let moved = sweep_orphans(&mut m).unwrap();
        assert_eq!(moved, 2, "one audio file and one stale state file");
        assert!(root.join(AUDIO_DIR).join("sermons.mp3_44100_128.mp3").exists(), "stitched files are not ids");
        assert!(!m.find("sermons").unwrap().state.sections.contains_key("cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd"));
        let orphaned: Vec<_> = std::fs::read_dir(root.join(ORPHANED_DIR)).unwrap().collect();
        assert_eq!(orphaned.len(), 1);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn stitch_key_follows_sections_and_recipe() {
        let m = load(&fixture()).unwrap();
        let ch = m.find("sermons").unwrap();
        let k1 = stitch_key(&ch.chapter);
        assert_eq!(k1, ch.stitch_key);
        // The Python twin (authorlm/generation.py::stitch_key) asserts the
        // same literal on the same fixture: both programs must agree on
        // whether a stitched file is stale.
        assert_eq!(k1, "bf49100e19686707");
        let mut altered = ch.chapter.clone();
        if let Some(Section::Silence(s)) = altered.sections.iter_mut().find(|s| matches!(s, Section::Silence(_))) {
            s.duration_ms += 100;
        }
        assert_ne!(stitch_key(&altered), k1, "a silence change moves the key");
        let mut reordered = ch.chapter.clone();
        reordered.sections.reverse();
        assert_ne!(stitch_key(&reordered), k1, "order is part of the key");
    }

    #[test]
    fn fold_is_display_only() {
        assert_eq!(fold("“Harken” — said he"), "\"harken\" - said he");
        assert_eq!(fold("a\u{00A0}b   c"), "a b c");
    }
}
