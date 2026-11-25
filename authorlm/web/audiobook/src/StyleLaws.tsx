import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Transport } from "./transport";
import { recall, remember } from "./remember";

// Mirrors style_page.dispatch (authorlm/style_page.py): a manuscript's style
// laws by level. A guide shows the laws defined AT it; an essay shows its
// effective law in the order the harness sends it, each displaced law struck
// through under the law that displaces it. Every write is the CLI verb's own
// function (`authorlm style edit/retire/unretire/move/copy/add`), recorded
// with the road "web" in the law's history (`authorlm style history <id>`).
interface Event { event: string; at: string; by: string; actor: string; detail: Record<string, any>; }
interface Law {
  id: string; aspect: string; statement: string; notes: string | null; status: string; version: number;
  created_at: string | null; created_by: string | null; level: string; overrides: string[];
  displaced_by: string[]; files: number; bound: string[]; history: Event[]; readonly: boolean;
  sent: string[]; chars: number; replacement: { id: string; statement: string; known: boolean } | null;
}
interface Tally { laws: number; chars: number; notes: number; }
interface Totals extends Tally {
  by_level: Record<string, Tally>; by_aspect: Record<string, Tally>;
  style_law_chars?: number; checklist_chars?: number;
}
interface Listing { level: string; laws: Law[]; totals: Totals; guide?: string | null; rendered?: { style_law: string; checklist: string }; }
interface Levels {
  manuscript: string; guides: { name: string; depth: number; parent: string | null; active: number; retired: number }[];
  files: { file: string; guide: string | null; attached: boolean }[]; defaults: number; aspects: string[];
}
interface Similar { id: string; aspect: string; statement: string; level: string; score: number; }
type Group = "sent" | "aspect" | "level";

// Where each choice lives (author's rule 2026-11-21, "each choice I make …
// survives a reload"):
// - the manuscript and the level or essay → the URL hash (#<ms>/style:<level>),
//   since they name what is being looked at (App.tsx writes it);
// - the text filter, "show retired", the grouping → localStorage, per
//   manuscript (a personal setting);
// - the scroll position → localStorage per manuscript and level (a place in a
//   long list), restored after each load;
// - an unsaved edit or a half-written new law → localStorage per law id / per
//   level, so a reload or a lost connection keeps the words.
const key = (ms: string, what: string) => `authorlm.style.${ms}.${what}`;
const short = (id: string) => id.startsWith("default:") ? id : id.slice(0, 11);
const day = (iso: string | null | undefined) => (iso || "").slice(0, 10);
const fmt = (n: number) => n.toLocaleString();
const loadJSON = <T,>(k: string, fallback: T): T => { try { const v = recall(k); return v ? JSON.parse(v) as T : fallback; } catch { return fallback; } };

