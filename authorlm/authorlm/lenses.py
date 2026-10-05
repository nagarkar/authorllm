"""Lenses — prompt-first sweeps (design: docs/sweep-framework.md;
cross-chapter architecture: docs/lens-architecture-design.md).

A lens is an author-ratified editorial concern stored as a markdown
prompt in `_lenses/<name>.md` (observation-invisible, author-editable,
like profiles). Its reference standard lives ONLY in that prompt — that
is what distinguishes it from an auditor, whose reference is maintained
in AuthorLM's stores.

Since 2026-09-06 a lens may carry TOML front matter declaring its CLASS
(`chapter` — the essay against itself; `cross-chapter` — the essay
against the TEXT of other chapters it names or neighbours), the INPUTS
it reads (the author's stable artifacts: glossary, audience profile,
organizing scheme, protected registers, the filter roster, the reading
order) and, for a cross-chapter lens, which TARGETS it may see. A bare
prompt with no front matter is a `chapter` lens with the glossary, which
is exactly what every lens was before.

The ruling that shaped this: LENSES RUN INDEPENDENTLY AND NEVER ON
SUMMARIES. A summary is rebuilt on every settle; a finding judged
against one is judged against something that changes while the chapter
does not. Where a rule needs another chapter, the authority is that
chapter's own text, carried in the payload's T block with its hash.

One payload, two minds. `assemble` builds four hashed blocks (S law,
A inputs, T targets, E essay) deterministically; `lens run` PRINTS it
for the chat agent or a subagent to answer, `lens run --native` sends
the same blocks to the configured cheap model, and `lens register`
stores an externally drafted answer. Findings are guidance rows
(kind='lens', own batch) reviewed through `lens review` →
record_review — one evidence stream, no forks. Each finding records the
hash of the essay and of every target it was judged against, so
`lens status` can call a batch STALE by byte comparison.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .concepts import concept_pattern, node_names
from .db import Database, ko_fields, loads, new_id
from .findings import flag_rules  # re-exported: lenses.flag_rules is public
                                   # API (tests call it directly); the
                                   # implementation lives once, in
                                   # findings.py, shared with morph.
from .revisions import read_manuscript_files

LENS_DIR = "_lenses"
LENS_KIND = "lens"
LENS_ORIGIN = "lens"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")

# ------------------------------------------------------------ front matter

CLASSES = ("chapter", "cross-chapter")
# The GRAIN is the unit block E holds: the whole file, or one poem of a
# `form = "verse"` file (verse.py; verse-and-cast-design.md, poem-grain
# ruling 2026-09-25). Class says what the unit is read against; grain says
# what the unit is. `siblings` is the poem-grain target: the other poems
# of the same file, full text.
GRAINS = ("chapter", "poem")
INPUTS = ("glossary", "audience", "scheme", "registers", "passes",
          "reading-order")
TARGETS = ("pointers", "neighbours", "earlier", "book", "siblings")
EXAMPLES_MODES = ("prompt", "omit")
KEYS = ("class", "inputs", "targets", "examples", "grain")
DELIMITER = "---"
DEFAULT_META = {"class": "chapter", "inputs": ["glossary"], "targets": [],
                "examples": "prompt", "grain": "chapter"}

# The reading-order heuristics the design ratified (§9 decisions 5).
CARD_WORDS = 400            # an entry under this is a chapter card, not a chapter
RECURRENCE_WORDS = 8        # a sentence this long, seen twice, is a recurrence
GLOSSARY_CAP = 60

# The ratified sweep order (design §6). Installed lenses not named here
# run after these, alphabetically.
SWEEP_ORDER = ("framework-consistency", "cross-references", "through-line",
               "validity-and-objections", "evidence-reach",
               "illustration-as-argument", "audience-pitch",
               "unsourced-claims")

PROFILE_DIR = "_profiles"
ALIASES_FILE = "chapter-aliases.toml"
PROTECTED_VALUE = "protected"


class LensError(ValueError):
    """A lens artifact is malformed. Raised at `lens add` and again at
    every load, so a typo fails where it was written."""


def _lens_path(manuscript: dict, name: str) -> Path:
    return Path(manuscript["path"]) / LENS_DIR / f"{name}.md"


def parse_lens(text: str) -> tuple[dict, str]:
    """`(meta, body)` from a lens artifact. Front matter is OPTIONAL: a
    bare prompt is a `chapter` lens reading the glossary, which is what
    every lens was before front matter existed. When present it is TOML
    between `---` lines, validated against the closed vocabularies
    above; every refusal names the legal values."""
    text = (text or "").replace("\r\n", "\n")
    lines = text.split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    meta = dict(DEFAULT_META)
    meta["inputs"] = list(DEFAULT_META["inputs"])
    meta["targets"] = []
    if start < len(lines) and lines[start].strip() == DELIMITER:
        end = None
        for i in range(start + 1, len(lines)):
            if lines[i].strip() == DELIMITER:
                end = i
                break
        if end is None:
            raise LensError(f"the front matter is never closed — add a "
                            f"'{DELIMITER}' line after the last key.")
        try:
            given = tomllib.loads("\n".join(lines[start + 1:end]))
        except tomllib.TOMLDecodeError as err:
            raise LensError(f"the front matter is not valid TOML: {err}. "
                            "String values need quotes: class = "
                            "\"cross-chapter\".") from None
        unknown = sorted(k for k in given if k not in KEYS)
        if unknown:
            raise LensError(
                f"unknown front-matter key(s): {', '.join(unknown)}. A "
                f"lens's front matter carries {', '.join(KEYS)}, nothing "
                "else.")
        klass = given.get("class", meta["class"])
        if klass not in CLASSES:
            raise LensError(f"class must be one of "
                            f"{', '.join(repr(c) for c in CLASSES)}, "
                            f"not {klass!r}.")
        inputs = given.get("inputs", meta["inputs"])
        if not isinstance(inputs, list) or \
                any(i not in INPUTS for i in inputs):
            raise LensError(f"inputs must be a list drawn from "
                            f"{', '.join(INPUTS)}.")
        targets = given.get("targets", [])
        if not isinstance(targets, list) or \
                any(t not in TARGETS for t in targets):
            raise LensError(f"targets must be a list drawn from "
                            f"{', '.join(TARGETS)}.")
        if klass == "chapter" and targets:
            raise LensError("a 'chapter' lens declares no targets — it "
                            "reads the essay against itself. Declare "
                            "class = \"cross-chapter\" to read other "
                            "chapters.")
        if klass == "cross-chapter" and not targets:
            raise LensError("a 'cross-chapter' lens must declare at least "
                            f"one target: {', '.join(TARGETS)}.")
        grain = given.get("grain", meta["grain"])
        if grain not in GRAINS:
            raise LensError(f"grain must be one of "
                            f"{', '.join(repr(g) for g in GRAINS)}, "
                            f"not {grain!r}.")
        if "siblings" in targets and grain != "poem":
            raise LensError("the 'siblings' target (the other poems of the "
                            "file) needs grain = \"poem\".")
        examples = given.get("examples", meta["examples"])
        if examples not in EXAMPLES_MODES:
            raise LensError(f"examples must be one of "
                            f"{', '.join(repr(m) for m in EXAMPLES_MODES)}.")
        meta.update(**{"class": klass, "inputs": list(inputs),
                       "targets": list(targets), "examples": examples,
                       "grain": grain})
        body = "\n".join(lines[end + 1:])
    else:
        body = "\n".join(lines[start:])
    return meta, body.strip("\n") + "\n"


def load_lens(manuscript: dict, name: str) -> tuple[dict, str]:
    path = _lens_path(manuscript, name)
    if not path.is_file():
        known = ", ".join(l["name"] for l in list_lenses(manuscript)) or "none"
        raise LookupError(f"no lens '{name}' (defined: {known})")
    return parse_lens(path.read_text(encoding="utf-8"))


def add_lens(manuscript: dict, name: str, prompt: str) -> Path:
    if not _NAME.match(name):
        raise ValueError("lens names are short kebab-case slugs, e.g. "
                         "'kantian-objections'")
    if not prompt.strip():
        raise ValueError("a lens is its prompt — pass the prompt on stdin")
    parse_lens(prompt)                      # refuse before writing
    path = _lens_path(manuscript, name)
    path.parent.mkdir(exist_ok=True)
    path.write_text(prompt.strip() + "\n", encoding="utf-8")
    return path


_PROSE = re.compile(r"^\s*(#+\s*)?(?P<text>\S.*)$")


def summary_of(body: str) -> str:
    for line in body.split("\n"):
        m = _PROSE.match(line)
        if m and m.group("text").strip():
            return m.group("text").strip().strip("*").strip()[:100]
    return ""


def list_lenses(manuscript: dict) -> list[dict]:
    """Every artifact in `_lenses/`, with its class. A malformed one is
    listed with its error where its summary would be, never hidden."""
    directory = Path(manuscript["path"]) / LENS_DIR
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        try:
            meta, body = parse_lens(text)
        except LensError as err:
            out.append({"name": path.stem, "class": None, "inputs": [],
                        "targets": [], "summary": "", "error": str(err)})
            continue
        out.append({"name": path.stem, "class": meta["class"],
                    "inputs": meta["inputs"], "targets": meta["targets"],
                    "summary": summary_of(body), "error": None})
    return out


_EXAMPLES_HEAD = re.compile(r"^## Examples\s*$", re.M)
_LENS_HEAD = re.compile(r"^## The lens\s*$", re.M)


def strip_examples(body: str) -> str:
    """The artifact without its Examples section (examples = "omit"):
    from the `## Examples` heading up to the `## The lens` heading. With
    either heading absent nothing is stripped — the author's prose is
    never guessed at."""
    a = _EXAMPLES_HEAD.search(body)
    b = _LENS_HEAD.search(body)
    if not a or not b or b.start() < a.start():
        return body
    return body[:a.start()] + body[b.start():]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


# ------------------------------------------------------------ the payload

LENS_SYSTEM = """\
You review ONE chapter of a nonfiction manuscript through the author's own
editorial lens. The material comes in four blocks:

