//! Tauri commands — the studio's verbs over a loaded audiobook folder.
//!
//! Generation writes `audio/<id>.<format>.mp3` and `state/<stem>.json`
//! and nothing else. Continuity (design §8): every request carries the
//! neighbouring same-voice text, and the neighbours' ElevenLabs request
//! ids when they are under two hours old and the model is not v3.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use tauri::{Emitter, Manager, State};
use tauri_plugin_store::StoreExt;

use crate::book;
use crate::types::{
    AuditResult, AuditSeverity, LoadedChapter, Master, Section, SectionState, Speech, AUDIO_DIR,
};

pub type SharedMaster = Arc<Mutex<Option<Master>>>;

const STORE_FILE: &str = "settings.json";
const STITCH_WINDOW_SECS: i64 = 2 * 60 * 60;
const QUALITIES: [&str; 4] = ["mp3_22050_32", "mp3_44100_64", "mp3_44100_128", "mp3_44100_192"];

// ---------------------------------------------------------------------------
// Opening, reloading, closing
// ---------------------------------------------------------------------------

fn open_internal(shared: &SharedMaster, dir: &Path) -> Result<book::ReloadSummary, String> {
    let mut master = book::load(dir)?;
    book::sweep_orphans(&mut master)?;
    let mut guard = shared.lock().unwrap();
    let summary = match guard.as_ref() {
        Some(old) if old.dir == master.dir => book::diff(old, &mut master),
        _ => book::ReloadSummary::default(),
    };
    *guard = Some(master);
    Ok(summary)
}

/// Re-read the open folder, diff against memory, keep the flags. Called
/// by the watcher thread and by the Reload menu item.
pub fn reload_internal(shared: &SharedMaster) -> Result<book::ReloadSummary, String> {
    let dir = {
        let guard = shared.lock().unwrap();
        guard.as_ref().map(|m| m.dir.clone()).ok_or("No audiobook folder is open")?
    };
    open_internal(shared, &dir)
}

#[tauri::command]
pub fn open_book(
    app: tauri::AppHandle,
    state: State<'_, SharedMaster>,
    dir: String,
) -> Result<book::ReloadSummary, String> {
    let path = PathBuf::from(&dir);
    // Accept the manuscript root too: the folder that CONTAINS _audio/.
    let path = if path.join(crate::types::BOOK_FILE).exists() { path }
               else if path.join("_audio").join(crate::types::BOOK_FILE).exists() { path.join("_audio") }
               else { path };
    {
        let mut guard = state.lock().unwrap();
        *guard = None;   // a different folder is not a reload of this one
    }
    let summary = open_internal(&state, &path)?;
    let canonical = state.lock().unwrap().as_ref().map(|m| m.dir.clone()).unwrap();
    crate::watcher::start(&app, canonical.clone())?;
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    store.set("lastBook", serde_json::Value::String(canonical.to_string_lossy().to_string()));
    store.save().map_err(|e| e.to_string())?;
    log::info!("[open_book] {}", canonical.display());
    Ok(summary)
}

/// The argv path: open `dir`, remember it, start the watcher. The
/// frontend's boot then finds it as the last folder and renders it.
pub fn open_from_argv(app: &tauri::AppHandle, dir: &str) -> Result<(), String> {
    let state = app.state::<SharedMaster>();
    let path = PathBuf::from(dir);
    let path = if path.join(crate::types::BOOK_FILE).exists() { path }
               else if path.join("_audio").join(crate::types::BOOK_FILE).exists() { path.join("_audio") }
               else { path };
    open_internal(&state, &path)?;
    let canonical = state.lock().unwrap().as_ref().map(|m| m.dir.clone()).unwrap();
    crate::watcher::start(app, canonical.clone())?;
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    store.set("lastBook", serde_json::Value::String(canonical.to_string_lossy().to_string()));
    store.save().map_err(|e| e.to_string())
}

#[tauri::command]
pub fn reload_book(state: State<'_, SharedMaster>) -> Result<book::ReloadSummary, String> {
    reload_internal(&state)
}

#[tauri::command]
pub fn close_book(app: tauri::AppHandle, state: State<'_, SharedMaster>) -> Result<(), String> {
    crate::watcher::stop(&app);
    *state.lock().unwrap() = None;
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    store.delete("lastBook");
    store.save().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn get_master(state: State<'_, SharedMaster>) -> Option<Master> {
    state.lock().unwrap().clone()
}

#[tauri::command]
pub async fn get_last_book(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    Ok(store.get("lastBook").and_then(|v| v.as_str().map(|s| s.to_string())))
}

// ---------------------------------------------------------------------------
// Settings — the API key, and nothing else
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn get_settings(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "elevenlabsApiKey": store.get("elevenlabsApiKey")
            .and_then(|v| v.as_str().map(|s| s.to_string())).unwrap_or_default(),
    }))
}

#[tauri::command]
pub async fn save_settings(app: tauri::AppHandle, elevenlabs_api_key: String) -> Result<(), String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    store.set("elevenlabsApiKey", serde_json::Value::String(elevenlabs_api_key.trim().to_string()));
    store.save().map_err(|e| e.to_string())?;
    Ok(())
}

