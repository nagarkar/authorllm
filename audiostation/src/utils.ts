// Pure helpers — no DOM, no Tauri invoke — so they can be tested directly.

import type { LoadedChapter, Master, Section, SectionChange, SectionState, SectionStatus, Speech } from './types';

/** A section is done at `format` when its id has a state entry for that
 *  format. (Whether the file exists on disk is the Rust side's check; the
 *  view trusts the state it was handed.) */
export function isDone(state: SectionState | undefined, format: string): boolean {
  return !!state?.audioFiles?.[format];
}

export function sectionStatus(
  speech: Speech,
  state: SectionState | undefined,
  change: SectionChange | undefined,
  format: string,
  errors: Map<string, string>,
  generating: Set<string>,
): SectionStatus {
  if (errors.has(speech.id)) return 'failed';
  if (generating.has(speech.id)) return 'generating';
  if (isDone(state, format)) return 'done';
  if (change) return 'changed';
  return 'ungenerated';
}

export function statusLabel(status: SectionStatus): string {
  const map: Record<SectionStatus, string> = {
    ungenerated: 'Not yet generated',
    generating: 'Generating…',
    done: 'Generated',
    changed: 'Changed since its audio — regenerate',
    failed: 'Failed — click for error details',
  };
  return map[status];
}

/** "Sep 4, 22:13" in the viewer's locale; the raw string if unparsable. */
export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function changeLabel(change: SectionChange): string {
  const since = change.since ? ` since ${formatWhen(change.since)}` : '';
  if (change.kind === 'params') return `changed${since}: ${change.detail.join(', ')}`;
  if (change.kind === 'text') return `text changed${since}`;
  return 'new';
}

/** Chapters whose sections carry a change flag, or that were added. */
export function chapterHasChanges(chapter: LoadedChapter, format: string): boolean {
  if (chapter.added) return true;
  if (chapter.removed.length > 0) return true;
  return Object.keys(chapter.changes).some(id => !isDone(chapter.state.sections[id], format));
}

export function pendingCount(chapter: LoadedChapter, format: string): { generated: number; total: number } {
  let generated = 0;
  let total = 0;
  for (const s of chapter.sections()) {
    total += 1;
    if (isDone(chapter.state.sections[s.id], format)) generated += 1;
  }
  return { generated, total };
}

// A LoadedChapter arrives as plain JSON; give it the one helper the
// view keeps reaching for.
declare module './types' {
  interface LoadedChapter { sections(): Speech[]; }
}

export function withHelpers(chapter: LoadedChapter): LoadedChapter {
  chapter.sections = () => chapter.chapter.sections.filter((s): s is Speech => s.type === 'speech');
  return chapter;
}

export function allChapters(master: Master): LoadedChapter[] {
  const out = [...master.chapters];
  if (master.openingCredits) out.push(master.openingCredits);
  if (master.closingCredits) out.push(master.closingCredits);
  if (master.aboutAuthor) out.push(master.aboutAuthor);
  return out.map(withHelpers);
}

export function speechOf(section: Section): Speech | null {
  return section.type === 'speech' ? section : null;
}

export function shortModel(m: string): string {
  return m.replace(/^eleven_/, '').replace(/_/g, ' ');
}

export function escHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function formatDuration(secs: number | null | undefined): string {
  if (!secs || secs <= 0) return '';
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/** Characters of speech still to generate at `format` — the credit cost. */
export function remainingCharacters(chapter: LoadedChapter, format: string): number {
  let chars = 0;
  for (const s of chapter.sections()) {
    if (!isDone(chapter.state.sections[s.id], format)) chars += s.text.length;
  }
  return chars;
}

/** A stitched file is stale when it was made from other sections or another
 *  encoder recipe than the chapter has now. */
export function stitchStale(chapter: LoadedChapter, format: string): boolean {
  if (!chapter.state.stitched?.[format]) return false;
  return (chapter.state.stitchKeys?.[format] ?? '') !== chapter.stitchKey;
}
