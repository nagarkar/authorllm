import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { request, workbenchTransport } from "./transport";
import { Workbench } from "./Workbench";

// Mirrors audiobook.Session.status (authorlm/audiobook.py).
interface ChapterSummary { stem: string; title: string; total: number; generated: number; previewed: number; remaining: number; remainingCharacters: number; stitched: string | null; stitchStale: boolean; }
interface Moved { kind: string; detail: string[]; since: string | null; }
interface Section {
  id: string; ordinal: number; kind: string; level?: number | null; cast: string; voiceName: string; characters: number;
  firstWords: string; text: string; pronunciations: {term: string; say: string}[];
  take: string | null; preview: string | null; previewUrl: string | null; takeUrl: string | null;
  generatedAt: string | null; moved: Moved | null;
}
interface Chapter extends ChapterSummary { quality: string; durationSecs: number | null; sections: Section[]; stitchedUrl: string | null; inRetailSample: string[]; }
interface Job { id: number; kind: string; state: string; message: string; stem?: string | null; characters: number; done: number; of: number; ordinals?: number[]; at: string; finishedAt?: string; }
interface Status {
  book: { title: string; quality: string; exportedAt: string | null; manuscript: string; chapters: ChapterSummary[] };
  chapter: Chapter | null;
  doc: { state: string; detail: string; at: string | null };
  export: { stale: boolean; changed: string[]; exported: string | null; reason: string };
  account: { remaining: number; used: number; limit: number; at: string } | null;
  jobs: { running: Job | null; queued: Job[]; recent: Job[] };
}
type Source = "preview" | "take";

