use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use tauri::State;
use tauri::Emitter;
use tauri_plugin_store::StoreExt;

use crate::types::{
    AudioManifest, AuditResult, AuditSeverity, BookMetadata, CoverSpec, ManifestSection,
    MasterManifest, MergeResult, PartialManifest, PronunciationDictionaryLocator,
    RetailSampleSpec, SectionRef, SpeechSection, VoiceInfo,
};

pub type SharedManifest  = Arc<Mutex<Option<MasterManifest>>>;
/// Path to the currently-open manifest file on disk.  `None` = clipboard / HTTP load.
pub type SharedFilePath  = Arc<Mutex<Option<String>>>;

const STORE_FILE: &str = "settings.json";

// ---------------------------------------------------------------------------
// Manifest commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_master_manifest(state: State<'_, SharedManifest>) -> Option<MasterManifest> {
    state.lock().unwrap().clone()
}

/// Legacy alias kept for backward compat during transition.
#[tauri::command]
pub fn get_manifest(state: State<'_, SharedManifest>) -> Option<MasterManifest> {
    state.lock().unwrap().clone()
}

#[tauri::command]
pub fn set_master_manifest(
    state: State<'_, SharedManifest>,
    manifest: MasterManifest,
) -> Result<(), String> {
    let total: usize = manifest.chapters.iter().map(|c| c.sections.len()).sum::<usize>()
        + manifest.opening_credits.as_ref().map_or(0, |m| m.sections.len())
        + manifest.closing_credits.as_ref().map_or(0, |m| m.sections.len())
        + manifest.about_author.as_ref().map_or(0, |m| m.sections.len());
    log::info!("Master manifest set: {} chapters, {} total sections", manifest.chapters.len(), total);
    *state.lock().unwrap() = Some(manifest);
    Ok(())
}

/// Legacy alias kept for backward compat during transition.
#[tauri::command]
pub fn set_manifest(
    state: State<'_, SharedManifest>,
    manifest: MasterManifest,
) -> Result<(), String> {
    set_master_manifest(state, manifest)
}

#[tauri::command]
pub fn clear_manifest(state: State<'_, SharedManifest>) -> Result<(), String> {
    *state.lock().unwrap() = None;
    Ok(())
}

/// Tell the backend which file the current manifest is associated with.
/// Called by the frontend whenever a file is opened, saved, or closed.
#[tauri::command]
pub fn set_manifest_file_path(
    file_path_state: State<'_, SharedFilePath>,
    file_path: Option<String>,
) -> Result<(), String> {
    *file_path_state.lock().unwrap() = file_path;
    Ok(())
}

/// Helper: get a mutable ref to the first chapter's sections (or named chapter).
fn get_chapter_sections_mut<'a>(
    master: &'a mut MasterManifest,
    chapter_tab_name: Option<&str>,
) -> Option<&'a mut AudioManifest> {
    if let Some(name) = chapter_tab_name {
        master.chapters.iter_mut().find(|c| c.tab_name == name)
    } else {
        master.chapters.first_mut()
    }
}

fn get_chapter_sections<'a>(
    master: &'a MasterManifest,
    chapter_tab_name: Option<&str>,
) -> Option<&'a AudioManifest> {
    if let Some(name) = chapter_tab_name {
        master.chapters.iter().find(|c| c.tab_name == name)
    } else {
        master.chapters.first()
    }
}

#[tauri::command]
pub fn update_section(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    section_id: String,
    new_text: Option<String>,
    new_stability: Option<f64>,
    new_similarity_boost: Option<f64>,
    new_speed: Option<f64>,
    new_voice_id: Option<String>,
    new_voice_name: Option<String>,
    clear_audio: Option<bool>,
) -> Result<(), String> {
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;

        if let Some(ManifestSection::Speech(s)) = master.find_section_mut(&section_id) {
            let has_audio = s.audio_files.as_ref().map_or(false, |m| !m.is_empty());
            if let Some(text) = new_text {
                if has_audio && text != s.text {
                    s.is_dirty = Some(true);
                }
                s.text = text;
            }
            if let Some(v) = new_stability        { s.stability = v; }
            if let Some(v) = new_similarity_boost { s.similarity_boost = v; }
            if let Some(v) = new_speed            { s.speed = Some(v); }
            if let Some(vid) = new_voice_id {
                if has_audio && vid != s.voice_id {
                    s.is_dirty = Some(true);
                }
                s.voice_id = vid;
            }
            if let Some(vname) = new_voice_name { s.voice_name = vname; }
            if clear_audio == Some(true) {
                s.audio_files = None;
                s.is_dirty = None;
            }
        }
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

#[tauri::command]
pub fn update_silence_section(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    section_id: String,
    duration_ms: u32,
) -> Result<(), String> {
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;

        if let Some(ManifestSection::Silence(s)) = master.find_section_mut(&section_id) {
            if s.duration_ms != duration_ms {
                s.duration_ms = duration_ms;
                s.audio_file_path = None;   // must regenerate — new duration may not exist in silenceclips/
            }
        }
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

// ---------------------------------------------------------------------------
// Settings commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub async fn get_settings(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    Ok(serde_json::json!({
        "elevenlabsApiKey": store.get("elevenlabsApiKey").and_then(|v| v.as_str().map(|s| s.to_string())).unwrap_or_default(),
        "outputDir":        store.get("outputDir").and_then(|v| v.as_str().map(|s| s.to_string())).unwrap_or_default(),
        "defaultTtsModel":  store.get("defaultTtsModel").and_then(|v| v.as_str().map(|s| s.to_string())).unwrap_or_else(|| "eleven_multilingual_v2".to_string()),
    }))
}

#[tauri::command]
pub async fn save_settings(
    app: tauri::AppHandle,
    elevenlabs_api_key: String,
    output_dir: String,
    default_tts_model: String,
) -> Result<(), String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    store.set("elevenlabsApiKey", serde_json::Value::String(elevenlabs_api_key));
    store.set("outputDir",        serde_json::Value::String(output_dir));
    store.set("defaultTtsModel",  serde_json::Value::String(default_tts_model));
    store.save().map_err(|e| e.to_string())?;
    Ok(())
}

/// Read a manifest JSON file from disk and load it into the shared state.
/// Supports both v1 AudioManifest (wrapped into MasterManifest) and v2 MasterManifest.
#[tauri::command]
pub fn load_manifest_from_file(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    file_path: String,
) -> Result<(), String> {
    let text = std::fs::read_to_string(&file_path)
        .map_err(|e| format!("Cannot read file: {e}"))?;
    let mut master = parse_manifest_json(&text)?;
    // Resolve any relative paths so in-memory state always holds absolute paths.
    if let Some(parent) = std::path::Path::new(&file_path).parent() {
        absolutize_manifest_paths(&mut master, parent);
    }
    let total: usize = master.chapters.iter().map(|c| c.sections.len()).sum::<usize>();
    log::info!("Manifest loaded from file: {} chapters, {} sections ({})", master.chapters.len(), total, file_path);
    *state.lock().unwrap() = Some(master);
    *file_path_state.lock().unwrap() = Some(file_path);
    Ok(())
}

/// Parse JSON as either a v2 MasterManifest or a v1 AudioManifest (wrapping the latter).
pub fn parse_manifest_json(text: &str) -> Result<MasterManifest, String> {
    // Try to detect by the presence of "chapters" key and version==2
    let value: serde_json::Value = serde_json::from_str(text)
        .map_err(|e| format!("Invalid JSON: {e}"))?;

    let is_master = value.get("version").and_then(|v| v.as_u64()) == Some(2)
        && value.get("chapters").is_some();

    if is_master {
        serde_json::from_value(value).map_err(|e| format!("Invalid master manifest JSON: {e}"))
    } else {
        // v1 AudioManifest — wrap it
        let v1: AudioManifest = serde_json::from_value(value)
            .map_err(|e| format!("Invalid manifest JSON: {e}"))?;
        Ok(MasterManifest {
            version: 2,
            document_title: v1.document_title.clone(),
            generated_at: v1.generated_at.clone(),
            chapters: vec![v1],
            opening_credits: None,
            closing_credits: None,
            about_author: None,
            retail_sample: None,
            cover: None,
            metadata: None,
        })
    }
}

/// Return the last file path the user opened, or null if none.
#[tauri::command]
pub async fn get_last_file(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    Ok(store.get("lastOpenedFile").and_then(|v| v.as_str().map(|s| s.to_string())))
}

/// Persist (or clear) the last opened file path.
#[tauri::command]
pub async fn save_last_file(app: tauri::AppHandle, file_path: Option<String>) -> Result<(), String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    match file_path {
        Some(p) => store.set("lastOpenedFile", serde_json::Value::String(p)),
        None    => { store.delete("lastOpenedFile"); }
    }
    store.save().map_err(|e| e.to_string())?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Log commands
// ---------------------------------------------------------------------------

/// Return the path to the app log file so the user (or tooling) can read it.
#[tauri::command]
pub fn get_log_path(app: tauri::AppHandle) -> Result<String, String> {
    use tauri::Manager;
    app.path()
        .app_log_dir()
        .map(|d| d.join("app.log").to_string_lossy().to_string())
        .map_err(|e| e.to_string())
}

/// Bridge: frontend JS errors → Rust log file.
#[tauri::command]
pub fn log_frontend_error(context: String, message: String) {
    log::error!("[frontend::{}] {}", context, message);
}

/// Bridge: frontend JS info → Rust log file.
#[tauri::command]
pub fn log_frontend_info(context: String, message: String) {
    log::info!("[frontend::{}] {}", context, message);
}

// ---------------------------------------------------------------------------
// Generation — ElevenLabs API
// ---------------------------------------------------------------------------

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct StitchProgressEvent {
    message: String,
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct GenerationProgressEvent {
    section_index:    u32,
    total_sections:   u32,
    voice_name:       String,
    stability:        f64,
    similarity_boost: f64,
    quality:          String,
    dict_version_id:  Option<String>,
}

struct SectionData {
    text:             String,
    voice_id:         String,
    voice_name:       String,
    tts_model:        String,
    stability:        f64,
    similarity_boost: f64,
    speed:            f64,
    pronunciation_dictionary_locators: Option<Vec<PronunciationDictionaryLocator>>,
}

fn extract_section_from_manifest(manifest: &AudioManifest, section_id: &str) -> Result<SectionData, String> {
    for section in &manifest.sections {
        if let ManifestSection::Speech(s) = section {
            if s.id == section_id {
                return Ok(SectionData {
                    text:             s.text.clone(),
                    voice_id:         s.voice_id.clone(),
                    voice_name:       s.voice_name.clone(),
                    tts_model:        s.tts_model.clone(),
                    stability:        s.stability,
                    similarity_boost: s.similarity_boost,
                    speed:            s.speed.unwrap_or(1.0),
                    pronunciation_dictionary_locators: manifest.pronunciation_dictionary_locators.clone(),
                });
            }
        }
    }
    Err(format!("Speech section {section_id} not found"))
}

fn extract_section(master: &MasterManifest, section_id: &str) -> Result<(SectionData, Option<Vec<PronunciationDictionaryLocator>>), String> {
    // Search all chapters and special sections
    let all_manifests: Vec<&AudioManifest> = master.chapters.iter()
        .chain(master.opening_credits.iter())
        .chain(master.closing_credits.iter())
        .chain(master.about_author.iter())
        .collect();

    for manifest in all_manifests {
        for section in &manifest.sections {
            if let ManifestSection::Speech(s) = section {
                if s.id == section_id {
                    return Ok((SectionData {
                        text:             s.text.clone(),
                        voice_id:         s.voice_id.clone(),
                        voice_name:       s.voice_name.clone(),
                        tts_model:        s.tts_model.clone(),
                        stability:        s.stability,
                        similarity_boost: s.similarity_boost,
                        speed:            s.speed.unwrap_or(1.0),
                        pronunciation_dictionary_locators: manifest.pronunciation_dictionary_locators.clone(),
                    }, manifest.pronunciation_dictionary_locators.clone()));
                }
            }
        }
    }
    Err(format!("Speech section {section_id} not found"))
}

/// Return the ElevenLabs API key from settings, failing if not set.
fn load_api_key(app: &tauri::AppHandle) -> Result<String, String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    let key = store.get("elevenlabsApiKey")
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_default();
    if key.is_empty() {
        let msg = "ElevenLabs API key not set — go to Settings first.";
        log::warn!("generate blocked: {msg}");
        return Err(msg.to_string());
    }
    Ok(key)
}

/// Determine where to write audio files.
///
/// Priority:
///   1. Parent directory of the currently-open manifest file (req #1)
///   2. `outputDir` from settings (fallback for clipboard/HTTP manifests)
fn resolve_output_dir(
    app: &tauri::AppHandle,
    file_path_state: &SharedFilePath,
) -> Result<String, String> {
    {
        let guard = file_path_state.lock().unwrap();
        if let Some(fp) = guard.as_ref() {
            if let Some(parent) = std::path::Path::new(fp).parent() {
                let dir = parent.to_string_lossy().to_string();
                if !dir.is_empty() {
                    return Ok(dir);
                }
            }
        }
    }
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    let output_dir = store.get("outputDir")
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_default();
    if output_dir.is_empty() {
        let msg = "Output directory not set — open a manifest file or configure Settings.";
        log::warn!("generate blocked: {msg}");
        return Err(msg.to_string());
    }
    Ok(output_dir)
}

