import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Transport } from "./transport";
import { recall, remember } from "./remember";

// Mirrors workflow_page.dispatch (authorlm/workflow_page.py): the per-essay
// editing workflow (docs/plans/filter-lens-consolidation.md §11.9). Each
// essay's workflows and their instances; the open instance's current step:
// the three lists with recommendations (Q1, Q3), the letter to rule (Q4,
// Q21d, Q21e), the unit form with its four actions (Q8a, Q8b, Q11), the
// beat plan (Q18), the wrap-up (Q23, Q24), the filter checklist (Q12),
// skips, engine and unit switches with a reason (Q6-Q9), and usage (Q7).
// Every write goes through the workflow package; the page never writes the
// essay file: a change lands only as a form the author accepts.
//
// Where each choice lives (owner's rule in ~/.claude/CLAUDE.md, "Web page
// state survives a reload", 2026-11-21):
// - the essay, the instance and the step being looked at → the URL hash
//   (#<ms>/workflows:<essay>/<instance>/<step>), since they name what is on
//   screen and are worth bookmarking (App.tsx writes the hash from `route`);
// - the listen voice → localStorage `authorlm.workflowVoice` (a personal
//   setting; its own key, because "browser voice" is offered only here and
//   the other pages' Listen buttons cannot play it);
// - the scroll position → localStorage per manuscript and instance
//   (`authorlm.wf.scroll.<ms>.<instance>`), a place in a long page, restored
//   after the instance loads;
// - whether hidden blurbs are shown → localStorage per manuscript.

interface Usage { turns?: number; input?: number; cached?: number; output?: number; dollars?: number; est_subscription?: number; }
interface InstRow { id: string; kind: string; number: number; step: string; engine: string; model: string; created: string; finished: string | null; }
interface Essay { file: string; instances: InstRow[]; open: string | null; new?: boolean; }
interface Overview {
  manuscript: string; essays: Essay[]; kinds: string[]; steps: string[]; engines: string[]; planned_engines: string[];
  units: string[]; modes: string[]; usage: { month: Usage; month_name: string; total: Usage };
}
interface Instance {
  id: string; file: string; kind: string; number: number; step: string; brief: string;
  reference: string[]; voice: string[]; aware: string[]; engine: string; model: string;
  unit: string; construction_mode: string; filter_unit: string; filters: string[]; filters_confirmed: boolean;
  carried_from: string | null;
}
interface Form { seq: number; reason: string; proposed: string; text: string; engine: string; model: string; }
interface Unit { label: string; kind: string; n?: number; beat?: number; before: string; state: string; text?: string; form?: Form; turns?: number; }
interface Walk {
  mode: string; unit: string; asked_unit: string; units: Unit[]; next: string | null; end: boolean; fallback: boolean;
  beat?: number; beat_open?: boolean; beats_before?: number; concluded?: boolean;
  earlier: { label: string; kind: string; state: string; text: string; stretch: number }[];
}
interface Estimate { engine: string; billed: boolean; model?: string; turns?: number; input_per_turn?: number; output_per_turn?: number; per_turn?: number | null; total?: number | null; why?: string; }
interface LintRun { at: string; counts: Record<string, number>; total: number; findings: { severity: string; code: string; message: string; quote: string; line?: number | null }[]; }
interface Finding { item: string; verdict: string | null; reason: string | null; n?: number; title: string; where: string; what: string; why: string; direction: string; tier: string; text: string; }
interface Letter {
  drafted: boolean; redraft: boolean; text: string; findings: Finding[]; keep: string[];
  passes: Record<string, { needed: boolean; reason: string }>; notes: string[];
  discussion: { seq: number; action: string; author: string; reply: string; reason: string; at: string }[];
  accepted_without_findings: boolean;
}
interface Rec { list: string; essay: string; change: string; reason: string; label: string; answer: string; }
interface Decision { id: string; kind: string; reason: string; words: string; detail: Record<string, any>; created_at: string; }
interface Blurb { text: string; last_used: string | null; hidden: number; }
interface View {
  instance: Instance; gate: string | null; next_step: string | null; decisions: Decision[];
  since_last: { added: string[]; removed: string[] }; order: string[]; protected: boolean;
  usage: { instance: Usage; essay: Usage; month: Usage; month_name: string; total: Usage };
  blurbs: Blurb[]; supported_tags: string[];
  letter: Letter; recommendations: { asked: boolean; items: Rec[]; text: string };
  walk?: Walk; wrapup?: { drafted: boolean; items: Unit[]; principles: { words: string; scope: string }[]; text: string };
  checklist?: { passes: { name: string; stage: string; ticked: boolean; why: string; letter?: { needed: boolean; reason: string } }[]; confirmed: boolean; audiobook: boolean };
  principles: Decision[]; skipped: Decision[];
  unplaced?: { label: string; kind: string; text: string }[]; lint?: LintRun | null;
  turns: { seq: number; step: string; unit_label: string; action: string; reason: string }[];
}

export interface WorkflowRoute { essay: string; instance: string; step: string; }
export const parseRoute = (item: string): WorkflowRoute => {
  const [essay = "", instance = "", step = ""] = (item || "").split("/");
  return { essay, instance, step };
};
export const routeItem = (r: WorkflowRoute) => [r.essay, r.instance, r.step].join("/").replace(/\/+$/, "");

const LISTS = ["reference", "voice", "aware"] as const;
const LIST_WORDS: Record<string, string> = {
  reference: "Reference — content binds; never imitated for style",
  voice: "Voice — style to match",
  aware: "Aware — summaries to keep in mind",
};
const STEP_WORDS: Record<string, string> = {
  start: "Start", letter: "Letter", construction: "Construction", wrapup: "Wrap-up",
  filters: "Filters", lint: "Lint", done: "Done",
};
const STATE_WORDS: Record<string, string> = {
  open: "not yet worked", pending: "waiting for you", passed: "no change needed",
  accepted: "accepted", rejected: "kept the original", dropped: "dropped", edited: "your text",
};
const money = (n?: number) => `$${(n || 0).toFixed(2)}`;
const usageLine = (u: Usage) => !u || !u.turns ? "no model turns" :
  `${u.turns} turns · in ${(u.input || 0).toLocaleString()} (cached ${(u.cached || 0).toLocaleString()}) · out ${(u.output || 0).toLocaleString()} · ${money(u.dollars)} billed · ~${money(u.est_subscription)} subscription`;
