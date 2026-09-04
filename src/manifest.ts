import { invoke } from '@tauri-apps/api/core';
import { open } from '@tauri-apps/plugin-dialog';
import { navigate, setStatusMessage } from './main';
import { logError } from './logger';
import type {
  AudioManifest,
  BookMetadata,
  MasterManifest,
  ManifestSection,
  SpeechSection,
  SectionStatus,
  VoiceInfo,
  AudioQuality,
  AuditResult,
  ManifestPanel,
} from './types';
import { FORMAT_FOR_QUALITY } from './types';
import { computeBlockedSections, sectionStatus as computeSectionStatus, escHtml, statusLabel, normalizeText } from './utils';

// ---------------------------------------------------------------------------
// Local types
// ---------------------------------------------------------------------------

interface DictInfo {
  name: string;
  id: string;
  latestVersionId: string;
  rules: Array<{ stringToReplace: string; replaceWith: string; alphabet: string }>;
}

interface DictionaryInfoItem {
  id: string;
  name: string;
  latestVersionId: string;
}

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

const sectionErrors_ = new Map<string, string>();
const dictInfoCache_ = new Map<string, DictInfo>();
let voiceCache_: VoiceInfo[] | null = null;
let lastChosenVoice_: { voiceId: string; voiceName: string } | null = null;
let currentAudio_: HTMLAudioElement | null = null;
let currentAudioUrl_: string | null = null;   // object URL — must be revoked when discarded
let currentPlayingId_: string | null = null;
let fullAudioBlobUrl_: string | null = null;  // blob URL for native <audio> player
let selectedId_: string | null = null;
let selectedQuality_: AudioQuality = 'low';
let lastManifestId_: string | null = null;
let contextMenu_: HTMLElement | null = null;
let currentChapterIndex_: number = 0;
let currentPanel_: ManifestPanel = 'chapters';
let acxQuality_: string = 'mp3_44100_128';
let activeFindClose_: (() => void) | null = null;
let findBarTargets_: (() => Array<HTMLInputElement | HTMLTextAreaElement>) | null = null;
let findBarListenerAttached_ = false;

const QUALITY_FOR_FORMAT: Record<string, AudioQuality> = {
  'mp3_44100_192': 'high',
  'mp3_44100_128': 'med',
  'mp3_44100_64':  'low',
  'mp3_22050_32':  'vlow',
};

function autoSelectQuality_(manifest: AudioManifest): void {
  const available = new Set<string>();
  for (const s of manifest.sections) {
    if (s.type === 'speech' && s.audioFiles) {
      for (const fmt of Object.keys(s.audioFiles)) available.add(fmt);
    }
  }
  for (const fmt of ['mp3_44100_192', 'mp3_44100_128', 'mp3_44100_64', 'mp3_22050_32']) {
    if (available.has(fmt)) { selectedQuality_ = QUALITY_FOR_FORMAT[fmt]; return; }
  }
}

export function clearSectionErrors(): void {
  sectionErrors_.clear();
  stopPlayback_();
  lastManifestId_ = null;   // force quality auto-select on next render
  currentChapterIndex_ = 0;
  if (fullAudioBlobUrl_) { URL.revokeObjectURL(fullAudioBlobUrl_); fullAudioBlobUrl_ = null; }
}

/** Invalidate the in-memory voice list — call when loading a new manifest file. */
export function clearVoiceCache(): void {
  voiceCache_ = null;
}

async function getVoices_(): Promise<VoiceInfo[]> {
  if (voiceCache_) return voiceCache_;
  try {
    voiceCache_ = await invoke<VoiceInfo[]>('list_voices');
  } catch {
    voiceCache_ = [];
  }
  return voiceCache_;
}

/** Stop and discard the current audio, revoking its object URL. */
function stopPlayback_(): void {
  if (!currentAudio_) return;
  // Clear refs BEFORE calling pause() so the 'pause' event handler is a no-op.
  const audio = currentAudio_;
  const url   = currentAudioUrl_;
  const prevId = currentPlayingId_;
  currentAudio_     = null;
  currentAudioUrl_  = null;
  currentPlayingId_ = null;
  audio.pause();
  if (url) URL.revokeObjectURL(url);
  if (prevId) setPlayButton_(prevId, 'idle');
}

/** Returns the visual playback state for a given section/audio ID. */
function playState_(id: string): 'idle' | 'playing' | 'paused' {
  if (currentPlayingId_ !== id || !currentAudio_) return 'idle';
  return currentAudio_.paused ? 'paused' : 'playing';
}

/**
 * Load an audio file and start playing it, wiring up pause/play/ended events
 * so the button stays in sync with both user actions and system interruptions
 * (e.g. Bluetooth disconnects). Throws on load or play errors.
 */
async function loadAndPlay_(filePath: string, id: string): Promise<void> {
  // Pause native full-audio player if it's running so the two don't overlap.
  const fullPlayer = document.getElementById('full-audio-player') as HTMLAudioElement | null;
  if (fullPlayer && !fullPlayer.paused) fullPlayer.pause();

  const b64   = await invoke<string>('read_audio_base64', { filePath });
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const url   = URL.createObjectURL(new Blob([bytes], { type: 'audio/mpeg' }));

  currentAudioUrl_  = url;
  currentAudio_     = new Audio(url);
  currentPlayingId_ = id;

  // Sync button with browser-driven state changes (Bluetooth, OS media controls, etc.)
  currentAudio_.addEventListener('pause', () => {
    if (currentPlayingId_ === id) setPlayButton_(id, 'paused');
  });
  currentAudio_.addEventListener('play', () => {
    if (currentPlayingId_ === id) setPlayButton_(id, 'playing');
  });
  currentAudio_.addEventListener('ended', () => {
    if (currentAudioUrl_ === url) URL.revokeObjectURL(url);
    currentAudioUrl_ = null; currentAudio_ = null; currentPlayingId_ = null;
    setPlayButton_(id, 'idle');
  });

  setPlayButton_(id, 'playing');
  await currentAudio_.play();
}

/**
 * Shared play-button click handler for both section and full-audio buttons.
 * Toggles pause/resume on the current audio; switches to a new audio otherwise.
 */
async function handlePlayClick_(e: Event, filePath: string, id: string): Promise<void> {
  e.stopPropagation();

  if (currentPlayingId_ === id && currentAudio_) {
    // Same audio — toggle pause / resume.
    if (currentAudio_.paused) {
      currentAudio_.play().catch(err => {
        if ((err as DOMException).name !== 'AbortError')
          showErrorOverlay(`Resume failed: ${err}`);
      });
    } else {
      currentAudio_.pause();
    }
    return;
  }

  // Different audio — discard current and load the requested one.
  stopPlayback_();

  try {
    await loadAndPlay_(filePath, id);
  } catch (err) {
    if ((err as DOMException).name !== 'AbortError') {
      if (currentPlayingId_ === id) stopPlayback_();
      showErrorOverlay(`Playback failed: ${String(err)}`);
    }
  }
}

function removeContextMenu_(): void {
  if (contextMenu_) { contextMenu_.remove(); contextMenu_ = null; }
}

function askDuration_(): Promise<number | null> {
  return new Promise(resolve => {
    const backdrop = document.createElement('div');
    backdrop.className = 'error-backdrop';  // reuse the existing overlay style
    backdrop.innerHTML = `
      <div class="error-box" style="max-width:320px;">
        <div class="error-box-header">
          <span>Insert Break</span>
          <button class="btn btn-icon" id="dur-close">&#10005;</button>
        </div>
        <div style="padding:12px 16px;display:flex;align-items:center;gap:10px;">
          <label for="dur-input" style="font-size:13px;white-space:nowrap;">Duration (ms):</label>
          <input id="dur-input" type="number" class="input-number-small" value="1000" min="0" step="100" style="width:90px;">
        </div>
        <div class="error-actions">
          <button class="btn btn-primary" id="dur-ok">Insert</button>
          <button class="btn btn-secondary" id="dur-cancel">Cancel</button>
        </div>
      </div>`;
    document.body.appendChild(backdrop);

    const input = backdrop.querySelector('#dur-input') as HTMLInputElement;
    input.focus();
    input.select();

    const finish = (val: number | null) => {
      document.body.removeChild(backdrop);
      resolve(val);
    };

    backdrop.querySelector('#dur-ok')!.addEventListener('click', () => finish(parseInt(input.value, 10) || 1000));
    backdrop.querySelector('#dur-close')!.addEventListener('click', () => finish(null));
    backdrop.querySelector('#dur-cancel')!.addEventListener('click', () => finish(null));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') finish(parseInt(input.value, 10) || 1000);
      if (e.key === 'Escape') finish(null);
    });
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) finish(null); });
  });
}

