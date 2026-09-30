"""The audiobook page's backend — the one surface for reviewing previews
and takes and triggering renders, on the phone and on the Mac
(docs/audiobook-review-design.md §3).

Served by `authorlm audio serve` (audiobook_server.py) exactly like the
pronunciation workbench: a loopback port, one compiled page
(`authorlm/audiobook_dist/index.html`, built from `web/audiobook/`), and
`{method, params}` posted to `/api/audiobook`, which is `dispatch`
below. The phone reaches it over Tailscale (`tailscale serve`), which
nothing here knows about.

**One worker.** Every act that writes — refresh (export + previews),
preview, generate, retake, stitch — is a job on one queue drained by one
thread, in order. The Doc is never consulted here: reconciliation stays
in chat (author's ruling 2026-09-26) and lands on disk, where the stale
banner sees it like any local edit.
Two taps on the same paragraph become two jobs, and the second finds
the take present and does nothing (generation.py's guard 2). The page
polls `status` and paints progress. Nothing here spends until a
generate or retake job runs, and each one is the author's tap.
"""

from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path
from typing import Any, Callable

from . import api
from . import audio
from . import generation as gen

# A test injects a stub ElevenLabs client and a stub preview renderer.
CLIENT_FACTORY: Callable[[], audio.ElevenLabs] | None = None
PREVIEW_RENDERER: Callable[[Path, dict, str, int], Path] | None = None

METHODS = ("status", "refresh", "preview", "generate", "retake", "stitch", "jobs")
_SESSIONS: dict[tuple, "Session"] = {}
RECENT_JOBS = 20


def app_html() -> str:
    path = Path(__file__).parent / "audiobook_dist" / "index.html"
    if not path.exists():
        raise RuntimeError("the audiobook page has not been built — "
                           "cd web/audiobook && npm install && npm run build")
    return path.read_text(encoding="utf-8")