S — THE LENS, ratified by the author: its rules, and the examples that show
    where its line falls.
A — INPUTS the author ratified: GLOSSARY (settled definitions, and the
    chapter where each term was introduced), AUDIENCE PROFILE, ORGANIZING
    SCHEME, PROTECTED REGISTERS, SENTENCE-LEVEL PASSES, READING ORDER. A
    block that says "(no profile on record)" is absent: never assume it.
T — TARGETS: the TEXT of the other chapters this lens may read, each under
    its file name, with derived lists computed from the book. T is the ONLY
    authority on any other chapter. Nothing you remember about the book
    counts, and no summary of any chapter exists here.
E — THE CHAPTER under review, whole.

Report only findings that the lens's concern actually covers; if nothing
qualifies, report nothing. Each finding quotes the chapter VERBATIM. A
finding about another chapter names that chapter's file exactly as it
appears in T and quotes it verbatim too. Name the Flag rule each finding
falls under, in the lens's own words. Reply with JSON only:
{"findings": [{"quote": "<verbatim sentence(s) from E>",
"note": "<the finding, in the shape the lens asks for>",
"rule": "<the Flag rule's heading, as written in S>",
"replacement": "<optional: the quote's substitute within its paragraph>",
"footnote": "<optional: the GIST of a footnote the quoted sentence should carry — a source to be supplied, a qualification — never the finished text>",
"judgment": "<optional: ONLY when no replacement can be written from S, A, T and E — the decision the author alone can make, stated as two or three concrete OPTIONS to choose between, each grounded in the quoted text: name the line or stanza it touches and, where one is obvious, the candidate word, rhyme, or cut; at most two sentences; no brackets and no line breaks>",
"target_file": "<optional: a file named in T>",
"target_quote": "<optional: verbatim sentence(s) from that file>"}]}

REWRITE OR JUDGMENT. Every finding proposes its repair as `replacement`
whenever the chapter, the GLOSSARY, and the target chapters supply what the
sentence needs: a wrong word, a misstated credit, a missing clause, a dropped
scoping, a frame sentence, a corrected pointer. Reserve `judgment` for a
repair that is a decision the author alone can make — cut or move a section,
choose between two claims the chapter makes, supply a fact the payload does
not contain, add an argument that does not yet exist — and state that
decision as OPTIONS the author can pick between, not a bare instruction:
two or three ways out, each tied to the words it would touch ("rhyme line
four with line two — 'wisdom' has no partner; 'ignorance' could take
'distance'"; "cut stanzas three to eight, or bring the pen back in the last
stanza"), in at most two sentences (author ruling 2026-09-27: a judgment
"could include more information or examples or suggestions"). The harness
plants it as a [Judgment: …] tag after the paragraph for the author to keep
or delete, so it must carry no brackets and no line breaks. A finding with
neither is a note the author cannot act on in the Doc, and is a fault of
the reply.

