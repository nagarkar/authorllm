import { useEffect, useState } from "react";

// The page's own volume (author's ask 2026-11-21): turn the phone down for
// YouTube and still hear Listen at a normal level. iPhone Safari ignores an
// audio element's volume, so every <audio> the page plays is routed, on its
// first play, through one Web Audio gain node; the slider sets that gain,
// up to 4x (a boost over the system volume), remembered in this browser.
const KEY = "authorlm.pageVolume";
const EVENT = "authorlm-page-volume";
const MAX = 4;
let ctx: AudioContext | null = null;
let gain: GainNode | null = null;
const routed = new WeakSet<HTMLMediaElement>();
const live = new Set<HTMLMediaElement>();      // routed elements, for pause/resume
let heldForBackground: HTMLMediaElement[] = [];

export function pageVolume(): number {
  try { const v = parseFloat(localStorage.getItem(KEY) || ""); return isFinite(v) ? Math.min(MAX, Math.max(0, v)) : 1; } catch { return 1; }
}

function setPageVolume(v: number) {
  try { localStorage.setItem(KEY, String(v)); } catch { /* private window */ }
  if (gain && ctx) gain.gain.setTargetAtTime(v, ctx.currentTime, 0.02);
  window.dispatchEvent(new Event(EVENT));
}

function route(el: HTMLMediaElement) {
  if (routed.has(el)) { void ctx?.resume(); return; }
  try {
    const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    if (!ctx) {
      ctx = new AC();
      gain = ctx.createGain();
      gain.gain.value = pageVolume();
      gain.connect(ctx.destination);
    }
    ctx.createMediaElementSource(el).connect(gain!);
    routed.add(el);
    live.add(el);
    void ctx.resume();
  } catch { /* leave the element playing on its own */ }
}

// Leaving the page (bug 2026-11-21): iPhone Safari stops a page's Web Audio
// when the page goes to the background, but the <audio> elements kept
// "playing" into it in silence, so the walk moved on paragraph by paragraph
// with no sound and stayed silent on return. Now what is playing is paused
// when the page is hidden and played again, from where it stopped, when the
// page comes back; if Safari will not restart it there, it stays paused.
function holdForBackground() {
  heldForBackground = [...live].filter(el => el.isConnected && !el.paused && !el.ended);
  heldForBackground.forEach(el => el.pause());
}

async function resumeFromBackground() {
  const held = heldForBackground;
  heldForBackground = [];
  try { await ctx?.resume(); } catch { /* resumes on the next tap */ }
  for (const el of held) {
    if (el.isConnected) { try { await el.play(); } catch { /* stays paused */ } }
  }
}

export function installPageVolume() {
  document.addEventListener("play", e => {
    if (e.target instanceof HTMLMediaElement) route(e.target);
  }, true);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") holdForBackground();
    else void resumeFromBackground();
  });
  window.addEventListener("pagehide", holdForBackground);
  window.addEventListener("pageshow", () => void resumeFromBackground());
}

export function VolumeSlider() {
  const [v, setV] = useState(pageVolume());
  useEffect(() => {
    const on = () => setV(pageVolume());
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return (
    <label className="page-volume" title="Volume of this page's audio, apart from the phone's volume">
      <span aria-hidden="true">🔊</span>
      <input type="range" min={0} max={MAX} step={0.05} value={v} aria-label="Page volume"
        onChange={e => setPageVolume(parseFloat(e.target.value))} />
      <span className="count">{Math.round(v * 100)}%</span>
    </label>
  );
}
