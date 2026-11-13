import { useCallback, useEffect, useRef, useState } from "react";
import type { Transport } from "./transport";

// Mirrors forms_page.dispatch (authorlm/forms_page.py): every open outside
// revision, Doc-road filter edit or lens edit ("form") of the manuscript, by file, as
// the Doc tab holds it now.
// A ruling (Accept / Edit / Decline) is held on the server until the file is
// merged; Merge lands that file's forms and brings the tab up to the file,
// or stops and says why (both sides changed, a ruling missing, a stale tab).
// A file changed on both sides is settled under Conflicts: each region both
// sides changed is a card (Keep mine, Keep the Doc's, Keep both, Edit), held
// on the server like a ruling; Merge lands them all and pushes.
interface Ruling { verdict: "accept" | "edit" | "decline"; text: string | null; seen: string; at: string; }
interface Form {
  id: string; origin: string; producer: "external" | "filter" | "lens"; note: string; kind: "replace" | "insert";
  old: string; proposed: string; current: string | null; found: boolean; edited_in_doc: boolean;
  prefix: string; suffix: string; anchor: string; ruling: Ruling | null;
  // A ruling the Doc overtook (the green half changed after it): the form
  // is back under To rule, and this says what the ruling was.
  ruling_stale: Ruling | null;
}
interface Diff { base_found: boolean; doc: string; local: string; note?: string; }
type Choice = "mine" | "doc" | "both" | "edit";
interface Card {
  id: string; local: string; doc: string; base: string; illustration: boolean;
  ruling: { verdict: Choice; text: string | null; at: string } | null;
}
interface Conflict {
  key: string | null; base_found: boolean; note: string; regions: Card[];
  decided: number; held: string | null; ready: boolean;
}
interface FileGroup {
  file: string; url: string | null; state: string; count: number; forms: Form[];
  origins: string[]; others: Record<string, number>; undecided: number; blocking: string; diff?: Diff;
  conflict?: Conflict;
}
interface Listing { doc_url: string | null; files: FileGroup[]; total: number; conflicts: number; }
interface Queue { pending: string[]; running: string | null; results: Record<string, MergeResult>; }
interface MergeResult {
  file: string; stopped: string | null; message: string; accepted: number; modified: number;
  declined: number; warnings: string[]; pushed: boolean; url: string | null; diff?: Diff;
  conflict_landing?: boolean; kept?: Record<Choice, number>;
}

const STATE_WORDS: Record<string, string> = {
  unchanged: "tab and file agree", changed: "edited in the Doc", local_ahead: "file ahead of the tab",
  conflict: "changed on both sides", missing: "tab not found",
};

// A form the tab no longer holds (or every form, when the tab itself was
// not found in the export) is invalid: it is neither to rule nor decided.
export interface FormsTab {
  file: string; count: number; toRule: number; invalid: number;
  // `conflicts` is the tab's conflict cards (changed on both sides), 0 when none.
  conflicts: number;
  // Merge state: ready (nothing blocks a merge), its place in the queue
  // (-1 when not queued), and whether it is merging now.
  ready: boolean; queued: number; merging: boolean;
}
// The header (App) holds the Tab menu and the merge buttons; the page hands
// it the tabs and these two actions.
export interface FormsActions { mergeTab: (file: string) => void; mergeAll: () => void; }

function conflictCount(g: FileGroup): number {
  if (g.state !== "conflict") return 0;
  return Math.max(1, g.conflict?.regions.length ?? 0);
}

// Why a tab's forms cannot be merged yet, or "" when they can. A tab changed
// on both sides lands from its Conflicts panel instead.
function blockerOf(g: FileGroup): string {
  return g.state === "conflict" ? "Changed on both sides: settle that under Conflicts first."
    : g.state === "missing" ? "The tab was not found in the Doc's export."
      : g.undecided ? `${g.undecided} form${g.undecided === 1 ? "" : "s"} still to rule on.` : "";
}

