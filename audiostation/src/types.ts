// Shared TypeScript types for audiostation.
// These mirror the Rust serde structs in src-tauri/src/types.rs, which
// mirror what AuthorLM writes (docs/audiobook-pipeline-design.md §14).

export interface ChapterRef { stem: string; file: string; title: string; }

export interface CoverSpec {
  path: string; width: number; height: number; format: string; color: string;
}

export interface DictionaryRef { name: string; id?: string | null; versionId?: string | null; }

export interface CastVoice {
  voiceId: string; voiceName: string; model: string;
  stability: number; similarity: number; speed: number;
}

export interface Book {
  schema: number;
  title: string;
  subtitle: string;
  author: string;
  narrator: string;
  publisher: string;
  language: string;
  copyrightYear?: number | null;
  copyrightHolder: string;
  chapters: ChapterRef[];
  openingCredits?: string | null;
  closingCredits?: string | null;
  aboutAuthor?: string | null;
  retailSample: string[];
  cover?: CoverSpec | null;
  pronunciationDictionary?: DictionaryRef | null;
  cast: Record<string, CastVoice>;
  model: string;
  quality: string;
  paragraphGapMs: number;
  generatedAt: string;
}

export interface Pronunciation { term: string; say: string; }

export interface Speech {
  type: 'speech';
  id: string;
  kind: string;           // "heading" | "paragraph"
  level?: number | null;
  text: string;
  cast: string;
  voiceId: string;
  voiceName: string;
  model: string;
  stability: number;
  similarity: number;
  speed: number;
  pronunciations: Pronunciation[];
  source: unknown;
}

export interface Silence { type: 'silence'; id: string; durationMs: number; }

export type Section = Speech | Silence;

export interface Chapter {
  schema: number;
  file?: string | null;
  stem: string;
  title: string;
  voiceDefault: string;
  sections: Section[];
}

export interface SectionState {
  audioFiles: Record<string, string>;   // format → path relative to the folder
  requestId?: string | null;
  generatedAt?: string | null;
}

export interface ChapterState {
  schema: number;
  sections: Record<string, SectionState>;
  stitched: Record<string, string>;
  stitchKeys?: Record<string, string>;
  durationSecs?: number | null;
  loudnessLufs?: number | null;
  truePeakDbtp?: number | null;
}

export interface SectionChange { kind: 'text' | 'params' | 'new'; detail: string[]; since?: string | null; }

export interface LoadedChapter {
  chapter: Chapter;
  state: ChapterState;
  changes: Record<string, SectionChange>;
  removed: string[];
  added: boolean;
  stitchKey: string;
}

export interface Master {
  dir: string;
  book: Book;
  chapters: LoadedChapter[];
  openingCredits?: LoadedChapter | null;
  closingCredits?: LoadedChapter | null;
  aboutAuthor?: LoadedChapter | null;
  bookChanges: string[];
}

export interface ReloadSummary {
  changedSections: number;
  removedSections: number;
  addedChapters: string[];
  removedChapters: string[];
  bookChanges: string[];
  /** State entries that differ from memory — takes AuthorLM made. */
  takesChanged: number;
}

export interface AuditResult {
  checkId: string; label: string; passed: boolean; message: string;
  severity: 'Error' | 'Warning' | 'Info';
}

export type SectionStatus = 'ungenerated' | 'generating' | 'done' | 'changed' | 'failed';

export const QUALITIES = ['mp3_22050_32', 'mp3_44100_64', 'mp3_44100_128', 'mp3_44100_192'] as const;
export type Quality = typeof QUALITIES[number];

export const QUALITY_LABEL: Record<Quality, string> = {
  mp3_22050_32: 'V.Low (22k/32k)',
  mp3_44100_64: 'Low (44k/64k)',
  mp3_44100_128: 'Std (44k/128k)',
  mp3_44100_192: 'High (44k/192k)',
};

export interface AppSettings { elevenlabsApiKey: string; }

export type View = 'book' | 'settings';
export type Panel = 'chapters' | 'acx';