Footnotes are requested, never written. Where the repair is a source the
author must supply, or a qualification that would break the sentence's
stride, give `footnote` the gist; the harness plants a [Footnote: gist] tag
after the quoted sentence and the author's footnote road drafts the text.
Do not invent a citation in a note or a replacement.
"""


@dataclass(frozen=True)
class LensPayload:
    """The four blocks, in volatility order. S and A are stable across the
    lenses run on one essay in one sitting; T and E vary per lens."""
    law: str            # block S — harness prompt + the lens artifact
    inputs: str         # block A — the declared INPUTS, rendered
    targets: str        # block T — target chapters and derived lists
    essay: str          # block E — the chapter under review
    meta: dict          # the lens's front matter
    target_files: dict  # file → text, for the finding gate
    file: str
    essay_sha: str
    poem: dict | None = None      # poem grain: {n, title, sha, text}

    @property
    def system(self) -> str:
        return self.law

    @property
    def user(self) -> str:
        return "\n".join(b for b in (self.inputs, self.targets, self.essay)
                         if b)

    @property
    def blocks(self) -> list[tuple[str, str]]:
        return [("S", self.law), ("A", self.inputs),
                ("T", self.targets), ("E", self.essay)]

    @property
    def hashes(self) -> dict[str, str]:
        return {name: _sha(text) for name, text in self.blocks}

    @property
    def target_shas(self) -> dict[str, str]:
        return {f: _sha(t) for f, t in self.target_files.items()}

    def render(self, name: str) -> str:
        """The printed form `lens run` shows: every block with its size
        and hash, so the answer can be traced to exactly what was seen."""
        where = self.file + (f" — poem {self.poem['n']} “{self.poem['title']}”"
                             if self.poem else "")
        reg = (f"lens register {name} {self.file}"
               + (f" --poem {self.poem['n']}" if self.poem else ""))
        out = [f"lens '{name}' [{self.meta['class']}"
               + (", poem grain" if self.poem else "") + f"] on {where} — "
               f"no model call was made. Answer the contract in block S "
               f"and pipe the JSON into '{reg}'; or run again with --native."]
        for label, text in self.blocks:
            if not text:
                continue
            out.append(f"\n───── block {label} — {len(text):,} chars — "
                       f"sha256 {_sha(text)}\n")
            out.append(text.rstrip("\n"))
        return "\n".join(out) + "\n"


def _section(label: str, body: str) -> str:
    body = (body or "").strip()
    return f"{label}\n{body or '(none)'}\n"


def _title_of(text: str) -> str:
    for line in (text or "").split("\n"):
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip().strip("*").strip()
    return ""


def _words(text: str) -> int:
    return len((text or "").split())


def _resolve(files: dict[str, str], file: str) -> str:
    rel = next((r for r in files if r == file or r.endswith("/" + file)
                or Path(r).name == file), None)
    if rel is None:
        raise LookupError(f"'{file}' is not a manuscript file")
    return rel


def _order(files: dict[str, str]) -> tuple[list[str], dict[str, dict],
                                           dict[str, str]]:
    """(reading order, toc attributes, matter map) — the three structural
    facts every input and target renderer needs."""
    from . import structure
    order, _unlisted = structure.reading_order(files)
    attrs = structure.toc_attrs(files.get(structure.TOC_FILENAME) or "")
    matter = structure.matter_map(files)
    return order, attrs, matter


def _is_card(files: dict[str, str], matter: dict[str, str], name: str) -> bool:
    """A card is a chapter too short to be a chapter (a part opener, a
    title page). Matter alone does not make one: the Metaphysic is back
    matter and eleven thousand words, and a proportion judged against it
    is a real judgment. The reading order still shows the matter."""
    return _words(files.get(name, "")) < CARD_WORDS


def protected_files(files: dict[str, str]) -> list[str]:
    """Chapters whose toc entry carries `register = "protected"`."""
    _, attrs, _ = _order(files)
    return [f for f, a in attrs.items() if a.get("register") == PROTECTED_VALUE]


# --- inputs (block A)

def _glossary(db: Database, manuscript: dict, rel: str, text: str) -> str:
    """GLOSSARY: the concepts this essay names or introduced — name,
    aliases, notes, and WHERE INTRODUCED (the fact that makes the ramp
    check a lookup rather than a summary read)."""
    from . import api
    try:
        graph = api.scoped_concepts(db, manuscript, file=rel, text=text)
    except LookupError:
        return ""
    lines = []
    for node in sorted(graph.get("nodes", []),
                       key=lambda n: n.get("name") or "")[:GLOSSARY_CAP]:
        notes = (node.get("notes") or "").strip() or "(no notes)"
        aliases = [a for a in (node.get("aliases") or []) if a]
        also = f" (also: {', '.join(sorted(aliases))})" if aliases else ""
        where = node.get("introduced_in")
        intro = f" [introduced in {where}]" if where else ""
        lines.append(f"- {node['name']}{also}{intro} — {notes}")
    edges = [e.get("edge") for e in graph.get("edges", []) if e.get("edge")]
    if edges:
        lines.append("")
        lines.append("Relations the author has recorded between them:")
        lines.extend(f"- {e}" for e in sorted(edges))
    return "\n".join(lines)


def _profile(manuscript: dict, key: str) -> str:
    from .filtering import _profiles_block
    return _profiles_block(manuscript, [key])


def _registers(files: dict[str, str]) -> str:
    prot = protected_files(files)
    if not prot:
        return ("(no chapter is marked protected — put register = "
                "\"protected\" on a toc.toml entry to exempt its voice)")
    return "\n".join(f"- {f} — {_title_of(files.get(f, ''))}" for f in prot)


def _passes(manuscript: dict) -> str:
    from . import filters
    rows = filters.list_filters(manuscript)
    if not rows:
        return "(no sentence-level passes installed)"
    return "\n".join(f"- {r['name']}: {r['summary'] or r.get('error', '')}"
                     for r in rows)


def _reading_order(files: dict[str, str], rel: str) -> str:
    order, _attrs, matter = _order(files)
    lines = []
    for i, name in enumerate(order, 1):
        marks = []
        if _is_card(files, matter, name):
            marks.append("card")
        if matter.get(name, "main") != "main":
            marks.append(matter[name])
        if name == rel:
            marks.append("THIS CHAPTER")
        tag = f"  [{', '.join(marks)}]" if marks else ""
        lines.append(f"{i:2d}. {name} — {_title_of(files[name])}{tag}")
    return "\n".join(lines)


def _inputs_block(db: Database, manuscript: dict, files: dict[str, str],
                  rel: str, text: str, inputs: list[str]) -> str:
    parts = []
    for key in inputs:
        if key == "glossary":
            parts.append(_section("GLOSSARY (the author's settled "
                                  "definitions; never re-word a term)",
                                  _glossary(db, manuscript, rel, text)))
        elif key == "audience":
            parts.append(_section("AUDIENCE PROFILE",
                                  _profile(manuscript, "audience")))
        elif key == "scheme":
            parts.append(_section("ORGANIZING SCHEME",
                                  _profile(manuscript, "scheme")))
        elif key == "registers":
            parts.append(_section("PROTECTED REGISTERS (voices that assert "
                                  "by design; exempt)", _registers(files)))
        elif key == "passes":
            parts.append(_section("SENTENCE-LEVEL PASSES (hand a "
                                  "one-sentence fault to these by name)",
                                  _passes(manuscript)))
        elif key == "reading-order":
            parts.append(_section("READING ORDER", _reading_order(files, rel)))
    return "\n".join(parts)


# --- targets (block T)

def _aliases(manuscript: dict) -> dict[str, list[str]]:
    """`_profiles/chapter-aliases.toml`: [aliases] "file.md" = ["…", …].
    Author-owned; the resolver's misses are what grows it."""
    path = Path(manuscript["path"]) / PROFILE_DIR / ALIASES_FILE
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}
    table = data.get("aliases", data)
    return {k: [str(v) for v in vs] for k, vs in table.items()
            if isinstance(vs, list)}