// One Doc tab at a time: the header's Tab menu (App) picks it from the tabs
// this listing reports, which are only the tabs with open forms.
export function MergeForms({ transport, manuscript, tab, onTabs }: {
  transport: Transport; manuscript: string; tab: string;
  onTabs: (tabs: FormsTab[], actions: FormsActions) => void;
}) {
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  // The merge queue runs on the web server, one tab after another; the page
  // only adds tabs to it and reads its state.
  const [queue, setQueue] = useState<Queue>({pending: [], running: null, results: {}});
  const results = queue.results;
  const done = useRef(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await transport.request<Listing & {error?: string}>("list", {manuscript});
      if (r.error) setError(r.error); else { setData(r); setError(""); }
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }, [transport, manuscript]);
  useEffect(() => { void load(); }, [load]);
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

  // A ruling is saved at once (milliseconds) and shown in place: the form
  // moves to Decided without reading the whole Doc again, which takes
  // seconds. A quiet re-read follows a little after the last ruling; if
  // the Doc has overtaken a ruling meanwhile, that re-read sends the form
  // back to To rule, flagged.
  const recheck = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(recheck.current), []);
  const decide = async (f: Form, verdict: string, text?: string): Promise<string | null> => {
    let ruling: Ruling | null;
    try {
      const r = await transport.request<{error?: string; ruling?: Ruling | null}>("decide", {manuscript, id: f.id, verdict, seen: f.current, ...(text != null ? {text} : {})});
      if (r?.error) return r.error;
      ruling = r.ruling ?? null;
    } catch (err) { return (err as Error).message; }
    setData(d => d && {...d, files: d.files.map(g => {
      if (!g.forms.some(x => x.id === f.id)) return g;
      const forms = g.forms.map(x => x.id === f.id ? {...x, ruling, ruling_stale: null} : x);
      return {...g, forms, undecided: forms.filter(x => x.found && !x.ruling).length};
    })});
    window.clearTimeout(recheck.current);
    recheck.current = window.setTimeout(() => void load(), 20000);
    return null;
  };

  const settle = async (file: string, c: Card, verdict: string, text?: string): Promise<string | null> => {
    try {
      const r = await transport.request<{error?: string}>("settle", {manuscript, file, region: c.id, verdict, ...(text != null ? {text} : {})});
      if (r?.error) return r.error;
    } catch (err) { return (err as Error).message; }
    await load();
    return null;
  };

  const readQueue = useCallback(async () => {
    try {
      const q = await transport.request<Queue & {error?: string}>("queue", {manuscript});
      if (!q.error) setQueue(q);
    } catch { /* the next poll tries again */ }
  }, [transport, manuscript]);
  useEffect(() => { void readQueue(); }, [readQueue]);
  const active = !!queue.running || queue.pending.length > 0;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => void readQueue(), 2000);
    return () => window.clearInterval(t);
  }, [active, readQueue]);
  // A merge that finished changes the listing: read it again.
  useEffect(() => {
    const n = Object.keys(queue.results).length;
    if (n > done.current) void load();
    done.current = n;
  }, [queue.results, load]);

  const merge = useCallback(async (file: string, conflictKey?: string) => {
    try {
      const q = await transport.request<Queue & {error?: string}>("enqueue",
        conflictKey ? {manuscript, file, conflict: true, key: conflictKey} : {manuscript, file});
      if (q.error) setError(q.error); else setQueue(q);
    } catch (err) { setError((err as Error).message); }
  }, [transport, manuscript]);
  // Merge all: every tab nothing blocks and not already in the queue, in
  // the Tab menu's order; the server still runs them one after another.
  const mergeAll = useCallback(async () => {
    if (!data) return;
    for (const g of data.files) {
      if (!blockerOf(g) && queue.running !== g.file && !queue.pending.includes(g.file)) await merge(g.file);
    }
  }, [data, queue, merge]);
  useEffect(() => {
    if (!data) return;
    onTabs(data.files.map(g => ({
      file: g.file, count: g.count,
      toRule: g.forms.filter(f => f.found && !f.ruling).length,
      invalid: g.forms.filter(f => !f.found).length, conflicts: conflictCount(g),
      ready: !blockerOf(g), queued: queue.pending.indexOf(g.file), merging: queue.running === g.file,
    })), {mergeTab: f => void merge(f), mergeAll: () => void mergeAll()});
  }, [data, queue, merge, mergeAll, onTabs]);

  if (!data && !error) return <div className="boot">Reading the open forms from the Doc…</div>;
  const landed = Object.values(results).filter(r => !data?.files.some(g => g.file === r.file));
  return (
    <div className="forms">
      {error && <div className="status bad">{error}</div>}
      <div className="card forms-head">
        <div className="devedit-actions">
          <b>{data ? `${data.total} open form${data.total === 1 ? "" : "s"} in ${data.files.length} file${data.files.length === 1 ? "" : "s"}${data.conflicts ? `, ${data.conflicts} changed on both sides` : ""}` : "Open forms"}</b>
          <button className="pill" disabled={loading} onClick={() => void load()}>{loading ? "reading…" : "⟳ Refresh"}</button>
          {data?.doc_url && <a className="pill" href={data.doc_url} target="_blank" rel="noreferrer">Open the Doc ↗</a>}
        </div>
        {active && <div className="muted forms-queue">{queue.running ? <><span className="spin" /> Merging {queue.running}</> : "Starting the queue"}{queue.pending.length ? `; queued: ${queue.pending.join(", ")}` : ""}. You can keep ruling meanwhile.</div>}
        <div className="muted">Rule on each form, then Merge this tab (or Merge all): the file lands the rulings and its tab is brought up to it. A file changed both here and in its tab is settled under Conflicts. Nothing reaches the Doc or the file before Merge.</div>
      </div>
      {landed.map(r => <ResultBox key={r.file} r={r} />)}
      {data && data.files.length === 0 && <div className="card muted">No outside revision, filter form or lens form is open on this manuscript, and no file is changed on both sides.</div>}
      {data?.files.filter(g => g.file === (data.files.some(x => x.file === tab) ? tab : data.files[0]?.file)).map(g => (
        <FileCard key={g.file} g={g} result={results[g.file]} merging={queue.running === g.file}
          queued={queue.pending.indexOf(g.file)}
          onDecide={decide}
          onLand={() => void merge(g.file, g.conflict?.key ?? undefined)}
          onSettle={(c, verdict, text) => settle(g.file, c, verdict, text)} />
      ))}
    </div>
  );
}

