"""The catalogue of read requests: the only road from an engine to the book.

Decided 2026-11-26 (docs/plans/filter-lens-consolidation.md 11.9, U8: owner,
"Yes, one catalogue serving both"): every engine gets manuscript material
only by asking for it by name (Q20), and AuthorLM answers from what is
current (Q20b). One entry per read:

    Request(name, when, params, verb, handler)

- `name`: what the model calls (an OpenAI function name; an MCP tool name
  for claude-headless, engines/mcp_read.py).
- `when`: one line saying when to use it, shown to the model.
- `params`: JSON-schema properties, with `required` naming the mandatory.
- `verb`: the AuthorLM command line that serves it
  (`authorlm engine read <name> --arg k=v`), for the owner to run by hand.
- `handler(reader, args) -> str`: the read itself.

Adding an entry makes it available to every engine at once.

**Limits.** Only the book is readable: an essay is any file in toc.toml's
reading order (`summaries._context_units`, minus the files the toc never
lists); never drafts, exports, `_retired`, profiles' neighbours or anything
else in the folders. A request outside the book is **declined**, with a
reason the model sees (`Declined`), and logged like any other request.
Read-only: nothing here writes the manuscript, the Doc or the style laws.
The one side effect is the summary refresh Q20b asks for (a stale or
missing summary is rebuilt before it is served, on the `[critique]` /
`[summaries]` models, billed like `summarize rebuild`).

`earlier_instances` reads a function the workflow package provides
(`authorlm.workflow.earlier_instances(db, manuscript, essay) -> list|str`);
it is imported when asked for, and answers "not available yet" while that
package or function does not exist.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

DECLINED_PREFIX = "DECLINED: "


class Declined(LookupError):
    """A request the catalogue will not serve; the message is the reason
    the model sees."""


@dataclass
class Request:
    name: str
    when: str
    params: dict
    required: tuple[str, ...]
    handler: Callable[["BookReader", dict], str]

    @property
    def verb(self) -> str:
        args = " ".join(f"--arg {p}=<{p}>" for p in self.params)
        return f"authorlm engine read {self.name}" + (f" {args}" if args else "")

    def function_tool(self) -> dict:
        """The OpenAI Responses function definition (strict: every
        property listed in `required`, optional ones nullable)."""
        props = {}
        for key, spec in self.params.items():
            spec = dict(spec)
            if key not in self.required:
                spec["type"] = [spec["type"], "null"]
            props[key] = spec
        return {"type": "function", "name": self.name,
                "description": self.when,
                "parameters": {"type": "object", "properties": props,
                               "required": list(self.params),
                               "additionalProperties": False},
                "strict": True}

    def input_schema(self) -> dict:
        """The plain JSON schema (MCP tool input)."""
        return {"type": "object", "properties": dict(self.params),
                "required": list(self.required),
                "additionalProperties": False}


@dataclass
class BookReader:
    """What a request reads from: one workspace, one manuscript. Opens the
    database on first use. `config` is the project config (for the
    summary refresh); `refresh=False` serves summaries as they stand."""

    workspace: str | None
    manuscript_name: str
    config: dict = field(default_factory=dict)
    refresh: bool = True
    _db: Any = None
    _manuscript: dict | None = None

    @property
    def db(self):
        if self._db is None:
            from .. import api

            self._db = api.open_db(self.workspace)
        return self._db

    @property
    def manuscript(self) -> dict:
        if self._manuscript is None:
            row = self.db.one("SELECT * FROM manuscripts WHERE name = ?",
                              (self.manuscript_name,))
            if not row:
                raise Declined(f"no manuscript named '{self.manuscript_name}'")
            self._manuscript = dict(row)
        return self._manuscript

    def essays(self) -> dict[str, str]:
        """{file: current text} for every essay in the reading order, in
        order. Files the toc never lists are outside the book."""
        from ..summaries import _context_units

        texts, unlisted, _flight = _context_units(self.db, self.manuscript)
        return {f: t for f, t in texts.items() if f not in set(unlisted)}

    def essay(self, file: Any) -> tuple[str, str]:
        if not isinstance(file, str) or not file.strip():
            raise Declined("name the essay's file, e.g. 'good-life.md'")
        file = file.strip()
        essays = self.essays()
        if file not in essays:
            raise Declined(
                f"'{file}' is not an essay in this book's reading order "
                f"(toc.toml); only those can be read. Ask for 'essay_list' "
                f"to see them.")
        return file, essays[file]


# ------------------------------------------------------------ handlers

def _essay_list(reader: BookReader, args: dict) -> str:
    from ..summaries import sections_of

    lines = []
    for n, (file, text) in enumerate(reader.essays().items(), 1):
        secs = sections_of(text)
        title = next((s["heading"] for s in secs if s["heading"]), "")
        lines.append(f"{n}. {file}  {title.lstrip('# ').strip()}"
                     f"  ({len(text.split()):,} words, {len(secs)} sections)")
    return "\n".join(lines) or "The reading order is empty."


def _essay_text(reader: BookReader, args: dict) -> str:
    file, text = reader.essay(args.get("file"))
    return f"ESSAY {file}, as it now stands:\n\n{text}"


def _essay_section(reader: BookReader, args: dict) -> str:
    from ..summaries import sections_of

    file, text = reader.essay(args.get("file"))
    n = args.get("section")
    secs = sections_of(text)
    if not isinstance(n, int) or not 1 <= n <= len(secs):
        raise Declined(f"{file} has sections 1 to {len(secs)}; "
                       f"section {n!r} does not exist")
    sec = secs[n - 1]
    body = "\n\n".join(p["text"] for p in sec["paragraphs"])
    return (f"ESSAY {file}, section {n} of {len(secs)} "
            f"({sec['heading'] or 'preamble'}):\n\n{body}")


def _refresh_summaries(reader: BookReader, file: str, level: str) -> str | None:
    """Bring `file`'s summaries at `level` up to date (Q20b); returns a
    note when it could not, None when they are current."""
    from .. import summaries as sums

    if not reader.refresh:
        return None
    try:
        llms = sums.clients(reader.config)
        if not llms[level].enabled:
            return "the summary model is off ([llm] enabled = false); served as stored"
        if level == "essay":
            state = next((r["state"] for r in sums.status(reader.db, reader.manuscript)
                          if r["file"] == file), "missing")
            if state in ("fresh", "upstream_stale", "rewriting"):
                return None
            sums.rebuild_one(reader.db, reader.manuscript, file, llms,
                             echo=lambda *_a, **_k: None)
        else:
            sums.ensure(reader.db, reader.manuscript, llms, level=level,
                        files=[file], echo=lambda *_a, **_k: None)
    except Exception as err:  # noqa: BLE001 - served as stored, with the reason
        return f"could not refresh ({type(err).__name__}: {err}); served as stored"
    return None


def _essay_summaries(reader: BookReader, args: dict) -> str:
    from .. import summaries as sums

    file, text = reader.essay(args.get("file"))
    level = args.get("level") or "essay"
    if level not in ("essay", "section", "paragraph"):
        raise Declined("level is one of essay, section, paragraph")
    note = _refresh_summaries(reader, file, level)
    got = sums.for_unit(reader.db, reader.manuscript, file, text)
    out = [f"SUMMARIES of {file} (level: {level})"]
    if note:
        out.append(f"NOTE: {note}")
    essay = got.get("essay")
    out.append("ESSAY: " + (f"[{essay['state']}] {essay['summary']}" if essay
                            else "(none)"))
    if level in ("section", "paragraph"):
        for s in got.get("sections") or []:
            out.append(f"SECTION {s['n']} ({s['heading'] or 'preamble'}, "
                       f"paragraphs {s['paragraphs'][0]}-{s['paragraphs'][1]})"
                       f"{' [stale]' if s['stale'] else ''}: {s['summary'] or '(none)'}")
    if level == "paragraph":
        for p in got.get("paragraphs") or []:
            out.append(f"P{p['n']} (section {p['section']}, {p['kind']})"
                       f"{' [stale]' if p['stale'] else ''}: {p['gist'] or '(none)'}")
    return "\n".join(out)


def _style_rules(reader: BookReader, args: dict) -> str:
    from ..styles import effective_style

    file, _text = reader.essay(args.get("file"))
    laws = effective_style(reader.db, reader.manuscript["id"], file)
    if not laws:
        return f"No style rules bind {file}."
    lines = [f"STYLE RULES in force for {file} (active only):"]
    for law in laws:
        lines.append(f"- {law['id']} [{law['aspect']}] {law['statement']}")
    return "\n".join(lines)


def _concept_notes(reader: BookReader, args: dict) -> str:
    from .. import concepts as cg

    name = args.get("name")
    mid = reader.manuscript["id"]
    if not name:
        rows = reader.db.all(
            "SELECT name, kind FROM concept_nodes WHERE manuscript_id = ? "
            "AND status NOT IN ('rejected', 'retired') ORDER BY name", (mid,))
        return ("CONCEPTS (name, kind):\n"
                + "\n".join(f"- {r['name']} ({r['kind']})" for r in rows)) \
            if rows else "No concepts on record."
    try:
        shown = cg.show_concept(reader.db, reader.manuscript, str(name))
    except LookupError as err:
        raise Declined(str(err))
    node = shown["node"]
    if node.get("status") in ("rejected", "retired"):
        raise Declined(f"'{name}' is {node['status']}; only current concepts are served")
    lines = [f"CONCEPT {node['name']} ({node.get('kind')})",
             f"notes: {node.get('notes') or '(none)'}"]
    aliases = cg.node_aliases(node)
    if aliases:
        lines.append("aliases: " + ", ".join(aliases))
    for e in shown["edges"]:
        lines.append(f"- {e['from_name']} {e['relation']} {e['to_name']}")
    return "\n".join(lines)


def _profile(reader: BookReader, args: dict) -> str:
    from .. import api

    key = args.get("key")
    if key and ("/" in str(key) or "\\" in str(key) or str(key).startswith(".")):
        raise Declined("a profile key is a plain name, e.g. 'audience'")
    try:
        got = api.get_profile(reader.manuscript, key or None)
    except LookupError as err:
        raise Declined(str(err))
    if "profiles" in got:
        return ("PROFILES (key, words):\n"
                + "\n".join(f"- {p['key']} ({p['words']} words)" for p in got["profiles"])) \
            if got["profiles"] else "No profiles on record."
    return f"PROFILE {got['key']}:\n\n{got['content']}"


def _earlier_instances(reader: BookReader, args: dict) -> str:
    file, _text = reader.essay(args.get("file"))
    try:
        from .. import workflow  # the workflow package (built separately)

        fn = getattr(workflow, "earlier_instances")
    except (ImportError, AttributeError):
        return ("Earlier workflow instances are not available yet "
                "(the workflow package does not provide them).")
    got = fn(reader.db, reader.manuscript, file)
    if isinstance(got, str):
        return got
    return json.dumps(got, ensure_ascii=False, indent=1, default=str)


_FILE = {"file": {"type": "string",
                  "description": "the essay's file name as the reading order lists it"}}

CATALOGUE: tuple[Request, ...] = (
    Request("essay_list",
            "The book's essays in reading order (file, title, size). Use it "
            "to find an essay's file name before asking for it.",
            {}, (), _essay_list),
    Request("essay_text",
            "The whole current text of one essay in the book. Use it when a "
            "finding rests on what another essay says (check it against the "
            "text, not only its summary).",
            dict(_FILE), ("file",), _essay_text),
    Request("essay_section",
            "One section (the text under one subheading, numbered from 1) of "
            "an essay in the book.",
            {**_FILE, "section": {"type": "integer",
                                  "description": "section number, from 1"}},
            ("file", "section"), _essay_section),
    Request("essay_summaries",
            "An essay's summaries, brought up to date first: the essay summary, "
            "or with level 'section' or 'paragraph' the finer ones too. Use it "
            "to decide whether a whole essay is worth reading.",
            {**_FILE, "level": {"type": "string",
                                "enum": ["essay", "section", "paragraph"],
                                "description": "default essay"}},
            ("file",), _essay_summaries),
    Request("style_rules",
            "The style rules in force for an essay (active only), with their ids.",
            dict(_FILE), ("file",), _style_rules),
    Request("concept_notes",
            "A concept's notes, aliases and relations; without a name, the list "
            "of current concepts.",
            {"name": {"type": "string", "description": "the concept's name"}},
            (), _concept_notes),
    Request("profile",
            "A manuscript profile (declared context about the book: audience, "
            "market, scheme, ...); without a key, the list of profiles.",
            {"key": {"type": "string", "description": "profile key, e.g. 'audience'"}},
            (), _profile),
    Request("earlier_instances",
            "This essay's earlier workflow instances: their findings, the "
            "author's decisions and the discussion. Context to weigh, never "
            "binding.",
            dict(_FILE), ("file",), _earlier_instances),
)

BY_NAME = {r.name: r for r in CATALOGUE}


def listing() -> str:
    """The catalogue as lines for a prompt: name(params): when."""
    lines = []
    for r in CATALOGUE:
        params = ", ".join(p + ("" if p in r.required else "?") for p in r.params)
        lines.append(f"- {r.name}({params}): {r.when}")
    return "\n".join(lines)


def run(reader: BookReader, name: str, args: dict | None) -> tuple[str, dict]:
    """Serve one request. Returns (the text the model gets, the log entry
    {name, args, chars, declined}). Never raises for a bad request: an
    unknown name, a bad argument or anything outside the book comes back
    as `DECLINED: <reason>`, which the model sees."""
    args = {k: v for k, v in (args or {}).items() if v is not None}
    declined = None
    try:
        request = BY_NAME.get(name)
        if request is None:
            raise Declined(f"there is no read request named '{name}'; "
                           f"the catalogue has {', '.join(BY_NAME)}")
        unknown = sorted(set(args) - set(request.params))
        if unknown:
            raise Declined(f"{name} takes no {', '.join(unknown)}")
        lacking = [p for p in request.required if p not in args]
        if lacking:
            raise Declined(f"{name} needs {', '.join(lacking)}")
        text = request.handler(reader, args)
    except Declined as err:
        declined = str(err)
        text = DECLINED_PREFIX + declined
    return text, {"name": name, "args": args, "chars": len(text),
                  "declined": declined}
