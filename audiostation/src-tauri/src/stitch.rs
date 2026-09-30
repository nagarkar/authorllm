//! Audio stitching via ffmpeg.
//!
//! Concatenates a chapter's generated speech sections and silence gaps
//! into one .mp3 under `_audio/audio/<stem>.<format>.mp3`. Silence
//! clips are generated on demand into `_audio/silence/` and shared by
//! every chapter of the book.

use std::path::{Path, PathBuf};
use std::process::Stdio;

use tokio::process::Command;

use crate::types::{LoadedChapter, Section, AUDIO_DIR, SILENCE_DIR};

/// Phase 1: resolve every segment to an absolute file path, generating
/// any missing silence clip. Fails fast on the first section without
/// audio at `format`.
pub async fn prepare_inputs(
    dir: &Path,
    chapter: &LoadedChapter,
    format: &str,
) -> Result<Vec<String>, String> {
    let ffmpeg = find_ffmpeg().ok_or_else(|| {
        "ffmpeg not found on PATH — install ffmpeg to stitch.".to_string()
    })?;
    let silence_dir = dir.join(SILENCE_DIR);
    std::fs::create_dir_all(&silence_dir)
        .map_err(|e| format!("Cannot create {}: {e}", silence_dir.display()))?;

    let mut inputs: Vec<String> = Vec::new();
    for section in &chapter.chapter.sections {
        match section {
            Section::Speech(s) => {
                let rel = chapter.state.sections.get(&s.id)
                    .and_then(|st| st.audio_files.get(format))
                    .ok_or_else(|| format!(
                        "Section {}… has no audio at {format} yet", &s.id[..8.min(s.id.len())]))?;
                let path = crate::types::resolve_in(dir, rel);
                if !path.exists() {
                    return Err(format!("Section {}…'s audio file is missing: {}",
                                       &s.id[..8.min(s.id.len())], path.display()));
                }
                inputs.push(path.to_string_lossy().to_string());
            }
            Section::Silence(s) => {
                let path = silence_dir.join(format!("silence_{}ms.mp3", s.duration_ms));
                if !path.exists() {
                    generate_silence(&ffmpeg, s.duration_ms as f64 / 1000.0,
                                     &path.to_string_lossy()).await?;
                }
                inputs.push(path.to_string_lossy().to_string());
            }
        }
    }
    if inputs.is_empty() {
        return Err("No sections to stitch.".to_string());
    }
    Ok(inputs)
}

/// Measurements from loudnorm pass 1, used to drive linear normalisation in pass 2.
pub struct LoudnormMeasurement {
    pub input_i:       f64,
    pub input_tp:      f64,
    pub input_lra:     f64,
    pub input_thresh:  f64,
    pub target_offset: f64,
}

fn concat_list(work_dir: &Path, name: &str, inputs: &[String]) -> Result<String, String> {
    let list_path = work_dir.join(name).to_string_lossy().to_string();
    let content = inputs.iter()
        .map(|p| format!("file '{}'\n", p.replace('\'', "'\\''")))
        .collect::<String>();
    std::fs::write(&list_path, content).map_err(|e| format!("Write {name}: {e}"))?;
    Ok(list_path)
}

/// Pass 1 of two-pass EBU R128 loudnorm.
pub async fn measure_loudness(dir: &Path, inputs: &[String]) -> Result<LoudnormMeasurement, String> {
    let ffmpeg = find_ffmpeg().ok_or_else(|| "ffmpeg not found on PATH".to_string())?;
    let list_path = concat_list(dir, "stitch_measure_list.txt", inputs)?;
    let output = Command::new(&ffmpeg)
        .stdin(Stdio::null())
        .args(["-y", "-f", "concat", "-safe", "0", "-i", &list_path,
               "-af", "loudnorm=I=-20:TP=-3:LRA=11:print_format=json",
               "-f", "null", "-"])
        .output()
        .await
        .map_err(|e| format!("ffmpeg loudnorm pass 1 failed: {e}"))?;
    let _ = std::fs::remove_file(&list_path);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        return Err(format!("ffmpeg loudnorm pass 1 exited {}: {}", output.status, stderr));
    }
    let start = stderr.rfind('{').ok_or("loudnorm JSON not found in ffmpeg output")?;
    let end = stderr[start..].find('}').map(|i| start + i + 1)
        .ok_or("loudnorm JSON not terminated")?;
    let json: serde_json::Value = serde_json::from_str(&stderr[start..end])
        .map_err(|e| format!("loudnorm JSON parse: {e}"))?;
    let f = |k: &str| -> Result<f64, String> {
        json.get(k).and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok())
            .ok_or_else(|| format!("loudnorm JSON lacks {k}"))
    };
    Ok(LoudnormMeasurement {
        input_i: f("input_i")?, input_tp: f("input_tp")?, input_lra: f("input_lra")?,
        input_thresh: f("input_thresh")?, target_offset: f("target_offset")?,
    })
}