const fmt = (n: number) => n.toLocaleString();
const when = (iso: string | null | undefined) => iso ? iso.slice(0, 16).replace("T", " ") : "";
const mmss = (s: number | null | undefined) => s == null ? "" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
type PageMode = "chapters" | "pron";
const parseHash = (): {mode: PageMode; stem: string; term: string} => {
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (h.startsWith("pron")) return {mode: "pron", stem: "", term: h.slice(5)};
  return {mode: "chapters", stem: h, term: ""};
};

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [stem, setStem] = useState<string>(parseHash().stem);
  const [mode, setMode] = useState<PageMode>(parseHash().mode);
  const [term, setTerm] = useState<string>(parseHash().term);
  const [error, setError] = useState<string>("");
  const [confirm, setConfirm] = useState<null | {label: string; run: () => Promise<void>}>(null);
  const [now, setNow] = useState<{source: Source; id: string} | null>(null);
  const [continuous, setContinuous] = useState(true);
  const audioRef = useRef<HTMLAudioElement>(null);
  const busy = !!(status?.jobs.running || status?.jobs.queued.length);

  const load = useCallback(async (s: string) => {
    try {
      const st = await request<Status>("status", s ? {stem: s} : {});
      setStatus(st);
      setError("");
      if (!s && st.book.chapters.length) {
        // Open where the work is: the first chapter with something left, else the first.
        const first = st.book.chapters.find(c => c.remaining > 0) || st.book.chapters[0];
        setStem(first.stem);
      }
    } catch (err) { setError((err as Error).message); }
  }, []);

  useEffect(() => { load(stem); }, [stem, load]);
  useEffect(() => { location.hash = mode === "pron" ? (term ? `pron:${term}` : "pron") : stem; }, [stem, mode, term]);
  const openWord = useCallback((t: string) => { setTerm(t); setMode("pron"); window.scrollTo({top: 0}); }, []);
  useEffect(() => {
    const t = setInterval(() => load(stem), busy ? 2000 : 10000);
    return () => clearInterval(t);
  }, [stem, busy, load]);

  const act = useCallback(async (method: string, params: Record<string, unknown>) => {
    try { await request<Job>(method, params); setError(""); await load(stem); }
    catch (err) { setError((err as Error).message); }
  }, [stem, load]);

  // ------------------------------------------------------- playback
  const chapter = status?.chapter || null;
  const playable = useMemo(() => (chapter?.sections || []).filter(s => now ? (now.source === "preview" ? s.previewUrl : s.takeUrl) : false), [chapter, now]);

  const play = useCallback((source: Source, sec: Section) => {
    const url = source === "preview" ? sec.previewUrl : sec.takeUrl;
    if (!url || !audioRef.current) return;
    setNow({source, id: sec.id});
    audioRef.current.src = url;
    audioRef.current.play().catch(() => {});
    document.getElementById(`card-${sec.id}`)?.scrollIntoView({block: "center", behavior: "smooth"});
  }, []);

  const step = useCallback((dir: 1 | -1) => {
    if (!now) return;
    const i = playable.findIndex(s => s.id === now.id);
    const next = playable[i + dir];
    if (next) play(now.source, next);
    else setNow(null);
  }, [now, playable, play]);

  const onEnded = useCallback(() => { if (continuous) step(1); else setNow(null); }, [continuous, step]);

  // ------------------------------------------------------- render
  if (error && !status) return <div className="boot bad">{error}</div>;
  if (!status) return <div className="boot">Opening the audiobook…</div>;
  const {book, doc, account, jobs} = status;
  const stale = status.export;
  const refreshing = [jobs.running, ...jobs.queued].some(j => j && j.kind === "refresh");
  const cur = book.chapters.find(c => c.stem === stem);

  return (
    <div className="page">
      <header>
        <div className="title-row">
          <h1 className="display">{book.title}</h1>
          <span className="count">{account ? `${fmt(account.remaining)} chars left` : book.quality}</span>
        </div>
        <div className="modes">
          <button className={`chip${mode === "chapters" ? " on" : ""}`} aria-pressed={mode === "chapters"} onClick={() => setMode("chapters")}>Chapters</button>
          <button className={`chip${mode === "pron" ? " on" : ""}`} aria-pressed={mode === "pron"} onClick={() => setMode("pron")}>Pronunciations</button>
        </div>
        {mode === "chapters" && <div className="bar">
          <select value={stem} onChange={e => { setNow(null); setStem(e.target.value); }} aria-label="Chapter">
            {book.chapters.map(c => (
              <option key={c.stem} value={c.stem}>
                {c.title} — {c.generated}/{c.total}{c.stitched ? " ✓" : ""}
              </option>
            ))}
          </select>
          <button className="chip" onClick={() => act("refresh", {stem})} title="Export and preview what changed on disk">↻ Refresh</button>
        </div>}
        <div className="doc">
          exported {when(stale.exported) || when(book.exportedAt) || "?"} · Doc: {doc.state}
        </div>
        {stale.stale && (
          <div className="stale" role="status">
            <span>Changed since the export: {stale.changed.length ? stale.changed.join(", ") : stale.reason}</span>
            <span className="stale-note">{refreshing ? <><span className="spin dark" /> re-exporting…</> : "re-exporting shortly"}</span>
          </div>
        )}
      </header>

      {error && <div className="status bad">{error}</div>}
      {mode === "pron" && <Workbench transport={workbenchTransport} initialTerm={term || undefined} />}
      {mode === "chapters" && <JobStrip jobs={jobs} />}

      {mode === "chapters" && chapter && cur && (
        <ChapterBar chapter={chapter}
          onGenerateAll={() => setConfirm({
            label: `Render ${chapter.remaining} paragraph(s) of ${chapter.title} — ${fmt(chapter.remainingCharacters)} characters?`,
            run: () => act("generate", {stem, remaining: true}),
          })}
          onStitch={() => act("stitch", {stem})} />
      )}

      {confirm && (
        <div className="bar confirm">
          <span>{confirm.label}</span>
          <button className="btn gen" onClick={async () => { const r = confirm.run; setConfirm(null); await r(); }}>Yes, render</button>
          <button className="btn plain" onClick={() => setConfirm(null)}>No</button>
        </div>
      )}

      {mode === "chapters" && chapter && chapter.sections.map(sec => (
        <Card key={sec.id} sec={sec} stem={stem}
          retail={chapter.inRetailSample.includes(sec.id)}
          playing={now?.id === sec.id ? now.source : null}
          onPlay={play} onWord={openWord}
          onGenerate={() => act("generate", {stem, ids: [sec.id]})}
          onRetake={() => setConfirm({
            label: `Re-render ¶ ${sec.ordinal} — ${fmt(sec.characters)} characters? The current take is replaced.`,
            run: () => act("retake", {stem, ids: [sec.id]}),
          })} />
      ))}

      {mode === "chapters" && <footer className={now ? "player on" : "player"}>
        <div className="player-row">
          <span className="now display">{now ? `¶ ${playable.find(s => s.id === now.id)?.ordinal ?? "?"} · ${now.source}` : "nothing playing"}</span>
          <button className="nav" onClick={() => step(-1)} disabled={!now} aria-label="Previous">⏮</button>
          <button className="nav" onClick={() => step(1)} disabled={!now} aria-label="Next">⏭</button>
          <button className={`chip${continuous ? " on" : ""}`} aria-pressed={continuous} onClick={() => setContinuous(!continuous)}>walk on</button>
        </div>
        <audio ref={audioRef} controls onEnded={onEnded} />
      </footer>}
    </div>
  );
}

