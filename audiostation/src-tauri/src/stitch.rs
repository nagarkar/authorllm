/// Audio stitching via ffmpeg.
///
/// Concatenates generated speech sections and silence gaps into a single .mp3.
/// Each silence section becomes a short silent audio clip generated on the fly.
/// Speech sections use their pre-generated .mp3 files.
use crate::types::ManifestSection;
use std::process::Stdio;
use tokio::process::Command;

/// Phase 1: resolve every segment to a file path, generating any missing silence clips.
///
/// Returns `(input_paths, silence_updates)` where `silence_updates` is a list of
/// `(silence_section_id, generated_silence_file_path)` pairs.
///
/// Fails fast — if any silence clip cannot be generated the error is returned
/// immediately and no further segments are processed.
pub async fn prepare_inputs(
    output_dir: &str,
    sections: &[ManifestSection],
    format: &str,
) -> Result<(Vec<String>, Vec<(String, String)>), String> {
    let work_dir = std::path::Path::new(output_dir);
    let ffmpeg = find_ffmpeg().ok_or_else(|| {
        "ffmpeg not found on PATH — install ffmpeg to use Stitch Audio.".to_string()
    })?;

    // Silence clips are shared across all manifests in the same book directory.
    // Store them one level up from the manifest directory so any manifest
    // under the same parent can reuse the same files.
    let silence_dir = work_dir
        .parent()
        .map(|p| p.join("silenceclips"))
        .unwrap_or_else(|| work_dir.join("silenceclips"));
    std::fs::create_dir_all(&silence_dir)
        .map_err(|e| format!("Cannot create silenceclips dir: {e}"))?;

    let mut input_paths: Vec<String> = Vec::new();
    let mut silence_updates: Vec<(String, String)> = Vec::new();

    for section in sections {
        match section {
            ManifestSection::Speech(s) => {
                let path = s.audio_files
                    .as_ref()
                    .and_then(|m| m.get(format))
                    .map(|p| p.as_str())
                    .ok_or_else(|| {
                        format!("Section {}… has no audio file for format {format}", &s.id[..8])
                    })?;
                input_paths.push(path.to_string());
            }
            ManifestSection::Silence(s) => {
                let silence_path = silence_dir
                    .join(format!("silence_{}ms.mp3", s.duration_ms))
                    .to_string_lossy()
                    .to_string();

                // Only generate the silence file if it doesn't already exist.
                if !std::path::Path::new(&silence_path).exists() {
                    let duration_secs = s.duration_ms as f64 / 1000.0;
                    generate_silence(&ffmpeg, duration_secs, &silence_path).await?;
                }
                input_paths.push(silence_path.clone());
                silence_updates.push((s.id.clone(), silence_path));
            }
        }
    }

    if input_paths.is_empty() {
        return Err("No sections to stitch.".to_string());
    }

    Ok((input_paths, silence_updates))
}

/// Measurements from loudnorm pass 1, used to drive linear normalisation in pass 2.
pub struct LoudnormMeasurement {
    pub input_i:       f64,
    pub input_tp:      f64,
    pub input_lra:     f64,
    pub input_thresh:  f64,
    pub target_offset: f64,
}

/// Pass 1 of two-pass EBU R128 loudnorm: measures the integrated loudness of the
/// concat stream and returns the values needed for pass 2.
pub async fn measure_loudness(
    output_dir: &str,
    input_paths: &[String],
) -> Result<LoudnormMeasurement, String> {
    let ffmpeg = find_ffmpeg().ok_or_else(|| {
        "ffmpeg not found on PATH — install ffmpeg to use Stitch Audio.".to_string()
    })?;

    let work_dir = std::path::Path::new(output_dir);
    let list_path = work_dir.join("stitch_measure_list.txt").to_string_lossy().to_string();
    let list_content = input_paths
        .iter()
        .map(|p| format!("file '{}'\n", p.replace('\'', "'\\''")))
        .collect::<String>();
    std::fs::write(&list_path, &list_content)
        .map_err(|e| format!("Write measure list: {e}"))?;

    let output = Command::new(&ffmpeg)
        .stdin(Stdio::null())
        .args([
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", &list_path,
            "-af", "loudnorm=I=-20:TP=-3:LRA=11:print_format=json",
            "-f", "null",
            "-",
        ])
        .output()
        .await
        .map_err(|e| format!("ffmpeg loudnorm pass 1 failed: {e}"))?;

    let _ = std::fs::remove_file(&list_path);

    // ffmpeg prints loudnorm JSON to stderr; find the last {...} block.
    let stderr = String::from_utf8_lossy(&output.stderr);
    let start = stderr.rfind('{').ok_or("loudnorm: no JSON in stderr")?;
    let end   = stderr.rfind('}').ok_or("loudnorm: no closing brace in stderr")? + 1;
    if end <= start {
        return Err("loudnorm: malformed JSON block in stderr".to_string());
    }
    let json: serde_json::Value = serde_json::from_str(&stderr[start..end])
        .map_err(|e| format!("loudnorm: parse JSON: {e}"))?;

    let get = |key: &str| -> Result<f64, String> {
        json.get(key)
            .and_then(|v| v.as_str())
            .ok_or_else(|| format!("loudnorm: missing field '{key}'"))?
            .parse::<f64>()
            .map_err(|e| format!("loudnorm: parse '{key}': {e}"))
    };

    Ok(LoudnormMeasurement {
        input_i:       get("input_i")?,
        input_tp:      get("input_tp")?,
        input_lra:     get("input_lra")?,
        input_thresh:  get("input_thresh")?,
        target_offset: get("target_offset")?,
    })
}