fn load_api_key(app: &tauri::AppHandle) -> Result<String, String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    let key = store.get("elevenlabsApiKey")
        .and_then(|v| v.as_str().map(|s| s.to_string())).unwrap_or_default();
    if key.is_empty() {
        return Err("ElevenLabs API key not set — open Settings first.".to_string());
    }
    Ok(key)
}

// ---------------------------------------------------------------------------
// Logging bridge
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_log_path(app: tauri::AppHandle) -> Result<String, String> {
    app.path().app_log_dir()
        .map(|d| d.join("app.log").to_string_lossy().to_string())
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub fn log_frontend_error(context: String, message: String) {
    log::error!("[frontend::{}] {}", context, message);
}

#[tauri::command]
pub fn log_frontend_info(context: String, message: String) {
    log::info!("[frontend::{}] {}", context, message);
}

// ---------------------------------------------------------------------------
// Generation
// ---------------------------------------------------------------------------

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct GenerationProgressEvent {
    section_index: u32,
    total_sections: u32,
    stem: String,
    voice_name: String,
    quality: String,
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct StitchProgressEvent {
    message: String,
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct SectionGeneratedEvent {
    stem: String,
    section_id: String,
    format: String,
}

/// Everything one ElevenLabs call needs, copied out from under the lock.
struct Request {
    speech: Speech,
    previous_text: Option<String>,
    next_text: Option<String>,
    previous_request_ids: Vec<String>,
    next_request_ids: Vec<String>,
    locators: Vec<serde_json::Value>,
}

fn recent(state: Option<&SectionState>, now: chrono::DateTime<chrono::Utc>) -> Option<String> {
    let st = state?;
    let id = st.request_id.clone()?;
    let at = st.generated_at.as_deref()
        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())?;
    if (now - at.with_timezone(&chrono::Utc)).num_seconds() < STITCH_WINDOW_SECS {
        Some(id)
    } else {
        None
    }
}

/// The nearest same-voice speech sections before and after, skipping
/// silences (design §8).
fn neighbours(chapter: &LoadedChapter, id: &str) -> (Option<Speech>, Option<Speech>) {
    let sections = &chapter.chapter.sections;
    let idx = match sections.iter().position(|s| s.id() == id) { Some(i) => i, None => return (None, None) };
    let voice = match &sections[idx] { Section::Speech(s) => s.voice_id.clone(), _ => return (None, None) };
    let prev = sections[..idx].iter().rev().find_map(|s| match s {
        Section::Speech(sp) if sp.voice_id == voice => Some(sp.clone()),
        _ => None,
    });
    let next = sections[idx + 1..].iter().find_map(|s| match s {
        Section::Speech(sp) if sp.voice_id == voice => Some(sp.clone()),
        _ => None,
    });
    (prev, next)
}

fn build_request(master: &Master, id: &str) -> Result<(String, Request), String> {
    let chapter = master.chapter_of(id).ok_or_else(|| format!("Section {id} not found"))?;
    let speech = chapter.chapter.sections.iter().find_map(|s| match s {
        Section::Speech(sp) if sp.id == id => Some(sp.clone()),
        _ => None,
    }).ok_or_else(|| format!("Section {id} is not speech"))?;
    let (prev, next) = neighbours(chapter, id);
    let now = chrono::Utc::now();
    let stitchable = !speech.model.starts_with("eleven_v3");
    let previous_request_ids = if stitchable {
        prev.as_ref().and_then(|p| recent(chapter.state.sections.get(&p.id), now)).into_iter().collect()
    } else { vec![] };
    let next_request_ids = if stitchable {
        next.as_ref().and_then(|n| recent(chapter.state.sections.get(&n.id), now)).into_iter().collect()
    } else { vec![] };
    let locators = master.book.pronunciation_dictionary.as_ref()
        .and_then(|d| match (&d.id, &d.version_id) {
            (Some(i), Some(v)) => Some(vec![serde_json::json!({
                "pronunciation_dictionary_id": i, "version_id": v })]),
            _ => None,
        }).unwrap_or_default();
    Ok((chapter.chapter.stem.clone(), Request {
        speech,
        previous_text: prev.map(|p| p.text),
        next_text: next.map(|n| n.text),
        previous_request_ids,
        next_request_ids,
        locators,
    }))
}

async fn call_elevenlabs(api_key: &str, req: &Request, output_format: &str) -> Result<(Vec<u8>, Option<String>), String> {
    let url = format!("https://api.elevenlabs.io/v1/text-to-speech/{}?output_format={}",
                      req.speech.voice_id, output_format);
    let mut payload = serde_json::json!({
        "text": req.speech.text,
        "model_id": req.speech.model,
        "voice_settings": {
            "stability": req.speech.stability,
            "similarity_boost": req.speech.similarity,
            "speed": req.speech.speed,
        }
    });
    if let Some(t) = &req.previous_text { payload["previous_text"] = serde_json::json!(t); }
    if let Some(t) = &req.next_text { payload["next_text"] = serde_json::json!(t); }
    if !req.previous_request_ids.is_empty() {
        payload["previous_request_ids"] = serde_json::json!(req.previous_request_ids);
    }
    if !req.next_request_ids.is_empty() {
        payload["next_request_ids"] = serde_json::json!(req.next_request_ids);
    }
    if !req.locators.is_empty() {
        payload["pronunciation_dictionary_locators"] = serde_json::json!(req.locators);
    }
    let client = reqwest::Client::new();
    let resp = client.post(&url)
        .header("xi-api-key", api_key)
        .header("Accept", "audio/mpeg")
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Request failed: {e}"))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("ElevenLabs API {status}: {body}"));
    }
    let request_id = resp.headers().get("request-id")
        .and_then(|v| v.to_str().ok()).map(|s| s.to_string());
    let audio = resp.bytes().await.map_err(|e| format!("Read response: {e}"))?.to_vec();
    Ok((audio, request_id))
}

fn record_generation(shared: &SharedMaster, stem: &str, id: &str, format: &str,
                     rel: String, request_id: Option<String>) -> Result<(), String> {
    let mut guard = shared.lock().unwrap();
    let master = guard.as_mut().ok_or("No audiobook folder is open")?;
    let dir = master.dir.clone();
    let chapter = master.find_mut(stem).ok_or("Chapter vanished during generation")?;
    // What this take was made from — the memory a later load reads when
    // this paragraph's id moves (book::flags_from_state).
    let made_from = chapter.chapter.sections.iter().find_map(|s| match s {
        Section::Speech(sp) if sp.id == id => Some(crate::types::MadeFrom::from(sp)),
        _ => None,
    });
    let entry = chapter.state.sections.entry(id.to_string()).or_default();
    entry.audio_files.insert(format.to_string(), rel);
    entry.request_id = request_id;
    entry.generated_at = Some(chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true));
    if made_from.is_some() { entry.made_from = made_from; }
    // The stitched file no longer reflects this chapter.
    chapter.state.stitched.remove(format);
    chapter.changes.remove(id);
    book::save_state(&dir, chapter)
}

