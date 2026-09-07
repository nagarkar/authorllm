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
from .revisions import read_manuscript_files

LENS_DIR = "_lenses"
LENS_KIND = "lens"
LENS_ORIGIN = "lens"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")

# ------------------------------------------------------------ front matter

CLASSES = ("chapter", "cross-chapter")
INPUTS = ("glossary", "audience", "scheme", "registers", "passes",
          "reading-order")
TARGETS = ("pointers", "neighbours", "earlier", "book")
EXAMPLES_MODES = ("prompt", "omit")
KEYS = ("class", "inputs", "targets", "examples")
DELIMITER = "---"
DEFAULT_META = {"class": "chapter", "inputs": ["glossary"], "targets": [],
                "examples": "prompt"}

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
        examples = given.get("examples", meta["examples"])
        if examples not in EXAMPLES_MODES:
            raise LensError(f"examples must be one of "
                            f"{', '.join(repr(m) for m in EXAMPLES_MODES)}.")
        meta.update(**{"class": klass, "inputs": list(inputs),
                       "targets": list(targets), "examples": examples})
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


def flag_rules(body: str) -> list[str]:
    """The Flag rule headings a lens states, for tallies and for the
    `rule` field's gate. Two shapes are read: `- **Heading.** …` bullets
    and `### Flag — heading` example headings; anything else the lens
    writes is simply not a named rule."""
    rules: list[str] = []
    for m in re.finditer(r"^\s*-\s+\*\*(.+?)\*\*", body, re.M):
        rules.append(m.group(1).strip().rstrip(".:"))
    for m in re.finditer(r"^###\s+Flag\s+[—-]+\s+(.+?)\s*$", body, re.M):
        rules.append(m.group(1).strip().rstrip("."))
    seen, out = set(), []
    for r in rules:
        k = _norm(r)
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out


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
"judgment": "<optional: ONLY when no replacement can be written from S, A, T and E — the decision the author alone can make, in one clause>",
"target_file": "<optional: a file named in T>",
"target_quote": "<optional: verbatim sentence(s) from that file>"}]}

REWRITE OR JUDGMENT. Every finding proposes its repair as `replacement`
whenever the chapter, the GLOSSARY, and the target chapters supply what the
sentence needs: a wrong word, a misstated credit, a missing clause, a dropped
scoping, a frame sentence, a corrected pointer. Reserve `judgment` for a
repair that is a decision the author alone can make — cut or move a section,
choose between two claims the chapter makes, supply a fact the payload does
not contain, add an argument that does not yet exist — and state that
decision in one clause; the harness plants it as a [Judgment: …] tag at the
sentence for the author to keep or delete. A finding with neither is a note
the author cannot act on in the Doc, and is a fault of the reply.

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
        out = [f"lens '{name}' [{self.meta['class']}] on {self.file} — "
               f"no model call was made. Answer the contract in block S "
               f"and pipe the JSON into 'lens register {name} "
               f"{self.file}'; or run again with --native."]
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
            "TERMS INTRODUCED LATER (named here, first introduced after this "
            "chapter in reading order — the ramp check, from the graph)",
            "\n".join(f"- {c} — {w}" for c, w in later) if later else "(none)"))
        parts.append(_section(
            "TERMS INTRODUCED EARLIER (concept — the chapter that supplied it)",
            "\n".join(f"- {c} — {w}" for c, w in earlier)
            if earlier else "(none)"))
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
             inputs_cache: dict | None = None) -> LensPayload:
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
    targets, chosen = _targets_block(db, manuscript, files, rel, text, meta)
    essay = _section(f"THE CHAPTER — {rel} — sha256 {_sha(text)}", text)
    return LensPayload(law=law, inputs=inputs, targets=targets, essay=essay,
                       meta=meta, target_files=chosen, file=rel,
                       essay_sha=_sha(text))


# ------------------------------------------------------------- the door

RESERVED_MARKERS = ("<<", ">>", "{{", "}}")


def _footnote_gist(raw) -> str:
    """A finding's `footnote` gist, made safe for the tag grammar: one
    line, no brackets (a tag never nests and never spans lines), no
    reserved markers. Empty when there is nothing to plant."""
    gist = " ".join(str(raw or "").split())
    gist = gist.replace("[", "(").replace("]", ")")
    for m in RESERVED_MARKERS:
        gist = gist.replace(m, "")
    return gist.strip()