/// Phase 2: write the concat list and run ffmpeg to produce the final audio file.
///
/// `existing_out_path`: if `Some`, overwrite that path; otherwise derive a new
/// filename from title + format.
///
/// `measurement`: if `Some`, applies two-pass linear loudnorm using the values
/// from pass 1. If `None`, falls back to single-pass (sufficient for low quality).
pub async fn concat_inputs(
    output_dir: &str,
    existing_out_path: Option<&str>,
    doc_title: &str,
    tab_name: &str,
    format: &str,
    input_paths: &[String],
    measurement: Option<&LoudnormMeasurement>,
) -> Result<String, String> {
    let ffmpeg = find_ffmpeg().ok_or_else(|| {
        "ffmpeg not found on PATH — install ffmpeg to use Stitch Audio.".to_string()
    })?;

    let work_dir = std::path::Path::new(output_dir);

    log::info!("stitch: building concat list with {} segments", input_paths.len());

    let list_path = work_dir.join("stitch_list.txt").to_string_lossy().to_string();
    let list_content = input_paths
        .iter()
        .map(|p| format!("file '{}'\n", p.replace('\'', "'\\''")))
        .collect::<String>();
    std::fs::write(&list_path, list_content)
        .map_err(|e| format!("Write concat list: {e}"))?;

    let out_path = if let Some(existing) = existing_out_path {
        existing.to_string()
    } else {
        let safe_name = sanitise_filename(&format!("{doc_title} — {tab_name}"));
        work_dir
            .join(format!("{format}_{safe_name}.mp3"))
            .to_string_lossy()
            .to_string()
    };

    let loudnorm_filter = match measurement {
        Some(m) => format!(
            "loudnorm=I=-20:TP=-3:LRA=11\
             :measured_I={:.2}:measured_TP={:.2}:measured_LRA={:.2}\
             :measured_thresh={:.2}:offset={:.2}:linear=true",
            m.input_i, m.input_tp, m.input_lra, m.input_thresh, m.target_offset
        ),
        None => "loudnorm=I=-20:TP=-3:LRA=11".to_string(),
    };

    let output = Command::new(&ffmpeg)
        .stdin(Stdio::null())
        .args([
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", &list_path,
            "-af", &loudnorm_filter,
            "-c:a", "libmp3lame",
            "-q:a", "2",           // VBR quality 2 ≈ 190 kbps
            &out_path,
        ])
        .output()
        .await
        .map_err(|e| format!("ffmpeg failed to start: {e}"))?;

    let _ = std::fs::remove_file(&list_path);

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("ffmpeg exited with {}: {}", output.status, stderr));
    }

    log::info!("stitch: output → {out_path}");
    Ok(out_path)
}