async fn generate_one(app: &tauri::AppHandle, shared: &SharedMaster, api_key: &str,
                      id: &str, format: &str) -> Result<String, String> {
    let (dir, stem, req) = {
        let guard = shared.lock().unwrap();
        let master = guard.as_ref().ok_or("No audiobook folder is open")?;
        let (stem, req) = build_request(master, id)?;
        (master.dir.clone(), stem, req)
    };
    let audio_dir = dir.join(AUDIO_DIR);
    std::fs::create_dir_all(&audio_dir).map_err(|e| format!("Cannot create audio dir: {e}"))?;
    let rel = format!("{AUDIO_DIR}/{id}.{format}.mp3");
    log::info!("[generate] {stem}/{id} {format} prev_ids={} next_ids={}",
               req.previous_request_ids.len(), req.next_request_ids.len());
    let (audio, request_id) = call_elevenlabs(api_key, &req, format).await.map_err(|e| {
        log::error!("[generate] {id} failed: {e}");
        e
    })?;
    std::fs::write(dir.join(&rel), &audio).map_err(|e| format!("Write audio: {e}"))?;
    record_generation(shared, &stem, id, format, rel.clone(), request_id)?;
    // The row turns green as it lands, not when the whole chapter returns.
    let _ = app.emit("section-generated", SectionGeneratedEvent {
        stem: stem.clone(), section_id: id.to_string(), format: format.to_string(),
    });
    Ok(rel)
}

fn check_format(quality: &str) -> Result<String, String> {
    if QUALITIES.contains(&quality) { Ok(quality.to_string()) }
    else { Err(format!("unknown quality {quality}; one of {}", QUALITIES.join(", "))) }
}

#[tauri::command]
pub async fn generate_section(
    app: tauri::AppHandle,
    state: State<'_, SharedMaster>,
    section_id: String,
    quality: String,
) -> Result<String, String> {
    let api_key = load_api_key(&app)?;
    let format = check_format(&quality)?;
    let (stem, voice) = {
        let guard = state.lock().unwrap();
        let master = guard.as_ref().ok_or("No audiobook folder is open")?;
        let (stem, req) = build_request(master, &section_id)?;
        (stem, req.speech.voice_name)
    };
    let _ = app.emit("generation-progress", GenerationProgressEvent {
        section_index: 1, total_sections: 1, stem: stem.clone(), voice_name: voice, quality: quality.clone(),
    });
    let rel = generate_one(&app, &state, &api_key, &section_id, &format).await?;
    // Auto-stitch when the chapter is now complete at this format.
    let complete = {
        let guard = state.lock().unwrap();
        guard.as_ref().and_then(|m| m.find(&stem).map(|c| c.all_done(&m.dir, &format))).unwrap_or(false)
    };
    if complete {
        if let Err(e) = stitch_internal(&app, &state, &stem, &format).await {
            log::warn!("[generate] auto-stitch failed: {e}");
        }
    }
    Ok(rel)
}