/// Phase 2: concat + loudnorm + encode into `audio/<stem>.<format>.mp3`.
/// Returns the path RELATIVE to `dir` — what the state file stores.
pub async fn concat_inputs(
    dir: &Path,
    stem: &str,
    format: &str,
    inputs: &[String],
    measurement: Option<&LoudnormMeasurement>,
) -> Result<String, String> {
    let ffmpeg = find_ffmpeg().ok_or_else(|| "ffmpeg not found on PATH".to_string())?;
    let out_dir = dir.join(AUDIO_DIR);
    std::fs::create_dir_all(&out_dir).map_err(|e| format!("Cannot create audio dir: {e}"))?;
    let rel = format!("{AUDIO_DIR}/{}.{format}.mp3", sanitise_filename(stem));
    let out_path = dir.join(&rel);
    let list_path = concat_list(dir, "stitch_list.txt", inputs)?;
    let loudnorm = match measurement {
        Some(m) => format!(
            "loudnorm=I=-20:TP=-3:LRA=11:measured_I={:.2}:measured_TP={:.2}:\
             measured_LRA={:.2}:measured_thresh={:.2}:offset={:.2}:linear=true",
            m.input_i, m.input_tp, m.input_lra, m.input_thresh, m.target_offset),
        None => "loudnorm=I=-20:TP=-3:LRA=11".to_string(),
    };
    let output = Command::new(&ffmpeg)
        .stdin(Stdio::null())
        .args(["-y", "-f", "concat", "-safe", "0", "-i", &list_path,
               // ACX: 44.1 kHz, constant 192 kbps, one channel throughout.
               // loudnorm resamples internally, so the rate must be pinned
               // after it or the encoder settles on 48 kHz.
               "-af", &format!("{loudnorm},aresample=44100"),
               "-ar", "44100", "-ac", "1",
               "-c:a", "libmp3lame", "-b:a", "192k",
               &out_path.to_string_lossy()])
        .output()
        .await
        .map_err(|e| format!("ffmpeg failed to start: {e}"))?;
    let _ = std::fs::remove_file(&list_path);
    if !output.status.success() {
        return Err(format!("ffmpeg exited {}: {}", output.status,
                           String::from_utf8_lossy(&output.stderr)));
    }
    log::info!("stitch: {} → {rel}", stem);
    Ok(rel)
}

/// Generate a silent MP3 of the given duration.
pub async fn generate_silence(ffmpeg: &str, secs: f64, out_path: &str) -> Result<(), String> {
    let output = Command::new(ffmpeg)
        .stdin(Stdio::null())
        .args(["-y", "-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
               "-t", &secs.to_string(), "-c:a", "libmp3lame", "-q:a", "9", out_path])
        .output()
        .await
        .map_err(|e| format!("ffmpeg silence generation failed: {e}"))?;
    if !output.status.success() {
        return Err(format!("ffmpeg exited {} generating silence: {}", output.status,
                           String::from_utf8_lossy(&output.stderr)));
    }
    Ok(())
}

/// Plain concatenation (no loudnorm) — the retail sample, whose parts are
/// already normalised chapter sections.
pub async fn concat_plain(dir: &Path, inputs: &[String], out_path: &Path) -> Result<(), String> {
    let ffmpeg = find_ffmpeg().ok_or_else(|| "ffmpeg not found on PATH".to_string())?;
    let list_path = concat_list(dir, "concat_plain_list.txt", inputs)?;
    let output = Command::new(&ffmpeg)
        .stdin(Stdio::null())
        .args(["-y", "-f", "concat", "-safe", "0", "-i", &list_path,
               "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "192k",
               &out_path.to_string_lossy()])
        .output()
        .await
        .map_err(|e| format!("ffmpeg failed to start: {e}"))?;
    let _ = std::fs::remove_file(&list_path);
    if !output.status.success() {
        return Err(format!("ffmpeg exited {}: {}", output.status,
                           String::from_utf8_lossy(&output.stderr)));
    }
    Ok(())
}

pub fn find_ffmpeg() -> Option<String> {
    find_tool("ffmpeg")
}

pub fn find_ffprobe() -> Option<String> {
    find_tool("ffprobe")
}

fn find_tool(name: &str) -> Option<String> {
    let candidates = [name.to_string(), format!("/usr/local/bin/{name}"),
                      format!("/opt/homebrew/bin/{name}")];
    for candidate in candidates {
        if std::process::Command::new(&candidate).arg("-version").output().is_ok() {
            return Some(candidate);
        }
    }
    None
}

/// Duration in seconds via ffprobe.
pub async fn measure_duration(path: &Path) -> Result<f64, String> {
    let ffprobe = find_ffprobe().ok_or_else(|| "ffprobe not found".to_string())?;
    let output = Command::new(&ffprobe)
        .stdin(Stdio::null())
        .args(["-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", &path.to_string_lossy()])
        .output()
        .await
        .map_err(|e| format!("ffprobe failed: {e}"))?;
    if !output.status.success() {
        return Err(format!("ffprobe exited {}", output.status));
    }
    String::from_utf8_lossy(&output.stdout).trim().parse::<f64>()
        .map_err(|e| format!("ffprobe duration parse: {e}"))
}

/// A filesystem-safe name: letters, digits, dash, underscore, dot.
pub fn sanitise_filename(s: &str) -> String {
    let out: String = s.chars()
        .map(|c| if c.is_alphanumeric() || c == '-' || c == '_' || c == '.' { c } else { '_' })
        .collect();
    let trimmed = out.trim_matches('_');
    if trimmed.is_empty() { "untitled".to_string() } else { trimmed.to_string() }
}

pub fn acx_output_dir(dir: &Path, title: &str) -> PathBuf {
    let stamp = chrono::Utc::now().format("%Y%m%d_%H%M%S").to_string();
    dir.join(crate::types::ACX_DIR).join(format!("{}_ACX_{stamp}", sanitise_filename(title)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitise_keeps_safe_chars_and_replaces_the_rest() {
        assert_eq!(sanitise_filename("Seven More: Sermons/To The Dead"), "Seven_More__Sermons_To_The_Dead");
        assert_eq!(sanitise_filename("///"), "untitled");
        assert_eq!(sanitise_filename("sermons"), "sermons");
    }
}
