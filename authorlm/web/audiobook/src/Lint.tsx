import { useCallback, useEffect, useMemo, useState } from "react";
import type { Transport } from "./transport";
import { recall, remember } from "./remember";

// The Lint view of the Filters page. Mirrors lint_page.dispatch
// (authorlm/lint_page.py), reached as the lint.* methods of /api/runs.
// Lint is deterministic and free: Run lints the ticked tabs on the server at
// once. Counts per rule come first; the rare rules, ruled one by one, come
// ahead of the bulk ones (owner's ask of 2026-11-25). A fix goes straight
// into the text and the tab (pull, edit, guarded push); no form is made.
//
// Where each choice lives (the owner's rule of 2026-11-21: "each choice I
// make ... survives a reload"):
//   URL hash      the rule and tab the findings are narrowed to
//                 (#<manuscript>/runs:lint[:<rule>[:<file>]], kept by
//                 App.tsx through onFilter): it names what is being looked
//                 at, so it is worth a bookmark or a link.
//   localStorage  per manuscript: the ticked tabs (shared with Filters and
//                 lenses, the same saved ticks), the ticked rules, the place
//                 in the one-by-one list (the finding's id), and the fixes
//                 accepted but not yet applied: personal working state, of
//                 no use in a shared link. The last run's tabs and rules are
//                 kept too, so a reload runs lint again and lands on the
//                 same finding.
const PROOF = ["spelling", "doubled-word", "spelling-variant", "quote-style", "footnote", "link-tracking", "banned-word"];
const STYLE = ["what-opener", "this-not-that", "term-in-definition", "em-dash-stack", "em-dash", "long-sentence",
  "reading-grade", "overlap", "redefinition", "italic-term"];
const WORDS: Record<string, string> = {
  "spelling": "Spelling", "doubled-word": "Doubled words", "unbalanced-quotes": "Unbalanced quote marks",
  "footnote": "Footnotes", "banned-word": "Banned words", "em-dash-stack": "Stacked em-dashes",
  "spelling-variant": "Spelling variant", "quote-style": "Quote style", "link-tracking": "Tracking tags in links",
  "em-dash": "Em-dash warnings",
};
const ruleName = (r: string) => WORDS[r] || r.replace(/-/g, " ");

interface Finding {
  id: string; rule: string; file: string; code: string; severity: string; quote: string; message: string; ref: string;
  line?: number; paragraph?: string; word?: string; suggestions?: string[]; uses?: number; to?: string | null;
  suggested?: string; style?: string | null;
}
interface Group { rule: string; count: number; act: "one" | "bulk" | "count"; ignored: number; findings: Finding[]; note?: string }
interface RunResult {
  files: string[]; groups: Group[]; counts: Record<string, number>; ignored: Record<string, number>;
  quote_laws: Record<string, { id: string; statement: string }>; ignore_file: string; accepted_file: string; error?: string;
}
interface Fix { id?: string; kind: "curl" | "untrack" | "word" | "paragraph"; file: string; rule: string; word?: string; to?: string; old?: string; new?: string; line?: number }
interface Segment { op: "equal" | "delete" | "insert"; text: string }
interface Preview { count: number; files: Record<string, number>; pairs: { file: string; line: number; segments: Segment[] }[]; error?: string }
interface TabOut { file: string; applied: string[]; blocked: { id: string; reason: string }[]; pushed: boolean; restaged: number; warnings: string[]; message: string }
interface Applied { batch: string; tabs: TabOut[]; applied: number; blocked: number; error?: string }
interface LogRow { id: string; file: string; rule: string; line: number; before_text: string; after_text: string; pushed: number; undone_at: string | null; created_at: string; segments: Segment[] }
interface Batch { batch: string; at: string; changes: LogRow[] }
interface TabInfo { file: string; title: string; checked_out: boolean; url: string | null }

const readJSON = <T,>(key: string, fallback: T): T => {
  try { const v = JSON.parse(recall(key) || "null"); return v == null ? fallback : v as T; } catch { return fallback; }
};
const short = (f: string) => f.replace(/\.md$/, "");