#[tauri::command]
pub async fn generate_all_remaining(
    app: tauri::AppHandle,
    state: State<'_, SharedMaster>,
    stem: String,
    quality: String,
) -> Result<u32, String> {
    let api_key = load_api_key(&app)?;
    let format = check_format(&quality)?;
    let pending: Vec<(String, String)> = {
        let guard = state.lock().unwrap();
        let master = guard.as_ref().ok_or("No audiobook folder is open")?;
        let chapter = master.find(&stem).ok_or_else(|| format!("No chapter {stem}"))?;
        chapter.speech().iter()
            .filter(|s| !chapter.done(&master.dir, &s.id, &format))
            .map(|s| (s.id.clone(), s.voice_name.clone()))
            .collect()
    };
    let total = pending.len() as u32;
    log::info!("[generate_all] {stem}: {total} section(s) at {format}");
    let mut generated = 0u32;
    for (i, (id, voice)) in pending.iter().enumerate() {
        let _ = app.emit("generation-progress", GenerationProgressEvent {
            section_index: i as u32 + 1, total_sections: total, stem: stem.clone(),
            voice_name: voice.clone(), quality: quality.clone(),
        });
        generate_one(&app, &state, &api_key, id, &format).await?;
        generated += 1;
    }
    let complete = {
        let guard = state.lock().unwrap();
        guard.as_ref().and_then(|m| m.find(&stem).map(|c| c.all_done(&m.dir, &format))).unwrap_or(false)
    };
    if complete {
        stitch_internal(&app, &state, &stem, &format).await?;
    }
    Ok(generated)
}

#[tauri::command]
pub fn clear_lower_quality(state: State<'_, SharedMaster>, quality: String) -> Result<u32, String> {
    let target = QUALITIES.iter().position(|q| *q == quality)
        .ok_or_else(|| format!("unknown quality {quality}"))?;
    let mut guard = state.lock().unwrap();
    let master = guard.as_mut().ok_or("No audiobook folder is open")?;
    let dir = master.dir.clone();
    let mut cleared = 0u32;
    for chapter in master.all_mut() {
        let mut touched = false;
        for entry in chapter.state.sections.values_mut() {
            let before = entry.audio_files.len();
            let mut removed: Vec<String> = Vec::new();
            entry.audio_files.retain(|fmt, rel| {
                let keep = QUALITIES.iter().position(|q| q == fmt).map_or(true, |r| r >= target);
                if !keep { removed.push(rel.clone()); }
                keep
            });
            for rel in removed {
                let _ = std::fs::remove_file(crate::types::resolve_in(&dir, &rel));
            }
            if entry.audio_files.len() != before { cleared += 1; touched = true; }
        }
        let mut gone: Vec<String> = Vec::new();
        chapter.state.stitched.retain(|fmt, rel| {
            let keep = QUALITIES.iter().position(|q| q == fmt).map_or(true, |r| r >= target);
            if !keep { gone.push(rel.clone()); }
            keep
        });
        for rel in gone {
            let _ = std::fs::remove_file(crate::types::resolve_in(&dir, &rel));
            touched = true;
        }
        if touched { book::save_state(&dir, chapter)?; }
    }
    log::info!("[clear_lower_quality] {quality}: {cleared} section(s)");
    Ok(cleared)
}

// ---------------------------------------------------------------------------
// Stitching
// ---------------------------------------------------------------------------

async fn stitch_internal(app: &tauri::AppHandle, shared: &SharedMaster, stem: &str, format: &str) -> Result<String, String> {
    let (dir, chapter) = {
        let guard = shared.lock().unwrap();
        let master = guard.as_ref().ok_or("No audiobook folder is open")?;
        let chapter = master.find(stem).ok_or_else(|| format!("No chapter {stem}"))?.clone();
        (master.dir.clone(), chapter)
    };
    let _ = app.emit("stitch-progress", StitchProgressEvent { message: format!("{stem}: preparing segments…") });
    let inputs = crate::stitch::prepare_inputs(&dir, &chapter, format).await?;
    let two_pass = matches!(format, "mp3_44100_128" | "mp3_44100_192");
    let rel = if two_pass {
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("{stem}: measuring loudness of {} segments (pass 1/2)…", inputs.len()) });
        let m = crate::stitch::measure_loudness(&dir, &inputs).await?;
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("{stem}: encoding with measured normalisation (pass 2/2)…") });
        crate::stitch::concat_inputs(&dir, stem, format, &inputs, Some(&m)).await?
    } else {
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("{stem}: mixing {} segments…", inputs.len()) });
        crate::stitch::concat_inputs(&dir, stem, format, &inputs, None).await?
    };
    let duration = crate::stitch::measure_duration(&dir.join(&rel)).await.ok();
    {
        let mut guard = shared.lock().unwrap();
        let master = guard.as_mut().ok_or("No audiobook folder is open")?;
        let chapter = master.find_mut(stem).ok_or("Chapter vanished during stitch")?;
        chapter.state.stitched.insert(format.to_string(), rel.clone());
        chapter.state.stitch_keys.insert(format.to_string(), chapter.stitch_key.clone());
        chapter.state.duration_secs = duration;
        if two_pass {
            chapter.state.loudness_lufs = Some(-20.0);
            chapter.state.true_peak_dbtp = Some(-3.0);
        }
        book::save_state(&dir, chapter)?;
    }
    let _ = app.emit("stitch-progress", StitchProgressEvent { message: format!("{stem}: stitched → {rel}") });
    Ok(rel)
}

#[tauri::command]
pub async fn stitch_audio(
    app: tauri::AppHandle,
    state: State<'_, SharedMaster>,
    stem: String,
    quality: String,
) -> Result<String, String> {
    let format = check_format(&quality)?;
    stitch_internal(&app, &state, &stem, &format).await
}

