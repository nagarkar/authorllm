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
    void ctx.resume();
  } catch { /* leave the element playing on its own */ }
}

export function installPageVolume() {
  document.addEventListener("play", e => {
    if (e.target instanceof HTMLMediaElement) route(e.target);
  }, true);
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
