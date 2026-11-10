import { useCallback, useEffect, useState } from "react";
import type { Transport } from "./transport";

// Mirrors forms_page.dispatch (authorlm/forms_page.py): every open outside
// revision ("form") of the manuscript, by file, as the Doc tab holds it now.
// A ruling (Accept / Edit / Decline) is held on the server until the file is
// merged; Merge lands that file's forms and brings the tab up to the file,
// or stops and says why (both sides changed, a ruling missing, a stale tab).
interface Ruling { verdict: "accept" | "edit" | "decline"; text: string | null; seen: string; at: string; }
interface Form {
  id: string; origin: string; note: string; kind: "replace" | "insert";
  old: string; proposed: string; current: string | null; found: boolean; edited_in_doc: boolean;
  prefix: string; suffix: string; anchor: string; ruling: Ruling | null;
}
interface Diff { base_found: boolean; doc: string; local: string; note?: string; }
interface FileGroup {
  file: string; url: string | null; state: string; count: number; forms: Form[];
  origins: string[]; others: Record<string, number>; undecided: number; blocking: string; diff?: Diff;
}
interface Listing { doc_url: string | null; files: FileGroup[]; total: number; }
interface MergeResult {
  file: string; stopped: string | null; message: string; accepted: number; modified: number;
  declined: number; warnings: string[]; pushed: boolean; url: string | null; diff?: Diff;
}

const STATE_WORDS: Record<string, string> = {
  unchanged: "tab and file agree", changed: "edited in the Doc", local_ahead: "file ahead of the tab",
  conflict: "changed on both sides", missing: "tab not found",
};

export interface FormsTab { file: string; count: number; toRule: number; }

// One Doc tab at a time: the header's Tab menu (App) picks it from the tabs
// this listing reports, which are only the tabs with open forms.
export function MergeForms({ transport, manuscript, tab, onTabs }: {
  transport: Transport; manuscript: string; tab: string; onTabs: (tabs: FormsTab[]) => void;
}) {
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<Record<string, MergeResult>>({});
  const [merging, setMerging] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await transport.request<Listing & {error?: string}>("list", {manuscript});
      if (r.error) setError(r.error); else { setData(r); setError(""); }
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }, [transport, manuscript]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (data) onTabs(data.files.map(g => ({file: g.file, count: g.count, toRule: g.forms.filter(f => !f.ruling).length}))); }, [data, onTabs]);
  // Coming back to the page (from the Doc, where the author may have edited
  // a form) reloads it, as the Dev edit does.
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onVisible);
    };
  }, [load]);

  const decide = async (f: Form, verdict: string, text?: string): Promise<string | null> => {
    try {
      const r = await transport.request<{error?: string}>("decide", {manuscript, id: f.id, verdict, seen: f.current, ...(text != null ? {text} : {})});
      if (r?.error) return r.error;
    } catch (err) { return (err as Error).message; }
    await load();
    return null;
  };

  const merge = async (file: string) => {
    setMerging(file);
    try {
      const r = await transport.request<MergeResult & {error?: string}>("merge", {manuscript, file});
      if (r.error) setResults(rs => ({...rs, [file]: {file, stopped: "error", message: r.error!, accepted: 0, modified: 0, declined: 0, warnings: [], pushed: false, url: null}}));
      else setResults(rs => ({...rs, [file]: r}));
    } catch (err) {
      setResults(rs => ({...rs, [file]: {file, stopped: "error", message: (err as Error).message, accepted: 0, modified: 0, declined: 0, warnings: [], pushed: false, url: null}}));
    } finally { setMerging(""); }
    await load();
  };

  if (!data && !error) return <div className="boot">Reading the open forms from the Doc…</div>;
  const landed = Object.values(results).filter(r => !data?.files.some(g => g.file === r.file));
  return (
    <div className="forms">
      {error && <div className="status bad">{error}</div>}
      <div className="card forms-head">
        <div className="devedit-actions">
          <b>{data ? `${data.total} open form${data.total === 1 ? "" : "s"} in ${data.files.length} file${data.files.length === 1 ? "" : "s"}` : "Open forms"}</b>
          <button className="pill" disabled={loading} onClick={() => void load()}>{loading ? "reading…" : "⟳ Refresh"}</button>
          {data?.doc_url && <a className="pill" href={data.doc_url} target="_blank" rel="noreferrer">Open the Doc ↗</a>}
        </div>
        <div className="muted">Rule on each form, then Merge its file: the file lands the rulings and its tab is brought up to it. Nothing reaches the Doc or the file before Merge.</div>
      </div>
      {landed.map(r => <ResultBox key={r.file} r={r} />)}
      {data && data.files.length === 0 && <div className="card muted">No outside revision is open on this manuscript.</div>}
      {data?.files.filter(g => g.file === (data.files.some(x => x.file === tab) ? tab : data.files[0]?.file)).map(g => (
        <FileCard key={g.file} g={g} result={results[g.file]} merging={merging === g.file} busy={!!merging}
          onMerge={() => void merge(g.file)} onDecide={decide} />
      ))}
    </div>
  );
}

