import { describe, it, expect } from 'vitest';
import {
  sectionStatus, statusLabel, changeLabel, chapterHasChanges, pendingCount,
  remainingCharacters, withHelpers, escHtml, shortModel, formatDuration, isDone, stitchStale,
} from '../utils';
import type { LoadedChapter, Speech, SectionState } from '../types';

const FMT = 'mp3_44100_128';

function speech(id: string, text = 'hello'): Speech {
  return {
    type: 'speech', id, kind: 'paragraph', text, cast: 'narrator',
    voiceId: 'v', voiceName: 'Voice', model: 'eleven_multilingual_v2',
    stability: 0.5, similarity: 0.75, speed: 1, pronunciations: [], source: null,
  };
}

function done(): SectionState {
  return { audioFiles: { [FMT]: 'audio/x.mp3' }, requestId: 'r', generatedAt: '2026-09-04T00:00:00Z' };
}

function chapter(opts: { states?: Record<string, SectionState>; changes?: LoadedChapter['changes']; removed?: string[]; added?: boolean; stitched?: Record<string, string>; stitchKeys?: Record<string, string> } = {}): LoadedChapter {
  return withHelpers({
    chapter: { schema: 1, stem: 's', title: 'S', voiceDefault: 'narrator',
               sections: [speech('a', 'one two'), { type: 'silence', id: 'q', durationMs: 700 }, speech('b', 'three')] },
    state: { schema: 1, sections: opts.states ?? {}, stitched: opts.stitched ?? {}, stitchKeys: opts.stitchKeys },
    changes: opts.changes ?? {},
    removed: opts.removed ?? [],
    added: opts.added ?? false,
    stitchKey: 'key-now',
  } as LoadedChapter);
}

describe('sectionStatus', () => {
  const errors = new Map<string, string>();
  const generating = new Set<string>();
  it('is ungenerated with no state and no flag', () => {
    expect(sectionStatus(speech('a'), undefined, undefined, FMT, errors, generating)).toBe('ungenerated');
  });
  it('is done when the state has the format', () => {
    expect(sectionStatus(speech('a'), done(), undefined, FMT, errors, generating)).toBe('done');
    expect(sectionStatus(speech('a'), done(), undefined, 'mp3_44100_192', errors, generating)).toBe('ungenerated');
  });
  it('a change flag shows only while the id has no audio', () => {
    const change = { kind: 'params' as const, detail: ['speed'] };
    expect(sectionStatus(speech('a'), undefined, change, FMT, errors, generating)).toBe('changed');
    expect(sectionStatus(speech('a'), done(), change, FMT, errors, generating)).toBe('done');
  });
  it('failed and generating win', () => {
    expect(sectionStatus(speech('a'), done(), undefined, FMT, new Map([['a', 'boom']]), generating)).toBe('failed');
    expect(sectionStatus(speech('a'), done(), undefined, FMT, errors, new Set(['a']))).toBe('generating');
  });
  it('has a label for every status', () => {
    for (const s of ['ungenerated', 'generating', 'done', 'changed', 'failed'] as const) {
      expect(statusLabel(s).length).toBeGreaterThan(3);
    }
  });
});

describe('change labels and chapter flags', () => {
  it('names the parameters that moved', () => {
    expect(changeLabel({ kind: 'params', detail: ['voice', 'speed'] })).toBe('changed: voice, speed');
    const dated = changeLabel({ kind: 'params', detail: ['Basilides: buh-SIL-ih-deez → basillydeez'], since: '2026-09-04T22:13:14Z' });
    expect(dated.startsWith('changed since ')).toBe(true);
    expect(dated.endsWith(': Basilides: buh-SIL-ih-deez → basillydeez')).toBe(true);
    expect(changeLabel({ kind: 'params', detail: ['speed'], since: 'garbage' })).toBe('changed since garbage: speed');
    expect(changeLabel({ kind: 'text', detail: [] })).toBe('text changed');
    expect(changeLabel({ kind: 'new', detail: [] })).toBe('new');
  });
  it('a chapter is flagged while a flagged section lacks audio', () => {
    expect(chapterHasChanges(chapter(), FMT)).toBe(false);
    expect(chapterHasChanges(chapter({ changes: { a: { kind: 'text', detail: [] } } }), FMT)).toBe(true);
    expect(chapterHasChanges(chapter({ changes: { a: { kind: 'text', detail: [] } }, states: { a: done() } }), FMT)).toBe(false);
    expect(chapterHasChanges(chapter({ removed: ['gone'] }), FMT)).toBe(true);
    expect(chapterHasChanges(chapter({ added: true }), FMT)).toBe(true);
  });
  it('counts generated speech, ignoring silences', () => {
    expect(pendingCount(chapter({ states: { a: done() } }), FMT)).toEqual({ generated: 1, total: 2 });
    expect(remainingCharacters(chapter({ states: { a: done() } }), FMT)).toBe('three'.length);
  });
  it('isDone reads the format key', () => {
    expect(isDone(done(), FMT)).toBe(true);
    expect(isDone(undefined, FMT)).toBe(false);
  });
});

describe('stitch staleness', () => {
  it('is not stale without a stitched file', () => {
    expect(stitchStale(chapter(), FMT)).toBe(false);
  });
  it('is current when the stored key matches the chapter key', () => {
    expect(stitchStale(chapter({ stitched: { [FMT]: 'audio/s.mp3' }, stitchKeys: { [FMT]: 'key-now' } }), FMT)).toBe(false);
  });
  it('is stale when the key moved or was never recorded', () => {
    expect(stitchStale(chapter({ stitched: { [FMT]: 'audio/s.mp3' }, stitchKeys: { [FMT]: 'key-old' } }), FMT)).toBe(true);
    expect(stitchStale(chapter({ stitched: { [FMT]: 'audio/s.mp3' } }), FMT)).toBe(true);
  });
});

describe('display helpers', () => {
  it('escapes html', () => {
    expect(escHtml('<a href="x">&</a>')).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;');
  });
  it('shortens model ids', () => {
    expect(shortModel('eleven_multilingual_v2')).toBe('multilingual v2');
  });
  it('formats durations', () => {
    expect(formatDuration(0)).toBe('');
    expect(formatDuration(65)).toBe('1:05');
  });
});
