import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
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
// - an unsaved draft of the brief → localStorage per manuscript and
//   instance (`authorlm.wf.briefDraft.<ms>.<instance>`), half-written text
//   that is personal and not worth a link; a reload reopens the editor
//   with it, and Save or Cancel clears it;
// - an unsaved edit of the letter's Keep list or of one finding →
//   localStorage per manuscript and instance (`authorlm.wf.keepDraft.<ms>.
//   <instance>`, `authorlm.wf.findingDraft.<ms>.<instance>.<item>`), as
//   JSON: half-made edits, personal, not worth a link; a reload reopens the
//   editor with them, and Save or Cancel clears them;
// - an unsent note for "Revise the letter" → localStorage per manuscript
//   and instance (`authorlm.wf.reviseNote.<ms>.<instance>`), half-written
//   text like the brief's draft; sending or Cancel clears it;
// - the reason and brief of an unsent "Start over from here" →
//   localStorage per manuscript and instance (`authorlm.wf.restartDraft.
//   <ms>.<instance>`, JSON), cleared when the new instance starts;
// - an unsent message to the engine about the letter or about one unit →
//   localStorage per manuscript, instance and unit (`authorlm.wf.discuss.
//   <ms>.<instance>.<label>`, label "letter" for the letter): half-written
//   text; it is cleared only once the server has taken the message, which
//   then shows in the thread from the running turn until the reply lands;
// - whether hidden blurbs are shown → localStorage per manuscript.

interface Usage { turns?: number; input?: number; cached?: number; output?: number; dollars?: number; est_subscription?: number; }
interface InstRow { id: string; kind: string; number: number; step: string; engine: string; model: string; created: string; finished: string | null; abandoned?: boolean; }
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
// A change of a form (workflow/edits.py): replace, insert, cut or move.
interface Change { kind: "replace" | "insert" | "cut" | "move"; original: string; where: string; quote: string; text: string; }
interface Placement { where: string; quote: string; supersedes: string[]; }
interface Form { seq: number; reason: string; proposed: string; text: string; engine: string; model: string; changes?: Change[]; placement?: Placement | null; mechanical?: boolean; }
// One exchange of a discussion: the author's words and the engine's reply.
interface Said { seq: number; author: string; reply: string; at: string; earlier?: boolean; }
interface Unit {
  label: string; kind: string; n?: number; beat?: number; before: string; state: string; text?: string; form?: Form; turns?: number; thread?: Said[];
  anchor?: { where: string; quote: string }; placement?: Placement | null; applied?: Change[]; parent?: string; children?: string[]; unapplied?: string;
}
interface Walk {
  mode: string; unit: string; asked_unit: string; units: Unit[]; next: string | null; end: boolean; fallback: boolean;
  beat?: number; beat_open?: boolean; paragraphs_before?: number; concluded?: boolean; unclaimed?: number[]; cover_asked?: boolean;
  earlier: { label: string; kind: string; state: string; text: string; stretch: number }[];
  reopen?: { label: string; finding: string; title: string; quote: string }[];
}
interface Estimate { engine: string; billed: boolean; model?: string; turns?: number; input_per_turn?: number; output_per_turn?: number; per_turn?: number | null; total?: number | null; why?: string; }
interface LintRun { at: string; counts: Record<string, number>; total: number; findings: { severity: string; code: string; message: string; quote: string; line?: number | null }[]; }
interface Finding {
  item: string; verdict: string | null; reason: string | null; n?: number; title: string; where: string; what: string; why: string; direction: string; tier: string; text: string;
  edited: string[]; original: Record<string, string>;
  continues?: { item: string; title: string; verdict: string | null; reason: string | null } | null;
}
interface EarlierLetter { revision: number; step: string; note: string; at: string; seq: number; findings: Finding[]; }
interface Letter {
  drafted: boolean; redraft: boolean; text: string; findings: Finding[]; keep: string[]; keep_letter: string[]; keep_edited: boolean;
  passes: Record<string, { needed: boolean; reason: string }>; notes: string[];
  discussion: { seq: number; action: string; author: string; reply: string; reason: string; at: string; earlier?: boolean }[];
  accepted_without_findings: boolean;
  revise: boolean; open: boolean; revision: number; earlier: EarlierLetter[]; revised_at: string; note: string; unruled: string[];
}
interface Rec { list: string; essay: string; change: string; reason: string; label: string; answer: string; }
interface Decision { id: string; kind: string; reason: string; words: string; detail: Record<string, any>; created_at: string; law_id?: string | null; }
// A principle or ruling with its style law once promoted (workflow/promote.py).
interface PromoLaw { id: string; aspect?: string; statement?: string; status?: string; guide?: string | null; file?: string | null; missing?: boolean; }
interface Promotions { decisions: (Decision & { law: PromoLaw | null })[]; aspects: string[]; guides: string[]; root: string | null; }
interface Blurb { text: string; last_used: string | null; hidden: number; }
interface View {
  instance: Instance; gate: string | null; next_step: string | null; decisions: Decision[];
  since_last: { added: string[]; removed: string[] }; order: string[]; protected: boolean;
  usage: { instance: Usage; essay: Usage; month: Usage; month_name: string; total: Usage };
  blurbs: Blurb[]; supported_tags: string[];
  letter: Letter; recommendations: { asked: boolean; items: Rec[]; text: string };
  walk?: Walk; wrapup?: { drafted: boolean; items: Unit[]; principles: { words: string; scope: string }[]; text: string };
  checklist?: { passes: { name: string; stage: string; ticked: boolean; why: string; letter?: { needed: boolean; reason: string } }[]; confirmed: boolean; audiobook: boolean; revised_here?: boolean };
  principles: Decision[]; skipped: Decision[]; promotions: Promotions;
  unplaced?: { label: string; kind: string; text: string }[]; lint?: LintRun | null;
  turns: { seq: number; step: string; unit_label: string; action: string; reason: string }[];
  landing: Landing; landed?: LandResult | null;
  job: Job | null; failed_attempts: Job[];
  summaries?: { total: number; stale: string[]; missing: string[] };
  abandoned: { reason: string; at: string; step: string; successor: string | null } | null;
  restarted_from: { id: string; reason: string } | null;
}
// A model turn running in the background (workflow/jobs.py), or the last
// one that failed; the page polls the instance while one runs.
interface Job {
  id: string; method: string; params: Record<string, unknown>; state: string; error: string;
  progress: { requests?: number; last?: string; done?: number; total?: number };
  started_at: string; finished_at: string | null; elapsed: number;
}
interface LandUnit { step: string; stretch: number; label: string; original: string; new: string; reason: string; status?: string; }
interface Landing {
  landed: number; waiting: number; left?: number; needs_owner: LandUnit[]; file_changed: boolean;
  last: { created_at: string; trigger: string; landed: number; pushed: number; note: string } | null;
}
interface LandResult { dry_run: boolean; note: string; written: boolean; pushed: boolean; units: LandUnit[]; }

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
// The steps at which the letter in force is revised, ruled and edited
// (core.LETTER_STEPS, with the owner's words).
const LETTER_STEPS = ["letter", "construction", "wrapup", "filters"];
const STATE_WORDS: Record<string, string> = {
  open: "not yet worked", pending: "waiting for you", passed: "no change needed",
  accepted: "accepted", rejected: "kept the original", dropped: "dropped", edited: "your text",
  split: "several changes", gone: "taken out by an earlier change",
};
// How construction walks the essay: the construction mode × the unit, in
// plain words (owner, 2026-11-26, at the letter step: "I didn't see any
// option to change from the default to section by section in the UI.").
// The choice lives on the server (the instance's construction_mode and
// unit), so nothing of it goes into browser storage.
const WALKS: { mode: string; unit: string; label: string }[] = [
  { mode: "apply-findings", unit: "paragraph", label: "Apply the findings, paragraph by paragraph" },
  { mode: "apply-findings", unit: "section", label: "Apply the findings, section by section" },
  { mode: "beats", unit: "section", label: "Rebuild section by section: plan each section, then write it whole" },
  { mode: "beats", unit: "paragraph", label: "Rebuild section by section: plan each section, then write it paragraph by paragraph" },
];
const MODE_WORDS: Record<string, string> = {
  "apply-findings": "Apply the findings",
  beats: "Rebuild section by section: plan each section, then write it",
};
const UNIT_WORDS: Record<string, string> = { paragraph: "paragraph by paragraph", section: "section by section" };
const walkLabel = (mode: string, unit: string) =>
  WALKS.find(w => w.mode === mode && w.unit === unit)?.label || `${MODE_WORDS[mode] || mode}, ${UNIT_WORDS[unit] || unit}`;
// Before construction a choice needs no reason of the author's (the server
// asks for one once the instance has turns, so it gets this one);
// mid-construction the author gives one.
const BEFORE_CONSTRUCTION = "chosen before construction";

