// The book view: chapters and their sections (read-only text, generate,
// play, stitch) and the ACX panel. Text and casting are AuthorLM's; this
// view never edits them (docs/audiobook-pipeline-design.md §10).

import { invoke } from '@tauri-apps/api/core';
import { navigate, setStatusMessage } from './main';
import { openFolder } from './menu';
import type {
  AuditResult, LoadedChapter, Master, Panel, Quality, ReloadSummary, Section, Speech,
} from './types';
import { QUALITIES, QUALITY_LABEL } from './types';
import {
  allChapters, changeLabel, chapterHasChanges, escHtml, formatDuration, isDone,
  pendingCount, remainingCharacters, sectionStatus, shortModel, statusLabel, stitchStale, withHelpers,
} from './utils';

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

const sectionErrors_ = new Map<string, string>();
const generating_ = new Set<string>();
let currentStem_: string | null = null;
let currentPanel_: Panel = 'chapters';
let selectedQuality_: Quality | null = null;
let acxQuality_: string = 'mp3_44100_128';
let currentAudio_: HTMLAudioElement | null = null;
let currentAudioUrl_: string | null = null;
let currentPlayingId_: string | null = null;

export function clearErrors(): void {
  sectionErrors_.clear();
  generating_.clear();
  stopPlayback_();
}

export function summaryText(s: ReloadSummary): string {
  const parts: string[] = [];
  if (s.changedSections) parts.push(`${s.changedSections} section(s) changed`);
  if (s.removedSections) parts.push(`${s.removedSections} removed`);
  if (s.addedChapters.length) parts.push(`chapters added: ${s.addedChapters.join(', ')}`);
  if (s.removedChapters.length) parts.push(`chapters removed: ${s.removedChapters.join(', ')}`);
  if (s.bookChanges.length) parts.push(`book: ${s.bookChanges.join(', ')}`);
  if (s.takesChanged) parts.push(`${s.takesChanged} take(s) changed`);
  return parts.length ? parts.join(' · ') : 'nothing changed';
}

function format_(): string { return selectedQuality_ ?? 'mp3_44100_128'; }

// ---------------------------------------------------------------------------
// Playback
// ---------------------------------------------------------------------------

function stopPlayback_(): void {
  if (!currentAudio_) return;
  const audio = currentAudio_;
  const url = currentAudioUrl_;
  const prev = currentPlayingId_;
  currentAudio_ = null; currentAudioUrl_ = null; currentPlayingId_ = null;
  audio.pause();
  if (url) URL.revokeObjectURL(url);
  if (prev) setPlayButton_(prev, 'idle');
}

function setPlayButton_(id: string, state: 'idle' | 'playing' | 'paused'): void {
  const btn = document.getElementById(`play-${id}`) as HTMLButtonElement | null;
  if (!btn) return;
  btn.classList.toggle('playing', state === 'playing');
  btn.classList.toggle('paused', state === 'paused');
  btn.innerHTML = state === 'playing' ? '&#9646;&#9646;' : '&#9654;';
}

async function playClip_(rel: string, id: string): Promise<void> {
  if (currentPlayingId_ === id && currentAudio_) {
    if (currentAudio_.paused) { await currentAudio_.play(); setPlayButton_(id, 'playing'); }
    else { currentAudio_.pause(); setPlayButton_(id, 'paused'); }
    return;
  }
  stopPlayback_();
  try {
    const b64 = await invoke<string>('read_audio_base64', { filePath: rel });
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/mpeg' }));
    const audio = new Audio(url);
    currentAudio_ = audio; currentAudioUrl_ = url; currentPlayingId_ = id;
    audio.addEventListener('ended', () => { if (currentPlayingId_ === id) stopPlayback_(); });
    await audio.play();
    setPlayButton_(id, 'playing');
  } catch (e) {
    showErrorOverlay(`Could not play: ${e}`);
  }
}

// ---------------------------------------------------------------------------
// Overlays
// ---------------------------------------------------------------------------

export function showErrorOverlay(message: string): void {
  const backdrop = document.createElement('div');
  backdrop.className = 'error-backdrop';
  backdrop.innerHTML = `
    <div class="error-box">
      <div class="error-box-header"><span>Error</span>
        <button class="btn btn-icon" id="err-close">&#10005;</button></div>
      <pre class="error-text">${escHtml(message)}</pre>
      <div class="error-actions"><button class="btn btn-secondary" id="err-close2">Close</button></div>
    </div>`;
  document.body.appendChild(backdrop);
  const close = () => backdrop.remove();
  backdrop.querySelector('#err-close')!.addEventListener('click', close);
  backdrop.querySelector('#err-close2')!.addEventListener('click', close);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
}