fn quality_to_output_format(quality: &str) -> &'static str {
    match quality {
        "vlow" => "mp3_22050_32",
        "med"  => "mp3_44100_128",
        "high" => "mp3_44100_192",
        _      => "mp3_44100_64",   // "low" and anything else
    }
}

/// Returns a rank for a format string or quality label (higher = better quality).
fn format_rank(s: &str) -> u8 {
    match s {
        "mp3_22050_32" | "vlow"  => 0,
        "mp3_44100_128" | "med"  => 2,
        "mp3_44100_192" | "high" => 3,
        _                        => 1,   // mp3_44100_64, "low", or unknown
    }
}

async fn call_elevenlabs(
    api_key: &str,
    data: &SectionData,
    output_format: &str,
) -> Result<Vec<u8>, String> {
    let url = format!(
        "https://api.elevenlabs.io/v1/text-to-speech/{}?output_format={}",
        data.voice_id, output_format
    );

    let mut payload = serde_json::json!({
        "text": data.text,
        "model_id": data.tts_model,
        "voice_settings": {
            "stability": data.stability,
            "similarity_boost": data.similarity_boost,
            "speed": data.speed
        }
    });

    if let Some(locators) = &data.pronunciation_dictionary_locators {
        if !locators.is_empty() {
            let api_locators: Vec<serde_json::Value> = locators.iter().map(|l| {
                serde_json::json!({
                    "pronunciation_dictionary_id": l.pronunciation_dictionary_id,
                    "version_id": l.version_id
                })
            }).collect();
            payload["pronunciation_dictionary_locators"] = serde_json::json!(api_locators);
        }
    }

    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
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

    let audio = resp.bytes().await.map_err(|e| format!("Read response: {e}"))?.to_vec();
    Ok(audio)
}

fn apply_result(
    master: &mut MasterManifest,
    section_id: &str,
    file_path: String,
    format: &str,
) {
    // Search all manifests
    for ch in &mut master.chapters {
        for section in &mut ch.sections {
            if let ManifestSection::Speech(s) = section {
                if s.id == section_id {
                    let files = s.audio_files.get_or_insert_with(HashMap::new);
                    files.insert(format.to_string(), file_path);
                    s.is_dirty = None;
                    return;
                }
            }
        }
    }
    for slot in [&mut master.opening_credits, &mut master.closing_credits, &mut master.about_author] {
        if let Some(am) = slot {
            for section in &mut am.sections {
                if let ManifestSection::Speech(s) = section {
                    if s.id == section_id {
                        let files = s.audio_files.get_or_insert_with(HashMap::new);
                        files.insert(format.to_string(), file_path);
                        s.is_dirty = None;
                        return;
                    }
                }
            }
        }
    }
}

/// Write the manifest to disk at the current file path, if one is set.
/// Failures are logged but not propagated — autosave is best-effort.
fn try_autosave(state: &SharedManifest, file_path_state: &SharedFilePath) {
    let path = {
        let guard = file_path_state.lock().unwrap();
        guard.clone()
    };
    let Some(path) = path else { return };

    let json = {
        let guard = state.lock().unwrap();
        let Some(manifest) = guard.as_ref() else { return };
        let mut to_save = manifest.clone();
        if let Some(parent) = std::path::Path::new(&path).parent() {
            relativize_manifest_paths(&mut to_save, parent);
        }
        match serde_json::to_string_pretty(&to_save) {
            Ok(j) => j,
            Err(e) => { log::warn!("Autosave serialise failed: {e}"); return; }
        }
    };

    if let Err(e) = std::fs::write(&path, json) {
        log::warn!("Autosave write failed ({path}): {e}");
    } else {
        log::info!("Autosaved to {path}");
    }
}

fn section_done_for_format(sp: &SpeechSection, format: &str) -> bool {
    sp.audio_files.as_ref().map_or(false, |m| m.contains_key(format))
        && !sp.is_dirty.unwrap_or(false)
}

/// Returns `true` if every speech section has been generated at `format` and none are dirty.
fn all_sections_done(manifest: &AudioManifest, format: &str) -> bool {
    manifest.sections.iter().all(|s| match s {
        ManifestSection::Speech(sp) => section_done_for_format(sp, format),
        ManifestSection::Silence(_) => true,
    })
}

/// Core stitch logic for a single AudioManifest (chapter or special section).
async fn stitch_manifest(
    app: &tauri::AppHandle,
    output_dir: &str,
    manifest: &AudioManifest,
    format: &str,
) -> Result<String, String> {
    let existing_out_path = manifest.audio_files.as_ref().and_then(|m| m.get(format)).cloned();

    // Validate: every speech section must be generated at the target format and not dirty.
    for s in &manifest.sections {
        if let ManifestSection::Speech(sp) = s {
            if !section_done_for_format(sp, format) {
                return Err(format!(
                    "Section \"{}\" (#{}…) has not been generated yet (or was edited).",
                    sp.voice_name,
                    &sp.id[..8.min(sp.id.len())]
                ));
            }
        }
    }

    let _ = app.emit("stitch-progress", StitchProgressEvent {
        message: "Generating silence clips…".to_string(),
    });
    let (input_paths, _silence_updates) = crate::stitch::prepare_inputs(
        output_dir,
        &manifest.sections,
        format,
    ).await?;

    let use_two_pass = matches!(format, "mp3_44100_128" | "mp3_44100_192");

    if use_two_pass {
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("Analyzing loudness of {} segments… (pass 1/2)", input_paths.len()),
        });
        let measurement = crate::stitch::measure_loudness(output_dir, &input_paths).await?;
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("Encoding {} segments with measured normalization… (pass 2/2)", input_paths.len()),
        });
        crate::stitch::concat_inputs(
            output_dir,
            existing_out_path.as_deref(),
            &manifest.document_title,
            &manifest.tab_name,
            format,
            &input_paths,
            Some(&measurement),
        ).await
    } else {
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("Mixing and normalizing {} segments…", input_paths.len()),
        });
        crate::stitch::concat_inputs(
            output_dir,
            existing_out_path.as_deref(),
            &manifest.document_title,
            &manifest.tab_name,
            format,
            &input_paths,
            None,
        ).await
    }
}

/// Core stitch logic shared by `stitch_audio` and the auto-stitch path.
async fn stitch_internal(
    app: &tauri::AppHandle,
    state: &SharedManifest,
    file_path_state: &SharedFilePath,
    format: &str,
    chapter_tab_name: Option<&str>,
) -> Result<String, String> {
    let output_dir = resolve_output_dir(app, file_path_state)?;

    let (manifest_clone, existing_out_path) = {
        let guard = state.lock().unwrap();
        let master = guard.as_ref().ok_or("No manifest loaded")?;
        let ch = get_chapter_sections(master, chapter_tab_name)
            .ok_or("No chapter found")?;
        (ch.clone(), ch.audio_files.as_ref().and_then(|m| m.get(format)).cloned())
    };

    // Validate: every speech section must be generated at the target format and not dirty.
    for s in &manifest_clone.sections {
        if let ManifestSection::Speech(sp) = s {
            if !section_done_for_format(sp, format) {
                return Err(format!(
                    "Section \"{}\" (#{}…) has not been generated yet (or was edited).",
                    sp.voice_name,
                    &sp.id[..8.min(sp.id.len())]
                ));
            }
        }
    }

    // Phase 1: resolve all segments — generate any missing silence clips.
    let _ = app.emit("stitch-progress", StitchProgressEvent {
        message: "Generating silence clips…".to_string(),
    });
    let (input_paths, silence_updates) = crate::stitch::prepare_inputs(
        &output_dir,
        &manifest_clone.sections,
        format,
    ).await?;

    // Phase 2 (and optional phase 3 for med/high): loudness-normalise and encode.
    let use_two_pass = matches!(format, "mp3_44100_128" | "mp3_44100_192");

    let out_path = if use_two_pass {
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("Analyzing loudness of {} segments… (pass 1/2)", input_paths.len()),
        });
        let measurement = crate::stitch::measure_loudness(&output_dir, &input_paths).await?;
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("Encoding {} segments with measured normalization… (pass 2/2)", input_paths.len()),
        });
        crate::stitch::concat_inputs(
            &output_dir,
            existing_out_path.as_deref(),
            &manifest_clone.document_title,
            &manifest_clone.tab_name,
            format,
            &input_paths,
            Some(&measurement),
        ).await?
    } else {
        let _ = app.emit("stitch-progress", StitchProgressEvent {
            message: format!("Mixing and normalizing {} segments…", input_paths.len()),
        });
        crate::stitch::concat_inputs(
            &output_dir,
            existing_out_path.as_deref(),
            &manifest_clone.document_title,
            &manifest_clone.tab_name,
            format,
            &input_paths,
            None,
        ).await?
    };

    // Measure duration from the output file (best-effort — failure doesn't abort the stitch).
    let duration_secs = crate::stitch::measure_duration(&out_path).await.ok();

    // Write silence file paths, stitched path, and measurements back into the manifest.
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        let ch = get_chapter_sections_mut(master, chapter_tab_name)
            .ok_or("No chapter found")?;
        ch.audio_files.get_or_insert_with(HashMap::new).insert(format.to_string(), out_path.clone());
        ch.duration_secs = duration_secs;
        if use_two_pass {
            ch.loudness_lufs  = Some(-20.0);
            ch.true_peak_dbtp = Some(-3.0);
        }
        for (silence_id, silence_path) in &silence_updates {
            for section in &mut ch.sections {
                if let ManifestSection::Silence(s) = section {
                    if &s.id == silence_id {
                        s.audio_file_path = Some(silence_path.clone());
                    }
                }
            }
        }
    }

    try_autosave(state, file_path_state);
    log::info!("stitch_audio: output → {out_path}");
    Ok(out_path)
}

/// Write the current manifest as pretty-printed JSON to the given absolute path.
///
/// Uses native Rust I/O to bypass the Tauri fs-plugin path-scope restrictions,
/// which only allow writes inside the app's data directories.
#[tauri::command]
pub fn save_manifest_to_file(
    state: State<'_, SharedManifest>,
    file_path: String,
) -> Result<(), String> {
    let mut to_save = {
        let guard = state.lock().unwrap();
        guard.as_ref().ok_or("No manifest loaded")?.clone()
    };
    if let Some(parent) = std::path::Path::new(&file_path).parent() {
        relativize_manifest_paths(&mut to_save, parent);
    }
    let json = serde_json::to_string_pretty(&to_save).map_err(|e| e.to_string())?;
    std::fs::write(&file_path, json).map_err(|e| format!("Write failed: {e}"))?;
    log::info!("Manifest saved to {file_path}");
    Ok(())
}

/// Generate audio for a single speech section via ElevenLabs TTS.
#[tauri::command]
pub async fn generate_section(
    app: tauri::AppHandle,
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    section_id: String,
    quality: Option<String>,
) -> Result<String, String> {
    let api_key    = load_api_key(&app)?;
    let output_dir = resolve_output_dir(&app, &file_path_state)?;
    let quality = quality.unwrap_or_else(|| "low".to_string());
    let output_format = quality_to_output_format(&quality);

    let (data, dict_version_id) = {
        let guard = state.lock().unwrap();
        let master = guard.as_ref().ok_or("No manifest loaded")?;
        let (d, locators) = extract_section(master, &section_id)?;
        let v = locators.as_ref()
            .and_then(|l| l.first())
            .map(|loc| loc.version_id.clone());
        (d, v)
    };

    // Section files go in a dedicated subdirectory so the manifest root stays clean.
    let sections_dir = format!("{output_dir}/sections");
    std::fs::create_dir_all(&sections_dir)
        .map_err(|e| format!("Cannot create sections dir: {e}"))?;
    let file_path = format!("{sections_dir}/{output_format}_{section_id}.mp3");

    log::info!("Generating section {section_id} quality={quality}");

    let _ = app.emit("generation-progress", GenerationProgressEvent {
        section_index:    1,
        total_sections:   1,
        voice_name:       data.voice_name.clone(),
        stability:        data.stability,
        similarity_boost: data.similarity_boost,
        quality:          quality.clone(),
        dict_version_id,
    });

    let audio = call_elevenlabs(&api_key, &data, output_format).await.map_err(|e| {
        log::error!("Section {section_id} failed: {e}");
        e
    })?;

    std::fs::write(&file_path, &audio)
        .map_err(|e| format!("Write audio: {e}"))?;

    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        apply_result(master, &section_id, file_path.clone(), output_format);
    }

    log::info!("Section {section_id} done → {file_path}");

    // Autosave the manifest with the updated audio path.
    try_autosave(&state, &file_path_state);

    // Auto-stitch if every section in the owning chapter is now done at this format.
    let (should_stitch, chapter_tab_name) = {
        let guard = state.lock().unwrap();
        if let Some(master) = guard.as_ref() {
            // Find which chapter this section belongs to
            let tab = master.chapters.iter()
                .find(|ch| ch.sections.iter().any(|s| s.id() == section_id))
                .map(|ch| ch.tab_name.clone());
            if let Some(tab_name) = tab {
                let done = master.chapters.iter()
                    .find(|ch| ch.tab_name == tab_name)
                    .map_or(false, |ch| all_sections_done(ch, output_format));
                (done, Some(tab_name))
            } else {
                (false, None)
            }
        } else {
            (false, None)
        }
    };
    if should_stitch {
        if let Err(e) = stitch_internal(&app, &state, &file_path_state, output_format, chapter_tab_name.as_deref()).await {
            log::warn!("Auto-stitch after section gen failed: {e}");
        }
    }

    Ok(file_path)
}