export function StyleLaws({transport, manuscript, level, onLevel}: {
  transport: Transport; manuscript: string; level: string; onLevel: (level: string) => void;
}) {
  const [levels, setLevels] = useState<Levels | null>(null);
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [query, setQuery] = useState(() => recall(key(manuscript, "query")));
  const [retired, setRetired] = useState(() => recall(key(manuscript, "retired")) === "1");
  const [group, setGroup] = useState<Group>(() => (recall(key(manuscript, "group")) as Group) || "sent");
  useEffect(() => remember(key(manuscript, "query"), query), [manuscript, query]);
  useEffect(() => remember(key(manuscript, "retired"), retired ? "1" : ""), [manuscript, retired]);
  useEffect(() => remember(key(manuscript, "group"), group), [manuscript, group]);
  const lv = level || "all";
  const isFile = lv.startsWith("file:");
  const isGuide = lv.startsWith("guide:");

  const loadLevels = useCallback(() => transport.request<Levels>("levels", {})
    .then(setLevels).catch(err => setError((err as Error).message)), [transport]);
  const restored = useRef("");
  const load = useCallback(async () => {
    try {
      const l = await transport.request<Listing>("laws", {level: lv});
      setListing(l); setError("");
    } catch (err) { setError((err as Error).message); }
  }, [transport, lv]);
  useEffect(() => { void loadLevels(); }, [loadLevels]);
  useEffect(() => { setListing(null); restored.current = ""; void load(); }, [load]);
  // Back to the place in the list once the laws are there; then keep it.
  useEffect(() => {
    if (!listing || restored.current === lv) return;
    restored.current = lv;
    const y = Number(recall(key(manuscript, `scroll.${lv}`)) || 0);
    if (y) window.scrollTo({top: y});  // the effect runs after the cards are laid out
  }, [listing, lv, manuscript]);
  useEffect(() => {
    let t = 0;
    // Not before the list is back in place: the short page while it loads
    // would otherwise record the top and lose the place.
    const onScroll = () => {
      if (restored.current !== lv) return;
      window.clearTimeout(t);
      t = window.setTimeout(() => remember(key(manuscript, `scroll.${lv}`), String(Math.round(window.scrollY))), 200);
    };
    window.addEventListener("scroll", onScroll, {passive: true});
    return () => { window.removeEventListener("scroll", onScroll); window.clearTimeout(t); };
  }, [manuscript, lv]);

  const act = useCallback(async (method: string, params: Record<string, unknown>, done: string) => {
    try {
      await transport.request(method, params);
      setNote(done); setError("");
      await Promise.all([load(), loadLevels()]);
      return true;
    } catch (err) { setError((err as Error).message); return false; }
  }, [transport, load, loadLevels]);

  const shown = useMemo(() => {
    if (!listing) return [];
    const q = query.trim().toLowerCase();
    return listing.laws.filter(l => (retired || l.status !== "retired")
      && (!q || `${l.statement} ${l.notes || ""} ${l.id} ${l.aspect} ${l.level}`.toLowerCase().includes(q)));
  }, [listing, query, retired]);
  const groups = useMemo(() => {
    const g = group === "sent" && !isFile ? "aspect" : group;
    if (g === "sent") return [{name: "", laws: shown}];
    const out = new Map<string, Law[]>();
    for (const l of shown) {
      const name = g === "aspect" ? l.aspect : l.level;
      out.set(name, [...(out.get(name) || []), l]);
    }
    return [...out.entries()].sort((a, b) => g === "aspect" ? a[0].localeCompare(b[0]) : 0).map(([name, laws]) => ({name, laws}));
  }, [shown, group, isFile]);

  const targets = useMemo(() => levels ? [
    ...levels.guides.map(g => ({value: `guide:${g.name}`, label: `${" ".repeat(g.depth)}${g.name}`})),
    ...levels.files.map(f => ({value: `file:${f.file}`, label: `essay: ${f.file}`})),
  ] : [], [levels]);

  if (error && !levels) return <div className="status bad">{error}</div>;
  if (!levels) return <div className="boot">Reading the style laws…</div>;
  const fileName = isFile ? lv.slice(5) : "";
  const t = listing?.totals;
  return (
    <div className="sl">
      <div className="sl-pick">
        <select value={isFile ? (levels.files.find(f => f.file === fileName)?.guide ? `guide:${levels.files.find(f => f.file === fileName)!.guide}` : "all") : lv}
          onChange={e => onLevel(e.target.value)} aria-label="Level">
          <option value="all">{levels.manuscript} — every level</option>
          {levels.guides.map(g => <option key={g.name} value={`guide:${g.name}`}>
            {" ".repeat(g.depth)}{g.name} ({g.active}{g.retired ? `, ${g.retired} retired` : ""})</option>)}
          <option value="defaults">Workspace defaults ({levels.defaults}, read-only)</option>
        </select>
        <select value={fileName} onChange={e => onLevel(e.target.value ? `file:${e.target.value}` : (isFile ? "all" : lv))} aria-label="Essay">
          <option value="">One essay: its effective law…</option>
          {levels.files.map(f => <option key={f.file} value={f.file}>{f.file.replace(/\.md$/, "")} — {f.guide || "no guide"}{f.attached ? "" : " (unattached)"}</option>)}
        </select>
      </div>

      {t && <Sizes totals={t} isFile={isFile} rendered={listing?.rendered} guide={listing?.guide} />}

      <div className="sl-tools">
        <input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Filter: words, id, aspect" aria-label="Filter" />
        <select value={group} onChange={e => setGroup(e.target.value as Group)} aria-label="Group">
          {isFile && <option value="sent">As sent</option>}
          <option value="aspect">By aspect</option>
          <option value="level">By level</option>
        </select>
        <label className="sl-check"><input type="checkbox" checked={retired} onChange={e => setRetired(e.target.checked)} /> retired</label>
      </div>

      {(isGuide || isFile) && <AddLaw transport={transport} manuscript={manuscript} level={lv} aspects={levels.aspects}
        onAdd={(p, done) => act("add", {...p, level: lv}, done)} />}
      {!isGuide && !isFile && <p className="sl-hint">Pick a guide or an essay to add a law there.</p>}

      {error && <div className="status bad">{error}</div>}
      {note && <div className="sl-note" role="status">{note}</div>}
      {!listing && <div className="boot">Reading…</div>}
      {listing && !shown.length && <p className="sl-hint">No laws here{query ? " match the filter" : ""}.</p>}
      {groups.map(g => <section key={g.name || "all"}>
        {g.name && <h3 className="sl-group">{g.name} <span>{g.laws.filter(l => l.status === "active" && !l.displaced_by.length).length} · {fmt(g.laws.filter(l => l.status === "active" && !l.displaced_by.length).reduce((n, l) => n + l.chars, 0))} chars</span></h3>}
        {g.laws.map(l => <LawCard key={l.id} law={l} manuscript={manuscript} aspects={levels.aspects} targets={targets}
          act={act} showLevel={!isGuide} />)}
      </section>)}
    </div>
  );
}