def chapter_names(files: dict[str, str], manuscript: dict | None = None
                  ) -> dict[str, list[str]]:
    """file → the names a pointer may use for it: its title, the title
    without a leading article, and the author's aliases."""
    aliases = _aliases(manuscript) if manuscript else {}
    from . import structure
    out: dict[str, list[str]] = {}
    for name, text in files.items():
        if structure.is_structural(name):
            continue
        title = _title_of(text)
        names = []
        if title:
            names.append(title)
            for art in ("The ", "A ", "An "):
                if title.startswith(art) and len(title) > len(art) + 2:
                    names.append(title[len(art):])
        names.extend(aliases.get(name, []))
        seen, uniq = set(), []
        for n in names:
            if n and n.lower() not in seen:
                seen.add(n.lower())
                uniq.append(n)
        out[name] = uniq
    return out


_FRAMED = re.compile(
    r"\bthe (?:essay|paper|chapter)(?: on)? ((?:the )?[A-Z][^.,;:!?\n]{0,60})")


def resolve_pointers(files: dict[str, str], rel: str,
                     manuscript: dict | None = None
                     ) -> tuple[list[tuple[str, str]], list[str]]:
    """(resolved, unresolved). Resolved: (target file, the phrase that
    named it). A chapter is a pointer target when one of its names occurs
    in the essay as a whole phrase, case-sensitively — the house style
    capitalizes internal references, and 'the good life' in prose is not
    a pointer to the chapter of that title. Unresolved: a framed reference
    ('the essay on X') whose X matched no chapter, reported so the alias
    table can grow."""
    text = files[rel]
    names = chapter_names(files, manuscript)
    resolved: list[tuple[str, str]] = []
    hit_files: set[str] = set()
    for name, cands in names.items():
        if name == rel:
            continue
        for cand in sorted(cands, key=len, reverse=True):
            if len(cand) < 3:
                continue
            if re.search(r"(?<![\w])" + re.escape(cand) + r"(?![\w])", text):
                resolved.append((name, cand))
                hit_files.add(name)
                break
    unresolved: list[str] = []
    all_names = [c for cs in names.values() for c in cs]
    for m in _FRAMED.finditer(text):
        phrase = m.group(1).strip()
        head = " ".join(phrase.split()[:8])
        if not any(re.search(r"(?<![\w])" + re.escape(c) + r"(?![\w])",
                             phrase) for c in all_names if len(c) >= 3):
            if head not in unresolved:
                unresolved.append(head)
    return resolved, unresolved


def neighbours(files: dict[str, str], rel: str) -> list[str]:
    """The nearest full chapters before and after, cards transparent."""
    order, _attrs, matter = _order(files)
    if rel not in order:
        return []
    i = order.index(rel)
    out = []
    for step in (-1, 1):
        j = i + step
        while 0 <= j < len(order):
            if not _is_card(files, matter, order[j]):
                out.append(order[j])
                break
            j += step
    return out


def terms_by_introduction(db: Database, manuscript: dict,
                          files: dict[str, str], rel: str, text: str
                          ) -> tuple[list[tuple[str, str]],
                                     list[tuple[str, str]]]:
    """(introduced later, introduced earlier): (concept, chapter) pairs
    for the concepts this essay names, by the graph's `introduced_in`
    and the reading order. Zero tokens, and exactly the fact the ramp
    check used to want a summary for."""
    order, _a, _m = _order(files)
    pos = {name: i for i, name in enumerate(order)}
    here = pos.get(rel)
    later, earlier = [], []
    if here is None:
        return later, earlier
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' AND introduced_in IS NOT NULL "
        "AND introduced_in != ?", (manuscript["id"], rel)):
        if not any(concept_pattern(nm).search(text) for nm in node_names(node)):
            continue
        where = node["introduced_in"]
        if where not in pos:
            continue
        pair = (node["name"], where)
        (later if pos[where] > here else earlier).append(pair)
    return sorted(later), sorted(earlier)


_ANCHOR = re.compile(r"\[\^[^\]]+\]")


def _sentences(text: str) -> list[str]:
    plain = _ANCHOR.sub("", text or "").replace("*", "").replace("_", "")
    plain = re.sub(r"^#.*$", "", plain, flags=re.M)
    out = []
    for s in re.split(r"(?<=[.!?])\s+", plain):
        s = " ".join(s.split())
        if len(s.split()) >= RECURRENCE_WORDS:
            out.append(s)
    return out


def recurrences(files: dict[str, str], rel: str) -> list[tuple[str, list[str]]]:
    """Sentences of this essay that occur verbatim in another chapter,
    with the chapters. A pure auditor: zero tokens."""
    from . import structure
    others = {n: " ".join(_ANCHOR.sub("", t).replace("*", "").split())
              for n, t in files.items()
              if n != rel and not structure.is_structural(n)}
    out = []
    for s in _sentences(files[rel]):
        where = [n for n, t in others.items() if s in t]
        if where:
            out.append((s, where))
    return out


def realizations(db: Database, manuscript: dict, files: dict[str, str],
                 rel: str, text: str) -> list[tuple[str, list[str]]]:
    """For each concept this essay names, the other chapters that name
    it too — candidates for 'the same telling recurs across chapters'."""
    from . import structure
    out = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (manuscript["id"],)):
        pats = [concept_pattern(nm) for nm in node_names(node)]
        if not any(p.search(text) for p in pats):
            continue
        where = [n for n, t in files.items()
                 if n != rel and not structure.is_structural(n)
                 and any(p.search(t) for p in pats)]
        if len(where) >= 2:
            out.append((node["name"], sorted(where)))
    return sorted(out)