function showContextMenu_(
  x: number, y: number, sectionId: string, root: HTMLElement, scroll: HTMLElement,
  master?: MasterManifest, isSpeech?: boolean,
): void {
  removeContextMenu_();
  const menu = document.createElement('div');
  menu.className = 'context-menu';
  menu.style.left = `${x}px`;
  menu.style.top  = `${y}px`;

  const inRetailSample = master?.retailSample?.sectionRefs.some(r => r.sectionId === sectionId) ?? false;
  const currentChapter = master?.chapters[currentChapterIndex_];
  const retailSampleHtml = (isSpeech && master && currentChapter) ? `
    <div class="context-menu-sep"></div>
    <button class="context-menu-item" data-action="retail-sample">
      ${inRetailSample ? '&#10003; Remove from Retail Sample' : '&#9671; Add to Retail Sample'}
    </button>` : '';

  menu.innerHTML = `
    <button class="context-menu-item" data-action="above">Insert Break Above</button>
    <button class="context-menu-item" data-action="below">Insert Break Below</button>
    <div class="context-menu-sep"></div>
    <button class="context-menu-item" data-action="speech-below">Insert Speech Below</button>
    ${retailSampleHtml}
    <div class="context-menu-sep"></div>
    <button class="context-menu-item context-menu-danger" data-action="delete">Delete Section</button>`;
  document.body.appendChild(menu);
  contextMenu_ = menu;

  menu.querySelectorAll<HTMLButtonElement>('[data-action]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      removeContextMenu_();
      const action = btn.dataset.action!;

      if (action === 'retail-sample') {
        if (!master || !currentChapter) return;
        const inSample = master.retailSample?.sectionRefs.some(r => r.sectionId === sectionId) ?? false;
        try {
          if (inSample) {
            await invoke('remove_retail_sample_ref', { sectionId });
          } else {
            await invoke('add_retail_sample_ref', {
              chapterTabName: currentChapter.tabName,
              sectionId,
            });
          }
          // Refresh the card in-place to update retail sample badge
          const freshMaster: MasterManifest | null = await invoke('get_master_manifest');
          if (freshMaster) {
            const ch = freshMaster.chapters[currentChapterIndex_];
            if (ch) {
              const freshBlocked = computeBlockedSections(ch.sections, selectedFormat_());
              const section = ch.sections.find(s => s.id === sectionId);
              if (section) {
                const rowEl = scroll.querySelector(`[data-id="${sectionId}"]`) as HTMLElement | null;
                const oldCard = rowEl?.querySelector('.section-card') as HTMLElement | null;
                if (oldCard) oldCard.replaceWith(buildCard(section, freshBlocked, root, scroll, freshMaster));
              }
            }
          }
        } catch (err) {
          showErrorOverlay(String(err));
        }
        return;
      }

      if (action === 'delete') {
        try {
          await invoke('delete_section', { sectionId });
          const row = scroll.querySelector(`[data-id="${sectionId}"]`) as HTMLElement | null;
          if (row) row.remove(); else renderManifestEditor(root);
        } catch (err) {
          showErrorOverlay(String(err));
        }
        return;
      }

      if (action === 'speech-below') {
        try {
          await invoke('insert_speech', { sectionId, position: 'below' });
          const freshMaster: MasterManifest | null = await invoke('get_master_manifest');
          if (!freshMaster) { renderManifestEditor(root); return; }
          const freshManifest = freshMaster.chapters[currentChapterIndex_];
          if (!freshManifest) { renderManifestEditor(root); return; }
          const anchorRow = scroll.querySelector(`[data-id="${sectionId}"]`) as HTMLElement | null;
          if (!anchorRow) { renderManifestEditor(root); return; }
          const freshIdx = freshManifest.sections.findIndex(s => s.id === sectionId);
          const newSectionIdx = freshIdx + 1;
          if (newSectionIdx >= freshManifest.sections.length) { renderManifestEditor(root); return; }
          const newSection = freshManifest.sections[newSectionIdx];

          // Apply default voice to the new section if it has no voice yet.
          if (newSection.type === 'speech' && !newSection.voiceId) {
            const voices = await getVoices_();
            const defaultVoice = lastChosenVoice_ ?? (voices.length > 0 ? { voiceId: voices[0].voiceId, voiceName: voices[0].name } : null);
            if (defaultVoice) {
              await invoke('update_section', {
                sectionId: newSection.id,
                newVoiceId: defaultVoice.voiceId,
                newVoiceName: defaultVoice.voiceName,
              });
              newSection.voiceId   = defaultVoice.voiceId;
              newSection.voiceName = defaultVoice.voiceName;
            }
          }

          const freshBlocked = computeBlockedSections(freshManifest.sections, selectedFormat_());
          anchorRow.after(buildRow(newSection, freshBlocked, root, scroll, freshMaster));
        } catch (err) {
          showErrorOverlay(String(err));
        }
        return;
      }

      // Silence insert (above / below) — prompt for duration first
      const position = action;
      const durationMs = await askDuration_();
      if (durationMs === null) return;   // user cancelled
      try {
        await invoke('insert_silence', { sectionId, position, durationMs });

        // In-place insert: fetch updated manifest, build only the new row, splice into DOM.
        const freshMaster: MasterManifest | null = await invoke('get_master_manifest');
        if (!freshMaster) { renderManifestEditor(root); return; }
        const freshManifest = freshMaster.chapters[currentChapterIndex_];
        if (!freshManifest) { renderManifestEditor(root); return; }

        const anchorRow = scroll.querySelector(`[data-id="${sectionId}"]`) as HTMLElement | null;
        if (!anchorRow) { renderManifestEditor(root); return; }

        // After the insert the new silence is immediately above or below the anchor section.
        const freshIdx = freshManifest.sections.findIndex(s => s.id === sectionId);
        const newSectionIdx = position === 'above' ? freshIdx - 1 : freshIdx + 1;
        if (newSectionIdx < 0 || newSectionIdx >= freshManifest.sections.length) {
          renderManifestEditor(root); return;
        }
        const freshBlocked = computeBlockedSections(freshManifest.sections, selectedFormat_());
        const newRow = buildRow(freshManifest.sections[newSectionIdx], freshBlocked, root, scroll, freshMaster);

        if (position === 'above') {
          scroll.insertBefore(newRow, anchorRow);
        } else {
          anchorRow.after(newRow);
        }
      } catch (err) {
        showErrorOverlay(String(err));
      }
    });
  });

  const closeHandler = (e: MouseEvent) => {
    if (contextMenu_ && !contextMenu_.contains(e.target as Node)) removeContextMenu_();
  };
  document.addEventListener('mousedown', closeHandler, { once: true });
}

/** Update a play button's icon/style in-place. Three states: idle ▶ · playing ⏸ · paused ▶ */
function setPlayButton_(id: string, state: 'idle' | 'playing' | 'paused'): void {
  const btn = document.getElementById(`play-${id}`) as HTMLButtonElement | null;
  if (!btn) return;
  if (state === 'playing') {
    btn.className = 'btn-play playing';
    btn.title     = 'Pause';
    btn.innerHTML = '&#9646;&#9646;';   // ▮▮
  } else if (state === 'paused') {
    btn.className = 'btn-play paused';
    btn.title     = 'Resume';
    btn.innerHTML = '&#9654;';          // ▶
  } else {
    btn.className = 'btn-play';
    btn.title     = 'Play audio';
    btn.innerHTML = '&#9654;';          // ▶
  }
}

// ---------------------------------------------------------------------------
// Voice-stitching / section status
// ---------------------------------------------------------------------------

function selectedFormat_(): string {
  return FORMAT_FOR_QUALITY[selectedQuality_];
}

function sectionStatus_(s: SpeechSection, blocked: Set<string>): SectionStatus {
  return computeSectionStatus(s, blocked, sectionErrors_, selectedFormat_());
}

// ---------------------------------------------------------------------------
// Error overlay
// ---------------------------------------------------------------------------

export function showErrorOverlay(message: string): void {
  const backdrop = document.createElement('div');
  backdrop.className = 'error-backdrop';
  backdrop.innerHTML = `
    <div class="error-box">
      <div class="error-box-header">
        <span>Error</span>
        <button class="btn btn-icon" id="err-close">&#10005;</button>
      </div>
      <textarea class="error-textarea" readonly>${escHtml(message)}</textarea>
      <div class="error-actions">
        <button class="btn btn-secondary" id="err-copy">Copy</button>
        <button class="btn btn-secondary" id="err-close2">Close</button>
      </div>
    </div>`;
  document.body.appendChild(backdrop);
  const close = () => document.body.removeChild(backdrop);
  backdrop.querySelector('#err-close')!.addEventListener('click', close);
  backdrop.querySelector('#err-close2')!.addEventListener('click', close);
  backdrop.querySelector('#err-copy')!.addEventListener('click', () => {
    navigator.clipboard.writeText(message).catch(() => {});
    (backdrop.querySelector('#err-copy') as HTMLButtonElement).textContent = 'Copied!';
  });
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
}

// ---------------------------------------------------------------------------
// Dict overlay
// ---------------------------------------------------------------------------

function showDictOverlay_(info: DictInfo): void {
  const rulesHtml = info.rules.length === 0
    ? '<p class="dict-no-rules">(no rules)</p>'
    : `<table class="dict-rules-table">
        <thead>
          <tr>
            <th>#</th>
            <th>Replace</th>
            <th>With</th>
            <th>Alphabet</th>
          </tr>
        </thead>
        <tbody>
          ${info.rules.map((r, i) => `
            <tr>
              <td>${i + 1}</td>
              <td>${escHtml(r.stringToReplace)}</td>
              <td>${escHtml(r.replaceWith)}</td>
              <td>${escHtml(r.alphabet)}</td>
            </tr>`).join('')}
        </tbody>
      </table>`;

  const backdrop = document.createElement('div');
  backdrop.className = 'error-backdrop';
  backdrop.innerHTML = `
    <div class="error-box">
      <div class="error-box-header">
        <span>Pronunciation Dictionary</span>
        <button class="btn btn-icon" id="dict-close">&#10005;</button>
      </div>
      <div class="dict-overlay-body">
        <p class="dict-overlay-name"><strong>${escHtml(info.name)}</strong></p>
        <p class="dict-overlay-id"><code>${escHtml(info.id)}</code></p>
        ${rulesHtml}
      </div>
    </div>`;
  document.body.appendChild(backdrop);
  const close = () => document.body.removeChild(backdrop);
  backdrop.querySelector('#dict-close')!.addEventListener('click', close);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
}

// ---------------------------------------------------------------------------
// Root render
// ---------------------------------------------------------------------------