const short = (f: string) => f.replace(/\.md$/, "");

// ------------------------------------------------------------------ diff

type Piece = [0 | 1 | -1, string];
// Word-level diff (LCS over words and the spaces between them): what the
// form colours, removed struck in red, added in green.
export function diffWords(a: string, b: string): Piece[] {
  const A = a.split(/(\s+)/).filter(Boolean), B = b.split(/(\s+)/).filter(Boolean);
  const n = A.length, m = B.length;
  if (n * m > 400000) return [[-1, a], [1, b]];
  const L: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: Piece[] = [];
  const push = (op: 0 | 1 | -1, s: string) => { const last = out[out.length - 1]; if (last && last[0] === op) last[1] += s; else out.push([op, s]); };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { push(0, A[i]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) { push(-1, A[i]); i++; }
    else { push(1, B[j]); j++; }
  }
  while (i < n) push(-1, A[i++]);
  while (j < m) push(1, B[j++]);
  return out;
}

function Diff({ before, after }: { before: string; after: string }) {
  const pieces = useMemo(() => diffWords(before, after), [before, after]);
  return <p className="devedit-diff wf-diff">{pieces.map(([op, s], k) =>
    op === 0 ? <span key={k}>{s}</span> : op < 0 ? <del key={k} className="del">{s}</del> : <ins key={k} className="add">{s}</ins>)}</p>;
}

// ---------------------------------------------------------------- listen

// The same voices as the audiobook page's drop-down (the Mac `say` voices
// from /api/voices), plus the browser's own speech (Q17). Never ElevenLabs.
const VOICE_KEY = "authorlm.workflowVoice";
const BROWSER = "browser";
const VOICE_EVENT = "authorlm-workflow-voice";
const workflowVoice = () => recall(VOICE_KEY);

export function WorkflowVoicePicker({ manuscript }: { manuscript: string }) {
  const [voices, setVoices] = useState<{ current: string; choices: string[]; missing: string[] } | null>(null);
  const [chosen, setChosen] = useState(workflowVoice());
  useEffect(() => {
    let live = true;
    fetch(`/api/voices?m=${encodeURIComponent(manuscript)}`).then(r => r.json())
      .then(v => { if (live) setVoices(v.choices ? v : { current: "", choices: [], missing: [] }); })
      .catch(() => { if (live) setVoices({ current: "", choices: [], missing: [] }); });
    return () => { live = false; };
  }, [manuscript]);
  const pick = (v: string) => { remember(VOICE_KEY, v); setChosen(v); window.dispatchEvent(new Event(VOICE_EVENT)); };
  if (!voices) return null;
  const value = chosen === BROWSER || voices.choices.includes(chosen) ? chosen : (voices.current || BROWSER);
  return (
    <select className="voice-pick" value={value} aria-label="Voice for Listen" onChange={e => pick(e.target.value === voices.current ? "" : e.target.value)}>
      {voices.choices.map(v => {
        const missing = voices.missing.includes(v);
        return <option key={v} value={v} disabled={missing}>{v}{v === voices.current ? " (preview voice)" : ""}{missing ? " — not installed" : ""}</option>;
      })}
      <option value={BROWSER}>Browser voice</option>
    </select>
  );
}