function FileCard({ g, result, merging, queued, onDecide, onLand, onSettle }: {
  g: FileGroup; result?: MergeResult; merging: boolean; queued: number;
  onDecide: (f: Form, verdict: string, text?: string) => Promise<string | null>;
  onLand: () => void; onSettle: (c: Card, verdict: string, text?: string) => Promise<string | null>;
}) {
  // As on the Dev edit page: a ruled form moves to Decided, where its
  // status shows and Undo or a new ruling sends it back. A form the tab
  // no longer holds goes under Invalid: it takes no ruling here. A file
  // changed on both sides opens on Conflicts.
  const conflict = g.state === "conflict";
  const [view, setView] = useState<"open" | "decided" | "invalid" | "conflicts">(conflict ? "conflicts" : "open");
  const decided = g.forms.filter(f => f.found && f.ruling);
  const open = g.forms.filter(f => f.found && !f.ruling);
  const invalid = g.forms.filter(f => !f.found);
  const blockedBy = blockerOf(g);
  return (
    <section className="card forms-file">
      <div className="forms-file-head">
        <h2 className="forms-file-name">{g.file}</h2>
        {g.count > 0 && <span className="badge">{g.count} not merged</span>}
        <span className={`state${conflict ? " forms-conflict" : ""}`}>{STATE_WORDS[g.state] ?? g.state}</span>
        {g.url && <a className="pill" href={g.url} target="_blank" rel="noreferrer">Open tab ↗</a>}
      </div>
      {(g.origins.length > 0 || g.blocking) && <div className="muted">{g.origins.length ? `From ${g.origins.join(", ")}. ` : ""}{g.blocking}</div>}
      {Object.keys(g.others).length > 0 && <div className="moved">Other forms in this tab are not ruled here: {Object.entries(g.others).map(([o, n]) => `${n} ${o}`).join(", ")}.</div>}
      {(merging || queued >= 0 || blockedBy) && <div className="muted">
        {merging ? <><span className="spin" /> Merging now.</> : queued >= 0 ? `In the merge queue${queued ? `, ${queued} ahead` : ", next"}.` : blockedBy}
      </div>}
      {result && <ResultBox r={result} />}
      <div className="devedit-actions">
        <button className={`pill${view === "open" ? " on" : ""}`} onClick={() => setView("open")}>To rule · {open.length}</button>
        <button className={`pill${view === "decided" ? " on" : ""}`} onClick={() => setView("decided")}>Decided · {decided.length}</button>
        <button className={`pill${view === "invalid" ? " on" : ""}`} onClick={() => setView("invalid")}>Invalid · {invalid.length}</button>
        <button className={`pill${view === "conflicts" ? " on" : ""}`} onClick={() => setView("conflicts")}>Conflicts · {conflictCount(g)}</button>
      </div>
      {view === "conflicts" && <ConflictPanel g={g} merging={merging} queued={queued} onLand={onLand} onSettle={onSettle} />}
      {view !== "conflicts" && (view === "open" ? open : view === "decided" ? decided : invalid).map(f => <FormCard key={f.id} f={f} onDecide={onDecide} />)}
      {view === "open" && open.length === 0 && <div className="muted">{decided.length ? "Every form in this tab is ruled. Check them under Decided, then Merge." : "Nothing here to rule: every form is under Invalid."}</div>}
      {view === "decided" && decided.length === 0 && <div className="muted">Nothing ruled yet in this tab.</div>}
      {view === "invalid" && invalid.length === 0 && <div className="muted">Every form is still in the tab.</div>}
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
        <span>{f.producer === "external" ? f.origin : `${f.producer} ${f.origin}`}</span>
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
      {f.ruling_stale && <div className="moved">The Doc changed this form after you ruled ({f.ruling_stale.verdict === "accept" ? "accepted" : f.ruling_stale.verdict === "edit" ? "edited" : "declined"}). Rule again on the text as it reads now.</div>}
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
        {!(f.producer === "lens" && f.kind === "insert") && <button className={`pill${ruled === "edit" ? " on" : ""}`} disabled={busy} onClick={() => setEditing(true)}>Edit</button>}
        <button className={`pill${ruled === "decline" ? " on" : ""}`} disabled={busy} onClick={() => void go("decline")}>Decline</button>
        {ruled && <button className="pill" disabled={busy} onClick={() => void go("clear")}>Undo</button>}
      </div>}
    </div>
  );
}