export async function renderManifestEditor(root: HTMLElement): Promise<void> {
  // Preserve scroll position so actions (insert break, generate, voice change, etc.)
  // don't jump back to the top of the list.
  const prevScrollTop = root.querySelector<HTMLElement>('.sections-scroll')?.scrollTop ?? 0;

  // Always clear first — prevents the double-panel bug when called from event handlers.
  // The find bar is part of root's DOM so it's destroyed here; clear the reference.
  if (activeFindClose_) { activeFindClose_(); activeFindClose_ = null; }
  root.innerHTML = '';
  ensureFindBarListener_(root);

  const master: MasterManifest | null = await invoke('get_master_manifest');

  if (!master || (master.chapters.length === 0 && !master.openingCredits && !master.closingCredits && !master.aboutAuthor)) {
    root.innerHTML = `
      <div class="empty-state">
        <p>No manifest loaded.</p>
        <p class="hint">Use <strong>File &#8594; Manifest from Clipboard</strong> or
        <strong>Open Manifest File</strong>, or send one from the EditorLLM add-on.<br>
        Listening on <code>http://127.0.0.1:3847</code>.</p>
      </div>`;
    return;
  }

  // Clamp chapter index to valid range
  if (currentChapterIndex_ >= master.chapters.length && master.chapters.length > 0) {
    currentChapterIndex_ = 0;
  }

  const manifest: AudioManifest | undefined = master.chapters[currentChapterIndex_];

  // Auto-select quality when a new manifest is loaded (not on in-page re-renders).
  const manifestId = `${master.documentTitle}|${master.generatedAt}`;
  if (manifestId !== lastManifestId_) {
    lastManifestId_ = manifestId;
    if (manifest) autoSelectQuality_(manifest);
  }

  const format = selectedFormat_();

  // Fixed toolbar
  const toolbar = document.createElement('div');
  toolbar.className = 'toolbar';
  toolbar.innerHTML = `
    <div class="toolbar-left">
      <span class="manifest-title">${escHtml(master.documentTitle)}</span>
      ${master.chapters.length > 1 ? `
        <select id="sel-chapter" class="chapter-select" title="Active chapter">
          ${master.chapters.map((ch, i) =>
            `<option value="${i}"${i === currentChapterIndex_ ? ' selected' : ''}>${escHtml(ch.tabName)}</option>`
          ).join('')}
        </select>` : (manifest ? `<span class="tab-badge">${escHtml(manifest.tabName)}</span>` : '')}
    </div>
    <div class="toolbar-right">
      <span id="credits-display" class="credits-display" title="ElevenLabs character credits remaining"></span>
      <button id="btn-clear-lower" class="btn btn-secondary btn-clear-lower" title="Clear audio for sections below selected quality">Clear Lower Quality</button>
      <button id="btn-generate-all" class="btn btn-primary">Generate All Remaining</button>
      <select id="sel-quality" class="quality-select" title="Audio quality">
        <option value="vlow"${selectedQuality_ === 'vlow' ? ' selected' : ''}>V.Low (22k/32k)</option>
        <option value="low"${selectedQuality_ === 'low' ? ' selected' : ''}>Low (44k/64k)</option>
        <option value="med"${selectedQuality_ === 'med' ? ' selected' : ''}>Std (44k/128k)</option>
        <option value="high"${selectedQuality_ === 'high' ? ' selected' : ''}>High (44k/192k)</option>
      </select>
      <button id="btn-settings" class="btn btn-icon" title="Settings">&#9881;</button>
    </div>`;
  root.appendChild(toolbar);

  // Chapter selector handler
  const chapterSel = toolbar.querySelector('#sel-chapter') as HTMLSelectElement | null;
  if (chapterSel) {
    chapterSel.addEventListener('change', () => {
      currentChapterIndex_ = parseInt(chapterSel.value, 10);
      renderManifestEditor(root);
    });
  }

  // Fetch ElevenLabs credits non-blocking — populate the span when the response arrives.
  invoke<{character_count: number; character_limit: number}>('get_subscription')
    .then(sub => {
      const el = toolbar.querySelector('#credits-display') as HTMLElement | null;
      if (el) {
        const remaining = sub.character_limit - sub.character_count;
        el.textContent = `${remaining.toLocaleString()} chars`;
        el.title = `${remaining.toLocaleString()} of ${sub.character_limit.toLocaleString()} chars remaining`;
      }
    })
    .catch(() => { /* non-fatal — API key may not be set yet */ });

  // Panel switcher tabs (Chapter Audio | ACX Package)
  const panelTabs = document.createElement('div');
  panelTabs.className = 'panel-tabs';
  panelTabs.innerHTML = `
    <button class="panel-tab${currentPanel_ === 'chapters' ? ' active' : ''}" data-panel="chapters">Chapter Audio</button>
    <button class="panel-tab${currentPanel_ === 'acx' ? ' active' : ''}" data-panel="acx">ACX Package</button>
    <button class="panel-tab${currentPanel_ === 'raw' ? ' active' : ''}" data-panel="raw">Raw Edits</button>`;
  root.appendChild(panelTabs);

  panelTabs.querySelectorAll<HTMLButtonElement>('.panel-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      if (activeFindClose_) { activeFindClose_(); }
      currentPanel_ = btn.dataset.panel as ManifestPanel;
      renderManifestEditor(root);
    });
  });

  // ACX panel — renders independently of the chapter panel
  if (currentPanel_ === 'acx') {
    renderAcxPanel_(master, root);
    // Textareas in special-section cards are built off-DOM; resize them once layout is stable.
    requestAnimationFrame(() => root.querySelectorAll<HTMLTextAreaElement>('textarea').forEach(autoResize_));
    const genAllBtn = toolbar.querySelector('#btn-generate-all') as HTMLButtonElement | null;
    const clearBtn  = toolbar.querySelector('#btn-clear-lower') as HTMLButtonElement | null;
    const qualSel   = toolbar.querySelector('#sel-quality') as HTMLSelectElement | null;
    if (genAllBtn) genAllBtn.style.display = 'none';
    if (clearBtn)  clearBtn.style.display  = 'none';
    if (qualSel)   qualSel.style.display   = 'none';
    toolbar.querySelector('#btn-settings')!.addEventListener('click', () => navigate('settings'));
    return;
  }

  // Raw Edits panel — full-height JSON editor
  if (currentPanel_ === 'raw') {
    renderRawEditPanel_(master, root);
    const genAllBtn = toolbar.querySelector('#btn-generate-all') as HTMLButtonElement | null;
    const clearBtn  = toolbar.querySelector('#btn-clear-lower') as HTMLButtonElement | null;
    const qualSel   = toolbar.querySelector('#sel-quality') as HTMLSelectElement | null;
    if (genAllBtn) genAllBtn.style.display = 'none';
    if (clearBtn)  clearBtn.style.display  = 'none';
    if (qualSel)   qualSel.style.display   = 'none';
    toolbar.querySelector('#btn-settings')!.addEventListener('click', () => navigate('settings'));
    return;
  }

  // No chapters case — bail early after showing panels
  if (!manifest) {
    root.insertAdjacentHTML('beforeend', '<div class="empty-state"><p>No chapters imported yet.</p></div>');
    toolbar.querySelector('#btn-settings')!.addEventListener('click', () => navigate('settings'));
    return;
  }

  const blocked = computeBlockedSections(manifest.sections, format);

  // Dict info row — always shown; "(None)" when no dictionary is currently selected.
  {
    const currentLocator = manifest.pronunciationDictionaryLocators?.[0] ?? null;
    const initVersionLabel = currentLocator ? `${currentLocator.versionId.slice(0, 8)}…` : '';
    const dictRow = document.createElement('div');
    dictRow.className = 'info-bar';
    dictRow.innerHTML = `
      <div class="info-bar-content">
        <span class="info-bar-label">Dictionary</span>
        <select id="dict-select" class="dict-select" title="Pronunciation dictionary">
          <option value="">(None)</option>
          ${currentLocator ? `<option value="${escHtml(currentLocator.pronunciationDictionaryId)}" selected>${escHtml(currentLocator.pronunciationDictionaryId)}</option>` : ''}
        </select>
        <span id="dict-version-display" class="info-bar-version"
          title="${currentLocator ? `Version ID: ${escHtml(currentLocator.versionId)}` : ''}"
          style="${currentLocator ? '' : 'display:none'}">${initVersionLabel}</span>
        <button class="btn btn-secondary info-bar-btn" id="btn-dict-details"
          style="${currentLocator ? '' : 'display:none'}">Details</button>
      </div>
      <div class="info-bar-actions">
        <span class="info-bar-sep"></span>
        <button class="btn btn-secondary info-bar-btn" id="btn-add-silences" title="Insert 1-second silences between consecutive speech sections">Add Silences</button>
      </div>`;
    root.appendChild(dictRow);

    const dictSelect     = dictRow.querySelector('#dict-select') as HTMLSelectElement;
    const versionDisplay = dictRow.querySelector('#dict-version-display') as HTMLElement;
    const detailsBtn     = dictRow.querySelector('#btn-dict-details') as HTMLButtonElement;

    let activeDictId = currentLocator?.pronunciationDictionaryId ?? '';

    const applyLatestVersion_ = (latestVersionId: string): void => {
      versionDisplay.title       = `Version ID: ${latestVersionId} (latest)`;
      versionDisplay.textContent = `v: ${latestVersionId.slice(0, 8)}… ✓`;
    };

    const showDictMeta_ = (show: boolean): void => {
      versionDisplay.style.display = show ? '' : 'none';
      detailsBtn.style.display     = show ? '' : 'none';
    };

    // Fetch all dictionaries from ElevenLabs and populate the selector.
    invoke<DictionaryInfoItem[]>('list_dictionaries')
      .then(dicts => {
        const noneOpt = dictSelect.querySelector<HTMLOptionElement>('option[value=""]')!;
        dictSelect.innerHTML = '';
        dictSelect.appendChild(noneOpt);
        for (const d of dicts) {
          const opt = document.createElement('option');
          opt.value = d.id;
          opt.textContent = d.name;
          if (d.id === activeDictId) opt.selected = true;
          dictSelect.appendChild(opt);
        }
        // If the active dict is not in the list, keep a fallback seed option.
        if (activeDictId && !dicts.find(d => d.id === activeDictId)) {
          const fallback = document.createElement('option');
          fallback.value = activeDictId;
          fallback.textContent = activeDictId;
          fallback.selected = true;
          noneOpt.insertAdjacentElement('afterend', fallback);
        }

        dictSelect.addEventListener('change', async () => {
          const selectedId = dictSelect.value;
          if (!selectedId) {
            try {
              await invoke('set_dictionary', { dictionaryId: '', versionId: '' });
              activeDictId = '';
              versionDisplay.textContent = '';
              showDictMeta_(false);
            } catch (e) { showErrorOverlay(String(e)); }
            return;
          }
          const selectedDict = dicts.find(d => d.id === selectedId);
          if (!selectedDict) return;
          try {
            await invoke('set_dictionary', { dictionaryId: selectedId, versionId: selectedDict.latestVersionId });
            activeDictId = selectedId;
            applyLatestVersion_(selectedDict.latestVersionId);
            showDictMeta_(true);
          } catch (e) { showErrorOverlay(String(e)); }
        });
      })
      .catch(() => { /* API unavailable — dropdown limited to current selection */ });

    // Resolve the active dictionary to its latest version on load.
    if (currentLocator) {
      invoke<string>('resolve_dictionary_to_latest', { dictionaryId: currentLocator.pronunciationDictionaryId })
        .then(applyLatestVersion_)
        .catch(() => { /* keep showing stored version on error */ });
    }

    // Details button — show rules overlay for the active dict.
    detailsBtn.addEventListener('click', async () => {
      if (!activeDictId) return;
      const cached = dictInfoCache_.get(activeDictId);
      if (cached) { showDictOverlay_(cached); return; }
      try {
        const info: DictInfo = await invoke('get_dictionary_info', { dictionaryId: activeDictId });
        dictInfoCache_.set(info.id, info);
        showDictOverlay_(info);
      } catch (e) { showErrorOverlay(String(e)); }
    });

    // Add Silences — inserts 1-second silences between consecutive speech sections.
    dictRow.querySelector('#btn-add-silences')!.addEventListener('click', async () => {
      try {
        const added = await invoke<number>('add_silences_between_speech', {
          durationMs: 1000,
          chapterTabName: manifest.tabName,
        });
        if (added > 0) renderManifestEditor(root);
        else setStatusMessage('No consecutive speech sections found — nothing to add.');
      } catch (e) { showErrorOverlay(String(e)); }
    });
  }

  // Full audio file row — shown when the selected quality has a stitched file.
  const fullAudioPath = manifest.audioFiles?.[format];
  if (fullAudioPath) {
    const audioRow = document.createElement('div');
    audioRow.className = 'info-bar';
    const basename = fullAudioPath.split('/').pop() ?? fullAudioPath;
    audioRow.innerHTML = `
      <div class="info-bar-content">
        <span class="info-bar-label">Full Audio</span>
        <span class="info-bar-value" title="${escHtml(fullAudioPath)}">${escHtml(basename)}</span>
        <audio id="full-audio-player" controls class="full-audio-player"></audio>
      </div>
      <button class="btn btn-secondary info-bar-btn" id="btn-full-audio-finder">File</button>`;
    root.appendChild(audioRow);

    audioRow.querySelector('#btn-full-audio-finder')!.addEventListener('click', () => {
      invoke('reveal_in_finder', { filePath: fullAudioPath })
        .catch((e) => showErrorOverlay(`Finder error: ${e}`));
    });

    const audioEl = audioRow.querySelector('#full-audio-player') as HTMLAudioElement;

    // Stop any section audio when native player starts.
    audioEl.addEventListener('play', () => stopPlayback_());

    // Revoke previous blob URL; load new one.
    if (fullAudioBlobUrl_) { URL.revokeObjectURL(fullAudioBlobUrl_); fullAudioBlobUrl_ = null; }
    invoke<string>('read_audio_base64', { filePath: fullAudioPath })
      .then(b64 => {
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        fullAudioBlobUrl_ = URL.createObjectURL(new Blob([bytes], { type: 'audio/mpeg' }));
        audioEl.src = fullAudioBlobUrl_;
      })
      .catch(err => invoke('log_frontend_error', { context: 'full-audio-load', message: String(err) }));

    audioEl.addEventListener('ended', () => {
      if (fullAudioBlobUrl_) { URL.revokeObjectURL(fullAudioBlobUrl_); fullAudioBlobUrl_ = null; }
    });
  }

  // Single scrollable container — cards and text are in the same row so they
  // scroll together and stay aligned.
  const scroll = document.createElement('div');
  scroll.className = 'sections-scroll';
  root.appendChild(scroll);

  for (const section of manifest.sections) {
    const row = buildRow(section, blocked, root, scroll, master);
    scroll.appendChild(row);
  }

  // Register find-bar targets for chapter audio (all text textareas in scroll).
  findBarTargets_ = () => Array.from(scroll.querySelectorAll<HTMLTextAreaElement>('textarea'));

  // Restore scroll position and auto-resize textareas after layout.
  requestAnimationFrame(() => {
    if (prevScrollTop > 0) scroll.scrollTop = prevScrollTop;
    scroll.querySelectorAll<HTMLTextAreaElement>('textarea').forEach(autoResize_);
  });

  // Toolbar handlers
  toolbar.querySelector('#btn-settings')!
    .addEventListener('click', () => navigate('settings'));

  (toolbar.querySelector('#sel-quality') as HTMLSelectElement)
    .addEventListener('change', (e) => {
      selectedQuality_ = (e.target as HTMLSelectElement).value as AudioQuality;
      renderManifestEditor(root);
    });

  toolbar.querySelector('#btn-generate-all')!.addEventListener('click', async () => {
    const btn = toolbar.querySelector('#btn-generate-all') as HTMLButtonElement;
    btn.disabled = true;
    btn.textContent = 'Generating…';
    let err = '';
    try {
      await invoke('generate_all_remaining', {
        quality: selectedQuality_,
        chapterTabName: manifest.tabName,
      });
    }
    catch (e) { err = String(e); }
    renderManifestEditor(root);
    setStatusMessage(err ? `Error: ${err}` : '');
  });

  toolbar.querySelector('#btn-clear-lower')!.addEventListener('click', async () => {
    try {
      const count = await invoke<number>('clear_lower_quality', { quality: selectedQuality_ });
      if (count > 0) renderManifestEditor(root);
    } catch (e) {
      showErrorOverlay(String(e));
    }
  });

}