def _targets_block(db: Database, manuscript: dict, files: dict[str, str],
                   rel: str, text: str, meta: dict
                   ) -> tuple[str, dict[str, str]]:
    """Block T and the file→text map behind it."""
    if meta["class"] != "cross-chapter":
        return "", {}
    chosen: dict[str, str] = {}
    parts: list[str] = []
    wanted = meta["targets"]
    if "pointers" in wanted:
        resolved, unresolved = resolve_pointers(files, rel, manuscript)
        for f, phrase in resolved:
            chosen.setdefault(f, files[f])
        if resolved:
            parts.append(_section(
                "POINTERS RESOLVED (chapter — the phrase in E that named it)",
                "\n".join(f"- {f} — “{p}”" for f, p in resolved)))
        parts.append(_section(
            "POINTERS UNRESOLVED (framed references naming no chapter; a "
            "finding candidate, and a line for the alias table)",
            "\n".join(f"- the essay/paper on {u}" for u in unresolved)
            if unresolved else "(none)"))
    if "neighbours" in wanted:
        nb = neighbours(files, rel)
        for f in nb:
            chosen.setdefault(f, files[f])
        parts.append(_section("NEIGHBOURS (nearest full chapters before "
                              "and after; cards transparent)",
                              "\n".join(f"- {f}" for f in nb)))
    if "earlier" in wanted:
        later, earlier = terms_by_introduction(db, manuscript, files, rel,
                                               text)
        parts.append(_section(
            "TERMS INTRODUCED LATER (named here; the graph records their first "
            "introduction AFTER this chapter in reading order. A LEAD, NOT A "
            "VERDICT: a term this chapter itself defines on its own page is "
            "supplied by this chapter, whatever the graph says — read E "
            "before flagging, and never flag a term at the sentence that "
            "defines it)",
            "\n".join(f"- {c} — {w}" for c, w in later) if later else "(none)"))
        parts.append(_section(
            "TERMS INTRODUCED EARLIER (concept — the chapter that supplied it)",
            "\n".join(f"- {c} — {w}" for c, w in earlier)
            if earlier else "(none)"))
    if "siblings" in wanted and meta.get("_poem") is not None:
        from .verse import poems_of

        from .verse import poem_level as _plevel

        others = [p for p in poems_of(text, _plevel(files, rel))
                  if p["n"] != meta["_poem"]["n"]]
        chosen.setdefault(rel, files[rel])
        parts.append(_section(
            "SIBLINGS (the other poems of this file, full text; a finding "
            f"about one names target_file {rel} and quotes it)",
            "\n\n".join(p["text"] for p in others) if others else "(none)"))
    if "book" in wanted:
        rec = recurrences(files, rel)
        parts.append(_section(
            "VERBATIM RECURRENCES (sentences of E found in other chapters)",
            "\n".join(f"- “{s}” — also in {', '.join(w)}" for s, w in rec)
            if rec else "(none)"))
        real = realizations(db, manuscript, files, rel, text)
        parts.append(_section(
            "CONCEPT REALIZATIONS (concept named here — other chapters that "
            "also name it; candidates for a telling that recurs)",
            "\n".join(f"- {c} — {', '.join(w)}" for c, w in real)
            if real else "(none)"))
    texts = []
    for f, t in chosen.items():
        texts.append(f"--- {f} — {_title_of(t)} — sha256 {_sha(t)} ---\n"
                     f"{t.rstrip()}\n")
    head = ("TARGETS — the only authority on any other chapter. Read these "
            "texts; nothing else about the book is available.\n")
    body = "\n".join(parts)
    if texts:
        body += "\nTARGET CHAPTERS\n" + "\n".join(texts)
    return head + body, chosen


def assemble(db: Database, manuscript: dict, name: str, file: str,
             files: dict[str, str] | None = None,
             inputs_cache: dict | None = None,
             poem: str | None = None) -> LensPayload:
    """The whole payload from stored state and the manuscript on disk,
    deterministically. `files` lets a sweep read the manuscript once;
    `inputs_cache` lets it render each distinct INPUTS block once."""
    meta, body = load_lens(manuscript, name)
    from .structure import refuse_sidecar
    refuse_sidecar(Path(file).name)
    if files is None:
        files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    text = files[rel]
    if rel in protected_files(files):
        raise ValueError(f"{rel} is a protected register — no lens runs on "
                         "it; the register protects its voice by design.")
    artifact = strip_examples(body) if meta["examples"] == "omit" else body
    law = LENS_SYSTEM + "\n" + _section("THE LENS (ratified by the author)",
                                        artifact)
    key = tuple(meta["inputs"])
    if inputs_cache is not None and key in inputs_cache:
        inputs = inputs_cache[key]
    else:
        inputs = _inputs_block(db, manuscript, files, rel, text,
                               meta["inputs"])
        if inputs_cache is not None:
            inputs_cache[key] = inputs
    chosen_poem = None
    if meta["grain"] == "poem":
        from .verse import (find_poem, frame_of, is_verse, poem_level,
                            poems_of)

        if not is_verse(files, rel):
            raise ValueError(
                f"lens '{name}' has grain = \"poem\" and {rel} is not a verse "
                "file — mark it in toc.toml (form = \"verse\") or run a "
                "chapter-grain lens on it.")
        level = poem_level(files, rel)
        poems = poems_of(text, level)
        if not poems:
            raise ValueError(f"{rel} is marked verse but has no poems at "
                             f"heading level {level} — set poem_level in "
                             "toc.toml to the level its poem titles use.")
        if poem is None:
            raise ValueError(
                f"lens '{name}' reads one poem at a time — name it with "
                f"--poem <n|title>; {rel} has {len(poems)}: "
                + "; ".join(f"{p['n']} {p['title']}" for p in poems))
        chosen_poem = find_poem(poems, poem)
        meta = {**meta, "_poem": chosen_poem}
    elif poem is not None:
        raise ValueError(f"lens '{name}' has grain = \"chapter\"; --poem "
                         "applies only to a poem-grain lens.")
    targets, chosen = _targets_block(db, manuscript, files, rel, text, meta)
    if chosen_poem is not None:
        frame = frame_of(text, level)
        essay = ((_section("THE SECTION FRAME (title and epigraph; context, "
                           "never the subject)", frame) + "\n") if frame else "") \
            + _section(f"THE POEM — {rel} — {chosen_poem['n']} of {len(poems)} "
                       f"— “{chosen_poem['title']}” — sha256 "
                       f"{_sha(chosen_poem['text'])}", chosen_poem["text"])
        return LensPayload(law=law, inputs=inputs, targets=targets,
                           essay=essay, meta=meta, target_files=chosen,
                           file=rel, essay_sha=_sha(text),
                           poem={"n": chosen_poem["n"],
                                 "title": chosen_poem["title"],
                                 "sha": _sha(chosen_poem["text"]),
                                 "text": chosen_poem["text"]})
    essay = _section(f"THE CHAPTER — {rel} — sha256 {_sha(text)}", text)
    return LensPayload(law=law, inputs=inputs, targets=targets, essay=essay,
                       meta=meta, target_files=chosen, file=rel,
                       essay_sha=_sha(text))


def assemble_poems(db: Database, manuscript: dict, name: str, file: str
                   ) -> list[LensPayload]:
    """Every poem of a verse file as its own payload, the INPUTS block
    rendered once. The whole-file loop `lens run` takes at poem grain."""
    from .verse import poems_of

    files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    cache: dict = {}
    from .verse import poem_level as _plevel

    return [assemble(db, manuscript, name, rel, files=files,
                     inputs_cache=cache, poem=str(p["n"]))
            for p in poems_of(files[rel], _plevel(files, rel))]


# ------------------------------------------------------------- the door

RESERVED_MARKERS = ("<<", ">>", "{{", "}}")