// A changed paragraph as one line: what goes struck through, what comes
// marked; long unchanged stretches are cut to their ends.
function Diff({ segments }: { segments: Segment[] }) {
  return <p className="lint-diff">{segments.map((s, i) => {
    if (s.op === "delete") return <del key={i}>{s.text}</del>;
    if (s.op === "insert") return <ins key={i}>{s.text}</ins>;
    const t = s.text.length > 90 ? `${i ? s.text.slice(0, 40) : ""}…${i < segments.length - 1 ? s.text.slice(-40) : ""}` : s.text;
    return <span key={i}>{t}</span>;
  })}</p>;
}

export function Lint({ transport, manuscript, tabs, ticked, setTicked, filter, onFilter }: {
  transport: Transport; manuscript: string; tabs: TabInfo[]; ticked: string[]; setTicked: (v: string[]) => void;
  filter: string; onFilter: (f: string) => void;
}) {
  const store = `authorlm.runs.${manuscript}.lint`;
  const [rules, setRules] = useState<string[]>(() => readJSON(`${store}.rules`, PROOF));
  const [result, setResult] = useState<RunResult | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [pos, setPos] = useState<string>(() => recall(`${store}.pos`));
  const [queued, setQueued] = useState<Fix[]>(() => readJSON(`${store}.queued`, [] as Fix[]));
  const [preview, setPreview] = useState<{ label: string; fixes: Fix[]; out: Preview } | null>(null);
  const [applied, setApplied] = useState<Applied | null>(null);
  const [draft, setDraft] = useState("");
  const [typed, setTyped] = useState("");
  const [log, setLog] = useState<Batch[]>([]);
  const [askedRule, askedFile] = filter.split(":");

  useEffect(() => { remember(`${store}.rules`, JSON.stringify(rules)); }, [store, rules]);
  useEffect(() => { remember(`${store}.pos`, pos); }, [store, pos]);
  useEffect(() => { remember(`${store}.queued`, queued.length ? JSON.stringify(queued) : ""); }, [store, queued]);

  const loadLog = useCallback(async () => {
    try { const out = await transport.request<{ batches: Batch[] }>("lint.log", {}); setLog(out.batches || []); } catch { /* shown on use */ }
  }, [transport]);

  const run = useCallback(async (files: string[], chosen: string[]) => {
    if (!files.length) return;
    setBusy("run"); setError("");
    try {
      const out = await transport.request<RunResult>("lint.run", { files, rules: chosen });
      if (out.error) { setError(out.error); return; }
      setResult(out);
      remember(`${store}.last`, JSON.stringify({ files, rules: chosen }));
    } catch (err) { setError((err as Error).message); } finally { setBusy(""); }
  }, [transport, store]);

  // A reload runs the last lint again (it is free and quick), so the list and
  // the place in it come back.
  useEffect(() => {
    const last = readJSON<{ files: string[]; rules: string[] } | null>(`${store}.last`, null);
    if (last && last.files.length) void run(last.files, last.rules);
    void loadLog();
  }, [store, run, loadLog]);

  const groups = result?.groups || [];
  // A rule or tab in the link that this run has no findings for (all fixed,
  // or not linted) narrows nothing, so the page never shows an empty list.
  const filterRule = groups.some(g => g.rule === askedRule) ? askedRule : "";
  const filterFile = result?.files.includes(askedFile) ? askedFile : "";
  const narrowed = (f: Finding) => (!filterRule || filterRule === "all" || f.rule === filterRule) && (!filterFile || f.file === filterFile);
  const oneByOne = useMemo(() => groups.filter(g => g.act === "one").flatMap(g => g.findings).filter(narrowed),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [groups, filterRule, filterFile]);
  const queuedIds = new Set(queued.map(q => q.id));
  const at = Math.max(0, oneByOne.findIndex(f => f.id === pos));
  const current = oneByOne[at];
  useEffect(() => { setDraft(current?.paragraph || ""); setTyped(""); }, [current?.id, current?.paragraph]);

  // A reload lands on the finding it left (its id is remembered).
  const [landed, setLanded] = useState(false);
  useEffect(() => {
    if (landed || !result || !pos) return;
    setLanded(true);
    window.requestAnimationFrame(() => document.querySelector(".lint-one")?.scrollIntoView({ block: "start" }));
  }, [result, pos, landed]);

  const step = (d: number) => { const next = oneByOne[Math.min(oneByOne.length - 1, Math.max(0, at + d))]; if (next) setPos(next.id); };
  const queue = (fix: Fix) => { setQueued(q => [...q.filter(x => x.id !== fix.id), fix]); step(1); };

  const ask = async (method: string, params: Record<string, unknown>) => {
    setBusy(method); setError("");
    try {
      const out = await transport.request<Record<string, unknown> & { error?: string }>(method, params);
      if (out.error) { setError(String(out.error)); return null; }
      return out;
    } catch (err) { setError((err as Error).message); return null; } finally { setBusy(""); }
  };
  const showPreview = async (label: string, fixes: Fix[]) => {
    const out = await ask("lint.preview", { fixes }) as Preview | null;
    if (out) setPreview({ label, fixes, out });
  };
  const apply = async (fixes: Fix[]) => {
    const out = await ask("lint.apply", { fixes }) as Applied | null;
    if (!out) return;
    setApplied(out); setPreview(null);
    const done = new Set(out.tabs.flatMap(t => t.applied));
    setQueued(q => q.filter(x => !done.has(x.id || "")));
    await loadLog();
    if (result) await run(result.files, rules);
  };
  const acceptWord = async (f: Finding) => {
    if (await ask("lint.accept", { words: [f.word] })) { step(1); if (result) await run(result.files, rules); }
  };
  const legitimate = async (f: Finding) => {
    if (await ask("lint.ignore", { id: f.id, rule: f.rule, file: f.file, quote: f.quote })) { step(1); if (result) await run(result.files, rules); }
  };
  const undo = async (ids: string[]) => {
    const out = await ask("lint.undo", { ids }) as Applied | null;
    if (out) { setApplied(out); await loadLog(); if (result) await run(result.files, rules); }
  };

  // The fix a finding's main button makes (the "a" key).
  const primary = (f: Finding | undefined): Fix | null => {
    if (!f) return null;
    if (f.rule === "spelling") {
      const to = typed.trim() || f.suggestions?.[0];
      return to ? { id: f.id, kind: "word", file: f.file, rule: f.rule, word: f.word, to } : null;
    }
    if (f.rule === "doubled-word" && draft === f.paragraph && f.suggested)
      return { id: f.id, kind: "paragraph", file: f.file, rule: f.rule, old: f.paragraph, new: f.suggested, line: f.line };
    if (f.paragraph && draft.trim() && draft !== f.paragraph)
      return { id: f.id, kind: "paragraph", file: f.file, rule: f.rule, old: f.paragraph, new: draft, line: f.line };
    return null;
  };

  // Keyboard: j / ArrowDown next, k / ArrowUp previous, a apply, s skip.
  // Never while a text box has focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); step(1); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); step(-1); }
      else if (e.key === "s") { e.preventDefault(); step(1); }
      else if (e.key === "a") { const fix = primary(current); if (fix) { e.preventDefault(); queue(fix); } }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const ticks = (list: string[], set: (v: string[]) => void, key: string) => set(list.includes(key) ? list.filter(k => k !== key) : [...list, key]);
  const rulePicker = (title: string, names: string[]) => (
    <div className="lint-rules">
      <span className="runs-group-title">{title}</span>
      <button className="pill" onClick={() => setRules([...new Set([...rules, ...names])])}>All</button>
      <button className="pill" onClick={() => setRules(rules.filter(r => !names.includes(r)))}>None</button>
      <div className="lint-chips">{names.map(r => (
        <label key={r} className={`pill${rules.includes(r) ? " on" : ""}`}>
          <input type="checkbox" className="lint-hide" checked={rules.includes(r)} onChange={() => ticks(rules, setRules, r)} />{ruleName(r)}
        </label>))}</div>
    </div>
  );

  const bulkGroups = groups.filter(g => g.act === "bulk");
  const countGroups = groups.filter(g => g.act === "count");
  const lawLine = (files: string[]) => {
    const laws = [...new Set(files.map(f => result?.quote_laws[f]?.statement).filter(Boolean))];
    return laws.length ? <p className="muted">The style law on quote marks: {laws.map(l => `“${l}”`).join("; ")}</p>
      : <p className="muted">No style law states the quote marks for these tabs, so each tab is moved to the style it uses more.</p>;
  };

  return (
    <div className="lint">
      {error && <div className="status bad">{error}</div>}
      <section className="card">
        <div className="card-head"><span>Lint</span><span className="badge">{ticked.length} tab{ticked.length === 1 ? "" : "s"}</span>
          <span className="badge">{rules.length} rules</span></div>
        <p className="muted">Lint is deterministic and free: it runs on the server at once, calls no model and writes nothing. Fixes you apply go straight into the text and its Doc tab; no form is made.</p>
        <div className="devedit-actions">
          <button className="pill" onClick={() => setTicked(tabs.map(t => t.file))}>All tabs</button>
          <button className="pill" onClick={() => setTicked([])}>None</button>
        </div>
        <div className="runs-tabs lint-tabs">{tabs.map(t => (
          <label key={t.file} className="runs-tab">
            <input type="checkbox" checked={ticked.includes(t.file)} onChange={() => ticks(ticked, setTicked, t.file)} />
            <span className="runs-tab-body"><span className="runs-name">{t.title}</span><span className="muted"> {t.file}{t.checked_out ? " · in the Doc (pulled first, pushed after)" : ""}</span></span>
          </label>))}</div>
        {rulePicker("Proof rules", PROOF)}
        {rulePicker("Style checks", STYLE)}
        <div className="devedit-actions">
          <button className="btn take" disabled={!!busy || !ticked.length || !rules.length} onClick={() => void run(ticked, rules)}>
            {busy === "run" ? "Linting…" : `Run lint on ${ticked.length} tab${ticked.length === 1 ? "" : "s"}`}</button>
        </div>
      </section>

      {applied && <section className="card">
        <div className="card-head"><span>Applied</span><span className="badge">{applied.applied} fixed</span><span className="badge">{applied.blocked} not applied</span>
          <button className="pill runs-close" onClick={() => setApplied(null)}>Close</button></div>
        {applied.tabs.map(t => <div key={t.file} className="lint-tab-out">
          <div className="runs-name">{short(t.file)}</div><div className="muted">{t.message}{t.restaged ? ` ${t.restaged} pending form(s) withdrawn and restaged with the fix.` : ""}</div>
          {t.blocked.map(b => <div key={b.id} className="moved bad-note">Not applied: {b.reason}</div>)}
          {t.warnings.map((w, i) => <div key={i} className="moved">{w}</div>)}
        </div>)}
      </section>}

      {result && <section className="card">
        <div className="card-head"><span>Findings by rule</span><span className="badge">{result.files.length} tabs linted</span></div>
        <div className="lint-chips">
          <button className={`pill${!filterRule || filterRule === "all" ? " on" : ""}`} onClick={() => onFilter(filterFile ? `all:${filterFile}` : "")}>All</button>
          {groups.map(g => <button key={g.rule} className={`pill${filterRule === g.rule ? " on" : ""}`}
            onClick={() => onFilter(filterRule === g.rule ? "" : `${g.rule}${filterFile ? `:${filterFile}` : ""}`)}>
            {ruleName(g.rule)} · {g.count}{g.act === "bulk" ? " (bulk)" : ""}</button>)}
        </div>
        <div className="lint-chips">
          <span className="muted">Tab:</span>
          <button className={`pill${!filterFile ? " on" : ""}`} onClick={() => onFilter(filterRule && filterRule !== "all" ? filterRule : "")}>Every linted tab</button>
          {result.files.map(f => <button key={f} className={`pill${filterFile === f ? " on" : ""}`} onClick={() => onFilter(`${filterRule || "all"}:${f}`)}>{short(f)}</button>)}
        </div>
        {groups.length === 0 && <p className="muted">Nothing found.</p>}
        {Object.keys(result.ignored).length > 0 && <p className="muted">Left out as legitimate uses: {Object.entries(result.ignored).map(([r, n]) => `${n} ${ruleName(r)}`).join(", ")} (listed in {result.ignore_file}; delete a line there to see one again).</p>}
      </section>}

      {result && oneByOne.length > 0 && <section className="card lint-one">
        <div className="card-head"><span>One by one</span><span className="badge">{at + 1} of {oneByOne.length}</span>
          {queued.length > 0 && <span className="badge runs-dirty">{queued.length} accepted, not applied</span>}</div>
        <p className="muted">Keys: j or ↓ next, k or ↑ previous, a apply, s skip. Accepted fixes wait below and go into each tab in one pull, edit and push.</p>
        {current && <div className="lint-finding">
          <div className="forms-file-head"><span className="state">{ruleName(current.rule)}</span>
            <span className="runs-name">{short(current.file)}{current.line ? `:${current.line}` : ""}</span>
            {queuedIds.has(current.id) && <span className="state done">accepted</span>}</div>
          <p>{current.message}</p>
          {current.rule === "spelling" ? <>
            <p className="lint-quote">«{current.quote}»</p>
            <div className="devedit-actions">
              {(current.suggestions || []).map(s => <button key={s} className="btn take" onClick={() => queue({ id: current.id, kind: "word", file: current.file, rule: "spelling", word: current.word, to: s })}>
                {current.word} → {s}{current.uses && current.uses > 1 ? ` (${current.uses} uses)` : ""}</button>)}
            </div>
            <div className="devedit-actions">
              <input className="runs-find lint-typed" value={typed} placeholder="Type a correction" aria-label="Type a correction" onChange={e => setTyped(e.target.value)} />
              <button className="btn" disabled={!typed.trim()} onClick={() => queue({ id: current.id, kind: "word", file: current.file, rule: "spelling", word: current.word, to: typed.trim() })}>Use it</button>
            </div>
            <div className="devedit-actions">
              <button className="btn" disabled={!!busy} onClick={() => void acceptWord(current)} title={`Adds it to ${result?.accepted_file}`}>Accept “{current.word}” for the book</button>
              <button className="btn plain" onClick={() => step(1)}>Skip</button>
            </div>
          </> : <>
            {current.paragraph != null && <textarea className="md-raw lint-text" value={draft} spellCheck={false}
              aria-label="The paragraph, to edit" onChange={e => setDraft(e.target.value)} />}
            {current.paragraph == null && <p className="lint-quote">«{current.quote}»</p>}
            <div className="devedit-actions">
              {current.rule === "doubled-word" && current.suggested && <button className="btn take" onClick={() => queue({ id: current.id, kind: "paragraph", file: current.file, rule: current.rule, old: current.paragraph, new: current.suggested, line: current.line })}>Delete one</button>}
              {current.paragraph != null && <button className="btn take" disabled={!draft.trim() || draft === current.paragraph}
                onClick={() => queue({ id: current.id, kind: "paragraph", file: current.file, rule: current.rule, old: current.paragraph, new: draft, line: current.line })}>Apply my edit</button>}
              <button className="btn" disabled={!!busy} onClick={() => void legitimate(current)}>{current.rule === "doubled-word" ? "Keep (meant)" : "Legitimate use"}</button>
              <button className="btn plain" onClick={() => step(1)}>Skip</button>
            </div>
          </>}
          <div className="devedit-actions">
            <button className="pill" disabled={at === 0} onClick={() => step(-1)}>Previous</button>
            <button className="pill" disabled={at >= oneByOne.length - 1} onClick={() => step(1)}>Next</button>
          </div>
        </div>}
      </section>}

      {queued.length > 0 && <div className="bar confirm runs-confirm lint-queued">
        <span><strong>{queued.length} accepted fix{queued.length === 1 ? "" : "es"}</strong> in {new Set(queued.map(q => q.file)).size} tab(s): {queued.map(q => `${short(q.file)} ${q.kind === "word" ? `${q.word} → ${q.to}` : ruleName(q.rule)}`).join("; ")}.</span>
        <button className="btn take" disabled={!!busy} onClick={() => void apply(queued)}>{busy === "lint.apply" ? "Applying…" : "Apply them"}</button>
        <button className="btn plain" onClick={() => setQueued([])}>Drop them</button>
      </div>}

      {result && bulkGroups.map(g => {
        const items = g.findings.filter(narrowed);
        if (!items.length) return null;
        const files = [...new Set(items.map(f => f.file))];
        return <section key={g.rule} className="card">
          <div className="card-head"><span>{ruleName(g.rule)}</span><span className="badge">{items.length} finding{items.length === 1 ? "" : "s"}</span><span className="badge">bulk</span></div>
          {g.rule === "quote-style" && lawLine(files)}
          {g.rule === "spelling-variant" ? <>
            <p className="muted">One word at a time: convert every use in the chosen tabs, or accept the spelling for the book.</p>
            {Object.entries(items.reduce((acc, f) => { (acc[f.word || ""] = acc[f.word || ""] || []).push(f); return acc; }, {} as Record<string, Finding[]>)).map(([word, fs]) => {
              const uses = fs.reduce((n, f) => n + (f.uses || 0), 0);
              const to = fs[0].to;
              const fixes: Fix[] = fs.map(f => ({ id: `${f.id}`, kind: "word", file: f.file, rule: "spelling-variant", word, to: to || "" }));
              return <div key={word} className="lint-word">
                <span className="runs-name">{word}</span><span className="muted"> {uses} use{uses === 1 ? "" : "s"} in {fs.map(f => short(f.file)).join(", ")}</span>
                <div className="devedit-actions">
                  {to && <button className="btn take" disabled={!!busy} onClick={() => void showPreview(`Convert all ${uses} “${word}” → “${to}”`, fixes)}>Convert all {uses} “{word}” → “{to}”</button>}
                  <button className="btn" disabled={!!busy} onClick={() => void acceptWord(fs[0])}>Accept “{word}” for the book</button>
                </div>
              </div>;
            })}
          </> : <>
            <p className="muted">{g.rule === "quote-style" ? "Every quote mark and apostrophe of the other style is changed; code, links and tags are left alone." : "Tracking tags (utm_ and the like) are taken out of each link; the rest of the link is kept."}</p>
            <div className="devedit-actions">
              {files.map(f => <button key={f} className="pill" disabled={!!busy} onClick={() => void showPreview(`${ruleName(g.rule)} on ${short(f)}`, [{ id: `${g.rule}:${f}`, kind: g.rule === "quote-style" ? "curl" : "untrack", file: f, rule: g.rule }])}>Apply on {short(f)}</button>)}
              {files.length > 1 && <button className="btn take" disabled={!!busy} onClick={() => void showPreview(`${ruleName(g.rule)} on ${files.length} tabs`, files.map(f => ({ id: `${g.rule}:${f}`, kind: g.rule === "quote-style" ? "curl" : "untrack", file: f, rule: g.rule })))}>Apply on all {files.length} tabs</button>}
            </div>
          </>}
          {preview && preview.fixes.some(x => x.rule === g.rule) && <div className="lint-preview">
            <p><strong>{preview.label}: {preview.out.count} change{preview.out.count === 1 ? "" : "s"}</strong> ({Object.entries(preview.out.files).map(([f, n]) => `${short(f)} ${n}`).join(", ")}). The first {preview.out.pairs.length} paragraphs:</p>
            {preview.out.pairs.map((p, i) => <div key={i} className="lint-pair"><span className="muted">{short(p.file)}:{p.line}</span><Diff segments={p.segments} /></div>)}
            <div className="devedit-actions"><button className="btn take" disabled={!!busy || !preview.out.count} onClick={() => void apply(preview.fixes)}>{busy === "lint.apply" ? "Applying…" : `Apply ${preview.out.count}`}</button>
            <button className="btn plain" onClick={() => setPreview(null)}>Not now</button></div>
          </div>}
        </section>;
      })}

      {result && countGroups.map(g => <section key={g.rule} className="card">
        <details><summary className="card-head"><span>{ruleName(g.rule)}</span><span className="badge">{g.count}</span><span className="badge">for a chat session</span></summary>
          <p className="muted">{g.note}</p></details>
      </section>)}

      <section className="card">
        <div className="card-head"><span>Changes made here</span><span className="badge">{log.length} apply click{log.length === 1 ? "" : "s"}</span></div>
        <p className="muted">Every paragraph a fix changed, before and after, is kept in the workspace database (table lint_changes). Undo puts the before back through the same pull, edit and push.</p>
        {log.length === 0 && <p className="muted">Nothing applied from this page yet.</p>}
        {log.slice(0, 20).map(b => {
          const open = b.changes.filter(c => !c.undone_at);
          return <details key={b.batch} className="lint-batch">
            <summary><span className="runs-name">{new Date(b.at).toLocaleString()}</span> <span className="muted">{b.changes.length} paragraph(s) in {[...new Set(b.changes.map(c => short(c.file)))].join(", ")} · {[...new Set(b.changes.map(c => c.rule))].join(", ")}{open.length ? "" : " · undone"}</span></summary>
            {b.changes.map(c => <div key={c.id} className="lint-pair"><span className="muted">{short(c.file)}:{c.line}{c.pushed ? " · pushed to the tab" : ""}{c.undone_at ? " · undone" : ""}</span>
              <Diff segments={c.segments} /></div>)}
            {open.length > 0 && <button className="btn" disabled={!!busy} onClick={() => void undo([b.batch])}>Undo this apply</button>}
          </details>;
        })}
      </section>
    </div>
  );
}
