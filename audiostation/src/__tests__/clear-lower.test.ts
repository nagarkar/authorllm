// @vitest-environment happy-dom
//
// "Clear Lower Quality" deletes audio from disk across every chapter of the
// book, with no undo. The click must ask first, and do nothing on Cancel.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { LoadedChapter, Master } from '../types';

const { invokeMock, confirmMock } = vi.hoisted(() => ({
  invokeMock: vi.fn(),
  confirmMock: vi.fn(),
}));

vi.mock('@tauri-apps/api/core', () => ({ invoke: invokeMock }));
vi.mock('@tauri-apps/plugin-dialog', () => ({ confirm: confirmMock, open: vi.fn() }));
vi.mock('../main', () => ({ navigate: vi.fn(), setStatusMessage: vi.fn() }));
vi.mock('../menu', () => ({ openFolder: vi.fn() }));

import { renderBookView } from '../book';

function master(): Master {
  return {
    dir: '/tmp/book/_audio',
    book: {
      schema: 1,
      title: 'Test Book',
      subtitle: '',
      author: 'A',
      narrator: 'N',
      publisher: '',
      language: 'en',
      copyrightHolder: '',
      chapters: [{ stem: 'one', file: 'one.md', title: 'One' }],
      retailSample: [],
      cast: {},
      model: 'eleven_multilingual_v2',
      quality: 'mp3_44100_192',
      paragraphGapMs: 500,
      generatedAt: '2026-10-03T00:00:00Z',
    },
    chapters: [{
      chapter: { schema: 1, stem: 'one', title: 'One', voiceDefault: 'narrator', sections: [] },
      state: { schema: 1, sections: {}, stitched: {} },
      changes: {},
      removed: [],
      added: false,
      stitchKey: '',
    } as unknown as LoadedChapter], // plain JSON, as the backend sends it; book.ts adds sections()
    bookChanges: [],
  };
}

const flush = () => new Promise(r => setTimeout(r, 0));

async function renderAndClick(): Promise<void> {
  const root = document.createElement('div');
  document.body.appendChild(root);
  await renderBookView(root);
  (root.querySelector('#btn-clear-lower') as HTMLButtonElement).click();
  await flush();
  await flush();
}

const clearCalls = () => invokeMock.mock.calls.filter(c => c[0] === 'clear_lower_quality');

describe('Clear Lower Quality', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    invokeMock.mockReset();
    confirmMock.mockReset();
    invokeMock.mockImplementation(async (cmd: string) => {
      if (cmd === 'get_master') return master();
      if (cmd === 'get_subscription') throw new Error('no key');
      if (cmd === 'clear_lower_quality') return 3;
      return null;
    });
  });

  it('does not delete anything when the user cancels', async () => {
    confirmMock.mockResolvedValue(false);
    await renderAndClick();
    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(confirmMock.mock.calls[0][0]).toContain('High (44k/192k)');
    expect(confirmMock.mock.calls[0][1]).toMatchObject({ kind: 'warning', okLabel: 'Delete' });
    expect(clearCalls()).toHaveLength(0);
  });

  it('clears at the selected quality once the user confirms', async () => {
    confirmMock.mockResolvedValue(true);
    await renderAndClick();
    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(clearCalls()).toEqual([['clear_lower_quality', { quality: 'mp3_44100_192' }]]);
  });
});