def _store_findings(db: Database, manuscript: dict, session: dict,
                    lens_name: str, relpath: str, file_text: str,
                    findings: list, source: str,
                    payload: LensPayload | None = None,
                    lens_body: str = "") -> dict:
    """Lens's own thin wrapper over the shared registration door
    (`findings.store_findings`, factored out 2026-09-26,
    `docs/morph-design.md` §7): supplies lens's cross-chapter target
    files/shas and poem grain from `payload`, and pins `producer_key`
    to `"lens"` so every stored row's metadata and this call's return
    value keep the exact shape `lens status`/`lens findings`/
    `lens review` — and every historical row already in the database —
    already expect."""
    from . import findings as fnd

    return fnd.store_findings(
        db, manuscript, session,
        kind=LENS_KIND, producer=lens_name, relpath=relpath,
        file_text=file_text, findings=findings, source=source,
        batch_prefix="lb", origin_type=LENS_ORIGIN, verb_stem="lens",
        producer_key="lens", rubric_body=lens_body,
        target_files=payload.target_files if payload else None,
        target_shas=payload.target_shas if payload else None,
        poem=payload.poem if payload else None)


def run_lens(db: Database, manuscript: dict, session: dict, name: str,
             relpath: str, llm, payload: LensPayload | None = None,
             poem: str | None = None) -> dict:
    """Native execution: the assembled payload, sent whole to the
    configured model. The same blocks `lens run` prints."""
    if payload is None:
        payload = assemble(db, manuscript, name, relpath, poem=poem)
    _meta, body = load_lens(manuscript, name)
    result = llm.complete_json(payload.system, payload.user)
    findings = (result or {}).get("findings", []) \
        if isinstance(result, dict) else []
    files = read_manuscript_files(Path(manuscript["path"]))
    return _store_findings(db, manuscript, session, name, payload.file,
                           files[payload.file], findings, source="native",
                           payload=payload, lens_body=body)


def register_findings(db: Database, manuscript: dict, session: dict,
                      name: str, relpath: str, findings: list,
                      poem: str | None = None) -> dict:
    """The registration door for externally produced findings (a Claude
    subagent answering the printed payload). Same hygiene, same store,
    same review loop. The payload is re-assembled here so target files
    are checked against what the lens was allowed to see; at poem grain
    `poem` names the one the reply answers for."""
    payload = assemble(db, manuscript, name, relpath, poem=poem)
    _meta, body = load_lens(manuscript, name)
    files = read_manuscript_files(Path(manuscript["path"]))
    return _store_findings(db, manuscript, session, name, payload.file,
                           files[payload.file], findings, source="external",
                           payload=payload, lens_body=body)


# ------------------------------------------------------ one form per unit

def one_per_unit(threads: list[dict]) -> tuple[list[dict], list[dict]]:
    """The tab carries ONE form per paragraph. Several lenses rewriting
    the same unit is common (a sweep); the earliest-staged form goes,
    the rest stay staged for the next push after the author's ruling.
    Returns (chosen, deferred), both in staging order."""
    chosen: dict[tuple[str, int], dict] = {}
    deferred: list[dict] = []
    for t in sorted(threads, key=lambda t: (t.get("created_at") or "", t["id"])):
        n = (loads(t.get("metadata"), {}) or {}).get("anchor_paragraph", 0)
        # Keyed by KIND as well: a judgment tag is an insertion after the
        # paragraph and may sit beside one rewrite of it.
        key = ("insert" if t.get("proposed_old") == "" else "replace", n)
        if key in chosen:
            deferred.append(t)
        else:
            chosen[key] = t
    return list(chosen.values()), deferred


def forms_out(db: Database, manuscript: dict, file: str) -> int:
    """How many lens forms are out in this file's tab right now."""
    from . import staging
    files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    return len(staging.door_threads(db, manuscript["id"], rel,
                                    states=("written",),
                                    origin_type=LENS_ORIGIN))


# ------------------------------------------------------ verdicts → findings

def propagate_verdicts(db: Database, manuscript: dict, threads,
                       kind: str = LENS_KIND) -> dict:
    """A resolved form IS its finding's verdict: a cleaned (accepted)
    form accepts the finding, a declined form (the green half emptied —
    for a judgment tag, the tag deleted) rejects it. Recorded through
    record_review, so the evidence stream is the same as a chat verdict.
    Only findings still `proposed` are touched.

    `kind` defaults to lens's own (every existing caller); morph's
    resolve passes `kind=morph.MORPH_KIND` — the logic is otherwise
    fully generic (reads `doc_threads` state, writes `guidance_history`/
    `editorial_reviews` through `record_review`), so this stays one
    function rather than a second copy in `findings.py`."""
    from . import beliefs as bel
    mid = manuscript["id"]
    out = {"accepted": 0, "rejected": 0}
    for t in threads:
        t = dict(t)
        if t.get("state") not in ("cleaned", "declined"):
            continue
        decision = "accepted" if t["state"] == "cleaned" else "rejected"
        rows = db.all(
            "SELECT * FROM guidance_history WHERE manuscript_id = ? AND "
            "kind = ? AND state = 'proposed' AND metadata LIKE ?",
            (mid, kind, f'%"edit_thread": "{t["id"]}"%'))
        for r in rows:
            # No explanation: the author resolved a form, they did not
            # type a reason. A canned "rejected in the Doc tab" used to
            # stand in for one and was seeded as a belief.
            bel.record_review(db, mid, dict(r), decision, None, None,
                              llm=None)
            out[decision] += 1
    return out


# ------------------------------------------------------------ repair

JUDGMENT_ID = re.compile(r"\|\s*id:\s*(gd-[0-9a-f]+)")

REPAIR_SYSTEM = """\
You are repairing ONE chapter of a nonfiction manuscript at exactly the
places where its author ACCEPTED a lens's judgment. Each accepted judgment
stands in the text as a [Judgment: …] tag after the sentence it concerns.
Blocks:

S — this contract, then STYLE LAW (binding, outranks your instinct) and
    PROTECTED TERMS (never substitute, re-word, or re-capitalize one).
A — GLOSSARY: the author's settled definitions.
J — THE JUDGMENTS: for each, its id, the lens and rule, the finding in
    full, the paragraph that carries the tag (the unit you may rewrite),
    and, where the finding named another chapter, that chapter's text.
E — THE CHAPTER, whole, tags in place.

For each judgment answer with exactly one action:
- "rewrite": `new` is the WHOLE unit rewritten under STYLE LAW, the tag
  removed, and nothing changed beyond what the judgment requires.
- "question": `text` is the one fact you would need and do not have —
  because it is not in S, A, J or E. Never guess it.
- "intent": `text` is a structural change no rewrite can carry (cut or move
  a section, add an argument or a section), stated as a work order in one
  sentence; the tag will be removed and the work order filed.
Never invent a fact, a citation, or a quotation. Never emit << >> {{ or }}.
Reply with JSON only:
{"repairs": [{"id": "gd-…", "action": "rewrite" | "question" | "intent",
"new": "<the whole unit, for rewrite>", "text": "<for question or intent>"}]}
"""


