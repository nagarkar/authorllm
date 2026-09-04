use std::collections::HashMap;
use serde::{Deserialize, Serialize};

/// A single ElevenLabs voice returned by the list_voices command.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct VoiceInfo {
    pub voice_id: String,
    pub name: String,
}

/// Pronunciation dictionary locator sent to the ElevenLabs TTS API.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PronunciationDictionaryLocator {
    pub pronunciation_dictionary_id: String,
    pub version_id: String,
}

/// Top-level audio manifest sent from the GAS add-on.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioManifest {
    pub version: u8,
    pub document_title: String,
    pub tab_name: String,
    /// ISO 8601 timestamp (e.g. "2026-04-30T12:00:00Z")
    pub generated_at: String,
    pub sections: Vec<ManifestSection>,
    /// Pronunciation dictionary locators applied to all speech sections.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pronunciation_dictionary_locators: Option<Vec<PronunciationDictionaryLocator>>,
    /// Stitched full-audio files keyed by output format (e.g. "mp3_44100_64").
    #[serde(skip_serializing_if = "Option::is_none")]
    pub audio_files: Option<HashMap<String, String>>,
    /// Duration of the stitched audio in seconds — filled after a successful stitch.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_secs: Option<f64>,
    /// Integrated loudness of the stitched file in LUFS — filled after a successful two-pass stitch.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub loudness_lufs: Option<f64>,
    /// True peak of the stitched file in dBTP — filled after a successful two-pass stitch.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub true_peak_dbtp: Option<f64>,
}

/// A section is either speech or silence.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum ManifestSection {
    #[serde(rename = "speech")]
    Speech(SpeechSection),
    #[serde(rename = "silence")]
    Silence(SilenceSection),
}

impl ManifestSection {
    pub fn id(&self) -> &str {
        match self {
            ManifestSection::Speech(s) => &s.id,
            ManifestSection::Silence(s) => &s.id,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SpeechSection {
    /// UUID
    pub id: String,
    pub text: String,
    pub voice_id: String,
    /// Display name only — not sent to ElevenLabs API.
    pub voice_name: String,
    pub tts_model: String,
    pub stability: f64,
    pub similarity_boost: f64,
    /// Playback speed multiplier (0.25–4.0). Defaults to 1.0 if absent.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub speed: Option<f64>,

    /// Generated audio files keyed by output format (e.g. "mp3_44100_64" → "/path/to/file.mp3").
    /// Only formats that have been generated are present.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub audio_files: Option<HashMap<String, String>>,
    /// True when text or voice was edited after generation.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_dirty: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SilenceSection {
    /// UUID
    pub id: String,
    pub duration_ms: u32,
    /// Set after stitching — path to the generated silence clip.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub audio_file_path: Option<String>,
}

/// Response body for GET /status
#[derive(Serialize)]
pub struct StatusResponse {
    pub running: bool,
    pub version: &'static str,
}

// ---------------------------------------------------------------------------
// Master manifest — multi-chapter ACX audiobook structure
// ---------------------------------------------------------------------------

/// Book-level metadata for ACX/KDP submission.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BookMetadata {
    pub title: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subtitle: Option<String>,
    pub author: String,
    pub narrator: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub publisher: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub copyright_year: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub copyright_holder: Option<String>,
    #[serde(default = "default_language")]
    pub language: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub asin: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub isbn: Option<String>,
}

fn default_language() -> String { "en".to_string() }

/// Top-level manifest — holds all chapters + special audio sections.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MasterManifest {
    pub version: u8,                    // always 2
    pub document_title: String,
    pub generated_at: String,
    #[serde(default)]
    pub chapters: Vec<AudioManifest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub opening_credits: Option<AudioManifest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub closing_credits: Option<AudioManifest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub about_author: Option<AudioManifest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub retail_sample: Option<RetailSampleSpec>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cover: Option<CoverSpec>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<BookMetadata>,
}

impl MasterManifest {
    pub fn new(document_title: String) -> Self {
        Self {
            version: 2,
            document_title,
            generated_at: chrono::Utc::now().to_rfc3339(),
            chapters: Vec::new(),
            opening_credits: None,
            closing_credits: None,
            about_author: None,
            retail_sample: None,
            cover: None,
            metadata: None,
        }
    }

