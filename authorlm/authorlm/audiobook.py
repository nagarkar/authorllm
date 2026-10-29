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
polls `status` and paints progress. A card's text can be edited on the
page (`source` / `edit`, 2026-10-26): the markdown block is rewritten in
the manuscript file and a refresh follows, so the paragraph returns
under a new id with nothing rendered — stale by construction. Nothing here spends until a
generate or retake job runs, and each one is the author's tap.
"""

from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from . import api
from . import audio
from . import generation as gen

# A test injects a stub ElevenLabs client and a stub preview renderer.
CLIENT_FACTORY: Callable[[], audio.ElevenLabs] | None = None
PREVIEW_RENDERER: Callable[[Path, dict, str, int], Path] | None = None

METHODS = ("manuscripts", "status", "refresh", "preview", "generate", "retake",
           "stitch", "jobs", "source", "edit", "push", "pull")
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
        # The last Push or Pull from this page, said in words ("" until
        # one runs); `at` is the tab's pushed_at stamp, read per poll.
        self.doc: dict = {"state": "", "detail": "", "at": None}
        # The Push button (author's ask 2026-10-26): the open chapter's
        # files go to their tabs of the master Doc, and the line under the
        # dropdown reads "pushed <when>" from the stamp push_doc leaves.
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
        # Every audio url names its manuscript, so one page can hold any
        # of them (the server falls back to its -m only when none is named).
        m = f"?m={quote(self.manuscript['name'])}"
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
            "chapter": None, "doc": self._doc_state(stem), "account": self.account,
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
                    # Every paragraph can be heard: a missing preview
                    # renders when it is asked for (gen.ensure_preview).
                    "previewUrl": f"/preview/{stem}/{sec['id']}.mp3{m}",
                    "takeUrl": f"/take/{stem}/{sec['id']}.mp3{m}" if sec["take"] else None,
                })
            full["stitchedUrl"] = f"/stitched/{stem}.mp3{m}" if full["stitched"] else None
            full["inRetailSample"] = [sid for sid in book.get("retailSample", [])
                                      if sid in texts]
            out["chapter"] = full
        return out

    # ------------------------------------------------- editing a card

    def _chapter_files(self, stem: str) -> tuple[dict, list[str]]:
        chapter = gen.load_chapter(self.root, gen.load_book(self.root), stem)
        return chapter, [chapter["file"], *chapter.get("continuations", [])]

    def source(self, stem: str, section_id: str) -> dict:
        """The raw markdown behind one card, for the edit box: `{file,
        line, markdown}`. Refuses by name when no block of the chapter's
        files says the section whole (then the file is the place to edit)."""
        chapter, names = self._chapter_files(stem)
        speech = next((s for s in gen.speech_of(chapter) if s["id"] == section_id), None)
        if speech is None:
            raise LookupError(f"no paragraph {section_id} in {stem}")
        root = Path(self.manuscript["path"])
        found = audio.locate_block(root, names, audio.load_config(root),
                                   speech["text"], speech.get("kind", "paragraph"))
        if found is None:
            raise LookupError(f"¶ {gen.ordinal_of(chapter, section_id)} of {stem} is "
                              "not one block of the file (a tag the audio strips, "
                              "or a list read item by item) — edit it in the file")
        return {k: found[k] for k in ("file", "line", "markdown")}

    def edit(self, stem: str, section_id: str, markdown: str) -> dict:
        """Write the edited markdown over the paragraph in its file, then
        refresh. The id hashes the text, so the edited paragraph comes back
        under a new id with no preview and no take: its preview renders
        on the next play, and its take waits for a Generate tap. The
        paragraph is relocated now, not when the box opened: a save after
        the file moved underneath is refused, never written blind."""
        chapter, names = self._chapter_files(stem)
        speech = next((s for s in gen.speech_of(chapter) if s["id"] == section_id), None)
        if speech is None:
            raise LookupError(f"no paragraph {section_id} in {stem}")
        root = Path(self.manuscript["path"])
        found = audio.locate_block(root, names, audio.load_config(root),
                                   speech["text"], speech.get("kind", "paragraph"))
        if found is None:
            raise LookupError(f"¶ {gen.ordinal_of(chapter, section_id)} of {stem} "
                              "has changed on disk — reload and edit again")
        if markdown.replace("\r\n", "\n").strip("\n") == found["markdown"]:
            return {"file": found["file"], "line": found["line"], "changed": False,
                    "job": None}
        audio.replace_block(root, found, markdown)
        return {"file": found["file"], "line": found["line"], "changed": True,
                "job": self.enqueue("refresh", stem=stem)}

    def _thread_db(self):
        """A connection for the calling thread: `self.db` belongs to the
        thread that opened the session, and SQLite refuses it elsewhere
        (the server answers each request on its own thread, the worker
        on another). The jobs before the Doc line never read the
        database; the Doc line and the push do."""
        return api.open_db(self.workspace)

    def _doc_state(self, stem: str | None) -> dict:
        """The Doc line: when the open chapter's files were last pushed
        (`gdocs.pushed_at`), the manuscript's newest push when no chapter
        is open, and the state the session reports."""
        from . import gdocs
        try:
            db = self._thread_db()
            if stem:
                _chapter, names = self._chapter_files(stem)
            else:
                names = list(gdocs.doc_status(db, self.manuscript))
            at = gdocs.pushed_at(db, self.manuscript, names)
        except (audio.AudioError, OSError, KeyError, LookupError):
            at = None
        return {**self.doc, "at": at}

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
        elif kind in ("stitch", "push", "pull"):
            gen.load_chapter(root, book, params["stem"])
        elif kind in ("refresh", "preview"):
            if params.get("stem"):
                gen.load_chapter(root, book, params["stem"])
            if kind == "preview":
                job["force"] = bool(params.get("force"))
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
            from .gdocs import DocChanged
            message = (str(err) if isinstance(err, DocChanged)
                       else f"{type(err).__name__}: {err}")
            self._set(job, state="failed", message=message,
                      finishedAt=gen.now_stamp())
            traceback.print_exc()
        finally:
            self.running = None

    # The jobs themselves. Each returns the one line the page shows.

    def _job_refresh(self, job: dict) -> str:
        """Export, then drop the previews nothing references — the free
        steps. Nothing is rendered here: a changed paragraph has a new id,
        and its preview renders when it is first played (the lazy listen,
        2026-10-18: "we could do it lazily if it's fast enough"). The Doc
        is not consulted (ruling 2026-09-26): reconcile in chat writes the
        pulled tab to disk, and the stale banner sees it."""
        parts: list[str] = []
        self._set(job, of=2, done=0, message="exporting")
        report = audio.export(self.db, self.manuscript)
        parts.append(f"export: {len(report['written'])} file(s) rewritten")
        if report["warnings"]:
            parts.append(f"{len(report['warnings'])} warning(s)")
        self._set(job, done=1, message="sweeping stale previews")
        root = self.root
        voice, rate = gen.preview_settings(self.manuscript)
        cleared = gen.check_preview_voice(root, voice, rate)
        swept = gen.sweep_previews(root, gen.load_book(root))
        if cleared:
            parts.append(f"{cleared} preview(s) cleared for the new voice {voice}")
        if swept:
            parts.append(f"{swept} stale preview(s) swept")
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


    def _job_push(self, job: dict) -> str:
        """`authorlm doc push <chapter file>` from the page: each file of
        the chapter to its tab, never opening a consent browser ('doc
        auth' is the one doorway). The stamp push_doc leaves is what the
        "pushed <when>" line shows next."""
        from . import gdocs

        _chapter, names = self._chapter_files(job["stem"])
        _config, service, docs_service = self._doc_services()
        self._set(job, of=len(names), done=0)
        db = self._thread_db()
        urls: list[str] = []
        held = 0
        sent: list[str] = []
        for i, name in enumerate(names):
            if gdocs.unchanged_since_push(db, self.manuscript, name):
                self._set(job, done=i + 1)
                continue
            self._set(job, message=f"pushing {name}")
            sent.append(name)
            # Guarded: a tab edited since the last push is never
            # overwritten from the page; the job fails with "did not
            # push; doc has changed. pull first" and Pull is beside it.
            result = gdocs.push_doc(db, self.manuscript, name,
                                    service=service, docs_service=docs_service,
                                    guard=True)
            urls.append(result["url"])
            held += len(result.get("held") or [])
            self._set(job, done=i + 1)
        if not sent:
            return "nothing to push: the Doc has every file as it is"
        self.doc = {**self.doc, "state": "pushed from the page",
                    "detail": urls[-1] if urls else ""}
        return (f"pushed {', '.join(sent)} to the Doc"
                + (f" · {held} paragraph(s) held under open comments" if held else ""))

    def _doc_services(self) -> tuple[dict, Any, Any]:
        """The config and the two Google services, never opening a
        consent browser ('doc auth' in the terminal is the one doorway)."""
        from . import gdocs, paths
        import tomllib

        paths.load_env()
        config: dict = {}
        toml_path = paths.config_path()
        if toml_path.exists():
            config = tomllib.loads(toml_path.read_text())
        return (config,
                gdocs.get_service(config, self.workspace, interactive=False),
                gdocs.get_docs_service(config, self.workspace, interactive=False))

    def _job_pull(self, job: dict) -> str:
        """`authorlm doc pull <chapter file>` from the page (2026-10-26):
        the chapter's tabs come down to disk, are collected, and the
        export is refreshed so the cards show the Doc's text. A file
        changed on both sides is left alone and named, as the CLI does."""
        from . import api, gdocs

        _chapter, names = self._chapter_files(job["stem"])
        config, service, docs_service = self._doc_services()
        self._set(job, of=len(names) + 1, done=0)
        db = self._thread_db()
        pulled: list[str] = []
        conflicts: list[str] = []
        ahead: list[str] = []
        missing: list[str] = []
        for i, name in enumerate(names):
            self._set(job, message=f"pulling {name}")
            report = gdocs.pull_doc(db, self.manuscript, name, service=service,
                                    docs_service=docs_service)
            pulled += [r for r in report.get("changed", []) if r not in pulled]
            conflicts += report.get("conflicts", [])
            ahead += report.get("local_ahead", [])
            missing += report.get("missing", [])
            self._set(job, done=i + 1)
        if missing:
            # Never "the Doc matches the files" when a tab was not read
            # (2026-10-26: Google's export dropped the tab titles and the
            # page said all was well while nothing came down).
            raise LookupError(f"nothing pulled for {', '.join(missing)}: its "
                              "tab could not be found in the Doc's export")
        if pulled:
            api.collect(db, self.manuscript, config, source="audiobook-pull")
        self._set(job, message="exporting")
        audio.export(db, self.manuscript)
        self._set(job, done=len(names) + 1)
        self.doc = {**self.doc, "state": "pulled from the page", "detail": ""}
        parts = [f"pulled {', '.join(pulled)}" if pulled
                 else "the Doc matches the files" if not ahead
                 else "nothing new in the Doc"]
        if ahead:
            parts.append(f"the file is ahead of the Doc, kept: {', '.join(ahead)} "
                         "(Push sends it)")
        if conflicts:
            parts.append(f"changed on both sides, left alone: {', '.join(conflicts)}")
        return " · ".join(parts)


def session(workspace: str | None, manuscript: str | None, db=None,
            inline: bool = False) -> Session:
    key = (workspace, manuscript or "*")
    if key not in _SESSIONS and manuscript:
        # The server's default session is keyed "*" when -m was left out;
        # the page then names that same manuscript, and it must find the
        # same session — one worker per manuscript, never two.
        for (w, _), s in _SESSIONS.items():
            if w == workspace and s.manuscript["name"] == manuscript:
                return s
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
    if method == "manuscripts":
        # A fresh connection: the session's belongs to the thread that
        # opened it, and the server answers each request on its own thread.
        return {"current": s.manuscript["name"],
                "manuscripts": manuscripts(db or api.open_db(workspace))}
    if method == "status":
        return s.status(params.get("stem"))
    if method == "jobs":
        return s.jobs()
    if method == "source":
        return s.source(params["stem"], params["id"])
    if method == "edit":
        return s.edit(params["stem"], params["id"], str(params.get("markdown") or ""))
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
    if method == "push":
        return s.enqueue("push", stem=params["stem"])
    if method == "pull":
        return s.enqueue("pull", stem=params["stem"])
    return s.enqueue("stitch", stem=params["stem"])


def manuscripts(db) -> list[str]:
    """The manuscripts the page's dropdown offers: those with an audio
    export (`audio export` writes it; one without has nothing to play)."""
    out = []
    for m in api.list_manuscripts(db):
        try:
            if (audio.audio_dir(m) / audio.BOOK_FILENAME).exists():
                out.append(m["name"])
        except (audio.AudioError, OSError, KeyError, TypeError):
            continue
    return out


def audio_file_for(s: Session, path: str) -> Path | None:
    """Map a GET path to a file under `_audio/`, or None.
    `/preview/<stem>/<id>.mp3` (rendered now if missing),
    `/take/<stem>/<id>.mp3` (at the book's quality), `/stitched/<stem>.mp3`."""
    root = s.root
    parts = path.strip("/").split("/")
    try:
        if parts[0] == "preview" and len(parts) == 3 and parts[2].endswith(".mp3"):
            stem, sid = parts[1], parts[2][:-4]
            if not _is_id(sid):
                return None
            return gen.ensure_preview(s.manuscript, stem, sid,
                                      renderer=PREVIEW_RENDERER)
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


__all__ = ["Session", "session", "dispatch", "app_html", "audio_file_for", "manuscripts",
           "METHODS", "CLIENT_FACTORY", "PREVIEW_RENDERER"]