// ---------------------------------------------------------------------------
// ACX panel
// ---------------------------------------------------------------------------

/**
 * Renders a special-section group (Opening Credits, Closing Credits, About Author).
 *
 * When `am` is present, renders individual `buildCard` section cards — one per
 * section — exactly like chapter sections.  When absent, shows a "Not imported"
 * placeholder.
 */
function renderSpecialSectionCard_(
  title: string,
  am: AudioManifest | undefined,
  root: HTMLElement,
  master: MasterManifest,
): HTMLElement {
  const group = document.createElement('div');
  group.className = 'acx-special-group';

  const quality = acxQuality_;
  const allDone = am ? am.sections.every(s =>
    s.type !== 'speech' || (s.audioFiles?.[quality] && !s.isDirty)
  ) : false;
  const speechCount = am ? am.sections.filter(s => s.type === 'speech').length : 0;
  const doneCount = am ? am.sections.filter(s =>
    s.type === 'speech' && s.audioFiles?.[quality] && !s.isDirty
  ).length : 0;

  const header = document.createElement('div');
  header.className = 'acx-special-header';
  header.innerHTML = `
    <span class="acx-card-title">${escHtml(title)}</span>
    ${am ? `<span class="badge ${allDone ? 'badge-done' : 'badge-pending'}">${allDone ? '&#10003; Ready' : `${doneCount}/${speechCount}`}</span>` : ''}`;
  group.appendChild(header);

  if (!am) {
    const missing = document.createElement('div');
    missing.className = 'acx-card-body acx-card-missing';
    missing.textContent = 'Not imported. Export from the GAS add-on.';
    group.appendChild(missing);
    return group;
  }

  const blocked = computeBlockedSections(am.sections, selectedFormat_());
  for (const section of am.sections) {
    group.appendChild(buildRow(section, blocked, root, group, master));
  }

  return group;
}

// ---------------------------------------------------------------------------
// Find bar — Ctrl+F / Cmd+F search across panel text fields
// ---------------------------------------------------------------------------

interface FindMatch_ { el: HTMLInputElement | HTMLTextAreaElement; start: number; end: number; }

function mountFindBar_(
  containerRoot: HTMLElement,
  getTargets: () => Array<HTMLInputElement | HTMLTextAreaElement>,
): () => void {
  const bar = document.createElement('div');
  bar.className = 'find-bar';
  bar.innerHTML = `
    <input class="find-input" placeholder="Find…" type="text" autocomplete="off" />
    <span class="find-count"></span>
    <button class="find-btn" id="find-prev" title="Previous (Shift+Enter)">▲</button>
    <button class="find-btn" id="find-next" title="Next (Enter)">▼</button>
    <button class="find-btn find-close" title="Close (Esc)">×</button>`;
  containerRoot.appendChild(bar);

  const input   = bar.querySelector<HTMLInputElement>('.find-input')!;
  const countEl = bar.querySelector<HTMLElement>('.find-count')!;

  let matches: FindMatch_[] = [];
  let current = -1;

  const buildMatches_ = (query: string): void => {
    matches = [];
    if (!query) return;
    const q = query.toLowerCase();
    for (const el of getTargets()) {
      const text = el.value.toLowerCase();
      let pos = 0;
      while ((pos = text.indexOf(q, pos)) !== -1) {
        matches.push({ el, start: pos, end: pos + q.length });
        pos++;
      }
    }
  };

  const goTo_ = (idx: number): void => {
    if (!matches.length) return;
    current = ((idx % matches.length) + matches.length) % matches.length;
    const m = matches[current];
    m.el.focus();
    m.el.setSelectionRange(m.start, m.end);
    m.el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    countEl.textContent = `${current + 1}/${matches.length}`;
  };

  const search_ = (): void => {
    buildMatches_(input.value);
    countEl.textContent = matches.length ? `1/${matches.length}` : (input.value ? '0/0' : '');
    current = -1;
    if (matches.length) goTo_(0);
  };

  const close_ = (): void => { bar.remove(); activeFindClose_ = null; };

  input.addEventListener('input', search_);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter')  { e.preventDefault(); goTo_(e.shiftKey ? current - 1 : current + 1); }
    if (e.key === 'Escape') { e.preventDefault(); close_(); }
  });
  bar.querySelector('#find-prev')!.addEventListener('click', () => goTo_(current - 1));
  bar.querySelector('#find-next')!.addEventListener('click', () => goTo_(current + 1));
  bar.querySelector('.find-close')!.addEventListener('click', close_);

  requestAnimationFrame(() => input.focus());
  return close_;
}

