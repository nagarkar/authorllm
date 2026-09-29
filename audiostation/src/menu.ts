import { invoke } from '@tauri-apps/api/core';
import { relaunch } from '@tauri-apps/plugin-process';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import type { Master, ReloadSummary } from './types';
import { setStatusMessage } from './main';
import { summaryText } from './book';

export function renderMenuBar(container: HTMLElement, onChanged: () => void): void {
  const bar = document.createElement('div');
  bar.className = 'menu-bar';
  bar.innerHTML = `
    <div class="menu-item" id="menu-file">
      <span>File</span>
      <div class="menu-dropdown" id="menu-file-dropdown">
        <button class="menu-action" id="menu-open">Open Audiobook Folder&#8230;</button>
        <button class="menu-action" id="menu-reload">Reload</button>
        <button class="menu-action" id="menu-reveal">Reveal Folder in Finder</button>
        <button class="menu-action" id="menu-close">Close</button>
        <div class="menu-separator"></div>
        <button class="menu-action" id="menu-restart">Restart</button>
      </div>
    </div>`;
  container.appendChild(bar);

  const fileItem = bar.querySelector('#menu-file')!;
  const dropdown = bar.querySelector<HTMLElement>('#menu-file-dropdown')!;
  fileItem.addEventListener('click', (e) => { e.stopPropagation(); dropdown.classList.toggle('open'); });
  document.addEventListener('click', () => dropdown.classList.remove('open'));

  const action = (id: string, fn: () => Promise<void>) => {
    bar.querySelector(id)!.addEventListener('click', async (e) => {
      e.stopPropagation();
      dropdown.classList.remove('open');
      await fn();
    });
  };

  action('#menu-open', () => openFolder(onChanged));
  action('#menu-reload', async () => {
    try {
      const summary: ReloadSummary = await invoke('reload_book');
      setStatusMessage(`Reloaded — ${summaryText(summary)}`);
      onChanged();
    } catch (e) { setStatusMessage(`Reload failed: ${e}`); }
  });
  action('#menu-reveal', async () => {
    const master: Master | null = await invoke('get_master');
    if (master) invoke('reveal_in_finder', { filePath: master.dir }).catch(() => {});
  });
  action('#menu-close', async () => {
    await invoke('close_book');
    onChanged();
  });
  action('#menu-restart', async () => { await relaunch(); });
}

export async function openFolder(onChanged: () => void): Promise<void> {
  const picked = await openDialog({ directory: true, multiple: false, title: 'Open the manuscript\'s _audio folder' });
  if (!picked) return;
  const dir = typeof picked === 'string' ? picked : (picked as string[])[0];
  try {
    const summary: ReloadSummary = await invoke('open_book', { dir });
    setStatusMessage(`Opened ${dir} — ${summaryText(summary)}`);
    onChanged();
  } catch (e) {
    alert(`Could not open ${dir}:\n${e}`);
  }
}

/** Re-open the last folder on boot, if it is still there. */
export async function restoreLastBook(onChanged: () => void): Promise<void> {
  let last: string | null;
  try { last = await invoke<string | null>('get_last_book'); } catch { return; }
  if (!last) return;
  try {
    await invoke('open_book', { dir: last });
    onChanged();
  } catch (e) {
    setStatusMessage(`Last folder could not be opened: ${e}`);
  }
}