function Sizes({totals, isFile, rendered, guide}: {totals: Totals; isFile: boolean; rendered?: Listing["rendered"]; guide?: string | null}) {
  const rows = (table: Record<string, Tally>, sort: boolean) => {
    const entries = Object.entries(table);
    if (sort) entries.sort((a, b) => b[1].chars - a[1].chars);
    return entries.map(([name, v]) => <tr key={name}><th>{name}</th><td>{v.laws}</td><td>{fmt(v.chars)}</td><td>{fmt(v.notes)}</td></tr>);
  };
  return <details className="sl-sizes" open>
    <summary><b>{totals.laws}</b> laws in force · <b>{fmt(totals.chars)}</b> chars of statements · {fmt(totals.notes)} of notes
      {isFile && totals.style_law_chars != null && <> · sent: <b>{fmt(totals.style_law_chars)}</b> STYLE LAW, {fmt(totals.checklist_chars || 0)} checklist</>}</summary>
    {isFile && <p className="sl-hint">Follows {guide || "the house style"}. Filters, lenses and drafting get the STYLE LAW block (statements of the prose aspects); the critic gets the checklist (every aspect). Notes are not sent.</p>}
    <div className="sl-tables">
      <table><thead><tr><th>Level</th><th>laws</th><th>chars</th><th>notes</th></tr></thead><tbody>{rows(totals.by_level, false)}</tbody></table>
      <table><thead><tr><th>Aspect</th><th>laws</th><th>chars</th><th>notes</th></tr></thead><tbody>{rows(totals.by_aspect, true)}</tbody></table>
    </div>
    {rendered && <details className="sl-raw"><summary>As sent: STYLE LAW block ({fmt(rendered.style_law.length)} chars)</summary><pre>{rendered.style_law}</pre></details>}
    {rendered && <details className="sl-raw"><summary>As sent: critic checklist ({fmt(rendered.checklist.length)} chars)</summary><pre>{rendered.checklist}</pre></details>}
  </details>;
}