// ---------------------------------------------------------------------------
// ElevenLabs account
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn get_subscription(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let api_key = load_api_key(&app)?;
    let client = reqwest::Client::new();
    let resp = client.get("https://api.elevenlabs.io/v1/user/subscription")
        .header("xi-api-key", &api_key).send().await
        .map_err(|e| format!("Request failed: {e}"))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("ElevenLabs API {status}: {body}"));
    }
    let json: serde_json::Value = resp.json().await.map_err(|e| format!("Parse: {e}"))?;
    Ok(serde_json::json!({
        "characterCount": json.get("character_count").and_then(|v| v.as_u64()).unwrap_or(0),
        "characterLimit": json.get("character_limit").and_then(|v| v.as_u64()).unwrap_or(0),
    }))
}

// ---------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------

fn resolve_for_read(state: &SharedMaster, path: &str) -> PathBuf {
    let p = Path::new(path);
    if p.is_absolute() { return p.to_path_buf(); }
    let guard = state.lock().unwrap();
    match guard.as_ref() {
        Some(m) => m.resolve(path),
        None => p.to_path_buf(),
    }
}

#[tauri::command]
pub fn read_audio_base64(state: State<'_, SharedMaster>, file_path: String) -> Result<String, String> {
    use base64::Engine;
    let path = resolve_for_read(&state, &file_path);
    let bytes = std::fs::read(&path).map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(bytes))
}