function JobStrip({jobs}: {jobs: Status["jobs"]}) {
  const r = jobs.running;
  const last = jobs.recent[jobs.recent.length - 1];
  if (!r && !jobs.queued.length && !last) return null;
  return (
    <div className="jobs">
      {r && <div className="job running">
        <span className="spin" /> <b>{r.kind}</b>{r.stem ? ` ${r.stem}` : ""}{r.of ? ` ${r.done}/${r.of}` : ""}{r.characters ? ` · ${fmt(r.characters)} chars` : ""}
        {r.message ? <span className="small"> — {r.message}</span> : null}
      </div>}
      {jobs.queued.length > 0 && <div className="job small">{jobs.queued.length} queued: {jobs.queued.map(j => `${j.kind}${j.ordinals ? " ¶ " + j.ordinals.join(",") : ""}`).join(" · ")}</div>}
      {!r && last && <div className={`job small ${last.state === "failed" ? "bad" : "ok"}`}>
        {last.kind}{last.stem ? ` ${last.stem}` : ""}{last.ordinals ? ` ¶ ${last.ordinals.join(",")}` : ""}: {last.message} ({when(last.finishedAt)})
      </div>}
    </div>
  );
}

function ChapterBar({chapter, onGenerateAll, onStitch}: {chapter: Chapter; onGenerateAll: () => void; onStitch: () => void}) {
  const ready = chapter.remaining === 0 && chapter.total > 0;
  return (
    <div className="chapter-bar">
      <div className="counts">
        <b className="display big">{chapter.generated}/{chapter.total}</b> rendered · {chapter.previewed} previewed
        {chapter.remaining > 0 && <> · <span className="warn">{chapter.remaining} left, {fmt(chapter.remainingCharacters)} chars</span></>}
        {chapter.stitched && <> · stitched {mmss(chapter.durationSecs)}{chapter.stitchStale ? <span className="warn"> (stale)</span> : null}</>}
      </div>
      <div className="actions">
        {chapter.remaining > 0 && <button className="btn gen" onClick={onGenerateAll}>✦ Generate remaining · {fmt(chapter.remainingCharacters)}</button>}
        <button className="btn take" onClick={onStitch} disabled={!ready} title={ready ? "" : "Every paragraph needs a take first"}>
          {chapter.stitched ? (chapter.stitchStale ? "Re-stitch (stale)" : "Re-stitch") : "Stitch"}
        </button>
      </div>
      {chapter.stitchedUrl && <audio className="stitched" controls preload="none" src={chapter.stitchedUrl} />}
    </div>
  );
}

function Card({sec, retail, playing, onPlay, onWord, onGenerate, onRetake}: {
  sec: Section; stem: string; retail: boolean; playing: Source | null;
  onPlay: (s: Source, sec: Section) => void; onWord: (term: string) => void; onGenerate: () => void; onRetake: () => void;
}) {
  const badge = sec.kind === "heading" ? `H${sec.level ?? ""}` : "¶";
  return (
    <article id={`card-${sec.id}`} className={`card${playing ? " playing" : ""}${sec.take ? " done" : ""}`}>
      <div className="card-head">
        <span className="ord display">{sec.ordinal}</span>
        <span className={`badge ${sec.kind}`}>{badge}</span>
        {retail && <span className="retail" title="In the retail sample">◇ sample</span>}
        <span className="cast">{sec.cast}</span>
        <span className="voice">{sec.voiceName}</span>
        <span className={`state ${sec.take ? "done" : sec.preview ? "previewed" : "none"}`} title={sec.take ? `take ${when(sec.generatedAt)}` : ""}>
          {sec.take ? "rendered" : sec.preview ? "preview" : "no preview"}
        </span>
      </div>
      <p className="text">{sec.text}</p>
      {sec.pronunciations.length > 0 && <div className="alias">
        {sec.pronunciations.map(p => <button key={p.term} className="word" onClick={() => onWord(p.term)} title="Open in the pronunciation workbench">{p.term} → {p.say}</button>)}
      </div>}
      {sec.moved && <div className="moved">changed since {when(sec.moved.since) || "its take"}: {sec.moved.detail.join(", ")}</div>}
      <div className="actions seg">
        {sec.previewUrl && <button className={`btn preview${playing === "preview" ? " on" : ""}`} onClick={() => onPlay("preview", sec)}>{playing === "preview" ? "◼" : "▶"} Preview</button>}
        {sec.takeUrl && <button className={`btn take${playing === "take" ? " on" : ""}`} onClick={() => onPlay("take", sec)}>{playing === "take" ? "◼" : "▶"} Take</button>}
        {sec.take
          ? <button className="btn retake" onClick={onRetake}>↺ Retake · {fmt(sec.characters)}</button>
          : <button className="btn gen" onClick={onGenerate}>✦ Generate · {fmt(sec.characters)}</button>}
      </div>
    </article>
  );
}