function ensureFindBarListener_(root: HTMLElement): void {
  if (findBarListenerAttached_) return;
  findBarListenerAttached_ = true;
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'f' && findBarTargets_) {
      e.preventDefault();
      if (activeFindClose_) { activeFindClose_(); return; }
      activeFindClose_ = mountFindBar_(root, findBarTargets_);
    }
  });
}

function renderRawEditPanel_(master: MasterManifest, root: HTMLElement): void {
  const panel = document.createElement('div');
  panel.className = 'raw-edit-panel';

  const editorToolbar = document.createElement('div');
  editorToolbar.className = 'raw-edit-toolbar';
  editorToolbar.innerHTML = `
    <button class="btn btn-secondary" id="raw-btn-validate">Validate</button>
    <button class="btn btn-primary" id="raw-btn-save">Save</button>
    <span class="raw-edit-hint">Cmd+Z = undo · Cmd+Y = redo</span>`;
  panel.appendChild(editorToolbar);

  const statusEl = document.createElement('div');
  statusEl.className = 'raw-edit-status';
  panel.appendChild(statusEl);

  const editorWrapper = document.createElement('div');
  editorWrapper.className = 'raw-editor-wrapper';

  const highlight = document.createElement('pre');
  highlight.className = 'raw-editor-highlight';
  highlight.setAttribute('aria-hidden', 'true');
  editorWrapper.appendChild(highlight);

  const ta = document.createElement('textarea');
  ta.className = 'raw-editor-textarea';
  ta.spellcheck = false;
  ta.value = JSON.stringify(master, null, 2);
  editorWrapper.appendChild(ta);
  panel.appendChild(editorWrapper);

  // Register find-bar targets for raw edits panel.
  findBarTargets_ = () => [ta];

  const syncHighlight_ = (): void => {
    highlight.innerHTML = highlightJson_(ta.value);
  };
  syncHighlight_();

  ta.addEventListener('scroll', () => {
    highlight.scrollTop  = ta.scrollTop;
    highlight.scrollLeft = ta.scrollLeft;
  });

  let undoStack: string[] = [];
  let redoStack: string[] = [];
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;

  const pushUndo_ = (val: string): void => {
    if (undoStack[undoStack.length - 1] !== val) {
      undoStack.push(val);
      if (undoStack.length > 200) undoStack.shift();
    }
    redoStack = [];
  };

  ta.addEventListener('input', () => {
    syncHighlight_();
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => pushUndo_(ta.value), 500);
  });

  ta.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.metaKey && !e.shiftKey && e.key === 'z') {
      e.preventDefault();
      if (undoStack.length > 0) {
        redoStack.push(ta.value);
        ta.value = undoStack.pop()!;
        syncHighlight_();
      }
    } else if (e.metaKey && e.key === 'y') {
      e.preventDefault();
      if (redoStack.length > 0) {
        undoStack.push(ta.value);
        ta.value = redoStack.pop()!;
        syncHighlight_();
      }
    }
  });

  const showStatus_ = (msgs: string[], type: 'ok' | 'error' | 'warn'): void => {
    statusEl.className = `raw-edit-status raw-edit-status--${type}`;
    statusEl.innerHTML = msgs.map(m => `<div>${escHtml(m)}</div>`).join('');
  };

  const parseJson_ = (): MasterManifest | null => {
    try {
      return JSON.parse(ta.value) as MasterManifest;
    } catch (e) {
      showStatus_([`JSON parse error: ${e}`], 'error');
      return null;
    }
  };

  const extractPaths_ = (m: MasterManifest): string[] => {
    const paths: string[] = [];
    const collect = (am: AudioManifest | undefined | null): void => {
      if (!am) return;
      for (const s of am.sections) {
        if (s.type === 'speech' && s.audioFiles) paths.push(...Object.values(s.audioFiles));
        else if (s.type === 'silence' && s.audioFilePath) paths.push(s.audioFilePath);
      }
      if (am.audioFiles) paths.push(...Object.values(am.audioFiles));
    };
    for (const ch of m.chapters) collect(ch);
    collect(m.openingCredits);
    collect(m.closingCredits);
    collect(m.aboutAuthor);
    return paths.filter(Boolean);
  };

  const checkPaths_ = async (m: MasterManifest): Promise<string[]> => {
    const paths = extractPaths_(m);
    if (paths.length === 0) return [];
    const results: { path: string; exists: boolean; size: number }[] =
      await invoke('check_file_paths', { paths });
    const missing = results.filter(r => !r.exists).map(r => `Missing: ${r.path}`);
    const empty   = results.filter(r => r.exists && r.size === 0).map(r => `Empty file: ${r.path}`);
    return [...missing, ...empty];
  };

  editorToolbar.querySelector('#raw-btn-validate')!.addEventListener('click', async () => {
    const m = parseJson_();
    if (!m) return;
    try {
      const problems = await checkPaths_(m);
      if (problems.length === 0) {
        const paths = extractPaths_(m);
        showStatus_([`Valid JSON. ${paths.length} audio file(s) all OK.`], 'ok');
      } else {
        showStatus_(problems, 'warn');
      }
    } catch (e) {
      showStatus_([`Path check failed: ${e}`], 'error');
    }
  });

  editorToolbar.querySelector('#raw-btn-save')!.addEventListener('click', async () => {
    const m = parseJson_();
    if (!m) return;

    let warnMsgs: string[] = [];
    try { warnMsgs = await checkPaths_(m); } catch { /* non-fatal */ }

    try {
      await invoke('set_master_manifest', { manifest: m });
      const filePath: string | null = await invoke('get_last_file');
      if (filePath) await invoke('save_manifest_to_file', { filePath });
      if (warnMsgs.length > 0) {
        showStatus_(['Saved with warnings:', ...warnMsgs], 'warn');
      } else {
        showStatus_(['Saved successfully.'], 'ok');
      }
      renderManifestEditor(root);
    } catch (e) {
      showStatus_([`Save failed: ${e}`], 'error');
    }
  });

  root.appendChild(panel);
}