function showAuditOverlay_(results: AuditResult[]): void {
  const errors = results.filter(r => !r.passed && r.severity === 'Error').length;
  const warnings = results.filter(r => !r.passed && r.severity === 'Warning').length;
  const rows = results.map(r => {
    const icon = r.passed ? '&#10003;' : (r.severity === 'Error' ? '&#10007;' : '&#9888;');
    const cls = r.passed ? 'audit-pass' : (r.severity === 'Error' ? 'audit-error' : 'audit-warn');
    return `<tr class="${cls}"><td class="audit-icon">${icon}</td>
      <td class="audit-label">${escHtml(r.label)}</td><td class="audit-msg">${escHtml(r.message)}</td></tr>`;
  }).join('');
  const summary = errors || warnings ? `${errors} error(s), ${warnings} warning(s)` : 'All checks passed';
  const backdrop = document.createElement('div');
  backdrop.className = 'error-backdrop';
  backdrop.innerHTML = `
    <div class="error-box" style="max-width:680px;width:90vw;">
      <div class="error-box-header"><span>ACX Structural Audit</span>
        <button class="btn btn-icon" id="audit-close">&#10005;</button></div>
      <p class="audit-summary">${escHtml(summary)}</p>
      <table class="audit-table"><tbody>${rows}</tbody></table>
      <div class="error-actions"><button class="btn btn-secondary" id="audit-close2">Close</button></div>
    </div>`;
  document.body.appendChild(backdrop);
  const close = () => backdrop.remove();
  backdrop.querySelector('#audit-close')!.addEventListener('click', close);
  backdrop.querySelector('#audit-close2')!.addEventListener('click', close);
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
}

// ---------------------------------------------------------------------------
// The view
// ---------------------------------------------------------------------------

// Renders race: boot, the restore of the last folder and a `book-changed`
// event can each ask for a render within the same tick. Each one clears
// the view, awaits the book, then appends — so two of them in flight
// paint the page twice. Only the most recent request may touch the DOM.
let renderSeq_ = 0;

