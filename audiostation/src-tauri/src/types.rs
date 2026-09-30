//! The audiobook folder, as AuthorLM writes it and audiostation reads it
//! (docs/audiobook-pipeline-design.md §2, §14).
//!
//! Two writers, two kinds of file, never the same file:
//!
//!   _audio/audiobook.json      AuthorLM writes  → `Book`
//!   _audio/chapters/<stem>.json AuthorLM writes → `Chapter`
//!   _audio/state/<stem>.json   audiostation writes → `ChapterState`
//!   _audio/audio/              audiostation writes
//!
//! The shared key is the section id: a hash AuthorLM computes over the
//! text, the resolved voice parameters and the applicable pronunciation
//! rules. Same id, same audio. A section is "done" when its id has a
//! state entry whose file exists. There is no dirty flag anywhere.
//!
//! The JSON fixture both test suites read lives at
//! `authorlm/tests/fixtures/audiobook/`; a field renamed here fails the
//! Python suite in the same commit.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

pub const BOOK_FILE: &str = "audiobook.json";
pub const CHAPTERS_DIR: &str = "chapters";
pub const STATE_DIR: &str = "state";
pub const AUDIO_DIR: &str = "audio";
pub const SILENCE_DIR: &str = "silence";
pub const ORPHANED_DIR: &str = "_orphaned";
pub const ACX_DIR: &str = "acx";
pub const SCHEMA: u8 = 1;

// ---------------------------------------------------------------------------
// audiobook.json — written by AuthorLM
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Book {
    pub schema: u8,
    pub title: String,
    #[serde(default)]
    pub subtitle: String,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub narrator: String,
    #[serde(default)]
    pub publisher: String,
    #[serde(default = "default_language")]
    pub language: String,
    #[serde(default)]
    pub copyright_year: Option<u16>,
    #[serde(default)]
    pub copyright_holder: String,
    #[serde(default)]
    pub chapters: Vec<ChapterRef>,
    #[serde(default)]
    pub opening_credits: Option<String>,
    #[serde(default)]
    pub closing_credits: Option<String>,
    #[serde(default)]
    pub about_author: Option<String>,
    #[serde(default)]
    pub retail_sample: Vec<String>,
    #[serde(default)]
    pub cover: Option<CoverSpec>,
    #[serde(default)]
    pub pronunciation_dictionary: Option<DictionaryRef>,
    #[serde(default)]
    pub cast: HashMap<String, CastVoice>,
    #[serde(default)]
    pub model: String,
    #[serde(default = "default_quality")]
    pub quality: String,
    #[serde(default)]
    pub paragraph_gap_ms: u32,
    #[serde(default)]
    pub generated_at: String,
}