/// Generate all ungenerated (or dirty) speech sections in document order.
///
/// Operates on the specified chapter (or first chapter if None).
/// After all sections are generated, automatically stitches the full audio file.
///
/// Returns the number of sections generated.
#[tauri::command]
pub async fn generate_all_remaining(
    app: tauri::AppHandle,
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    quality: Option<String>,
    chapter_tab_name: Option<String>,
) -> Result<u32, String> {
    let api_key    = load_api_key(&app)?;
    let output_dir = resolve_output_dir(&app, &file_path_state)?;
    let quality = quality.unwrap_or_else(|| "low".to_string());
    let output_format = quality_to_output_format(&quality);
    let mut generated = 0u32;

    // Count up front so we can show "x of N" in the progress event.
    let total_to_generate: u32 = {
        let guard = state.lock().unwrap();
        let master = guard.as_ref().ok_or("No manifest loaded")?;
        let chapter = get_chapter_sections(master, chapter_tab_name.as_deref())
            .ok_or("No chapter found")?;
        chapter.sections.iter().filter(|s| match s {
            ManifestSection::Speech(sp) => !section_done_for_format(sp, output_format),
            _ => false,
        }).count() as u32
    };

    log::info!("generate_all_remaining: starting ({total_to_generate} sections, format={output_format})");

    // Ensure sections/ subdirectory exists before entering the loop.
    let sections_dir = format!("{output_dir}/sections");
    std::fs::create_dir_all(&sections_dir)
        .map_err(|e| format!("Cannot create sections dir: {e}"))?;

    loop {
        // Find the next unblocked section that needs generation at this format.
        let (section_id, data) = {
            let guard = state.lock().unwrap();
            let Some(master) = guard.as_ref() else { break };
            let Some(chapter) = get_chapter_sections(master, chapter_tab_name.as_deref()) else { break };

            let blocked = compute_blocked(chapter, output_format);

            let found = chapter.sections.iter().find_map(|s| {
                if let ManifestSection::Speech(sp) = s {
                    let needs = !section_done_for_format(sp, output_format);
                    if needs && !blocked.contains(&sp.id) {
                        return Some(sp.id.clone());
                    }
                }
                None
            });

            let Some(id) = found else { break };
            let data = extract_section_from_manifest(chapter, &id)?;
            (id, data)
        };

        let file_path = format!("{sections_dir}/{output_format}_{section_id}.mp3");

        let _ = app.emit("generation-progress", GenerationProgressEvent {
            section_index:    generated + 1,
            total_sections:   total_to_generate,
            voice_name:       data.voice_name.clone(),
            stability:        data.stability,
            similarity_boost: data.similarity_boost,
            quality:          quality.clone(),
            dict_version_id:  None,
        });

        let audio = match call_elevenlabs(&api_key, &data, output_format).await {
            Ok(v) => v,
            Err(err) => {
                log::error!("generate_all_remaining: section {section_id} failed: {err}");
                return Err(err);
            }
        };

        std::fs::write(&file_path, &audio)
            .map_err(|e| format!("Write {section_id}: {e}"))?;

        {
            let mut guard = state.lock().unwrap();
            let master = guard.as_mut().ok_or("No manifest loaded")?;
            apply_result(master, &section_id, file_path, output_format);
        }

        try_autosave(&state, &file_path_state);
        log::info!("Generated section {section_id}");
        generated += 1;
    }

    log::info!("generate_all_remaining: done, {generated} sections");

    // Stitch if we generated new sections, or if all sections were already done.
    let all_done = {
        let guard = state.lock().unwrap();
        guard.as_ref().and_then(|m| get_chapter_sections(m, chapter_tab_name.as_deref()))
            .map_or(false, |ch| all_sections_done(ch, output_format))
    };
    if generated > 0 || all_done {
        stitch_internal(&app, &state, &file_path_state, output_format, chapter_tab_name.as_deref()).await
            .map_err(|e| {
                log::error!("Auto-stitch after generate_all_remaining failed: {e}");
                e
            })?;
    }

    Ok(generated)
}

/// Clear audio entries whose format rank is strictly below the target quality level.
/// Entries at or above the target rank are preserved.
#[tauri::command]
pub fn clear_lower_quality(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    quality: String,
) -> Result<u32, String> {
    let target_rank = format_rank(&quality);
    let mut cleared = 0u32;
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;

        let all_manifests: Vec<&mut AudioManifest> = master.chapters.iter_mut()
            .chain(master.opening_credits.iter_mut())
            .chain(master.closing_credits.iter_mut())
            .chain(master.about_author.iter_mut())
            .collect();

        for manifest in all_manifests {
            for section in &mut manifest.sections {
                if let ManifestSection::Speech(s) = section {
                    if let Some(files) = s.audio_files.as_mut() {
                        let before = files.len();
                        files.retain(|fmt, _| format_rank(fmt) >= target_rank);
                        let removed = before - files.len();
                        if removed > 0 {
                            cleared += 1;
                            if files.is_empty() {
                                s.audio_files = None;
                                s.is_dirty = None;
                            }
                        }
                    }
                }
            }
        }
    }
    if cleared > 0 {
        try_autosave(&state, &file_path_state);
    }
    log::info!("clear_lower_quality({quality}): cleared entries from {cleared} sections");
    Ok(cleared)
}

// ---------------------------------------------------------------------------
// Stitch — concatenate generated audio into a single file via ffmpeg
// ---------------------------------------------------------------------------

/// Concatenate all generated sections (speech + silence) into one .mp3.
///
/// Requires all speech sections to be generated at `quality` and not dirty.
/// Calls ffmpeg which must be on PATH. Returns the path to the stitched file.
#[tauri::command]
pub async fn stitch_audio(
    app: tauri::AppHandle,
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    quality: Option<String>,
    chapter_tab_name: Option<String>,
) -> Result<String, String> {
    let quality = quality.unwrap_or_else(|| "low".to_string());
    let output_format = quality_to_output_format(&quality);
    log::info!("stitch_audio: starting (format={output_format})");
    stitch_internal(&app, &state, &file_path_state, output_format, chapter_tab_name.as_deref()).await
}

// ---------------------------------------------------------------------------
// Pronunciation Dictionary — ElevenLabs API
// ---------------------------------------------------------------------------

/// Fetch metadata and rules for an ElevenLabs pronunciation dictionary.
#[tauri::command]
pub async fn get_dictionary_info(
    app: tauri::AppHandle,
    dictionary_id: String,
) -> Result<serde_json::Value, String> {
    let store = app.store(STORE_FILE).map_err(|e| e.to_string())?;
    let api_key = store
        .get("elevenlabsApiKey")
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_default();

    if api_key.is_empty() {
        return Err("ElevenLabs API key not set — go to Settings first.".to_string());
    }

    let url = format!(
        "https://api.elevenlabs.io/v1/pronunciation-dictionaries/{}",
        dictionary_id
    );

    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .header("xi-api-key", &api_key)
        .send()
        .await
        .map_err(|e| format!("Request failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("ElevenLabs API {status}: {body}"));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("Parse response: {e}"))?;

    let name = json
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let latest_version_id = json
        .get("latest_version_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let rules: Vec<serde_json::Value> = json
        .get("rules")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .map(|rule| {
                    let string_to_replace = rule
                        .get("string_to_replace")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let rule_type = rule
                        .get("type")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                    let (replace_with, alphabet) = if rule_type == "phoneme" {
                        let phoneme = rule
                            .get("phoneme")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string();
                        let alpha = rule
                            .get("alphabet")
                            .and_then(|v| v.as_str())
                            .unwrap_or("ipa")
                            .to_string();
                        (phoneme, alpha)
                    } else {
                        let alias = rule
                            .get("alias")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string();
                        (alias, String::new())
                    };
                    serde_json::json!({
                        "stringToReplace": string_to_replace,
                        "replaceWith": replace_with,
                        "alphabet": alphabet,
                    })
                })
                .collect()
        })
        .unwrap_or_default();

    Ok(serde_json::json!({
        "name": name,
        "id": dictionary_id,
        "latestVersionId": latest_version_id,
        "rules": rules,
    }))
}

/// Update the manifest's pronunciation dictionary locator to use the latest version ID.
#[tauri::command]
pub async fn resolve_dictionary_to_latest(
    app: tauri::AppHandle,
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    dictionary_id: String,
) -> Result<String, String> {
    let api_key = load_api_key(&app)?;

    let url = format!(
        "https://api.elevenlabs.io/v1/pronunciation-dictionaries/{}",
        dictionary_id
    );
    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .header("xi-api-key", &api_key)
        .send()
        .await
        .map_err(|e| format!("Request failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("ElevenLabs API {status}: {body}"));
    }

    let json: serde_json::Value = resp.json().await.map_err(|e| format!("Parse response: {e}"))?;
    let latest_version_id = json
        .get("latest_version_id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| "ElevenLabs response missing latest_version_id".to_string())?
        .to_string();

    {
        let mut guard = state.lock().unwrap();
        if let Some(master) = guard.as_mut() {
            for ch in &mut master.chapters {
                if let Some(locators) = ch.pronunciation_dictionary_locators.as_mut() {
                    for locator in locators.iter_mut() {
                        if locator.pronunciation_dictionary_id == dictionary_id {
                            locator.version_id = latest_version_id.clone();
                        }
                    }
                }
            }
        }
    }

    try_autosave(&state, &file_path_state);
    log::info!("Dictionary {dictionary_id} resolved to latest version {latest_version_id}");
    Ok(latest_version_id)
}

// ---------------------------------------------------------------------------
// Pronunciation Dictionary — list and set
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DictionaryInfo {
    pub id: String,
    pub name: String,
    pub latest_version_id: String,
}

#[tauri::command]
pub async fn list_dictionaries(app: tauri::AppHandle) -> Result<Vec<DictionaryInfo>, String> {
    let store = app.store("settings.json").map_err(|e| e.to_string())?;
    let api_key = store
        .get("elevenlabsApiKey")
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_default();
    if api_key.is_empty() {
        return Err("ElevenLabs API key not set.".to_string());
    }

    let client = reqwest::Client::new();
    let mut all: Vec<DictionaryInfo> = Vec::new();
    let mut page_size = 100u32;
    let mut cursor: Option<String> = None;

    loop {
        let mut url = format!(
            "https://api.elevenlabs.io/v1/pronunciation-dictionaries?page_size={page_size}"
        );
        if let Some(ref c) = cursor {
            url.push_str(&format!("&cursor={c}"));
        }

        let resp = client
            .get(&url)
            .header("xi-api-key", &api_key)
            .send()
            .await
            .map_err(|e| format!("Request failed: {e}"))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(format!("ElevenLabs API {status}: {body}"));
        }

        let json: serde_json::Value = resp.json().await.map_err(|e| format!("Parse: {e}"))?;
        let dicts = json["pronunciation_dictionaries"]
            .as_array()
            .ok_or("Unexpected response format")?;

        for d in dicts {
            let id = d["id"].as_str().unwrap_or("").to_string();
            let name = d["name"].as_str().unwrap_or("").to_string();
            let latest_version_id = d["latest_version_id"].as_str().unwrap_or("").to_string();
            if !id.is_empty() {
                all.push(DictionaryInfo { id, name, latest_version_id });
            }
        }

        cursor = json.get("next_cursor")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string());
        if cursor.is_none() { break; }
        page_size = 100;
    }

    Ok(all)
}

#[tauri::command]
pub fn set_dictionary(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    dictionary_id: String,
    version_id: String,
) -> Result<(), String> {
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        let new_locators = if dictionary_id.is_empty() {
            None
        } else {
            Some(vec![crate::types::PronunciationDictionaryLocator {
                pronunciation_dictionary_id: dictionary_id,
                version_id,
            }])
        };
        // Apply to every chapter and special section so all TTS calls use the same dict.
        for ch in &mut master.chapters {
            ch.pronunciation_dictionary_locators = new_locators.clone();
        }
        if let Some(ref mut s) = master.opening_credits  { s.pronunciation_dictionary_locators = new_locators.clone(); }
        if let Some(ref mut s) = master.closing_credits  { s.pronunciation_dictionary_locators = new_locators.clone(); }
        if let Some(ref mut s) = master.about_author     { s.pronunciation_dictionary_locators = new_locators.clone(); }
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

// ---------------------------------------------------------------------------
// Voice listing — ElevenLabs API
// ---------------------------------------------------------------------------

/// Fetch all available voices from ElevenLabs and return minimal {voiceId, name} records.
#[tauri::command]
pub async fn list_voices(app: tauri::AppHandle) -> Result<Vec<VoiceInfo>, String> {
    let api_key = load_api_key(&app)?;
    let client = reqwest::Client::new();
    let mut all_voices: Vec<VoiceInfo> = Vec::new();
    let mut next_cursor: Option<String> = None;

    loop {
        let mut url = "https://api.elevenlabs.io/v1/voices?show_legacy=true".to_string();
        if let Some(ref cursor) = next_cursor {
            url.push_str(&format!("&next_cursor={cursor}"));
        }

        let resp = client
            .get(&url)
            .header("xi-api-key", &api_key)
            .send()
            .await
            .map_err(|e| format!("Request failed: {e}"))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            return Err(format!("ElevenLabs API {status}: {body}"));
        }

        let json: serde_json::Value = resp
            .json()
            .await
            .map_err(|e| format!("Parse response: {e}"))?;

        let page = json["voices"]
            .as_array()
            .ok_or("Unexpected voices response")?
            .iter()
            .map(|v| VoiceInfo {
                voice_id: v["voice_id"].as_str().unwrap_or("").to_string(),
                name:     v["name"].as_str().unwrap_or("").to_string(),
            })
            .filter(|v| !v.voice_id.is_empty());

        all_voices.extend(page);

        next_cursor = json
            .get("next_cursor")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string());

        if next_cursor.is_none() {
            break;
        }
    }

    all_voices.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    Ok(all_voices)
}