function ConflictPanel({ g, merging, queued, onLand, onSettle }: {
  g: FileGroup; merging: boolean; queued: number; onLand: () => void;
  onSettle: (c: Card, verdict: string, text?: string) => Promise<string | null>;
}) {
  if (g.state !== "conflict") return <div className="muted">This tab and its file have not both changed since the last push.</div>;
  const c = g.conflict;
  if (!c || !c.base_found) return <>{c?.note ? <div className="moved bad-note">{c.note}</div> : null}{g.diff && <DiffBox diff={g.diff} />}</>;
  const left = c.regions.length - c.decided;
  const why = c.held ? "" : left ? `${left} card${left === 1 ? "" : "s"} still to choose on.` : "";
  return (
    <>
      <div className="muted">Changes made on one side only merge by themselves. Each card below was changed on both sides: choose what the file and the tab should read. Nothing is written before Merge.</div>
      {c.held && <div className="moved">{c.held}</div>}
      <div className="forms-merge">
        <button className="btn take" disabled={merging || queued >= 0 || !c.ready} onClick={onLand}>
          {merging ? <><span className="spin dark" /> merging…</>
            : queued >= 0 ? `Queued${queued ? ` · ${queued} ahead` : " · next"}` : `Merge ${g.file}`}
        </button>
        {why && <span className="muted">{why}</span>}
      </div>
      {c.regions.map((card, i) => <ConflictCard key={card.id} n={i + 1} card={card} onSettle={onSettle} />)}
      {c.regions.length === 0 && <div className="muted">No region was changed on both sides; Merge lands both sides' changes.</div>}
    </>
  );
}

const CHOICE_WORDS: Record<Choice, string> = { mine: "kept mine", doc: "kept the Doc's", both: "kept both", edit: "edited" };

function ConflictCard({ n, card, onSettle }: { n: number; card: Card; onSettle: (c: Card, verdict: string, text?: string) => Promise<string | null> }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(card.ruling?.text ?? card.local);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { setText(card.ruling?.text ?? card.local); }, [card.ruling?.text, card.local]);
  const go = async (verdict: string, t?: string) => {
    setBusy(true); setMsg("");
    const err = await onSettle(card, verdict, t);
    setBusy(false);
    if (err) setMsg(err); else setEditing(false);
  };
  const chosen = card.ruling?.verdict;
  return (
    <div className={`forms-form${chosen ? " ruled" : ""}`}>
      <div className="card-head">
        <span>Card {n}</span>
        {chosen && <span className="state done">{CHOICE_WORDS[chosen]}</span>}
      </div>
      <div className="muted">Yours (the file)</div>
      <p className="devedit-diff">{card.local || <i className="muted">(nothing: the file took this out)</i>}</p>
      <div className="muted">The Doc's</div>
      <p className="devedit-diff">{card.doc || <i className="muted">(nothing: the Doc took this out)</i>}</p>
      <div className="muted forms-base">As it read at the last push: {card.base || "(nothing)"}</div>
      {chosen === "edit" && !editing && <><div className="muted">Your edit</div><p className="devedit-diff"><b className="add">{card.ruling!.text}</b></p></>}
      {card.illustration && <div className="moved">This card touches a picture's [Illustration: ...] tag. Changing the description marks its picked picture stale.</div>}
      {msg && <div className="status bad">{msg}</div>}
      {editing && <>
        <textarea className="devedit-edit" value={text} onChange={e => setText(e.target.value)}
          rows={Math.min(14, Math.max(3, Math.ceil(text.length / 70) + text.split("\n").length))} aria-label="The text this card should read" />
        <div className="devedit-actions">
          <button className="pill on" disabled={busy} onClick={() => void go("edit", text)}>{busy ? "…" : "Save edit"}</button>
          <button className="pill" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </>}
      {!editing && <div className="devedit-actions">
        <button className={`pill${chosen === "mine" ? " on" : ""}`} disabled={busy} onClick={() => void go("mine")}>Keep mine</button>
        <button className={`pill${chosen === "doc" ? " on" : ""}`} disabled={busy} onClick={() => void go("doc")}>Keep the Doc's</button>
        <button className={`pill${chosen === "both" ? " on" : ""}`} disabled={busy} onClick={() => void go("both")} title="Yours, then the Doc's, as separate paragraphs, to trim later">Keep both</button>
        <button className={`pill${chosen === "edit" ? " on" : ""}`} disabled={busy} onClick={() => setEditing(true)}>Edit</button>
        {chosen && <button className="pill" disabled={busy} onClick={() => void go("clear")}>Undo</button>}
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
      {ok && r.kept
        ? <div><b>{r.file} merged:</b> {r.kept.mine} kept mine · {r.kept.doc} kept the Doc's · {r.kept.both} kept both · {r.kept.edit} edited{r.pushed ? " · tab up to date" : " · tab NOT brought up to the file"}</div>
        : ok
        ? <div><b>{r.file} merged:</b> {r.accepted} accepted · {r.modified} modified · {r.declined} declined{r.pushed ? " · tab up to date" : " · tab NOT brought up to the file"}</div>
        : <div><b>{r.file} not merged.</b> {r.message}</div>}
      {r.warnings.length > 0 && <ul>{r.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      {r.diff && <DiffBox diff={r.diff} />}
    </div>
  );
}
