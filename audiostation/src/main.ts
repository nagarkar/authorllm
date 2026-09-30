import { listen } from '@tauri-apps/api/event';
import type { ReloadSummary, View } from './types';
import { renderBookView, clearErrors, summaryText } from './book';
import { renderSettingsView } from './settings';
import { renderMenuBar, restoreLastBook } from './menu';
import { logError, logInfo } from './logger';

interface GenerationProgressEvent {
  sectionIndex: number; totalSections: number; stem: string; voiceName: string; quality: string;
}
interface StitchProgressEvent { message: string; }

export function setStatusMessage(msg: string): void {
  const bar = document.getElementById('status-bar');
  if (bar) bar.textContent = msg;
}

// ---------------------------------------------------------------------------
// View router
// ---------------------------------------------------------------------------

let currentView: View = 'book';

export function navigate(view: View): void {
  currentView = view;
  render();
}

function render(): void {
  const root = document.getElementById('view-root')!;
  root.innerHTML = '';
  if (currentView === 'book') renderBookView(root);
  else renderSettingsView(root);
}

// ---------------------------------------------------------------------------
// Events from Rust
// ---------------------------------------------------------------------------

async function setupListeners(): Promise<void> {
  await listen<ReloadSummary>('book-changed', (event) => {
    setStatusMessage(`AuthorLM exported — ${summaryText(event.payload)}`);
    if (currentView === 'book') render();
  });
  // AuthorLM's `audio generate` wrote a take into the shared state
  // (design §5, guard 1): repaint so the row turns green here too.
  await listen<ReloadSummary>('state-changed', (event) => {
    setStatusMessage(`AuthorLM rendered — ${summaryText(event.payload)}`);
    if (currentView === 'book') render();
  });
  await listen<string>('book-error', (event) => {
    setStatusMessage(`Reload failed: ${event.payload}`);
  });
  await listen<GenerationProgressEvent>('generation-progress', (event) => {
    const { sectionIndex, totalSections, stem, voiceName, quality } = event.payload;
    setStatusMessage(`Generating ${sectionIndex} of ${totalSections} · ${stem} · ${voiceName} · ${quality}`);
  });
  await listen<StitchProgressEvent>('stitch-progress', (event) => {
    setStatusMessage(event.payload.message);
  });
  // Progressive output: each generated section repaints the view so its
  // row turns green while the rest of the chapter is still rendering.
  await listen<{ stem: string; sectionId: string; format: string }>('section-generated', () => {
    if (currentView === 'book') render();
  });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot(): Promise<void> {
  const app = document.getElementById('app')!;
  renderMenuBar(app, () => { clearErrors(); navigate('book'); });

  const viewRoot = document.createElement('div');
  viewRoot.id = 'view-root';
  app.appendChild(viewRoot);

  const statusBar = document.createElement('div');
  statusBar.id = 'status-bar';
  app.appendChild(statusBar);

  await setupListeners();
  await restoreLastBook(() => navigate('book'));
  render();
}

boot()
  .then(() => logInfo('boot', 'audiostation ready'))
  .catch((e) => logError('boot', e));

window.addEventListener('error', (e) => {
  logError('window.onerror', `${e.message} at ${e.filename}:${e.lineno}:${e.colno}`);
});
window.addEventListener('unhandledrejection', (e) => {
  logError('unhandledrejection', e.reason);
});