function ListenBar({ before, reason, after, manuscript }: { before: string; reason: string; after: string; manuscript: string }) {
  const [busy, setBusy] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => { if (url) void player.current?.play().catch(() => {}); return () => { if (url) URL.revokeObjectURL(url); }; }, [url]);
  useEffect(() => () => { try { speechSynthesis.cancel(); } catch { /* none */ } }, []);
  const parts: [string, string[]][] = [
    ["Reason + after", ["reason", "after"]],
    ["Before + reason + after", ["before", "reason", "after"]],
    ["Before", ["before"]], ["Reason", ["reason"]], ["After", ["after"]],
  ];
  const words: Record<string, [string, string]> = {
    before: ["Before.", before], reason: ["The reason.", reason], after: ["After.", after],
  };
  const play = async (label: string, which: string[]) => {
    const segs = which.filter(w => words[w][1].trim());
    if (!segs.length) return;
    setError(""); setUrl("");
    const voice = workflowVoice();
    if (voice === BROWSER) {
      try {
        speechSynthesis.cancel();
        for (const w of segs) for (const t of words[w]) speechSynthesis.speak(new SpeechSynthesisUtterance(t));
      } catch { setError("This browser cannot speak."); }
      return;
    }
    setBusy(label);
    try {
      const markdown = segs.map(w => `${words[w][0]}\n\n${words[w][1]}`).join("\n\n");
      const response = await fetch("/api/try-text", {
        method: "POST", headers: { "Content-Type": "application/json", "X-AuthorLM": "1" },
        body: JSON.stringify({ markdown, voice: voice || undefined, manuscript }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || "Could not read it aloud.");
      setUrl(URL.createObjectURL(await response.blob()));
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(""); }
  };
  return (
    <div className="wf-listen">
      {parts.map(([label, which]) => (
        <button key={label} className="pill" disabled={!!busy || !which.some(w => words[w][1].trim())} onClick={() => void play(label, which)}>
          {busy === label ? "Preparing…" : `▶ ${label}`}</button>
      ))}
      <button className="pill" onClick={() => { try { speechSynthesis.cancel(); } catch { /* none */ } player.current?.pause(); setUrl(""); }}>■ Stop</button>
      {url && <audio ref={player} src={url} controls aria-label="Read aloud" />}
      {error && <span className="bad">{error}</span>}
    </div>
  );
}

// -------------------------------------------------------------- estimate

// Q6: "A billed engine shows its estimated cost when chosen". Asked of the
// server (workflow_page.estimate) whenever a billed engine is picked.
function CostEstimate({ transport, engine, model, essay, id }: { transport: Transport; engine: string; model: string; essay?: string; id?: string }) {
  const [e, setE] = useState<Estimate | null>(null);
  useEffect(() => {
    let live = true;
    if (!engine) { setE(null); return; }
    transport.request<Estimate>("estimate", { engine, model, essay: essay || "", id: id || "" })
      .then(r => { if (live) setE(r); }).catch(() => { if (live) setE(null); });
    return () => { live = false; };
  }, [transport, engine, model, essay, id]);
  if (!e || !e.billed) return null;
  return <div className="muted wf-estimate">{e.engine}{e.model ? ` (${e.model})` : ""} is billed. {e.total != null
    ? `Estimated ${money(e.total)} for the whole walk: about ${e.turns} turns at ~${money(e.per_turn ?? 0)} each (${(e.input_per_turn || 0).toLocaleString()} tokens in, ${e.output_per_turn} out, uncached; a floor).`
    : `No estimate: ${e.why}.`}</div>;
}

// ------------------------------------------------------------- unit form

function UnitForm({ unit, newEssay, blurbs, tags, manuscript, busy, run, showHidden, setShowHidden }: {
  unit: Unit; newEssay: boolean; blurbs: Blurb[]; tags: string[]; manuscript: string; busy: boolean;
  run: (method: string, params: Record<string, unknown>) => Promise<boolean>;
  showHidden: boolean; setShowHidden: (v: boolean) => void;
}) {
  const [mode, setMode] = useState<"" | "redo" | "edit">("");
  const [ticked, setTicked] = useState<string[]>([]);
  const [free, setFree] = useState("");
  const [newBlurb, setNewBlurb] = useState("");
  const [text, setText] = useState("");
  const form = unit.form;
  const pending = unit.state === "pending";
  const resolved = ["accepted", "rejected", "dropped", "edited", "passed"].includes(unit.state);
  const after = pending ? (form?.proposed || "") : (unit.text ?? "");
  const noOriginal = !unit.before.trim();
  const bracket = /\[[^\]]*\]/.test(text);
  const visible = blurbs.filter(b => showHidden || !b.hidden);
  const title = unit.kind === "plan" ? `Beat ${unit.beat}: plan` : unit.beat ? `Beat ${unit.beat}: ${unit.kind === "section" ? "section" : `paragraph ${unit.n}`}`
    : unit.label.startsWith("w:") ? `${unit.kind} ${unit.label.split(":")[2]}` : `${unit.kind} ${unit.n ?? unit.label}`;
  return (
    <div className={`card wf-unit ${unit.state}`} id={`unit-${unit.label}`}>
      <div className="card-head"><span>{title}</span><span className={`state${resolved ? " done" : ""}`}>{STATE_WORDS[unit.state] || unit.state}</span>
        {form?.engine && <span className="muted">{form.engine}{form.model ? ` (${form.model})` : ""}</span>}</div>
      {pending && form && <>
        {/* The reason sits above the new text (Q16). */}
        <div className="wf-reason"><b>Reason:</b> {form.reason || <i className="muted">(none given)</i>}</div>
        {form.text && <div className="muted wf-said">{form.text}</div>}
        {noOriginal ? <p className="devedit-diff"><ins className="add">{form.proposed}</ins></p>
          : <Diff before={unit.before} after={form.proposed} />}
        <ListenBar before={unit.before} reason={form.reason} after={form.proposed} manuscript={manuscript} />
      </>}
      {!pending && <>
        {resolved && unit.state !== "passed" && unit.before && unit.text !== unit.before ? <Diff before={unit.before} after={unit.text || ""} />
          : <p className="devedit-diff">{unit.text || unit.before || <i className="muted">(empty)</i>}</p>}
        {unit.state === "passed" && form?.text && <div className="muted">{form.text}</div>}
      </>}
      {mode === "redo" && <div className="wf-redo">
        <div className="muted">What is wrong (tick any; most recent first)</div>
        <div className="wf-blurbs">
          {visible.map(b => (
            <span key={b.text} className={`wf-blurb${b.hidden ? " hidden" : ""}`}>
              <label><input type="checkbox" checked={ticked.includes(b.text)}
                onChange={e => setTicked(t => e.target.checked ? [...t, b.text] : t.filter(x => x !== b.text))} /> {b.text}</label>
              <button className="wf-x" title={b.hidden ? "Show this blurb again" : "Hide this blurb"} aria-label={b.hidden ? `Show ${b.text}` : `Hide ${b.text}`}
                onClick={() => void run("blurb_hide", { text: b.text, hidden: !b.hidden })}>{b.hidden ? "↺" : "×"}</button>
            </span>))}
        </div>
        <div className="devedit-actions">
          <input className="wf-input" value={newBlurb} placeholder="a new blurb (two or three words)" onChange={e => setNewBlurb(e.target.value)} />
          <button className="pill" disabled={!newBlurb.trim() || busy} onClick={async () => {
            if (await run("blurb_add", { blurbs: [newBlurb.trim()] })) { setTicked(t => [...t, newBlurb.trim()]); setNewBlurb(""); }
          }}>Add</button>
          <label className="muted"><input type="checkbox" checked={showHidden} onChange={e => setShowHidden(e.target.checked)} /> show hidden</label>
        </div>
        <textarea className="devedit-edit" rows={3} value={free} placeholder="Or say it in your words (the engine may turn it into a new blurb)" onChange={e => setFree(e.target.value)} />
        <div className="devedit-actions">
          <button className="pill on" disabled={busy || (!ticked.length && !free.trim())} onClick={async () => {
            if (await run("act", { label: unit.label, action: "redo", blurbs: ticked, free })) { setMode(""); setTicked([]); setFree(""); }
          }}>{busy ? "Asking…" : "Redo"}</button>
          <button className="pill" onClick={() => setMode("")}>Cancel</button>
        </div>
      </div>}
      {mode === "edit" && <div className="wf-edit">
        <textarea className="devedit-edit" autoFocus value={text} onChange={e => setText(e.target.value)}
          rows={Math.min(16, Math.max(4, Math.ceil(text.length / 70) + text.split("\n").length))} aria-label="Your text for this unit" />
        <div className="muted">Tags: {tags.map(t => `[${t}: …]`).join(", ")}, or any [note] to the engine at that spot. {bracket
          ? "This edit has a bracket: it goes back to the engine as a redo." : "No bracket: this is your text, kept as is."}</div>
        <div className="devedit-actions">
          <button className="pill on" disabled={busy || !text.trim()} onClick={async () => {
            if (await run("act", { label: unit.label, action: "edit", text })) setMode("");
          }}>{busy ? "…" : bracket ? "Send as redo" : "Keep my text"}</button>
          <button className="pill" onClick={() => setMode("")}>Cancel</button>
        </div>
      </div>}
      {!mode && <div className="devedit-actions">
        {pending && <>
          <button className="pill on" disabled={busy} onClick={() => void run("act", { label: unit.label, action: "accept" })}>Accept</button>
          <button className="pill" disabled={busy} onClick={() => void run("act", { label: unit.label, action: "reject" })}
            title={noOriginal || newEssay ? "Drop the proposed text" : "Keep the original"}>{noOriginal ? "Reject (drop)" : "Reject (keep original)"}</button>
          <button className="pill" disabled={busy} onClick={() => setMode("redo")}>Redo…</button>
          <button className="pill" disabled={busy} onClick={() => { setText(form?.proposed || unit.before); setMode("edit"); }}>Edit…</button>
        </>}
        {resolved && unit.state !== "passed" && <button className="pill" disabled={busy} onClick={() => void run("reopen", { label: unit.label })}>Reopen</button>}
      </div>}
    </div>
  );
}