function renderAcxPanel_(master: MasterManifest, root: HTMLElement): void {
  const panel = document.createElement('div');
  panel.className = 'acx-panel';

  // Book metadata form
  const meta = master.metadata;
  const metaForm = document.createElement('div');
  metaForm.className = 'acx-metadata-form';
  metaForm.innerHTML = `
    <div class="acx-metadata-grid">
      <label class="acx-meta-label" for="acx-meta-title">Title <span class="required">*</span></label>
      <input id="acx-meta-title" class="acx-meta-input" type="text" placeholder="Book title" value="${escHtml(meta?.title ?? '')}" />
      <label class="acx-meta-label" for="acx-meta-author">Author <span class="required">*</span></label>
      <input id="acx-meta-author" class="acx-meta-input" type="text" placeholder="Author name" value="${escHtml(meta?.author ?? '')}" />
      <label class="acx-meta-label" for="acx-meta-narrator">Narrator <span class="required">*</span></label>
      <input id="acx-meta-narrator" class="acx-meta-input" type="text" placeholder="Narrator name" value="${escHtml(meta?.narrator ?? '')}" />
      <label class="acx-meta-label" for="acx-meta-subtitle">Subtitle</label>
      <input id="acx-meta-subtitle" class="acx-meta-input" type="text" placeholder="(optional)" value="${escHtml(meta?.subtitle ?? '')}" />
      <label class="acx-meta-label" for="acx-meta-publisher">Publisher</label>
      <input id="acx-meta-publisher" class="acx-meta-input" type="text" placeholder="(optional)" value="${escHtml(meta?.publisher ?? '')}" />
      <label class="acx-meta-label" for="acx-meta-year">Copyright Year</label>
      <input id="acx-meta-year" class="acx-meta-input" type="number" placeholder="e.g. 2025" min="1900" max="2100" value="${meta?.copyrightYear ?? ''}" />
      <label class="acx-meta-label" for="acx-meta-language">Language</label>
      <input id="acx-meta-language" class="acx-meta-input" type="text" placeholder="en" value="${escHtml(meta?.language ?? 'en')}" />
      <label class="acx-meta-label" for="acx-meta-asin">ASIN</label>
      <input id="acx-meta-asin" class="acx-meta-input" type="text" placeholder="(optional)" value="${escHtml(meta?.asin ?? '')}" />
    </div>`;
  panel.appendChild(metaForm);

  // Register find-bar targets for ACX panel (metadata text inputs).
  findBarTargets_ = () => Array.from(metaForm.querySelectorAll<HTMLInputElement>('.acx-meta-input[type="text"]'));

  const readMetaForm = (): BookMetadata => {
    const g = (id: string) => (metaForm.querySelector(`#${id}`) as HTMLInputElement).value.trim();
    const year = parseInt(g('acx-meta-year'), 10);
    return {
      title:           g('acx-meta-title'),
      author:          g('acx-meta-author'),
      narrator:        g('acx-meta-narrator'),
      subtitle:        g('acx-meta-subtitle') || undefined,
      publisher:       g('acx-meta-publisher') || undefined,
      copyrightYear:   isNaN(year) ? undefined : year,
      language:        g('acx-meta-language') || 'en',
      asin:            g('acx-meta-asin') || undefined,
    };
  };

  const updateGenerateBtn = () => {
    const m = readMetaForm();
    const btn = controlsRow.querySelector('#acx-generate-btn') as HTMLButtonElement | null;
    if (btn) {
      const missing = !m.title || !m.author || !m.narrator;
      btn.disabled = missing;
      btn.title = missing ? 'Fill in Title, Author, and Narrator first' : '';
    }
  };

  const saveMetaDebounced = (() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    return () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const m = readMetaForm();
        invoke('set_book_metadata', { metadata: m }).catch((e: unknown) => {
          logError('acx-meta', String(e));
        });
        updateGenerateBtn();
      }, 400);
    };
  })();

  metaForm.querySelectorAll<HTMLInputElement>('.acx-meta-input').forEach(inp => {
    inp.addEventListener('input', saveMetaDebounced);
  });

  // Controls row
  const controlsRow = document.createElement('div');
  controlsRow.className = 'acx-controls-row';
  controlsRow.innerHTML = `
    <select id="acx-quality-sel" class="quality-select" title="ACX audio quality">
      <option value="mp3_44100_128"${acxQuality_ === 'mp3_44100_128' ? ' selected' : ''}>Standard (128kbps)</option>
      <option value="mp3_44100_192"${acxQuality_ === 'mp3_44100_192' ? ' selected' : ''}>High (192kbps)</option>
    </select>
    <button class="btn btn-secondary" id="acx-audit-btn">Structural Audit</button>
    <button class="btn btn-primary" id="acx-generate-btn" title="">Generate ACX Package</button>`;
  panel.appendChild(controlsRow);

  (controlsRow.querySelector('#acx-quality-sel') as HTMLSelectElement).addEventListener('change', (e) => {
    acxQuality_ = (e.target as HTMLSelectElement).value;
    renderManifestEditor(root);
  });

  // Apply initial disabled state based on current metadata
  updateGenerateBtn();

  // Special sections grid
  const specialGrid = document.createElement('div');
  specialGrid.className = 'acx-special-grid';

  specialGrid.appendChild(renderSpecialSectionCard_('Opening Credits', master.openingCredits, root, master));
  specialGrid.appendChild(renderSpecialSectionCard_('Closing Credits', master.closingCredits, root, master));
  specialGrid.appendChild(renderSpecialSectionCard_('About Author (optional)', master.aboutAuthor, root, master));
  panel.appendChild(specialGrid);

  // Retail sample card
  const retailCard = document.createElement('div');
  retailCard.className = 'acx-section-card';
  const refs = master.retailSample?.sectionRefs ?? [];
  const refItems = refs.map(ref => {
    const ch = master.chapters.find(c => c.tabName === ref.chapterTabName);
    const section = ch?.sections.find(s => s.id === ref.sectionId);
    const text = section?.type === 'speech' ? section.text.slice(0, 50) + (section.text.length > 50 ? '…' : '') : '';
    return `<li class="acx-retail-ref">${escHtml(ref.chapterTabName)} — ${escHtml(text)}</li>`;
  }).join('');

  retailCard.innerHTML = `
    <div class="acx-card-header">
      <span class="acx-card-title">Retail Sample</span>
      ${refs.length > 0 ? `<span class="badge badge-done">${refs.length} section${refs.length > 1 ? 's' : ''}</span>` : ''}
    </div>
    <div class="acx-card-body">
      ${refs.length > 0
        ? `<ul class="acx-retail-list">${refItems}</ul>
           <button class="btn btn-secondary" id="acx-retail-clear">Clear All</button>`
        : '<span class="acx-card-missing">Right-click chapter sections to designate retail sample audio.</span>'}
    </div>`;
  panel.appendChild(retailCard);

  retailCard.querySelector('#acx-retail-clear')?.addEventListener('click', async () => {
    for (const ref of refs) {
      try { await invoke('remove_retail_sample_ref', { sectionId: ref.sectionId }); } catch {}
    }
    renderManifestEditor(root);
  });

  // Cover image card
  const coverCard = document.createElement('div');
  coverCard.className = 'acx-section-card';
  const cover = master.cover;
  const hasCover = !!cover?.imagePath;
  const coverBasename = cover?.imagePath?.split('/').pop() ?? '';
  const dimOk = (cover?.width ?? 0) >= 2400 && (cover?.height ?? 0) >= 2400;
  const fmtOk = cover?.format ? ['jpg', 'jpeg', 'png'].includes(cover.format) : false;
  const sizeStr = cover?.fileSizeBytes ? `${(cover.fileSizeBytes / 1_000_000).toFixed(1)} MB` : 'unknown';

  coverCard.innerHTML = `
    <div class="acx-card-header">
      <span class="acx-card-title">Cover Image</span>
      ${hasCover ? `<span class="badge ${dimOk && fmtOk ? 'badge-done' : 'badge-error'}">${dimOk && fmtOk ? '&#10003; Valid' : '&#9888; Issues'}</span>` : ''}
    </div>
    <div class="acx-card-body">
      ${hasCover ? `
        <span class="acx-cover-name">${escHtml(coverBasename)}</span>
        <div class="acx-cover-specs">
          <span class="${dimOk ? 'acx-spec-ok' : 'acx-spec-err'}">${cover!.width ?? '?'}x${cover!.height ?? '?'}px ${dimOk ? '&#10003;' : '&#10007;'}</span>
          <span class="${fmtOk ? 'acx-spec-ok' : 'acx-spec-err'}">${cover!.format ?? 'unknown'} ${fmtOk ? '&#10003;' : '&#10007;'}</span>
          <span>${escHtml(sizeStr)}</span>
        </div>` : `<span class="acx-card-missing">No cover image selected.</span>`}
      <button class="btn btn-secondary" id="acx-pick-cover">Pick Image</button>
    </div>`;
  panel.appendChild(coverCard);

  coverCard.querySelector('#acx-pick-cover')!.addEventListener('click', async () => {
    const picked = await open({
      filters: [{ name: 'Images', extensions: ['jpg', 'jpeg', 'png', 'tif', 'tiff'] }],
    });
    if (!picked) return;
    const imagePath = typeof picked === 'string' ? picked : (picked as string[])[0];
    try {
      await invoke('set_cover_image', { imagePath });
      renderManifestEditor(root);
    } catch (e) {
      showErrorOverlay(String(e));
    }
  });

  // Progress line
  const progressLine = document.createElement('div');
  progressLine.className = 'acx-progress-line';
  progressLine.style.display = 'none';
  panel.appendChild(progressLine);

  root.appendChild(panel);

  // Audit button
  controlsRow.querySelector('#acx-audit-btn')!.addEventListener('click', async () => {
    try {
      const results: AuditResult[] = await invoke('run_acx_audit', { quality: acxQuality_ });
      showAuditOverlay_(results);
    } catch (e) {
      showErrorOverlay(String(e));
    }
  });

  // Generate ACX Package button
  controlsRow.querySelector('#acx-generate-btn')!.addEventListener('click', async () => {
    const btn = controlsRow.querySelector('#acx-generate-btn') as HTMLButtonElement;
    btn.disabled = true;
    btn.textContent = 'Generating…';
    progressLine.style.display = 'block';
    progressLine.textContent = 'Starting ACX package generation…';

    const { listen } = await import('@tauri-apps/api/event');
    const unlisten = await listen<{ message: string }>('acx-package-progress', (ev) => {
      progressLine.textContent = ev.payload.message;
    });

    try {
      const outputPath: string = await invoke('generate_acx_package', { quality: acxQuality_ });
      unlisten();
      progressLine.textContent = `Package created: ${outputPath.split('/').pop()}`;
      btn.disabled = false;
      btn.textContent = 'Generate ACX Package';
      const revealBtn = document.createElement('button');
      revealBtn.className = 'btn btn-secondary';
      revealBtn.textContent = 'Reveal in Finder';
      revealBtn.style.marginLeft = '8px';
      revealBtn.addEventListener('click', () => {
        invoke('reveal_in_finder', { filePath: outputPath }).catch((e) => showErrorOverlay(String(e)));
      });
      progressLine.appendChild(revealBtn);
    } catch (e) {
      unlisten();
      btn.disabled = false;
      btn.textContent = 'Generate ACX Package';
      progressLine.style.display = 'none';
      showErrorOverlay(String(e));
    }
  });
}

function showAuditOverlay_(results: AuditResult[]): void {
  const errors   = results.filter(r => !r.passed && r.severity === 'Error').length;
  const warnings = results.filter(r => !r.passed && r.severity === 'Warning').length;

  const rowsHtml = results.map(r => {
    const icon = r.passed ? '&#10003;' : (r.severity === 'Error' ? '&#10007;' : '&#9888;');
    const cls  = r.passed ? 'audit-pass' : (r.severity === 'Error' ? 'audit-error' : 'audit-warn');
    return `<tr class="${cls}">
      <td class="audit-icon">${icon}</td>
      <td class="audit-label">${escHtml(r.label)}</td>
      <td class="audit-msg">${escHtml(r.message)}</td>
    </tr>`;
  }).join('');

  const summary = errors > 0 || warnings > 0
    ? `${errors} error${errors !== 1 ? 's' : ''}, ${warnings} warning${warnings !== 1 ? 's' : ''}`
    : 'All checks passed';

  const backdrop = document.createElement('div');
  backdrop.className = 'error-backdrop';
  backdrop.innerHTML = `
    <div class="error-box" style="max-width:680px;width:90vw;">
      <div class="error-box-header">
        <span>ACX Structural Audit</span>
        <button class="btn btn-icon" id="audit-close">&#10005;</button>
      </div>
      <p class="audit-summary">${escHtml(summary)}</p>
      <table class="audit-table">
        <tbody>${rowsHtml}</tbody>
      </table>
      <div class="error-actions">
        <button class="btn btn-secondary" id="audit-close2">Close</button>
      </div>
    </div>`;
  document.body.appendChild(backdrop);
  const close = () => document.body.removeChild(backdrop);
  backdrop.querySelector('#audit-close')!.addEventListener('click', close);
  backdrop.querySelector('#audit-close2')!.addEventListener('click', close);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
}

