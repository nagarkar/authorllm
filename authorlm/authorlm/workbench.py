"""The pronunciation workbench — a local page for settling how the book's
hard words are said (audiobook-pipeline-design §11).

Served by `authorlm workbench` (workbench_server.py), like the Triage
App, and for the same reason its logic lives here: its output is a
manuscript file. Pick a word (every row of `pronunciations.md`, or a new one), see every voice
that has to say it and the sentence it says it in, type a respelling,
regenerate, listen, and Save — which writes the rows into
`pronunciations.md` on the author's word. `collect` sees the change, the
Doc mirror carries it, and Push sends the table to ElevenLabs.
audiostation never touches the table; it only reads the locator the
export writes.

The compiled page is `authorlm/workbench_dist/index.html` (built from
`web/workbench/`); the page posts `{method, params}` to `/api/workbench`,
which is `dispatch` below. Nothing spends credits until a Regenerate is
pressed, and each press is one sentence per voice. (An MCP App road
existed for one day, 2026-09-04, and was removed: it never rendered in
the client the author uses.)
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from . import api
from . import audio
from . import pronunciations as pron

# A test injects a stub ElevenLabs client here.
CLIENT_FACTORY: Callable[[], audio.ElevenLabs] | None = None

_SUGGEST_SYSTEM = (
    "You write pronunciation respellings for an audiobook narrator. Reply "
    "with ONE respelling in plain English syllables, hyphen-separated, the "
    "stressed syllable in capitals (e.g. buh-SIL-ih-deez), nothing else — "
    "no IPA, no quotes, no explanation.")

METHODS = ("state", "contexts", "render", "clip", "save", "remove", "clear", "push",
           "export", "suggest")
RENDER_MODES = ("respelling", "default", "dictionary")


def app_html() -> str:
    path = Path(__file__).parent / "workbench_dist" / "index.html"
    if not path.exists():
        raise RuntimeError("the pronunciation workbench frontend has not been "
                           "built — cd web/workbench && npm install && npm run build")
    return path.read_text(encoding="utf-8")


class Session:
    """The open manuscript and its lazily-made clients."""

    def __init__(self, workspace: str | None, manuscript: str | None,
                 db=None) -> None:
        self.workspace = workspace
        self.db = db or api.open_db(workspace)
        self.manuscript = api.get_manuscript(self.db, manuscript)
        self._client: audio.ElevenLabs | None = None
        self._built: dict | None = None

    @property
    def root(self) -> Path:
        return Path(self.manuscript["path"])

    # Every clip the workbench rendered, kept, keyed by what produced it
    # (author's ruling 2026-09-04): the term, the voice and its settings,
    # the mode, the alias as read, and the sentence. The page shows, for
    # each voice, the clip whose key matches what is in the boxes RIGHT
    # NOW — change the respelling back to one you tried an hour ago and
    # its clip comes back; nothing is re-rendered. The mp3s live under
    # _audio/auditions/say/; this index is {key: record}.
    @property
    def clips_index(self) -> Path:
        return self.root / audio.AUDIO_DIR / audio.AUDITIONS_DIR / "say" / \
            "workbench-clips.json"

    def _clips(self) -> dict:
        if self.clips_index.exists():
            try:
                index = json.loads(self.clips_index.read_text(encoding="utf-8"))
            except ValueError:
                return {}
            # Only keyed records; the first-day nested shape is migrated
            # in `_records_for`, where the voices are at hand.
            return {k: v for k, v in index.items()
                    if isinstance(v, dict) and v.get("key") == k}
        return {}

    def _write_clips(self, index: dict) -> None:
        self.clips_index.parent.mkdir(parents=True, exist_ok=True)
        self.clips_index.write_text(json.dumps(index, indent=1, ensure_ascii=False),
                                    encoding="utf-8")

    def _remember(self, term: str, clip: dict) -> dict:
        index = self._clips()
        record = {k: clip[k] for k in ("key", "name", "path", "spoken", "say",
                                       "cast", "voice_name", "file", "chars",
                                       "mode", "sentence", "dictionary_version")}
        record["term"] = pron.key(term)
        record["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        index[record["key"]] = record          # a re-take replaces the take
        self._write_clips(index)
        return record

    def _migrate_legacy(self, term: str, ctxs: list[dict]) -> None:
        """The first-day index was {term: {cast: record}} without keys.
        Give each such record its key from the voice that cast has now,
        so the clips made that morning are not lost."""
        if not self.clips_index.exists():
            return
        try:
            raw = json.loads(self.clips_index.read_text(encoding="utf-8"))
        except ValueError:
            return
        legacy = {t: v for t, v in raw.items()
                  if isinstance(v, dict) and v.get("key") != t}
        if not legacy:
            return
        voices = {c["cast"]: c["voice"] for c in ctxs}
        index = {k: v for k, v in raw.items() if k not in legacy}
        for term_key, by_cast in legacy.items():
            for cast, rec in by_cast.items():
                if term_key != pron.key(term) or cast not in voices:
                    index.setdefault("_legacy", {}).setdefault(term_key, {})[cast] = rec
                    continue
                key = clip_key(term, cast, voices[cast], rec.get("mode", "respelling"),
                               rec.get("say", ""), rec.get("sentence", rec.get("spoken", "")))
                index[key] = {**rec, "key": key, "term": term_key,
                              "dictionary_version": rec.get("dictionary_version")}
        stray = index.pop("_legacy", None)
        if stray:
            # Another term's legacy rows wait for their own contexts.
            index.update(stray)
        self._write_clips(index)

    def _records_for(self, term: str, ctxs: list[dict]) -> list[dict]:
        self._migrate_legacy(term, ctxs)
        want = pron.key(term)
        out = []
        for record in self._clips().values():
            if record.get("term") == want and Path(record.get("path") or "").exists():
                out.append(record)
        out.sort(key=lambda r: r.get("at") or "", reverse=True)
        return out

    def clip(self, key: str) -> dict:
        record = self._clips().get(key or "")
        if not record or not Path(record["path"]).exists():
            raise LookupError(f"no clip with key {key!r} on disk")
        return {**record, "audio_base64": base64.b64encode(
            Path(record["path"]).read_bytes()).decode("ascii")}

    def client(self) -> audio.ElevenLabs:
        if self._client is None:
            self._client = (CLIENT_FACTORY() if CLIENT_FACTORY
                            else audio.ElevenLabs(audio.api_key()))
        return self._client

    def built(self) -> dict:
        if self._built is None:
            self._built = audio.build(self.manuscript)
        return self._built

    # ---------------------------------------------------------- reads

    def state(self) -> dict:
        path = self.root / pron.FILENAME
        rows, warnings = pron.parse(path.read_text(encoding="utf-8")
                                    if path.exists() else "")
        config = audio.load_config(self.root)
        book = self.built()["book"]
        # A row is "tested" when some clip was rendered with its CURRENT
        # reading (any voice, any sentence); a reading changed since the
        # last clip is untested again. The dropdown says so.
        # Both sides folded: a clip records the term as typed, the row
        # is looked up by key (2026-09-26: "Brahman" never matched
        # "brahman", so nothing ever read as tested).
        tried = {(pron.key(r.get("term") or ""), " ".join((r.get("say") or "").split()))
                 for r in self._clips().values() if r.get("mode") == "respelling"}
        for row in rows:
            row["tested"] = bool(row["say"]) and \
                (pron.key(row["term"]), " ".join(row["say"].split())) in tried
        # The locator the LAST EXPORT wrote — what audiostation is using —
        # comes from audiobook.json on disk; the in-memory build carries
        # none (it never looks the dictionary up).
        locator = None
        exported = self.root / audio.AUDIO_DIR / audio.BOOK_FILENAME
        if exported.exists():
            try:
                locator = json.loads(exported.read_text(encoding="utf-8")) \
                    .get("pronunciationDictionary")
            except ValueError:
                locator = None
        return {
            "manuscript": self.manuscript["name"],
            "terms": rows,
            "warnings": warnings,
            "dictionary": config["tts"]["dictionary"],
            "model": config["tts"]["model"],
            "phoneme_model": config["tts"]["model"] in audio.PHONEME_MODELS,
            "locator": locator,
            "cast": {k: v["voiceName"] or v["voiceId"]
                     for k, v in book["cast"].items()},
        }

    def contexts(self, term: str) -> dict:
        term = (term or "").strip()
        if not term:
            raise ValueError("a term is needed")
        ctxs = audio.say_contexts(self.manuscript, term, limit=None,
                                  per_cast=True, built=self.built())
        return {"term": term, "say": audio.table_say(self.manuscript, term),
                "contexts": ctxs, "clips": self._records_for(term, ctxs)}

    # --------------------------------------------------------- writes

    def render(self, term: str, say: str, ctx: dict, mode: str) -> dict:
        """One clip in one of three modes: `respelling` substitutes the
        typed respelling inline; `dictionary` does the same AND attaches
        the pushed dictionary so the neighbours read by their rules (an
        empty box hears the word by its pushed rule); `default` is the voice's own reading
        with no rule at all (what the book sounds like after Remove);
        `dictionary` renders the untouched sentence against the pushed
        dictionary."""
        term = (term or "").strip()
        if not term:
            raise ValueError("a term is needed")
        if not ctx or not ctx.get("voice") or not ctx.get("sentence"):
            raise ValueError("a context (voice and sentence) is needed")
        if mode not in RENDER_MODES:
            raise ValueError(f"unknown render mode {mode!r} (one of "
                             f"{', '.join(RENDER_MODES)})")
        say = (say or "").strip()
        if mode == "respelling" and not say:
            raise ValueError("type a respelling first, or pick 'the voice's "
                             "default' to hear it with no rule")
        # The sentence is editable in the page (a long one costs more and
        # says no more), but it must still carry the word.
        ctx = dict(ctx)
        ctx["sentence"] = " ".join((ctx.get("sentence") or "").split())
        if not audio._term_pattern(term).search(ctx["sentence"]):
            raise ValueError(f"the sentence no longer contains '{term}' — "
                             "keep the word in it")
        client = self.client()
        use_dictionary = mode == "dictionary"
        locators = audio.dictionary_locators(self.manuscript, client) \
            if use_dictionary else None
        key = clip_key(term, ctx["cast"], ctx["voice"], mode, say, ctx["sentence"])
        clip = audio.render_context(self.manuscript, client, ctx, term,
                                    say if mode in ("respelling", "dictionary") else "",
                                    use_dictionary, locators=locators,
                                    tag=f"wb-{key}")
        clip["key"] = key
        clip["mode"] = mode
        clip["say"] = audio.alias_for(say) if mode in ("respelling", "dictionary") else ""
        clip["sentence"] = ctx["sentence"]
        clip["alias"] = clip["say"]
        clip["dictionary_version"] = locators[0]["version_id"] if locators else None
        record = self._remember(term, clip)
        return {**clip, "at": record["at"], "term": record["term"],
                "audio_base64": base64.b64encode(
                    Path(clip["path"]).read_bytes()).decode("ascii")}

    def remove(self, term: str) -> dict:
        """The author's verdict that the default reading is better: drop
        the row, and the one matching remote rule if the dictionary is
        on ElevenLabs (free — no speech is rendered). Export afterwards
        so the new dictionary version reaches audiostation."""
        term = (term or "").strip()
        if not term:
            raise ValueError("a term is needed")
        if not audio.table_say(self.manuscript, term) and \
                pron.key(term) not in pron.lookup(pron.parse(
                    (self.root / pron.FILENAME).read_text(encoding="utf-8")
                    if (self.root / pron.FILENAME).exists() else "")[0]):
            raise LookupError(f"'{term}' is not a row in {pron.FILENAME}")
        client = None
        config = audio.load_config(self.root)
        if (config["tts"]["dictionary"] or "").strip():
            client = self.client()
        result = audio.remove_say(self.manuscript, term, client)
        self._built = None
        return result

    def clear(self, term: str) -> dict:
        """Keep the word, drop its reading (and its remote rule now)."""
        term = (term or "").strip()
        if not term:
            raise ValueError("a term is needed")
        client = None
        config = audio.load_config(self.root)
        if (config["tts"]["dictionary"] or "").strip():
            client = self.client()
        result = audio.clear_say(self.manuscript, term, client)
        self._built = None
        return result

    def save(self, rows: list[dict]) -> dict:
        """Write the rows. A NEW term may land with no reading yet (the
        table's '(not settled yet)' state — the word is on the list); an
        existing row is never blanked by a save, Remove is the verb for
        that."""
        existing = pron.lookup(pron.parse(
            (self.root / pron.FILENAME).read_text(encoding="utf-8")
            if (self.root / pron.FILENAME).exists() else "")[0])
        saved, skipped = [], []
        for row in rows or []:
            term = (row.get("term") or "").strip()
            say = (row.get("say") or "").strip()
            if not term:
                continue
            if not say and pron.key(term) in existing:
                skipped.append(term)
                continue
            saved.append(audio.settle_say(self.manuscript, term, say,
                                          row.get("note")))
        self._built = None
        return {"saved": saved, "count": len(saved), "skipped": skipped}

    def push(self) -> dict:
        client = self.client()
        plan = audio.dictionary_plan(self.manuscript, client)
        lines = audio.describe_plan(plan)
        if not (plan["create"] or plan["add"] or plan["change"] or plan["remove"]):
            return {"plan": lines, "applied": False, "nothing_to_push": True}
        result = audio.dictionary_apply(plan, client)
        self._built = None
        return {"plan": lines, "applied": True, **result}

    def export(self) -> dict:
        result = audio.export(self.db, self.manuscript)
        self._built = None
        return result

    def suggest(self, term: str, sentence: str = "") -> dict:
        llm = api.make_llm(self.workspace)
        reply = llm.complete(
            _SUGGEST_SYSTEM,
            f"Term: {term}\n" + (f"Sentence: {sentence}\n" if sentence else "")
            + "Respelling:")
        if not reply:
            return {"say": None,
                    "reason": "the LLM is disabled here — ask in chat for a "
                              "respelling, then type it in"}
        say = reply.strip().splitlines()[0].strip().strip("\"'`.")
        return {"say": say if say and "|" not in say and len(say) < 80 else None,
                "reason": None if say else "no usable reply"}


def clip_key(term: str, cast: str, voice: dict, mode: str, say: str,
             sentence: str) -> str:
    """What a clip is a clip OF. Same inputs, same key, same file; a
    different alias, sentence, voice setting or mode is a different clip.
    The dictionary's version is deliberately NOT in it — the page cannot
    know it — and is stored on the record for the eye instead."""
    alias = audio.alias_for(say) if mode in ("respelling", "dictionary") else ""
    parts = [pron.key(term), cast, str(voice.get("voice_id", "")),
             str(voice.get("model", "")),
             f"{float(voice.get('stability', 0)):.3f}",
             f"{float(voice.get('similarity', 0)):.3f}",
             f"{float(voice.get('speed', 1)):.3f}",
             mode, alias, " ".join((sentence or "").split())]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


# One session per (workspace, manuscript) for the life of the local
# server: the build is cached between requests and dropped on writes.
_SESSIONS: dict[tuple[str | None, str], Session] = {}


def session(workspace: str | None, manuscript: str | None, db=None) -> Session:
    key = (workspace, manuscript or "*")
    if key not in _SESSIONS:
        _SESSIONS[key] = Session(workspace, manuscript, db=db)
    return _SESSIONS[key]


def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None, db=None) -> dict[str, Any]:
    """`/api/workbench`'s transport: one JSON method into the session."""
    if method not in METHODS:
        raise ValueError(f"unknown workbench method {method!r} (one of "
                         f"{', '.join(METHODS)})")
    s = session(workspace, params.get("manuscript"), db=db)
    if method == "state":
        return s.state()
    if method == "contexts":
        return s.contexts(params.get("term", ""))
    if method == "render":
        return s.render(params.get("term", ""), params.get("say", ""),
                        params.get("context") or {},
                        params.get("mode") or "respelling")
    if method == "clip":
        return s.clip(params.get("key", ""))
    if method == "save":
        return s.save(params.get("rows") or [])
    if method == "remove":
        return s.remove(params.get("term", ""))
    if method == "clear":
        return s.clear(params.get("term", ""))
    if method == "push":
        return s.push()
    if method == "export":
        return s.export()
    return s.suggest(params.get("term", ""), params.get("sentence", ""))
