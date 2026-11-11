import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Transport } from "./transport";
import { MarkdownBox } from "./MarkdownBox";

// Undo for an edit box: a snapshot every few seconds while the text changes,
// kept per item in this browser's localStorage (lost if storage is cleared;
// that is fine). Guards against a fat-fingered select-all-and-delete.
function useUndo(key: string, text: string, setText: (t: string) => void) {
  const storeKey = `devedit-undo:${key}`;
  const [stack, setStack] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(storeKey) || "[]"); } catch { return []; }
  });
  useEffect(() => {
    try { setStack(JSON.parse(localStorage.getItem(storeKey) || "[]")); } catch { setStack([]); }
  }, [storeKey]);
  useEffect(() => {
    const id = setTimeout(() => {
      setStack(prev => {
        if (prev[prev.length - 1] === text) return prev;
        const next = [...prev, text].slice(-50);
        try { localStorage.setItem(storeKey, JSON.stringify(next)); } catch { /* storage off */ }
        return next;
      });
    }, 2500);
    return () => clearTimeout(id);
  }, [text, storeKey]);
  const undo = () => {
    // Step back to the last snapshot that differs from what is in the box.
    const prev = [...stack];
    while (prev.length && prev[prev.length - 1] === text) prev.pop();
    const back = prev[prev.length - 1];
    if (back === undefined) return;
    setStack(prev);
    try { localStorage.setItem(storeKey, JSON.stringify(prev)); } catch { /* storage off */ }
    setText(back);
  };
  const canUndo = stack.some(t => t !== text);
  return { undo, canUndo };
}

// Mirrors devedit.dispatch (authorlm/devedit.py): the letter and its
// decisions, then the review queue one item at a time. Accept applies the
// item to the manuscript file; Push sends a file to its Doc tab, guarded as
// on the Chapters mode.
interface Decision { n: number; question: string; options?: string[]; recommended?: string; answer?: string; reason?: string; binding?: boolean; answered?: string; }
interface Item {
  id: string; file: string; tier: string; original?: string | null; proposed?: string | null; move?: string;
  reason?: string; finding?: string; links?: string[]; diff?: [string, string][];
  verdict?: string; final?: string | null; reason_given?: string; why?: string; decided?: string;
  // The whole paragraph(s) around the passage (devedit._paragraph_context).
  prefix?: string; suffix?: string; paragraph?: string; paragraph_proposed?: string; context_missing?: boolean;
}

// The change marked in its paragraph: the text before and after the passage
// plain, the passage's word diff (or the author's own words) in between.
function InContext({ it, middle }: { it: Item; middle: ReactNode }) {
  return <>
    <p className="devedit-diff" style={{whiteSpace: "pre-wrap"}}>{it.prefix}{middle}{it.suffix}</p>
    {it.context_missing && <div className="muted">The paragraph could not be found in the file; this is the passage alone.</div>}
  </>;
}
const diffOf = (it: Item) => it.diff?.map(([op, t], i) =>
  op === "=" ? <span key={i}>{t}</span> : op === "+" ? <b key={i} className="add">{t}</b> : <s key={i} className="del">{t}</s>);
interface Next { item: Item | null; left: number; counts: Record<string, number>; unpushed: string[]; }
interface Step { step: string; status: string; executor?: string; model?: string; effort?: string; changed?: string[]; }
interface Status { run: string; steps: Step[]; counts: Record<string, number>; unpushed: string[]; decisions: Decision[]; }

// Arc rounds (devedit `arcs`): an essay whose case or story is in question.
interface ArcCandidate { case?: string; kind?: string; stage?: string; story?: string; moral?: string; fit?: string; weakness?: string; source?: string; text?: string; }
interface ArcAnswer { at?: string; round?: number; text: string; via?: string; }
interface ArcOpen { file: string; pass: string; rounds?: number; round?: {at?: string; judgment?: string; candidates?: ArcCandidate[]; questions?: string[]}; answers?: ArcAnswer[]; }
interface Arcs { open: ArcOpen[]; status: {file: string; state: string; pass?: string; outcome?: string}[]; }

type Tab = "letter" | "review" | "decided" | "arcs";

