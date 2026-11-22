import { useEffect, useState } from "react";

// The voice the Listen buttons read in, chosen once per page and remembered
// in this browser. Empty means the manuscript's preview voice
// (audiobook.toml [preview] voice), which this choice never changes.
const KEY = "authorlm.listenVoice";
const EVENT = "authorlm-listen-voice";

export function listenVoice(): string {
  try { return localStorage.getItem(KEY) || ""; } catch { return ""; }
}

// The manuscript the page has open: the first part of its hash (#SMSTTD/forms).
export function pageManuscript(): string {
  try { return decodeURIComponent(location.hash.replace(/^#/, "").split("/")[0] || ""); } catch { return ""; }
}

function setListenVoice(voice: string) {
  try { if (voice) localStorage.setItem(KEY, voice); else localStorage.removeItem(KEY); } catch { /* private window */ }
  window.dispatchEvent(new Event(EVENT));
}

export function VoicePicker({ manuscript }: { manuscript: string }) {
  const [voices, setVoices] = useState<{ current: string; choices: string[]; missing: string[] } | null>(null);
  const [chosen, setChosen] = useState(listenVoice());
  useEffect(() => {
    let live = true;
    fetch(`/api/voices?m=${encodeURIComponent(manuscript)}`).then(r => r.json()).then(v => { if (live && v.choices) setVoices(v); }).catch(() => {});
    return () => { live = false; };
  }, [manuscript]);
  useEffect(() => {
    const on = () => setChosen(listenVoice());
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  if (!voices) return null;
  const value = chosen && voices.choices.includes(chosen) ? chosen : voices.current;
  return (
    <label className="listen-voice muted">Listen voice{" "}
      <select value={value} aria-label="Voice for Listen" onChange={e => setListenVoice(e.target.value === voices.current ? "" : e.target.value)}>
        {voices.choices.map(v => {
          const missing = voices.missing.includes(v);
          return <option key={v} value={v} disabled={missing}>{v}{v === voices.current ? " (preview voice)" : ""}{missing ? " — not installed" : ""}</option>;
        })}
      </select>
    </label>
  );
}