// ---------------------------------------------------------------------------
// Section row  (card left + text right, scroll together)
// ---------------------------------------------------------------------------

function buildRow(
  section: ManifestSection,
  blocked: Set<string>,
  root: HTMLElement,
  scroll: HTMLElement,
  master?: MasterManifest,
): HTMLElement {
  const row = document.createElement('div');
  const isSilence = section.type === 'silence';
  row.className = 'section-row'
    + (isSilence ? ' silence-row' : '')
    + (selectedId_ === section.id ? ' selected' : '');
  row.dataset.id = section.id;

  row.addEventListener('click', () => {
    scroll.querySelectorAll<HTMLElement>('.section-row').forEach(r => r.classList.remove('selected'));
    row.classList.add('selected');
    selectedId_ = section.id;
  });

  row.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    e.stopPropagation();
    showContextMenu_(e.clientX, e.clientY, section.id, root, scroll, master, section.type === 'speech');
  });

  row.appendChild(buildCard(section, blocked, root, scroll, master));
  if (!isSilence) {
    const textCell = buildTextCell(section);
    row.appendChild(textCell);
    attachTextHandlers_(section, textCell, row, scroll, root);
  }
  return row;
}

function attachTextHandlers_(
  section: ManifestSection,
  textCell: HTMLElement,
  row: HTMLElement,
  scroll: HTMLElement,
  root: HTMLElement,
): void {
  if (section.type !== 'speech') return;
  const sp = section as SpeechSection;
  const ta = textCell.querySelector('textarea') as HTMLTextAreaElement;
  if (!ta) return;

  let savedText = sp.text;
  let warnEl: HTMLElement | null = null;

  const hasAudio_ = (): boolean =>
    !!(sp.audioFiles && Object.keys(sp.audioFiles).length > 0);

  const textChanged_ = (): boolean =>
    normalizeText(ta.value) !== normalizeText(savedText);

  const removeWarn_ = (): void => {
    if (warnEl) { warnEl.remove(); warnEl = null; }
  };

  const normalizeInPlace_ = (): void => {
    const n = normalizeText(ta.value);
    if (ta.value !== n) ta.value = n;
  };

  const showWarn_ = (): void => {
    if (warnEl || !textChanged_()) {
      // Equivalent content — silently normalize the textarea value
      normalizeInPlace_();
      return;
    }
    if (!hasAudio_()) {
      // No audio — normalize then save silently
      normalizeInPlace_();
      invoke('update_section', { sectionId: sp.id, newText: ta.value });
      savedText = ta.value;
      return;
    }
    warnEl = document.createElement('div');
    warnEl.className = 'text-change-warn';
    warnEl.innerHTML =
      `<span class="text-change-msg">Text changed — keep existing audio?</span>` +
      `<button class="btn btn-secondary btn-sm" id="twarn-keep-${sp.id}">Keep audio</button>` +
      `<button class="btn btn-secondary btn-sm text-change-remove" id="twarn-rm-${sp.id}">Remove audio</button>`;
    textCell.appendChild(warnEl);

    warnEl.querySelector(`#twarn-keep-${sp.id}`)!.addEventListener('click', async (e) => {
      e.stopPropagation();
      normalizeInPlace_();
      await invoke('update_section', { sectionId: sp.id, newText: ta.value });
      savedText = ta.value;
      removeWarn_();
    });

    warnEl.querySelector(`#twarn-rm-${sp.id}`)!.addEventListener('click', async (e) => {
      e.stopPropagation();
      normalizeInPlace_();
      await invoke('update_section', { sectionId: sp.id, newText: ta.value, clearAudio: true });
      savedText = ta.value;
      removeWarn_();
      // Rebuild card in-place — audio state changed
      const freshMaster: MasterManifest | null = await invoke('get_master_manifest');
      if (freshMaster) {
        const allSections = [
          ...freshMaster.chapters.flatMap(c => c.sections),
          ...(freshMaster.openingCredits?.sections ?? []),
          ...(freshMaster.closingCredits?.sections ?? []),
          ...(freshMaster.aboutAuthor?.sections ?? []),
        ];
        const freshSection = allSections.find(s => s.id === sp.id);
        if (freshSection) {
          const chManifest = freshMaster.chapters.find(c => c.sections.some(s => s.id === sp.id));
          const freshBlocked = computeBlockedSections(
            chManifest?.sections ?? [],
            selectedFormat_(),
          );
          const oldCard = row.querySelector('.section-card') as HTMLElement | null;
          if (oldCard) oldCard.replaceWith(buildCard(freshSection, freshBlocked, root, scroll, freshMaster));
        }
      }
    });
  };

  // Hide warning if user reverts text to equivalent of saved
  ta.addEventListener('input', () => {
    if (warnEl && !textChanged_()) removeWarn_();
  });

  // Show warning when textarea loses focus with a meaningfully changed value
  ta.addEventListener('change', () => showWarn_());

  // Also show when mouse leaves the entire row (covers keyboard navigation away)
  row.addEventListener('mouseleave', () => {
    if (textChanged_()) showWarn_();
  });
}

// ---------------------------------------------------------------------------
// Section card (left side of each row)
// ---------------------------------------------------------------------------

