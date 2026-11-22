// Mix, don't interrupt (author's ask 2026-11-21): on iPhone a page that
// plays audio normally pauses whatever else is playing (YouTube, music).
// Safari's Audio Session API (iOS 16.4+) lets the page say its sound is
// "ambient": it plays alongside other audio instead of stopping it. Note
// that ambient sound follows the ring/silent switch. Browsers without the
// API ignore this and behave as before.
export function mixWithOtherAudio() {
  const nav = navigator as Navigator & { audioSession?: { type: string } };
  try { if (nav.audioSession) nav.audioSession.type = "ambient"; } catch { /* not settable here */ }
}