fn default_language() -> String { "en".to_string() }
fn default_quality() -> String { "mp3_44100_128".to_string() }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChapterRef {
    pub stem: String,
    pub file: String,
    pub title: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CoverSpec {
    pub path: String,
    #[serde(default)]
    pub width: u32,
    #[serde(default)]
    pub height: u32,
    #[serde(default)]
    pub format: String,
    #[serde(default)]
    pub color: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct DictionaryRef {
    pub name: String,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub version_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct CastVoice {
    #[serde(default)]
    pub voice_id: String,
    #[serde(default)]
    pub voice_name: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub stability: f64,
    #[serde(default)]
    pub similarity: f64,
    #[serde(default)]
    pub speed: f64,
}

// ---------------------------------------------------------------------------
// chapters/<stem>.json — written by AuthorLM
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Chapter {
    pub schema: u8,
    #[serde(default)]
    pub file: Option<String>,
    pub stem: String,
    pub title: String,
    #[serde(default)]
    pub voice_default: String,
    #[serde(default)]
    pub sections: Vec<Section>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum Section {
    #[serde(rename = "speech")]
    Speech(Speech),
    #[serde(rename = "silence")]
    Silence(Silence),
}

impl Section {
    pub fn id(&self) -> &str {
        match self {
            Section::Speech(s) => &s.id,
            Section::Silence(s) => &s.id,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Speech {
    pub id: String,
    #[serde(default)]
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub level: Option<u8>,
    pub text: String,
    #[serde(default)]
    pub cast: String,
    pub voice_id: String,
    #[serde(default)]
    pub voice_name: String,
    pub model: String,
    pub stability: f64,
    pub similarity: f64,
    #[serde(default = "one")]
    pub speed: f64,
    #[serde(default)]
    pub pronunciations: Vec<Pronunciation>,
    #[serde(default)]
    pub source: serde_json::Value,
}

fn one() -> f64 { 1.0 }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Pronunciation {
    pub term: String,
    pub say: String,
}

/// What a generated take was made from — the text, the voice and its
/// settings, and the pronunciation rules that applied — recorded on the
/// state entry at generation time. On any later load, a section without
/// audio is paired to its last take by text and the card says what
/// changed SINCE THAT TAKE, whether or not the app was running when the
/// export happened (author's ask, 2026-09-04: "so the reason matches up
/// with my memory of what I did").
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct MadeFrom {
    pub text: String,
    #[serde(default)]
    pub cast: String,
    pub voice_id: String,
    pub model: String,
    pub stability: f64,
    pub similarity: f64,
    #[serde(default = "one")]
    pub speed: f64,
    #[serde(default)]
    pub pronunciations: Vec<Pronunciation>,
}

impl From<&Speech> for MadeFrom {
    fn from(s: &Speech) -> Self {
        MadeFrom { text: s.text.clone(), cast: s.cast.clone(), voice_id: s.voice_id.clone(),
                   model: s.model.clone(), stability: s.stability, similarity: s.similarity,
                   speed: s.speed, pronunciations: s.pronunciations.clone() }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Silence {
    pub id: String,
    pub duration_ms: u32,
}

// ---------------------------------------------------------------------------
// state/<stem>.json — written by audiostation, and by nothing else
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ChapterState {
    #[serde(default = "schema_default")]
    pub schema: u8,
    /// section id → what has been generated for it. Paths are relative
    /// to the audiobook folder.
    #[serde(default)]
    pub sections: HashMap<String, SectionState>,
    /// output format → stitched chapter file, relative to the folder.
    #[serde(default)]
    pub stitched: HashMap<String, String>,
    /// output format → the stitch key the file was made with (§10.2: a
    /// stitched file is a function of the section sequence and the
    /// encoder recipe; when the key no longer matches the chapter's,
    /// the file is stale and Re-stitch lights up).
    #[serde(default)]
    pub stitch_keys: HashMap<String, String>,
    #[serde(default)]
    pub duration_secs: Option<f64>,
    #[serde(default)]
    pub loudness_lufs: Option<f64>,
    #[serde(default)]
    pub true_peak_dbtp: Option<f64>,
}

fn schema_default() -> u8 { SCHEMA }

impl Default for ChapterState {
    fn default() -> Self {
        ChapterState { schema: SCHEMA, sections: HashMap::new(), stitched: HashMap::new(),
                       stitch_keys: HashMap::new(),
                       duration_secs: None, loudness_lufs: None, true_peak_dbtp: None }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
#[serde(rename_all = "camelCase")]
pub struct SectionState {
    /// output format → file, relative to the audiobook folder.
    #[serde(default)]
    pub audio_files: HashMap<String, String>,
    /// The ElevenLabs `request-id` header of the LAST generation, for
    /// request stitching within its two-hour window (design §8).
    #[serde(default)]
    pub request_id: Option<String>,
    /// RFC 3339, when that generation happened.
    #[serde(default)]
    pub generated_at: Option<String>,
    /// What that generation was made from (absent on takes recorded
    /// before 2026-09-04; those can still be paired by id, never by text).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub made_from: Option<MadeFrom>,
}

// ---------------------------------------------------------------------------
// What changed on the last reload (design §10.3) — in memory only
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SectionChange {
    /// "text" | "params" | "new"
    pub kind: String,
    /// For "params": which fields moved (voice, model, stability, …), and
    /// each pronunciation rule by name: "Basilides: old → new",
    /// "added Prohairesis → pro-HY-ruh-sis", "removed Abraxas".
    #[serde(default)]
    pub detail: Vec<String>,
    /// When the take this is measured against was generated (RFC 3339),
    /// when known — the badge reads "changed since <then>".
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub since: Option<String>,
}

// ---------------------------------------------------------------------------
// The loaded folder, in memory
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LoadedChapter {
    pub chapter: Chapter,
    pub state: ChapterState,
    /// id → change flag, kept until that id has generated audio.
    #[serde(default)]
    pub changes: HashMap<String, SectionChange>,
    /// Texts of sections that vanished on the last reload.
    #[serde(default)]
    pub removed: Vec<String>,
    /// True when this chapter appeared on the last reload.
    #[serde(default)]
    pub added: bool,
    /// What a stitch of this chapter would be keyed by right now.
    #[serde(default)]
    pub stitch_key: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Master {
    /// The `_audio/` folder, absolute.
    pub dir: PathBuf,
    pub book: Book,
    /// In `book.chapters` order.
    pub chapters: Vec<LoadedChapter>,
    pub opening_credits: Option<LoadedChapter>,
    pub closing_credits: Option<LoadedChapter>,
    pub about_author: Option<LoadedChapter>,
    /// Book-level fields that moved on the last reload.
    #[serde(default)]
    pub book_changes: Vec<String>,
}

impl Master {
    /// Every loaded chapter, chapters first then the special slots.
    pub fn all(&self) -> Vec<&LoadedChapter> {
        let mut out: Vec<&LoadedChapter> = self.chapters.iter().collect();
        out.extend(self.opening_credits.iter());
        out.extend(self.closing_credits.iter());
        out.extend(self.about_author.iter());
        out
    }

    pub fn all_mut(&mut self) -> Vec<&mut LoadedChapter> {
        let mut out: Vec<&mut LoadedChapter> = self.chapters.iter_mut().collect();
        out.extend(self.opening_credits.iter_mut());
        out.extend(self.closing_credits.iter_mut());
        out.extend(self.about_author.iter_mut());
        out
    }

    pub fn find(&self, stem: &str) -> Option<&LoadedChapter> {
        self.all().into_iter().find(|c| c.chapter.stem == stem)
    }

    pub fn find_mut(&mut self, stem: &str) -> Option<&mut LoadedChapter> {
        self.all_mut().into_iter().find(|c| c.chapter.stem == stem)
    }

    /// The chapter holding a section id.
    pub fn chapter_of(&self, section_id: &str) -> Option<&LoadedChapter> {
        self.all().into_iter()
            .find(|c| c.chapter.sections.iter().any(|s| s.id() == section_id))
    }


    pub fn resolve(&self, rel: &str) -> PathBuf {
        resolve_in(&self.dir, rel)
    }

    /// The manuscript root — the folder above `_audio/` — where the
    /// cover path in `audiobook.json` is relative to.
    pub fn manuscript_root(&self) -> PathBuf {
        self.dir.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| self.dir.clone())
    }
}

pub fn resolve_in(dir: &Path, rel: &str) -> PathBuf {
    let p = Path::new(rel);
    if p.is_absolute() { p.to_path_buf() } else { dir.join(p) }
}

impl LoadedChapter {
    /// A section is done at `format` when its id has a state entry whose
    /// file exists (design §6).
    pub fn done(&self, dir: &Path, id: &str, format: &str) -> bool {
        self.state.sections.get(id)
            .and_then(|s| s.audio_files.get(format))
            .map(|rel| resolve_in(dir, rel).exists())
            .unwrap_or(false)
    }

    pub fn all_done(&self, dir: &Path, format: &str) -> bool {
        self.chapter.sections.iter().all(|s| match s {
            Section::Speech(sp) => self.done(dir, &sp.id, format),
            Section::Silence(_) => true,
        })
    }

    pub fn speech(&self) -> Vec<&Speech> {
        self.chapter.sections.iter().filter_map(|s| match s {
            Section::Speech(sp) => Some(sp),
            _ => None,
        }).collect()
    }
}

// ---------------------------------------------------------------------------
// ACX audit
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct AuditResult {
    pub check_id: String,
    pub label: String,
    pub passed: bool,
    pub message: String,
    pub severity: AuditSeverity,
}

#[derive(Debug, Serialize, Clone)]
pub enum AuditSeverity {
    Error,
    Warning,
    Info,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../authorlm/tests/fixtures/audiobook/_audio")
    }

    #[test]
    fn fixture_book_parses() {
        let text = std::fs::read_to_string(fixture_dir().join(BOOK_FILE)).unwrap();
        let book: Book = serde_json::from_str(&text).unwrap();
        assert_eq!(book.schema, SCHEMA);
        assert_eq!(book.title, "Seven More Sermons To The Dead");
        assert_eq!(book.chapters.iter().map(|c| c.stem.as_str()).collect::<Vec<_>>(),
                   vec!["sermons", "kindness"]);
        assert_eq!(book.about_author.as_deref(), Some("chapters/about.json"));
        assert_eq!(book.retail_sample.len(), 3);
        assert_eq!(book.cover.as_ref().unwrap().width, 2400);
        assert_eq!(book.cast["herdsman"].speed, 0.97);
        assert_eq!(book.pronunciation_dictionary.as_ref().unwrap().name,
                   "Scratch pronunciations");
    }

    #[test]
    fn fixture_chapters_parse_and_roundtrip() {
        for entry in std::fs::read_dir(fixture_dir().join(CHAPTERS_DIR)).unwrap() {
            let path = entry.unwrap().path();
            let text = std::fs::read_to_string(&path).unwrap();
            let chapter: Chapter = serde_json::from_str(&text).unwrap();
            assert_eq!(chapter.schema, SCHEMA);
            assert!(chapter.sections.iter().any(|s| matches!(s, Section::Speech(_))),
                    "{path:?} has speech");
            // Round trip: what we parse is what AuthorLM wrote, field for field.
            let value: serde_json::Value = serde_json::from_str(&text).unwrap();
            let back: serde_json::Value = serde_json::to_value(&chapter).unwrap();
            assert_eq!(value, back, "{path:?} round-trips");
        }
    }

    #[test]
    fn fixture_state_parses_and_done_semantics() {
        let dir = fixture_dir();
        let text = std::fs::read_to_string(dir.join(STATE_DIR).join("sermons.json")).unwrap();
        let state: ChapterState = serde_json::from_str(&text).unwrap();
        assert_eq!(state.sections.len(), 1);
        let (id, entry) = {
            let (i, e) = state.sections.iter().next().unwrap();
            (i.clone(), e.clone())
        };
        assert_eq!(entry.request_id.as_deref(), Some("req-fixture-0001"));
        let chapter: Chapter = serde_json::from_str(
            &std::fs::read_to_string(dir.join(CHAPTERS_DIR).join("sermons.json")).unwrap()).unwrap();
        let loaded = LoadedChapter { chapter, state, changes: HashMap::new(),
                                     removed: vec![], added: false, stitch_key: String::new() };
        // The state names a file that does not exist in the fixture, so
        // the section is NOT done: done means the file is there.
        assert!(!loaded.done(&dir, &id, "mp3_44100_128"));
        assert!(!loaded.all_done(&dir, "mp3_44100_128"));
        // Write only the state file's shape back: relative paths survive.
        let json = serde_json::to_string(&loaded.state).unwrap();
        assert!(json.contains("audio/"));
        assert!(!json.contains(dir.to_string_lossy().as_ref()));
    }

    #[test]
    fn state_default_is_empty_and_serializes_schema() {
        let s = ChapterState::default();
        let json = serde_json::to_string(&s).unwrap();
        assert!(json.contains("\"schema\":1"), "a fresh state file carries the schema");
        let back: ChapterState = serde_json::from_str("{}").unwrap();
        assert_eq!(back.schema, SCHEMA);
        assert!(back.sections.is_empty());
    }
}