function AddLaw({transport, manuscript, level, aspects, onAdd}: {
  transport: Transport; manuscript: string; level: string; aspects: string[];
  onAdd: (p: Record<string, unknown>, done: string) => Promise<boolean>;
}) {
  const store = key(manuscript, `draft.add.${level}`);
  const [draft, setDraft] = useState(() => loadJSON(store, {aspect: "lexicon", statement: "", notes: "", open: false}));
  useEffect(() => remember(store, draft.statement || draft.notes || draft.open ? JSON.stringify(draft) : ""), [store, draft]);
  const [similar, setSimilar] = useState<Similar[]>([]);
  useEffect(() => {
    const s = draft.statement.trim();
    if (s.length < 12) { setSimilar([]); return; }
    const t = window.setTimeout(() => transport.request<Similar[]>("similar", {level, statement: s})
      .then(setSimilar).catch(() => setSimilar([])), 400);
    return () => window.clearTimeout(t);
  }, [draft.statement, level, transport]);
  const where = level.startsWith("file:") ? `${level.slice(5)} only (a file-local law)` : level.slice(6);
  if (!draft.open) return <button className="btn gen sl-add-open" onClick={() => setDraft({...draft, open: true})}>＋ Add a law to {where}</button>;
  return <div className="card sl-add">
    <div className="display">New law for {where}</div>
    <select value={draft.aspect} onChange={e => setDraft({...draft, aspect: e.target.value})} aria-label="Aspect">
      {aspects.map(a => <option key={a} value={a}>{a}</option>)}
    </select>
    <textarea value={draft.statement} onChange={e => setDraft({...draft, statement: e.target.value})} placeholder="The law, as the model should read it" aria-label="Statement" rows={3} />
    <textarea value={draft.notes} onChange={e => setDraft({...draft, notes: e.target.value})} placeholder="Note (not sent to the model): your words, why, Inspect: hints" aria-label="Note" rows={2} />
    {similar.length > 0 && <div className="moved">Reads like a law already in force here or above it (adding still works):
      <ul>{similar.map(s => <li key={s.id}><b>{short(s.id)}</b> ({s.level}, {s.aspect}): {s.statement}</li>)}</ul></div>}
    <div className="actions">
      <button className="btn take" disabled={!draft.statement.trim()} onClick={async () => {
        if (await onAdd({aspect: draft.aspect, statement: draft.statement, notes: draft.notes}, `Added to ${where}.`))
          setDraft({aspect: draft.aspect, statement: "", notes: "", open: false});
      }}>Add</button>
      <button className="btn plain" onClick={() => setDraft({...draft, open: false})}>Close</button>
    </div>
  </div>;
}