def _edit_entry(units: list[str], raw_quote: str, replacement: str,
                note: str, taken_units: set[int]) -> tuple[dict | None, str]:
    """Anchor one finding's `replacement` to its unit, or refuse.

    The door's law (filter-pass design §7.2), applied literally: the
    quote must occur VERBATIM in exactly one unit and exactly once
    within it — an unanchored replace picks an occurrence, and picking
    is guessing. Returns (entry, "") on success or (None, reason)."""
    hits = [i for i, u in enumerate(units, 1) if raw_quote in u]
    if not hits:
        return None, ("quote matches the file only after whitespace "
                      "normalization — not verbatim at the byte level, "
                      "so no surgical edit can anchor to it")
    if len(hits) > 1:
        return None, (f"quote occurs in {len(hits)} units — an edit "
                      f"cannot anchor to an ambiguous quote")
    n = hits[0]
    unit = units[n - 1]
    if unit.count(raw_quote) > 1:
        return None, ("quote occurs more than once within its unit — "
                      "an edit cannot anchor to an ambiguous quote")
    if n in taken_units:
        return None, ("its unit already carries a staged edit from "
                      "this batch")
    if any(m in replacement for m in RESERVED_MARKERS):
        return None, "replacement carries reserved markers (<< >> {{ }})"
    new = unit.replace(raw_quote, replacement)
    if not new.strip():
        return None, "replacement would leave the unit empty"
    if new == unit:
        return None, "replacement is identical to the quoted text"
    return {"n": n, "new": new, "why": note}, ""


def _store_findings(db: Database, manuscript: dict, session: dict,
                    lens_name: str, relpath: str, file_text: str,
                    findings: list, source: str,
                    payload: LensPayload | None = None,
                    lens_body: str = "") -> dict:
    """The registration door's core: hygiene-gate each finding (verbatim
    quote in the file) and store the survivors as a fresh lens batch.

    Since the cross-chapter design: a finding may name a `target_file`
    from block T and a `target_quote` from it. The target file must be
    one the lens declared and the payload resolved — a finding about a
    chapter the lens never read is REFUSED by name (a subagent that read
    around the payload has left it). A target quote that is not verbatim
    in its file is kept but marked `target_unverified`. `rule` is matched
    against the lens's Flag headings; an unknown rule is kept and marked.
    Every stored finding carries the essay's sha and each target's sha —
    the provenance `lens status` compares to call a batch STALE.

    A finding may carry an optional `replacement` (filter-pass design
    §7.2): the quoted text's proposed substitute within its unit. When it
    anchors cleanly a `doc_threads` row is staged with origin 'lens'.
    Ruling on the finding and settling the edit stay independent."""
    from . import passes
    from . import staging

    flat = " ".join(file_text.split()).lower()
    units = passes.paragraphs_of(file_text)
    batch_id = new_id("lb")
    stored, dropped = [], 0
    refused_targets: list[dict] = []
    entries: list[tuple[dict, dict]] = []
    refused: list[dict] = []
    taken_units: set[int] = set()
    known_rules = {_norm(r): r for r in flag_rules(lens_body)} if lens_body \
        else {}
    target_files = payload.target_files if payload else {}
    target_shas = payload.target_shas if payload else {}
    index = 0
    for f in findings:
        if not isinstance(f, dict):
            dropped += 1
            continue
        raw_quote = str(f.get("quote", ""))
        quote = " ".join(raw_quote.split())
        note = str(f.get("note", "")).strip()
        if not quote or not note or quote.lower() not in flat:
            dropped += 1
            continue
        tfile = str(f.get("target_file") or "").strip() or None
        if tfile and tfile not in target_files:
            refused_targets.append({"quote": quote[:80], "target": tfile})
            continue
        index += 1
        row = ko_fields("gd")
        row.update(
            manuscript_id=manuscript["id"], session_id=session["id"],
            intent_id=None, batch_id=batch_id, batch_index=index,
            kind=LENS_KIND,
            suggestion=f"[{lens_name}] {note}",
            explanation=f"On “{quote}” ({relpath}): {note}",
            state="proposed",
        )
        meta = {
            "lens": lens_name, "file": relpath, "quote": quote,
            "source": source,
            "dedupe_key": f"lens:{lens_name}:{relpath}:{index}",
            "essay_sha": _sha(file_text),
            "targets": target_shas,
        }
        rule = str(f.get("rule") or "").strip()
        if rule:
            meta["rule"] = known_rules.get(_norm(rule), rule)
            meta["rule_known"] = _norm(rule) in known_rules
        if tfile:
            meta["target_file"] = tfile
            tq = " ".join(str(f.get("target_quote") or "").split())
            if tq:
                meta["target_quote"] = tq
                tflat = " ".join(target_files[tfile].split())
                if tq not in tflat:
                    meta["target_unverified"] = ("target quote is not "
                                                 "verbatim in the target")
        gist = _footnote_gist(f.get("footnote"))
        if gist:
            meta["footnote"] = gist
        judgment = _footnote_gist(f.get("judgment"))
        if judgment:
            meta["judgment"] = judgment
        replacement = None
        if "replacement" in f or gist:
            replacement = str(f.get("replacement") or raw_quote)
            if gist and "[footnote:" not in replacement.lower():
                # Planted, never drafted (footnote design §0, §12): the
                # tag is the author's own request grammar, inert until
                # the footnote road resolves it.
                replacement = replacement.rstrip() + f"[Footnote: {gist}]"
        elif judgment:
            # A judgment is planted the same way: the sentence unchanged,
            # the decision it needs beside it, the finding's id in the
            # tag so `lens repair` can find its way back. Deleting the
            # tag in the tab rejects the finding; leaving it accepts it.
            replacement = (raw_quote.rstrip()
                           + f"[Judgment: {lens_name} — {judgment} "
                             f"| id: {row['id'][:11]}]")
        if replacement is not None:
            entry, reason = _edit_entry(units, raw_quote, replacement,
                                        note, taken_units)
            if entry is None:
                meta["edit_refused"] = reason
                refused.append({"quote": quote[:80], "reason": reason})
            else:
                taken_units.add(entry["n"])
                entries.append((entry, row))
        row["metadata"] = json.dumps(meta)
        db.insert("guidance_history", row)
        stored.append(row)
    edits = []
    if entries:
        # The owner is the BATCH, not the lens: stage_edits keys threads
        # by owner:file:ordinal and replaces in place on a re-run, which
        # for a lens would overwrite forms already out in the tab.
        staged = staging.stage_edits(
            db, manuscript["id"], f"{lens_name}@{batch_id[3:11]}", relpath,
            file_text, [e for e, _ in entries], origin_type=LENS_ORIGIN,
            verb_stem="lens")
        by_n = {loads(t["metadata"], {}).get("anchor_paragraph"): t
                for t in staged}
        for entry, frow in entries:
            thread = by_n.get(entry["n"])
            if thread is None:
                continue
            fmeta = json.loads(frow["metadata"])
            fmeta["edit_thread"] = thread["id"]
            db.update("guidance_history", frow["id"],
                      {"metadata": json.dumps(fmeta)})
            frow["metadata"] = json.dumps(fmeta)
            edits.append(thread)
    return {"lens": lens_name, "file": relpath, "batch_id": batch_id,
            "findings": [dict(r) for r in stored],
            "dropped_ungrounded": dropped,
            "refused_targets": refused_targets,
            "edits_staged": edits, "edits_refused": refused}


