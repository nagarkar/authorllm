import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Transport } from "./transport";
import { recall, remember } from "./remember";
import { Lint } from "./Lint";

// Mirrors runs_page.dispatch (authorlm/runs_page.py): every filter and lens
// the manuscript sees, the text of one of them (editable, saved back to its
// file), and requests for runs on chosen tabs.
// The page calls no model. "Ask for runs" writes one request per (filter or
// lens, tab); a Claude Code session carries them out (`authorlm requests`),
// and each request's state here is read from what its run has recorded.
//
// Where each choice lives (the owner's rule: every choice survives a reload):
//   URL hash      the manuscript and the open filter or lens
//                 (#<manuscript>/runs:<kind>:<name>, kept by App.tsx): they
//                 name what is being looked at, worth a bookmark or a link.
//                 The Lint view is #<manuscript>/runs:lint[:<rule>[:<file>]]
//                 (Lint.tsx), for the same reason.
//   localStorage  per manuscript: the ticked filters and lenses, the ticked
//                 tabs, the find box and the scroll position (personal
//                 working state, of no use in a shared link); per artifact:
//                 the unsaved draft of its text with the hash of the file it
//                 was started from, so a reload never loses typing.
type Kind = "filter" | "lens";
interface Artifact {
  kind: Kind; name: string; class: string | null; summary: string; error: string | null;
  source: "manuscript" | "library"; path: string; runs: number;
  last: null | { date: string; file: string; status?: string; proposed?: number; accepted?: number; rejected?: number; forms_out?: number; findings?: number };
}
interface Tab {
  file: string; title: string; words: number; units: number; checked_out: boolean;
  forms_out: Record<string, number>; protected: boolean; owner_edits?: string | null; url: string | null; blocked: string | null; needs: string | null;
}
interface Request {
  id: string; kind: Kind; name: string; file: string; asked_at: string; state: string; detail: string;
  url?: string | null; commands: string[];
}
interface Catalog {
  manuscript: string; filters: Artifact[]; lenses: Artifact[]; tabs: Tab[]; requests: Request[];
  drafting: Record<Kind, { road: string; native_model: string | null; note: string }>;
}
interface Shown {
  kind: Kind; name: string; source: "manuscript" | "library"; path: string; text: string; sha: string;
  error: string | null; recovery: string; backup?: string | null; warnings?: string[]; changed?: boolean;
}
interface Pair { kind: Kind; name: string; file: string; reason?: string }
interface Asked { queued: Pair[]; already: Pair[]; blocked: Pair[]; requests: Request[]; error?: string }

const STATE_WORDS: Record<string, string> = {
  queued: "waiting for a session", drafting: "being drafted", staged: "forms staged",
  in_doc: "forms in the tab", findings: "findings registered", clean: "no proposals",
  settled: "settled", abandoned: "abandoned",
};
const WAITING = new Set(["queued", "drafting"]);
const keyOf = (a: { kind: Kind; name: string }) => `${a.kind}:${a.name}`;
const readList = (key: string): string[] => {
  try { const v = JSON.parse(recall(key) || "[]"); return Array.isArray(v) ? v.map(String) : []; } catch { return []; }
};

function lastLine(a: Artifact): string {
  if (!a.last) return "never run on this manuscript";
  const where = `${a.last.file.replace(/\.md$/, "")}, ${a.last.date}`;
  if (a.kind === "lens") return `${a.runs} run${a.runs === 1 ? "" : "s"} · last: ${where}, ${a.last.findings} finding(s)`;
  return `${a.runs} run${a.runs === 1 ? "" : "s"} · last: ${where}, ${a.last.status}, ${a.last.accepted} accepted / ${a.last.rejected} declined of ${a.last.proposed}`;
}

