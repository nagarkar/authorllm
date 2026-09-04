import { listen } from '@tauri-apps/api/event';
import { invoke } from '@tauri-apps/api/core';
import { confirm as dialogConfirm, open } from '@tauri-apps/plugin-dialog';
import type { MergeResult, PartialManifestInfo, View } from './types';
import { renderManifestEditor, clearSectionErrors, clearVoiceCache } from './manifest';
import { renderSettingsView } from './settings';
import { renderMenuBar, restoreLastFile } from './menu';
import { logError, logInfo } from './logger';

interface GenerationProgressEvent {
  sectionIndex: number;
  totalSections: number;
  voiceName: string;
  stability: number;
  similarityBoost: number;
  quality: string;
  dictVersionId?: string;
}

interface StitchProgressEvent {
  message: string;
}

export function setStatusMessage(msg: string): void {
  const bar = document.getElementById('status-bar');
  if (bar) bar.textContent = msg;
}

// ---------------------------------------------------------------------------
// View router
// ---------------------------------------------------------------------------

let currentView: View = 'manifest';

export function navigate(view: View): void {
  currentView = view;
  render();
}

function render(): void {
  const root = document.getElementById('view-root')!;
  root.innerHTML = '';

  if (currentView === 'manifest') {
    renderManifestEditor(root);
  } else {
    renderSettingsView(root);
  }
}

// ---------------------------------------------------------------------------
// Partial manifest-received event (from the HTTP server via Tauri)
// ---------------------------------------------------------------------------

async function setupManifestListener(): Promise<void> {
  await listen<{ info: PartialManifestInfo; partial: unknown }>('partial-manifest-received', async (event) => {
    const { info, partial } = event.payload;
    const partialJson = JSON.stringify(partial);

    setStatusMessage(`Received ${info.displayName} from GAS add-on`);

    const currentPath: string | null = await invoke('get_last_file');

    let targetPath: string | null = null;

    if (currentPath) {
      const confirmed = await dialogConfirm(
        `Add "${info.displayName}" to the current manifest file?\n(${currentPath})`,
        { title: 'Merge Manifest', okLabel: 'Add to current', cancelLabel: 'Choose file…' }
      );
      if (confirmed) {
        targetPath = currentPath;
      }
    }

    if (!targetPath) {
      const chosen = await open({ filters: [{ name: 'Manifest', extensions: ['json'] }] });
      if (!chosen) return;
      targetPath = typeof chosen === 'string' ? chosen : (chosen as string[])[0];
    }

    try {
      const result: MergeResult = await invoke('merge_partial_into_master', {
        filePath: targetPath,
        partialJson,
        createIfMissing: true,
      });
      const msg = `Merged: ${result.sectionsPreserved} preserved, ${result.sectionsUpdated} updated` +
        (result.orphanedFileCount > 0 ? `, ${result.orphanedFileCount} files orphaned` : '');
      setStatusMessage(msg);
      clearSectionErrors();
      clearVoiceCache();
      await invoke('set_manifest_file_path', { filePath: targetPath });
      await invoke('save_last_file', { filePath: targetPath });
      navigate('manifest');
    } catch (e) {
      setStatusMessage(`Merge failed: ${e}`);
    }
  });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function setupProgressListener(): Promise<void> {
  await listen<GenerationProgressEvent>('generation-progress', (event) => {
    const { sectionIndex, totalSections, voiceName, stability, similarityBoost, quality, dictVersionId } = event.payload;
    const parts = [
      `Generating ${sectionIndex} of ${totalSections}`,
      `voice: ${voiceName}`,
      `stability=${stability.toFixed(2)}`,
      `sim=${similarityBoost.toFixed(2)}`,
      `quality=${quality}`,
    ];
    if (dictVersionId) parts.push(`dict=${dictVersionId.slice(0, 8)}…`);
    setStatusMessage(parts.join(' · '));
  });
  await listen<StitchProgressEvent>('stitch-progress', (event) => {
    setStatusMessage(event.payload.message);
  });
}

async function boot(): Promise<void> {
  const app = document.getElementById('app')!;

  // Persistent menu bar — survives view transitions.
  renderMenuBar(app, () => { clearVoiceCache(); navigate('manifest'); });

  // View container — cleared and re-rendered on each navigation.
  const viewRoot = document.createElement('div');
  viewRoot.id = 'view-root';
  app.appendChild(viewRoot);

  // Persistent status bar — outside the scrollable area.
  const statusBar = document.createElement('div');
  statusBar.id = 'status-bar';
  app.appendChild(statusBar);

  await setupManifestListener();
  await setupProgressListener();
  await restoreLastFile(() => navigate('manifest'));
  render();
}

boot()
  .then(() => logInfo('boot', 'App ready'))
  .catch((e) => logError('boot', e));

// Global unhandled error capture → Rust log file.
window.addEventListener('error', (e) => {
  logError('window.onerror', `${e.message} at ${e.filename}:${e.lineno}:${e.colno}`);
});
window.addEventListener('unhandledrejection', (e) => {
  logError('unhandledrejection', e.reason);
});