def run_lens(db: Database, manuscript: dict, session: dict, name: str,
             relpath: str, llm, payload: LensPayload | None = None) -> dict:
    """Native execution: the assembled payload, sent whole to the
    configured model. The same blocks `lens run` prints."""
    if payload is None:
        payload = assemble(db, manuscript, name, relpath)
    _meta, body = load_lens(manuscript, name)
    result = llm.complete_json(payload.system, payload.user)
    findings = (result or {}).get("findings", []) \
        if isinstance(result, dict) else []
    files = read_manuscript_files(Path(manuscript["path"]))
    return _store_findings(db, manuscript, session, name, payload.file,
                           files[payload.file], findings, source="native",
                           payload=payload, lens_body=body)


def register_findings(db: Database, manuscript: dict, session: dict,
                      name: str, relpath: str, findings: list) -> dict:
    """The registration door for externally produced findings (a Claude
    subagent answering the printed payload). Same hygiene, same store,
    same review loop. The payload is re-assembled here so target files
    are checked against what the lens was allowed to see."""
    payload = assemble(db, manuscript, name, relpath)
    _meta, body = load_lens(manuscript, name)
    files = read_manuscript_files(Path(manuscript["path"]))
    return _store_findings(db, manuscript, session, name, payload.file,
                           files[payload.file], findings, source="external",
                           payload=payload, lens_body=body)


# ------------------------------------------------------ verdicts → findings

def propagate_verdicts(db: Database, manuscript: dict, threads) -> dict:
    """A resolved lens form IS its finding's verdict: a cleaned (accepted)
    form accepts the finding, a declined form (the green half emptied —
    for a judgment tag, the tag deleted) rejects it. Recorded through
    record_review, so the evidence stream is the same as a chat verdict.
    Only findings still `proposed` are touched."""
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
            (mid, LENS_KIND, f'%"edit_thread": "{t["id"]}"%'))
        for r in rows:
            meta = loads(r["metadata"], {}) or {}
            what = "judgment" if meta.get("judgment") else "rewrite"
            bel.record_review(db, mid, dict(r), decision,
                              f"{what} {decision} in the Doc tab", None,
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
                 f"lens: {meta.get('lens', '?')}; rule: {meta.get('rule', '?')}",
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
            "targets": meta.get("targets") or {}})
        b["count"] += 1
        b["states"][r["state"]] = b["states"].get(r["state"], 0) + 1
        rule = meta.get("rule") or "(no rule named)"
        b["rules"][rule] = b["rules"].get(rule, 0) + 1
        if meta.get("target_unverified"):
            b["unverified"] += 1
    for b in batches.values():
        if b["essay_sha"] and b["essay_sha"] != now_sha:
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