// ---------------------------------------------------------------- steps

function StartStep({ v, run, busy }: { v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean }) {
  const inst = v.instance;
  const [add, setAdd] = useState<Record<string, string>>({});
  const recs = v.recommendations;
  return <>
    {inst.brief && <div className="card"><div className="card-head"><span>The brief</span></div><p>{inst.brief}</p></div>}
    {inst.carried_from && <div className="card muted">Choices carried from the last instance.
      {(v.since_last.added.length || v.since_last.removed.length) ? ` Since then: added ${v.since_last.added.join(", ") || "none"}; removed ${v.since_last.removed.join(", ") || "none"}.` : " The reading order has not changed since."}</div>}
    {LISTS.map(which => (
      <div className="card" key={which}>
        <div className="card-head"><span>{LIST_WORDS[which]}</span></div>
        {inst[which].length ? <ul className="wf-list">{inst[which].map(e => <li key={e}>{short(e)}
          {inst.step !== "done" && <button className="wf-x" aria-label={`Remove ${e}`} disabled={busy}
            onClick={() => void run("list", { list: which, essay: e, change: "remove" })}>×</button>}</li>)}</ul>
          : <p className="muted">Empty: the author's explicit choice.</p>}
        {inst.step !== "done" && <div className="devedit-actions">
          <select value={add[which] || ""} onChange={e => setAdd(a => ({ ...a, [which]: e.target.value }))} aria-label={`Add to ${which}`}>
            <option value="">Add an essay…</option>
            {v.order.filter(e => e !== inst.file && !inst[which].includes(e)).map(e => <option key={e} value={e}>{short(e)}</option>)}
          </select>
          <button className="pill" disabled={!add[which] || busy} onClick={async () => {
            if (await run("list", { list: which, essay: add[which], change: "add" })) setAdd(a => ({ ...a, [which]: "" }));
          }}>Add</button>
        </div>}
      </div>))}
    <div className="card">
      <div className="card-head"><span>Recommendations</span></div>
      {!recs.asked && <p className="muted">The engine reads the summaries and recommends essays for each list, with a reason each; nothing changes without your word.</p>}
      {recs.text && <p className="muted">{recs.text}</p>}
      {recs.items.map(r => (
        <div key={r.label} className="wf-rec">
          <div><b>{r.change === "remove" ? "Remove" : "Add"} {short(r.essay)}</b> {r.change === "remove" ? "from" : "to"} <i>{r.list}</i>: {r.reason}</div>
          {r.answer ? <span className="state done">{r.answer}</span> : <div className="devedit-actions">
            <button className="pill on" disabled={busy} onClick={() => void run("rec", { list: r.list, essay: r.essay, change: r.change, reason: r.reason, verdict: "accept" })}>Accept</button>
            <button className="pill" disabled={busy} onClick={() => void run("rec", { list: r.list, essay: r.essay, change: r.change, verdict: "decline" })}>Decline</button>
          </div>}
        </div>))}
      {inst.step === "start" && <button className="pill on" disabled={busy} onClick={() => void run("recommend", {})}>
        {busy ? "Asking…" : recs.asked ? "Ask again" : "Recommend"}</button>}
    </div>
  </>;
}

function LetterStep({ v, run, busy }: { v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean }) {
  const L = v.letter;
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [reply, setReply] = useState("");
  const here = v.instance.step === "letter";
  return <>
    {!L.drafted && <div className="card">
      <p className="muted">One reading of the whole essay: numbered findings with a tier each, what to keep, which passes the essay needs, and short paragraph notes.</p>
      {here && <div className="devedit-actions">
        <button className="pill on" disabled={busy} onClick={() => void run("letter", {})}>{busy ? "Writing…" : "Write the letter"}</button>
        {v.instance.kind === "new" && <button className="pill" disabled={busy} onClick={() => void run("accept_letter", {})}
          title="A new essay's letter is its beat plan: accept without findings">Accept with no findings</button>}
      </div>}
    </div>}
    {L.drafted && here && (L.redraft || !L.findings.length) && <div className="card devedit-actions">
      {!L.findings.length && <span className="muted">The letter as written has no findings.</span>}
      {!L.findings.length && <button className="pill on" disabled={busy} onClick={() => void run("accept_letter", {})}>Accept with no findings</button>}
      {L.redraft && <button className="pill" disabled={busy} onClick={() => void run("letter", {})}
        title="Nothing is ruled yet: a new draft replaces this one and its findings">{busy ? "Writing…" : "Redraft the letter"}</button>}
    </div>}
    {L.findings.map(f => (
      <div key={f.item} className={`card wf-finding${f.verdict ? " ruled" : ""}`}>
        <div className="card-head"><span>{f.item}</span>{f.tier && <span className={`wf-tier ${f.tier}`}>{f.tier}</span>}
          {f.verdict && <span className="state done">{f.verdict}{f.reason ? `: ${f.reason}` : ""}</span>}</div>
        <p><b>{f.title}</b></p>
        {f.where && <p><span className="muted">Where:</span> {f.where}</p>}
        {f.what && <p><span className="muted">What is wrong:</span> {f.what}</p>}
        {f.why && <p><span className="muted">Why it matters:</span> {f.why}</p>}
        {f.direction && <p><span className="muted">Direction:</span> {f.direction}</p>}
        {!f.what && !f.direction && f.text && <pre className="wf-pre">{f.text}</pre>}
        {here && <div className="devedit-actions">
          <input className="wf-input" placeholder="reason (optional)" value={reasons[f.item] ?? ""} onChange={e => setReasons(r => ({ ...r, [f.item]: e.target.value }))} />
          {(["accept", "reject", "defer"] as const).map(verdict => (
            <button key={verdict} className={`pill${f.verdict === verdict ? " on" : ""}`} disabled={busy}
              onClick={() => void run("finding", { item: f.item, verdict, reason: reasons[f.item] || "" })}>{verdict[0].toUpperCase() + verdict.slice(1)}</button>))}
        </div>}
      </div>))}
    {L.drafted && <div className="card">
      {L.keep.length > 0 && <><div className="card-head"><span>Keep</span></div><ul>{L.keep.map((k, i) => <li key={i}>{k}</li>)}</ul></>}
      {Object.keys(L.passes).length > 0 && <><div className="card-head"><span>Passes</span></div>
        <ul>{Object.entries(L.passes).map(([name, p]) => <li key={name}><b>{name}</b>: {p.needed ? "needed" : "not needed"}{p.reason ? ` — ${p.reason}` : ""}</li>)}</ul></>}
      {L.notes.length > 0 && <><div className="card-head"><span>Paragraph notes</span></div>
        <p className="muted">Not ruled one by one; construction picks each up at its paragraph.</p>
        <ul>{L.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></>}
      <details><summary className="muted">The letter as written</summary><pre className="wf-pre">{L.text}</pre></details>
    </div>}
    {(L.drafted || L.discussion.length > 0) && <div className="card">
      <div className="card-head"><span>Discussion</span></div>
      {L.discussion.map(d => <div key={d.seq} className="wf-turn">
        {d.author && <p><b>You:</b> {d.author}</p>}{d.reply && <p><b>Editor:</b> {d.reply}</p>}</div>)}
      {here && <><textarea className="devedit-edit" rows={3} value={reply} placeholder="Debate a finding, ask a question…" onChange={e => setReply(e.target.value)} />
        <button className="pill on" disabled={busy || !reply.trim()} onClick={async () => { if (await run("letter_reply", { text: reply })) setReply(""); }}>{busy ? "…" : "Send"}</button></>}
    </div>}
  </>;
}

function WalkStep({ v, run, busy, manuscript, showHidden, setShowHidden }: {
  v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean; manuscript: string;
  showHidden: boolean; setShowHidden: (b: boolean) => void;
}) {
  const w = v.walk!;
  const inst = v.instance;
  const newEssay = inst.kind === "new";
  const units = w.units;
  return <>
    <div className="card muted">
      {w.mode === "beats" ? `Beat by beat: each beat's plan, then its text ${inst.unit === "section" ? "as a whole section" : "paragraph by paragraph"}.`
        : `${inst.step === "filters" ? "The filters" : "The accepted findings"}, ${w.unit} by ${w.unit}.`}
      {w.fallback && ` The essay has no subheadings, so it goes by paragraph.`}
      {inst.step === "construction" && v.letter.notes.length > 0 && ` ${v.letter.notes.length} paragraph note(s) from the letter.`}
    </div>
    {w.earlier.length > 0 && <details className="card">
      <summary className="muted">{w.earlier.length} unit(s) ruled before the last switch of unit or mode: their text is carried into this walk</summary>
      <ul>{w.earlier.map(e => <li key={`${e.stretch}-${e.label}`}><b>{e.label}</b> ({STATE_WORDS[e.state] || e.state}): {e.text || <i className="muted">(empty)</i>}</li>)}</ul>
    </details>}
    {inst.step === "filters" && (v.unplaced || []).length > 0 && <div className="card muted">
      Accepted at the wrap-up but not found in the essay, so not placed: {(v.unplaced || []).map(u => `${u.label} (“${u.text}”)`).join("; ")}.</div>}
    {units.map(u => <UnitForm key={u.label} unit={u} newEssay={newEssay} blurbs={v.blurbs} tags={v.supported_tags}
      manuscript={manuscript} busy={busy} run={run} showHidden={showHidden} setShowHidden={setShowHidden} />)}
    {(w.end || (w.next && !units.some(u => u.state === "pending")) || (w.mode === "beats" && !w.concluded)) && <div className="card devedit-actions">
      {w.next && !units.some(u => u.state === "pending") &&
        <button className="pill on" disabled={busy} onClick={() => void run("propose", {})}>{busy ? "Asking…" : `Work ${w.next}`}</button>}
      {w.mode === "beats" && w.beat_open && !units.some(u => u.state === "pending") &&
        <button className="pill" disabled={busy} onClick={() => void run("beat_done", {})}>Beat {w.beat} done — next beat</button>}
      {w.mode === "beats" && !w.concluded && inst.step === "construction" && !units.some(u => u.state === "pending") &&
        <button className="pill" disabled={busy} onClick={() => void run("conclude", {})}
          title="Construction ends when you conclude it (Q18)">The essay is built — conclude the beats</button>}
      {w.end && <span className="muted">{w.mode === "beats" ? "The beats are concluded." : "The walk has reached the end of the essay."}</span>}
    </div>}
  </>;
}

function WrapupStep({ v, run, busy, manuscript, showHidden, setShowHidden }: {
  v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean; manuscript: string;
  showHidden: boolean; setShowHidden: (b: boolean) => void;
}) {
  const w = v.wrapup!;
  const ticked = new Set(v.principles.map(p => p.words));
  return <>
    {!w.drafted && <div className="card"><p className="muted">The removed passages, the placeholders left, the terms to check, and the principles you stated during the walk.</p>
      <button className="pill on" disabled={busy} onClick={() => void run("wrapup", {})}>{busy ? "Asking…" : "Show the wrap-up"}</button></div>}
    {w.text && <div className="card"><pre className="wf-pre">{w.text}</pre></div>}
    {w.items.map(u => <UnitForm key={u.label} unit={u} newEssay={false} blurbs={v.blurbs} tags={v.supported_tags}
      manuscript={manuscript} busy={busy} run={run} showHidden={showHidden} setShowHidden={setShowHidden} />)}
    {w.principles.length > 0 && <div className="card">
      <div className="card-head"><span>Principles stated during the walk</span></div>
      <p className="muted">Tick one to make it a candidate style rule (a rule only if accepted later).</p>
      {w.principles.map(p => <div key={p.words} className="devedit-actions">
        <label><input type="checkbox" checked={ticked.has(p.words)} disabled={busy || ticked.has(p.words)}
          onChange={() => void run("principle", { words: p.words, scope: p.scope })} /> {p.words}</label>
        <span className="muted">scope: {p.scope || "not said"}</span></div>)}
    </div>}
  </>;
}

function Checklist({ v, run, busy }: { v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean }) {
  const c = v.checklist!;
  const [ticks, setTicks] = useState<Record<string, boolean>>(() => Object.fromEntries(c.passes.map(p => [p.name, p.ticked])));
  useEffect(() => setTicks(Object.fromEntries(c.passes.map(p => [p.name, p.ticked]))), [c]);
  const here = v.instance.step === "filters";
  return <div className="card">
    <div className="card-head"><span>Filter passes</span>{c.confirmed && <span className="state done">chosen</span>}</div>
    <p className="muted">Ticking only includes a pass; they always run in this standard order.</p>
    {c.passes.map(p => <div key={p.name} className="wf-check">
      <label><input type="checkbox" checked={!!ticks[p.name]} disabled={!here || busy}
        onChange={e => setTicks(t => ({ ...t, [p.name]: e.target.checked }))} /> <b>{p.name}</b></label>
      <span className="muted">{p.why}</span></div>)}
    {here && <button className="pill on" disabled={busy} onClick={() => void run("filters", { names: c.passes.filter(p => ticks[p.name]).map(p => p.name) })}>
      {c.confirmed ? "Change the selection" : "Use these passes"}</button>}
  </div>;
}

// --------------------------------------------------------------- the page

export function Workflows({ transport, manuscript, route, onRoute }: {
  transport: Transport; manuscript: string; route: WorkflowRoute; onRoute: (r: WorkflowRoute) => void;
}) {
  const [ov, setOv] = useState<Overview | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showHidden, setShowHiddenState] = useState(() => recall(`authorlm.wf.hiddenBlurbs.${manuscript}`) === "1");
  const setShowHidden = (b: boolean) => { remember(`authorlm.wf.hiddenBlurbs.${manuscript}`, b ? "1" : ""); setShowHiddenState(b); };
  const [startForm, setStartForm] = useState({ kind: "refine", brief: "", engine: "", model: "" });
  const [sw, setSw] = useState({ engine: "", model: "", reason: "", unit: "", mode: "", unitReason: "", skipReason: "", skipTo: "" });

  const loadOv = useCallback(() => transport.request<Overview>("overview", {}).then(o => { setOv(o); setError(""); })
    .catch(err => setError((err as Error).message)), [transport]);
  useEffect(() => { void loadOv(); }, [loadOv]);

  // The essay's instance to show: the one in the URL, else its open one,
  // else its latest.
  const essay = ov?.essays.find(e => e.file === route.essay) || null;
  const instanceId = route.instance || essay?.open || essay?.instances[essay.instances.length - 1]?.id || "";
  const loadView = useCallback(async () => {
    if (!instanceId) { setView(null); return; }
    try { setView(await transport.request<View>("instance", { id: instanceId })); setError(""); }
    catch (err) { setError((err as Error).message); }
  }, [transport, instanceId]);
  useEffect(() => { void loadView(); }, [loadView]);

  // Back to the place on the page once the instance is there; then keep it.
  const restored = useRef("");
  useEffect(() => {
    if (!view || restored.current === view.instance.id) return;
    restored.current = view.instance.id;
    const y = Number(recall(`authorlm.wf.scroll.${manuscript}.${view.instance.id}`) || 0);
    if (y) window.scrollTo({ top: y });
  }, [view, manuscript]);
  useEffect(() => {
    if (!view) return;
    const id = view.instance.id;
    let t = 0;
    const on = () => { window.clearTimeout(t); t = window.setTimeout(() => remember(`authorlm.wf.scroll.${manuscript}.${id}`, String(Math.round(window.scrollY))), 250); };
    window.addEventListener("scroll", on, { passive: true });
    return () => { window.removeEventListener("scroll", on); window.clearTimeout(t); };
  }, [view?.instance.id, manuscript]);

  const run = useCallback(async (method: string, params: Record<string, unknown>): Promise<boolean> => {
    if (!view) return false;
    setBusy(true);
    try {
      const out = await transport.request<View | unknown>(method, { id: view.instance.id, ...params });
      if (out && typeof out === "object" && "instance" in (out as View)) setView(out as View);
      else await loadView();
      setError("");
      void loadOv();  // the essays' steps and the month's usage move with every action
      return true;
    } catch (err) { setError((err as Error).message); return false; }
    finally { setBusy(false); }
  }, [transport, view, loadView, loadOv]);

  if (!ov) return <div className="card">{error ? <span className="bad">{error}</span> : "Loading workflows…"}</div>;
  const inst = view?.instance;
  const shownStep = route.step && inst && ov.steps.indexOf(route.step) <= ov.steps.indexOf(inst.step) ? route.step : inst?.step || "";
  const setStep = (s: string) => onRoute({ ...route, essay: inst?.file || route.essay, instance: inst?.id || "", step: s === inst?.step ? "" : s });

  return (
    <div className="wf">
      {error && <div className="status bad">{error}</div>}
      <div className="card">
        <div className="card-head"><span>Workflows</span><span className="muted">{ov.usage.month_name}: {usageLine(ov.usage.month)}</span></div>
        <div className="wf-essays">
          {ov.essays.map(e => {
            const latest = e.instances[e.instances.length - 1];
            return <button key={e.file} className={`wf-essay${e.file === route.essay ? " on" : ""}`}
              onClick={() => onRoute({ essay: e.file, instance: "", step: "" })}>
              <b>{short(e.file)}</b>
              <span className="muted">{latest ? `${latest.kind} #${latest.number} · ${STEP_WORDS[latest.step]}` : "no workflow yet"}
                {e.instances.length > 1 ? ` · ${e.instances.length} instances` : ""}</span>
            </button>;
          })}
        </div>
      </div>

      {route.essay && <div className="card">
        <div className="card-head"><span>{short(route.essay)}</span></div>
        {essay && essay.instances.length > 0 && <div className="devedit-actions">
          {essay.instances.map(i => <button key={i.id} className={`pill${i.id === instanceId ? " on" : ""}`}
            onClick={() => onRoute({ essay: route.essay, instance: i.id, step: "" })}>{i.kind} #{i.number}: {STEP_WORDS[i.step]}</button>)}
        </div>}
        {!essay?.open && <div className="wf-start">
          <div className="muted">Start a workflow on this essay. The lists, engine, units and filters carry over from its last instance.</div>
          <div className="devedit-actions">
            <select value={startForm.kind} onChange={e => setStartForm(f => ({ ...f, kind: e.target.value }))} aria-label="Kind">
              {ov.kinds.map(k => <option key={k} value={k}>{k}</option>)}</select>
            <select value={startForm.engine} onChange={e => setStartForm(f => ({ ...f, engine: e.target.value }))} aria-label="Engine">
              <option value="">engine: carried over (or the default)</option>
              {ov.engines.map(k => <option key={k} value={k}>{k}{k === "openai-api" ? " (billed)" : ""}</option>)}</select>
            <input className="wf-input" value={startForm.model} placeholder="model (optional)" onChange={e => setStartForm(f => ({ ...f, model: e.target.value }))} />
          </div>
          <CostEstimate transport={transport} engine={startForm.engine} model={startForm.model} essay={route.essay} />
          <textarea className="devedit-edit" rows={3} value={startForm.brief} placeholder="The brief: what to build or improve, and what you think is wrong"
            onChange={e => setStartForm(f => ({ ...f, brief: e.target.value }))} />
          <button className="pill on" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              const r = await transport.request<{ id: string }>("start", { essay: route.essay, ...startForm });
              setStartForm(f => ({ ...f, brief: "" })); await loadOv(); onRoute({ essay: route.essay, instance: r.id, step: "" });
            } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
          }}>Start</button>
        </div>}
      </div>}

      {view && inst && <>
        <div className="card wf-head">
          <div className="card-head"><span>{short(inst.file)} · {inst.kind} #{inst.number}</span>
            <span className="muted">{inst.engine}{inst.model ? ` (${inst.model})` : ""} · {inst.construction_mode}, by {inst.unit}; filters by {inst.filter_unit}</span>
            {view.protected && <span className="wf-tier must" title="register = &quot;protected&quot; in the toc">protected register</span>}</div>
          <nav className="devedit-tabs wf-steps" aria-label="Steps">
            {ov.steps.map(s => {
              const reached = ov.steps.indexOf(s) <= ov.steps.indexOf(inst.step);
              return <button key={s} className={`pill${s === shownStep ? " on" : ""}`} disabled={!reached} onClick={() => setStep(s)}>
                {STEP_WORDS[s]}{s === inst.step ? " •" : ""}</button>;
            })}
          </nav>
          {inst.step !== "done" && <div className="wf-gate">
            {view.gate ? <span className="muted">Next ({STEP_WORDS[view.next_step || ""]}) waits: {view.gate}</span>
              : <button className="pill on" disabled={busy} onClick={() => void run("advance", {})}>On to {STEP_WORDS[view.next_step || ""]}</button>}
          </div>}
          <div className="muted">Usage — this instance: {usageLine(view.usage.instance)}; this essay: {usageLine(view.usage.essay)}; {view.usage.month_name}: {usageLine(view.usage.month)}</div>
          {inst.engine === "openai-api" && <div className="muted">openai-api is billed: each turn's dollars are in the usage line.</div>}
          {inst.engine === "openai-api" && inst.step !== "done" && <CostEstimate transport={transport} engine={inst.engine} model={inst.model} id={inst.id} />}
          {inst.step !== "done" && <details className="wf-settings">
            <summary>Switch engine or unit, or skip a step</summary>
            <div className="devedit-actions">
              <select value={sw.engine || inst.engine} onChange={e => setSw(s => ({ ...s, engine: e.target.value }))} aria-label="Engine">
                {ov.engines.map(k => <option key={k} value={k}>{k}</option>)}</select>
              <input className="wf-input" placeholder={`model (now: ${inst.model || "default"})`} value={sw.model} onChange={e => setSw(s => ({ ...s, model: e.target.value }))} />
              <input className="wf-input" placeholder="why switch" value={sw.reason} onChange={e => setSw(s => ({ ...s, reason: e.target.value }))} />
              <button className="pill" disabled={busy} onClick={async () => {
                if (await run("engine", { engine: sw.engine || inst.engine, model: sw.model || null, reason: sw.reason })) setSw(s => ({ ...s, engine: "", model: "", reason: "" }));
              }}>Switch engine</button>
            </div>
            {sw.engine && sw.engine !== inst.engine && <CostEstimate transport={transport} engine={sw.engine} model={sw.model} id={inst.id} />}
            <div className="devedit-actions">
              <select value={sw.unit} onChange={e => setSw(s => ({ ...s, unit: e.target.value }))} aria-label="Unit">
                <option value="">unit…</option>{ov.units.map(u => <option key={u} value={u}>{u}</option>)}</select>
              {inst.kind !== "new" && <select value={sw.mode} onChange={e => setSw(s => ({ ...s, mode: e.target.value }))} aria-label="Construction mode">
                <option value="">construction mode…</option>{ov.modes.map(u => <option key={u} value={u}>{u}</option>)}</select>}
              <input className="wf-input" placeholder="why switch" value={sw.unitReason} onChange={e => setSw(s => ({ ...s, unitReason: e.target.value }))} />
              <button className="pill" disabled={busy || (!sw.unit && !sw.mode)} onClick={async () => {
                if (await run("unit", { unit: sw.unit || null, mode: sw.mode || null, reason: sw.unitReason })) setSw(s => ({ ...s, unit: "", mode: "", unitReason: "" }));
              }}>Switch</button>
            </div>
            <div className="devedit-actions">
              <select value={sw.skipTo} onChange={e => setSw(s => ({ ...s, skipTo: e.target.value }))} aria-label="Skip to">
                <option value="">skip to the next step</option>
                {ov.steps.slice(ov.steps.indexOf(inst.step) + 2).map(s => <option key={s} value={s}>skip to {STEP_WORDS[s]}</option>)}</select>
              <input className="wf-input wide" placeholder="one sentence: why skip" value={sw.skipReason} onChange={e => setSw(s => ({ ...s, skipReason: e.target.value }))} />
              <button className="pill" disabled={busy || !sw.skipReason.trim()} onClick={async () => {
                if (await run("skip", { reason: sw.skipReason, to: sw.skipTo || null })) setSw(s => ({ ...s, skipReason: "", skipTo: "" }));
              }}>Skip</button>
            </div>
          </details>}
          {view.skipped.length > 0 && <ul className="muted">{view.skipped.map(d => <li key={d.id}>Skipped {(d.detail.skipped || []).join(", ")}: {d.reason}</li>)}</ul>}
        </div>

        {shownStep === "start" && <StartStep v={view} run={run} busy={busy} />}
        {shownStep === "letter" && <LetterStep v={view} run={run} busy={busy} />}
        {(shownStep === "construction" || shownStep === "filters") && shownStep === inst.step && view.walk && <>
          {shownStep === "filters" && view.checklist && <Checklist v={view} run={run} busy={busy} />}
          {(shownStep === "construction" || inst.filters_confirmed) &&
            <WalkStep v={view} run={run} busy={busy} manuscript={manuscript} showHidden={showHidden} setShowHidden={setShowHidden} />}
        </>}
        {shownStep === "wrapup" && shownStep === inst.step && view.wrapup &&
          <WrapupStep v={view} run={run} busy={busy} manuscript={manuscript} showHidden={showHidden} setShowHidden={setShowHidden} />}
        {shownStep === "lint" && inst.step === "lint" && <div className="card">
          {view.checklist && <p className="muted">Passes run: {inst.filters.join(", ") || "none"}.</p>}
          <p className="muted">The deterministic lint and proof checks over the essay as it now stands (<code>authorlm book lint</code>'s rules), with every ruling of this instance applied.</p>
          <button className="pill on" disabled={busy} onClick={() => void run("lint", {})}>{busy ? "Linting…" : view.lint ? "Run lint again" : "Run lint"}</button>
          {view.lint && <>
            <p>{view.lint.total ? `${view.lint.total} finding(s): ${Object.entries(view.lint.counts).map(([k, n]) => `${n} ${k}`).join(", ")}` : "Clean."}</p>
            <ul>{view.lint.findings.map((f, k) => <li key={k}><b>[{f.severity}] {f.code}</b>: {f.message}{f.quote ? <> — «{f.quote}»</> : null}</li>)}</ul>
          </>}</div>}
        {shownStep !== inst.step && !["start", "letter"].includes(shownStep) && <div className="card">
          <p className="muted">This step is behind the instance; its turns are on the record:</p>
          <ul>{view.turns.filter(t => t.step === shownStep).map(t => <li key={t.seq}>{t.unit_label || "—"} · {t.action}{t.reason ? ` · ${t.reason}` : ""}</li>)}</ul>
        </div>}
        {shownStep === "done" && <div className="card"><p>This instance is done.</p></div>}
      </>}
    </div>
  );
}
