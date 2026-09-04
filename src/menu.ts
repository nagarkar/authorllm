import { invoke } from '@tauri-apps/api/core';
import { relaunch } from '@tauri-apps/plugin-process';
import { open as openFilePicker, save as saveFilePicker, confirm as dialogConfirm } from '@tauri-apps/plugin-dialog';
import { exists } from '@tauri-apps/plugin-fs';
import type { MasterManifest } from './types';
import { clearSectionErrors } from './manifest';
import { validateManifestJson } from './utils';

// ---------------------------------------------------------------------------
// File path tracking — null means loaded from clipboard / HTTP (no file path)
// ---------------------------------------------------------------------------

let currentFilePath_: string | null = null;

export function setCurrentFilePath(path: string | null): void {
  currentFilePath_ = path;
  // Keep the Rust in-memory state in sync so generate commands use the correct output dir.
  invoke('set_manifest_file_path', { filePath: path }).catch(() => {/* non-fatal */});
  invoke('save_last_file', { filePath: path }).catch(() => {/* non-fatal */});
}

// ---------------------------------------------------------------------------
// Menu bar
// ---------------------------------------------------------------------------

export function renderMenuBar(
  container: HTMLElement,
  onManifestLoaded: () => void
): void {
  const bar = document.createElement('div');
  bar.className = 'menu-bar';
  bar.innerHTML = `
    <div class="menu-item" id="menu-file">
      <span>File</span>
      <div class="menu-dropdown" id="menu-file-dropdown">
        <button class="menu-action" id="menu-paste-manifest">Manifest from Clipboard</button>
        <button class="menu-action" id="menu-open-manifest">Open Manifest File&#8230;</button>
        <button class="menu-action" id="menu-save-manifest">Save Manifest&#8230;</button>
        <button class="menu-action" id="menu-close-file">Close File</button>
        <div class="menu-separator"></div>
        <button class="menu-action" id="menu-restart">Restart</button>
      </div>
    </div>`;
  container.appendChild(bar);

  const fileItem = bar.querySelector('#menu-file')!;
  const dropdown = bar.querySelector<HTMLElement>('#menu-file-dropdown')!;

  fileItem.addEventListener('click', (e) => {
    e.stopPropagation();
    dropdown.classList.toggle('open');
  });

  // Close dropdown when clicking anywhere outside.
  document.addEventListener('click', () => dropdown.classList.remove('open'));

  bar.querySelector('#menu-paste-manifest')!.addEventListener('click', async (e) => {
    e.stopPropagation();
    dropdown.classList.remove('open');
    await loadFromClipboard(onManifestLoaded);
  });

  bar.querySelector('#menu-open-manifest')!.addEventListener('click', async (e) => {
    e.stopPropagation();
    dropdown.classList.remove('open');
    await loadFromFile(onManifestLoaded);
  });

  bar.querySelector('#menu-save-manifest')!.addEventListener('click', async (e) => {
    e.stopPropagation();
    dropdown.classList.remove('open');
    await saveToFile();
  });

  bar.querySelector('#menu-close-file')!.addEventListener('click', async (e) => {
    e.stopPropagation();
    dropdown.classList.remove('open');
    await closeFile(onManifestLoaded);
  });

  bar.querySelector('#menu-restart')!.addEventListener('click', async (e) => {
    e.stopPropagation();
    dropdown.classList.remove('open');
    await relaunch();
  });
}

// ---------------------------------------------------------------------------
// Load paths
// ---------------------------------------------------------------------------

async function loadFromClipboard(onLoaded: () => void): Promise<void> {
  let text: string;
  try {
    text = await navigator.clipboard.readText();
  } catch {
    alert('Could not read clipboard.');
    return;
  }
  if (!text.trim()) {
    alert('Clipboard is empty.');
    return;
  }
  setCurrentFilePath(null);
  await applyJson(text, onLoaded);
}

async function saveToFile(): Promise<void> {
  const manifest: MasterManifest | null = await invoke('get_master_manifest');
  if (!manifest) {
    alert('No manifest is currently loaded — nothing to save.');
    return;
  }

  const path = await saveFilePicker({
    title: 'Save Manifest',
    defaultPath: `${manifest.documentTitle}.json`,
    filters: [{ name: 'JSON', extensions: ['json'] }],
  });
  if (!path) return;

  try {
    // Use native Rust I/O so the write isn't restricted by the Tauri fs-plugin
    // path scope, which only covers the app's data directories by default.
    await invoke('save_manifest_to_file', { filePath: path });
    setCurrentFilePath(path);
  } catch (e) {
    alert(`Could not write file: ${e}`);
  }
}

async function loadFromFile(onLoaded: () => void): Promise<void> {
  const path = await openFilePicker({
    title: 'Open Manifest',
    filters: [{ name: 'JSON', extensions: ['json'] }],
  });
  if (!path) return;

  const current: MasterManifest | null = await invoke('get_master_manifest');
  if (current) {
    const ok = await dialogConfirm(
      `Replace the current manifest for "${current.documentTitle}"?`,
      { title: 'Replace Manifest?', okLabel: 'Replace', cancelLabel: 'Keep' }
    );
    if (!ok) return;
  }

  try {
    // Use the Rust command so parse_manifest_json handles both v1 and v2 formats.
    await invoke('load_manifest_from_file', { filePath: path as string });
  } catch (e) {
    alert(`Could not load file: ${e}`);
    return;
  }
  setCurrentFilePath(path as string);
  clearSectionErrors();
  onLoaded();
}