    /// All sections across all chapters and special sections, searched by ID.
    pub fn find_section_mut(&mut self, id: &str) -> Option<&mut ManifestSection> {
        for ch in &mut self.chapters {
            if let Some(s) = ch.sections.iter_mut().find(|s| s.id() == id) {
                return Some(s);
            }
        }
        for slot in [&mut self.opening_credits, &mut self.closing_credits, &mut self.about_author] {
            if let Some(am) = slot {
                if let Some(s) = am.sections.iter_mut().find(|s| s.id() == id) {
                    return Some(s);
                }
            }
        }
        None
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RetailSampleSpec {
    #[serde(default)]
    pub section_refs: Vec<SectionRef>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SectionRef {
    pub chapter_tab_name: String,
    pub section_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CoverSpec {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub width: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub height: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub format: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_size_bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub color_space: Option<String>,
}

/// Incoming partial manifest from GAS.
/// One or more fields may be Some — chapter exports piggyback opening/closing
/// credits and about-author so the desktop always receives a fresh snapshot.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PartialManifest {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub chapter: Option<AudioManifest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub opening_credits: Option<AudioManifest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub closing_credits: Option<AudioManifest>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub about_author: Option<AudioManifest>,
}

impl PartialManifest {
    /// Returns the kind of the primary (first non-None) field. Used for display only.
    pub fn kind(&self) -> &'static str {
        if self.chapter.is_some()              { "chapter" }
        else if self.opening_credits.is_some() { "openingCredits" }
        else if self.closing_credits.is_some() { "closingCredits" }
        else if self.about_author.is_some()    { "aboutAuthor" }
        else                                   { "unknown" }
    }
    /// Returns a reference to the primary (first non-None) AudioManifest.
    pub fn inner(&self) -> Option<&AudioManifest> {
        self.chapter.as_ref()
            .or(self.opening_credits.as_ref())
            .or(self.closing_credits.as_ref())
            .or(self.about_author.as_ref())
    }
    pub fn display_name(&self) -> String {
        let mut parts: Vec<String> = Vec::new();
        if let Some(m) = &self.chapter       { parts.push(format!("chapter:{}", m.tab_name)); }
        if self.opening_credits.is_some()    { parts.push("openingCredits".into()); }
        if self.closing_credits.is_some()    { parts.push("closingCredits".into()); }
        if self.about_author.is_some()       { parts.push("aboutAuthor".into()); }
        if parts.is_empty() { "Unknown".into() } else { parts.join("+") }
    }
}

/// Returned to frontend after receiving a partial manifest.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PartialManifestInfo {
    pub kind: String,
    pub display_name: String,
    pub document_title: String,
}

/// Result of merge_partial_into_master.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct MergeResult {
    pub sections_preserved: usize,
    pub sections_updated: usize,
    pub orphaned_file_count: usize,
    pub orphaned_dir: Option<String>,
}

/// Single ACX structural audit result.
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
#[serde(rename_all = "camelCase")]
pub enum AuditSeverity {
    Error,
    Warning,
    Info,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deserialize_speech_section() {
        let json = r#"{
            "type": "speech",
            "id": "abc-123",
            "text": "Hello world",
            "voiceId": "voice-001",
            "voiceName": "Aria",
            "ttsModel": "eleven_multilingual_v2",
            "stability": 0.5,
            "similarityBoost": 0.75
        }"#;
        let section: ManifestSection = serde_json::from_str(json).unwrap();
        match section {
            ManifestSection::Speech(s) => {
                assert_eq!(s.id, "abc-123");
                assert_eq!(s.text, "Hello world");
                assert_eq!(s.voice_id, "voice-001");
                assert_eq!(s.voice_name, "Aria");
                assert!((s.stability - 0.5).abs() < 1e-9);
                assert!(s.audio_files.is_none());
                assert!(s.is_dirty.is_none());
            }
            _ => panic!("Expected speech section"),
        }
    }