@dataclass(frozen=True)
class RepairPayload:
    law: str
    inputs: str
    judgments: str
    essay: str
    file: str
    tags: list          # [{id, n, unit, gist, raw, finding}]

    @property
    def system(self) -> str:
        return self.law

    @property
    def user(self) -> str:
        return "\n".join(b for b in (self.inputs, self.judgments, self.essay) if b)

    @property
    def blocks(self) -> list[tuple[str, str]]:
        return [("S", self.law), ("A", self.inputs),
                ("J", self.judgments), ("E", self.essay)]

    def render(self) -> str:
        out = [f"lens repair on {self.file} — {len(self.tags)} accepted "
               f"judgment(s); no model call was made. Answer block S's "
               f"contract and pipe the JSON into 'lens repair {self.file} "
               f"--reply <json>'; or run again with --native."]
        for label, text in self.blocks:
            if text:
                out.append(f"\n───── block {label} — {len(text):,} chars — "
                           f"sha256 {_sha(text)}\n")
                out.append(text.rstrip("\n"))
        return "\n".join(out) + "\n"


def judgment_tags(db: Database, manuscript: dict, files: dict[str, str],
                  rel: str) -> list[dict]:
    """Every open [Judgment: …] tag in the file, with the unit that
    carries it and the finding its id names (None when the id is
    missing or unknown — a hand-written tag is still a work order)."""
    from . import directives, passes
    text = files[rel]
    units = passes.paragraphs_of(text)
    out = []
    for tag in directives.scan_text(text, "judgment"):
        m = JUDGMENT_ID.search(tag["gist"])
        finding = None
        fid = m.group(1) if m else None
        if fid:
            row = db.one("SELECT * FROM guidance_history WHERE manuscript_id = ? "
                         "AND id LIKE ?", (manuscript["id"], fid + "%"))
            finding = dict(row) if row else None
        n = next((i for i, u in enumerate(units, 1) if tag["raw"] in u), None)
        out.append({"id": fid, "n": n, "unit": units[n - 1] if n else "",
                    "gist": tag["gist"], "raw": tag["raw"], "finding": finding})
    return out


def assemble_repair(db: Database, manuscript: dict, file: str,
                    files: dict[str, str] | None = None) -> RepairPayload:
    from . import filtering
    from . import styles as st
    from .structure import refuse_sidecar
    refuse_sidecar(Path(file).name)
    if files is None:
        files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    text = files[rel]
    tags = judgment_tags(db, manuscript, files, rel)
    if not tags:
        raise LookupError(f"{rel} carries no open [Judgment: …] tag — nothing "
                          "to repair. Accepted judgments arrive at 'lens "
                          "resolve'; a deleted tag was a rejection.")
    law = (REPAIR_SYSTEM + "\n"
           + _section("STYLE LAW", st.render(db, manuscript["id"], rel))
           + _section("PROTECTED TERMS", filtering._protected_block(
               filtering.protected_terms(db, manuscript, rel, text=text))))
    inputs = _section("GLOSSARY", _glossary(db, manuscript, rel, text))
    parts = []
    for t in tags:
        f = t["finding"] or {}
        meta = loads(f.get("metadata"), {}) or {}
        block = [f"--- judgment {t['id'] or '(no id)'} — unit {t['n']} ---",
                 # `judgment_tags` finds the row by id alone, no kind
                 # filter — this already resolves a morph-planted tag
                 # too, whose metadata key is `producer`, not `lens`.
                 f"producer: {meta.get('lens') or meta.get('producer', '?')}; "
                 f"rule: {meta.get('rule', '?')}",
                 f"tag: {t['raw']}",
                 f"finding: {f.get('explanation') or t['gist']}",
                 "unit (rewrite this whole paragraph, tag removed):",
                 t["unit"]]
        tf = meta.get("target_file")
        if tf and tf in files:
            block.append(f"target chapter {tf} — sha256 {_sha(files[tf])}:")
            block.append(files[tf].rstrip())
        parts.append("\n".join(block) + "\n")
    judgments = "THE JUDGMENTS\n" + "\n".join(parts)
    essay = _section(f"THE CHAPTER — {rel} — sha256 {_sha(text)}", text)
    return RepairPayload(law=law, inputs=inputs, judgments=judgments,
                         essay=essay, file=rel, tags=tags)


def record_repairs(db: Database, manuscript: dict, session: dict, file: str,
                   reply, payload: RepairPayload | None = None) -> dict:
    """Land a repair reply: rewrites are staged through the door (the tag
    gone from the new unit), questions are returned for the author,
    intents are declared scoped to the essay and their tag removal is
    staged. A `new` that still carries the tag, carries reserved
    markers, or is empty is refused by id."""
    from . import api, staging
    if payload is None:
        payload = assemble_repair(db, manuscript, file)
    files = read_manuscript_files(Path(manuscript["path"]))
    rel = payload.file
    text = files[rel]
    by_id = {t["id"]: t for t in payload.tags if t["id"]}
    items = (reply or {}).get("repairs", []) if isinstance(reply, dict) else []
    batch = new_id("lr")
    entries, questions, intents, refused = [], [], [], []
    for it in items:
        if not isinstance(it, dict):
            continue
        fid = str(it.get("id") or "").strip()
        tag = by_id.get(fid) or next((t for t in payload.tags
                                       if t["id"] and t["id"].startswith(fid)),
                                      None)
        if tag is None or tag["n"] is None:
            refused.append({"id": fid, "reason": "no such judgment in the file"})
            continue
        action = it.get("action")
        if action == "rewrite":
            new = str(it.get("new") or "").strip()
            if not new or "[judgment:" in new.lower() \
                    or any(m in new for m in RESERVED_MARKERS):
                refused.append({"id": fid, "reason": "rewrite is empty, still "
                                "carries the tag, or carries reserved markers"})
                continue
            entries.append({"n": tag["n"], "new": new,
                            "why": f"repair of {fid}: {str(it.get('text') or '')}"
                                   .rstrip(": ")})
        elif action == "question":
            questions.append({"id": fid, "n": tag["n"],
                              "text": str(it.get("text") or "").strip()})
        elif action == "intent":
            statement = str(it.get("text") or "").strip()
            if not statement:
                refused.append({"id": fid, "reason": "intent without a statement"})
                continue
            declared = api.declare_intent(db, manuscript, statement, scope=rel)
            intents.append({"id": fid, "intent": declared["intent"]["id"],
                            "text": statement})
            entries.append({"n": tag["n"], "new": _remove_tag(tag["unit"], tag["raw"]),
                            "why": f"{fid} filed as intent "
                                   f"{declared['intent']['id']}: {statement}"})
        else:
            refused.append({"id": fid, "reason": f"unknown action {action!r}"})
    staged = []
    if entries:
        staged = staging.stage_edits(db, manuscript["id"], f"repair@{batch[3:11]}",
                                     rel, text, entries, origin_type=LENS_ORIGIN,
                                     verb_stem="lens")
    return {"file": rel, "batch_id": batch, "staged": staged,
            "questions": questions, "intents": intents, "refused": refused}


def _remove_tag(unit: str, raw: str) -> str:
    out = unit.replace(raw, "")
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"  +", " ", out)).strip()


