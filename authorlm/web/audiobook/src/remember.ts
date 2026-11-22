// Small per-browser memory for page choices (author's rule 2026-11-21:
// what is picked in a drop-down survives a reload). Each origin keeps its
// own (localhost and the tailnet name are separate). Never throws.
export function recall(key: string): string {
  try { return localStorage.getItem(key) || ""; } catch { return ""; }
}

export function remember(key: string, value: string): void {
  try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); } catch { /* storage off */ }
}