export async function renderBookView(root: HTMLElement): Promise<void> {
  const seq = ++renderSeq_;
  const prevScrollTop = root.querySelector<HTMLElement>('.sections-scroll')?.scrollTop ?? 0;

  const master: Master | null = await invoke('get_master');
  if (seq !== renderSeq_) return;
  root.innerHTML = '';
  if (!master) {
    root.innerHTML = `
      <div class="empty-state">
        <p>No audiobook folder is open.</p>
        <p class="hint">Run <code>authorlm audio export</code>, then open the manuscript's
          <code>_audio/</code> folder.</p>
        <p><button class="btn btn-primary" id="btn-open-empty">Open Audiobook Folder&#8230;</button></p>
      </div>`;
    root.querySelector('#btn-open-empty')!.addEventListener('click', () => openFolder(() => navigate('book')));
    return;
  }
  const chapters = allChapters(master);
  if (!selectedQuality_) {
    selectedQuality_ = (QUALITIES as readonly string[]).includes(master.book.quality)
      ? master.book.quality as Quality : 'mp3_44100_128';
    acxQuality_ = selectedQuality_ === 'mp3_44100_192' ? 'mp3_44100_192' : 'mp3_44100_128';
  }
  if (!currentStem_ || !chapters.some(c => c.chapter.stem === currentStem_)) {
    currentStem_ = chapters[0]?.chapter.stem ?? null;
  }
  const format = format_();
  const current = chapters.find(c => c.chapter.stem === currentStem_) ?? null;

  // Toolbar
  const toolbar = document.createElement('div');
  toolbar.className = 'toolbar';
  const options = chapters.map(c => {
    const mark = chapterHasChanges(c, format) ? ' ●' : '';
    const label = master.book.chapters.some(r => r.stem === c.chapter.stem)
      ? c.chapter.title : `— ${c.chapter.title}`;
    return `<option value="${escHtml(c.chapter.stem)}"${c.chapter.stem === currentStem_ ? ' selected' : ''}>${escHtml(label)}${mark}</option>`;
  }).join('');
  toolbar.innerHTML = `
    <div class="toolbar-left">
      <span class="manifest-title" title="${escHtml(master.dir)}">${escHtml(master.book.title)}</span>
      <select id="sel-chapter" class="chapter-select" title="Chapter">${options}</select>
    </div>
    <div class="toolbar-right">
      <span id="credits-display" class="credits-display" title="ElevenLabs characters remaining"></span>
      <button id="btn-clear-lower" class="btn btn-secondary btn-clear-lower" title="Delete audio below the selected quality">Clear Lower Quality</button>
      <button id="btn-generate-all" class="btn btn-primary">Generate All Remaining</button>
      <select id="sel-quality" class="quality-select" title="Audio quality">
        ${QUALITIES.map(q => `<option value="${q}"${q === selectedQuality_ ? ' selected' : ''}>${QUALITY_LABEL[q]}</option>`).join('')}
      </select>
      <button id="btn-settings" class="btn btn-icon" title="Settings">&#9881;</button>
    </div>`;
  root.appendChild(toolbar);
  (toolbar.querySelector('#sel-chapter') as HTMLSelectElement).addEventListener('change', (e) => {
    currentStem_ = (e.target as HTMLSelectElement).value;
    renderBookView(root);
  });
  (toolbar.querySelector('#sel-quality') as HTMLSelectElement).addEventListener('change', (e) => {
    selectedQuality_ = (e.target as HTMLSelectElement).value as Quality;
    renderBookView(root);
  });
  toolbar.querySelector('#btn-settings')!.addEventListener('click', () => navigate('settings'));
  invoke<{ characterCount: number; characterLimit: number }>('get_subscription')
    .then(sub => {
      const el = toolbar.querySelector('#credits-display') as HTMLElement | null;
      if (el) {
        const remaining = sub.characterLimit - sub.characterCount;
        el.textContent = `${remaining.toLocaleString()} chars`;
        el.title = `${remaining.toLocaleString()} of ${sub.characterLimit.toLocaleString()} ElevenLabs characters remaining`;
      }
    })
    .catch(() => { /* no key yet */ });

  // Panel tabs
  const tabs = document.createElement('div');
  tabs.className = 'panel-tabs';
  tabs.innerHTML = `
    <button class="panel-tab${currentPanel_ === 'chapters' ? ' active' : ''}" data-panel="chapters">Chapters</button>
    <button class="panel-tab${currentPanel_ === 'acx' ? ' active' : ''}" data-panel="acx">ACX Package</button>`;
  root.appendChild(tabs);
  tabs.querySelectorAll<HTMLButtonElement>('.panel-tab').forEach(btn => {
    btn.addEventListener('click', () => { currentPanel_ = btn.dataset.panel as Panel; renderBookView(root); });
  });

  // Book-level change notice
  if (master.bookChanges.length) {
    const bar = document.createElement('div');
    bar.className = 'info-bar book-changed-bar';
    bar.innerHTML = `<div class="info-bar-content"><span class="info-bar-label">Book changed</span>
      <span class="info-bar-value">${escHtml(master.bookChanges.join(', '))}</span></div>`;
    root.appendChild(bar);
  }

  if (currentPanel_ === 'acx') {
    for (const id of ['#btn-generate-all', '#btn-clear-lower', '#sel-quality']) {
      (toolbar.querySelector(id) as HTMLElement).style.display = 'none';
    }
    renderAcxPanel_(master, chapters, root);
    return;
  }

  if (!current) {
    root.insertAdjacentHTML('beforeend', '<div class="empty-state"><p>No chapters in audiobook.json.</p></div>');
    return;
  }

  // Info bar: dictionary, cast, stitched audio
  const dict = master.book.pronunciationDictionary;
  const castSummary = Object.entries(master.book.cast)
    .map(([k, v]) => `${k}: ${v.voiceName || v.voiceId}`).join(' · ');
  const info = document.createElement('div');
  info.className = 'info-bar';
  info.innerHTML = `
    <div class="info-bar-content">
      <span class="info-bar-label">Dictionary</span>
      <span class="info-bar-value">${dict ? escHtml(dict.name) + (dict.versionId ? ` <span class="info-bar-version" title="${escHtml(dict.versionId)}">v ${escHtml(dict.versionId.slice(0, 8))}…</span>` : ' <em class="voice-empty">(no version — push it)</em>') : '(none)'}</span>
      <span class="info-bar-sep"></span>
      <span class="info-bar-label">Cast</span>
      <span class="info-bar-value" title="${escHtml(castSummary)}">${escHtml(castSummary)}</span>
    </div>
    <div class="info-bar-actions">
      <span class="info-bar-label">${escHtml(shortModel(master.book.model))}</span>
    </div>`;
  root.appendChild(info);

  const counts = pendingCount(current, format);
  const stitched = current.state.stitched[format];
  const header = document.createElement('div');
  header.className = 'info-bar';
  const readyToStitch = counts.generated === counts.total && counts.total > 0;
  const stale = stitchStale(current, format);
  header.innerHTML = `
    <div class="info-bar-content">
      <span class="info-bar-label">${escHtml(current.chapter.title)}</span>
      <span class="info-bar-value">${counts.generated}/${counts.total} generated at ${escHtml(format)}
        ${counts.generated < counts.total ? `· ${remainingCharacters(current, format).toLocaleString()} chars to go` : ''}
        ${current.added ? '· <span class="change-badge">new chapter</span>' : ''}
        ${stale ? '· <span class="change-badge">stitched file is stale — sections or encoder changed</span>' : ''}</span>
      ${stitched ? `<audio id="full-audio-player" controls class="full-audio-player"></audio>
        <span class="info-bar-value">${escHtml(formatDuration(current.state.durationSecs))}</span>` : ''}
    </div>
    <div class="info-bar-actions">
      ${stitched ? `<button class="btn btn-secondary info-bar-btn" id="btn-full-audio-finder">File</button>` : ''}
      <button class="btn ${stale ? 'btn-primary' : 'btn-secondary'} info-bar-btn" id="btn-stitch" ${readyToStitch ? '' : 'disabled title="Generate every section first"'}>${stitched ? (stale ? 'Re-stitch (stale)' : 'Re-stitch') : 'Stitch'}</button>
    </div>`;
  root.appendChild(header);
  if (stitched) {
    const audioEl = header.querySelector('#full-audio-player') as HTMLAudioElement;
    audioEl.addEventListener('play', () => stopPlayback_());
    invoke<string>('read_audio_base64', { filePath: stitched }).then(b64 => {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      audioEl.src = URL.createObjectURL(new Blob([bytes], { type: 'audio/mpeg' }));
    }).catch(() => {});
    header.querySelector('#btn-full-audio-finder')!.addEventListener('click', () => {
      invoke('reveal_in_finder', { filePath: stitched }).catch(e => showErrorOverlay(String(e)));
    });
  }
  header.querySelector('#btn-stitch')!.addEventListener('click', async () => {
    try {
      await invoke('stitch_audio', { stem: current.chapter.stem, quality: format });
      renderBookView(root);
    } catch (e) { showErrorOverlay(String(e)); }
  });

  if (current.removed.length) {
    const removed = document.createElement('div');
    removed.className = 'info-bar removed-notice';
    removed.innerHTML = `<div class="info-bar-content"><span class="info-bar-label">Removed since last reload</span>
      <span class="info-bar-value">${current.removed.map(t => `“${escHtml(t.slice(0, 60))}${t.length > 60 ? '…' : ''}”`).join(' · ')}</span></div>`;
    root.appendChild(removed);
  }

  const scroll = document.createElement('div');
  scroll.className = 'sections-scroll';
  root.appendChild(scroll);
  for (const section of current.chapter.sections) {
    scroll.appendChild(buildRow_(master, current, section, format, root));
  }
  requestAnimationFrame(() => { if (prevScrollTop > 0) scroll.scrollTop = prevScrollTop; });

  toolbar.querySelector('#btn-generate-all')!.addEventListener('click', async () => {
    const btn = toolbar.querySelector('#btn-generate-all') as HTMLButtonElement;
    btn.disabled = true;
    btn.textContent = 'Generating…';
    let err = '';
    try {
      await invoke('generate_all_remaining', { stem: current.chapter.stem, quality: format });
    } catch (e) { err = String(e); }
    renderBookView(root);
    setStatusMessage(err ? `Error: ${err}` : `${current.chapter.title}: generated and stitched at ${format}`);
  });
  toolbar.querySelector('#btn-clear-lower')!.addEventListener('click', async () => {
    try {
      const n = await invoke<number>('clear_lower_quality', { quality: format });
      setStatusMessage(`Cleared lower-quality audio from ${n} section(s).`);
      renderBookView(root);
    } catch (e) { showErrorOverlay(String(e)); }
  });
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

function buildRow_(master: Master, chapter: LoadedChapter, section: Section, format: string, root: HTMLElement): HTMLElement {
  const row = document.createElement('div');
  const isSilence = section.type === 'silence';
  const change = isSilence ? undefined : chapter.changes[section.id];
  const done = !isSilence && isDone(chapter.state.sections[section.id], format);
  const flagged = !!change && !done;
  // One class per state, on the row, so the TEXT cell carries the colour:
  // amber for changed, dashed grey for never generated, green for generated.
  const stateClass = isSilence ? '' : flagged ? ' row-changed' : done ? ' row-done' : ' row-pending';
  row.className = 'section-row' + (isSilence ? ' silence-row' : '') + stateClass;
  row.dataset.id = section.id;
  row.appendChild(buildCard_(master, chapter, section, format, root));
  if (!isSilence) {
    const cell = document.createElement('div');
    cell.className = 'text-cell';
    const sp = section as Speech;
    if (flagged && change) {
      const why = document.createElement('div');
      why.className = 'text-why';
      why.textContent = `${changeLabel(change)} — regenerate`;
      cell.appendChild(why);
    } else if (!done) {
      const why = document.createElement('div');
      why.className = 'text-why text-why-pending';
      why.textContent = 'not generated';
      cell.appendChild(why);
    }
    const text = document.createElement('div');
    text.className = 'text-static' + (sp.kind === 'heading' ? ' text-heading' : '');
    text.textContent = sp.text;
    cell.appendChild(text);
    if (sp.pronunciations.length) {
      const pron = document.createElement('div');
      pron.className = 'pron-line';
      pron.textContent = sp.pronunciations.map(p => `${p.term} → ${p.say}`).join(' · ');
      cell.appendChild(pron);
    }
    row.appendChild(cell);
  }
  return row;
}

function buildCard_(master: Master, chapter: LoadedChapter, section: Section, format: string, root: HTMLElement): HTMLElement {
  const card = document.createElement('div');
  card.className = 'section-card';
  if (section.type === 'silence') {
    card.classList.add('silence-card');
    card.innerHTML = `<span class="badge badge-silence">SILENCE</span>
      <span class="silence-ms-label">${section.durationMs} ms</span>`;
    return card;
  }
  const sp = section;
  const state = chapter.state.sections[sp.id];
  const change = chapter.changes[sp.id];
  const status = sectionStatus(sp, state, change, format, sectionErrors_, generating_);
  const rel = state?.audioFiles?.[format];
  const errMsg = sectionErrors_.get(sp.id) ?? '';
  const inRetail = master.book.retailSample.includes(sp.id);
  const genLabel = status === 'done' ? 'Regenerate' : 'Generate';
  const genBtn = status === 'failed'
    ? `<button class="btn btn-failed" id="gen-${sp.id}" title="Click to see the error">&#9679; Failed</button>`
    : status === 'generating'
      ? `<button class="btn btn-generate btn-disabled" disabled>Generating&#8230;</button>`
      : `<button class="btn btn-generate" id="gen-${sp.id}">${genLabel}</button>`;
  const playing = currentPlayingId_ === sp.id && currentAudio_ && !currentAudio_.paused;
  card.innerHTML = `
    <div class="card-header">
      <span class="badge ${sp.kind === 'heading' ? 'badge-heading' : 'badge-speech'}" title="${escHtml(sp.model)}">${sp.kind === 'heading' ? `H${sp.level ?? ''}` : 'SPEECH'}</span>
      ${inRetail ? '<span class="retail-badge" title="In the retail sample">&#9671;</span>' : ''}
      <span class="status-dot status-${status}" title="${statusLabel(status)}"></span>
      <div class="card-actions">
        ${genBtn}
        ${rel ? `<button class="btn-play${playing ? ' playing' : ''}" id="play-${sp.id}" title="Play">${playing ? '&#9646;&#9646;' : '&#9654;'}</button>` : ''}
        ${rel ? `<button class="btn-reveal" id="reveal-${sp.id}" title="Show in Finder">File</button>` : ''}
      </div>
    </div>
    <div class="voice-line">
      <span class="voice-name" title="${escHtml(sp.voiceId)}">${escHtml(sp.cast)}${sp.voiceName ? ` · ${escHtml(sp.voiceName)}` : ''}</span>
    </div>
    <div class="params-line" title="${escHtml(shortModel(sp.model))}">
      stab ${sp.stability.toFixed(2)} · sim ${sp.similarity.toFixed(2)} · spd ${sp.speed.toFixed(2)}
    </div>
    ${change && status !== 'done' ? `<div class="change-line"><span class="change-badge">${escHtml(changeLabel(change))}</span></div>` : ''}`;

  const gen = card.querySelector(`#gen-${sp.id}`) as HTMLButtonElement | null;
  if (gen) {
    if (status === 'failed') {
      gen.addEventListener('click', (e) => { e.stopPropagation(); showErrorOverlay(errMsg); });
    } else {
      gen.addEventListener('click', async (e) => {
        e.stopPropagation();
        generating_.add(sp.id);
        gen.disabled = true;
        gen.textContent = 'Generating…';
        try {
          await invoke('generate_section', { sectionId: sp.id, quality: format });
          sectionErrors_.delete(sp.id);
        } catch (err) {
          sectionErrors_.set(sp.id, String(err));
        }
        generating_.delete(sp.id);
        renderBookView(root);
      });
    }
  }
  const play = card.querySelector(`#play-${sp.id}`) as HTMLButtonElement | null;
  if (play && rel) play.addEventListener('click', (e) => { e.stopPropagation(); playClip_(rel, sp.id); });
  const reveal = card.querySelector(`#reveal-${sp.id}`) as HTMLButtonElement | null;
  if (reveal && rel) reveal.addEventListener('click', (e) => {
    e.stopPropagation();
    invoke('reveal_in_finder', { filePath: rel }).catch(err => showErrorOverlay(String(err)));
  });
  return card;
}

// ---------------------------------------------------------------------------
// ACX panel — everything here is read-only except the two buttons
// ---------------------------------------------------------------------------

function renderAcxPanel_(master: Master, chapters: LoadedChapter[], root: HTMLElement): void {
  const panel = document.createElement('div');
  panel.className = 'acx-panel';
  const b = master.book;
  const quality = acxQuality_;
  const meta = document.createElement('div');
  meta.className = 'acx-metadata-form';
  const rows: Array<[string, string]> = [
    ['Title', b.title], ['Subtitle', b.subtitle], ['Author', b.author], ['Narrator', b.narrator],
    ['Publisher', b.publisher], ['Copyright', `${b.copyrightYear ?? ''} ${b.copyrightHolder}`.trim()],
    ['Language', b.language], ['Model', b.model], ['Dictionary',
      b.pronunciationDictionary ? `${b.pronunciationDictionary.name}${b.pronunciationDictionary.versionId ? '' : ' (no version)'}` : '(none)'],
  ];
  meta.innerHTML = `<div class="acx-metadata-grid">${rows.map(([k, v]) =>
    `<span class="acx-meta-label">${escHtml(k)}</span><span class="acx-meta-value${v ? '' : ' acx-card-missing'}">${escHtml(v || '(not set — authorlm manuscript set)')}</span>`).join('')}</div>`;
  panel.appendChild(meta);

  const controls = document.createElement('div');
  controls.className = 'acx-controls-row';
  controls.innerHTML = `
    <select id="acx-quality-sel" class="quality-select" title="ACX audio quality">
      <option value="mp3_44100_128"${quality === 'mp3_44100_128' ? ' selected' : ''}>Standard (128kbps)</option>
      <option value="mp3_44100_192"${quality === 'mp3_44100_192' ? ' selected' : ''}>High (192kbps)</option>
    </select>
    <button class="btn btn-secondary" id="acx-audit-btn">Structural Audit</button>
    <button class="btn btn-primary" id="acx-generate-btn">Generate ACX Package</button>`;
  panel.appendChild(controls);
  (controls.querySelector('#acx-quality-sel') as HTMLSelectElement).addEventListener('change', (e) => {
    acxQuality_ = (e.target as HTMLSelectElement).value;
    renderBookView(root);
  });

  const grid = document.createElement('div');
  grid.className = 'acx-special-grid';
  const special = (title: string, c: LoadedChapter | null | undefined) => {
    const g = document.createElement('div');
    g.className = 'acx-special-group';
    if (!c) {
      g.innerHTML = `<div class="acx-special-header"><span class="acx-card-title">${escHtml(title)}</span></div>
        <div class="acx-card-body acx-card-missing">Not in audiobook.json — 'authorlm audio export'.</div>`;
      return g;
    }
    const lc = withHelpers(c);
    const counts = pendingCount(lc, quality);
    const done = counts.generated === counts.total;
    g.innerHTML = `<div class="acx-special-header"><span class="acx-card-title">${escHtml(title)}</span>
        <span class="badge ${done ? 'badge-done' : 'badge-pending'}">${done ? '&#10003; Ready' : `${counts.generated}/${counts.total}`}</span></div>
      <div class="acx-card-body">${lc.sections().map(s => `<div class="acx-credit-text">${escHtml(s.text)}</div>`).join('')}</div>`;
    return g;
  };
  grid.appendChild(special('Opening Credits', master.openingCredits));
  grid.appendChild(special('Closing Credits', master.closingCredits));
  grid.appendChild(special('About the Author', master.aboutAuthor));
  panel.appendChild(grid);

  const retail = document.createElement('div');
  retail.className = 'acx-section-card';
  const texts = b.retailSample.map(id => {
    for (const c of chapters) {
      const s = c.chapter.sections.find(x => x.id === id);
      if (s && s.type === 'speech') return `${c.chapter.title} — ${s.text.slice(0, 70)}${s.text.length > 70 ? '…' : ''}`;
    }
    return `${id.slice(0, 8)}… (not in any chapter)`;
  });
  retail.innerHTML = `<div class="acx-card-header"><span class="acx-card-title">Retail Sample</span>
      ${texts.length ? `<span class="badge badge-done">${texts.length} section(s)</span>` : ''}</div>
    <div class="acx-card-body">${texts.length ? `<ul class="acx-retail-list">${texts.map(t => `<li class="acx-retail-ref">${escHtml(t)}</li>`).join('')}</ul>`
      : '<span class="acx-card-missing">None — set [retail_sample] in audiobook.toml.</span>'}</div>`;
  panel.appendChild(retail);

  const cover = document.createElement('div');
  cover.className = 'acx-section-card';
  const cv = b.cover;
  const dimOk = !!cv && cv.width >= 2400 && cv.height >= 2400 && cv.width === cv.height;
  cover.innerHTML = `<div class="acx-card-header"><span class="acx-card-title">Cover Image</span>
      ${cv ? `<span class="badge ${dimOk ? 'badge-done' : 'badge-error'}">${dimOk ? '&#10003; Valid' : '&#9888; Issues'}</span>` : ''}</div>
    <div class="acx-card-body">${cv ? `<span class="acx-cover-name">${escHtml(cv.path)}</span>
      <div class="acx-cover-specs"><span>${cv.width}x${cv.height}px</span><span>${escHtml(cv.format)}</span><span>${escHtml(cv.color)}</span></div>`
      : '<span class="acx-card-missing">None — set cover.path in audiobook.toml.</span>'}</div>`;
  panel.appendChild(cover);

  const progress = document.createElement('div');
  progress.className = 'acx-progress-line';
  progress.style.display = 'none';
  panel.appendChild(progress);
  root.appendChild(panel);

  controls.querySelector('#acx-audit-btn')!.addEventListener('click', async () => {
    try { showAuditOverlay_(await invoke<AuditResult[]>('run_acx_audit', { quality })); }
    catch (e) { showErrorOverlay(String(e)); }
  });
  controls.querySelector('#acx-generate-btn')!.addEventListener('click', async () => {
    const btn = controls.querySelector('#acx-generate-btn') as HTMLButtonElement;
    btn.disabled = true;
    btn.textContent = 'Generating…';
    progress.style.display = 'block';
    progress.textContent = 'Starting…';
    const { listen } = await import('@tauri-apps/api/event');
    const unlisten = await listen<{ message: string }>('acx-package-progress', (ev) => {
      progress.textContent = ev.payload.message;
    });
    try {
      const out: string = await invoke('generate_acx_package', { quality });
      unlisten();
      progress.textContent = `Package created: ${out}`;
      const reveal = document.createElement('button');
      reveal.className = 'btn btn-secondary';
      reveal.textContent = 'Reveal in Finder';
      reveal.style.marginLeft = '8px';
      reveal.addEventListener('click', () => invoke('reveal_in_finder', { filePath: out }).catch(e => showErrorOverlay(String(e))));
      progress.appendChild(reveal);
    } catch (e) {
      unlisten();
      progress.style.display = 'none';
      showErrorOverlay(String(e));
    }
    btn.disabled = false;
    btn.textContent = 'Generate ACX Package';
  });
}