// ---------------------------------------------------------------------------
// Playback & file reveal
// ---------------------------------------------------------------------------

/// Open the file's containing folder in Finder with the file selected.
#[tauri::command]
pub async fn reveal_in_finder(file_path: String) -> Result<(), String> {
    log::info!("Revealing in Finder: {file_path}");
    tokio::process::Command::new("open")
        .args(["-R", &file_path])
        .spawn()
        .map_err(|e| format!("open -R failed: {e}"))?;
    Ok(())
}

/// Read an audio file from disk and return it as a base64-encoded string.
#[tauri::command]
pub fn read_audio_base64(file_path: String) -> Result<String, String> {
    use base64::Engine as _;
    let bytes = std::fs::read(&file_path)
        .map_err(|e| format!("Cannot read audio file: {e}"))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(&bytes))
}

// ---------------------------------------------------------------------------
// Voice-stitching constraint
// ---------------------------------------------------------------------------

pub fn compute_blocked(manifest: &AudioManifest, format: &str) -> HashSet<String> {
    let mut voice_blocked: HashMap<String, bool> = HashMap::new();
    let mut blocked = HashSet::new();

    for section in &manifest.sections {
        if let ManifestSection::Speech(s) = section {
            let prior = voice_blocked.get(&s.voice_id).copied().unwrap_or(false);
            let done  = section_done_for_format(s, format);

            if prior {
                blocked.insert(s.id.clone());
                voice_blocked.insert(s.voice_id.clone(), true);
            } else if !done {
                voice_blocked.insert(s.voice_id.clone(), true);
            }
        }
    }
    blocked
}

#[tauri::command]
pub fn get_blocked_sections(
    state: State<'_, SharedManifest>,
    quality: Option<String>,
    chapter_tab_name: Option<String>,
) -> Vec<String> {
    let quality = quality.unwrap_or_else(|| "low".to_string());
    let format = quality_to_output_format(&quality);
    let guard = state.lock().unwrap();
    match guard.as_ref() {
        None => vec![],
        Some(m) => {
            match get_chapter_sections(m, chapter_tab_name.as_deref()) {
                None => vec![],
                Some(ch) => compute_blocked(ch, format).into_iter().collect(),
            }
        }
    }
}

/// Scan sections for consecutive speech sections with no silence between them and
/// insert a silence of `duration_ms` at each gap. Returns the number of silences added.
#[tauri::command]
pub fn add_silences_between_speech(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    duration_ms: u32,
    chapter_tab_name: Option<String>,
) -> Result<u32, String> {
    use uuid::Uuid;
    let mut added = 0u32;
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        let chapter = get_chapter_sections_mut(master, chapter_tab_name.as_deref())
            .ok_or("No chapter found")?;

        // Walk backwards so inserted indices don't shift upcoming positions.
        let len = chapter.sections.len();
        let mut i = len.saturating_sub(1);
        loop {
            if i == 0 { break; }
            let prev_is_speech = matches!(&chapter.sections[i - 1], ManifestSection::Speech(_));
            let curr_is_speech = matches!(&chapter.sections[i],     ManifestSection::Speech(_));
            if prev_is_speech && curr_is_speech {
                let new_id = Uuid::new_v4().simple().to_string();
                chapter.sections.insert(i, ManifestSection::Silence(
                    crate::types::SilenceSection { id: new_id, duration_ms, audio_file_path: None }
                ));
                added += 1;
            }
            i -= 1;
        }
    }
    if added > 0 {
        try_autosave(&state, &file_path_state);
    }
    log::info!("add_silences_between_speech: inserted {added} silences of {duration_ms}ms");
    Ok(added)
}

/// Insert a new empty speech section immediately above or below the given section.
#[tauri::command]
pub fn insert_speech(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    section_id: String,
    position: String,   // "above" | "below"
) -> Result<(), String> {
    use uuid::Uuid;
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;

        // Find which manifest holds this section
        let manifest = find_manifest_for_section_mut(master, &section_id)
            .ok_or_else(|| format!("Section {section_id} not found"))?;

        let idx = manifest.sections.iter().position(|s| s.id() == section_id)
            .ok_or_else(|| format!("Section {section_id} not found"))?;

        let insert_idx = if position == "above" { idx } else { idx + 1 };
        let new_id = Uuid::new_v4().simple().to_string();

        manifest.sections.insert(insert_idx, ManifestSection::Speech(
            SpeechSection {
                id: new_id,
                text: String::new(),
                voice_id: String::new(),
                voice_name: String::new(),
                tts_model: "eleven_multilingual_v2".to_string(),
                stability: 0.5,
                similarity_boost: 0.75,
                audio_files: None,
                speed: None, is_dirty: None,
            }
        ));
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

/// Insert a new silence section immediately above or below the given section.
#[tauri::command]
pub fn insert_silence(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    section_id: String,
    position: String,   // "above" | "below"
    duration_ms: u32,
) -> Result<(), String> {
    use uuid::Uuid;
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;

        let manifest = find_manifest_for_section_mut(master, &section_id)
            .ok_or_else(|| format!("Section {section_id} not found"))?;

        let idx = manifest.sections.iter().position(|s| s.id() == section_id)
            .ok_or_else(|| format!("Section {section_id} not found"))?;

        let insert_idx = if position == "above" { idx } else { idx + 1 };
        let new_id = Uuid::new_v4().simple().to_string();

        manifest.sections.insert(insert_idx, ManifestSection::Silence(
            crate::types::SilenceSection { id: new_id, duration_ms, audio_file_path: None }
        ));
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

#[tauri::command]
pub fn delete_section(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    section_id: String,
) -> Result<(), String> {
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;

        let manifest = find_manifest_for_section_mut(master, &section_id)
            .ok_or_else(|| format!("Section {section_id} not found"))?;

        let before = manifest.sections.len();
        manifest.sections.retain(|s| s.id() != section_id);
        if manifest.sections.len() == before {
            return Err(format!("Section {section_id} not found"));
        }
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

/// Find the AudioManifest containing the given section_id.
fn find_manifest_for_section_mut<'a>(
    master: &'a mut MasterManifest,
    section_id: &str,
) -> Option<&'a mut AudioManifest> {
    for ch in &mut master.chapters {
        if ch.sections.iter().any(|s| s.id() == section_id) {
            return Some(ch);
        }
    }
    for slot in [&mut master.opening_credits, &mut master.closing_credits, &mut master.about_author] {
        if let Some(am) = slot {
            if am.sections.iter().any(|s| s.id() == section_id) {
                return Some(am);
            }
        }
    }
    None
}

#[tauri::command]
pub async fn get_subscription(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let api_key = load_api_key(&app)?;
    let client = reqwest::Client::new();
    let resp = client
        .get("https://api.elevenlabs.io/v1/user/subscription")
        .header("xi-api-key", &api_key)
        .send()
        .await
        .map_err(|e| format!("Request failed: {e}"))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("ElevenLabs API {status}: {body}"));
    }
    resp.json::<serde_json::Value>().await.map_err(|e| format!("Parse: {e}"))
}

// ---------------------------------------------------------------------------
// New commands: merge_partial_into_master
// ---------------------------------------------------------------------------

/// Normalize text for hash-based matching: collapse whitespace.
pub fn normalize_text(text: &str) -> String {
    // Normalise punctuation that Google Docs may export differently across re-exports:
    // curly quotes → straight, dashes → hyphen, non-standard spaces → regular space,
    // zero-width characters dropped.  Then collapse all whitespace runs to a single space.
    let mapped: String = text.chars().filter_map(|c| match c {
        '\u{2018}' | '\u{2019}' | '\u{02BC}'                        => Some('\''), // ' ' ʼ → '
        '\u{201C}' | '\u{201D}'                                      => Some('"'),  // " " → "
        '\u{2010}' ..= '\u{2015}' | '\u{2212}'                      => Some('-'),  // various dashes → -
        '\u{00AD}'                                                   => None,       // soft hyphen — drop
        '\u{200B}' | '\u{200C}' | '\u{200D}' | '\u{FEFF}'          => None,       // zero-width — drop
        '\u{00A0}' | '\u{202F}' | '\u{2007}' | '\u{2060}'          => Some(' '),  // non-breaking spaces → space
        c => Some(c),
    }).collect();
    mapped.split_whitespace().collect::<Vec<_>>().join(" ")
}

// ---------------------------------------------------------------------------
// File-path validation (used by Raw Edits panel)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PathCheckResult {
    pub path:   String,
    pub exists: bool,
    pub size:   u64,
}

/// For each path in `paths`, check whether the file exists and return its size.
/// Paths that do not exist get `exists: false, size: 0`.
/// This is a pure Rust std::fs call — no Tauri capability needed.
#[tauri::command]
pub fn check_file_paths(paths: Vec<String>) -> Vec<PathCheckResult> {
    paths.into_iter().map(|p| {
        match std::fs::metadata(&p) {
            Ok(m)  => PathCheckResult { path: p, exists: true,  size: m.len() },
            Err(_) => PathCheckResult { path: p, exists: false, size: 0 },
        }
    }).collect()
}

// ---------------------------------------------------------------------------
// Path relativization helpers
// ---------------------------------------------------------------------------

/// Normalize `..` and `.` components in a path without requiring it to exist on disk.
fn normalize_path(path: &std::path::Path) -> std::path::PathBuf {
    let mut comps: Vec<std::path::Component<'_>> = Vec::new();
    for comp in path.components() {
        match comp {
            std::path::Component::ParentDir => { comps.pop(); }
            std::path::Component::CurDir    => {}
            c                               => comps.push(c),
        }
    }
    comps.iter().collect()
}

/// Compute a relative path from `base` to `target`, with a `./` prefix when
/// `target` is under `base`, and `../` prefix(es) when it is above.
fn compute_relative_path(base: &std::path::Path, target: &std::path::Path) -> String {
    let base_comps: Vec<_> = base.components().collect();
    let tgt_comps:  Vec<_> = target.components().collect();
    let common = base_comps.iter().zip(tgt_comps.iter())
        .take_while(|(a, b)| a == b)
        .count();
    let up_count = base_comps.len() - common;
    let mut result = std::path::PathBuf::new();
    for _ in 0..up_count { result.push(".."); }
    for comp in &tgt_comps[common..] { result.push(comp); }
    let s = result.to_string_lossy().to_string();
    // Prefix with "./" when the target is directly under or beside base so it's
    // unambiguous relative syntax in the manifest JSON.
    if up_count == 0 && !s.is_empty() { format!("./{s}") } else { s }
}

/// Walk every stored audio file path in one AudioManifest and apply `f` to it.
fn map_audio_paths_in_manifest<F: FnMut(&str) -> String>(am: &mut AudioManifest, f: &mut F) {
    if let Some(files) = am.audio_files.as_mut() {
        for v in files.values_mut() {
            *v = f(v);
        }
    }
    for section in &mut am.sections {
        match section {
            ManifestSection::Speech(sp) => {
                if let Some(files) = sp.audio_files.as_mut() {
                    for v in files.values_mut() {
                        *v = f(v);
                    }
                }
            }
            ManifestSection::Silence(si) => {
                if let Some(p) = si.audio_file_path.as_mut() {
                    *p = f(p);
                }
            }
        }
    }
}

