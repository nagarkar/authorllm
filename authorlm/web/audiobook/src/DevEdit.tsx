import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { Transport } from "./transport";
import { MarkdownBox } from "./MarkdownBox";

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

type Tab = "letter" | "review" | "decided";

export function DevEdit({ transport, manuscript }: { transport: Transport; manuscript: string }) {
  const [tab, setTab] = useState<Tab>("letter");
  const [status, setStatus] = useState<Status | null>(null);
  const [letter, setLetter] = useState<{letter: string; map: string; decisions: Decision[]} | null>(null);
  const [next, setNext] = useState<Next | null>(null);
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const [why, setWhy] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState<Item[]>([]);
  const [doneStage, setDoneStage] = useState<{n: number; name: string} | null>(null);

  const call = useCallback(async <T,>(method: string, params: Record<string, unknown> = {}): Promise<T | null> => {
    setError("");
    try {
      const r = await transport.request<T & {error?: string}>(method, {manuscript, ...params});
      if (r && (r as {error?: string}).error) { setError((r as {error: string}).error); return null; }
      return r;
    } catch (err) { setError((err as Error).message); return null; }
  }, [transport, manuscript]);

  const load = useCallback(async () => {
    const [s, l, n, d] = await Promise.all([
      call<Status>("status"),
      call<{letter: string; map: string; decisions: Decision[]}>("letter"),
      call<Next>("next"),
      call<{items: Item[]; stage: {n: number; name: string} | null}>("decided"),
    ]);
    if (d) { setDone(d.items); setDoneStage(d.stage); }
    if (s) setStatus(s);
    if (l) setLetter(l);
    if (n) { setNext(n); setText(n.item?.proposed ?? ""); setReason(""); setWhy(false); }
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
  const decideItem = useCallback(async (verdict: "accept" | "reject" | "defer") => {
    if (!item) return;
    if (verdict === "reject" && !reason.trim()) { setError("Say why in a few words, then reject."); return; }
    setBusy(verdict);
    const edited = item.proposed != null && text !== item.proposed;
    const r = await call("verdict", {id: item.id, verdict, reason, ...(verdict === "accept" && edited ? {text} : {})});
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
          {item.diff ? <InContext it={item} middle={text !== item.proposed
              ? <><s className="del">{item.original}</s> <b className="add">{text}</b></> : diffOf(item)} />
            : <p className="devedit-diff">{item.move ?? item.proposed}</p>}
          {item.proposed != null && <textarea className="devedit-edit" value={text} onChange={e => setText(e.target.value)} rows={Math.min(12, Math.max(3, Math.ceil(text.length / 70)))} aria-label="Proposed text (edit before accepting)" />}
          {why && <div className="devedit-why">{item.reason}{item.finding ? <><br /><span className="muted">Letter finding: {item.finding}</span></> : null}</div>}
          <input id="devedit-reason" className="devedit-reason" placeholder="Reason (needed to reject)" value={reason} onChange={e => setReason(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") void decideItem("reject"); }} />
          <div className="devedit-actions">
            <button className="pill on" disabled={!!busy} onClick={() => void decideItem("accept")}>{busy === "accept" ? "…" : "a Accept"}</button>
            <button className="pill" disabled={!!busy} onClick={() => void decideItem("reject")}>x Reject</button>
            <button className="pill" disabled={!!busy} onClick={() => void decideItem("defer")}>d Defer</button>
            <button className="pill" onClick={() => setWhy(w => !w)}>? Why</button>
          </div>
        </div>
      ) : <div className="card muted">{status?.counts.total ? "Every item has a verdict." : "The queue is empty: the review starts once the queue is built."}</div>)}

      {tab === "decided" && (done.length
        ? done.map(it => <DecidedCard key={it.id} it={it} onRevise={revise} />)
        : <div className="card muted">No item has a verdict yet.</div>)}
    </div>
  );
}

// An item already decided: its verdict, the text that landed, and the
// Review card's controls to change it. Leaving an accept undoes it in the
// file; accepting other wording swaps it (devedit.revise).
function DecidedCard({ it, onRevise }: { it: Item; onRevise: (it: Item, verdict: string, reason: string, text?: string) => Promise<string | null> }) {
  const [text, setText] = useState(it.final ?? it.proposed ?? "");
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
function Listen({ markdown }: { markdown: string }) {
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
        {busy ? "Preparing audio… (a minute or two for the whole letter)" : "▶ Listen to the letter"}</button>}
      {url && <audio ref={player} src={url} controls aria-label="The letter read aloud" />}
      {error && <span className="bad">{error}</span>}
    </div>
  );
}