function WalkChoice({ inst, ov, run, busy }: {
  inst: Instance; ov: Overview; busy: boolean;
  run: (m: string, p: Record<string, unknown>) => Promise<boolean>;
}) {
  const [pending, setPending] = useState<{ mode: string; unit: string } | null>(null);
  const [reason, setReason] = useState("");
  // A new essay has no findings to apply: it is built beat by beat only.
  const walks = WALKS.filter(w => ov.modes.includes(w.mode) && ov.units.includes(w.unit)
    && (inst.kind !== "new" || w.mode === "beats"));
  const current = `${inst.construction_mode}/${inst.unit}`;
  const mid = inst.step === "construction";
  const choose = (mode: string, unit: string, why: string) =>
    run("unit", { phase: "construction", mode, unit, reason: why });
  return (
    <div className="wf-walk">
      <label className="wf-field">How construction will walk
        <select value={pending ? `${pending.mode}/${pending.unit}` : current} aria-label="How construction will walk" disabled={busy}
          onChange={async e => {
            const [mode, unit] = e.target.value.split("/");
            if (`${mode}/${unit}` === current) { setPending(null); return; }
            if (mid) { setPending({ mode, unit }); return; }
            await choose(mode, unit, BEFORE_CONSTRUCTION);
          }}>
          {!walks.some(w => `${w.mode}/${w.unit}` === current) &&
            <option value={current}>{walkLabel(inst.construction_mode, inst.unit)}</option>}
          {walks.map(w => <option key={`${w.mode}/${w.unit}`} value={`${w.mode}/${w.unit}`}>{w.label}</option>)}
        </select>
      </label>
      {pending && <div className="devedit-actions">
        <input className="wf-input wide" placeholder="Why switch now?" value={reason}
          aria-label="Why switch how construction walks" onChange={e => setReason(e.target.value)} />
        <button className="pill on" disabled={busy || !reason.trim()} onClick={async () => {
          if (await choose(pending.mode, pending.unit, reason)) { setPending(null); setReason(""); }
        }}>Switch</button>
        <button className="pill" disabled={busy} onClick={() => { setPending(null); setReason(""); }}>Cancel</button>
      </div>}
    </div>
  );
}

const money = (n?: number) => `$${(n || 0).toFixed(2)}`;
const usageLine = (u: Usage) => !u || !u.turns ? "no model turns" :
  `${u.turns} turns · in ${(u.input || 0).toLocaleString()} (cached ${(u.cached || 0).toLocaleString()}) · out ${(u.output || 0).toLocaleString()} · ${money(u.dollars)} billed · ~${money(u.est_subscription)} subscription`;
const short = (f: string) => f.replace(/\.md$/, "");

// ------------------------------------------------------------------ landing

// Landing: the ruled units written into the essay file and pushed to its
// Doc tab (workflow/landing.py). They land when a step that rules text
// ends, or on Land; a unit whose original text is gone from the file
// waits for the author here, with both texts.
function LandingCard({ v, run, busy }: { v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean }) {
  const L = v.landing;
  if (!L || (!L.landed && !L.waiting && !L.needs_owner.length && !L.last && !v.landed)) return null;
  const preview = v.landed && v.landed.dry_run ? v.landed.units.filter(u => u.status === "land") : [];
  return <div className="card">
    <div className="card-head"><span>Landing in {short(v.instance.file)}</span>
      <span className="muted">{L.landed} landed · {L.waiting} wait for the step's end · {L.needs_owner.length} need you</span></div>
    {L.file_changed && <p className="muted">The file has changed since construction began; units are matched by their own text, so your edits stay.</p>}
    {v.landed && !v.landed.dry_run && <p>{v.landed.note}</p>}
    {!v.landed && L.last && <p className="muted">Last landing ({L.last.trigger}, {L.last.created_at.slice(0, 16).replace("T", " ")}): {L.last.note}</p>}
    <div className="devedit-actions">
      <button className="pill on" disabled={busy || !L.waiting} onClick={() => void run("land", {})}>Land now</button>
      <button className="pill" disabled={busy || !L.waiting} onClick={() => void run("land", { dry_run: true })}>Preview</button>
    </div>
    {preview.length > 0 && <>
      <p className="muted">Would land:</p>
      {preview.map(u => <div key={`${u.step}${u.stretch}${u.label}`}><b>{STEP_WORDS[u.step]} {u.label}</b><Diff before={u.original} after={u.new} /></div>)}
    </>}
    {L.needs_owner.map(u => <div key={`${u.step}${u.stretch}${u.label}`} className="card muted">
      <b>{STEP_WORDS[u.step]} {u.label} — not landed:</b> {u.reason}.
      <Diff before={u.original} after={u.new} />
      <button className="pill" disabled={busy} onClick={() => void run("leave", { label: u.label })}>Leave as the file has it</button>
    </div>)}
  </div>;
}

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

// Where an insert or a move goes in, in words.
function whereWords(a: { where?: string; quote?: string } | null | undefined): string {
  const q = (a?.quote || "").replace(/\s+/g, " ");
  const short = q.length > 70 ? `${q.slice(0, 67)}…` : q;
  return a?.where === "start" ? "at the start of the essay" : a?.where === "end" ? "at the end of the essay"
    : a?.where === "before" ? `before the paragraph opening “${short}”` : a?.where === "after" ? `after the paragraph opening “${short}”`
    : "after this paragraph";
}

// The text a replace leaves of the unit: the whole unit's new text, or the
// unit with its quoted passage replaced.
function replaced(before: string, c: Change): string {
  if (!c.original) return c.text;
  const i = before.indexOf(c.original);
  return i < 0 ? c.text : before.slice(0, i) + c.text + before.slice(i + c.original.length);
}

// One change, laid out by its kind (owner, 2026-11-27: "I can't eyeball
// what has been changed easily"): a replace as before and after, an insert
// with its place and the new text in green, a cut struck, a move in both
// places. The same diff colours as every form.
function ChangeView({ c, before }: { c: Change; before: string }) {
  const orig = c.original || before;
  if (c.kind === "replace") return <Diff before={before || orig} after={before ? replaced(before, c) : c.text} />;
  if (c.kind === "insert") return <div className="wf-change">
    <div className="muted wf-where">Inserted {whereWords(c.where ? c : null)}; nothing is removed.</div>
    <p className="devedit-diff wf-diff"><ins className="add">{c.text}</ins></p></div>;
  if (c.kind === "cut") return <div className="wf-change">
    <div className="muted wf-where">Cut: this comes out of the essay.</div>
    <p className="devedit-diff wf-diff"><del className="del">{orig}</del></p></div>;
  return <div className="wf-change">
    <div className="muted wf-where">Moved from here…</div>
    <p className="devedit-diff wf-diff"><del className="del">{orig}</del></p>
    <div className="muted wf-where">…and put in {whereWords(c.where ? c : { where: "end" })}:</div>
    <p className="devedit-diff wf-diff"><ins className="add">{c.text || orig}</ins></p></div>;
}

const KIND_WORDS: Record<string, string> = { replace: "Replace", insert: "Insert", cut: "Cut", move: "Move" };

// A form made of changes rather than the unit rewritten whole.
function isChanges(unit: Unit, changes: Change[]): boolean {
  if (unit.kind === "plan") return false;
  if (unit.beat && (unit.kind === "insert" || unit.kind === "paragraph")) return true;
  return changes.length > 0 && !(changes.length === 1 && changes[0].kind === "replace" && !changes[0].original);
}