class Session:
    """The open manuscript, its job queue, and the worker that drains it."""

    def __init__(self, workspace: str | None, manuscript: str | None,
                 db=None, inline: bool = False) -> None:
        self.workspace = workspace
        self.db = db or api.open_db(workspace)
        self.manuscript = api.get_manuscript(self.db, manuscript)
        self.inline = inline          # tests: run each job in the caller
        self._client: audio.ElevenLabs | None = None
        self._lock = threading.Lock()
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._jobs: list[dict] = []   # every job, newest last
        self._next_id = 1
        self.running: dict | None = None
        # The page never touches the Doc (author's ruling 2026-09-26:
        # reconciliation stays in chat); a pull lands on disk and the
        # stale banner picks it up like any local edit.
        self.doc: dict = {"state": "reconciled in chat", "detail": "", "at": None}
        self.account: dict | None = None
        if not inline:
            threading.Thread(target=self._drain, daemon=True,
                             name="audiobook-worker").start()
            threading.Thread(target=self._watch_sources, daemon=True,
                             name="audiobook-watch").start()

    # ------------------------------------------------------- helpers

    @property
    def root(self) -> Path:
        return audio.audio_dir(self.manuscript)

    def client(self) -> audio.ElevenLabs:
        if self._client is None:
            self._client = (CLIENT_FACTORY() if CLIENT_FACTORY
                            else audio.ElevenLabs(audio.api_key()))
        return self._client

    def _read_account(self) -> None:
        try:
            sub = self.client().subscription()
            self.account = {"used": sub["used"], "limit": sub["limit"],
                            "remaining": max(sub["limit"] - sub["used"], 0),
                            "at": gen.now_stamp()}
        except (audio.AudioError, OSError):
            self.account = None

    # ---------------------------------------------------------- reads

    def status(self, stem: str | None = None) -> dict:
        """Everything the page paints: the book's chapter list with
        counts, one chapter in full when `stem` is given, the Doc state
        from the last refresh, the jobs, the account."""
        root = self.root
        book = gen.load_book(root)
        chapters: list[dict] = []
        for st, _rel in gen.chapter_files(book):
            c = gen.chapter_status(root, book, gen.load_chapter(root, book, st))
            chapters.append({k: c[k] for k in ("stem", "title", "total", "generated",
                                               "previewed", "remaining",
                                               "remainingCharacters", "stitched",
                                               "stitchStale")})
        out: dict[str, Any] = {
            "book": {"title": book.get("title", ""), "quality": book["quality"],
                     "exportedAt": book.get("generatedAt"), "chapters": chapters,
                     "manuscript": self.manuscript["name"]},
            "chapter": None, "doc": self.doc, "account": self.account,
            "export": gen.export_staleness(self.manuscript),
            "jobs": self.jobs(),
        }
        if stem:
            chapter = gen.load_chapter(root, book, stem)
            full = gen.chapter_status(root, book, chapter)
            texts = {s["id"]: s for s in gen.speech_of(chapter)}
            for sec in full["sections"]:
                sp = texts[sec["id"]]
                sec.update({
                    "text": sp["text"], "voiceName": sp.get("voiceName", ""),
                    "level": sp.get("level"),
                    "pronunciations": sp.get("pronunciations", []),
                    "previewUrl": f"/preview/{sec['id']}.mp3" if sec["preview"] else None,
                    "takeUrl": f"/take/{stem}/{sec['id']}.mp3" if sec["take"] else None,
                })
            full["stitchedUrl"] = f"/stitched/{stem}.mp3" if full["stitched"] else None
            full["inRetailSample"] = [sid for sid in book.get("retailSample", [])
                                      if sid in texts]
            out["chapter"] = full
        return out

    def jobs(self) -> dict:
        with self._lock:
            recent = [dict(j) for j in self._jobs[-RECENT_JOBS:]]
        return {"running": next((j for j in recent if j["state"] == "running"), None),
                "queued": [j for j in recent if j["state"] == "queued"],
                "recent": [j for j in recent if j["state"] in ("done", "failed")][-8:]}

    # ---------------------------------------------------------- jobs

    def enqueue(self, kind: str, **params: Any) -> dict:
        """Queue a job and return it as the page will see it, with the
        cost it would spend (a plan, never a render) already on it."""
        root = self.root
        book = gen.load_book(root)
        job: dict[str, Any] = {"kind": kind, "state": "queued", "message": "",
                               "at": gen.now_stamp(), "characters": 0,
                               "stem": params.get("stem"), "done": 0, "of": 0}
        if kind in ("generate", "retake"):
            chapter = gen.load_chapter(root, book, params["stem"])
            spec = ",".join(params.get("ids") or []) or None
            plan = gen.plan(root, book, chapter, spec,
                            bool(params.get("remaining")), kind == "retake")
            if not plan["ids"]:
                raise ValueError("nothing to render: every named paragraph "
                                 "already has a take" if plan["skipped"]
                                 else "nothing to render")
            job.update(characters=plan["characters"], ids=plan["ids"],
                       of=len(plan["ids"]),
                       ordinals=[gen.ordinal_of(chapter, i) for i in plan["ids"]])
        elif kind == "stitch":
            gen.load_chapter(root, book, params["stem"])
        elif kind in ("refresh", "preview"):
            if params.get("stem"):
                gen.load_chapter(root, book, params["stem"])
        else:
            raise ValueError(f"unknown job kind {kind!r}")
        with self._lock:
            job["id"] = self._next_id
            self._next_id += 1
            self._jobs.append(job)
        if self.inline:
            self._run(job)
        else:
            self._queue.put(job)
        return dict(job)

    def _drain(self) -> None:
        while True:
            job = self._queue.get()
            self._run(job)

    AUTO_REFRESH_SECS = 15

    def wants_refresh(self) -> bool:
        """The free steps run on their own (ruling 11; the author, seeing
        the banner ask for a tap: "why not just pull the data"): true when
        a source is newer than the export and no refresh is queued or
        running."""
        try:
            stale = gen.export_staleness(self.manuscript)
        except (audio.AudioError, OSError):
            return False
        if not stale["stale"] or stale.get("reason") == "never exported":
            return False
        with self._lock:
            return not any(j["kind"] == "refresh" and j["state"] in ("queued", "running")
                           for j in self._jobs)

    def _watch_sources(self) -> None:
        import time
        while True:
            time.sleep(self.AUTO_REFRESH_SECS)
            try:
                if self.wants_refresh():
                    self.enqueue("refresh")
            except Exception:  # noqa: BLE001 — the watcher never dies
                traceback.print_exc()

    def _set(self, job: dict, **fields: Any) -> None:
        with self._lock:
            job.update(fields)

    def _run(self, job: dict) -> None:
        self._set(job, state="running", startedAt=gen.now_stamp())
        self.running = job
        try:
            result = getattr(self, f"_job_{job['kind']}")(job)
            self._set(job, state="done", message=result, finishedAt=gen.now_stamp())
        except Exception as err:  # noqa: BLE001 — a job reports, the worker lives
            self._set(job, state="failed", message=f"{type(err).__name__}: {err}",
                      finishedAt=gen.now_stamp())
            traceback.print_exc()
        finally:
            self.running = None

    # The jobs themselves. Each returns the one line the page shows.

    def _job_refresh(self, job: dict) -> str:
        """Export, then preview whatever is new — the free steps. The Doc
        is not consulted here (ruling 2026-09-26): reconcile in chat
        writes the pulled tab to disk, and the stale banner sees it."""
        parts: list[str] = []
        self._set(job, of=2, done=0, message="exporting")
        report = audio.export(self.db, self.manuscript)
        parts.append(f"export: {len(report['written'])} file(s) rewritten")
        if report["warnings"]:
            parts.append(f"{len(report['warnings'])} warning(s)")
        self._set(job, done=1, message="previewing new paragraphs")
        pv = gen.preview(self.manuscript, job.get("stem"),
                         renderer=PREVIEW_RENDERER,
                         progress=lambda m: self._set(job, message=m))
        parts.append(f"{len(pv['rendered'])} preview(s) rendered")
        if pv["errors"]:
            parts.append(f"{len(pv['errors'])} preview error(s): {pv['errors'][0]}")
        self._set(job, done=2)
        if self.account is None:
            self._read_account()
        return " · ".join(parts)

    def _job_preview(self, job: dict) -> str:
        pv = gen.preview(self.manuscript, job.get("stem"), renderer=PREVIEW_RENDERER,
                         force=bool(job.get("force")),
                         progress=lambda m: self._set(job, message=m))
        return (f"{len(pv['rendered'])} preview(s) rendered · {pv['kept']} kept"
                + (f" · {pv['swept']} swept" if pv["swept"] else ""))

    def _job_generate(self, job: dict, retake: bool = False) -> str:
        done = 0

        def progress(_line: str) -> None:
            nonlocal done
            done += 1
            self._set(job, done=done - 1, message=_line)
        result = gen.generate(self.manuscript, job["stem"],
                              spec=",".join(job["ids"]), retake=retake,
                              client=self.client(), progress=progress)
        self._set(job, done=len(result["rendered"]))
        if result["account"]:
            self.account = {**result["account"], "at": gen.now_stamp()}
        n = len(result["rendered"])
        skipped = len(result["skipped"])
        return (f"{n} paragraph(s) {'re-rendered' if retake else 'rendered'} · "
                f"{result['characters']:,} characters spent"
                + (f" · {skipped} already had a take" if skipped else ""))

    def _job_retake(self, job: dict) -> str:
        return self._job_generate(job, retake=True)

    def _job_stitch(self, job: dict) -> str:
        self._set(job, of=2, done=0)

        def progress(line: str) -> None:
            self._set(job, message=line, done=min(job["done"] + 1, 2))
        result = gen.stitch(self.manuscript, job["stem"], progress=progress)
        dur = result["durationSecs"]
        mins = f" · {int(dur // 60)}:{int(dur % 60):02d}" if dur else ""
        return f"stitched {result['segments']} segments → {result['rel']}{mins}"