function LawCard({law, manuscript, aspects, targets, act, showLevel}: {
  law: Law; manuscript: string; aspects: string[]; targets: {value: string; label: string}[];
  act: (method: string, params: Record<string, unknown>, done: string) => Promise<boolean>; showLevel: boolean;
}) {
  const store = key(manuscript, `draft.edit.${law.id}`);
  const [edit, setEdit] = useState<null | {statement: string; notes: string; aspect: string}>(() => loadJSON(store, null));
  useEffect(() => remember(store, edit ? JSON.stringify(edit) : ""), [store, edit]);
  const [panel, setPanel] = useState<"" | "retire" | "move" | "history">("");
  const [reason, setReason] = useState("");
  const [to, setTo] = useState("");
  const displaced = law.displaced_by.length > 0;
  const retired = law.status === "retired";
  const last = law.history.filter(h => h.event !== "add").slice(-1)[0];
  const retiredWhy = [...law.history].reverse().find(h => h.event === "retire")?.detail.reason;
  return <article className={`card sl-law${displaced ? " displaced" : ""}${retired ? " retired" : ""}`} id={`law-${law.id}`}>
    <div className="card-head">
      <span className="badge">{law.aspect}</span>
      {showLevel && <span className="sl-level">{law.level}</span>}
      <span className="sl-id" title={law.id}>{short(law.id)}</span>
      {!law.readonly && <span title={`added ${law.created_at || ""} by ${law.created_by || "?"}`}>v{law.version} · {day(law.created_at)}</span>}
      <span className="state" title={law.bound.join("\n")}>{law.files} file{law.files === 1 ? "" : "s"}</span>
    </div>
    {displaced && <div className="sl-why">displaced by {law.displaced_by.map(short).join(", ")}</div>}
    {retired && <div className="sl-why">retired{retiredWhy ? `: ${retiredWhy}` : ""}{law.replacement ? <> · {law.replacement.known ? "replaced by" : "likely replaced by"} <b>{short(law.replacement.id)}</b>: {law.replacement.statement}</> : null}</div>}
    {!edit && <p className="text sl-statement">{law.statement}</p>}
    {!edit && law.notes && <p className="sl-notes">{law.notes}</p>}
    {!edit && !law.sent.includes("style_law") && <div className="sl-hint">Not in the STYLE LAW block (a {law.aspect} law); the critic's checklist only.</div>}
    {!edit && last && <div className="sl-hint">{last.event} {day(last.at)} by {last.by} via {last.actor}{last.detail.before?.statement ? <> · was: <s>{last.detail.before.statement}</s></> : null}</div>}
    {law.readonly && <div className="sl-hint">A workspace default: edit it in authorlm/library/style-defaults.toml, or add a law that overrides it.</div>}
    {edit && <div className="sl-edit">
      <select value={edit.aspect} onChange={e => setEdit({...edit, aspect: e.target.value})} aria-label="Aspect">
        {aspects.map(a => <option key={a} value={a}>{a}</option>)}
      </select>
      <textarea value={edit.statement} onChange={e => setEdit({...edit, statement: e.target.value})} aria-label="Statement" rows={4} />
      <textarea value={edit.notes} onChange={e => setEdit({...edit, notes: e.target.value})} aria-label="Note" rows={3} placeholder="Note (not sent to the model)" />
      <div className="sl-hint">Same id ({short(law.id)}); version {law.version + 1}; the present wording stays in its history.</div>
      <div className="actions">
        <button className="btn take" onClick={async () => {
          const changed: Record<string, unknown> = {id: law.id};
          if (edit.statement !== law.statement) changed.statement = edit.statement;
          if (edit.notes !== (law.notes || "")) changed.notes = edit.notes;
          if (edit.aspect !== law.aspect) changed.aspect = edit.aspect;
          if (await act("edit", changed, `Saved ${short(law.id)}.`)) setEdit(null);
        }}>Save</button>
        <button className="btn plain" onClick={() => setEdit(null)}>Cancel</button>
      </div>
    </div>}
    {!law.readonly && !edit && <div className="actions sl-actions">
      {!retired && <button className="btn plain" onClick={() => setEdit({statement: law.statement, notes: law.notes || "", aspect: law.aspect})}>✎ Edit</button>}
      {!retired && <button className="btn plain" onClick={() => setPanel(panel === "retire" ? "" : "retire")}>Retire</button>}
      {retired && <button className="btn plain" onClick={() => act("unretire", {id: law.id}, `${short(law.id)} is in force again.`)}>Un-retire</button>}
      {!retired && <button className="btn plain" onClick={() => setPanel(panel === "move" ? "" : "move")}>Move / copy</button>}
      {law.history.length > 0 && <button className="btn plain" onClick={() => setPanel(panel === "history" ? "" : "history")}>History ({law.history.length})</button>}
    </div>}
    {panel === "retire" && !retired && <div className="sl-panel">
      <input value={reason} onChange={e => setReason(e.target.value)} placeholder="Why retire it? (required)" aria-label="Reason" />
      <button className="btn retake" disabled={!reason.trim()} onClick={async () => {
        if (await act("retire", {id: law.id, reason}, `Retired ${short(law.id)}.`)) { setPanel(""); setReason(""); }
      }}>Retire</button>
    </div>}
    {panel === "move" && <div className="sl-panel">
      <select value={to} onChange={e => setTo(e.target.value)} aria-label="To">
        <option value="">To…</option>
        {targets.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
      </select>
      <button className="btn plain" disabled={!to} onClick={async () => { if (await act("move", {id: law.id, to}, `Moved ${short(law.id)}.`)) setPanel(""); }}>Move</button>
      <button className="btn plain" disabled={!to} onClick={async () => { if (await act("copy", {id: law.id, to}, `Copied ${short(law.id)}.`)) setPanel(""); }}>Copy</button>
    </div>}
    {panel === "history" && <ol className="sl-history">
      {law.history.map((h, i) => <li key={i}>
        <b>{h.event}</b> {h.at.slice(0, 16).replace("T", " ")} by {h.by} via {h.actor}
        {h.detail.reason && <> — {h.detail.reason}</>}
        {h.detail.from && <> — {h.detail.from} → {h.detail.to}</>}
        {h.detail.copy && <> — copy {short(h.detail.copy)} at {h.detail.to}</>}
        {h.detail.copy_of && <> — copied from {short(h.detail.copy_of)}</>}
        {h.detail.before && Object.entries(h.detail.before as Record<string, string | null>).map(([k, v]) => <div key={k} className="sl-was">
          {k} was: <s>{v || "(none)"}</s>
          {!retired && <button className="chip sl-restore" onClick={() => setEdit({statement: law.statement, notes: law.notes || "", aspect: law.aspect, [k]: v || ""})}>Put back</button>}
        </div>)}
      </li>)}
    </ol>}
  </article>;
}