export function FiltersLenses({ transport, manuscript, open, onOpen }: {
  transport: Transport; manuscript: string; open: string; onOpen: (key: string) => void;
}) {
  const store = `authorlm.runs.${manuscript}`;
  // The Lint view shares this page, its tabs and their saved ticks.
  const isLint = open === "lint" || open.startsWith("lint:");
  const [cat, setCat] = useState<Catalog | null>(null);
  const [error, setError] = useState("");
  const [find, setFind] = useState(() => recall(`${store}.find`));
  const [picked, setPicked] = useState<string[]>(() => readList(`${store}.picked`));
  const [tabs, setTabs] = useState<string[]>(() => readList(`${store}.tabs`));
  const [shown, setShown] = useState<Shown | null>(null);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<{ bad: boolean; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [review, setReview] = useState<Asked | null>(null);
  const [asked, setAsked] = useState<Asked | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const c = await transport.request<Catalog & { error?: string }>("catalog", {});
      if (c.error) { setError(c.error); return; }
      setCat(c); setError("");
    } catch (err) { setError((err as Error).message); }
  }, [transport]);
  useEffect(() => {
    void load();
    // Requests move when a session works on them: read again every 20 s,
    // and when the page comes back to the front.
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 20000);
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [load]);

  useEffect(() => { remember(`${store}.find`, find); }, [store, find]);
  useEffect(() => { remember(`${store}.picked`, picked.length ? JSON.stringify(picked) : ""); }, [store, picked]);
  useEffect(() => { remember(`${store}.tabs`, tabs.length ? JSON.stringify(tabs) : ""); }, [store, tabs]);

  // The scroll position, restored once the lists have drawn.
  const restored = useRef(false);
  useEffect(() => {
    if (!cat || restored.current) return;
    restored.current = true;
    const y = Number(recall(`${store}.scroll`) || 0);
    if (y > 0) window.requestAnimationFrame(() => window.scrollTo({ top: y }));
    // A link to one filter or lens (no place remembered yet) opens on it.
    else if (open) window.requestAnimationFrame(() => document.querySelector(".runs-item.on")?.scrollIntoView({ block: "center" }));
  }, [cat, store, open]);
  useEffect(() => {
    let timer = 0;
    const onScroll = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => { if (restored.current) remember(`${store}.scroll`, String(Math.round(window.scrollY))); }, 250);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.clearTimeout(timer); window.removeEventListener("scroll", onScroll); };
  }, [store]);

  // The open artifact's text. An unsaved draft from before a reload comes
  // back when the file is still the one it was started from; when the file
  // has changed since, the draft is still shown, with a warning.
  const draftKey = open ? `${store}.draft.${open}` : "";
  useEffect(() => {
    setShown(null); setNote(null);
    if (!open || isLint) return;
    const [kind, name] = open.split(":");
    let live = true;
    transport.request<Shown & { error?: string }>("show", { kind, name }).then(s => {
      if (!live) return;
      if (!s.text && s.error) { setNote({ bad: true, text: s.error }); return; }
      setShown(s);
      let kept: { base: string; text: string } | null = null;
      try { kept = JSON.parse(recall(`${store}.draft.${open}`) || "null"); } catch { kept = null; }
      if (kept && typeof kept.text === "string" && kept.text !== s.text) {
        setDraft(kept.text);
        setNote(kept.base === s.sha
          ? { bad: false, text: "Your unsaved edit from before the reload is back. Save it or revert." }
          : { bad: true, text: "Your unsaved edit is back, but the file has changed on disk since you started it. Copy what you need, then Revert to see the file as it is now." });
      } else setDraft(s.text);
    }).catch(err => { if (live) setNote({ bad: true, text: (err as Error).message }); });
    return () => { live = false; };
  }, [open, store, transport]);
  const dirty = !!shown && draft !== shown.text;
  useEffect(() => {
    if (!shown || !draftKey) return;
    remember(draftKey, dirty ? JSON.stringify({ base: shown.sha, text: draft }) : "");
  }, [draft, dirty, shown, draftKey]);
  // A tab closed with unsaved text asks first (the draft is kept either way).
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async (to?: "manuscript") => {
    if (!shown) return;
    setSaving(true);
    try {
      const out = await transport.request<Shown & { error?: string }>("save",
        { kind: shown.kind, name: shown.name, text: draft, seen: shown.sha, ...(to ? { to } : {}) });
      if (out.error) { setNote({ bad: true, text: `Not saved. ${out.error}` }); return; }
      setShown(out); setDraft(out.text);
      setNote({ bad: false, text: `Saved to ${out.path} at ${new Date().toLocaleTimeString()}.`
        + (out.backup ? ` The text it replaced is at ${out.backup}.` : "")
        + (out.warnings?.length ? ` ${out.warnings.join(" ")}` : "") });
      void load();
    } catch (err) { setNote({ bad: true, text: `Not saved. ${(err as Error).message}` }); }
    finally { setSaving(false); }
  };

  const all = useMemo(() => cat ? [...cat.filters, ...cat.lenses] : [], [cat]);
  const groups = useMemo(() => {
    const q = find.trim().toLowerCase();
    const hit = (a: Artifact) => !q || `${a.name} ${a.summary} ${a.class || ""} ${a.source}`.toLowerCase().includes(q);
    const out: { title: string; items: Artifact[] }[] = [];
    const add = (title: string, items: Artifact[]) => { if (items.length) out.push({ title, items }); };
    if (cat) {
      for (const klass of ["sequential", "global"])
        add(`Filters · ${klass}`, cat.filters.filter(a => a.class === klass && hit(a)));
      add("Filters · not parsing", cat.filters.filter(a => !a.class && hit(a)));
      for (const klass of ["chapter", "cross-chapter"])
        add(`Lenses · ${klass}`, cat.lenses.filter(a => a.class === klass && hit(a)));
      add("Lenses · not parsing", cat.lenses.filter(a => !a.class && hit(a)));
    }
    return out;
  }, [cat, find]);

  const pickedItems = all.filter(a => picked.includes(keyOf(a)));
  const pickedTabs = cat ? cat.tabs.filter(t => tabs.includes(t.file)) : [];
  const params = () => ({ items: pickedItems.map(a => ({ kind: a.kind, name: a.name })), files: pickedTabs.map(t => t.file) });
  const toggle = (list: string[], set: (v: string[]) => void, key: string) => {
    set(list.includes(key) ? list.filter(k => k !== key) : [...list, key]);
    setReview(null); setAsked(null);
  };
  const preview = async () => {
    setBusy(true); setAsked(null);
    try {
      const out = await transport.request<Asked>("preview", params());
      if (out.error) setError(out.error); else { setReview(out); setError(""); }
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  const ask = async () => {
    setBusy(true);
    try {
      const out = await transport.request<Asked>("queue", params());
      if (out.error) setError(out.error); else { setAsked(out); setReview(null); setError(""); await load(); }
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  };
  const cancel = async (id: string) => {
    try { await transport.request("cancel", { id }); await load(); } catch (err) { setError((err as Error).message); }
  };

  if (!cat) return <div className="card muted">{error || "Reading the filters and lenses…"}</div>;
  const views = (
    <div className="devedit-actions runs-views" role="tablist">
      <button className={`pill${isLint ? "" : " on"}`} role="tab" aria-selected={!isLint} onClick={() => onOpen("")}>Filters and lenses</button>
      <button className={`pill${isLint ? " on" : ""}`} role="tab" aria-selected={isLint} onClick={() => onOpen("lint")}>Lint</button>
    </div>
  );
  if (isLint) return (
    <div className="runs">
      {views}
      <Lint transport={transport} manuscript={manuscript} tabs={cat.tabs} ticked={tabs} setTicked={setTabs}
        filter={open.slice(5)} onFilter={f => onOpen(f ? `lint:${f}` : "lint")} />
    </div>
  );
  const waiting = cat.requests.filter(r => WAITING.has(r.state));
  const openArtifact = all.find(a => keyOf(a) === open);
  const pairLine = (p: Pair) => `${p.kind} ${p.name} on ${p.file.replace(/\.md$/, "")}`;

  // The open filter or lens is edited where it sits in the list, under its
  // own row; when the find box hides that row, under the list.
  const openListed = groups.some(g => g.items.some(a => keyOf(a) === open));
  const editor = open ? (
    <section className="runs-editor">
        <div className="card-head"><span>{open.split(":")[0]}</span>
          <span className="runs-name">{open.split(":")[1]}</span>
          {dirty && <span className="badge runs-dirty">unsaved changes</span>}
          <button className="pill runs-close" onClick={() => onOpen("")}>Close</button></div>
        {shown && <p className="muted runs-path">{shown.path}</p>}
        {note && <div className={note.bad ? "moved bad-note" : "moved"}>{note.text}</div>}
        {shown && <>
          <textarea className="md-raw runs-text" value={draft} spellCheck={false}
            aria-label={`Text of the ${shown.kind} ${shown.name}`} onChange={e => setDraft(e.target.value)} />
          <div className="devedit-actions">
            <button className="btn take" disabled={!dirty || saving} onClick={() => void save()}>
              {saving ? "Saving…" : shown.source === "library" ? "Save to the library (every book)" : "Save"}</button>
            {shown.source === "library" && <button className="btn" disabled={!dirty || saving} onClick={() => void save("manuscript")}
              title="Writes this manuscript's own copy, which then wins over the library's for this book only">Save as this book's own</button>}
            <button className="btn plain" disabled={!dirty || saving} onClick={() => { setDraft(shown.text); setNote(null); }}>Revert</button>
          </div>
          <p className="muted">The text is checked before it is written: one that does not parse is not saved. {shown.recovery}</p>
        </>}
        {!shown && !note && <p className="muted">Reading {openArtifact?.name || "the file"}…</p>}
    </section>
  ) : null;

  return (
    <div className="runs">
      {views}
      {error && <div className="status bad">{error}</div>}

      <section className="card">
        <div className="card-head"><span>Filters and lenses</span>
          <span className="badge">{cat.filters.length} filters</span><span className="badge">{cat.lenses.length} lenses</span></div>
        <input className="runs-find" type="search" value={find} placeholder="Find by name, purpose or class"
          aria-label="Find a filter or lens" onChange={e => setFind(e.target.value)} />
        {groups.length === 0 && <p className="muted">Nothing matches.</p>}
        {groups.map(g => (
          <div key={g.title} className="runs-group">
            <h3 className="runs-group-title">{g.title}</h3>
            {g.items.map(a => {
              const k = keyOf(a);
              return (
                <div key={k} className={`runs-item${open === k ? " on" : ""}`}>
                  <label className="runs-tick" title="Tick to run it on the tabs chosen below">
                    <input type="checkbox" checked={picked.includes(k)} disabled={!!a.error}
                      onChange={() => toggle(picked, setPicked, k)} aria-label={`Choose ${a.name} for a run`} />
                  </label>
                  <button className="runs-open" onClick={() => onOpen(open === k ? "" : k)} aria-expanded={open === k}>
                    <span className="runs-name">{a.name}</span>
                    <span className="badge">{a.source === "library" ? "library" : "this book"}</span>
                    <span className="runs-purpose">{a.error ? `Does not parse: ${a.error}` : a.summary}</span>
                    <span className="muted">{lastLine(a)}</span>
                  </button>
                  {open === k && editor}
                </div>
              );
            })}
          </div>
        ))}
        {open && !openListed && editor}
      </section>

      <section className="card">
        <div className="card-head"><span>Run on tabs</span>
          <span className="badge">{pickedItems.length} chosen</span><span className="badge">{pickedTabs.length} tab{pickedTabs.length === 1 ? "" : "s"}</span></div>
        <p className="muted">{pickedItems.length
          ? `Chosen: ${pickedItems.map(a => a.name).join(", ")}.`
          : "Tick one or more filters or lenses above, then the tabs here."}</p>
        <div className="devedit-actions">
          <button className="pill" onClick={() => { setTabs(cat.tabs.filter(t => !t.blocked).map(t => t.file)); setReview(null); }}>All available tabs</button>
          <button className="pill" onClick={() => { setTabs([]); setReview(null); }}>None</button>
        </div>
        <div className="runs-tabs">
          {cat.tabs.map(t => (
            <label key={t.file} className={`runs-tab${t.blocked ? " blocked" : ""}`}>
              <input type="checkbox" checked={tabs.includes(t.file)} disabled={!!t.blocked && !tabs.includes(t.file)} onChange={() => toggle(tabs, setTabs, t.file)} />
              <span className="runs-tab-body">
                <span className="runs-name">{t.title}</span>
                <span className="muted"> {t.file} · {t.words.toLocaleString()} words · {t.units} units{t.protected ? " · protected register (no lens)" : ""}{!t.blocked && t.needs ? " · checked out to the Doc (the session pulls it first)" : ""}</span>
                {t.blocked && <span className="moved bad-note">Not available now: {t.blocked}</span>}
              </span>
            </label>
          ))}
        </div>
        <p className="muted runs-road">
          <strong>How these runs are drafted.</strong> This page calls no model and spends nothing. Each run becomes a request;
          a Claude Code session carries it out (<code>authorlm requests -m {manuscript}</code>), and its forms then reach the tab
          and the Merge page as usual. {pickedItems.some(a => a.kind === "filter") || !pickedItems.length ? cat.drafting.filter.note : ""}{" "}
          {pickedItems.some(a => a.kind === "lens") ? cat.drafting.lens.note : ""}
        </p>
        <div className="devedit-actions">
          <button className="btn take" disabled={busy || !pickedItems.length || !pickedTabs.length} onClick={() => void preview()}>
            {busy && !review ? "Checking…" : `Review ${pickedItems.length * pickedTabs.length} run${pickedItems.length * pickedTabs.length === 1 ? "" : "s"}`}</button>
        </div>
        {review && <div className="bar confirm runs-confirm">
          <span>
            <strong>{review.queued.length} run{review.queued.length === 1 ? "" : "s"} would be asked for</strong>
            {review.queued.length > 0 && <>: {review.queued.map(pairLine).join("; ")}.</>}
            {review.already.length > 0 && <> Already waiting: {review.already.map(pairLine).join("; ")}.</>}
            {review.blocked.map(b => <span key={`${b.kind}:${b.name}:${b.file}`} className="moved bad-note">Left out: {pairLine(b)}. {b.reason}</span>)}
            {" "}No model is called and nothing is spent by asking.
          </span>
          <button className="btn take" disabled={busy || !review.queued.length} onClick={() => void ask()}>
            {busy ? "Asking…" : `Ask for ${review.queued.length} run${review.queued.length === 1 ? "" : "s"}`}</button>
          <button className="btn plain" onClick={() => setReview(null)}>Not now</button>
        </div>}
        {asked && <div className="moved">Asked for {asked.queued.length} run{asked.queued.length === 1 ? "" : "s"}
          {asked.blocked.length ? `; ${asked.blocked.length} left out` : ""}. They wait below until a session carries them out.</div>}
      </section>

      <section className="card">
        <div className="card-head"><span>Requests</span>
          <span className="badge">{waiting.length} waiting for a session</span>
          <span className="badge">{cat.requests.length - waiting.length} further on</span></div>
        {cat.requests.length === 0 && <p className="muted">No runs have been asked for from this page.</p>}
        {waiting.length > 0 && <p className="muted">To have them carried out, tell a Claude Code session in the authorlm repo:
          "run the requests for {manuscript}" (it reads them with <code>authorlm requests -m {manuscript}</code>).</p>}
        {[...cat.requests].reverse().map(r => (
          <div key={r.id} className="runs-request">
            <div className="forms-file-head">
              <span className={`state${WAITING.has(r.state) ? "" : " done"}`}>{STATE_WORDS[r.state] || r.state}</span>
              <span className="runs-name">{r.name}</span>
              <span className="muted">{r.kind} on {r.file.replace(/\.md$/, "")}</span>
            </div>
            <div className="muted">{r.detail}{r.url && <> · <a href={r.url} target="_blank" rel="noreferrer">open the tab</a></>}</div>
            {WAITING.has(r.state) && <details className="muted"><summary>What the session runs</summary>
              <pre className="runs-commands">{r.commands.join("\n")}</pre></details>}
            {r.state === "queued" && <button className="pill" onClick={() => void cancel(r.id)}>Take off the list</button>}
          </div>
        ))}
      </section>
    </div>
  );
}