def session(workspace: str | None, manuscript: str | None, db=None,
            inline: bool = False) -> Session:
    key = (workspace, manuscript or "*")
    if key not in _SESSIONS:
        _SESSIONS[key] = Session(workspace, manuscript, db=db, inline=inline)
    return _SESSIONS[key]


def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None, db=None,
             inline: bool = False) -> dict[str, Any]:
    """`/api/audiobook`'s transport: one JSON method into the session."""
    if method not in METHODS:
        raise ValueError(f"unknown audiobook method {method!r} (one of "
                         f"{', '.join(METHODS)})")
    s = session(workspace, params.get("manuscript"), db=db, inline=inline)
    if method == "status":
        return s.status(params.get("stem"))
    if method == "jobs":
        return s.jobs()
    if method == "refresh":
        return s.enqueue("refresh", stem=params.get("stem"))
    if method == "preview":
        return s.enqueue("preview", stem=params.get("stem"),
                         force=bool(params.get("force")))
    if method == "generate":
        return s.enqueue("generate", stem=params["stem"],
                         ids=params.get("ids"), remaining=bool(params.get("remaining")))
    if method == "retake":
        return s.enqueue("retake", stem=params["stem"], ids=params.get("ids") or [])
    return s.enqueue("stitch", stem=params["stem"])


def audio_file_for(s: Session, path: str) -> Path | None:
    """Map a GET path to a file under `_audio/`, or None. `/preview/<id>.mp3`,
    `/take/<stem>/<id>.mp3` (at the book's quality), `/stitched/<stem>.mp3`."""
    root = s.root
    parts = path.strip("/").split("/")
    try:
        if parts[0] == "preview" and len(parts) == 2 and parts[1].endswith(".mp3"):
            sid = parts[1][:-4]
            if not _is_id(sid):
                return None
            return gen.preview_path(root, sid)
        book = gen.load_book(root)
        if parts[0] == "take" and len(parts) == 3 and parts[2].endswith(".mp3"):
            stem, sid = parts[1], parts[2][:-4]
            if not _is_id(sid):
                return None
            rel = gen.take_of(root, gen.load_state(root, stem), sid, book["quality"])
            return (root / rel) if rel else None
        if parts[0] == "stitched" and len(parts) == 2 and parts[1].endswith(".mp3"):
            stem = parts[1][:-4]
            rel = gen.load_state(root, stem)["stitched"].get(book["quality"])
            return (root / rel) if rel and (root / rel).exists() else None
    except (audio.AudioError, OSError, KeyError):
        return None
    return None


def _is_id(s: str) -> bool:
    return len(s) == 32 and all(c in "0123456789abcdef" for c in s)


__all__ = ["Session", "session", "dispatch", "app_html", "audio_file_for",
           "METHODS", "CLIENT_FACTORY", "PREVIEW_RENDERER"]