/// Convert every relative audio path in `master` to an absolute path by joining
/// with `base_dir`.  Absolute paths are left unchanged.  Call this immediately
/// after deserialising a manifest from disk so the in-memory state always holds
/// absolute paths.
pub fn absolutize_manifest_paths(master: &mut MasterManifest, base_dir: &std::path::Path) {
    let mut f = |p: &str| -> String {
        let path = std::path::Path::new(p);
        if path.is_absolute() {
            p.to_string()
        } else {
            // Strip leading "./" before joining; then normalize so "../silenceclips/x"
            // becomes a clean absolute path with no redundant ".." components.
            let rel = p.strip_prefix("./").unwrap_or(p);
            normalize_path(&base_dir.join(rel)).to_string_lossy().to_string()
        }
    };
    for ch in &mut master.chapters {
        map_audio_paths_in_manifest(ch, &mut f);
    }
    for slot in [&mut master.opening_credits, &mut master.closing_credits, &mut master.about_author] {
        if let Some(am) = slot {
            map_audio_paths_in_manifest(am, &mut f);
        }
    }
}

/// Convert every audio path that lives under `base_dir` to a `./`-relative path.
/// Paths outside `base_dir` (unlikely but possible for user-chosen cover images,
/// which are not touched here) are left as absolute.  Call this on a *clone* of
/// the manifest just before serialising to disk.
pub fn relativize_manifest_paths(master: &mut MasterManifest, base_dir: &std::path::Path) {
    let mut f = |p: &str| -> String {
        let path = std::path::Path::new(p);
        if path.is_absolute() {
            compute_relative_path(base_dir, path)
        } else {
            p.to_string() // already relative — leave as-is
        }
    };
    for ch in &mut master.chapters {
        map_audio_paths_in_manifest(ch, &mut f);
    }
    for slot in [&mut master.opening_credits, &mut master.closing_credits, &mut master.about_author] {
        if let Some(am) = slot {
            map_audio_paths_in_manifest(am, &mut f);
        }
    }
}

/// Merge an incoming `AudioManifest` into a chapter slot (by tab_name), doing
/// text-hash matching to preserve existing audio where the text is unchanged.
fn merge_chapter_slot_(
    master: &mut MasterManifest,
    incoming: AudioManifest,
    manifest_dir: &str,
    sections_preserved: &mut usize,
    sections_updated: &mut usize,
    orphaned_files: &mut Vec<String>,
    orphaned_dir: &mut Option<String>,
) {
    let tab_name = incoming.tab_name.clone();
    let timestamp = chrono::Utc::now().timestamp();

    if let Some(existing_idx) = master.chapters.iter().position(|c| c.tab_name == tab_name) {
        let existing = master.chapters[existing_idx].clone();
        let mut merged = incoming.clone();

        for new_section in &mut merged.sections {
            if let ManifestSection::Speech(new_sp) = new_section {
                let new_norm = normalize_text(&new_sp.text);
                if let Some(ManifestSection::Speech(old_sp)) = existing.sections.iter().find(|s| {
                    if let ManifestSection::Speech(sp) = s { normalize_text(&sp.text) == new_norm } else { false }
                }) {
                    new_sp.audio_files = old_sp.audio_files.clone();
                    new_sp.is_dirty = old_sp.is_dirty;
                    *sections_preserved += 1;
                } else {
                    *sections_updated += 1;
                }
            }
        }

        let orphan_dir_path = format!("{manifest_dir}/_orphaned/{}_{timestamp}",
            crate::stitch::sanitise_filename(&tab_name));
        let mut has_orphans = false;
        for old_section in &existing.sections {
            if let ManifestSection::Speech(old_sp) = old_section {
                let old_norm = normalize_text(&old_sp.text);
                let has_match = incoming.sections.iter().any(|s| {
                    if let ManifestSection::Speech(new_sp) = s { normalize_text(&new_sp.text) == old_norm } else { false }
                });
                if !has_match {
                    if let Some(files) = &old_sp.audio_files {
                        for path in files.values() {
                            if std::path::Path::new(path).exists() {
                                if !has_orphans {
                                    let _ = std::fs::create_dir_all(&orphan_dir_path);
                                    has_orphans = true;
                                }
                                let filename = std::path::Path::new(path)
                                    .file_name()
                                    .map(|f| f.to_string_lossy().to_string())
                                    .unwrap_or_else(|| "file.mp3".to_string());
                                let dest = format!("{orphan_dir_path}/{filename}");
                                if let Ok(()) = std::fs::rename(path, &dest) {
                                    orphaned_files.push(dest);
                                }
                            }
                        }
                    }
                }
            }
        }
        if has_orphans { *orphaned_dir = Some(orphan_dir_path); }
        master.chapters[existing_idx] = merged;
    } else {
        *sections_updated += incoming.sections.iter()
            .filter(|s| matches!(s, ManifestSection::Speech(_)))
            .count();
        master.chapters.push(incoming);
    }
}

/// Replace a special slot (openingCredits / closingCredits / aboutAuthor),
/// moving orphaned audio files to a timestamped directory.
fn merge_special_slot_(
    slot: &mut Option<AudioManifest>,
    incoming: AudioManifest,
    manifest_dir: &str,
    sections_updated: &mut usize,
    orphaned_files: &mut Vec<String>,
    orphaned_dir: &mut Option<String>,
) {
    if let Some(old_am) = slot.as_ref() {
        let timestamp = chrono::Utc::now().timestamp();
        let orphan_dir_path = format!("{manifest_dir}/_orphaned/{}_{timestamp}",
            crate::stitch::sanitise_filename(&old_am.tab_name));
        let mut has_orphans = false;
        for section in &old_am.sections {
            if let ManifestSection::Speech(sp) = section {
                if let Some(files) = &sp.audio_files {
                    for path in files.values() {
                        if std::path::Path::new(path).exists() {
                            if !has_orphans {
                                let _ = std::fs::create_dir_all(&orphan_dir_path);
                                has_orphans = true;
                            }
                            let filename = std::path::Path::new(path)
                                .file_name()
                                .map(|f| f.to_string_lossy().to_string())
                                .unwrap_or_else(|| "file.mp3".to_string());
                            let dest = format!("{orphan_dir_path}/{filename}");
                            if let Ok(()) = std::fs::rename(path, &dest) {
                                orphaned_files.push(dest);
                            }
                        }
                    }
                }
            }
        }
        if has_orphans { *orphaned_dir = Some(orphan_dir_path); }
    }
    *sections_updated += incoming.sections.iter()
        .filter(|s| matches!(s, ManifestSection::Speech(_)))
        .count();
    *slot = Some(incoming);
}

#[tauri::command]
pub async fn merge_partial_into_master(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    file_path: String,
    partial_json: String,
    create_if_missing: bool,
) -> Result<MergeResult, String> {
    let partial: PartialManifest = serde_json::from_str(&partial_json)
        .map_err(|e| format!("Invalid partial manifest JSON: {e}"))?;

    let doc_title = partial.inner()
        .ok_or("Empty partial manifest")?
        .document_title.clone();

    let manifest_dir = std::path::Path::new(&file_path)
        .parent()
        .map(|p| p.to_string_lossy().to_string())
        .unwrap_or_else(|| ".".to_string());
    let manifest_dir_path = std::path::Path::new(&manifest_dir).to_path_buf();

    // Load or create master
    let mut master = if std::path::Path::new(&file_path).exists() {
        let text = std::fs::read_to_string(&file_path)
            .map_err(|e| format!("Cannot read file: {e}"))?;
        let mut m = parse_manifest_json(&text)?;
        // Resolve relative paths so merge logic operates on absolute paths.
        absolutize_manifest_paths(&mut m, &manifest_dir_path);
        m
    } else if create_if_missing {
        MasterManifest::new(doc_title)
    } else {
        return Err(format!("File not found: {file_path}"));
    };

    let mut sections_preserved = 0usize;
    let mut sections_updated = 0usize;
    let mut orphaned_files: Vec<String> = Vec::new();
    let mut orphaned_dir: Option<String> = None;

    if let Some(ch) = partial.chapter {
        merge_chapter_slot_(&mut master, ch, &manifest_dir,
            &mut sections_preserved, &mut sections_updated,
            &mut orphaned_files, &mut orphaned_dir);
    }
    if let Some(oc) = partial.opening_credits {
        merge_special_slot_(&mut master.opening_credits, oc, &manifest_dir,
            &mut sections_updated, &mut orphaned_files, &mut orphaned_dir);
    }
    if let Some(cc) = partial.closing_credits {
        merge_special_slot_(&mut master.closing_credits, cc, &manifest_dir,
            &mut sections_updated, &mut orphaned_files, &mut orphaned_dir);
    }
    if let Some(aa) = partial.about_author {
        merge_special_slot_(&mut master.about_author, aa, &manifest_dir,
            &mut sections_updated, &mut orphaned_files, &mut orphaned_dir);
    }

    // Relativize a clone for disk; keep absolute paths in shared state.
    let mut to_save = master.clone();
    relativize_manifest_paths(&mut to_save, &manifest_dir_path);
    let json = serde_json::to_string_pretty(&to_save)
        .map_err(|e| format!("Serialise failed: {e}"))?;
    std::fs::write(&file_path, &json)
        .map_err(|e| format!("Write failed: {e}"))?;

    // Update shared state with absolute-path version.
    *state.lock().unwrap() = Some(master);
    *file_path_state.lock().unwrap() = Some(file_path.clone());

    log::info!("merge_partial_into_master: preserved={sections_preserved} updated={sections_updated} orphaned={}", orphaned_files.len());

    Ok(MergeResult {
        sections_preserved,
        sections_updated,
        orphaned_file_count: orphaned_files.len(),
        orphaned_dir,
    })
}

// ---------------------------------------------------------------------------
// set_book_metadata
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn set_book_metadata(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    metadata: BookMetadata,
) -> Result<(), String> {
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        master.metadata = Some(metadata);
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

// ---------------------------------------------------------------------------
// set_cover_image
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn set_cover_image(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    image_path: String,
) -> Result<CoverSpec, String> {
    // Get file size
    let metadata = std::fs::metadata(&image_path)
        .map_err(|e| format!("Cannot read file metadata: {e}"))?;
    let file_size_bytes = metadata.len();

    // Detect format from extension
    let ext = std::path::Path::new(&image_path)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| e.to_lowercase())
        .unwrap_or_default();
    let format = match ext.as_str() {
        "jpg" | "jpeg" => Some("jpg".to_string()),
        "png"          => Some("png".to_string()),
        "tif" | "tiff" => Some("tif".to_string()),
        _              => None,
    };

    // Read image dimensions using the image crate
    let (width, height) = read_image_dimensions(&image_path)?;

    let cover = CoverSpec {
        image_path: Some(image_path.clone()),
        width: Some(width),
        height: Some(height),
        format,
        file_size_bytes: Some(file_size_bytes),
        color_space: None,
    };

    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        master.cover = Some(cover.clone());
    }

    try_autosave(&state, &file_path_state);
    log::info!("Cover image set: {image_path} ({width}x{height})");
    Ok(cover)
}

fn read_image_dimensions(path: &str) -> Result<(u32, u32), String> {
    // Use imagesize crate or fall back to reading the file header manually.
    // Since we only have the `image` crate available, use it.
    // We use image::image_dimensions which is efficient (reads header only).
    let (w, h) = image::image_dimensions(path)
        .map_err(|e| format!("Cannot read image dimensions: {e}"))?;
    Ok((w, h))
}

