// Pure utility functions — no DOM, no Tauri invoke.
// Exported so they can be imported by tests and by other modules.

import type { ManifestSection, SpeechSection, SectionStatus } from './types';

// ---------------------------------------------------------------------------
// Manifest section logic
// ---------------------------------------------------------------------------

export function computeBlockedSections(sections: ManifestSection[], format: string): Set<string> {
  const voiceBlocked = new Map<string, boolean>();
  const blocked = new Set<string>();
  for (const s of sections) {
    if (s.type !== 'speech') continue;
    const isDone = !!s.audioFiles?.[format] && !s.isDirty;
    const prior  = voiceBlocked.get(s.voiceId) ?? false;
    if (prior)        { blocked.add(s.id); voiceBlocked.set(s.voiceId, true); }
    else if (!isDone) { voiceBlocked.set(s.voiceId, true); }
  }
  return blocked;
}

export function sectionStatus(
  s: SpeechSection,
  blocked: Set<string>,
  errors: Map<string, string>,
  format: string,
): SectionStatus {
  if (errors.has(s.id))                          return 'failed';
  if (blocked.has(s.id))                         return 'blocked';
  if ((s as any)._generating)                    return 'generating';
  if (s.audioFiles?.[format] && s.isDirty)       return 'dirty';
  if (s.audioFiles?.[format])                    return 'done';
  return 'ungenerated';
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

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

export function statusLabel(status: SectionStatus): string {
  const map: Record<SectionStatus, string> = {
    ungenerated: 'Not yet generated',
    generating:  'Generating…',
    done:        'Generated',
    dirty:       'Text changed — regenerate',
    blocked:     'Waiting for prior same-voice section',
    failed:      'Failed — click for error details',
  };
  return map[status];
}

// ---------------------------------------------------------------------------
// Manifest JSON validation (used by menu.ts and tests)
// ---------------------------------------------------------------------------

export type ManifestValidationResult =
  | { ok: true;  manifest: any }
  | { ok: false; error: string };

export function validateManifestJson(json: string): ManifestValidationResult {
  let parsed: any;
  try {
    parsed = JSON.parse(json);
  } catch {
    return { ok: false, error: 'Invalid JSON — could not parse as a manifest.' };
  }
  if (!Array.isArray(parsed?.sections)) {
    return { ok: false, error: 'Not a valid manifest: missing "sections" array.' };
  }
  return { ok: true, manifest: parsed };
}

/**
 * Mirror of the Rust `normalize_text` used for dirty-detection matching.
 * Folds curly quotes/dashes/non-breaking spaces to their ASCII equivalents,
 * drops zero-width and soft-hyphen characters, then collapses all whitespace
 * runs to a single space and trims.
 */
export function normalizeText(text: string): string {
  let out = '';
  for (const c of text) {
    const cp = c.codePointAt(0)!;
    if (cp === 0x2018 || cp === 0x2019 || cp === 0x02BC) { out += "'"; }
    else if (cp === 0x201C || cp === 0x201D)              { out += '"'; }
    else if ((cp >= 0x2010 && cp <= 0x2015) || cp === 0x2212) { out += '-'; }
    else if (cp === 0x00AD || cp === 0x200B || cp === 0x200C ||
             cp === 0x200D || cp === 0xFEFF)              { /* drop */ }
    else if (cp === 0x00A0 || cp === 0x202F || cp === 0x2007 || cp === 0x2060) { out += ' '; }
    else                                                  { out += c; }
  }
  return out.split(/\s+/).filter(Boolean).join(' ');
}