function Changes({ unit, changes }: { unit: Unit; changes: Change[] }) {
  return <>{changes.map((c, k) => <div key={k}>
    <div className="wf-kind"><span className={`wf-kind-tag ${c.kind}`}>{KIND_WORDS[c.kind] || c.kind}</span></div>
    <ChangeView c={unit.anchor && c.kind === "insert" && !c.where ? { ...c, ...unit.anchor } : c} before={unit.before} />
  </div>)}</>;
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

// The listen controls follow the unit's kind. A text unit (a paragraph, a
// section, a filter unit, a wrap-up item) offers before, reason and after
// in their combinations, showing only those it has text for; a beat's plan
// is not essay prose and has no before, so it offers one quiet Listen that
// reads the plan (owner, 2026-11-27: "I get these weird controls in the
// beat one ... like reason and before and after ... it doesn't make sense
// when we're discussing a beat").
const TEXT_PARTS: [string, string[]][] = [
  ["Reason + after", ["reason", "after"]],
  ["Before + reason + after", ["before", "reason", "after"]],
  ["Before", ["before"]], ["Reason", ["reason"]], ["After", ["after"]],
];
const PLAN_PARTS: [string, string[]][] = [["Listen", ["after"]]];

function ListenBar({ before, reason, after, manuscript, plan = false }: { before: string; reason: string; after: string; manuscript: string; plan?: boolean }) {
  const [busy, setBusy] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const player = useRef<HTMLAudioElement>(null);
  useEffect(() => { if (url) void player.current?.play().catch(() => {}); return () => { if (url) URL.revokeObjectURL(url); }; }, [url]);
  useEffect(() => () => { try { speechSynthesis.cancel(); } catch { /* none */ } }, []);
  const words: Record<string, [string, string]> = {
    before: ["Before.", before], reason: ["The reason.", reason], after: [plan ? "The plan." : "After.", after],
  };
  // Only the parts with text to read: a part with nothing in it is not shown.
  const parts = (plan ? PLAN_PARTS : TEXT_PARTS).filter(([, which]) => which.some(w => words[w][1].trim()));
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
        <button key={label} className="pill" disabled={!!busy} onClick={() => void play(label, which)}>
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

// ------------------------------------------------------------ discussion

// Seconds since a running job began, ticking between polls.
function useElapsed(job: Job | null): number {
  const [now, setNow] = useState(() => Date.now());
  const [seen, setSeen] = useState(() => Date.now());
  useEffect(() => { setSeen(Date.now()); setNow(Date.now()); }, [job?.id, job?.elapsed]);
  useEffect(() => {
    if (job?.state !== "running") return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [job?.state, job?.id]);
  if (!job) return 0;
  return job.state === "running" ? job.elapsed + Math.max(0, (now - seen) / 1000) : job.elapsed;
}

// A conversation with the engine, under the letter or under one unit
// (owner, 2026-11-27: "I expected that when I hit send, my message would
// remain there and I would get a response from the engine"). The message
// shows at once, from the running turn on the server (`job.params.text`),
// with "thinking…" and its time, so it survives a reload; the reply takes
// its place in the thread when the turn ends. The unsent draft is kept
// until the server has taken it (see the header).
function Discussion({ items, job, mine, send, run, dismiss, busy, draftKey, placeholder, children }: {
  items: Said[]; job: Job | null; mine: (j: Job) => boolean; send: (text: string) => Promise<boolean>;
  run: (m: string, p: Record<string, unknown>) => Promise<boolean>; dismiss: () => void;
  busy: boolean; draftKey: string; placeholder: string; children?: ReactNode;
}) {
  const [draft, setDraftState] = useState(() => recall(draftKey));
  const setDraft = (d: string) => { remember(draftKey, d); setDraftState(d); };
  useEffect(() => { setDraftState(recall(draftKey)); }, [draftKey]);
  const [sending, setSending] = useState(false);
  const ours = job && mine(job) ? job : null;
  const elapsed = useElapsed(ours);
  const said = String(ours?.params?.text ?? "");
  return <div className="wf-discuss">
    {items.map(d => <div key={d.seq} className={`wf-turn${d.earlier ? " muted" : ""}`}>
      {d.earlier && <span className="muted">(about an earlier letter) </span>}
      {d.author && <p><b>You:</b> {d.author}</p>}{d.reply && <p className="wf-said"><b>Editor:</b> {d.reply}</p>}</div>)}
    {ours && <div className="wf-turn wf-pending">
      <p><b>You:</b> {said}</p>
      {ours.state === "running"
        ? <p className="muted wf-thinking"><b>Editor:</b> thinking… {clock(elapsed)}{ours.progress?.requests ? ` · ${ours.progress.requests} read request${ours.progress.requests === 1 ? "" : "s"}` : ""}</p>
        : <div><p className="bad">No reply: {ours.error}</p>
          <div className="devedit-actions">
            <button className="pill on" disabled={busy} onClick={() => void run(ours.method, ours.params)}>Send again</button>
            <button className="pill" disabled={busy} onClick={dismiss}>Dismiss</button>
          </div></div>}
    </div>}
    {children}
    <textarea className="devedit-edit wf-discuss-box" rows={rowsFor(draft || "", 2)} value={draft || ""} placeholder={placeholder}
      aria-label={placeholder} onChange={e => setDraft(e.target.value)} />
    <div className="devedit-actions">
      <button className="pill on" disabled={busy || sending || !(draft || "").trim()} onClick={async () => {
        setSending(true);
        try { if (await send((draft || "").trim())) setDraft(""); } finally { setSending(false); }
      }}>{sending ? "Sending…" : "Send"}</button>
      {busy && !sending && (draft || "").trim() && <span className="muted">A turn is running; send when it ends.</span>}
    </div>
  </div>;
}

// ------------------------------------------------------------- unit form

function UnitForm({ unit, newEssay, blurbs, tags, manuscript, busy, run, showHidden, setShowHidden, job, inst }: {
  unit: Unit; newEssay: boolean; blurbs: Blurb[]; tags: string[]; manuscript: string; busy: boolean;
  run: (method: string, params: Record<string, unknown>) => Promise<boolean>;
  showHidden: boolean; setShowHidden: (v: boolean) => void; job: Job | null; inst: string;
}) {
  const [mode, setMode] = useState<"" | "redo" | "edit" | "move">("");
  const [moveWhere, setMoveWhere] = useState("after");
  const [moveQuote, setMoveQuote] = useState("");
  const [ticked, setTicked] = useState<string[]>([]);
  const [free, setFree] = useState("");
  const [newBlurb, setNewBlurb] = useState("");
  const [text, setText] = useState("");
  const form = unit.form;
  const pending = unit.state === "pending";
  const resolved = ["accepted", "rejected", "dropped", "edited", "passed"].includes(unit.state);
  const after = pending ? (form?.proposed || "") : (unit.text ?? "");
  const noOriginal = !unit.before.trim();
  // Footnote markers ([^RT3]) and links ([text](url)) are essay text,
  // not notes to the engine (matches core._bracketed).
  const bracket = /\[[^\]]*\]/.test(
    text.replace(/\[\^[^\]\s]+\]:?|\[[^\]]*\]\([^)]*\)/g, ""));
  const visible = blurbs.filter(b => showHidden || !b.hidden);
  // The discussion since the form the author sees: what "Redo with this
  // discussion" hands the engine (workflow_page.act, with_discussion).
  const thread = unit.thread || [];
  const fresh = thread.filter(d => d.seq > (form?.seq || 0));
  const canRedo = ["pending", "passed", "open"].includes(unit.state);
  const [withTalk, setWithTalk] = useState(true);
  const title = unit.kind === "plan" ? `Beat ${unit.beat}: plan`
    : unit.kind === "leftover" ? `Beat ${unit.beat}: left over, old paragraph ${unit.n}`
    : unit.kind === "cover" ? `Left standing: old paragraph ${unit.n}`
    : unit.kind === "change" ? `${unit.parent}: change ${unit.n}`
    : unit.beat ? `Beat ${unit.beat}: ${unit.kind === "paragraph" ? `new paragraph ${unit.n}` : "new text"}`
    : unit.label.startsWith("w:") ? `${unit.kind} ${unit.label.split(":")[2]}` : `${unit.kind} ${unit.n ?? unit.label}`;
  const changes = (pending ? form?.changes : unit.applied) || [];
  const byChanges = !unit.label.startsWith("w:") && isChanges(unit, changes);
  const firstKind = (form?.changes || [])[0]?.kind;
  // The author's own changes on a unit with an original (not a wrap-up item).
  const canCut = !!unit.before && !unit.label.startsWith("w:") && unit.kind !== "change" && ["pending", "open", "passed"].includes(unit.state);
  const kept = unit.kind === "leftover" || unit.kind === "cover";
  if (unit.state === "split") return <div className="card wf-unit split" id={`unit-${unit.label}`}>
    <div className="card-head"><span>{title}</span><span className="state">{STATE_WORDS.split}</span></div>
    <p className="muted">{(unit.children || []).length} changes proposed here, each its own form below{form?.reason ? `: ${form.reason}` : "."}</p>
    <div className="devedit-actions"><button className="pill" disabled={busy} onClick={() => void run("reopen", { label: unit.label })}>Propose this paragraph again</button></div>
  </div>;
  return (
    <div className={`card wf-unit ${unit.state}`} id={`unit-${unit.label}`}>
      <div className="card-head"><span>{title}</span><span className={`state${resolved ? " done" : ""}`}>{STATE_WORDS[unit.state] || unit.state}</span>
        {form?.engine && <span className="muted">{form.engine}{form.model ? ` (${form.model})` : ""}</span>}</div>
      {pending && form && <>
        {/* The reason sits above the new text (Q16). */}
        <div className="wf-reason"><b>Reason:</b> {form.reason || <i className="muted">(none given)</i>}</div>
        {form.text && <div className="muted wf-said">{form.text}</div>}
        {byChanges ? <Changes unit={unit} changes={form.changes || []} />
          : noOriginal ? <p className="devedit-diff"><ins className="add">{form.proposed}</ins></p>
          : <Diff before={unit.before} after={form.proposed} />}
        <ListenBar before={unit.before} reason={form.reason} after={form.proposed} manuscript={manuscript} plan={unit.kind === "plan"} />
      </>}
      {!pending && <>
        {unit.state === "open" && kept && <div className="muted wf-where">No beat's plan claimed this paragraph: keep it, cut it or move it.</div>}
        {(unit.state === "accepted" || unit.state === "edited") && byChanges ? <Changes unit={unit} changes={changes} />
          : resolved && unit.state !== "passed" && unit.before && unit.text !== unit.before ? <Diff before={unit.before} after={unit.text || ""} />
          : <p className="devedit-diff">{unit.text || unit.before || <i className="muted">(empty)</i>}</p>}
        {unit.unapplied && <div className="bad">Not applied: {unit.unapplied}</div>}
        {unit.state === "passed" && form?.text && <div className="muted">{form.text}</div>}
      </>}
      {unit.kind === "plan" && form?.placement && <div className="wf-placement muted">
        Its new text goes in {whereWords(form.placement)}; the rest of the essay stays.
        {form.placement.supersedes.length > 0 ? <> It takes the place of: {form.placement.supersedes.map(q => `“${q}”`).join("; ")} (each comes to you once the text is in).</> : " It supersedes no old paragraph."}
      </div>}
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
        {fresh.length > 0 && <label className="muted"><input type="checkbox" checked={withTalk} onChange={e => setWithTalk(e.target.checked)} /> with the discussion below ({fresh.length} exchange{fresh.length === 1 ? "" : "s"})</label>}
        <div className="devedit-actions">
          <button className="pill on" disabled={busy || (!ticked.length && !free.trim() && !(withTalk && fresh.length))} onClick={async () => {
            if (await run("act", { label: unit.label, action: "redo", blurbs: ticked, free, with_discussion: withTalk && fresh.length > 0 })) { setMode(""); setTicked([]); setFree(""); }
          }}>{busy ? "Asking…" : "Redo"}</button>
          <button className="pill" onClick={() => setMode("")}>Cancel</button>
        </div>
      </div>}
      {mode === "move" && <div className="wf-edit">
        <div className="devedit-actions">
          <select className="wf-input" value={moveWhere} aria-label="Where to move it" onChange={e => setMoveWhere(e.target.value)}>
            <option value="after">after the paragraph opening…</option><option value="before">before the paragraph opening…</option>
            <option value="start">to the start of the essay</option><option value="end">to the end of the essay</option>
          </select>
          {(moveWhere === "after" || moveWhere === "before") && <input className="wf-input" value={moveQuote} placeholder="its opening words, exactly"
            aria-label="Opening words of the paragraph" onChange={e => setMoveQuote(e.target.value)} />}
        </div>
        <div className="devedit-actions">
          <button className="pill on" disabled={busy || ((moveWhere === "after" || moveWhere === "before") && !moveQuote.trim())} onClick={async () => {
            if (await run("act", { label: unit.label, action: "move", where: moveWhere, quote: moveQuote })) setMode("");
          }}>Move it</button>
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
          <button className="pill on" disabled={busy} onClick={() => void run("act", { label: unit.label, action: "accept" })}>
            {kept && firstKind === "cut" ? "Cut it" : "Accept"}</button>
          <button className="pill" disabled={busy} onClick={() => void run("act", { label: unit.label, action: "reject" })}
            title={noOriginal || newEssay ? "Drop the proposed text" : "Keep the original"}>{kept ? "Keep it" : firstKind === "insert" ? "Reject (insert nothing)" : noOriginal ? "Reject (drop)" : "Reject (keep original)"}</button>
          <button className="pill" disabled={busy} onClick={() => setMode("redo")}>{kept ? "Merge or redo…" : "Redo…"}</button>
          <button className="pill" disabled={busy} onClick={() => { setText(form?.proposed || unit.before); setMode("edit"); }}>Edit…</button>
        </>}
        {unit.state === "open" && kept && <>
          <button className="pill on" disabled={busy} onClick={() => void run("act", { label: unit.label, action: "keep" })}>Keep it</button>
          <button className="pill" disabled={busy} onClick={() => void run("propose", { label: unit.label })}>Ask the engine</button>
        </>}
        {canCut && !(pending && kept && firstKind === "cut") && <button className="pill" disabled={busy} onClick={() => void run("act", { label: unit.label, action: "cut" })}
          title="Take this paragraph out yourself">Cut it</button>}
        {canCut && <button className="pill" disabled={busy} onClick={() => setMode("move")} title="Move this paragraph yourself">Move…</button>}
        {resolved && unit.state !== "passed" && <button className="pill" disabled={busy} onClick={() => void run("reopen", { label: unit.label })}>Reopen</button>}
      </div>}
      {/* Talk with the engine about this unit (owner, 2026-11-27: "there is
          no way to interact with the engine as I did here in the chat"):
          the proposal stays as it is until Redo with this discussion. */}
      <Discussion items={thread} job={job} busy={busy} run={run}
        mine={j => j.method === "discuss" && j.params?.label === unit.label}
        send={text => run("discuss", { label: unit.label, text })}
        dismiss={() => job && void run("job_dismiss", { job: job.id })}
        draftKey={`authorlm.wf.discuss.${manuscript}.${inst}.${unit.label}`}
        placeholder={unit.kind === "plan" ? "Discuss this plan with the engine…" : "Discuss this with the engine…"}>
        {!mode && canRedo && fresh.length > 0 && <div className="devedit-actions">
          <button className="pill" disabled={busy} onClick={() => void run("act", { label: unit.label, action: "redo", blurbs: [], free: "", with_discussion: true })}>
            Redo with this discussion</button>
          <span className="muted">The proposal changes only when you redo.</span>
        </div>}
      </Discussion>
    </div>
  );
}