// ---------------------------------------------------------------------------
// Retail sample commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn add_retail_sample_ref(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    chapter_tab_name: String,
    section_id: String,
) -> Result<(), String> {
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        let rs = master.retail_sample.get_or_insert(RetailSampleSpec { section_refs: Vec::new() });
        if !rs.section_refs.iter().any(|r| r.section_id == section_id) {
            rs.section_refs.push(SectionRef { chapter_tab_name, section_id });
        }
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

#[tauri::command]
pub fn remove_retail_sample_ref(
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    section_id: String,
) -> Result<(), String> {
    {
        let mut guard = state.lock().unwrap();
        let master = guard.as_mut().ok_or("No manifest loaded")?;
        if let Some(rs) = master.retail_sample.as_mut() {
            rs.section_refs.retain(|r| r.section_id != section_id);
        }
    }
    try_autosave(&state, &file_path_state);
    Ok(())
}

// ---------------------------------------------------------------------------
// ACX audit
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn run_acx_audit(
    state: State<'_, SharedManifest>,
    quality: String,
) -> Result<Vec<AuditResult>, String> {
    let guard = state.lock().unwrap();
    let master = guard.as_ref().ok_or("No manifest loaded")?;
    Ok(perform_acx_audit(master, &quality))
}

fn perform_acx_audit(master: &MasterManifest, quality: &str) -> Vec<AuditResult> {
    let mut results = Vec::new();

    // 1. Chapters exist
    results.push(AuditResult {
        check_id: "chapters_exist".into(),
        label: "Chapters present".into(),
        passed: !master.chapters.is_empty(),
        message: if master.chapters.is_empty() {
            "No chapters have been imported.".into()
        } else {
            format!("{} chapter(s) present.", master.chapters.len())
        },
        severity: AuditSeverity::Error,
    });

    // 2. All chapters generated
    let chapters_missing: Vec<String> = master.chapters.iter()
        .filter(|ch| !all_sections_done(ch, quality))
        .map(|ch| ch.tab_name.clone())
        .collect();
    results.push(AuditResult {
        check_id: "chapters_generated".into(),
        label: "All chapters generated".into(),
        passed: chapters_missing.is_empty(),
        message: if chapters_missing.is_empty() {
            "All chapters have audio generated.".into()
        } else {
            format!("Missing audio in: {}", chapters_missing.join(", "))
        },
        severity: AuditSeverity::Error,
    });

    // 3. Opening credits present
    results.push(AuditResult {
        check_id: "opening_credits_present".into(),
        label: "Opening Credits present".into(),
        passed: master.opening_credits.is_some(),
        message: if master.opening_credits.is_some() {
            "Opening credits imported.".into()
        } else {
            "Opening credits not imported.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 4. Opening credits generated
    let oc_generated = master.opening_credits.as_ref()
        .map_or(false, |m| all_sections_done(m, quality));
    results.push(AuditResult {
        check_id: "opening_credits_generated".into(),
        label: "Opening Credits audio generated".into(),
        passed: oc_generated,
        message: if oc_generated {
            "Opening credits audio complete.".into()
        } else {
            "Opening credits audio not fully generated.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 5. Closing credits present
    results.push(AuditResult {
        check_id: "closing_credits_present".into(),
        label: "Closing Credits present".into(),
        passed: master.closing_credits.is_some(),
        message: if master.closing_credits.is_some() {
            "Closing credits imported.".into()
        } else {
            "Closing credits not imported.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 6. Closing credits generated
    let cc_generated = master.closing_credits.as_ref()
        .map_or(false, |m| all_sections_done(m, quality));
    results.push(AuditResult {
        check_id: "closing_credits_generated".into(),
        label: "Closing Credits audio generated".into(),
        passed: cc_generated,
        message: if cc_generated {
            "Closing credits audio complete.".into()
        } else {
            "Closing credits audio not fully generated.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 7. About author (warning if not present)
    results.push(AuditResult {
        check_id: "about_author_present".into(),
        label: "About Author present (optional)".into(),
        passed: master.about_author.is_some(),
        message: if master.about_author.is_some() {
            "About Author imported.".into()
        } else {
            "About Author not imported (optional for ACX).".into()
        },
        severity: AuditSeverity::Warning,
    });

    // 8. Retail sample designated
    let has_retail = master.retail_sample.as_ref()
        .map_or(false, |rs| !rs.section_refs.is_empty());
    results.push(AuditResult {
        check_id: "retail_sample_designated".into(),
        label: "Retail sample designated".into(),
        passed: has_retail,
        message: if has_retail {
            format!("{} section(s) in retail sample.",
                master.retail_sample.as_ref().map_or(0, |rs| rs.section_refs.len()))
        } else {
            "No retail sample sections designated.".into()
        },
        severity: AuditSeverity::Warning,
    });

    // 9. Cover selected
    let has_cover = master.cover.as_ref().and_then(|c| c.image_path.as_ref()).is_some();
    results.push(AuditResult {
        check_id: "cover_selected".into(),
        label: "Cover image selected".into(),
        passed: has_cover,
        message: if has_cover {
            "Cover image selected.".into()
        } else {
            "No cover image selected.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 10. Cover dimensions
    let cover_dims_ok = master.cover.as_ref()
        .map_or(false, |c| c.width.unwrap_or(0) >= 2400 && c.height.unwrap_or(0) >= 2400);
    results.push(AuditResult {
        check_id: "cover_dimensions".into(),
        label: "Cover dimensions ≥ 2400×2400".into(),
        passed: cover_dims_ok,
        message: if let Some(c) = &master.cover {
            if let (Some(w), Some(h)) = (c.width, c.height) {
                format!("Cover is {w}×{h}px.")
            } else {
                "Cover dimensions unknown.".into()
            }
        } else {
            "No cover image.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 11. Cover format
    let cover_format_ok = master.cover.as_ref()
        .and_then(|c| c.format.as_ref())
        .map_or(false, |f| matches!(f.as_str(), "jpg" | "jpeg" | "png"));
    results.push(AuditResult {
        check_id: "cover_format".into(),
        label: "Cover format (JPG or PNG)".into(),
        passed: cover_format_ok,
        message: if let Some(fmt) = master.cover.as_ref().and_then(|c| c.format.as_ref()) {
            format!("Format: {fmt}")
        } else {
            "Cover format unknown.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 12. Cover file size ≤ 10MB
    let cover_size_ok = master.cover.as_ref()
        .and_then(|c| c.file_size_bytes)
        .map_or(true, |s| s <= 10_000_000);
    results.push(AuditResult {
        check_id: "cover_file_size".into(),
        label: "Cover file size ≤ 10 MB".into(),
        passed: cover_size_ok,
        message: if let Some(sz) = master.cover.as_ref().and_then(|c| c.file_size_bytes) {
            format!("{:.1} MB", sz as f64 / 1_000_000.0)
        } else {
            "Size unknown.".into()
        },
        severity: AuditSeverity::Warning,
    });

    // 13. ACX quality
    let quality_ok = matches!(quality, "mp3_44100_128" | "mp3_44100_192");
    results.push(AuditResult {
        check_id: "acx_quality".into(),
        label: "ACX-compliant quality (128 or 192 kbps)".into(),
        passed: quality_ok,
        message: if quality_ok {
            format!("Quality: {quality}")
        } else {
            format!("Quality '{quality}' is not ACX-compliant. Use Standard (128kbps) or High (192kbps).")
        },
        severity: AuditSeverity::Error,
    });

    // 14. Book metadata complete
    let metadata_complete = master.metadata.as_ref()
        .map_or(false, |m| !m.title.trim().is_empty() && !m.author.trim().is_empty() && !m.narrator.trim().is_empty());
    results.push(AuditResult {
        check_id: "metadata_complete".into(),
        label: "Book metadata complete (title, author, narrator)".into(),
        passed: metadata_complete,
        message: if metadata_complete {
            "Title, author, and narrator are set.".into()
        } else {
            "Title, author, and narrator are required for ACX submission.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 15. Cover square aspect ratio
    let cover_square = master.cover.as_ref()
        .map_or(true, |c| match (c.width, c.height) {
            (Some(w), Some(h)) => w == h,
            _ => true, // unknown — don't fail if we can't measure
        });
    results.push(AuditResult {
        check_id: "cover_square".into(),
        label: "Cover is square (1:1 aspect ratio)".into(),
        passed: cover_square,
        message: if let Some(c) = &master.cover {
            match (c.width, c.height) {
                (Some(w), Some(h)) if w != h => format!("Cover is {w}×{h} — must be square."),
                (Some(w), Some(h)) => format!("Cover is {w}×{h} (square ✓)."),
                _ => "Cover dimensions unknown.".into(),
            }
        } else {
            "No cover image.".into()
        },
        severity: AuditSeverity::Error,
    });

    // 16. Cover colorspace (JPEG must be RGB, not CMYK)
    let cover_is_cmyk = master.cover.as_ref()
        .and_then(|c| c.image_path.as_ref())
        .map_or(false, |p| crate::stitch::jpeg_is_cmyk(p));
    results.push(AuditResult {
        check_id: "cover_colorspace".into(),
        label: "Cover colorspace (RGB, not CMYK)".into(),
        passed: !cover_is_cmyk,
        message: if cover_is_cmyk {
            "Cover JPEG uses CMYK colorspace. ACX requires sRGB — convert in Photoshop or GIMP.".into()
        } else {
            "Cover colorspace OK.".into()
        },
        severity: AuditSeverity::Error,
    });

    // Pronunciation dictionary — recommend one is set for each chapter.
    let chapters_without_dict: Vec<&str> = master.chapters.iter()
        .filter(|ch| ch.pronunciation_dictionary_locators.as_ref().map_or(true, |v| v.is_empty()))
        .map(|ch| ch.tab_name.as_str())
        .collect();
    results.push(AuditResult {
        check_id: "pronunciation_dictionary".into(),
        label: "Pronunciation dictionary set".into(),
        passed: chapters_without_dict.is_empty(),
        message: if chapters_without_dict.is_empty() {
            "Pronunciation dictionary configured for all chapters.".into()
        } else {
            format!(
                "No pronunciation dictionary set for: {}. Use the Dictionary dropdown in the Chapter Audio panel.",
                chapters_without_dict.join(", ")
            )
        },
        severity: AuditSeverity::Warning,
    });

    results
}

// ---------------------------------------------------------------------------
// ACX package generation
// ---------------------------------------------------------------------------

/// Embed ID3 tags into an MP3 using a stream-copy ffmpeg pass (no re-encoding).
/// Writes to a temp file then renames over the original.
async fn add_id3_tags(
    file_path: &str,
    work_dir: &str,
    title: &str,
    album: &str,
    artist: &str,       // narrator
    album_artist: &str, // author
    track: Option<&str>,
    date: Option<&str>,
    publisher: Option<&str>,
    language: &str,
) -> Result<(), String> {
    let ffmpeg = crate::stitch::find_ffmpeg()
        .ok_or_else(|| "ffmpeg not found".to_string())?;
    let temp = format!("{work_dir}/_id3_tmp.mp3");

    let mut args: Vec<String> = vec![
        "-y".into(),
        "-i".into(), file_path.into(),
        "-codec:a".into(), "copy".into(),
        "-metadata".into(), format!("title={title}"),
        "-metadata".into(), format!("album={album}"),
        "-metadata".into(), format!("artist={artist}"),
        "-metadata".into(), format!("album_artist={album_artist}"),
        "-metadata".into(), format!("comment=Narrator: {artist}"),
        "-metadata".into(), format!("language={language}"),
    ];
    if let Some(t) = track    { args.extend(["-metadata".into(), format!("track={t}")]); }
    if let Some(d) = date     { args.extend(["-metadata".into(), format!("date={d}")]); }
    if let Some(p) = publisher { args.extend(["-metadata".into(), format!("publisher={p}")]); }
    args.push(temp.clone());

    let out = tokio::process::Command::new(&ffmpeg)
        .stdin(std::process::Stdio::null())
        .args(&args)
        .output()
        .await
        .map_err(|e| format!("ffmpeg ID3 pass failed: {e}"))?;

    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!("ffmpeg ID3 exited {}: {}", out.status, stderr));
    }
    std::fs::rename(&temp, file_path)
        .map_err(|e| format!("Rename after ID3 tagging: {e}"))?;
    Ok(())
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct AcxProgressEvent {
    message: String,
}

#[tauri::command]
pub async fn generate_acx_package(
    app: tauri::AppHandle,
    state: State<'_, SharedManifest>,
    file_path_state: State<'_, SharedFilePath>,
    quality: String,
) -> Result<String, String> {
    if !matches!(quality.as_str(), "mp3_44100_128" | "mp3_44100_192") {
        return Err("ACX requires standard (128kbps) or high (192kbps) quality".to_string());
    }

    let output_dir = resolve_output_dir(&app, &file_path_state)?;

    let master = {
        let guard = state.lock().unwrap();
        guard.as_ref().ok_or("No manifest loaded")?.clone()
    };

    let timestamp = chrono::Utc::now().format("%Y%m%d_%H%M%S").to_string();
    let safe_title = crate::stitch::sanitise_filename(&master.document_title);
    let acx_dir = format!("{output_dir}/{safe_title}_ACX_{timestamp}");
    std::fs::create_dir_all(&acx_dir)
        .map_err(|e| format!("Cannot create ACX directory: {e}"))?;

    // Collect book metadata fields once — used for ID3 tags on every output file.
    let (book_title, book_author, book_narrator, book_publisher, book_date, book_language) =
        if let Some(m) = &master.metadata {
            (
                m.title.clone(),
                m.author.clone(),
                m.narrator.clone(),
                m.publisher.clone(),
                m.copyright_year.map(|y| y.to_string()),
                m.language.clone(),
            )
        } else {
            (master.document_title.clone(), String::new(), String::new(), None, None, "en".to_string())
        };

    let tag = |dest: &str, title: &str, track: Option<&str>| {
        // Capture by value for async move — returns a future.
        let dest = dest.to_string();
        let title = title.to_string();
        let track = track.map(|t| t.to_string());
        let acx_dir = acx_dir.clone();
        let book_title = book_title.clone();
        let book_author = book_author.clone();
        let book_narrator = book_narrator.clone();
        let book_publisher = book_publisher.clone();
        let book_date = book_date.clone();
        let book_language = book_language.clone();
        async move {
            add_id3_tags(
                &dest, &acx_dir, &title, &book_title,
                &book_narrator, &book_author,
                track.as_deref(),
                book_date.as_deref(),
                book_publisher.as_deref(),
                &book_language,
            ).await
        }
    };

    let emit_progress = |msg: &str| {
        let _ = app.emit("acx-package-progress", AcxProgressEvent { message: msg.to_string() });
        log::info!("ACX: {msg}");
    };

    emit_progress(&format!("Creating ACX package in {acx_dir}…"));

    // Opening credits
    if let Some(oc) = &master.opening_credits {
        emit_progress("Stitching Opening Credits…");
        let out = stitch_manifest(&app, &output_dir, oc, &quality).await
            .map_err(|e| format!("Opening credits stitch failed: {e}"))?;
        let dest = format!("{acx_dir}/00_Opening_Credits.mp3");
        std::fs::copy(&out, &dest).map_err(|e| format!("Copy opening credits: {e}"))?;
        tag(&dest, "Opening Credits", Some("0")).await
            .unwrap_or_else(|e| log::warn!("ID3 tag opening credits: {e}"));
        emit_progress("Opening Credits done.");
    }

    // Chapters
    for (i, chapter) in master.chapters.iter().enumerate() {
        let chapter_num = i + 1;
        emit_progress(&format!("Processing chapter {chapter_num}: {}…", chapter.tab_name));

        // Use pre-stitched file if available
        let dest = format!("{acx_dir}/{chapter_num:02}_{}.mp3",
            crate::stitch::sanitise_filename(&chapter.tab_name));

        if let Some(pre_stitched) = chapter.audio_files.as_ref().and_then(|m| m.get(&quality)) {
            if std::path::Path::new(pre_stitched).exists() {
                std::fs::copy(pre_stitched, &dest)
                    .map_err(|e| format!("Copy chapter {chapter_num}: {e}"))?;
                tag(&dest, &chapter.tab_name, Some(&chapter_num.to_string())).await
                    .unwrap_or_else(|e| log::warn!("ID3 tag chapter {chapter_num}: {e}"));
                emit_progress(&format!("Chapter {chapter_num} copied from pre-stitched file."));
                continue;
            }
        }

        // Need to stitch
        let out = stitch_manifest(&app, &output_dir, chapter, &quality).await
            .map_err(|e| format!("Chapter {chapter_num} stitch failed: {e}"))?;
        std::fs::copy(&out, &dest).map_err(|e| format!("Copy chapter {chapter_num}: {e}"))?;
        tag(&dest, &chapter.tab_name, Some(&chapter_num.to_string())).await
            .unwrap_or_else(|e| log::warn!("ID3 tag chapter {chapter_num}: {e}"));
        emit_progress(&format!("Chapter {chapter_num} stitched and saved."));
    }

    // About Author
    if let Some(aa) = &master.about_author {
        emit_progress("Stitching About Author…");
        let out = stitch_manifest(&app, &output_dir, aa, &quality).await
            .map_err(|e| format!("About author stitch failed: {e}"))?;
        let dest = format!("{acx_dir}/about_author.mp3");
        std::fs::copy(&out, &dest).map_err(|e| format!("Copy about author: {e}"))?;
        tag(&dest, "About the Author", None).await
            .unwrap_or_else(|e| log::warn!("ID3 tag about author: {e}"));
        emit_progress("About Author done.");
    }

    // Retail sample
    if let Some(rs) = &master.retail_sample {
        if !rs.section_refs.is_empty() {
            emit_progress("Building retail sample…");
            let mut sample_paths: Vec<String> = Vec::new();
            for sref in &rs.section_refs {
                if let Some(ch) = master.chapters.iter().find(|c| c.tab_name == sref.chapter_tab_name) {
                    for section in &ch.sections {
                        if let ManifestSection::Speech(sp) = section {
                            if sp.id == sref.section_id {
                                if let Some(path) = sp.audio_files.as_ref().and_then(|m| m.get(&quality)) {
                                    sample_paths.push(path.clone());
                                }
                            }
                        }
                    }
                }
            }
            if !sample_paths.is_empty() {
                let dest = format!("{acx_dir}/retail_sample.mp3");
                // Interleave a 1-second silence between each speech section.
                let paths_with_gaps = if sample_paths.len() > 1 {
                    let ffmpeg = crate::stitch::find_ffmpeg()
                        .ok_or_else(|| "ffmpeg not found on PATH".to_string())?;
                    let silence_path = format!("{output_dir}/_retail_gap_1s.mp3");
                    crate::stitch::generate_silence(&ffmpeg, 1.0, &silence_path).await
                        .map_err(|e| format!("Retail sample gap generation failed: {e}"))?;
                    let mut interleaved: Vec<String> = Vec::with_capacity(sample_paths.len() * 2 - 1);
                    for (i, p) in sample_paths.iter().enumerate() {
                        interleaved.push(p.clone());
                        if i + 1 < sample_paths.len() {
                            interleaved.push(silence_path.clone());
                        }
                    }
                    interleaved
                } else {
                    sample_paths.clone()
                };
                concat_files_ffmpeg(&paths_with_gaps, &dest, &output_dir).await
                    .map_err(|e| format!("Retail sample concat failed: {e}"))?;
                emit_progress(&format!("Retail sample: {} sections.", sample_paths.len()));
            }
        }
    }

    // Closing credits
    if let Some(cc) = &master.closing_credits {
        let chapter_count = master.chapters.len();
        emit_progress("Stitching Closing Credits…");
        let out = stitch_manifest(&app, &output_dir, cc, &quality).await
            .map_err(|e| format!("Closing credits stitch failed: {e}"))?;
        let dest = format!("{acx_dir}/{:02}_Closing_Credits.mp3", chapter_count + 1);
        std::fs::copy(&out, &dest).map_err(|e| format!("Copy closing credits: {e}"))?;
        tag(&dest, "Closing Credits", Some(&(chapter_count + 1).to_string())).await
            .unwrap_or_else(|e| log::warn!("ID3 tag closing credits: {e}"));
        emit_progress("Closing Credits done.");
    }

    // Cover image
    if let Some(cover) = &master.cover {
        if let Some(img_path) = &cover.image_path {
            if std::path::Path::new(img_path).exists() {
                let ext = cover.format.as_deref().unwrap_or("jpg");
                let dest = format!("{acx_dir}/cover.{ext}");
                std::fs::copy(img_path, &dest)
                    .map_err(|e| format!("Copy cover: {e}"))?;
                emit_progress("Cover image copied.");
            }
        }
    }

    emit_progress(&format!("ACX package complete: {acx_dir}"));
    Ok(acx_dir)
}

/// Concatenate audio files using ffmpeg concat demuxer.
async fn concat_files_ffmpeg(
    input_paths: &[String],
    output_path: &str,
    work_dir: &str,
) -> Result<(), String> {
    let ffmpeg = crate::stitch::find_ffmpeg()
        .ok_or_else(|| "ffmpeg not found on PATH".to_string())?;

    let list_path = format!("{work_dir}/_acx_concat_list.txt");
    let list_content = input_paths
        .iter()
        .map(|p| format!("file '{}'\n", p.replace('\'', "'\\''")))
        .collect::<String>();
    std::fs::write(&list_path, &list_content)
        .map_err(|e| format!("Write concat list: {e}"))?;

    let output = tokio::process::Command::new(&ffmpeg)
        .stdin(std::process::Stdio::null())
        .args([
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", &list_path,
            "-c", "copy",
            output_path,
        ])
        .output()
        .await
        .map_err(|e| format!("ffmpeg failed: {e}"))?;

    let _ = std::fs::remove_file(&list_path);

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("ffmpeg exited {}: {}", output.status, stderr));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{AudioManifest, ManifestSection, SpeechSection, SilenceSection, MasterManifest};

    const TEST_FORMAT: &str = "mp3_44100_64";

    fn make_manifest(sections: Vec<ManifestSection>) -> AudioManifest {
        AudioManifest {
            version: 1,
            document_title: "Test".into(),
            tab_name: "Tab".into(),
            generated_at: "2026-01-01T00:00:00Z".into(),
            pronunciation_dictionary_locators: None,
            audio_files: None,
            duration_secs: None,
            loudness_lufs: None,
            true_peak_dbtp: None,
            sections,
        }
    }

    fn make_master(chapters: Vec<AudioManifest>) -> MasterManifest {
        MasterManifest {
            version: 2,
            document_title: "Test".into(),
            generated_at: "2026-01-01T00:00:00Z".into(),
            chapters,
            opening_credits: None,
            closing_credits: None,
            about_author: None,
            retail_sample: None,
            cover: None,
            metadata: None,
        }
    }

    fn speech(id: &str, voice_id: &str, done: bool, dirty: bool) -> ManifestSection {
        let audio_files = if done {
            let mut m = HashMap::new();
            m.insert(TEST_FORMAT.to_string(), format!("/tmp/{TEST_FORMAT}_{id}.mp3"));
            Some(m)
        } else {
            None
        };
        ManifestSection::Speech(SpeechSection {
            id: id.into(),
            text: "hello".into(),
            voice_id: voice_id.into(),
            voice_name: "Voice".into(),
            tts_model: "eleven_multilingual_v2".into(),
            stability: 0.5,
            similarity_boost: 0.75,
            speed: None,
            audio_files,
            is_dirty: if dirty { Some(true) } else { None },
        })
    }

    fn silence(id: &str) -> ManifestSection {
        ManifestSection::Silence(SilenceSection {
            id: id.into(),
            duration_ms: 500,
            audio_file_path: None,
        })
    }

    #[test]
    fn empty_manifest_no_blocked() {
        assert!(compute_blocked(&make_manifest(vec![]), TEST_FORMAT).is_empty());
    }

    #[test]
    fn single_ungenerated_not_blocked() {
        let m = make_manifest(vec![speech("s1", "v1", false, false)]);
        assert!(compute_blocked(&m, TEST_FORMAT).is_empty());
    }

    #[test]
    fn second_same_voice_blocked_when_first_ungenerated() {
        let m = make_manifest(vec![
            speech("s1", "v1", false, false),
            speech("s2", "v1", false, false),
        ]);
        let b = compute_blocked(&m, TEST_FORMAT);
        assert!(!b.contains("s1"));
        assert!(b.contains("s2"));
    }

    #[test]
    fn second_not_blocked_when_first_done() {
        let m = make_manifest(vec![
            speech("s1", "v1", true, false),
            speech("s2", "v1", false, false),
        ]);
        assert!(compute_blocked(&m, TEST_FORMAT).is_empty());
    }

    #[test]
    fn dirty_blocks_later_same_voice() {
        let m = make_manifest(vec![
            speech("s1", "v1", true, true),
            speech("s2", "v1", false, false),
        ]);
        assert!(compute_blocked(&m, TEST_FORMAT).contains("s2"));
    }

    #[test]
    fn different_voices_independent() {
        let m = make_manifest(vec![
            speech("s1", "v1", false, false),
            speech("s2", "v2", false, false),
            speech("s3", "v1", false, false),
        ]);
        let b = compute_blocked(&m, TEST_FORMAT);
        assert!(!b.contains("s1"));
        assert!(!b.contains("s2"));
        assert!(b.contains("s3"));
    }

    #[test]
    fn silence_ignored_in_blocking() {
        let m = make_manifest(vec![
            speech("s1", "v1", false, false),
            silence("gap"),
            speech("s2", "v1", false, false),
        ]);
        let b = compute_blocked(&m, TEST_FORMAT);
        assert!(!b.contains("s1"));
        assert!(b.contains("s2"));
    }

    #[test]
    fn chain_of_three_same_voice() {
        let m = make_manifest(vec![
            speech("s1", "v1", false, false),
            speech("s2", "v1", false, false),
            speech("s3", "v1", false, false),
        ]);
        let b = compute_blocked(&m, TEST_FORMAT);
        assert!(!b.contains("s1"));
        assert!(b.contains("s2"));
        assert!(b.contains("s3"));
    }

    #[test]
    fn all_sections_done_true_when_all_generated() {
        let m = make_manifest(vec![
            speech("s1", "v1", true, false),
            silence("gap"),
            speech("s2", "v2", true, false),
        ]);
        assert!(all_sections_done(&m, TEST_FORMAT));
    }

    #[test]
    fn all_sections_done_false_when_one_ungenerated() {
        let m = make_manifest(vec![
            speech("s1", "v1", true,  false),
            speech("s2", "v2", false, false),
        ]);
        assert!(!all_sections_done(&m, TEST_FORMAT));
    }

    #[test]
    fn all_sections_done_false_when_dirty() {
        let m = make_manifest(vec![speech("s1", "v1", true, true)]);
        assert!(!all_sections_done(&m, TEST_FORMAT));
    }

    // -----------------------------------------------------------------------
    // Quality helpers
    // -----------------------------------------------------------------------

    #[test]
    fn quality_to_output_format_all_levels() {
        assert_eq!(quality_to_output_format("vlow"), "mp3_22050_32");
        assert_eq!(quality_to_output_format("low"),  "mp3_44100_64");
        assert_eq!(quality_to_output_format("med"),  "mp3_44100_128");
        assert_eq!(quality_to_output_format("high"), "mp3_44100_192");
    }

    #[test]
    fn quality_to_output_format_unknown_falls_back_to_low() {
        assert_eq!(quality_to_output_format(""),        "mp3_44100_64");
        assert_eq!(quality_to_output_format("unknown"), "mp3_44100_64");
    }

    #[test]
    fn format_rank_ordering() {
        assert!(format_rank("vlow")         < format_rank("low"));
        assert!(format_rank("low")          < format_rank("med"));
        assert!(format_rank("med")          < format_rank("high"));
        assert!(format_rank("mp3_22050_32") < format_rank("mp3_44100_64"));
        assert!(format_rank("mp3_44100_64") < format_rank("mp3_44100_128"));
        assert!(format_rank("mp3_44100_128") < format_rank("mp3_44100_192"));
        assert_eq!(format_rank("vlow"), format_rank("mp3_22050_32"));
        assert_eq!(format_rank("low"),  format_rank("mp3_44100_64"));
        assert_eq!(format_rank("med"),  format_rank("mp3_44100_128"));
        assert_eq!(format_rank("high"), format_rank("mp3_44100_192"));
    }

    #[test]
    fn format_rank_unknown_treated_as_low() {
        assert_eq!(format_rank(""),        format_rank("low"));
        assert_eq!(format_rank("unknown"), format_rank("low"));
    }

    #[test]
    fn elevenlabs_url_contains_output_format() {
        for (quality, expected_format) in &[
            ("vlow", "mp3_22050_32"),
            ("low",  "mp3_44100_64"),
            ("med",  "mp3_44100_128"),
            ("high", "mp3_44100_192"),
        ] {
            let fmt = quality_to_output_format(quality);
            let url = format!(
                "https://api.elevenlabs.io/v1/text-to-speech/{}?output_format={}",
                "voice-abc", fmt
            );
            assert!(
                url.contains(&format!("output_format={}", expected_format)),
                "URL for quality={quality} missing output_format={expected_format}: {url}"
            );
        }
    }

    #[test]
    fn clear_lower_quality_rank_logic() {
        assert!(format_rank("mp3_22050_32") < format_rank("mp3_44100_64"),
            "vlow-quality format must rank below low");
        assert!(format_rank("mp3_44100_64") < format_rank("mp3_44100_128"),
            "low-quality format must rank below med");
        assert!(format_rank("mp3_44100_192") >= format_rank("mp3_44100_128"),
            "high-quality format must survive a med clear");
    }

    #[test]
    fn audio_files_absent_means_no_audio() {
        let json = r#"{
            "type": "speech",
            "id": "s1",
            "text": "Hi",
            "voiceId": "v1",
            "voiceName": "Alice",
            "ttsModel": "eleven_multilingual_v2",
            "stability": 0.5,
            "similarityBoost": 0.75
        }"#;
        let section: crate::types::ManifestSection = serde_json::from_str(json).unwrap();
        if let crate::types::ManifestSection::Speech(s) = section {
            assert!(s.audio_files.is_none());
            assert!(!section_done_for_format(&s, "mp3_44100_64"));
        } else {
            panic!("Expected speech");
        }
    }

    // -----------------------------------------------------------------------
    // New tests for MasterManifest and related types
    // -----------------------------------------------------------------------

    #[test]
    fn master_manifest_serde_roundtrip() {
        let master = make_master(vec![make_manifest(vec![speech("s1", "v1", false, false)])]);
        let json = serde_json::to_string_pretty(&master).unwrap();
        let back: MasterManifest = serde_json::from_str(&json).unwrap();
        assert_eq!(back.version, 2);
        assert_eq!(back.document_title, "Test");
        assert_eq!(back.chapters.len(), 1);
        assert_eq!(back.chapters[0].sections.len(), 1);
        assert!(back.opening_credits.is_none());
        assert!(back.cover.is_none());
    }

    #[test]
    fn partial_manifest_kind_detection() {
        use crate::types::PartialManifest;
        let chapter_partial = PartialManifest {
            chapter: Some(make_manifest(vec![])),
            opening_credits: None,
            closing_credits: None,
            about_author: None,
        };
        assert_eq!(chapter_partial.kind(), "chapter");

        let oc_partial = PartialManifest {
            chapter: None,
            opening_credits: Some(make_manifest(vec![])),
            closing_credits: None,
            about_author: None,
        };
        assert_eq!(oc_partial.kind(), "openingCredits");

        let cc_partial = PartialManifest {
            chapter: None,
            opening_credits: None,
            closing_credits: Some(make_manifest(vec![])),
            about_author: None,
        };
        assert_eq!(cc_partial.kind(), "closingCredits");

        let aa_partial = PartialManifest {
            chapter: None,
            opening_credits: None,
            closing_credits: None,
            about_author: Some(make_manifest(vec![])),
        };
        assert_eq!(aa_partial.kind(), "aboutAuthor");

        let empty = PartialManifest {
            chapter: None,
            opening_credits: None,
            closing_credits: None,
            about_author: None,
        };
        assert_eq!(empty.kind(), "unknown");
    }

    #[test]
    fn manifest_section_id_helper() {
        let sp = ManifestSection::Speech(SpeechSection {
            id: "speech-123".into(),
            text: "hello".into(),
            voice_id: "v1".into(),
            voice_name: "Alice".into(),
            tts_model: "eleven_multilingual_v2".into(),
            stability: 0.5,
            similarity_boost: 0.75,
            audio_files: None,
            speed: None, is_dirty: None,
        });
        assert_eq!(sp.id(), "speech-123");

        let si = ManifestSection::Silence(SilenceSection {
            id: "silence-456".into(),
            duration_ms: 500,
            audio_file_path: None,
        });
        assert_eq!(si.id(), "silence-456");
    }

    #[test]
    fn v1_manifest_wrapped_as_master() {
        let v1_json = r#"{
            "version": 1,
            "documentTitle": "My Book",
            "tabName": "Chapter 1",
            "generatedAt": "2026-01-01T00:00:00Z",
            "sections": []
        }"#;
        let master = parse_manifest_json(v1_json).unwrap();
        assert_eq!(master.version, 2);
        assert_eq!(master.document_title, "My Book");
        assert_eq!(master.chapters.len(), 1);
        assert_eq!(master.chapters[0].tab_name, "Chapter 1");
        assert!(master.opening_credits.is_none());
    }

    #[test]
    fn v2_master_manifest_parsed_directly() {
        let v2_json = r#"{
            "version": 2,
            "documentTitle": "My Book",
            "generatedAt": "2026-01-01T00:00:00Z",
            "chapters": []
        }"#;
        let master = parse_manifest_json(v2_json).unwrap();
        assert_eq!(master.version, 2);
        assert_eq!(master.chapters.len(), 0);
    }

    #[test]
    fn normalize_text_collapses_whitespace() {
        assert_eq!(normalize_text("hello  world"), "hello world");
        assert_eq!(normalize_text("  leading"), "leading");
        assert_eq!(normalize_text("trailing  "), "trailing");
        assert_eq!(normalize_text("a\tb\nc"), "a b c");
    }

    #[test]
    fn master_find_section_mut_searches_all_manifests() {
        let mut master = make_master(vec![make_manifest(vec![speech("s1", "v1", false, false)])]);
        master.opening_credits = Some(make_manifest(vec![speech("oc1", "v2", false, false)]));

        assert!(master.find_section_mut("s1").is_some());
        assert!(master.find_section_mut("oc1").is_some());
        assert!(master.find_section_mut("nonexistent").is_none());
    }

    #[test]
    fn acx_audit_rejects_non_acx_quality() {
        let master = make_master(vec![]);
        let results = perform_acx_audit(&master, "mp3_44100_64");
        let quality_check = results.iter().find(|r| r.check_id == "acx_quality").unwrap();
        assert!(!quality_check.passed);
    }

    #[test]
    fn acx_audit_accepts_acx_quality() {
        let master = make_master(vec![]);
        let results = perform_acx_audit(&master, "mp3_44100_128");
        let quality_check = results.iter().find(|r| r.check_id == "acx_quality").unwrap();
        assert!(quality_check.passed);

        let results2 = perform_acx_audit(&master, "mp3_44100_192");
        let quality_check2 = results2.iter().find(|r| r.check_id == "acx_quality").unwrap();
        assert!(quality_check2.passed);
    }

    // ---------------------------------------------------------------------------
    // Path relativization / absolutization tests
    // ---------------------------------------------------------------------------

    fn speech_with_audio(id: &str, audio_path: &str) -> ManifestSection {
        let mut m = HashMap::new();
        m.insert(TEST_FORMAT.to_string(), audio_path.to_string());
        ManifestSection::Speech(SpeechSection {
            id: id.into(),
            text: "test".into(),
            voice_id: "v1".into(),
            voice_name: "Voice".into(),
            tts_model: "eleven_multilingual_v2".into(),
            stability: 0.5,
            similarity_boost: 0.75,
            audio_files: Some(m),
            speed: None, is_dirty: None,
        })
    }

    fn silence_with_audio(id: &str, audio_path: &str) -> ManifestSection {
        ManifestSection::Silence(SilenceSection {
            id: id.into(),
            duration_ms: 500,
            audio_file_path: Some(audio_path.to_string()),
        })
    }

    #[test]
    fn relativize_then_absolutize_roundtrips() {
        let base = std::path::Path::new("/books/mybook");
        let abs_speech  = "/books/mybook/sections/mp3_44100_64_abc.mp3";
        // Silence clips now live in the shared silenceclips dir one level up.
        let abs_silence = "/books/silenceclips/silence_500ms.mp3";

        let mut master = make_master(vec![make_manifest(vec![
            speech_with_audio("s1", abs_speech),
            silence_with_audio("si1", abs_silence),
        ])]);

        // Relativize
        relativize_manifest_paths(&mut master, base);
        let sp_path = match &master.chapters[0].sections[0] {
            ManifestSection::Speech(s) => s.audio_files.as_ref().unwrap()[TEST_FORMAT].clone(),
            _ => panic!("expected speech"),
        };
        assert_eq!(sp_path, "./sections/mp3_44100_64_abc.mp3");
        let si_path = match &master.chapters[0].sections[1] {
            ManifestSection::Silence(s) => s.audio_file_path.clone().unwrap(),
            _ => panic!("expected silence"),
        };
        // Silence clip is one dir above manifest dir → ../silenceclips/...
        assert_eq!(si_path, "../silenceclips/silence_500ms.mp3");

        // Absolutize back
        absolutize_manifest_paths(&mut master, base);
        let sp_path2 = match &master.chapters[0].sections[0] {
            ManifestSection::Speech(s) => s.audio_files.as_ref().unwrap()[TEST_FORMAT].clone(),
            _ => panic!("expected speech"),
        };
        assert_eq!(sp_path2, abs_speech);
        let si_path2 = match &master.chapters[0].sections[1] {
            ManifestSection::Silence(s) => s.audio_file_path.clone().unwrap(),
            _ => panic!("expected silence"),
        };
        assert_eq!(si_path2, abs_silence);
    }

    #[test]
    fn relativize_uses_dotdot_for_paths_outside_base() {
        let base = std::path::Path::new("/books/mybook");
        let sibling = "/books/silenceclips/silence_500ms.mp3";
        let mut master = make_master(vec![make_manifest(vec![
            silence_with_audio("si1", sibling),
        ])]);
        relativize_manifest_paths(&mut master, base);
        let path = match &master.chapters[0].sections[0] {
            ManifestSection::Silence(s) => s.audio_file_path.clone().unwrap(),
            _ => panic!(),
        };
        assert_eq!(path, "../silenceclips/silence_500ms.mp3");
    }

    #[test]
    fn normalize_path_resolves_dotdot() {
        let p = std::path::Path::new("/books/mybook/../silenceclips/x.mp3");
        assert_eq!(normalize_path(p), std::path::Path::new("/books/silenceclips/x.mp3"));
    }

    #[test]
    fn compute_relative_path_under_base() {
        let base   = std::path::Path::new("/books/mybook");
        let target = std::path::Path::new("/books/mybook/sections/x.mp3");
        assert_eq!(compute_relative_path(base, target), "./sections/x.mp3");
    }

    #[test]
    fn compute_relative_path_sibling_dir() {
        let base   = std::path::Path::new("/books/mybook");
        let target = std::path::Path::new("/books/silenceclips/silence_500ms.mp3");
        assert_eq!(compute_relative_path(base, target), "../silenceclips/silence_500ms.mp3");
    }

    #[test]
    fn absolutize_leaves_already_absolute_paths_unchanged() {
        let base = std::path::Path::new("/books/mybook");
        let abs = "/books/mybook/sections/mp3_44100_64_xyz.mp3";
        let mut master = make_master(vec![make_manifest(vec![
            speech_with_audio("s1", abs),
        ])]);
        absolutize_manifest_paths(&mut master, base);
        let path = match &master.chapters[0].sections[0] {
            ManifestSection::Speech(s) => s.audio_files.as_ref().unwrap()[TEST_FORMAT].clone(),
            _ => panic!(),
        };
        assert_eq!(path, abs);
    }

    #[test]
    fn relativize_handles_special_slots() {
        let base = std::path::Path::new("/books/mybook");
        let abs = "/books/mybook/sections/mp3_44100_64_oc1.mp3";
        let mut master = make_master(vec![]);
        master.opening_credits = Some(make_manifest(vec![speech_with_audio("oc1", abs)]));
        relativize_manifest_paths(&mut master, base);
        let path = match &master.opening_credits.as_ref().unwrap().sections[0] {
            ManifestSection::Speech(s) => s.audio_files.as_ref().unwrap()[TEST_FORMAT].clone(),
            _ => panic!(),
        };
        assert_eq!(path, "./sections/mp3_44100_64_oc1.mp3");
    }
}