function FileCard({ g, result, merging, busy, onMerge, onDecide }: {
  g: FileGroup; result?: MergeResult; merging: boolean; busy: boolean; onMerge: () => void;
  onDecide: (f: Form, verdict: string, text?: string) => Promise<string | null>;
}) {
  // As on the Dev edit page: a ruled form moves to Decided, where its
  // status shows and Undo or a new ruling sends it back.
  const [view, setView] = useState<"open" | "decided">("open");
  const decided = g.forms.filter(f => f.ruling);
  const open = g.forms.filter(f => !f.ruling);
  const conflict = g.state === "conflict";
  const blockedBy = conflict ? "Changed on both sides: settle that first."
    : g.state === "missing" ? "The tab was not found in the Doc's export."
      : g.undecided ? `${g.undecided} form${g.undecided === 1 ? "" : "s"} still to rule on.` : "";
  return (
    <section className="card forms-file">
      <div className="forms-file-head">
        <h2 className="forms-file-name">{g.file}</h2>
        <span className="badge">{g.count} not merged</span>
        <span className={`state${conflict ? " forms-conflict" : ""}`}>{STATE_WORDS[g.state] ?? g.state}</span>
        {g.url && <a className="pill" href={g.url} target="_blank" rel="noreferrer">Open tab ↗</a>}
      </div>
      <div className="muted">{g.origins.length ? `From ${g.origins.join(", ")}. ` : ""}{g.blocking}</div>
      {Object.keys(g.others).length > 0 && <div className="moved">Other forms in this tab are not ruled here: {Object.entries(g.others).map(([o, n]) => `${n} ${o}`).join(", ")}.</div>}
      <div className="forms-merge">
        <button className="btn take" disabled={busy || !!blockedBy} onClick={onMerge}>
          {merging ? <><span className="spin dark" /> merging…</> : `Merge ${g.file}`}
        </button>
        {blockedBy && <span className="muted">{blockedBy}</span>}
      </div>
      {result && <ResultBox r={result} />}
      {conflict && g.diff && <DiffBox diff={g.diff} />}
      <div className="devedit-actions">
        <button className={`pill${view === "open" ? " on" : ""}`} onClick={() => setView("open")}>To rule · {open.length}</button>
        <button className={`pill${view === "decided" ? " on" : ""}`} onClick={() => setView("decided")}>Decided · {decided.length}</button>
      </div>
      {(view === "open" ? open : decided).map(f => <FormCard key={f.id} f={f} onDecide={onDecide} />)}
      {view === "open" && open.length === 0 && <div className="muted">Every form in this tab is ruled. Check them under Decided, then Merge.</div>}
      {view === "decided" && decided.length === 0 && <div className="muted">Nothing ruled yet in this tab.</div>}
    </section>
  );
}