// ---------------------------------------------------------------- steps

// The author's brief, on every step (owner, 2026-11-26, at the start step:
// "if I have read the recap for sermon 3 and I've read sermon 3 and I have
// some thoughts about what we should take into account, should I be able
// to edit the brief and add things there?"). Edit opens a roomy textarea;
// Save goes to workflow_page `brief` (core.set_brief). Saving is allowed
// while a model turn runs: that turn keeps the brief it started with.
// Promote a principle or ruling to a style law, one at a time (never in
// bulk): the wording is editable, the aspect and guide chosen. The open
// form's draft (wording, aspect, guide) is a personal, unsent edit, so it
// lives in browser storage per manuscript and decision and survives a
// reload; which decision's form is open is part of that draft.
function PromoteCard({ v, run, asking, manuscript }: {
  v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; asking: boolean; manuscript: string;
}) {
  const pr = v.promotions;
  const openKey = `authorlm.wf.promoteOpen.${manuscript}.${v.instance.id}`;
  const draftKey = (id: string) => `authorlm.wf.promoteDraft.${manuscript}.${id}`;
  const [openId, setOpenState] = useState<string>(() => recall(openKey) || "");
  const [draft, setDraftState] = useState<{ statement: string; aspect: string; guide: string } | null>(null);
  useEffect(() => { setOpenState(recall(openKey) || ""); }, [openKey]);
  useEffect(() => {
    if (!openId) { setDraftState(null); return; }
    const d = pr?.decisions.find(x => x.id === openId);
    if (!d || d.law) { setDraftState(null); return; }
    let saved: { statement: string; aspect: string; guide: string } | null = null;
    try { saved = JSON.parse(recall(draftKey(openId)) || "null"); } catch { saved = null; }
    setDraftState(saved || { statement: d.words, aspect: "", guide: pr.root || "" });
  }, [openId, pr]);
  if (!pr || pr.decisions.length === 0) return null;
  const setOpen = (id: string) => { remember(openKey, id); setOpenState(id); };
  const setDraft = (d: { statement: string; aspect: string; guide: string }) => { remember(draftKey(openId), JSON.stringify(d)); setDraftState(d); };
  const close = () => { remember(draftKey(openId), ""); setOpen(""); };
  return (
    <div className="card wf-promote">
      <div className="card-head"><span>Principles and rulings</span></div>
      <p className="muted">Promote the ones you want as style laws, one at a time; the law's notes quote the decision and this instance.</p>
      {pr.decisions.map(d => <div key={d.id} className="wf-promote-row">
        <div><b>{d.kind}</b>: {d.words} <span className="muted">· {new Date(d.created_at).toLocaleDateString([], { dateStyle: "medium" })}{d.reason ? ` · ${d.reason}` : ""}</span></div>
        {d.law ? <div className="muted">{d.law.missing
          ? <>Promoted to law {d.law.id}, which no longer exists (authorlm doctor reports it).</>
          : <>Promoted: <a href={`#${encodeURIComponent(manuscript)}/style:${d.law.guide ? `guide:${encodeURIComponent(d.law.guide)}` : "all"}`}>{d.law.aspect} law {d.law.id}</a>
            {d.law.guide ? ` in ${d.law.guide}` : ""}{d.law.status !== "active" ? ` (${d.law.status})` : ""}: {d.law.statement}</>}</div>
          : openId !== d.id ? <div className="devedit-actions"><button className="pill" onClick={() => setOpen(d.id)}>Promote…</button></div>
          : draft && <>
            <textarea className="devedit-edit" aria-label="The law's wording" rows={Math.max(3, Math.ceil(draft.statement.length / 70))}
              value={draft.statement} onChange={e => setDraft({ ...draft, statement: e.target.value })} />
            <div className="devedit-actions">
              <select aria-label="Aspect" value={draft.aspect} onChange={e => setDraft({ ...draft, aspect: e.target.value })}>
                <option value="">aspect…</option>{pr.aspects.map(a => <option key={a} value={a}>{a}</option>)}</select>
              <select aria-label="Style guide" value={draft.guide} onChange={e => setDraft({ ...draft, guide: e.target.value })}>
                {pr.guides.map(g => <option key={g} value={g}>{g}{g === pr.root ? " (root)" : ""}</option>)}</select>
              <button className="pill on" disabled={asking || !draft.aspect || !draft.statement.trim() || !draft.guide} onClick={async () => {
                if (await run("promote", { decision: d.id, statement: draft.statement, aspect: draft.aspect, guide: draft.guide })) close();
              }}>{asking ? "Promoting…" : "Promote to law"}</button>
              <button className="pill" disabled={asking} onClick={close}>Cancel</button>
            </div></>}
      </div>)}
    </div>
  );
}