    #[test]
    fn deserialize_silence_section() {
        let json = r#"{"type":"silence","id":"sil-1","durationMs":500}"#;
        let section: ManifestSection = serde_json::from_str(json).unwrap();
        match section {
            ManifestSection::Silence(s) => {
                assert_eq!(s.id, "sil-1");
                assert_eq!(s.duration_ms, 500);
                assert!(s.audio_file_path.is_none());
            }
            _ => panic!("Expected silence section"),
        }
    }

    #[test]
    fn deserialize_speech_with_result() {
        let json = r#"{
            "type": "speech",
            "id": "s1",
            "text": "Hi",
            "voiceId": "v1",
            "voiceName": "Alice",
            "ttsModel": "eleven_multilingual_v2",
            "stability": 0.5,
            "similarityBoost": 0.75,
            "audioFiles": {"mp3_44100_64": "/output/s1.mp3"}
        }"#;
        let section: ManifestSection = serde_json::from_str(json).unwrap();
        match section {
            ManifestSection::Speech(s) => {
                let af = s.audio_files.as_ref().unwrap();
                assert_eq!(af.get("mp3_44100_64").map(|s| s.as_str()), Some("/output/s1.mp3"));
                assert!(s.is_dirty.is_none());
            }
            _ => panic!("Expected speech"),
        }
    }

    #[test]
    fn deserialize_dirty_speech() {
        let json = r#"{
            "type": "speech",
            "id": "s1",
            "text": "Changed",
            "voiceId": "v1",
            "voiceName": "Alice",
            "ttsModel": "eleven_multilingual_v2",
            "stability": 0.5,
            "similarityBoost": 0.75,
            "audioFiles": {"mp3_44100_64": "/old.mp3"},
            "isDirty": true
        }"#;
        let section: ManifestSection = serde_json::from_str(json).unwrap();
        match section {
            ManifestSection::Speech(s) => {
                assert_eq!(s.is_dirty, Some(true));
                assert!(s.audio_files.is_some());
            }
            _ => panic!("Expected speech"),
        }
    }

    #[test]
    fn manifest_roundtrip() {
        let manifest = AudioManifest {
            version: 1,
            document_title: "My Doc".into(),
            tab_name: "Tab 1".into(),
            generated_at: "2026-01-01T00:00:00Z".into(),
            pronunciation_dictionary_locators: None,
            audio_files: None,
            duration_secs: None,
            loudness_lufs: None,
            true_peak_dbtp: None,
            sections: vec![
                ManifestSection::Speech(SpeechSection {
                    id: "s1".into(),
                    text: "Hello".into(),
                    voice_id: "v1".into(),
                    voice_name: "Alice".into(),
                    tts_model: "eleven_multilingual_v2".into(),
                    stability: 0.5,
                    similarity_boost: 0.75,
                    audio_files: None,
                    speed: None, is_dirty: None,
                }),
                ManifestSection::Silence(SilenceSection {
                    id: "si1".into(),
                    duration_ms: 1000,
                    audio_file_path: None,
                }),
            ],
        };
        let json = serde_json::to_string(&manifest).unwrap();
        let roundtripped: AudioManifest = serde_json::from_str(&json).unwrap();
        assert_eq!(roundtripped.document_title, manifest.document_title);
        assert_eq!(roundtripped.tab_name, manifest.tab_name);
        assert_eq!(roundtripped.sections.len(), 2);
    }

    #[test]
    fn silence_audio_path_omitted_when_none() {
        let section = ManifestSection::Silence(SilenceSection {
            id: "sil1".into(),
            duration_ms: 500,
            audio_file_path: None,
        });
        let json = serde_json::to_string(&section).unwrap();
        assert!(!json.contains("audioFilePath"));

        let with_path = ManifestSection::Silence(SilenceSection {
            id: "sil2".into(),
            duration_ms: 500,
            audio_file_path: Some("/tmp/silence_500ms.mp3".into()),
        });
        let json2 = serde_json::to_string(&with_path).unwrap();
        assert!(json2.contains("audioFilePath"));
    }

    #[test]
    fn optional_fields_omitted_in_serialization() {
        let section = ManifestSection::Speech(SpeechSection {
            id: "s1".into(),
            text: "Hi".into(),
            voice_id: "v1".into(),
            voice_name: "A".into(),
            tts_model: "m".into(),
            stability: 0.5,
            similarity_boost: 0.75,
            audio_files: None,
            speed: None, is_dirty: None,
        });
        let json = serde_json::to_string(&section).unwrap();
        assert!(!json.contains("audioFiles"));
        assert!(!json.contains("isDirty"));
    }

    #[test]
    fn stitch_measurements_roundtrip() {
        let json = r#"{
            "version":1,"documentTitle":"D","tabName":"T","generatedAt":"2026-01-01T00:00:00Z",
            "sections":[],
            "durationSecs":182.4,"loudnessLufs":-20.0,"truePeakDbtp":-3.0
        }"#;
        let m: AudioManifest = serde_json::from_str(json).unwrap();
        assert!((m.duration_secs.unwrap() - 182.4).abs() < 1e-9);
        assert!((m.loudness_lufs.unwrap() - (-20.0)).abs() < 1e-9);
        assert!((m.true_peak_dbtp.unwrap() - (-3.0)).abs() < 1e-9);
        // Omitted when None
        let m2 = AudioManifest { version: 1, document_title: "D".into(), tab_name: "T".into(),
            generated_at: "2026-01-01T00:00:00Z".into(), sections: vec![],
            pronunciation_dictionary_locators: None, audio_files: None,
            duration_secs: None, loudness_lufs: None, true_peak_dbtp: None };
        let out = serde_json::to_string(&m2).unwrap();
        assert!(!out.contains("durationSecs"));
        assert!(!out.contains("loudnessLufs"));
    }

    #[test]
    fn book_metadata_roundtrip() {
        let meta = BookMetadata {
            title: "Seven Sermons".into(),
            subtitle: None,
            author: "C.G. Jung".into(),
            narrator: "Adam Stone".into(),
            publisher: None,
            copyright_year: Some(2026),
            copyright_holder: Some("Estate of C.G. Jung".into()),
            language: "en".into(),
            asin: None,
            isbn: None,
        };
        let json = serde_json::to_string(&meta).unwrap();
        assert!(json.contains("\"author\":\"C.G. Jung\""));
        assert!(!json.contains("subtitle"));
        assert!(!json.contains("publisher"));
        let back: BookMetadata = serde_json::from_str(&json).unwrap();
        assert_eq!(back.title, "Seven Sermons");
        assert_eq!(back.copyright_year, Some(2026));
    }

    #[test]
    fn book_metadata_default_language() {
        let json = r#"{"title":"T","author":"A","narrator":"N"}"#;
        let m: BookMetadata = serde_json::from_str(json).unwrap();
        assert_eq!(m.language, "en");
    }

    #[test]
    fn audio_files_map_roundtrip() {
        let mut files = HashMap::new();
        files.insert("mp3_44100_64".to_string(), "/out/mp3_44100_64_s1.mp3".to_string());
        files.insert("mp3_44100_128".to_string(), "/out/mp3_44100_128_s1.mp3".to_string());
        let section = ManifestSection::Speech(SpeechSection {
            id: "s1".into(),
            text: "Hi".into(),
            voice_id: "v1".into(),
            voice_name: "A".into(),
            tts_model: "m".into(),
            stability: 0.5,
            similarity_boost: 0.75,
            audio_files: Some(files),
            speed: None, is_dirty: None,
        });
        let json = serde_json::to_string(&section).unwrap();
        let back: ManifestSection = serde_json::from_str(&json).unwrap();
        if let ManifestSection::Speech(s) = back {
            let af = s.audio_files.unwrap();
            assert_eq!(af.get("mp3_44100_64").map(|s| s.as_str()), Some("/out/mp3_44100_64_s1.mp3"));
            assert_eq!(af.get("mp3_44100_128").map(|s| s.as_str()), Some("/out/mp3_44100_128_s1.mp3"));
            assert!(af.get("mp3_44100_192").is_none());
        } else {
            panic!("Expected speech");
        }
    }
}