export function DevEdit({ transport, manuscript }: { transport: Transport; manuscript: string }) {
  const [tab, setTab] = useState<Tab>("letter");
  const [status, setStatus] = useState<Status | null>(null);
  const [letter, setLetter] = useState<{letter: string; map: string; decisions: Decision[]} | null>(null);
  const [next, setNext] = useState<Next | null>(null);
  const [text, setText] = useState("");
  const lastId = useRef<string | undefined>(undefined);
  // Cards the author skipped this visit: shown again after the rest.
  const skipped = useRef<string[]>([]);
  const [reason, setReason] = useState("");
  const [why, setWhy] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState<Item[]>([]);
  const [doneStage, setDoneStage] = useState<{n: number; name: string} | null>(null);
  const [arcs, setArcs] = useState<Arcs | null>(null);

  const call = useCallback(async <T,>(method: string, params: Record<string, unknown> = {}): Promise<T | null> => {
    setError("");
    try {
      const r = await transport.request<T & {error?: string}>(method, {manuscript, ...params});
      if (r && (r as {error?: string}).error) { setError((r as {error: string}).error); return null; }
      return r;
    } catch (err) { setError((err as Error).message); return null; }
  }, [transport, manuscript]);

  const load = useCallback(async () => {
    const [s, l, n, d, a] = await Promise.all([
      call<Status>("status"),
      call<{letter: string; map: string; decisions: Decision[]}>("letter"),
      call<Next>("next", {skip: skipped.current}),
      call<{items: Item[]; stage: {n: number; name: string} | null}>("decided"),
      call<Arcs>("arcs"),
    ]);
    if (a) setArcs(a);
    if (d) { setDone(d.items); setDoneStage(d.stage); }
    if (s) setStatus(s);
    if (l) setLetter(l);
    if (n) {
      // A reload of the same card (the page comes back into view, Refresh)
      // keeps what the author has typed; only a new card resets the box.
      const fresh = n.item?.id !== lastId.current;
      lastId.current = n.item?.id;
      setNext(n);
      if (fresh) { setText(n.item?.paragraph_proposed ?? n.item?.proposed ?? ""); setReason(""); setWhy(false); }
    }
  }, [call]);
  useEffect(() => { void load(); }, [load]);
  // Coming back to the page (from the chat, or after the phone slept)
  // reloads its data: anything decided or generated meanwhile shows, and a
  // request the phone cut off is not left as a stale "Load failed".
  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("pageshow", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("pageshow", onVisible);
    };
  }, [load]);
  const refresh = async (f: string) => {
    setBusy(`refresh:${f}`);
    try { await load(); } finally { setBusy(""); }
  };

  const item = next?.item ?? null;
  const reviewUndo = useUndo(`review:${item?.id ?? "-"}`, text, setText);
  const decideItem = useCallback(async (verdict: "accept" | "reject" | "defer") => {
    if (!item) return;
    if (verdict === "reject" && !reason.trim()) { setError("Say why in a few words, then reject."); return; }
    setBusy(verdict);
    // The box holds the whole paragraph when the card has one; an edit is
    // sent as the paragraph, and the server finds the passage inside it.
    const whole = item.paragraph_proposed != null;
    const start = whole ? item.paragraph_proposed : item.proposed;
    const edited = item.proposed != null && text !== start;
    const r = await call("verdict", {id: item.id, verdict, reason,
      ...(edited ? (whole ? {paragraph: text} : {text}) : {})});
    setBusy("");
    if (r) await load();
  }, [item, text, reason, call, load]);

  // Keys a / x / d / ? when focus is not in a text box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (tab !== "review" || t.tagName === "TEXTAREA" || t.tagName === "INPUT" || e.metaKey || e.ctrlKey) return;
      if (e.key === "a") void decideItem("accept");
      else if (e.key === "x") (document.getElementById("devedit-reason") as HTMLInputElement | null)?.focus();
      else if (e.key === "d") void decideItem("defer");
      else if (e.key === "s" && item) { skipped.current = [...skipped.current.filter(x => x !== item.id), item.id]; void load(); }
      else if (e.key === "?") setWhy(w => !w);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, decideItem]);

  // A decided item's card reports on itself, like a decision's.
  const revise = async (it: Item, verdict: string, reason: string, text?: string): Promise<string | null> => {
    try {
      const r = await transport.request<{error?: string}>("revise", {manuscript, id: it.id, verdict, reason, ...(text != null ? {text} : {})});
      if (r?.error) return r.error;
    } catch (err) { return (err as Error).message; }
    await load();
    return null;
  };

  const push = async (file: string) => {
    setBusy(`push:${file}`);
    const r = await call<{unpushed: string[]}>("push", {file});
    setBusy("");
    if (r) await load();
  };
  const pull = async (file: string) => {
    setBusy(`pull:${file}`);
    await call("pull", {file});
    setBusy("");
    await load();
  };
  // A decision's save reports on its own card: the page-level banner is
  // out of sight once the author has scrolled to decision 14.
  const decide = async (d: Decision, answer: string, why: string, binding: boolean): Promise<string | null> => {
    try {
      const r = await transport.request<{error?: string}>("decide", {manuscript, n: d.n, answer, reason: why, binding});
      if (r?.error) return r.error;
    } catch (err) { return (err as Error).message; }
    const l = await call<{letter: string; map: string; decisions: Decision[]}>("letter");
    if (l) setLetter(l);
    return null;
  };
  // An arc card's action reports on its own card, then the list reloads.
  const arcCall = async (method: "arc_answer" | "arc_settle", params: Record<string, unknown>): Promise<string | null> => {
    try {
      const r = await transport.request<{error?: string}>(method, {manuscript, ...params});
      if (r?.error) return r.error;
    } catch (err) { return (err as Error).message; }
    await load();
    return null;
  };
  const saveDoc = async (doc: "letter" | "map", text: string): Promise<boolean> => {
    const r = await call("save", {doc, text});
    return r !== null;
  };

  if (!status && !error) return <div className="boot">Loading the developmental edit…</div>;
  return (
    <div className="devedit">
      {error && <div className="status bad">{error}</div>}
      {status && <div className="card devedit-head">
        <div className="devedit-tabs">
          <button className={`pill${tab === "letter" ? " on" : ""}`} onClick={() => setTab("letter")}>Letter &amp; decisions</button>
          <button className={`pill${tab === "review" ? " on" : ""}`} onClick={() => setTab("review")}>Review</button>
          <button className={`pill${tab === "decided" ? " on" : ""}`} onClick={() => setTab("decided")}>Decided{doneStage ? ` · ${doneStage.name}` : ""} · {done.length}</button>
          {(arcs?.open.length ?? 0) > 0 && <button className={`pill${tab === "arcs" ? " on" : ""}`} onClick={() => setTab("arcs")}>Arcs · {arcs!.open.length}</button>}
          <span className="muted">run {status.run} · must {status.counts.must ?? 0} · should {status.counts.should ?? 0} · could {status.counts.could ?? 0} · accepted {status.counts.accept ?? 0}</span>
        </div>
        {status.unpushed.length > 0 && <div className="devedit-push">
          <span className="muted">Accepted, not yet on the Doc:</span>
          {status.unpushed.map(f => (
            <span key={f} className="devedit-file">{f}
              <button className="pill" disabled={!!busy} onClick={() => void pull(f)}>{busy === `pull:${f}` ? "pulling…" : "⇩ Pull"}</button>
              <button className="pill" disabled={!!busy} onClick={() => void push(f)}>{busy === `push:${f}` ? "pushing…" : "⇧ Push"}</button>
              <button className="pill" disabled={!!busy} onClick={() => void refresh(f)}>{busy === `refresh:${f}` ? "refreshing…" : "⟳ Refresh"}</button>
            </span>
          ))}
        </div>}
      </div>}

      {tab === "letter" && letter && <>
        <Listen markdown={letter.letter} />
        <MarkdownBox label="Letter" value={letter.letter} onSave={text => saveDoc("letter", text)} />
        <h2 className="devedit-h">Decisions · {letter.decisions.filter(d => d.answered).length} of {letter.decisions.length} answered</h2>
        {letter.decisions.map(d => <DecisionCard key={d.n} d={d} onDecide={decide} />)}
        {letter.map && <details className="card"><summary>Argument map</summary><MarkdownBox label="Argument map" value={letter.map} onSave={text => saveDoc("map", text)} /></details>}
      </>}

      {tab === "review" && (item ? (
        <div className="card devedit-item">
          <div className="card-head"><span className="ord">#{item.id}</span><span>{item.tier}</span><span>{item.file}</span><span>{next?.left} left</span></div>
          {item.diff ? ((item.paragraph_proposed ?? item.proposed) === text
              ? <InContext it={item} middle={diffOf(item)} />
              : item.paragraph_proposed != null
                // The author is editing the whole paragraph: show it as it will read.
                ? <p className="devedit-diff" style={{whiteSpace: "pre-wrap"}}><b className="add">{text}</b></p>
                : <InContext it={item} middle={<><s className="del">{item.original}</s> <b className="add">{text}</b></>} />)
            : <p className="devedit-diff">{item.move ?? item.proposed}</p>}
          {item.proposed != null && <textarea className="devedit-edit" value={text} onChange={e => setText(e.target.value)} rows={Math.min(12, Math.max(3, Math.ceil(text.length / 70)))} aria-label="Proposed text (edit before accepting)" />}
          {item.proposed != null && <Listen markdown={text} label="▶ Listen" wait="Preparing audio…" />}
          {item.proposed != null && <div className="devedit-undo">
            <button className="pill" disabled={!reviewUndo.canUndo} onClick={reviewUndo.undo}>↶ Undo</button>
            <button className="pill" disabled={text === (item.paragraph_proposed ?? item.proposed)} onClick={() => setText(item.paragraph_proposed ?? item.proposed ?? "")}>Reset to proposed</button>
          </div>}
          {why && <div className="devedit-why">{item.reason}{item.finding ? <><br /><span className="muted">Letter finding: {item.finding}</span></> : null}</div>}
          <input id="devedit-reason" className="devedit-reason" placeholder="Reason (needed to reject)" value={reason} onChange={e => setReason(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") void decideItem("reject"); }} />
          <div className="devedit-actions">
            <button className="pill on" disabled={!!busy} onClick={() => void decideItem("accept")}>{busy === "accept" ? "…" : "a Accept"}</button>
            <button className="pill" disabled={!!busy} onClick={() => void decideItem("reject")}>x Reject</button>
            <button className="pill" disabled={!!busy} onClick={() => void decideItem("defer")}>d Defer</button>
            <button className="pill" disabled={!!busy} onClick={() => { if (item) { skipped.current = [...skipped.current.filter(x => x !== item.id), item.id]; void load(); } }}>s Skip</button>
            <button className="pill" onClick={() => setWhy(w => !w)}>? Why</button>
          </div>
        </div>
      ) : <div className="card muted">{status?.counts.total ? "Every item has a verdict." : "The queue is empty: the review starts once the queue is built."}</div>)}

      {tab === "decided" && (done.length
        ? done.map(it => <DecidedCard key={it.id} it={it} onRevise={revise} />)
        : <div className="card muted">No item has a verdict yet.</div>)}

      {tab === "arcs" && (arcs?.open.length
        ? arcs.open.map(a => <ArcCard key={a.file} a={a} onAct={arcCall} />)
        : <div className="card muted">No essay is waiting on its arc.</div>)}
    </div>
  );
}

// An item already decided: its verdict, the text that landed, and the
// Review card's controls to change it. Leaving an accept undoes it in the
// file; accepting other wording swaps it (devedit.revise).
function DecidedCard({ it, onRevise }: { it: Item; onRevise: (it: Item, verdict: string, reason: string, text?: string) => Promise<string | null> }) {
  const [text, setText] = useState(it.final ?? it.proposed ?? "");
  const decidedUndo = useUndo(`decided:${it.id}`, text, setText);
  const [reason, setReason] = useState(it.reason_given ?? "");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const go = async (verdict: "accept" | "reject" | "defer") => {
    if (verdict === "reject" && !reason.trim()) { setMsg("Say why in a few words, then reject."); return; }
    setBusy(verdict); setMsg("");
    const edited = it.proposed != null && text !== it.proposed;
    const err = await onRevise(it, verdict, reason, verdict === "accept" && edited ? text : undefined);
    setBusy("");
    if (err) setMsg(err); else setOpen(false);
  };
  return (
    <div className="card devedit-item">
      <div className="card-head"><span className="ord">#{it.id}</span><span>{it.tier}</span><span>{it.file}</span>
        <span className={`pill${it.verdict === "accept" ? " on" : ""}`}>{it.verdict}</span>
        {!open && <button className="pill" onClick={() => setOpen(true)}>Change</button>}</div>
      {it.verdict === "accept" && it.final != null
        ? <InContext it={it} middle={<b className="add">{it.final}</b>} />
        : it.diff ? <InContext it={it} middle={diffOf(it)} />
          : <p className="devedit-diff">{it.move ?? it.proposed}</p>}
      <div className="muted">{it.why}{it.reason_given ? <> · Reason: {it.reason_given}</> : null}</div>
      {msg && <div className="status bad">{msg}</div>}
      {open && <>
        {it.proposed != null && <textarea className="devedit-edit" value={text} onChange={e => setText(e.target.value)} rows={Math.min(12, Math.max(3, Math.ceil(text.length / 70)))} aria-label="Text to accept (edit before accepting)" />}
        {it.proposed != null && <div className="devedit-undo"><button className="pill" disabled={!decidedUndo.canUndo} onClick={decidedUndo.undo}>↶ Undo</button></div>}
        <input className="devedit-reason" placeholder="Reason (needed to reject)" value={reason} onChange={e => setReason(e.target.value)} />
        <div className="devedit-actions">
          <button className="pill on" disabled={!!busy} onClick={() => void go("accept")}>{busy === "accept" ? "…" : "Accept"}</button>
          <button className="pill" disabled={!!busy} onClick={() => void go("reject")}>Reject</button>
          <button className="pill" disabled={!!busy} onClick={() => void go("defer")}>Defer</button>
          <button className="pill" onClick={() => setOpen(false)}>Cancel</button>
        </div>
      </>}
    </div>
  );
}

// One essay's open arc round: the agent's judgment, its candidate cases,
// its questions and the author's answers so far; the author answers or
// settles (devedit arc_answer / arc_settle).
function ArcCard({ a, onAct }: { a: ArcOpen; onAct: (method: "arc_answer" | "arc_settle", params: Record<string, unknown>) => Promise<string | null> }) {
  const [text, setText] = useState("");
  const arcUndo = useUndo(`arc:${a.file}`, text, setText);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const go = async (key: string, method: "arc_answer" | "arc_settle", params: Record<string, unknown>) => {
    setBusy(key); setMsg("");
    const err = await onAct(method, {file: a.file, ...params});
    setBusy("");
    if (err) setMsg(err); else if (method === "arc_answer") setText("");
  };
  const answer = () => text.trim() ? void go("answer", "arc_answer", {text}) : setMsg("Write an answer first.");
  const none = () => text.trim() ? void go("none", "arc_settle", {outcome: "none", reason: text}) : setMsg("Say in the box why the essay needs no case, then press No case.");
  const r = a.round ?? {};
  return (
    <div className="card devedit-item">
      <div className="card-head"><span>{a.file}</span><span className="pill">{a.pass}</span>{a.rounds ? <span className="muted">round {a.rounds}</span> : null}</div>
      {r.judgment && <p>{r.judgment}</p>}
      {(r.candidates ?? []).map((c, i) => (
        <div key={i} className="card">
          <div className="card-head"><span className="ord">{i + 1}</span><b>{c.case}</b>
            {c.kind && <span className="pill">{c.kind}</span>}{c.stage && <span className="pill">{c.stage}</span>}</div>
          {c.story && <p>{c.story}</p>}
          {c.moral && <p><span className="muted">Moral:</span> {c.moral}</p>}
          {c.fit && <p><span className="muted">How it fits:</span> {c.fit}</p>}
          {c.weakness && <p><span className="muted">Weakness:</span> {c.weakness}</p>}
          {c.source && <p className="muted">Source: {c.source}</p>}
          {c.text && <p className="devedit-diff" style={{whiteSpace: "pre-wrap"}}>{c.text}</p>}
          <div className="devedit-actions">
            <button className="pill on" disabled={!!busy} onClick={() => void go(`c${i}`, "arc_settle", {outcome: "arc", candidate: i + 1, ...(text.trim() ? {reason: text} : {})})}>{busy === `c${i}` ? "…" : "Settle this one"}</button>
          </div>
        </div>
      ))}
      {(r.questions ?? []).length > 0 && <><div className="muted">Questions</div><ul>{r.questions!.map((q, i) => <li key={i}>{q}</li>)}</ul></>}
      {(a.answers ?? []).length > 0 && <><div className="muted">Your answers</div>
        {a.answers!.map((x, i) => <p key={i}><span className="muted">{x.round != null ? `round ${x.round} · ` : ""}{x.at ?? ""}</span><br />{x.text}</p>)}</>}
      {msg && <div className="status bad">{msg}</div>}
      <textarea className="devedit-edit" value={text} onChange={e => setText(e.target.value)} rows={4} placeholder="Answer, or the reason for No case" aria-label="Answer to the arc questions" />
      <div className="devedit-undo"><button className="pill" disabled={!arcUndo.canUndo} onClick={arcUndo.undo}>↶ Undo</button></div>
      <div className="devedit-actions">
        <button className="pill on" disabled={!!busy} onClick={answer}>{busy === "answer" ? "sending…" : "Send answer"}</button>
        <button className="pill" disabled={!!busy} onClick={() => void go("keep", "arc_settle", {outcome: "keep", ...(text.trim() ? {reason: text} : {})})}>{busy === "keep" ? "…" : "Keep current case"}</button>
        <button className="pill" disabled={!!busy} onClick={none}>{busy === "none" ? "…" : "No case"}</button>
      </div>
    </div>
  );
}

function DecisionCard({ d, onDecide }: { d: Decision & {why?: string}; onDecide: (d: Decision, answer: string, why: string, binding: boolean) => Promise<string | null> }) {
  const [answer, setAnswer] = useState(d.answer ?? d.recommended ?? (d.options?.[0] ?? ""));
  const [why, setWhy] = useState(d.reason ?? "");
  const [binding, setBinding] = useState(d.binding ?? true);
  const [saved, setSaved] = useState<{ok: boolean; text: string} | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const err = await onDecide(d, answer, why, binding);
    setBusy(false);
    setSaved(err ? {ok: false, text: err} : {ok: true, text: `Saved ${new Date().toLocaleTimeString()}: ${answer} (${binding ? "binding" : "provisional"})`});
  };
  return (
    <div className="card devedit-decision">
      <div className="card-head"><span className="ord">{d.n}</span>{d.answered ? <span>answered · {d.binding === false ? "provisional" : "binding"}</span> : <span>open</span>}</div>
      <p>{d.question}</p>
      {d.why && <p className="muted">{d.why}</p>}
      {d.options && d.options.length > 0
        ? <div className="devedit-actions">{d.options.map(o => <button key={o} className={`pill${answer === o ? " on" : ""}`} onClick={() => setAnswer(o)}>{o}{d.recommended === o ? " (recommended)" : ""}</button>)}</div>
        : <input className="devedit-reason" value={answer} onChange={e => setAnswer(e.target.value)} placeholder="Your answer" />}
      <input className="devedit-reason" value={why} onChange={e => setWhy(e.target.value)} placeholder="Why (your words)" />
      <label className="muted"><input type="checkbox" checked={!binding} onChange={e => setBinding(!e.target.checked)} /> provisional (may be reconsidered with new evidence)</label>
      <div className="devedit-actions">
        <button className="pill on" disabled={busy} onClick={() => void save()}>{busy ? "saving…" : d.answered ? "Update decision" : "Save decision"}</button>
        {saved && <span className={saved.ok ? "ok" : "bad"}>{saved.text}</span>}
      </div>
    </div>
  );
}

// Reads the letter aloud with the Mac preview voice, through the same road as
// the Try text page (/api/try-text); nothing is saved.
function Listen({ markdown, label = "▶ Listen to the letter", wait = "Preparing audio… (a minute or two for the whole letter)" }: { markdown: string; label?: string; wait?: string }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    if (!url) return;
    void player.current?.play().catch(() => {});
    return () => URL.revokeObjectURL(url);
  }, [url]);
  useEffect(() => { setUrl(""); }, [markdown]);
  const listen = async () => {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/try-text", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-AuthorLM": "1"},
        body: JSON.stringify({markdown}),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Could not read the letter aloud.");
      setUrl(URL.createObjectURL(await response.blob()));
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="listen">
      {!url && <button className="pill on" disabled={busy || !markdown.trim()} onClick={() => void listen()}>
        {busy ? wait : label}</button>}
      {url && <audio ref={player} src={url} controls aria-label="Read aloud" />}
      {error && <span className="bad">{error}</span>}
    </div>
  );
}