function BriefCard({ v, run, asking, manuscript }: {
  v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; asking: boolean; manuscript: string;
}) {
  const inst = v.instance;
  const key = `authorlm.wf.briefDraft.${manuscript}.${inst.id}`;
  const [draft, setDraftState] = useState<string | null>(() => { const d = recall(key); return d ? d : null; });
  const setDraft = (d: string | null) => { remember(key, d ?? ""); setDraftState(d); };
  useEffect(() => { const d = recall(key); setDraftState(d ? d : null); }, [key]);
  const open = inst.step !== "done";
  const turnRunning = v.job?.state === "running";
  const changes = v.decisions.filter(d => d.kind === "brief-change");
  const last = changes[changes.length - 1];
  if (!open && !inst.brief) return null;
  const editing = open && draft !== null;
  const unchanged = editing && draft.trim() === (inst.brief || "").trim();
  return (
    <div className="card wf-brief">
      <div className="card-head"><span>The brief</span>
        {open && !editing && <button className="pill" onClick={() => setDraft(inst.brief || "")}>Edit</button>}</div>
      {!editing && (inst.brief ? <p className="wf-brief-text">{inst.brief}</p>
        : <p className="muted">No brief yet: say what to build or improve, and what to take into account.</p>)}
      {!editing && last && <div className="muted">Edited {changes.length > 1 ? `${changes.length} times; last ` : ""}at the {STEP_WORDS[last.detail.step] || last.detail.step} step, {new Date(last.created_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}. Earlier turns kept the brief they had.</div>}
      {editing && <>
        <textarea className="devedit-edit wf-brief-edit" autoFocus value={draft} aria-label="The brief"
          rows={Math.min(24, Math.max(8, draft.split("\n").length + Math.ceil(draft.length / 60)))}
          placeholder="What to build or improve, and what to take into account (several paragraphs are fine)"
          // The caret opens at the end: the usual edit is adding thoughts.
          onFocus={e => { const n = e.currentTarget.value.length; e.currentTarget.setSelectionRange(n, n); }}
          onChange={e => setDraft(e.target.value)} />
        <div className="muted">{turnRunning
          ? "A turn is running: it uses the brief it started with; the next turn reads the one you save."
          : "The next model turn reads the brief you save; earlier turns keep the one they had."}</div>
        <div className="devedit-actions">
          <button className="pill on" disabled={asking || unchanged} onClick={async () => {
            if (await run("brief", { brief: draft })) setDraft(null);
          }}>{asking ? "Saving…" : "Save"}</button>
          <button className="pill" disabled={asking} onClick={() => setDraft(null)}>Cancel</button>
        </div>
      </>}
    </div>
  );
}

function StartStep({ v, run, busy }: { v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean }) {
  const inst = v.instance;
  const [add, setAdd] = useState<Record<string, string>>({});
  const recs = v.recommendations;
  return <>
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
      {inst.step === "start" && v.summaries && (v.summaries.stale.length + v.summaries.missing.length) > 0 && <div className="devedit-actions">
        <span className="muted" title={[...v.summaries.stale, ...v.summaries.missing].join(", ")}>
          {v.summaries.stale.length} of {v.summaries.total} essay summaries are stale{v.summaries.missing.length ? `, ${v.summaries.missing.length} missing` : ""}: Recommend reads them as they stand, marked stale.</span>
        <button className="pill" disabled={busy} title="Rebuilds each stale or missing summary, billed like authorlm summarize rebuild (about two cents an essay)"
          onClick={() => void run("summaries", {})}>Bring summaries up to date</button>
      </div>}
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

// A draft kept in localStorage as JSON (see the header: unsaved letter
// edits survive a reload); null when no edit is open.
function useJsonDraft<T>(key: string): [T | null, (d: T | null) => void] {
  const read = () => { try { const s = recall(key); return s ? JSON.parse(s) as T : null; } catch { return null; } };
  const [draft, setState] = useState<T | null>(read);
  useEffect(() => { setState(read()); }, [key]);  // eslint-disable-line react-hooks/exhaustive-deps
  const set = (d: T | null) => { remember(key, d === null ? "" : JSON.stringify(d)); setState(d); };
  return [draft, set];
}

const rowsFor = (s: string, min = 2) => Math.min(14, Math.max(min, s.split("\n").length + Math.ceil(s.length / 48)));

// The letter's Keep list, editable while the instance is at the letter step
// (owner, 2026-11-26: "the keep section in the letter output cannot be
// edited ... so that I can change it before we move to the next stage").
// Save goes to workflow_page `keep` (core.set_keep): the whole list; the
// letter's own list stays in its text and on the record.
function KeepCard({ L, here, run, busy, asking, manuscript, inst }: {
  L: Letter; here: boolean; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean; asking: boolean; manuscript: string; inst: string;
}) {
  const [draft, setDraft] = useJsonDraft<string[]>(`authorlm.wf.keepDraft.${manuscript}.${inst}`);
  const editing = here && draft !== null;
  if (!editing && !L.keep.length && !here) return null;
  const clean = (draft || []).map(k => k.trim()).filter(Boolean);
  const unchanged = editing && JSON.stringify(clean) === JSON.stringify(L.keep);
  const put = (i: number, s: string) => setDraft((draft || []).map((k, j) => j === i ? s : k));
  return (
    <div className="card wf-keep">
      <div className="card-head"><span>Keep</span>
        {L.keep_edited && <span className="wf-edited">edited</span>}
        {here && !editing && <button className="pill" onClick={() => setDraft(L.keep.length ? [...L.keep] : [""])}>Edit</button>}</div>
      {!editing && (L.keep.length ? <ul>{L.keep.map((k, i) => <li key={i}>{k}</li>)}</ul>
        : <p className="muted">Nothing named to keep.</p>)}
      {!editing && L.keep_edited && <details><summary className="muted">The letter's list</summary>
        <ul className="muted">{L.keep_letter.map((k, i) => <li key={i}>{k}</li>)}</ul></details>}
      {editing && <>
        {draft!.map((k, i) => <div key={i} className="wf-keep-item">
          <textarea className="devedit-edit" rows={rowsFor(k)} value={k} aria-label={`Keep item ${i + 1}`}
            placeholder="What to keep" onChange={e => put(i, e.target.value)} />
          <button className="wf-x" aria-label={`Remove keep item ${i + 1}`} onClick={() => setDraft(draft!.filter((_, j) => j !== i))}>×</button>
        </div>)}
        <button className="pill" onClick={() => setDraft([...draft!, ""])}>+ Add an item</button>
        <div className="muted">Construction and every later turn keep the list you save; the letter's own stays on the record.</div>
        <div className="devedit-actions">
          <button className="pill on" disabled={busy || unchanged} onClick={async () => {
            if (await run("keep", { items: clean })) setDraft(null);
          }}>{asking ? "Saving…" : "Save"}</button>
          <button className="pill" disabled={asking} onClick={() => setDraft(null)}>Cancel</button>
        </div>
      </>}
    </div>
  );
}

const FIELDS = [["title", "Title"], ["where", "Where"], ["what", "What is wrong"], ["why", "Why it matters"], ["direction", "Direction"]] as const;
type FindingDraft = Record<string, string>;

// One finding of the letter, with its verdict buttons and an Edit control
// for small tweaks (owner, 2026-11-26: "if I can edit the recommendation as
// well, that would be ideal. So I can tweak it in minor ways"). Editable
// before and after the verdict while the letter step is open; Save goes to
// workflow_page `finding_edit` (core.edit_finding) with the changed fields.
const VERDICT_WORDS: Record<string, string> = { accept: "accepted", reject: "rejected", defer: "deferred" };
const VERDICT_LABEL: Record<string, string> = { accept: "Accepted", reject: "Rejected", defer: "Deferred" };

function FindingCard({ f, here, run, busy, asking, manuscript, inst, reason, setReason, revised = false }: {
  f: Finding; here: boolean; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean; asking: boolean;
  manuscript: string; inst: string; reason: string; setReason: (s: string) => void; revised?: boolean;
}) {
  const [draft, setDraft] = useJsonDraft<FindingDraft>(`authorlm.wf.findingDraft.${manuscript}.${inst}.${f.item}`);
  const editing = here && draft !== null;
  const [already, setAlready] = useState(false);
  const now: FindingDraft ={ title: f.title, where: f.where, what: f.what, why: f.why, direction: f.direction, tier: f.tier };
  const changed = editing ? Object.fromEntries(Object.entries(draft!).filter(([k, s]) => s.trim() !== (now[k] || ""))) : {};
  return (
    <div className={`card wf-finding${f.verdict ? " ruled" : ""}`}>
      <div className="card-head"><span>{f.item}</span>{f.tier && <span className={`wf-tier ${f.tier}`}>{f.tier}</span>}
        {f.edited.length > 0 && <span className="wf-edited" title={`You changed: ${f.edited.join(", ")}`}>edited</span>}
        {f.verdict && <span className="state done">{f.verdict}{f.reason ? `: ${f.reason}` : ""}</span>}
        {here && !editing && <button className="pill wf-edit" onClick={() => setDraft(now)}>Edit</button>}</div>
      {!editing && <>
        {f.continues && <p className="muted wf-continues">Continues {f.continues.item} of the earlier letter ({f.continues.title}): {f.continues.verdict
          ? <>you {VERDICT_WORDS[f.continues.verdict] || f.continues.verdict} it{f.continues.reason ? ` — ${f.continues.reason}` : ""}.</> : "not ruled there."}</p>}
        {revised && !f.continues && <p className="muted wf-continues">New in this letter.</p>}
        <p><b>{f.title}</b></p>
        {/* The verdict, unmistakable (owner, 2026-11-26: he pressed Accept on a
            finding already accepted and thought it was "not taking my accept"). */}
        <p className={`wf-verdict ${f.verdict || "open"}`}>{f.verdict
          ? <>{VERDICT_LABEL[f.verdict] || f.verdict} ✓{f.reason ? ` — ${f.reason}` : ""}</> : "Not yet decided"}</p>
        {f.where && <p><span className="muted">Where:</span> {f.where}</p>}
        {f.what && <p><span className="muted">What is wrong:</span> {f.what}</p>}
        {f.why && <p><span className="muted">Why it matters:</span> {f.why}</p>}
        {f.direction && <p><span className="muted">Direction:</span> {f.direction}</p>}
        {!f.what && !f.direction && f.text && <pre className="wf-pre">{f.text}</pre>}
        {f.edited.length > 0 && <details><summary className="muted">The letter's words</summary>
          {f.edited.map(k => <p key={k} className="muted"><span>{(FIELDS.find(x => x[0] === k) || [k, "Tier"])[1]}:</span> {f.original[k] || "—"}</p>)}</details>}
      </>}
      {editing && <div className="wf-finding-edit">
        {FIELDS.map(([k, label]) => <label key={k}><span className="muted">{label}</span>
          <textarea className="devedit-edit" rows={rowsFor(draft![k] || "", k === "title" ? 1 : 2)} value={draft![k] || ""}
            onChange={e => setDraft({ ...draft!, [k]: e.target.value })} /></label>)}
        <label><span className="muted">Tier</span>
          <select value={draft!.tier || ""} onChange={e => setDraft({ ...draft!, tier: e.target.value })}>
            {!draft!.tier && <option value="">(none)</option>}
            {["must", "should", "could"].map(t => <option key={t} value={t}>{t}</option>)}
          </select></label>
        <div className="muted">Construction and every later turn work from your version; the letter's words stay on the record.</div>
        <div className="devedit-actions">
          <button className="pill on" disabled={busy || !Object.keys(changed).length || !(draft!.title || "").trim()} onClick={async () => {
            if (await run("finding_edit", { item: f.item, fields: changed })) setDraft(null);
          }}>{asking ? "Saving…" : "Save"}</button>
          <button className="pill" disabled={asking} onClick={() => setDraft(null)}>Cancel</button>
        </div>
      </div>}
      {here && !editing && <div className="devedit-actions">
        {/* Re-ruling a continued finding is one tap: the earlier verdict and reason. */}
        {!f.verdict && f.continues?.verdict && <button className="pill on wf-again" disabled={busy}
          onClick={() => void run("finding", { item: f.item, verdict: f.continues!.verdict, reason: f.continues!.reason || "" })}>
          {f.continues.verdict[0].toUpperCase() + f.continues.verdict.slice(1)} again</button>}
        <input className="wf-input" placeholder="reason (optional)" value={reason} onChange={e => setReason(e.target.value)} />
        {/* The chosen verdict reads "Accepted ✓" (filled); pressing it again
            sends nothing and says it is already chosen. */}
        {(["accept", "reject", "defer"] as const).map(verdict => {
          const chosen = f.verdict === verdict;
          return <button key={verdict} className={`pill${chosen ? " on wf-chosen" : ""}`} disabled={busy} aria-pressed={chosen}
            onClick={() => { if (chosen) { setAlready(true); return; } setAlready(false); void run("finding", { item: f.item, verdict, reason }); }}>
            {chosen ? `${VERDICT_LABEL[verdict]} ✓` : verdict[0].toUpperCase() + verdict.slice(1)}</button>;
        })}
        {already && f.verdict && <span className="muted wf-already">Already {VERDICT_WORDS[f.verdict]}.</span>}
      </div>}
    </div>
  );
}

// "Revise the letter" with the author's optional note on what to look at
// differently (owner, 2026-11-26: "Even if I accept the findings, it need
// not necessarily cause me to not be able to redraft"). From the letter
// step to the filters; it runs as a background turn (`revise_letter`, a
// job like the others). The unsent note survives a reload (header).
function RevisePanel({ v, run, busy, manuscript }: {
  v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean; manuscript: string;
}) {
  const key = `authorlm.wf.reviseNote.${manuscript}.${v.instance.id}`;
  const [note, setNoteState] = useState<string | null>(() => { const d = recall(key); return d ? d : null; });
  const setNote = (d: string | null) => { remember(key, d ?? ""); setNoteState(d); };
  useEffect(() => { const d = recall(key); setNoteState(d ? d : null); }, [key]);
  const step = v.instance.step;
  if (!v.letter.revise) return null;
  const after = step === "letter" ? "" : step === "filters"
    ? " Its passes re-tick the filter list; units already filtered keep theirs."
    : " Units you accepted stay accepted; the walk goes on from where it is.";
  if (note === null) return <div className="devedit-actions wf-revise">
    <button className="pill" disabled={busy} onClick={() => setNote(" ")}>Revise the letter…</button>
  </div>;
  return <div className="wf-revise">
    <textarea className="devedit-edit" rows={3} value={note.trimStart()} autoFocus aria-label="What to look at differently"
      placeholder="Optional: what to look at differently this time" onChange={e => setNote(e.target.value || " ")} />
    <div className="muted">A new letter on the essay as it now stands replaces this one, which stays on the record.{after}</div>
    <div className="devedit-actions">
      <button className="pill on" disabled={busy} onClick={async () => {
        if (await run("revise_letter", { note: note.trim() })) setNote(null);
      }}>{busy ? "Revising…" : "Revise the letter"}</button>
      <button className="pill" disabled={busy} onClick={() => setNote(null)}>Cancel</button>
    </div>
  </div>;
}

function LetterStep({ v, run, busy, asking, manuscript }: {
  v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean; asking: boolean; manuscript: string;
}) {
  const L = v.letter;
  const [reasons, setReasons] = useState<Record<string, string>>({});
  // The letter in force is ruled, edited and discussed from the letter step
  // to the filters (LETTER_STEPS); it is first written at the letter step.
  const here = LETTER_STEPS.includes(v.instance.step);
  const drafting = v.instance.step === "letter";
  return <>
    {!L.drafted && <div className="card">
      <p className="muted">One reading of the whole essay: numbered findings with a tier each, what to keep, which passes the essay needs, and short paragraph notes.</p>
      {drafting && <div className="devedit-actions">
        <button className="pill on" disabled={busy} onClick={() => void run("letter", {})}>{busy ? "Writing…" : "Write the letter"}</button>
        {v.instance.kind === "new" && <button className="pill" disabled={busy} onClick={() => void run("accept_letter", {})}
          title="A new essay's letter is its beat plan: accept without findings">Accept with no findings</button>}
      </div>}
    </div>}
    {L.drafted && <div className="card">
      {/* When every finding is ruled, say so and point at the next step's
          button (the gate bar at the top of the page). */}
      {L.findings.length > 0 && !L.unruled.length && <p className="wf-all-decided">{v.instance.step === "letter"
        ? <>All {L.findings.length} findings decided — next: <b>On to Construction</b>, at the top of the page.</>
        : <>All {L.findings.length} findings decided — the walk goes on.</>}</p>}
      {L.revision > 0 && <p className="muted">Revision {L.revision} of the letter, asked at the {STEP_WORDS[L.revised_at] || L.revised_at} step{L.note ? `: “${L.note}”` : ""}. The earlier letter is kept below.</p>}
      {L.unruled.length > 0 && v.instance.step !== "letter" && <p>{L.unruled.length} finding(s) wait for your verdict before the walk goes on.</p>}
      {drafting && !L.findings.length && <div className="devedit-actions"><span className="muted">The letter as written has no findings.</span>
        <button className="pill on" disabled={busy} onClick={() => void run("accept_letter", {})}>Accept with no findings</button></div>}
      <RevisePanel v={v} run={run} busy={busy} manuscript={manuscript} />
    </div>}
    {L.findings.map(f => <FindingCard key={f.item} f={f} here={here} run={run} busy={busy} asking={asking}
      manuscript={manuscript} inst={v.instance.id} reason={reasons[f.item] ?? ""} revised={L.revision > 0}
      setReason={s => setReasons(r => ({ ...r, [f.item]: s }))} />)}
    {L.drafted && <KeepCard L={L} here={here} run={run} busy={busy} asking={asking} manuscript={manuscript} inst={v.instance.id} />}
    {L.drafted && <div className="card">
      {Object.keys(L.passes).length > 0 && <><div className="card-head"><span>Passes</span></div>
        <ul>{Object.entries(L.passes).map(([name, p]) => <li key={name}><b>{name}</b>: {p.needed ? "needed" : "not needed"}{p.reason ? ` — ${p.reason}` : ""}</li>)}</ul></>}
      {L.notes.length > 0 && <><div className="card-head"><span>Paragraph notes</span></div>
        <p className="muted">Not ruled one by one; construction picks each up at its paragraph.</p>
        <ul>{L.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></>}
      <details><summary className="muted">The letter as written</summary><pre className="wf-pre">{L.text}</pre></details>
    </div>}
    {L.earlier.length > 0 && <details className="card wf-earlier">
      <summary className="muted">{L.earlier.length} earlier letter(s), superseded: kept on the record, no longer in force</summary>
      {[...L.earlier].reverse().map(e => <div key={e.revision} className="wf-turn">
        <p className="muted">Replaced at the {STEP_WORDS[e.step] || e.step} step{e.note ? `: “${e.note}”` : ""}</p>
        <ul>{e.findings.map(f => <li key={f.item}><b>{f.item}</b>{f.tier ? ` (${f.tier})` : ""}: {f.title} — {f.verdict ? VERDICT_WORDS[f.verdict] || f.verdict : "not ruled"}{f.reason ? `: ${f.reason}` : ""}</li>)}</ul>
      </div>)}
    </details>}
    {(L.drafted || L.discussion.length > 0) && <div className="card">
      <div className="card-head"><span>Discussion</span></div>
      {here ? <Discussion items={L.discussion} job={v.job} busy={busy} run={run}
        mine={j => j.method === "letter_reply"}
        send={text => run("letter_reply", { text })}
        dismiss={() => v.job && void run("job_dismiss", { job: v.job.id })}
        draftKey={`authorlm.wf.discuss.${manuscript}.${v.instance.id}.letter`}
        placeholder="Debate a finding, ask a question…" />
        : L.discussion.map(d => <div key={d.seq} className="wf-turn">
          {d.author && <p><b>You:</b> {d.author}</p>}{d.reply && <p className="wf-said"><b>Editor:</b> {d.reply}</p>}</div>)}
    </div>}
  </>;
}

// The letter in force, above the walk at construction, the wrap-up and the
// filters: its findings waiting for a verdict (the walk waits for them), a
// way to the Letter step to rule them, and Revise the letter.
function LetterInForce({ v, run, busy, manuscript, toLetter }: {
  v: View; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; busy: boolean; manuscript: string; toLetter: () => void;
}) {
  const L = v.letter;
  if (!L.drafted) return null;
  const ruled = L.findings.filter(f => f.verdict).length;
  return <div className="card wf-in-force">
    <div className="card-head"><span>The letter in force</span>
      <span className="muted">{L.revision ? `revision ${L.revision} · ` : ""}{L.findings.length} finding(s), {ruled} ruled</span></div>
    {L.unruled.length > 0 && <div className="devedit-actions">
      <span>{L.unruled.length} new finding(s) wait for your verdict; the walk goes on once they are ruled.</span>
      <button className="pill on" onClick={toLetter}>Rule them at the Letter step</button>
    </div>}
    <RevisePanel v={v} run={run} busy={busy} manuscript={manuscript} />
  </div>;
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
      {w.mode === "beats" ? `Beat by beat: each beat's plan, then its new text ${inst.unit === "section" ? "whole" : "paragraph by paragraph"}, inserted; the old text stays until you rule what each beat supersedes.`
        : `${inst.step === "filters" ? "The filters" : "The accepted findings"}, ${w.unit} by ${w.unit}.`}
      {w.fallback && ` The essay has no subheadings, so it goes by paragraph.`}
      {inst.step === "construction" && v.letter.notes.length > 0 && ` ${v.letter.notes.length} paragraph note(s) from the letter.`}
    </div>
    {w.earlier.length > 0 && <details className="card">
      <summary className="muted">{w.earlier.length} unit(s) ruled before the last switch of unit or mode: their text is carried into this walk</summary>
      <ul>{w.earlier.map(e => <li key={`${e.stretch}-${e.label}`}><b>{e.label}</b> ({STATE_WORDS[e.state] || e.state}): {e.text || <i className="muted">(empty)</i>}</li>)}</ul>
    </details>}
    {(w.reopen || []).length > 0 && <div className="card wf-reopen">
      <div className="card-head"><span>The revised letter touches units you accepted</span></div>
      <p className="muted">Reopen one to work it again under the new letter; the rest of the walk stays as it is.</p>
      {w.reopen!.map(r => <div key={r.label} className="wf-rec">
        <div><b>{r.finding}</b> ({r.title}) quotes <b>{r.label}</b>: “{r.quote}”</div>
        <div className="devedit-actions">
          <button className="pill on" disabled={busy} onClick={() => void run("reopen", { label: r.label })}>Reopen {r.label}</button>
          <button className="pill" disabled={busy} onClick={() => void run("reopen_decline", { label: r.label })}>Leave {r.label} as accepted</button>
        </div>
      </div>)}
    </div>}
    {inst.step === "filters" && (v.unplaced || []).length > 0 && <div className="card muted">
      Accepted at the wrap-up but not found in the essay, so not placed: {(v.unplaced || []).map(u => `${u.label} (“${u.text}”)`).join("; ")}.</div>}
    {units.map(u => <UnitForm key={u.label} unit={u} newEssay={newEssay} blurbs={v.blurbs} tags={v.supported_tags}
      manuscript={manuscript} busy={busy} run={run} showHidden={showHidden} setShowHidden={setShowHidden} job={v.job} inst={inst.id} />)}
    {(w.end || (w.next && !units.some(u => u.state === "pending")) || (w.mode === "beats" && !w.concluded)) && <div className="card devedit-actions">
      {w.next && !units.some(u => u.state === "pending") &&
        <button className="pill on" disabled={busy} onClick={() => void run("propose", {})}>{busy ? "Asking…" : `Work ${w.next}`}</button>}
      {w.mode === "beats" && w.beat_open && !units.some(u => u.state === "pending") &&
        <button className="pill" disabled={busy} onClick={() => void run("beat_done", {})}>Beat {w.beat} done — next beat</button>}
      {w.mode === "beats" && !w.concluded && inst.step === "construction" && !units.some(u => u.state === "pending") &&
        <button className="pill" disabled={busy} onClick={() => void run("conclude", {})}
          title="Construction ends when you conclude it (Q18)">{!w.cover_asked && (w.unclaimed || []).length > 0
            ? `The essay is built — review the ${(w.unclaimed || []).length} old paragraph(s) no beat claimed`
            : "The essay is built — conclude the beats"}</button>}
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
      manuscript={manuscript} busy={busy} run={run} showHidden={showHidden} setShowHidden={setShowHidden} job={v.job} inst={v.instance.id} />)}
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
    {c.revised_here && <p className="wf-revised-line">The letter was revised here: its passes re-tick this list, and the passes you choose now apply only to the units still ahead.</p>}
    {c.passes.map(p => <div key={p.name} className="wf-check">
      <label><input type="checkbox" checked={!!ticks[p.name]} disabled={!here || busy}
        onChange={e => setTicks(t => ({ ...t, [p.name]: e.target.checked }))} /> <b>{p.name}</b></label>
      <span className="muted">{p.why}</span></div>)}
    {here && <button className="pill on" disabled={busy} onClick={() => void run("filters", { names: c.passes.filter(p => ticks[p.name]).map(p => p.name) })}>
      {c.confirmed ? "Change the selection" : "Use these passes"}</button>}
  </div>;
}

// "Start over from here" (owner, 2026-11-26: "abandoning the workflow
// instance... copying the selections of voice and reference and
// everything that's already there and starting from scratch, but then
// changing the brief as you want it and regenerating the letter"). One
// sentence of reason and the brief (the instance's, editable); the server
// closes this instance as abandoned and starts the new one (core.restart).
function RestartForm({ v, transport, manuscript, onStarted, busy }: {
  v: View; transport: Transport; manuscript: string; busy: boolean; onStarted: (id: string) => Promise<void>;
}) {
  const inst = v.instance;
  const [draft, setDraft] = useJsonDraft<{ reason: string; brief: string }>(`authorlm.wf.restartDraft.${manuscript}.${inst.id}`);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  if (!draft) return <div className="devedit-actions">
    <button className="pill" disabled={busy} onClick={() => setDraft({ reason: "", brief: inst.brief || "" })}>Start over from here…</button>
  </div>;
  return <div className="wf-restart">
    <div className="muted">Closes {inst.kind} #{inst.number} as abandoned (kept on the record; nothing more lands) and starts #{inst.number + 1} with the same lists, engine, units, filters and this brief. Its letter can read this one's.</div>
    <input className="wf-input wide" placeholder="one sentence: why start over" value={draft.reason}
      onChange={e => setDraft({ ...draft, reason: e.target.value })} aria-label="Why start over" />
    <textarea className="devedit-edit" rows={rowsFor(draft.brief, 4)} value={draft.brief} aria-label="The new instance's brief"
      placeholder="The brief for the new instance" onChange={e => setDraft({ ...draft, brief: e.target.value })} />
    {error && <div className="bad">{error}</div>}
    <div className="devedit-actions">
      <button className="pill on" disabled={busy || sending || !draft.reason.trim()} onClick={async () => {
        setSending(true);
        try {
          const r = await transport.request<{ id: string }>("restart", { id: inst.id, reason: draft.reason, brief: draft.brief });
          setDraft(null); setError(""); await onStarted(r.id);
        } catch (err) { setError((err as Error).message); } finally { setSending(false); }
      }}>{sending ? "Starting…" : "Start over"}</button>
      <button className="pill" disabled={sending} onClick={() => setDraft(null)}>Cancel</button>
    </div>
  </div>;
}

// ------------------------------------------------------------------ jobs

const JOB_WORDS: Record<string, string> = {
  recommend: "Recommending", letter: "Writing the letter", letter_reply: "Answering",
  propose: "Proposing", act: "Redoing", reopen: "Reopening", wrapup: "Writing the wrap-up", discuss: "Answering",
  summaries: "Bringing summaries up to date", revise_letter: "Revising the letter",
};
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// The running turn with its elapsed time (ticking here between polls) and
// what it has read so far; or the failed attempt with its error and Retry.
function JobCard({ job, run, dismiss }: { job: Job; run: (m: string, p: Record<string, unknown>) => Promise<boolean>; dismiss: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  const [seen] = useState(() => Date.now());
  useEffect(() => {
    if (job.state !== "running") return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [job.state]);
  const elapsed = job.state === "running" ? job.elapsed + Math.max(0, (now - seen) / 1000) : job.elapsed;
  const p = job.progress || {};
  const what = JOB_WORDS[job.method] || job.method;
  if (job.state === "running") return <div className="card wf-job">
    <span>{what}… {clock(elapsed)}</span>
    {p.total !== undefined ? <span className="muted"> · {p.done || 0} of {p.total} essays{p.last ? ` (now ${short(p.last)})` : ""}</span>
      : p.requests ? <span className="muted"> · {p.requests} read request{p.requests === 1 ? "" : "s"} so far{p.last ? ` (last: ${p.last})` : ""}</span> : null}
    <div className="muted">It runs on the server: you may leave this page and come back.</div>
  </div>;
  return <div className="card wf-job">
    <div className="bad">{what} failed after {clock(elapsed)}: {job.error}</div>
    <div className="muted">Nothing was recorded from this attempt.</div>
    <div className="devedit-actions">
      <button className="pill on" onClick={() => void run(job.method, job.params)}>Retry</button>
      <button className="pill" onClick={dismiss}>Dismiss</button>
    </div>
  </div>;
}

// --------------------------------------------------------------- the page

export function Workflows({ transport, manuscript, route, onRoute }: {
  transport: Transport; manuscript: string; route: WorkflowRoute; onRoute: (r: WorkflowRoute) => void;
}) {
  const [ov, setOv] = useState<Overview | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [asking, setBusy] = useState(false);
  // Busy while a request is out, or while a model turn runs on the server.
  const busy = asking || view?.job?.state === "running";
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

  // While a model turn runs on the server, poll the instance; the job's
  // end brings the turn (or its error) onto the page.
  const jobRunning = view?.job?.state === "running";
  useEffect(() => {
    if (!jobRunning) return;
    const t = window.setInterval(() => { void loadView(); }, 2000);
    return () => { window.clearInterval(t); void loadOv(); };
  }, [jobRunning, loadView, loadOv]);

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

  // The essay drop-down (owner's ask 2026-11-26: "Use standard UI controls
  // like drop-downs … to make workflows and tasks easily navigable"): the
  // essays with an open workflow first, then those whose workflows are all
  // done, then the rest. Its choice, with the instance and step, stays in
  // the URL hash, as before (App.tsx writes it from `route`).
  const inProgress = ov.essays.filter(e => e.open);
  const finished = ov.essays.filter(e => !e.open && e.instances.length);
  const notStarted = ov.essays.filter(e => !e.instances.length);
  const essayOption = (e: Essay) => {
    const at = e.instances.find(i => i.id === e.open) || e.instances[e.instances.length - 1];
    return <option key={e.file} value={e.file}>{short(e.file)}{at ? ` — ${at.kind} #${at.number} · ${STEP_WORDS[at.step]}` : ""}
      {e.instances.length > 1 ? ` (${e.instances.length})` : ""}</option>;
  };
  const startOpen = !essay?.instances.length;
  // The instance shown belongs to the chosen essay (a reply still in flight
  // for the essay left behind is not drawn under the new one).
  const here = !!(view && inst && inst.file === route.essay);

  return (
    <div className="wf">
      {error && <div className="status bad">{error}</div>}
      <div className="card wf-nav">
        <label className="wf-field">Essay
          <select value={route.essay} aria-label="Essay" onChange={e => onRoute({ essay: e.target.value, instance: "", step: "" })}>
            {!route.essay && <option value="">Choose an essay — {inProgress.length} in progress</option>}
            {route.essay && !essay && <option value={route.essay}>{short(route.essay)} (not in this manuscript)</option>}
            {inProgress.length > 0 && <optgroup label="In progress">{inProgress.map(essayOption)}</optgroup>}
            {finished.length > 0 && <optgroup label="Done">{finished.map(essayOption)}</optgroup>}
            {notStarted.length > 0 && <optgroup label="Not started">{notStarted.map(essayOption)}</optgroup>}
          </select>
        </label>
        {/* Nothing chosen yet: the next thing to do is to pick up a workflow
            already under way, one tap each (ui.md: the main action first). */}
        {!route.essay && inProgress.length > 0 && <div className="devedit-actions">
          {inProgress.map(e => {
            const at = e.instances.find(i => i.id === e.open)!;
            return <button key={e.file} className="pill on wf-continue" onClick={() => onRoute({ essay: e.file, instance: "", step: "" })}>
              Continue {short(e.file)} · {STEP_WORDS[at.step]}</button>;
          })}
        </div>}
        {essay && essay.instances.length > 0 && <div className="wf-nav-row">
          <label className="wf-field">Workflow
            <select value={instanceId} aria-label="Workflow instance" onChange={e => onRoute({ essay: route.essay, instance: e.target.value, step: "" })}>
              {essay.instances.map(i => <option key={i.id} value={i.id}>{i.kind} #{i.number} · {i.abandoned ? "abandoned" : STEP_WORDS[i.step]}</option>)}
            </select>
          </label>
          {here && inst && <label className="wf-field">Step
            <select value={shownStep} aria-label="Step" onChange={e => setStep(e.target.value)}>
              {ov.steps.map(s => {
                const reached = ov.steps.indexOf(s) <= ov.steps.indexOf(inst.step);
                return <option key={s} value={s} disabled={!reached}>{STEP_WORDS[s]}{s === inst.step ? " (current)" : reached ? " (done)" : ""}</option>;
              })}
            </select>
          </label>}
        </div>}
        {instanceId && !here && !error && <div className="muted">Loading the workflow…</div>}
        {here && view && inst && inst.step !== "done" && <div className="wf-gate">
          {view.gate ? <span className="muted">Next ({STEP_WORDS[view.next_step || ""]}) waits: {view.gate}</span>
            : <button className="pill on" disabled={busy} onClick={() => void run("advance", {})}>On to {STEP_WORDS[view.next_step || ""]}</button>}
          {/* Always in sight from the start to the end of construction, beside
              the button that moves on, not inside the switch panel below. */}
          {["start", "letter", "construction"].includes(inst.step) &&
            <WalkChoice key={inst.id} inst={inst} ov={ov} run={run} busy={busy} />}
        </div>}
        {route.essay && essay && !essay.open && <details className="wf-start" key={route.essay} open={startOpen}>
          <summary>{startOpen ? "Start a workflow on this essay" : "Start a new workflow on this essay"}</summary>
          <div className="muted">The lists, engine, units and filters carry over from its last instance.</div>
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
          }}>Start workflow</button>
        </details>}
        <details className="wf-usage">
          <summary>Usage</summary>
          {here && view && <>
            <p>This instance: {usageLine(view.usage.instance)}</p>
            <p>This essay: {usageLine(view.usage.essay)}</p>
          </>}
          <p>{ov.usage.month_name}: {usageLine(ov.usage.month)}</p>
        </details>
      </div>

      {here && view && inst && <>
        <div className="card wf-head">
          <div className="card-head"><span>{short(inst.file)} · {inst.kind} #{inst.number}</span>
            <span className="muted">{inst.engine}{inst.model ? ` (${inst.model})` : ""} · {walkLabel(inst.construction_mode, inst.unit)}; filters {UNIT_WORDS[inst.filter_unit] || `by ${inst.filter_unit}`}</span>
            {view.protected && <span className="wf-tier must" title="register = &quot;protected&quot; in the toc">protected register</span>}</div>
          {inst.engine === "openai-api" && <div className="muted">openai-api is billed: each turn's dollars are under Usage, above.</div>}
          {inst.engine === "openai-api" && inst.step !== "done" && <CostEstimate transport={transport} engine={inst.engine} model={inst.model} id={inst.id} />}
          {inst.step !== "done" && <details className="wf-settings">
            <summary>Switch engine or unit, skip a step, or start over</summary>
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
                <option value="">unit: no change</option>{ov.units.map(u => <option key={u} value={u}>{UNIT_WORDS[u] || u}</option>)}</select>
              {inst.kind !== "new" && <select value={sw.mode} onChange={e => setSw(s => ({ ...s, mode: e.target.value }))} aria-label="Construction mode">
                <option value="">how construction walks: no change</option>{ov.modes.map(u => <option key={u} value={u}>{MODE_WORDS[u] || u}</option>)}</select>}
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
            <RestartForm v={view} transport={transport} manuscript={manuscript} busy={busy}
              onStarted={async id => { await loadOv(); onRoute({ essay: inst.file, instance: id, step: "" }); }} />
          </details>}
          {view.abandoned && <div className="wf-abandoned">Abandoned at the {STEP_WORDS[view.abandoned.step] || view.abandoned.step} step: {view.abandoned.reason}
            {view.abandoned.successor && <> <button className="pill" onClick={() => onRoute({ essay: inst.file, instance: view.abandoned!.successor!, step: "" })}>Open the instance that replaced it</button></>}</div>}
          {view.restarted_from && <div className="muted">Started over from an abandoned instance: {view.restarted_from.reason}</div>}
          {view.skipped.length > 0 && <ul className="muted">{view.skipped.map(d => <li key={d.id}>Skipped {(d.detail.skipped || []).join(", ")}: {d.reason}</li>)}</ul>}
        </div>

        <BriefCard key={inst.id} v={view} run={run} asking={asking} manuscript={manuscript} />
        <PromoteCard v={view} run={run} asking={asking} manuscript={manuscript} />
        {view.job && <JobCard key={view.job.id} job={view.job} run={run}
          dismiss={() => void run("job_dismiss", { job: view.job!.id })} />}
        <LandingCard v={view} run={run} busy={busy} />
        {shownStep === "start" && <StartStep v={view} run={run} busy={busy} />}
        {shownStep === "letter" && <LetterStep v={view} run={run} busy={busy} asking={asking} manuscript={manuscript} />}
        {["construction", "wrapup", "filters"].includes(shownStep) && shownStep === inst.step &&
          <LetterInForce v={view} run={run} busy={busy} manuscript={manuscript} toLetter={() => setStep("letter")} />}
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