// ---------------------------------------------------------------------------
// Close file
// ---------------------------------------------------------------------------

async function closeFile(onClosed: () => void): Promise<void> {
  const manifest: MasterManifest | null = await invoke('get_master_manifest');
  if (!manifest) return;

  let savePath = currentFilePath_;

  if (!savePath) {
    // Loaded from clipboard or HTTP — ask for a save location.
    savePath = await saveFilePicker({
      title: 'Save Before Closing',
      defaultPath: `${manifest.documentTitle}.json`,
      filters: [{ name: 'JSON', extensions: ['json'] }],
    }) as string | null;

    if (!savePath) return; // user cancelled the picker — abort close
  }

  let fileExists = false;
  try {
    fileExists = await exists(savePath);
  } catch {
    fileExists = false;
  }

  if (fileExists) {
    const choice = await showCloseChoice_(savePath);
    if (choice === 'cancel') return;

    if (choice === 'overwrite') {
      try {
        await invoke('save_manifest_to_file', { filePath: savePath });
      } catch (e) {
        alert(`Could not write file: ${e}`);
        return;
      }
    }
    // 'discard' falls through without saving
  } else {
    // File doesn't exist yet — save unconditionally.
    try {
      await invoke('save_manifest_to_file', { filePath: savePath });
    } catch (e) {
      alert(`Could not write file: ${e}`);
      return;
    }
  }

  await invoke('clear_manifest');
  setCurrentFilePath(null);
  clearSectionErrors();
  onClosed();
}

// ---------------------------------------------------------------------------
// Shared validation + apply
// ---------------------------------------------------------------------------

async function applyJson(json: string, onLoaded: () => void): Promise<void> {
  const result = validateManifestJson(json);
  if (!result.ok) {
    alert(result.error);
    return;
  }
  const manifest = result.manifest;

  const current: MasterManifest | null = await invoke('get_master_manifest');
  if (current) {
    const ok = await dialogConfirm(
      `Replace the current manifest for "${current.documentTitle}"?`,
      { title: 'Replace Manifest?', okLabel: 'Replace', cancelLabel: 'Keep' }
    );
    if (!ok) return;
  }

  await invoke('set_master_manifest', { manifest });
  clearSectionErrors();
  onLoaded();
}

// ---------------------------------------------------------------------------
// 3-option modal: Overwrite / Discard Changes / Cancel
// ---------------------------------------------------------------------------

function showCloseChoice_(filePath: string): Promise<'overwrite' | 'discard' | 'cancel'> {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'error-backdrop';
    const fileName = filePath.split('/').pop() ?? filePath;
    backdrop.innerHTML = `
      <div class="error-box">
        <div class="error-box-header">
          <span>File Already Exists</span>
        </div>
        <p style="margin: 0 0 16px; color: var(--text-muted, #999);">
          <strong>${escHtml_(fileName)}</strong> already exists.
          What would you like to do?
        </p>
        <div class="error-actions">
          <button class="btn btn-primary"   id="choice-overwrite">Overwrite</button>
          <button class="btn btn-secondary" id="choice-discard">Discard Changes</button>
          <button class="btn btn-secondary" id="choice-cancel">Cancel</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);

    const cleanup = (result: 'overwrite' | 'discard' | 'cancel') => {
      document.body.removeChild(backdrop);
      resolve(result);
    };

    backdrop.querySelector('#choice-overwrite')!.addEventListener('click', () => cleanup('overwrite'));
    backdrop.querySelector('#choice-discard')!.addEventListener('click', () => cleanup('discard'));
    backdrop.querySelector('#choice-cancel')!.addEventListener('click', () => cleanup('cancel'));
    // Clicking the backdrop itself cancels.
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) cleanup('cancel'); });
  });
}

function escHtml_(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---------------------------------------------------------------------------
// Restore last opened file on boot
// ---------------------------------------------------------------------------

export async function restoreLastFile(onLoaded: () => void): Promise<void> {
  let lastPath: string | null;
  try {
    lastPath = await invoke<string | null>('get_last_file');
  } catch {
    return;
  }
  if (!lastPath) return;

  let fileOk: boolean;
  try {
    fileOk = await exists(lastPath);
  } catch {
    fileOk = false;
  }

  if (!fileOk) {
    // File was moved or deleted — forget it.
    invoke('save_last_file', { filePath: null }).catch(() => {/* non-fatal */});
    return;
  }

  // Use the Rust command to read and parse — avoids Tauri fs-plugin scope
  // restrictions that block readTextFile on arbitrary paths at startup.
  try {
    await invoke('load_manifest_from_file', { filePath: lastPath });
  } catch {
    return;
  }

  currentFilePath_ = lastPath;
  clearSectionErrors();
  onLoaded();
}
