import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Transport } from "./transport";

// Mirrors workbench.Session's replies (authorlm/workbench.py).
interface Row { term: string; say: string; note: string; tested?: boolean; }
interface Voice { key: string; voice_id: string; voice_name: string; model: string; stability: number; similarity: number; speed: number; }
interface Context { file: string; stem: string; cast: string; voice: Voice; sentence: string; section_id: string; }
interface State {
  manuscript: string; terms: Row[]; warnings: string[]; dictionary: string; model: string;
  phoneme_model: boolean; locator: {name: string; id?: string | null; versionId?: string | null} | null;
  cast: Record<string, string>;
}
type Mode = "respelling" | "default" | "dictionary";
interface Clip { key: string; spoken: string; say: string; cast: string; voice_name: string; file: string; chars: number; alias: string; mode: Mode; sentence: string; dictionary_version?: string | null; at?: string; audio_base64?: string; }
const norm = (s: string) => (s || "").split(/\s+/).filter(Boolean).join(" ");
const MODE_LABEL: Record<Mode, string> = {
  respelling: "read as written",
  default: "the voice's default — no rule at all",
  dictionary: "full dictionary plus current word",
};

export function Workbench({transport, manuscript, initialTerm}: {transport: Transport; manuscript?: string; initialTerm?: string}) {
  const [state, setState] = useState<State | null>(null);
  const [term, setTerm] = useState<string>("");
  const [say, setSay] = useState("");
  const [note, setNote] = useState("");
  const [contexts, setContexts] = useState<Context[]>([]);
  // The sentence each voice will read — the book's by default, editable
  // (a long one costs more and says no more), and the last one used is
  // remembered with the clip.
  const [sentences, setSentences] = useState<Record<string, string>>({});
  // Every clip ever rendered for this word (no audio), and the one shown
  // per voice: the clip whose key matches what is in the boxes now.
  const [records, setRecords] = useState<Clip[]>([]);
  const [clips, setClipsState] = useState<Record<string, Clip>>({});
  const clipsRef = useRef<Record<string, Clip>>({});
  const audioCache = useRef<Record<string, Clip>>({});
  function setClips(next: Record<string, Clip> | ((prev: Record<string, Clip>) => Record<string, Clip>)) {
    setClipsState((prev) => {
      const value = typeof next === "function" ? next(prev) : next;
      clipsRef.current = value;
      return value;
    });
  }
  const [dirty, setDirty] = useState<Record<string, Row>>({});
  const [newTerm, setNewTerm] = useState("");
  const [onlyUntested, setOnlyUntested] = useState(false);
  const [onlyEmpty, setOnlyEmpty] = useState(false);
  const [mode, setMode] = useState<Mode>("respelling");
  // Inline, not window.confirm(): embedded browsers (the Claude desktop
  // pane among them) suppress native dialogs and return false silently.
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [termStatus, setTermStatus] = useState<{text: string; kind?: string}>({text: ""});
  const [globalStatus, setGlobalStatus] = useState<{text: string; kind?: string}>({
    text: "",
  });

  const params = useCallback((extra: Record<string, unknown> = {}) => ({manuscript, ...extra}), [manuscript]);

  const loadState = useCallback(async () => {
    const s = await transport.request<State>("state", params());
    setState(s);
    return s;
  }, [transport, params]);

  useEffect(() => {
    loadState().catch((e) => setGlobalStatus({text: String(e.message || e), kind: "bad"}));
  }, [loadState]);

  // A tap on a card's respelling line opens that word here.
  const lastInitial = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (initialTerm && initialTerm !== lastInitial.current) {
      lastInitial.current = initialTerm;
      void selectTerm(initialTerm);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTerm]);

  const tableRow = useMemo(() => state?.terms.find((r) => r.term === term), [state, term]);

  // Picking two words in quick succession must not let the slower reply
  // land in the faster one's box: only the latest selection's reply is
  // applied.
  const selectSeq = useRef(0);

  // Autoplay: a clip plays the moment it lands; several (Regenerate all
  // voices) play in arrival order, never over one another. The <audio>
  // element for a clip is mounted by the render AFTER setClips, so the
  // arrival is noted here and the effect below starts playback once the
  // element exists.
  const audioEls = useRef<Record<string, HTMLAudioElement | null>>({});
  // Phones allow sound only from a tap. The tap that starts a render
  // plays a moment of silence through ONE hidden player, which unlocks
  // it; every clip the render returns then plays through that same
  // player, in arrival order, never over one another.
  const player = useRef<HTMLAudioElement | null>(null);
  const queue = useRef<string[]>([]);
  const playing = useRef(false);
  const SILENCE = "data:audio/mpeg;base64,//uQxAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAACcQCAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA//////////////////////////////////////////////////////////////////8AAABhTEFNRTMuMTAwA8MAAAAAAAAAABQgJAUHQQAB9AAAAnGMHkkIAAAAAAD/+0DEAAPAAAGkAAAAIAAANIAAAAQVTEFNRTMuMTAwVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV";

  function unlockPlayer() {
    const el = player.current;
    if (!el) return;
    el.src = SILENCE;
    el.play().catch(() => {});
  }

  function playNext() {
    const el = player.current;
    if (!el || playing.current) return;
    const src = queue.current.shift();
    if (!src) return;
    playing.current = true;
    el.onended = () => { playing.current = false; playNext(); };
    el.onerror = () => { playing.current = false; playNext(); };
    el.src = src;
    el.play().catch(() => { playing.current = false; playNext(); });
  }

  function playClip(clip: Clip) {
    if (!clip.audio_base64) return;
    queue.current.push(`data:audio/mpeg;base64,${clip.audio_base64}`);
    playNext();
  }

  // What is in the boxes → which stored clip each voice shows. Records
  // are newest first, so a re-take of the same key wins.
  useEffect(() => {
    let cancelled = false;
    const s = norm(say);
    const wanted: Record<string, Clip | undefined> = {};
    for (const c of contexts) {
      const sent = norm(sentences[c.cast] ?? c.sentence);
      wanted[c.cast] = records.find((r) =>
        r.cast === c.cast && r.mode === mode && norm(r.sentence) === sent &&
        (mode === "default" || norm(r.say) === s));
    }
    const current = clipsRef.current;
    const next: Record<string, Clip> = {};
    const toFetch: Clip[] = [];
    for (const c of contexts) {
      const w = wanted[c.cast];
      if (!w) continue;
      if (current[c.cast]?.key === w.key && current[c.cast]?.audio_base64) next[c.cast] = current[c.cast];
      else if (audioCache.current[w.key]) next[c.cast] = audioCache.current[w.key];
      else toFetch.push(w);
    }
    const same = Object.keys(next).length === Object.keys(current).length &&
      Object.keys(next).every((k) => current[k]?.key === next[k].key);
    if (!same) setClips(next);
    if (!toFetch.length) return;
    (async () => {
      for (const w of toFetch) {
        try {
          const full = await transport.request<Clip>("clip", params({key: w.key}));
          audioCache.current[w.key] = full;
          if (cancelled) return;
          setClips((prev) => ({...prev, [w.cast]: full}));
        } catch {
          // A clip whose file went missing simply does not show.
        }
      }
    })();
    return () => { cancelled = true; };
  }, [say, mode, sentences, records, contexts, transport, params]);

  function stopPlayback() {
    queue.current = [];
    if (player.current) player.current.pause();
    playing.current = false;
  }

  async function selectTerm(next: string) {
    const seq = ++selectSeq.current;
    stopPlayback();
    setTerm(next);
    setClips({});
    setSay("");
    setNote("");
    setConfirmRemove(false);
    setSentences({});
    if (!next) { setContexts([]); return; }
    setContexts([]);
    setTermStatus({text: "finding every voice that says it…"});
    try {
      const r = await transport.request<{say: string; contexts: Context[]; clips: Clip[]}>("contexts", params({term: next}));
      if (seq !== selectSeq.current) return;
      setContexts(r.contexts);
      const recs = r.clips || [];
      setRecords(recs);
      // The sentence box opens on the last sentence this voice read, if
      // any — the book's otherwise. The matching effect below then
      // brings back the clip for the active reading, if one exists.
      const initial: Record<string, string> = {};
      for (const c of r.contexts) initial[c.cast] = recs.find((x) => x.cast === c.cast)?.sentence || c.sentence;
      setSentences(initial);
      const d = dirty[next];
      setSay(d ? d.say : r.say || "");
      setNote(d ? d.note : state?.terms.find((t) => t.term === next)?.note || "");
      setTermStatus(r.contexts.length
        ? {text: `${r.contexts.length} voice(s) say it`}
        : {text: "not spoken anywhere in the audiobook — check the spelling", kind: "bad"});
    } catch (e) {
      if (seq !== selectSeq.current) return;
      setTermStatus({text: String((e as Error).message), kind: "bad"});
    }
  }

  function markDirty(nextSay: string, nextNote: string) {
    const s = nextSay.trim();
    const row = tableRow;
    // A new word stays staged even with no reading yet; an existing row
    // is staged only by a real, non-empty change (a blank never erases).
    const changed = !row || (!!s && (row.say !== s || (row.note || "") !== nextNote.trim()));
    setDirty((d) => {
      const copy = {...d};
      if (changed) copy[term] = {term, say: s, note: nextNote.trim()};
      else delete copy[term];
      return copy;
    });
  }

  async function regen(targets: Context[]) {
    const s = say.trim();
    if (mode === "respelling" && !s) { setTermStatus({text: "type a respelling first, or pick the voice's default to hear it with no rule", kind: "bad"}); return; }
    let chars = 0;
    stopPlayback();
    unlockPlayer();
    for (const c of targets) {
      setBusy(c.cast);
      setTermStatus({text: `rendering ${c.cast}…`});
      try {
        const clip = await transport.request<Clip>("render", params({term, say: s, context: {...c, sentence: sentences[c.cast] ?? c.sentence}, mode}));
        chars += clip.chars;
        audioCache.current[clip.key] = clip;
        const {audio_base64: _a, ...record} = clip;
        setRecords((prev) => [record as Clip, ...prev.filter((r) => r.key !== clip.key)]);
        setClips((prev) => ({...prev, [c.cast]: clip}));
        playClip(clip);
      } catch (e) {
        setBusy(null);
        setTermStatus({text: String((e as Error).message), kind: "bad"});
        return;
      }
    }
    setBusy(null);
    setTermStatus({
      text: `${chars} characters spent · ${mode === "respelling" ? `read as written: “${s}”` : MODE_LABEL[mode]}`,
      kind: "ok",
    });
    if (mode === "respelling") markDirty(s, note);
    // The dropdown's "(untested)" comes from the server's view of the
    // clips on disk; a render just changed that view.
    loadState().catch(() => {});
  }

  async function remove() {
    if (!tableRow) return;
    setConfirmRemove(false);
    setTermStatus({text: "removing…"});
    try {
      const r = await transport.request<{removed_row: boolean; removed_rule: boolean; version_id: string | null}>("remove", params({term}));
      setDirty((d) => { const copy = {...d}; delete copy[term]; return copy; });
      await loadState();
      const removed = term;
      await selectTerm("");
      setGlobalStatus({
        text: `removed “${removed}” from pronunciations.md` +
          (r.removed_rule ? ` and its rule from ElevenLabs (version ${r.version_id})` : " (no rule for it on ElevenLabs)"),
        kind: "ok",
      });
    } catch (e) {
      setTermStatus({text: String((e as Error).message), kind: "bad"});
    }
  }

  async function clear() {
    if (!tableRow) return;
    setTermStatus({text: "clearing…"});
    try {
      const r = await transport.request<{removed_rule: boolean; version_id: string | null}>("clear", params({term}));
      setDirty((d) => { const copy = {...d}; delete copy[term]; return copy; });
      setSay("");
      await loadState();
      setTermStatus({
        text: `reading cleared — “${term}” stays in the table with no reading` +
          (r.removed_rule ? `; its rule left ElevenLabs (version ${r.version_id})` : "") + ". The voice reads it its own way.",
        kind: "ok",
      });
    } catch (e) {
      setTermStatus({text: String((e as Error).message), kind: "bad"});
    }
  }

  async function addTerm() {
    const t = newTerm.trim();
    if (!t) return;
    try {
      const r = await transport.request<{contexts: Context[]}>("contexts", params({term: t}));
      if (!r.contexts.length) { setGlobalStatus({text: `“${t}” is not spoken anywhere in the audiobook`, kind: "bad"}); return; }
      setDirty((d) => ({...d, [t]: {term: t, say: "", note: ""}}));
      setNewTerm("");
      await selectTerm(t);
    } catch (e) {
      setGlobalStatus({text: String((e as Error).message), kind: "bad"});
    }
  }

  async function suggest() {
    setTermStatus({text: "asking…"});
    try {
      const r = await transport.request<{say: string | null; reason: string | null}>("suggest", params({term, sentence: contexts[0]?.sentence || ""}));
      if (r.say) { setSay(r.say); markDirty(r.say, note); setTermStatus({text: "suggested — regenerate to hear it"}); }
      else setTermStatus({text: r.reason || "no suggestion", kind: "bad"});
    } catch (e) {
      setTermStatus({text: String((e as Error).message), kind: "bad"});
    }
  }

  async function saveRows(rows: Row[]) {
    try {
      const r = await transport.request<{count: number; skipped: string[]}>("save", params({rows}));
      setDirty((d) => { const copy = {...d}; for (const row of rows) delete copy[row.term]; return copy; });
      await loadState();
      const text = `saved ${r.count} row(s) to pronunciations.md — Push when the readings are settled` +
        (r.skipped.length ? `\n(${r.skipped.join(", ")}: no reading typed, existing row kept)` : "");
      setGlobalStatus({text, kind: "ok"});
      setTermStatus({text: rows.some((row) => row.term === term) ? "saved to the table — Push when settled" : termStatus.text, kind: "ok"});
    } catch (e) {
      setGlobalStatus({text: String((e as Error).message), kind: "bad"});
    }
  }

  function save() { return saveRows(Object.values(dirty)); }

  async function push() {
    setGlobalStatus({text: "pushing…"});
    try {
      const r = await transport.request<{plan: string[]; applied: boolean; version_id?: string; added?: number; changed?: number}>("push", params());
      setGlobalStatus({
        text: r.plan.join("\n") + (r.applied ? `\n→ version ${r.version_id}: ${r.added} added, ${r.changed} changed — the export follows on its own` : "\n→ nothing to push"),
        kind: "ok",
      });
      await loadState();
    } catch (e) {
      setGlobalStatus({text: String((e as Error).message), kind: "bad"});
    }
  }

  const dirtyNames = Object.keys(dirty);
  const loc = state?.locator;
  // Every word in the table plus the ones staged as new, alphabetical
  // (case-insensitive), narrowed by whatever is in the find box.
  const words = useMemo(() => {
    const rows: {term: string; say: string; tested?: boolean; isNew?: boolean}[] = [...(state?.terms || [])];
    for (const t of dirtyNames) if (!rows.some((r) => r.term === t)) rows.push({term: t, say: "", isNew: true});
    rows.sort((a, b) => a.term.localeCompare(b.term, undefined, {sensitivity: "base"}));
    const q = newTerm.trim().toLowerCase();
    let out = q ? rows.filter((r) => r.term.toLowerCase().includes(q)) : rows;
    if (onlyUntested || onlyEmpty) {
      out = out.filter((r) => (onlyUntested && !!r.say && !r.tested) || (onlyEmpty && !r.say));
    }
    // The word on screen stays pickable even when a filter would hide it.
    if (term && !out.some((r) => r.term === term)) {
      const cur = rows.find((r) => r.term === term);
      if (cur) out = [cur, ...out];
    }
    return out;
  }, [state, dirtyNames, newTerm, onlyUntested, onlyEmpty, term]);

  return (
    <div className="wb">
      <audio ref={player} preload="auto" style={{display: "none"}} />
      <section className="picker">
      <h2 className="picker-title">Find a word</h2>
      <div className="bar">
        <input type="text" className="find" value={newTerm} placeholder="filter the list, or type a new word from the book…"
          onChange={(e) => setNewTerm(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { const hit = words.find((w) => w.term.toLowerCase() === newTerm.trim().toLowerCase()); if (hit) void selectTerm(hit.term); else void addTerm(); } }} />
        {newTerm.trim() && !words.some((w) => w.term.toLowerCase() === newTerm.trim().toLowerCase()) &&
          <button onClick={() => void addTerm()}>Add “{newTerm.trim()}”</button>}
        <span className="wb-filters">
          <label className="wb-check"><input type="checkbox" checked={onlyUntested} onChange={(e) => setOnlyUntested(e.target.checked)} /> untested</label>
          <label className="wb-check"><input type="checkbox" checked={onlyEmpty} onChange={(e) => setOnlyEmpty(e.target.checked)} /> ★ no reading</label>
        </span>
        <select className="wordpick" value={term} onChange={(e) => void selectTerm(e.target.value)} title="Every row of pronunciations.md, A to Z; ★ = no reading yet">
          <option value="">— pick a word ({words.length}) —</option>
          {words.map((w) => (
            <option key={w.term} value={w.term}>{!w.say ? "★ " : ""}{w.term}{w.isNew ? " (new)" : (w.say && !w.tested) ? " (untested)" : ""}</option>
          ))}
        </select>
        <span className={"wb-status " + (loc?.versionId ? "ok" : "bad")}>
          {state?.dictionary
            ? `dictionary “${state.dictionary}”` + (loc?.versionId ? ` · version ${loc.versionId.slice(0, 8)}… in the last export` : " · not pushed yet")
            : state ? "no dictionary named in audiobook.toml" : ""}
        </span>
      </div>
      </section>

      {term && (
        <div className="card">
          <div className="bar">
            <strong>{term}</strong>
            <button className="primary" disabled={!dirty[term]} onClick={() => void saveRows([dirty[term]])}>
              {tableRow ? "Save this reading" : "Add to the table"}
            </button>
            {tableRow?.say && <button className="clear" disabled={!!busy} onClick={() => void clear()} title="Keep the word, drop its reading and its ElevenLabs rule">Clear reading</button>}
            {tableRow && !confirmRemove && <button className="danger" disabled={!!busy} onClick={() => setConfirmRemove(true)} title="Drop the word and its rule">Delete word</button>}
            <input type="text" className="say" value={say} placeholder="respelling — stressed syllable in CAPS, e.g. buh-SIL-ih-deez"
              onChange={(e) => { setSay(e.target.value); markDirty(e.target.value, note); }} />
            {say.trim() && <span className="alias">read as written: “{say.trim()}”</span>}
            <button onClick={() => void suggest()} title="Ask the configured LLM for a respelling">Suggest</button>
            <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} title="What Regenerate renders">
              {(Object.keys(MODE_LABEL) as Mode[]).map((m) => <option key={m} value={m}>{MODE_LABEL[m]}</option>)}
            </select>
            <button className="primary" disabled={!!busy || !contexts.length} onClick={() => void regen(contexts)}>▶ Regenerate all voices</button>
          </div>
          {tableRow && confirmRemove && (
            <div className="bar confirm">
              <span>Delete “{term}” from pronunciations.md? The word leaves the list and its ElevenLabs rule goes with it; the voice reads it its own way. To keep the word listed without a reading, use Clear reading instead.</span>
              <button className="danger" disabled={!!busy} onClick={() => void remove()}>Yes, delete</button>
              <button onClick={() => setConfirmRemove(false)}>Keep it</button>
            </div>
          )}
          <div className="bar">
            <input type="text" className="note" value={note} placeholder="note (optional) — e.g. Greek; stress on the second syllable"
              onChange={(e) => { setNote(e.target.value); markDirty(say, e.target.value); }} />
          </div>
          <div className="bar table-line">
            <span className="small">
              {tableRow
                ? (tableRow.say ? <>in the table: <span className="alias">“{tableRow.say}”</span></> : "in the table with no reading yet")
                : "not in the table yet"}
              {dirty[term] && (dirty[term].say !== (tableRow?.say || "") || (dirty[term].note || "") !== (tableRow?.note || ""))
                && <> · unsaved: <span className="alias">“{dirty[term].say || "(no reading)"}”</span></>}
            </span>
          </div>
          {contexts.map((c) => {
            const clip = clips[c.cast];
            const takes = records.filter((r) => r.cast === c.cast).length;
            return (
              <div className="row" key={c.cast}>
                <div>
                  <div className="cast">{c.cast}</div>
                  <div className="voice">{c.voice.voice_name || c.voice.voice_id} · stab {c.voice.stability.toFixed(2)} spd {c.voice.speed.toFixed(2)}</div>
                  <div className="file">{c.file}</div>
                  {takes > 0 && <div className="file">{takes} clip(s) on disk{clip ? "" : " · none for these boxes"}</div>}
                </div>
                <div>
                  <textarea className="sentence" rows={2} value={sentences[c.cast] ?? c.sentence}
                    title="What this voice will read — shorten it if you like; keep the word in it"
                    onChange={(e) => setSentences((prev) => ({...prev, [c.cast]: e.target.value}))} />
                  <div className="small">
                    {(sentences[c.cast] ?? c.sentence).length} characters
                    {(sentences[c.cast] ?? c.sentence) !== c.sentence && (
                      <> · <a href="#" onClick={(e) => { e.preventDefault(); setSentences((prev) => ({...prev, [c.cast]: c.sentence})); }}>restore the book's sentence</a></>
                    )}
                  </div>
                  {clip && (
                    <>
                      <div className="spoken">heard ({MODE_LABEL[clip.mode]}{clip.at ? `, ${clip.at}` : ""}{clip.dictionary_version ? `, dictionary ${clip.dictionary_version.slice(0, 8)}…` : ""}): “{clip.spoken}”</div>
                      <audio controls preload="auto" ref={(el) => { audioEls.current[c.cast] = el; }} src={`data:audio/mpeg;base64,${clip.audio_base64}`} />
                    </>
                  )}
                </div>
                <div>
                  <button className="regen" disabled={!!busy} onClick={() => void regen([c])}>{busy === c.cast ? "Rendering…" : "▶ Regenerate"}</button>
                </div>
              </div>
            );
          })}
          <div className={"status " + (termStatus.kind || "")}>{termStatus.text}</div>
        </div>
      )}

      <footer className="wb-footer">
        {globalStatus.text && <div className={"status " + (globalStatus.kind || "")}>{globalStatus.text}</div>}
        {state?.phoneme_model && <div className="status bad">This model takes phoneme rules; the workbench tests respellings as aliases.</div>}
        {!!state?.warnings.length && <div className="status bad">{state.warnings.join("\n")}</div>}
        <div className="actions">
          {dirtyNames.length > 0 && <button className="primary" onClick={() => void save()}>Save {dirtyNames.length} unsaved</button>}
          <button className="push" onClick={() => void push()}>Push to ElevenLabs</button>
          {dirtyNames.length > 0 && <span className="dirty">{dirtyNames.join(", ")}</span>}
        </div>
      </footer>
    </div>
  );
}