function buildCard(
  section: ManifestSection,
  blocked: Set<string>,
  root: HTMLElement,
  scroll: HTMLElement,
  master?: MasterManifest,
): HTMLElement {
  const card = document.createElement('div');
  card.className = 'section-card';

  if (section.type === 'silence') {
    card.classList.add('silence-card');
    card.innerHTML = `
      <span class="badge badge-silence">SILENCE</span>
      <span class="silence-sep">|</span>
      <input type="number" class="input-number-small" id="dur-${section.id}"
        value="${section.durationMs}" min="0" step="250" title="Duration in milliseconds">
      <span class="silence-ms-label">ms</span>`;
    card.querySelector(`#dur-${section.id}`)!
      .addEventListener('change', async (e) => {
        const val = parseInt((e.target as HTMLInputElement).value, 10);
        if (isNaN(val) || val < 0) return;
        await invoke('update_silence_section', { sectionId: section.id, durationMs: val });
      });
    return card;
  }

  const sp = section as SpeechSection;
  const fmt = selectedFormat_();
  const audioPath = sp.audioFiles?.[fmt];
  const status = sectionStatus_(sp, blocked);
  const errMsg = sectionErrors_.get(sp.id) ?? '';
  const audioState  = playState_(sp.id);
  const canGenerate = status !== 'blocked' && status !== 'generating';

  const genBtnHtml = status === 'failed'
    ? `<button class="btn btn-failed" id="gen-${sp.id}" title="Click to see error">&#9679; Failed</button>`
    : status === 'generating'
      ? `<button class="btn btn-generate btn-disabled" disabled>Generating&#8230;</button>`
      : `<button class="btn btn-generate ${canGenerate ? '' : 'btn-disabled'}" id="gen-${sp.id}"
           ${canGenerate ? '' : 'disabled'}
           title="${status === 'blocked' ? `Waiting for prior ${escHtml(sp.voiceName)} section` : ''}"
         >${status === 'dirty' ? 'Regenerate' : 'Generate'}</button>`;

  const playIcon  = audioState === 'playing' ? '&#9646;&#9646;' : '&#9654;';
  const playTitle = audioState === 'playing' ? 'Pause' : audioState === 'paused' ? 'Resume' : 'Play audio';
  const playHtml  = audioPath
    ? `<button class="btn-play${audioState !== 'idle' ? ' ' + audioState : ''}" id="play-${sp.id}"
         title="${playTitle}">${playIcon}</button>`
    : '';

  const finderHtml = audioPath
    ? `<button class="btn-reveal" id="reveal-${sp.id}" title="Show in Finder">File</button>`
    : '';

  const inRetailSample = master?.retailSample?.sectionRefs.some(r => r.sectionId === sp.id) ?? false;
  const retailBadge = inRetailSample ? '<span class="retail-badge" title="In retail sample">&#9671;</span>' : '';

  card.innerHTML = `
    <div class="card-header">
      <span class="badge badge-speech" title="${escHtml(sp.ttsModel)}">SPEECH</span>
      ${retailBadge}
      <span class="status-dot status-${status}" title="${statusLabel(status)}"></span>
      <div class="card-actions">
        ${genBtnHtml}
        ${playHtml}
        ${finderHtml}
      </div>
    </div>
    <div class="voice-line">
      <span class="voice-name editable-voice" id="vname-${sp.id}" title="Click to change voice">${sp.voiceName ? escHtml(sp.voiceName) : '<em class="voice-empty">(select voice)</em>'}</span>
    </div>
    <div class="sliders-row">
      <span class="param-label">Stab</span>
      <input type="range" min="0" max="1" step="0.01"
        value="${sp.stability}" id="stab-${sp.id}" class="slider"
        title="Stability: ${sp.stability.toFixed(2)}">
      <span class="param-label">Sim</span>
      <input type="range" min="0" max="1" step="0.01"
        value="${sp.similarityBoost}" id="sim-${sp.id}" class="slider"
        title="Similarity: ${sp.similarityBoost.toFixed(2)}">
      <span class="param-label">Spd</span>
      <input type="range" min="0.75" max="1.5" step="0.25"
        value="${(sp.speed ?? 1.0).toFixed(2)}" id="spd-${sp.id}" class="slider slider-narrow"
        title="Speed: ${(sp.speed ?? 1.0).toFixed(2)}x">
    </div>`;

  // Slider handlers — update title (tooltip) on move, persist on mouseup/touchend.
  const stabEl = card.querySelector(`#stab-${sp.id}`) as HTMLInputElement;
  stabEl.addEventListener('input', () => {
    stabEl.title = `Stability: ${parseFloat(stabEl.value).toFixed(2)}`;
  });
  stabEl.addEventListener('change', async () => {
    await invoke('update_section', { sectionId: sp.id, newStability: parseFloat(stabEl.value) });
  });

  const simEl = card.querySelector(`#sim-${sp.id}`) as HTMLInputElement;
  simEl.addEventListener('input', () => {
    simEl.title = `Similarity: ${parseFloat(simEl.value).toFixed(2)}`;
  });
  simEl.addEventListener('change', async () => {
    await invoke('update_section', { sectionId: sp.id, newSimilarityBoost: parseFloat(simEl.value) });
  });

  const spdEl = card.querySelector(`#spd-${sp.id}`) as HTMLInputElement;
  spdEl.addEventListener('input', () => {
    spdEl.title = `Speed: ${parseFloat(spdEl.value).toFixed(2)}x`;
  });
  spdEl.addEventListener('change', async () => {
    await invoke('update_section', { sectionId: sp.id, newSpeed: parseFloat(spdEl.value) });
  });

  // Voice name — click to open inline dropdown
  const vnameEl = card.querySelector(`#vname-${sp.id}`) as HTMLElement;

  const openVoicePicker_ = async (anchor: HTMLElement) => {
    const voices = await getVoices_();
    if (!voices.length) return;

    const sel = document.createElement('select');
    sel.className = 'voice-select';

    // Collect voice IDs used anywhere in the manifest, preserving order of appearance.
    const usedIds = new Set<string>();
    if (master) {
      const allAms = [
        ...master.chapters,
        ...(master.openingCredits ? [master.openingCredits] : []),
        ...(master.closingCredits ? [master.closingCredits] : []),
        ...(master.aboutAuthor   ? [master.aboutAuthor]   : []),
      ];
      for (const am of allAms) {
        for (const s of am.sections) {
          if (s.type === 'speech' && s.voiceId) usedIds.add(s.voiceId);
        }
      }
    }
    const usedVoices   = voices.filter(v => usedIds.has(v.voiceId));
    const unusedVoices = voices.filter(v => !usedIds.has(v.voiceId));

    // Placeholder for new sections with no voice yet
    if (!sp.voiceId) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = '(select voice)';
      opt.selected = true;
      sel.appendChild(opt);
    } else if (!voices.find(v => v.voiceId === sp.voiceId)) {
      // Ensure current voice appears even if not in the API list (e.g. custom clone)
      const opt = document.createElement('option');
      opt.value = sp.voiceId;
      opt.textContent = sp.voiceName || sp.voiceId;
      opt.selected = true;
      sel.appendChild(opt);
    }

    const addVoiceOption = (v: VoiceInfo) => {
      const opt = document.createElement('option');
      opt.value = v.voiceId;
      opt.textContent = v.name;
      if (v.voiceId === sp.voiceId) opt.selected = true;
      sel.appendChild(opt);
    };

    if (usedVoices.length > 0) {
      const grpUsed = document.createElement('optgroup');
      grpUsed.label = '— Used in this book —';
      sel.appendChild(grpUsed);
      usedVoices.forEach(v => { addVoiceOption(v); });

      if (unusedVoices.length > 0) {
        const grpAll = document.createElement('optgroup');
        grpAll.label = '— All voices —';
        sel.appendChild(grpAll);
        unusedVoices.forEach(v => { addVoiceOption(v); });
      }
    } else {
      voices.forEach(v => { addVoiceOption(v); });
    }

    anchor.replaceWith(sel);
    sel.focus();

    let committed = false;

    sel.addEventListener('change', async () => {
      committed = true;
      const newVoiceId = sel.value;
      if (!newVoiceId) return; // placeholder selected — nothing to do
      const oldVoiceId   = sp.voiceId;   // capture before mutation
      const newVoiceName = voices.find(v => v.voiceId === newVoiceId)?.name ?? sp.voiceName;

      // Optimistically update sp so blur-restore shows the new name immediately.
      sp.voiceId   = newVoiceId;
      sp.voiceName = newVoiceName;
      lastChosenVoice_ = { voiceId: newVoiceId, voiceName: newVoiceName };

      await invoke('update_section', {
        sectionId: sp.id,
        newVoiceId,
        newVoiceName,
      });

      // Rebuild only the cards whose blocking/status may have changed:
      // this section, any section that shared the old voice, any that share the new voice.
      const freshMaster: MasterManifest | null = await invoke('get_master_manifest');
      if (freshMaster) {
        const freshManifest = freshMaster.chapters[currentChapterIndex_];
        if (freshManifest) {
          const freshBlocked = computeBlockedSections(freshManifest.sections, selectedFormat_());
          for (const s of freshManifest.sections) {
            if (s.type !== 'speech') continue;
            if (s.id !== sp.id && s.voiceId !== oldVoiceId && s.voiceId !== newVoiceId) continue;
            const row = scroll.querySelector(`[data-id="${s.id}"]`) as HTMLElement | null;
            const oldCard = row?.querySelector('.section-card') as HTMLElement | null;
            if (oldCard) oldCard.replaceWith(buildCard(s, freshBlocked, root, scroll, freshMaster));
          }
        } else {
          renderManifestEditor(root);
        }
      } else {
        renderManifestEditor(root);
      }
    });

    sel.addEventListener('blur', () => {
      if (committed) return;
      // User dismissed without picking — restore span without saving
      const restored = document.createElement('span');
      restored.className = 'voice-name editable-voice';
      restored.id = `vname-${sp.id}`;
      restored.title = 'Click to change voice';
      restored.innerHTML = sp.voiceName ? escHtml(sp.voiceName) : '<em class="voice-empty">(select voice)</em>';
      sel.replaceWith(restored);
      restored.addEventListener('click', (ev) => { ev.stopPropagation(); openVoicePicker_(restored); });
    });
  };

  vnameEl.addEventListener('click', (e) => { e.stopPropagation(); openVoicePicker_(vnameEl); });

  // Generate / Failed button
  const genBtn = card.querySelector(`#gen-${sp.id}`) as HTMLButtonElement | null;
  if (genBtn) {
    if (status === 'failed') {
      genBtn.addEventListener('click', (e) => { e.stopPropagation(); showErrorOverlay(errMsg); });
    } else if (canGenerate) {
      genBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        genBtn.disabled = true;
        genBtn.textContent = 'Generating…';
        try {
          await invoke('generate_section', { sectionId: sp.id, quality: selectedQuality_ });
          sectionErrors_.delete(sp.id);
        } catch (err) {
          sectionErrors_.set(sp.id, String(err));
        }
        // Refresh only the generated card and same-voice siblings (blocking may have changed).
        // Avoids a full re-render so scroll position and focus are not disrupted.
        const freshMaster2: MasterManifest | null = await invoke('get_master_manifest');
        if (freshMaster2) {
          const freshManifest2 = freshMaster2.chapters[currentChapterIndex_];
          if (freshManifest2) {
            const freshBlocked = computeBlockedSections(freshManifest2.sections, selectedFormat_());
            for (const s of freshManifest2.sections) {
              if (s.type !== 'speech') continue;
              if (s.id !== sp.id && s.voiceId !== sp.voiceId) continue;
              const row = scroll.querySelector(`[data-id="${s.id}"]`) as HTMLElement | null;
              const oldCard = row?.querySelector('.section-card') as HTMLElement | null;
              if (oldCard) oldCard.replaceWith(buildCard(s, freshBlocked, root, scroll, freshMaster2));
            }
          } else {
            renderManifestEditor(root);
          }
        } else {
          renderManifestEditor(root);
        }
      });
    }
  }

  // Play button — pause/resume aware, handled by shared handlePlayClick_
  const playBtn = card.querySelector(`#play-${sp.id}`) as HTMLButtonElement | null;
  if (playBtn && audioPath) {
    playBtn.addEventListener('click', (e) => handlePlayClick_(e, audioPath, sp.id));
  }

  // Reveal in Finder
  const revealBtn = card.querySelector(`#reveal-${sp.id}`) as HTMLButtonElement | null;
  if (revealBtn && audioPath) {
    revealBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      invoke('reveal_in_finder', { filePath: audioPath })
        .catch((err) => showErrorOverlay(`Finder error: ${err}`));
    });
  }

  return card;
}

// ---------------------------------------------------------------------------
// Text cell (right side of each row)
// ---------------------------------------------------------------------------

function buildTextCell(section: ManifestSection): HTMLElement {
  const cell = document.createElement('div');
  cell.className = 'text-cell';

  if (section.type === 'silence') {
    const lbl = document.createElement('span');
    lbl.className = 'silence-label';
    lbl.textContent = `[ ${section.durationMs} ms silence ]`;
    cell.appendChild(lbl);
    return cell;
  }

  const sp = section as SpeechSection;
  const ta = document.createElement('textarea');
  ta.className = 'text-area';
  ta.value = sp.text;
  ta.placeholder = '(empty)';
  autoResize_(ta);
  ta.addEventListener('input', () => autoResize_(ta));
  // change listener attached by buildRow so it has access to audio state and row element
  cell.appendChild(ta);
  return cell;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function autoResize_(ta: HTMLTextAreaElement): void {
  ta.style.height = 'auto';
  ta.style.height = ta.scrollHeight + 'px';
}

function highlightJson_(text: string): string {
  const esc = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return esc.replace(
    /("(?:\\.|[^"\\])*")(\s*:)|("(?:\\.|[^"\\])*")|(\b(?:true|false|null)\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}[\],])/g,
    (_m, key, colon, str, kw, num, struct) => {
      if (key)    return `<span class="jh-key">${key}</span>${colon}`;
      if (str)    return `<span class="jh-str">${str}</span>`;
      if (kw)     return `<span class="jh-kw">${kw}</span>`;
      if (num)    return `<span class="jh-num">${num}</span>`;
      if (struct) return `<span class="jh-struct">${struct}</span>`;
      return _m;
    },
  );
}