function FormCard({ f, onDecide }: { f: Form; onDecide: (f: Form, verdict: string, text?: string) => Promise<string | null> }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(f.ruling?.text ?? f.current ?? f.proposed);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { setText(f.ruling?.text ?? f.current ?? f.proposed); }, [f.ruling?.text, f.current, f.proposed]);
  const go = async (verdict: string, t?: string) => {
    setBusy(true); setMsg("");
    const err = await onDecide(f, verdict, t);
    setBusy(false);
    if (err) setMsg(err); else setEditing(false);
  };
  const ruled = f.ruling?.verdict;
  // What lands if merged now: the ruling's text over what the tab holds.
  const shown = ruled === "edit" ? f.ruling!.text! : f.current ?? f.proposed;
  const newPart = ruled === "decline" ? null : <b className="add">{shown}</b>;
  return (
    <div className={`forms-form${ruled ? " ruled" : ""}`}>
      <div className="card-head">
        <span>{f.origin}</span>
        {f.note && <span className="forms-note">{f.note}</span>}
        {ruled && <span className={`state ${ruled === "decline" ? "" : "done"}`}>{ruled === "accept" ? "accepted" : ruled === "edit" ? "edited" : "declined"}</span>}
      </div>
      {f.kind === "replace"
        ? <p className="devedit-diff">{f.prefix}<s className="del">{f.old}</s>{newPart && <> {newPart}</>}{f.suffix}</p>
        : <>
          {f.anchor && <p className="devedit-diff muted forms-anchor">{f.anchor}</p>}
          <p className="devedit-diff">{ruled === "decline" ? <s className="del">{shown}</s> : <>New paragraph: {newPart}</>}</p>
        </>}
      {f.edited_in_doc && <div className="muted">Edited in the Doc. Staged as: “{f.proposed}”</div>}
      {!f.found && <div className="moved">This form is no longer in the tab (taken out by hand?). Merge reads what the tab holds for it; it takes no ruling here.</div>}
      {msg && <div className="status bad">{msg}</div>}
      {f.found && editing && <>
        <textarea className="devedit-edit" value={text} onChange={e => setText(e.target.value)}
          rows={Math.min(10, Math.max(2, Math.ceil(text.length / 70)))} aria-label="New text for this form" />
        <div className="devedit-actions">
          <button className="pill on" disabled={busy} onClick={() => void go("edit", text)}>{busy ? "…" : "Save edit"}</button>
          <button className="pill" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </>}
      {f.found && !editing && <div className="devedit-actions">
        <button className={`pill${ruled === "accept" ? " on" : ""}`} disabled={busy} onClick={() => void go("accept")}>Accept</button>
        <button className={`pill${ruled === "edit" ? " on" : ""}`} disabled={busy} onClick={() => setEditing(true)}>Edit</button>
        <button className={`pill${ruled === "decline" ? " on" : ""}`} disabled={busy} onClick={() => void go("decline")}>Decline</button>
        {ruled && <button className="pill" disabled={busy} onClick={() => void go("clear")}>Undo</button>}
      </div>}
    </div>
  );
}

function DiffBox({ diff }: { diff: Diff }) {
  if (!diff.base_found) return <div className="moved bad-note">{diff.note}</div>;
  return (
    <div className="forms-diff">
      <div className="muted">What the Doc changed since the last push</div>
      <pre>{diff.doc || "(nothing)"}</pre>
      <div className="muted">What the file changed since the last push</div>
      <pre>{diff.local || "(nothing)"}</pre>
    </div>
  );
}

function ResultBox({ r }: { r: MergeResult }) {
  const ok = !r.stopped;
  return (
    <div className={`forms-result${ok ? " ok" : " bad"}`}>
      {ok
        ? <div><b>{r.file} merged:</b> {r.accepted} accepted · {r.modified} modified · {r.declined} declined{r.pushed ? " · tab up to date" : " · tab NOT brought up to the file"}</div>
        : <div><b>{r.file} not merged.</b> {r.message}</div>}
      {r.warnings.length > 0 && <ul>{r.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      {r.diff && <DiffBox diff={r.diff} />}
    </div>
  );
}