#[tauri::command]
pub async fn reveal_in_finder(state: State<'_, SharedMaster>, file_path: String) -> Result<(), String> {
    let path = resolve_for_read(&state, &file_path);
    std::process::Command::new("open").arg("-R").arg(&path).spawn()
        .map_err(|e| format!("open -R failed: {e}"))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// ACX audit and package
// ---------------------------------------------------------------------------

fn audit(master: &Master, quality: &str) -> Vec<AuditResult> {
    let mut out = Vec::new();
    let push = |out: &mut Vec<AuditResult>, id: &str, label: &str, passed: bool, msg: String, sev: AuditSeverity| {
        out.push(AuditResult { check_id: id.into(), label: label.into(), passed, message: msg, severity: sev });
    };
    let dir = &master.dir;
    push(&mut out, "chapters_exist", "Chapters present", !master.chapters.is_empty(),
         if master.chapters.is_empty() { "No chapters in audiobook.json.".into() }
         else { format!("{} chapter(s).", master.chapters.len()) }, AuditSeverity::Error);
    let missing: Vec<String> = master.chapters.iter()
        .filter(|c| !c.all_done(dir, quality)).map(|c| c.chapter.stem.clone()).collect();
    push(&mut out, "chapters_generated", "All chapters generated", missing.is_empty(),
         if missing.is_empty() { "Every chapter has audio.".into() }
         else { format!("Missing audio in: {}", missing.join(", ")) }, AuditSeverity::Error);
    for (id, label, slot, sev) in [
        ("opening_credits", "Opening credits", &master.opening_credits, AuditSeverity::Error),
        ("closing_credits", "Closing credits", &master.closing_credits, AuditSeverity::Error),
        ("about_author", "About the author", &master.about_author, AuditSeverity::Info),
    ] {
        match slot {
            Some(c) => {
                let done = c.all_done(dir, quality);
                push(&mut out, &format!("{id}_generated"), &format!("{label} generated"), done,
                     if done { format!("{label} generated.") } else { format!("{label} not generated yet.") },
                     sev.clone());
            }
            None => push(&mut out, &format!("{id}_present"), &format!("{label} present"), false,
                         format!("{label} missing from audiobook.json — 'authorlm audio export'."), sev.clone()),
        }
    }
    let rs = &master.book.retail_sample;
    push(&mut out, "retail_sample_designated", "Retail sample designated", !rs.is_empty(),
         if rs.is_empty() { "No retail sample — set [retail_sample] in audiobook.toml.".into() }
         else { format!("{} section(s).", rs.len()) }, AuditSeverity::Error);
    match &master.book.cover {
        None => push(&mut out, "cover_selected", "Cover image", false,
                     "No cover — set cover.path in audiobook.toml.".into(), AuditSeverity::Error),
        Some(c) => {
            let path = master.manuscript_root().join(&c.path);
            push(&mut out, "cover_selected", "Cover image", path.exists(),
                 if path.exists() { c.path.clone() } else { format!("{} does not exist", path.display()) },
                 AuditSeverity::Error);
            push(&mut out, "cover_dimensions", "Cover ≥ 2400 px", c.width >= 2400 && c.height >= 2400,
                 format!("{}x{}", c.width, c.height), AuditSeverity::Error);
            push(&mut out, "cover_square", "Cover square", c.width == c.height,
                 format!("{}x{}", c.width, c.height), AuditSeverity::Error);
            push(&mut out, "cover_format", "Cover JPG or PNG", matches!(c.format.as_str(), "jpg" | "jpeg" | "png"),
                 c.format.clone(), AuditSeverity::Error);
            push(&mut out, "cover_colorspace", "Cover RGB", c.color == "rgb" || c.color.is_empty(),
                 if c.color.is_empty() { "unknown".into() } else { c.color.clone() }, AuditSeverity::Warning);
        }
    }
    push(&mut out, "acx_quality", "ACX quality", matches!(quality, "mp3_44100_128" | "mp3_44100_192"),
         quality.to_string(), AuditSeverity::Error);
    let b = &master.book;
    let meta_missing: Vec<&str> = [("title", &b.title), ("author", &b.author), ("narrator", &b.narrator)]
        .iter().filter(|(_, v)| v.is_empty()).map(|(k, _)| *k).collect();
    push(&mut out, "metadata_complete", "Metadata complete", meta_missing.is_empty(),
         if meta_missing.is_empty() { "Title, author and narrator set.".into() }
         else { format!("Missing: {} — 'authorlm manuscript set'.", meta_missing.join(", ")) },
         AuditSeverity::Error);
    let dict_ok = b.pronunciation_dictionary.as_ref().map_or(false, |d| d.version_id.is_some());
    push(&mut out, "pronunciation_dictionary", "Pronunciation dictionary", dict_ok,
         match &b.pronunciation_dictionary {
             None => "No dictionary named in audiobook.toml.".into(),
             Some(d) if d.version_id.is_none() => format!("'{}' has no version yet — 'authorlm audio dictionary push' then 'audio export'.", d.name),
             Some(d) => format!("'{}' at version {}", d.name, d.version_id.clone().unwrap_or_default()),
         }, AuditSeverity::Warning);
    let stale: Vec<String> = master.all().iter()
        .filter(|c| c.state.stitched.contains_key(quality)
                    && c.state.stitch_keys.get(quality).map_or(true, |k| *k != c.stitch_key))
        .map(|c| c.chapter.stem.clone()).collect();
    push(&mut out, "stitches_current", "Stitched files current", stale.is_empty(),
         if stale.is_empty() { "Every stitched file matches its sections and the encoder.".into() }
         else { format!("Re-stitch: {}", stale.join(", ")) }, AuditSeverity::Error);
    // ACX: every file under 120 minutes.
    let long: Vec<String> = master.all().iter()
        .filter(|c| c.state.duration_secs.map_or(false, |d| d > 120.0 * 60.0))
        .map(|c| format!("{} ({:.0} min)", c.chapter.stem, c.state.duration_secs.unwrap_or(0.0) / 60.0))
        .collect();
    push(&mut out, "file_length", "Every file under 120 minutes", long.is_empty(),
         if long.is_empty() { "No stitched file exceeds 120 minutes.".into() }
         else { format!("Too long for ACX: {}", long.join(", ")) }, AuditSeverity::Error);
    let flagged: usize = master.all().iter().map(|c| c.changes.len()).sum();
    push(&mut out, "no_pending_changes", "No sections awaiting regeneration", flagged == 0,
         if flagged == 0 { "Every changed section has been regenerated.".into() }
         else { format!("{flagged} section(s) changed since their audio was generated.") },
         AuditSeverity::Error);
    out
}

#[tauri::command]
pub fn run_acx_audit(state: State<'_, SharedMaster>, quality: String) -> Result<Vec<AuditResult>, String> {
    let guard = state.lock().unwrap();
    let master = guard.as_ref().ok_or("No audiobook folder is open")?;
    Ok(audit(master, &quality))
}

async fn add_id3_tags(dir: &Path, file: &Path, title: &str, album: &str, narrator: &str, author: &str,
                      track: Option<&str>, date: Option<&str>, publisher: Option<&str>, language: &str) -> Result<(), String> {
    let ffmpeg = crate::stitch::find_ffmpeg().ok_or("ffmpeg not found")?;
    let temp = dir.join("_id3_tmp.mp3");
    let mut args: Vec<String> = vec![
        "-y".into(), "-i".into(), file.to_string_lossy().to_string(),
        "-codec:a".into(), "copy".into(),
        "-metadata".into(), format!("title={title}"),
        "-metadata".into(), format!("album={album}"),
        "-metadata".into(), format!("artist={narrator}"),
        "-metadata".into(), format!("album_artist={author}"),
        "-metadata".into(), format!("comment=Narrator: {narrator}"),
        "-metadata".into(), format!("language={language}"),
    ];
    if let Some(t) = track { args.extend(["-metadata".into(), format!("track={t}")]); }
    if let Some(d) = date { args.extend(["-metadata".into(), format!("date={d}")]); }
    if let Some(p) = publisher { args.extend(["-metadata".into(), format!("publisher={p}")]); }
    args.push(temp.to_string_lossy().to_string());
    let out = tokio::process::Command::new(&ffmpeg).stdin(std::process::Stdio::null()).args(&args)
        .output().await.map_err(|e| format!("ffmpeg ID3 pass failed: {e}"))?;
    if !out.status.success() {
        return Err(format!("ffmpeg ID3 exited {}: {}", out.status, String::from_utf8_lossy(&out.stderr)));
    }
    std::fs::rename(&temp, file).map_err(|e| format!("Rename after ID3 tagging: {e}"))
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct AcxProgressEvent { message: String }

#[tauri::command]
pub async fn generate_acx_package(
    app: tauri::AppHandle,
    state: State<'_, SharedMaster>,
    quality: String,
) -> Result<String, String> {
    if !matches!(quality.as_str(), "mp3_44100_128" | "mp3_44100_192") {
        return Err("ACX requires mp3_44100_128 or mp3_44100_192".into());
    }
    let master = state.lock().unwrap().clone().ok_or("No audiobook folder is open")?;
    let dir = master.dir.clone();
    let acx_dir = crate::stitch::acx_output_dir(&dir, &master.book.title);
    std::fs::create_dir_all(&acx_dir).map_err(|e| format!("Cannot create ACX dir: {e}"))?;
    let b = master.book.clone();
    let year = b.copyright_year.map(|y| y.to_string());
    let emit = |msg: String| {
        let _ = app.emit("acx-package-progress", AcxProgressEvent { message: msg.clone() });
        log::info!("[acx] {msg}");
    };
    let tag = |dest: PathBuf, title: String, track: Option<String>| {
        let acx_dir = acx_dir.clone();
        let b = b.clone();
        let year = year.clone();
        async move {
            add_id3_tags(&acx_dir, &dest, &title, &b.title, &b.narrator, &b.author,
                         track.as_deref(), year.as_deref(),
                         if b.publisher.is_empty() { None } else { Some(b.publisher.as_str()) },
                         &b.language).await
        }
    };

    // Ensure every part is stitched at this quality, then copy it in.
    async fn stitched_path(app: &tauri::AppHandle, shared: &SharedMaster, stem: &str, format: &str) -> Result<PathBuf, String> {
        let existing = {
            let guard = shared.lock().unwrap();
            let m = guard.as_ref().ok_or("No audiobook folder is open")?;
            m.find(stem).and_then(|c| {
                // A stitched file counts only if it was made from these
                // sections with this recipe.
                let current = c.state.stitch_keys.get(format).map_or(false, |k| *k == c.stitch_key);
                if current { c.state.stitched.get(format).map(|r| m.resolve(r)) } else { None }
            })
        };
        if let Some(p) = existing { if p.exists() { return Ok(p); } }
        let rel = stitch_internal(app, shared, stem, format).await?;
        let guard = shared.lock().unwrap();
        Ok(guard.as_ref().unwrap().resolve(&rel))
    }

    emit(format!("Creating ACX package in {}…", acx_dir.display()));
    let n = master.chapters.len();
    if let Some(oc) = &master.opening_credits {
        emit("Opening credits…".into());
        let src = stitched_path(&app, &state, &oc.chapter.stem, &quality).await?;
        let dest = acx_dir.join("00_Opening_Credits.mp3");
        std::fs::copy(&src, &dest).map_err(|e| format!("Copy opening credits: {e}"))?;
        tag(dest, "Opening Credits".into(), Some("0".into())).await.unwrap_or_else(|e| log::warn!("[acx] tag: {e}"));
    }
    for (i, ch) in master.chapters.iter().enumerate() {
        let num = i + 1;
        emit(format!("Chapter {num}: {}…", ch.chapter.title));
        let src = stitched_path(&app, &state, &ch.chapter.stem, &quality).await?;
        let dest = acx_dir.join(format!("{num:02}_{}.mp3", crate::stitch::sanitise_filename(&ch.chapter.stem)));
        std::fs::copy(&src, &dest).map_err(|e| format!("Copy chapter {num}: {e}"))?;
        tag(dest, ch.chapter.title.clone(), Some(num.to_string())).await.unwrap_or_else(|e| log::warn!("[acx] tag: {e}"));
    }
    if let Some(aa) = &master.about_author {
        emit("About the author…".into());
        let src = stitched_path(&app, &state, &aa.chapter.stem, &quality).await?;
        let dest = acx_dir.join("about_author.mp3");
        std::fs::copy(&src, &dest).map_err(|e| format!("Copy about author: {e}"))?;
        tag(dest, "About the Author".into(), None).await.unwrap_or_else(|e| log::warn!("[acx] tag: {e}"));
    }
    if !master.book.retail_sample.is_empty() {
        emit("Retail sample…".into());
        let mut parts: Vec<String> = Vec::new();
        for id in &master.book.retail_sample {
            if let Some(ch) = master.chapter_of(id) {
                if let Some(rel) = ch.state.sections.get(id).and_then(|s| s.audio_files.get(&quality)) {
                    let p = master.resolve(rel);
                    if p.exists() { parts.push(p.to_string_lossy().to_string()); }
                }
            }
        }
        if parts.is_empty() {
            return Err("Retail sample sections are not generated at this quality.".into());
        }
        let gap = dir.join(crate::types::SILENCE_DIR).join("silence_700ms.mp3");
        if !gap.exists() {
            std::fs::create_dir_all(gap.parent().unwrap()).map_err(|e| e.to_string())?;
            let ffmpeg = crate::stitch::find_ffmpeg().ok_or("ffmpeg not found")?;
            crate::stitch::generate_silence(&ffmpeg, 0.7, &gap.to_string_lossy()).await?;
        }
        let mut inputs: Vec<String> = Vec::new();
        for (i, p) in parts.iter().enumerate() {
            if i > 0 { inputs.push(gap.to_string_lossy().to_string()); }
            inputs.push(p.clone());
        }
        crate::stitch::concat_plain(&dir, &inputs, &acx_dir.join("retail_sample.mp3")).await?;
    }
    if let Some(cc) = &master.closing_credits {
        emit("Closing credits…".into());
        let src = stitched_path(&app, &state, &cc.chapter.stem, &quality).await?;
        let dest = acx_dir.join(format!("{:02}_Closing_Credits.mp3", n + 1));
        std::fs::copy(&src, &dest).map_err(|e| format!("Copy closing credits: {e}"))?;
        tag(dest, "Closing Credits".into(), Some((n + 1).to_string())).await.unwrap_or_else(|e| log::warn!("[acx] tag: {e}"));
    }
    if let Some(cover) = &master.book.cover {
        let src = master.manuscript_root().join(&cover.path);
        if src.exists() {
            let ext = if cover.format.is_empty() { "jpg" } else { cover.format.as_str() };
            std::fs::copy(&src, acx_dir.join(format!("cover.{ext}"))).map_err(|e| format!("Copy cover: {e}"))?;
            emit("Cover copied.".into());
        }
    }
    emit(format!("ACX package complete: {}", acx_dir.display()));
    Ok(acx_dir.to_string_lossy().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../authorlm/tests/fixtures/audiobook/_audio")
    }

    #[test]
    fn neighbours_skip_silences_and_other_voices() {
        let m = book::load(&fixture()).unwrap();
        let ch = m.find("sermons").unwrap();
        let speech = ch.speech();
        // The Dead's paragraph sits between two Herdsman paragraphs.
        let dead = speech.iter().find(|s| s.cast == "the_dead").unwrap();
        let (p, n) = neighbours(ch, &dead.id);
        assert!(p.is_none() && n.is_none(), "no other section shares Julian's voice");
        let after = speech.iter().position(|s| s.cast == "the_dead").unwrap() + 1;
        let herdsman = speech[after];
        let (p, n) = neighbours(ch, &herdsman.id);
        assert_eq!(p.unwrap().text, speech[after - 2].text, "skips the Dead's paragraph and the silence");
        assert!(n.is_some());
    }

    #[test]
    fn request_carries_neighbour_text_and_recent_ids_only() {
        let mut m = book::load(&fixture()).unwrap();
        let ch = m.find("sermons").unwrap();
        let speech: Vec<Speech> = ch.speech().into_iter().cloned().collect();
        let first = speech[0].clone();     // has a state entry in the fixture, but old
        let second = speech[1].clone();
        let (_stem, req) = build_request(&m, &second.id).unwrap();
        assert_eq!(req.previous_text.as_deref(), Some(first.text.as_str()));
        assert!(req.previous_request_ids.is_empty(), "the fixture's request is from 2026-01-01 — long past the window");
        assert!(req.locators.is_empty(), "the fixture's dictionary has no version");
        // Make the first section's generation recent.
        let now = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true);
        m.find_mut("sermons").unwrap().state.sections.get_mut(&first.id).unwrap().generated_at = Some(now);
        let (_stem, req) = build_request(&m, &second.id).unwrap();
        assert_eq!(req.previous_request_ids, vec!["req-fixture-0001".to_string()]);
        // v3 never stitches by id.
        if let Some(Section::Speech(s)) = m.find_mut("sermons").unwrap().chapter.sections.iter_mut()
            .find(|s| s.id() == second.id) { s.model = "eleven_v3".into(); }
        let (_stem, req) = build_request(&m, &second.id).unwrap();
        assert!(req.previous_request_ids.is_empty());
        assert!(req.previous_text.is_some(), "text conditioning still applies");
        // A versioned dictionary rides as a locator.
        m.book.pronunciation_dictionary.as_mut().unwrap().id = Some("D1".into());
        m.book.pronunciation_dictionary.as_mut().unwrap().version_id = Some("V1".into());
        let (_stem, req) = build_request(&m, &second.id).unwrap();
        assert_eq!(req.locators[0]["version_id"], "V1");
    }

    #[test]
    fn audit_names_what_is_missing() {
        let m = book::load(&fixture()).unwrap();
        let results = audit(&m, "mp3_44100_128");
        let by_id: HashMap<&str, &AuditResult> = results.iter().map(|r| (r.check_id.as_str(), r)).collect();
        assert!(by_id["chapters_exist"].passed);
        assert!(!by_id["chapters_generated"].passed);
        assert!(by_id["chapters_generated"].message.contains("sermons"));
        assert!(by_id["retail_sample_designated"].passed);
        assert!(by_id["cover_dimensions"].passed && by_id["cover_square"].passed);
        assert!(by_id["metadata_complete"].passed);
        assert!(!by_id["pronunciation_dictionary"].passed);
        // The fixture's kindness state remembers a take of the last
        // paragraph at another speed and with a rule since removed
        // (state/kindness.json): one section awaits regeneration, on both
        // sides — authorlm/tests/test_audio.py asserts the same flag.
        assert!(!by_id["no_pending_changes"].passed);
        assert!(by_id["no_pending_changes"].message.starts_with("1 section(s) changed"));
        assert!(by_id["acx_quality"].passed);
        assert!(!audit(&m, "mp3_44100_64").iter().find(|r| r.check_id == "acx_quality").unwrap().passed);
    }
}
