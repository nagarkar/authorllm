// Phone typing (author's ask 2026-11-21): while a text box has focus on a
// narrow screen, the band (header) folds away, the box's top edge is pinned
// to the top of what is visible above the keyboard, and the box fills that
// space but for room for the buttons under it. It stays there as the
// keyboard opens and as the text grows; leaving the box restores the page.
const NARROW = "(max-width: 820px)";
const ROOM_BELOW = 64; // px kept under the box for Save / Cancel
let active: HTMLTextAreaElement | null = null;
let release = 0;

function fit() {
  if (!active) return;
  const vv = window.visualViewport;
  const height = vv ? vv.height : window.innerHeight;
  const top = vv ? vv.offsetTop : 0;
  active.style.height = `${Math.max(120, height - ROOM_BELOW - 12)}px`;
  const r = active.getBoundingClientRect();
  window.scrollBy(0, r.top - top - 6);
}

export function installTypingFocus() {
  const root = document.documentElement;
  document.addEventListener("focusin", e => {
    const t = e.target;
    if (!(t instanceof HTMLTextAreaElement) || !window.matchMedia(NARROW).matches) return;
    window.clearTimeout(release);
    if (active && active !== t) active.style.height = "";
    active = t;
    root.classList.add("typing");
    fit();
    // iOS opens the keyboard after focus and scrolls on its own; settle after it.
    window.setTimeout(fit, 350);
  });
  document.addEventListener("focusout", e => {
    if (e.target !== active) return;
    // Hold the layout a moment so a tap on Save lands where the finger was.
    release = window.setTimeout(() => {
      if (document.activeElement === active) return;
      if (active) active.style.height = "";
      active = null;
      root.classList.remove("typing");
    }, 400);
  });
  window.visualViewport?.addEventListener("resize", fit);
}
