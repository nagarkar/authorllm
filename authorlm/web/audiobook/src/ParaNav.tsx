import { useEffect, useRef, useState } from "react";
import { recall, remember } from "./remember";

// Paragraph stepping in the audiobook view (author's ask 2026-11-21): ▲ and
// ▼ move one paragraph, and a numbered picker jumps to any. The card at the
// top of the view (just under the sticky header) is "current".
function cards(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("article.card[id^='card-']"));
}

function headerBottom(): number {
  const h = document.querySelector("header");
  return h ? h.getBoundingClientRect().bottom : 0;
}

function currentIndex(list: HTMLElement[]): number {
  const top = headerBottom() + 4;
  const i = list.findIndex(c => c.getBoundingClientRect().bottom > top);
  return i < 0 ? list.length - 1 : i;
}

function goTo(list: HTMLElement[], i: number): number {
  const at = Math.max(0, Math.min(list.length - 1, i));
  const c = list[at];
  if (c) window.scrollTo({top: window.scrollY + c.getBoundingClientRect().top - headerBottom() - 8});
  return at;
}

export function ParaNav({ count, storeKey }: { count: number; storeKey: string }) {
  const [at, setAt] = useState(0);
  // The paragraph you were at comes back after a reload (author's rule
  // 2026-11-21): the view returns to it once the chapter's cards are drawn.
  const restored = useRef("");
  useEffect(() => {
    if (!count || restored.current === storeKey) return;
    restored.current = storeKey;
    const saved = parseInt(recall(storeKey), 10);
    if (saved > 0) window.setTimeout(() => setAt(goTo(cards(), saved)), 50);
  }, [count, storeKey]);
  useEffect(() => { if (restored.current === storeKey) remember(storeKey, String(at)); }, [at, storeKey]);
  useEffect(() => {
    let timer = 0;
    const on = () => { window.clearTimeout(timer); timer = window.setTimeout(() => setAt(currentIndex(cards())), 60); };
    window.addEventListener("scroll", on, {passive: true});
    on();
    return () => { window.removeEventListener("scroll", on); window.clearTimeout(timer); };
  }, [count]);
  if (count < 2) return null;
  return (
    <span className="para-nav">
      <button className="chip" aria-label="Previous paragraph" onClick={() => { const l = cards(); setAt(goTo(l, currentIndex(l) - 1)); }}>▲</button>
      <select className="para-pick" value={at} aria-label="Jump to paragraph"
        onChange={e => setAt(goTo(cards(), parseInt(e.target.value, 10)))}>
        {Array.from({length: count}, (_, i) => <option key={i} value={i}>¶ {i + 1}</option>)}
      </select>
      <button className="chip" aria-label="Next paragraph" onClick={() => { const l = cards(); setAt(goTo(l, currentIndex(l) + 1)); }}>▼</button>
    </span>
  );
}