/// Generate a silent MP3 of the given duration using ffmpeg.
pub async fn generate_silence(ffmpeg: &str, secs: f64, out_path: &str) -> Result<(), String> {
    let output = Command::new(ffmpeg)
        .stdin(Stdio::null())
        .args([
            "-y",
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=mono:sample_rate=44100",
            "-t", &secs.to_string(),
            "-c:a", "libmp3lame",
            "-q:a", "9",           // lowest quality — silence, file size matters
            out_path,
        ])
        .output()
        .await
        .map_err(|e| format!("ffmpeg silence generation failed: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("ffmpeg exited {} generating silence: {}", output.status, stderr));
    }
    Ok(())
}

pub fn find_ffmpeg() -> Option<String> {
    for candidate in ["ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"] {
        if std::process::Command::new(candidate)
            .arg("-version")
            .output()
            .is_ok()
        {
            return Some(candidate.to_string());
        }
    }
    None
}

pub fn find_ffprobe() -> Option<String> {
    for candidate in ["ffprobe", "/usr/local/bin/ffprobe", "/opt/homebrew/bin/ffprobe"] {
        if std::process::Command::new(candidate)
            .arg("-version")
            .output()
            .is_ok()
        {
            return Some(candidate.to_string());
        }
    }
    None
}

/// Measure the duration of an audio file using ffprobe.
/// Returns `Ok(duration_secs)` or an error string.
pub async fn measure_duration(path: &str) -> Result<f64, String> {
    let ffprobe = find_ffprobe().ok_or_else(|| {
        "ffprobe not found on PATH — install ffmpeg to measure duration.".to_string()
    })?;
    let output = Command::new(&ffprobe)
        .stdin(Stdio::null())
        .args(["-v", "quiet", "-print_format", "json", "-show_entries", "format=duration", path])
        .output()
        .await
        .map_err(|e| format!("ffprobe failed: {e}"))?;
    if !output.status.success() {
        return Err(format!("ffprobe exited {}", output.status));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let json: serde_json::Value = serde_json::from_str(&stdout)
        .map_err(|e| format!("ffprobe parse: {e}"))?;
    json.get("format")
        .and_then(|f| f.get("duration"))
        .and_then(|d| d.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .ok_or_else(|| "ffprobe: missing duration field".to_string())
}

/// Returns `true` if the file at `path` is a JPEG with CMYK colour (4 components).
/// Reads only the JPEG SOF marker — no external dependency required.
/// Returns `false` on any parse error or for non-JPEG files.
pub fn jpeg_is_cmyk(path: &str) -> bool {
    let Ok(data) = std::fs::read(path) else { return false; };
    // Require SOI marker (FF D8)
    if data.len() < 4 || data[0] != 0xFF || data[1] != 0xD8 { return false; }
    let mut i = 2usize;
    while i + 3 < data.len() {
        if data[i] != 0xFF { break; }
        let marker = data[i + 1];
        // SOF markers: C0–C3, C5–C7, C9–CB, CD–CF (excludes DHT=C4, JPG=C8, DAC=CC)
        let is_sof = matches!(marker, 0xC0..=0xC3 | 0xC5..=0xC7 | 0xC9..=0xCB | 0xCD..=0xCF);
        if is_sof {
            // SOF layout: marker(2) + length(2) + precision(1) + height(2) + width(2) + ncomponents(1)
            return i + 9 < data.len() && data[i + 9] == 4;
        }
        if marker == 0xD9 { break; } // EOI — no SOF found
        let seg_len = u16::from_be_bytes([data[i + 2], data[i + 3]]) as usize;
        if seg_len < 2 { break; }
        i += 2 + seg_len;
    }
    false
}

pub fn sanitise_filename(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            c => c,
        })
        .collect::<String>()
        .trim()
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitise_normal_passthrough() {
        assert_eq!(sanitise_filename("Hello World"), "Hello World");
    }

    #[test]
    fn sanitise_em_dash_allowed() {
        assert_eq!(sanitise_filename("Doc — Tab"), "Doc — Tab");
    }

    #[test]
    fn sanitise_slash_replaced() {
        assert_eq!(sanitise_filename("a/b"), "a_b");
    }

    #[test]
    fn sanitise_backslash_replaced() {
        assert_eq!(sanitise_filename(r"a\b"), "a_b");
    }

    #[test]
    fn sanitise_all_illegal_chars() {
        let result = sanitise_filename(r#"a/b\c:d*e?f"g<h>i|j"#);
        assert!(!result.contains('/'));
        assert!(!result.contains('\\'));
        assert!(!result.contains(':'));
        assert!(!result.contains('*'));
        assert!(!result.contains('?'));
        assert!(!result.contains('"'));
        assert!(!result.contains('<'));
        assert!(!result.contains('>'));
        assert!(!result.contains('|'));
        assert_eq!(result, "a_b_c_d_e_f_g_h_i_j");
    }

    #[test]
    fn sanitise_trims_whitespace() {
        assert_eq!(sanitise_filename("  hello  "), "hello");
    }

    #[test]
    fn sanitise_empty_string() {
        assert_eq!(sanitise_filename(""), "");
    }

    #[test]
    fn find_ffmpeg_does_not_panic() {
        // Result depends on environment; just verify it doesn't panic.
        let _ = find_ffmpeg();
    }

    #[test]
    fn find_ffprobe_does_not_panic() {
        let _ = find_ffprobe();
    }

    #[test]
    fn jpeg_is_cmyk_rejects_non_jpeg() {
        // Random bytes — not a JPEG
        assert!(!jpeg_is_cmyk("/tmp/nonexistent_file_for_test.jpg"));
    }

    #[test]
    fn jpeg_is_cmyk_detects_rgb_sof() {
        // Minimal synthetic JPEG: SOI + APP0 + SOF0 with 3 components (RGB/YCbCr)
        let mut data: Vec<u8> = vec![
            0xFF, 0xD8, // SOI
            0xFF, 0xE0, 0x00, 0x10, // APP0 marker + length 16
            0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, // JFIF data
            0xFF, 0xC0, // SOF0
            0x00, 0x11, // length = 17
            0x08,       // precision
            0x00, 0x10, // height
            0x00, 0x10, // width
            0x03,       // 3 components = RGB/YCbCr
        ];
        // Write to a temp file and test
        let path = "/tmp/editorllm_test_rgb.jpg";
        std::fs::write(path, &data).unwrap();
        assert!(!jpeg_is_cmyk(path));
        // Now patch to 4 components (CMYK)
        let cmyk_idx = data.len() - 1;
        data[cmyk_idx] = 0x04;
        std::fs::write(path, &data).unwrap();
        assert!(jpeg_is_cmyk(path));
        let _ = std::fs::remove_file(path);
    }
}