def dismiss_judgments(db: Database, manuscript: dict, file: str,
                      ids: list[str], reason: str) -> dict:
    """The author's dismissal from chat: the finding is rejected with
    the reason verbatim and the tag's removal is staged as a form."""
    from . import beliefs as bel, staging
    files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    tags = judgment_tags(db, manuscript, files, rel)
    entries, done, missing = [], [], []
    for want in ids:
        tag = next((t for t in tags if t["id"] and t["id"].startswith(want)), None)
        if tag is None or tag["n"] is None:
            missing.append(want)
            continue
        f = tag["finding"]
        if f and f["state"] == "proposed":
            bel.record_review(db, manuscript["id"], dict(f), "rejected", reason,
                              None, llm=None)
        entries.append({"n": tag["n"], "new": _remove_tag(tag["unit"], tag["raw"]),
                        "why": f"dismissed: {reason}"})
        done.append(tag["id"])
    staged = staging.stage_edits(db, manuscript["id"], f"dismiss@{new_id('ld')[3:11]}",
                                 rel, files[rel], entries, origin_type=LENS_ORIGIN,
                                 verb_stem="lens") if entries else []
    return {"file": rel, "dismissed": done, "missing": missing, "staged": staged}


# ------------------------------------------------------------ status

def status(db: Database, manuscript: dict, file: str) -> list[dict]:
    """Every lens batch on this file: lens, when, states, per-rule
    tallies, unverified target quotes, and STALE when the essay or any
    target has changed since (byte comparison of the recorded shas)."""
    files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    now_sha = _sha(files[rel])
    rows = db.all(
        "SELECT * FROM guidance_history WHERE manuscript_id = ? "
        "AND kind = ? ORDER BY created_at, batch_index",
        (manuscript["id"], LENS_KIND))
    batches: dict[str, dict] = {}
    for r in (dict(x) for x in rows):
        meta = loads(r.get("metadata"), {}) or {}
        if meta.get("file") != rel:
            continue
        b = batches.setdefault(r["batch_id"], {
            "batch_id": r["batch_id"], "lens": meta.get("lens"),
            "created_at": r.get("created_at"), "source": meta.get("source"),
            "count": 0, "states": {}, "rules": {}, "unverified": 0,
            "stale": [], "essay_sha": meta.get("essay_sha"),
            "poem": meta.get("poem"), "poem_sha": meta.get("poem_sha"),
            "targets": meta.get("targets") or {}})
        b["count"] += 1
        b["states"][r["state"]] = b["states"].get(r["state"], 0) + 1
        rule = meta.get("rule") or "(no rule named)"
        b["rules"][rule] = b["rules"].get(rule, 0) + 1
        if meta.get("target_unverified"):
            b["unverified"] += 1
    poem_shas: dict[str, str] = {}
    if any(b.get("poem_sha") for b in batches.values()):
        from .verse import poems_of

        from .verse import poem_level as _plevel

        poem_shas = {p["title"]: _sha(p["text"])
                     for p in poems_of(files[rel], _plevel(files, rel))}
    for b in batches.values():
        if b.get("poem_sha"):
            # Poem grain: stale only when THIS poem changed (or vanished),
            # never because a sibling was reworded.
            if poem_shas.get(b["poem"]) != b["poem_sha"]:
                b["stale"].append(f"{rel}#{b['poem']}")
        elif b["essay_sha"] and b["essay_sha"] != now_sha:
            b["stale"].append(rel)
        for tf, sha in (b["targets"] or {}).items():
            cur = files.get(tf)
            if cur is None or _sha(cur) != sha:
                b["stale"].append(tf)
    return sorted(batches.values(), key=lambda b: b["created_at"] or "")


def findings(db: Database, manuscript: dict, file: str,
             all_states: bool = False) -> list[dict]:
    """Every lens finding on this file (open ones by default), oldest
    first, with the handle `lens review` takes: the row id. The listing
    is what makes a sweep's eight batches reviewable — `review <n>` sees
    only the latest batch."""
    files = read_manuscript_files(Path(manuscript["path"]))
    rel = _resolve(files, file)
    flat = " ".join(files[rel].split()).lower()
    out = []
    for r in (dict(x) for x in db.all(
            "SELECT * FROM guidance_history WHERE manuscript_id = ? "
            "AND kind = ? ORDER BY created_at, batch_index",
            (manuscript["id"], LENS_KIND))):
        meta = loads(r.get("metadata"), {}) or {}
        if meta.get("file") != rel:
            continue
        if not all_states and r["state"] != "proposed":
            continue
        quote = meta.get("quote") or ""
        out.append({"id": r["id"], "state": r["state"], "lens": meta.get("lens"),
                    "rule": meta.get("rule"), "quote": quote,
                    # A finding whose passage has since been rewritten is
                    # visible as such, so it can be dismissed rather than
                    # puzzled over.
                    "present": bool(quote) and quote.lower() in flat,
                    "note": r["suggestion"], "created_at": r.get("created_at"),
                    "edit_thread": meta.get("edit_thread"),
                    "footnote": meta.get("footnote"),
                    "judgment": meta.get("judgment"),
                    "target_file": meta.get("target_file"),
                    "also_flagged_by": [a.get("lens") for a in
                                        meta.get("also_flagged_by", [])]})
    return out


# ------------------------------------------------------------- sweep

def sweep_order(manuscript: dict, only: list[str] | None = None,
                skip: list[str] | None = None) -> list[str]:
    installed = [l["name"] for l in list_lenses(manuscript) if not l["error"]]
    ordered = [n for n in SWEEP_ORDER if n in installed]
    ordered += sorted(n for n in installed if n not in SWEEP_ORDER)
    if only:
        missing = [n for n in only if n not in installed]
        if missing:
            raise LookupError(f"no lens named {', '.join(missing)}")
        ordered = [n for n in ordered if n in only]
    if skip:
        ordered = [n for n in ordered if n not in skip]
    return ordered


def link_overlaps(db: Database, batch_ids: list[str]) -> int:
    """Cross-link findings from different lenses whose quotes overlap
    (equal, or one contained in the other) so the author sees one passage
    once with several notes. Returns the number of links written."""
    rows = []
    for bid in batch_ids:
        rows.extend(dict(r) for r in db.all(
            "SELECT * FROM guidance_history WHERE batch_id = ?", (bid,)))
    linked = 0
    for r in rows:
        meta = loads(r.get("metadata"), {}) or {}
        q = _norm(meta.get("quote", ""))
        if not q:
            continue
        others = []
        for o in rows:
            if o["id"] == r["id"]:
                continue
            om = loads(o.get("metadata"), {}) or {}
            oq = _norm(om.get("quote", ""))
            if oq and (q in oq or oq in q) and om.get("lens") != meta.get("lens"):
                others.append({"lens": om.get("lens"), "id": o["id"]})
        if others:
            meta["also_flagged_by"] = others
            db.update("guidance_history", r["id"],
                      {"metadata": json.dumps(meta)})
            linked += 1
    return linked
