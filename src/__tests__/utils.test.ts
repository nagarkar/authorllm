import { describe, it, expect } from 'vitest';
import {
  computeBlockedSections,
  sectionStatus,
  shortModel,
  escHtml,
  statusLabel,
  validateManifestJson,
} from '../utils';
import type { ManifestSection, SpeechSection } from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const TEST_FORMAT = 'mp3_44100_64';

function speech(
  id: string,
  voiceId: string,
  opts: { done?: boolean; dirty?: boolean; generating?: boolean } = {},
): SpeechSection {
  return {
    type: 'speech',
    id,
    text: 'hello',
    voiceId,
    voiceName: 'Voice',
    ttsModel: 'eleven_multilingual_v2',
    stability: 0.5,
    similarityBoost: 0.75,
    audioFiles: opts.done ? { [TEST_FORMAT]: `/tmp/${TEST_FORMAT}_${id}.mp3` } : undefined,
    isDirty: opts.dirty ?? undefined,
    ...(opts.generating ? { _generating: true } : {}),
  } as any;
}

function silence(id: string): ManifestSection {
  return { type: 'silence', id, durationMs: 500 };
}

// ---------------------------------------------------------------------------
// computeBlockedSections
// ---------------------------------------------------------------------------

describe('computeBlockedSections', () => {
  it('returns empty set for empty sections', () => {
    expect(computeBlockedSections([], TEST_FORMAT)).toEqual(new Set());
  });

  it('single ungenerated section is not blocked', () => {
    const blocked = computeBlockedSections([speech('s1', 'v1')], TEST_FORMAT);
    expect(blocked.size).toBe(0);
  });

  it('second same-voice section blocked when first is ungenerated', () => {
    const sections: ManifestSection[] = [speech('s1', 'v1'), speech('s2', 'v1')];
    const blocked = computeBlockedSections(sections, TEST_FORMAT);
    expect(blocked.has('s1')).toBe(false);
    expect(blocked.has('s2')).toBe(true);
  });

  it('second same-voice not blocked when first is done', () => {
    const sections: ManifestSection[] = [
      speech('s1', 'v1', { done: true }),
      speech('s2', 'v1'),
    ];
    expect(computeBlockedSections(sections, TEST_FORMAT).size).toBe(0);
  });

  it('dirty section counts as not-done and blocks later same-voice', () => {
    const sections: ManifestSection[] = [
      speech('s1', 'v1', { done: true, dirty: true }),
      speech('s2', 'v1'),
    ];
    expect(computeBlockedSections(sections, TEST_FORMAT).has('s2')).toBe(true);
  });

  it('different voices do not block each other', () => {
    const sections: ManifestSection[] = [
      speech('s1', 'v1'),
      speech('s2', 'v2'),
      speech('s3', 'v1'),
    ];
    const blocked = computeBlockedSections(sections, TEST_FORMAT);
    expect(blocked.has('s1')).toBe(false);
    expect(blocked.has('s2')).toBe(false);
    expect(blocked.has('s3')).toBe(true);
  });

  it('silence sections are ignored', () => {
    const sections: ManifestSection[] = [
      speech('s1', 'v1'),
      silence('gap'),
      speech('s2', 'v1'),
    ];
    const blocked = computeBlockedSections(sections, TEST_FORMAT);
    expect(blocked.has('s2')).toBe(true);
  });

  it('chain of three same-voice all after first are blocked', () => {
    const sections: ManifestSection[] = [
      speech('s1', 'v1'),
      speech('s2', 'v1'),
      speech('s3', 'v1'),
    ];
    const blocked = computeBlockedSections(sections, TEST_FORMAT);
    expect(blocked.has('s1')).toBe(false);
    expect(blocked.has('s2')).toBe(true);
    expect(blocked.has('s3')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// sectionStatus
// ---------------------------------------------------------------------------

describe('sectionStatus', () => {
  it('ungenerated when no request id', () => {
    const s = speech('s1', 'v1');
    expect(sectionStatus(s, new Set(), new Map(), TEST_FORMAT)).toBe('ungenerated');
  });

  it('done when has request id and not dirty', () => {
    const s = speech('s1', 'v1', { done: true });
    expect(sectionStatus(s, new Set(), new Map(), TEST_FORMAT)).toBe('done');
  });

  it('dirty when has request id and is dirty', () => {
    const s = speech('s1', 'v1', { done: true, dirty: true });
    expect(sectionStatus(s, new Set(), new Map(), TEST_FORMAT)).toBe('dirty');
  });

  it('blocked when in blocked set', () => {
    const s = speech('s1', 'v1');
    expect(sectionStatus(s, new Set(["s1"]), new Map(), TEST_FORMAT)).toBe('blocked');
  });

  it('failed when in errors map', () => {
    const s = speech('s1', 'v1');
    const errors = new Map([['s1', 'API error']]);
    expect(sectionStatus(s, new Set(), errors, TEST_FORMAT)).toBe('failed');
  });

  it('failed takes priority over blocked', () => {
    const s = speech('s1', 'v1');
    const errors = new Map([['s1', 'API error']]);
    expect(sectionStatus(s, new Set(["s1"]), errors, TEST_FORMAT)).toBe('failed');
  });
});

// ---------------------------------------------------------------------------
// shortModel
// ---------------------------------------------------------------------------

describe('shortModel', () => {
  it('strips eleven_ prefix', () => {
    expect(shortModel('eleven_multilingual_v2')).toBe('multilingual v2');
  });

  it('replaces underscores with spaces', () => {
    expect(shortModel('eleven_turbo_v2_5')).toBe('turbo v2 5');
  });

  it('replaces underscores with spaces for unknown model (no eleven_ prefix)', () => {
    expect(shortModel('custom_model')).toBe('custom model');
  });
});

// ---------------------------------------------------------------------------
// escHtml
// ---------------------------------------------------------------------------

describe('escHtml', () => {
  it('escapes ampersand', () => {
    expect(escHtml('a & b')).toBe('a &amp; b');
  });

  it('escapes less-than', () => {
    expect(escHtml('<script>')).toBe('&lt;script&gt;');
  });

  it('escapes double quote', () => {
    expect(escHtml('"hello"')).toBe('&quot;hello&quot;');
  });

  it('passthrough for plain text', () => {
    expect(escHtml('Hello World')).toBe('Hello World');
  });

  it('escapes multiple entities in one string', () => {
    expect(escHtml('<b>A & B</b>')).toBe('&lt;b&gt;A &amp; B&lt;/b&gt;');
  });
});

// ---------------------------------------------------------------------------
// statusLabel
// ---------------------------------------------------------------------------

describe('statusLabel', () => {
  it('returns label for each status', () => {
    const statuses = ['ungenerated', 'generating', 'done', 'dirty', 'blocked', 'failed'] as const;
    for (const s of statuses) {
      expect(statusLabel(s)).toBeTruthy();
      expect(typeof statusLabel(s)).toBe('string');
    }
  });

  it('ungenerated label mentions "generated"', () => {
    expect(statusLabel('ungenerated').toLowerCase()).toContain('generated');
  });

  it('blocked label mentions "waiting" or "prior"', () => {
    const label = statusLabel('blocked').toLowerCase();
    expect(label.includes('waiting') || label.includes('prior')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// validateManifestJson
// ---------------------------------------------------------------------------

describe('validateManifestJson', () => {
  it('rejects invalid JSON', () => {
    const result = validateManifestJson('not json {{');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/invalid json/i);
  });

  it('rejects JSON without sections array', () => {
    const result = validateManifestJson('{"version":1}');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/sections/i);
  });

  it('rejects when sections is not an array', () => {
    const result = validateManifestJson('{"sections":"oops"}');
    expect(result.ok).toBe(false);
  });

  it('accepts minimal valid manifest', () => {
    const result = validateManifestJson('{"sections":[]}');
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.manifest.sections).toEqual([]);
  });

  it('accepts full manifest', () => {
    const json = JSON.stringify({
      version: 1,
      documentTitle: 'Doc',
      tabName: 'Tab',
      generatedAt: '2026-01-01T00:00:00Z',
      sections: [
        {
          type: 'speech',
          id: 's1',
          text: 'Hello',
          voiceId: 'v1',
          voiceName: 'Alice',
          ttsModel: 'eleven_multilingual_v2',
          stability: 0.5,
          similarityBoost: 0.75,
        },
      ],
    });
    const result = validateManifestJson(json);
    expect(result.ok).toBe(true);
  });
});
