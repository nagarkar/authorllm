// Shared TypeScript types for EditorLLM Desktop.
// These mirror the Rust serde structs in src-tauri/src/types.rs.
// Do NOT import this file from GAS source (it lives in desktop/ only).

export interface VoiceInfo {
  voiceId: string;
  name: string;
}

export interface PronunciationDictionaryLocator {
  pronunciationDictionaryId: string;
  versionId: string;
}

export interface AudioManifest {
  version: 1;
  documentTitle: string;
  tabName: string;
  /** ISO 8601 timestamp */
  generatedAt: string;
  sections: ManifestSection[];
  /** Pronunciation dictionary locators applied to all speech sections during generation. */
  pronunciationDictionaryLocators?: PronunciationDictionaryLocator[];
  /** Stitched full-audio files keyed by output format (e.g. "mp3_44100_64"). */
  audioFiles?: Record<string, string>;
  /** Duration of the stitched output in seconds (set after successful stitch). */
  durationSecs?: number;
  /** Integrated loudness of the stitched output in LUFS (set after two-pass stitch). */
  loudnessLufs?: number;
  /** True peak of the stitched output in dBTP (set after two-pass stitch). */
  truePeakDbtp?: number;
}

export type ManifestSection = SpeechSection | SilenceSection;

export interface SpeechSection {
  id: string;           // UUID
  type: 'speech';
  text: string;
  voiceId: string;
  /** Display name — not sent to ElevenLabs directly */
  voiceName: string;
  ttsModel: string;
  stability: number;    // 0.0 – 1.0
  similarityBoost: number; // 0.0 – 1.0
  speed?: number;       // 0.25 – 4.0; defaults to 1.0 if absent

  /** Generated audio files keyed by output format (e.g. "mp3_44100_64" → "/path/to/file.mp3"). */
  audioFiles?: Record<string, string>;
  /** True when text or voice was edited after generation */
  isDirty?: boolean;
}

export type AudioQuality = 'vlow' | 'low' | 'med' | 'high';

/** Map from AudioQuality label to ElevenLabs output_format string. */
export const FORMAT_FOR_QUALITY: Record<AudioQuality, string> = {
  vlow: 'mp3_22050_32',
  low:  'mp3_44100_64',
  med:  'mp3_44100_128',
  high: 'mp3_44100_192',
};

export interface SilenceSection {
  id: string;
  type: 'silence';
  durationMs: number;
  /** Set after stitching — path to the generated silence clip. */
  audioFilePath?: string;
}

// ---------------------------------------------------------------------------
// UI state types (not sent to Rust — frontend only)
// ---------------------------------------------------------------------------

export type SectionStatus =
  | 'ungenerated'
  | 'generating'
  | 'done'
  | 'dirty'
  | 'blocked'
  | 'failed';

export interface AppSettings {
  elevenlabsApiKey: string;
  outputDir: string;
  defaultTtsModel: 'eleven_multilingual_v2' | 'eleven_turbo_v2_5' | 'eleven_flash_v2_5';
}

export type View = 'manifest' | 'settings';

// ---------------------------------------------------------------------------
// Master manifest — multi-chapter ACX audiobook structure
// ---------------------------------------------------------------------------

export interface BookMetadata {
  title: string;
  subtitle?: string;
  author: string;
  narrator: string;
  publisher?: string;
  copyrightYear?: number;
  copyrightHolder?: string;
  language: string;
  asin?: string;
  isbn?: string;
}

export interface MasterManifest {
  version: 2;
  documentTitle: string;
  generatedAt: string;
  chapters: AudioManifest[];
  openingCredits?: AudioManifest;
  closingCredits?: AudioManifest;
  aboutAuthor?: AudioManifest;
  retailSample?: RetailSampleSpec;
  cover?: CoverSpec;
  metadata?: BookMetadata;
}

export interface RetailSampleSpec {
  sectionRefs: SectionRef[];
}

export interface SectionRef {
  chapterTabName: string;
  sectionId: string;
}

export interface CoverSpec {
  imagePath?: string;
  width?: number;
  height?: number;
  format?: string;
  fileSizeBytes?: number;
  colorSpace?: string;
}

export interface PartialManifestInfo {
  kind: string;
  displayName: string;
  documentTitle: string;
}

export interface MergeResult {
  sectionsPreserved: number;
  sectionsUpdated: number;
  orphanedFileCount: number;
  orphanedDir?: string;
}

export interface AuditResult {
  checkId: string;
  label: string;
  passed: boolean;
  message: string;
  severity: 'Error' | 'Warning' | 'Info';
}

export type ManifestPanel = 'chapters' | 'acx' | 'raw';
