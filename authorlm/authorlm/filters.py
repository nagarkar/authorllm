"""Filters — the artifact (docs/filter-pass-design.md §1.2).

A LENS reads one essay whole and reports findings. A FILTER reads one
essay UNIT BY UNIT and proposes an edit to each unit — the same ratified
prompt applied to every paragraph, conditioned on what came before, the
way the write verb's autoregressive loop conditions each beat. A filter
is therefore a lens-shaped artifact whose findings are pre-cast as edits,
and which routes through the edit door instead of the guidance queue.

The artifact lives at `<manuscript>/_filters/<name>.md` — a SIBLING
directory to `_lenses/`, not a shared one with a kind marker. Three
reasons, in order of weight:

1. `lens list` and `filter list` must not be able to disagree about a
   file. A shared directory with an in-file kind marker means a
   malformed or absent marker silently reclassifies an artifact — and
   the two kinds have different OUTPUTS, a guidance row versus a
   manuscript edit. Two directories cannot be ambiguous.
2. The underscore prefix is already load-bearing: underscore
   directories are observation-invisible, so a filter prompt can never
   be read as manuscript prose, extracted from, or put in a reading
   order. `_filters/` gets that for free.
3. `lenses.add_lens` is ten lines. The duplication a sibling directory
   costs is smaller than the abstraction a shared one would need, and
   the two artifacts diverge anyway: a filter has front matter and a
   lens has none.

**Why front matter at all.** A lens's prompt fully determines what the
harness does: read the file, ask, gate the answer. A filter's does not —
the CLASS changes the harness's behaviour (whether a prelude call
happens, whether the prefix grows, whether units are independent). That
one fact has to be machine-readable, and it cannot live in prose.

Adding a filter in an existing class is a file and NO code. Inventing a
third class IS code, and this module says so rather than pretending the
class set is open: a design that implies an open set gets handed a
`whole-file` filter within a month.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .lenses import _NAME

FILTER_DIR = "_filters"

# Fixed set, and deliberately not extensible by configuration.
CLASSES = ("sequential", "global")

CLASS_HELP = {
    "sequential": "unit N is judged in the light of every unit before "
                  "it, in the form THIS RUN gave them, with a carried "
                  "STATE; order matters",
    "global": "unit N is judged against the whole pinned essay and a "
              "frozen prelude REGISTRY; units are independent",
}

# `state` is documentation ONLY — it tells the author (in `filter show`)
# and the assistant (in the payload) what this filter's STATE block is
# for. The harness never parses the state itself: the Sponsor's own
# examples are heterogeneous (a word ledger, a metaphor registry, a voice
# note) and any schema imposed here is a schema the next filter's prompt
# has to fight.
#
# `prelude` declares a SEQUENTIAL filter's optional prelude (§15.22
# §3.1). §1.1's table said a sequential filter has no prelude; this
# amends it openly rather than smuggling it. The generalization: a
# prelude is a whole-essay pass that runs before any unit is judged and
# produces a RUN-SCOPED ARTIFACT. For `global` that artifact is the
# frozen registry, rendered into block A. For a sequential filter that
# declares one, the artifact is a set of PROPOSALS — nothing enters block
# A, nothing is frozen into the run beyond a marker that it happened, and
# the run's mechanics are untouched.
#
# Adding a filter that WANTS a pronunciation prelude is code, once. §1's
# "a file and no code" holds for the two classes as they stand; this is
# the first front-matter key added since ratification, and the honest
# statement is that the key set is closed again after it.
PRELUDES = ("pronunciations",)

KEYS = ("class", "state", "prelude", "summaries", "profiles")

DELIMITER = "---"


class FilterError(ValueError):
    """A filter artifact is malformed. Always raised at the moment the
    author writes it (`filter add`) as well as at `filter run`, so a typo
    fails loudly where it was made rather than at the first run."""


def _filter_path(manuscript: dict, name: str) -> Path:
    return Path(manuscript["path"]) / FILTER_DIR / f"{name}.md"


def parse_front_matter(text: str) -> tuple[dict, str]:
    """`(meta, prompt_body)` from a filter artifact.

    Delimiters are `---` lines; the body between them is TOML — the
    project's configuration language (config.toml, toc.toml), parsed
    with the stdlib `tomllib`. No YAML dependency is added for this.

    Every refusal names what is wrong AND the legal values, because the
    author is writing this by hand in their editor and a bare "invalid
    front matter" sends them to read code."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    # Leading blank lines are tolerated; a missing opening delimiter is
    # not guessed at.
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != DELIMITER:
        raise FilterError(
            "a filter needs TOML front matter — the first line must be "
            f"'{DELIMITER}'. The shortest legal filter is:\n\n"
            f"{DELIMITER}\nclass = \"sequential\"\n{DELIMITER}\n\n"
            "# What this filter is for\n…")
    end = None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == DELIMITER:
            end = i
            break
    if end is None:
        raise FilterError(
            f"the front matter is never closed — add a '{DELIMITER}' "
            "line after the last key.")
    try:
        meta = tomllib.loads("\n".join(lines[start + 1:end]))
    except tomllib.TOMLDecodeError as err:
        raise FilterError(
            f"the front matter is not valid TOML: {err}. Keys are "
            "TOML, so string values need quotes: class = \"sequential\"."
        ) from None
    unknown = sorted(k for k in meta if k not in KEYS)
    if unknown:
        # Loudly, at the moment it is written: a typo (`klass`, `type`)
        # that is silently ignored surfaces as a filter that behaves
        # nothing like the one the author thought they ratified.
        raise FilterError(
            f"unknown front-matter key(s): {', '.join(unknown)}. A "
            f"filter's front matter carries "
            f"{', '.join(KEYS[:-1])} and {KEYS[-1]}, nothing else.")
    klass = meta.get("class")
    if klass is None:
        raise FilterError(
            "a filter's front matter must declare its class — one of "
            f"{' or '.join(repr(c) for c in CLASSES)}. A filter is never "
            "silently defaulted into a class: the class decides whether "
            "unit 12 can see unit 11's edit, and guessing that is "
            "guessing about your editorial method.")
    if klass not in CLASSES:
        raise FilterError(
            f"unknown class {klass!r} — the legal values are "
            f"{' and '.join(repr(c) for c in CLASSES)}. "
            + "; ".join(f"{c}: {CLASS_HELP[c]}" for c in CLASSES)
            + ". Inventing a third class is code, not configuration.")
    state = meta.get("state")
    if state is not None and not isinstance(state, str):
        raise FilterError("`state` is a one-line string describing what "
                          "this filter's STATE block is for.")
    prelude = meta.get("prelude")
    if prelude is not None:
        if not isinstance(prelude, str) or prelude not in PRELUDES:
            raise FilterError(
                f"unknown prelude {prelude!r} — the legal value is "
                f"{' and '.join(repr(p) for p in PRELUDES)}. A prelude is "
                "a whole-essay pass that runs before any unit is judged; "
                "'pronunciations' proposes dictionary rows for the terms "
                "of this essay a narrator would stumble over.")
        if klass == "global":
            raise FilterError(
                "a GLOBAL filter's prelude is its frozen registry, and "
                "two outputs for one call is two preludes. Drop "
                "`prelude` here, or make this filter sequential.")
    profiles = meta.get("profiles", [])
    if (not isinstance(profiles, list)
            or not all(isinstance(k, str) and k.strip() for k in profiles)):
        raise FilterError(
            "`profiles` is a list of profile keys — e.g. "
            "profiles = [\"audience\"]. Each names a declared profile whose "
            "text the payload should carry. A key with no profile on record "
            "renders as a labelled absence rather than refusing the run: a "
            "filter must not become unrunnable because a declaration was "
            "renamed.")
    summaries = meta.get("summaries", False)
    if not isinstance(summaries, bool):
        raise FilterError(
            "`summaries` is a boolean — true or false. It declares "
            "whether this filter's payload carries the compressed "
            "summaries of the OTHER essays, before and after this one in "
            "the reading order. Default false: the cross-essay view is "
            "worth real tokens, so a filter asks for it.")
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise FilterError("a filter is its prompt — there is nothing "
                          "after the front matter.")
    return {"class": klass, "state": (state or "").strip(),
            "prelude": prelude, "summaries": summaries,
            "profiles": [k.strip() for k in profiles]}, body


def add_filter(manuscript: dict, name: str, text: str) -> tuple[Path, dict]:
    """Ratify a filter artifact. The whole thing is validated BEFORE
    anything is written, so a refused `filter add` leaves no file."""
    if not _NAME.match(name or ""):
        raise FilterError("filter names are short kebab-case slugs, e.g. "
                          "'duplicate-words'")
    if not (text or "").strip():
        raise FilterError("a filter is its prompt — pass the artifact "
                          "(front matter and prompt) on stdin")
    meta, _body = parse_front_matter(text)
    path = _filter_path(manuscript, name)
    path.parent.mkdir(exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path, meta


_PROSE = re.compile(r"^\s*(#+\s*)?(?P<text>\S.*)$")


def summary_of(body: str) -> str:
    """The artifact's first PROSE line — what `filter list` prints, the
    way `lenses.list_lenses` does. The front matter never appears in it,
    and a leading markdown heading is unwrapped rather than shown with
    its hashes."""
    for line in body.split("\n"):
        m = _PROSE.match(line)
        if m and m.group("text").strip():
            return m.group("text").strip()[:100]
    return ""


def list_filters(manuscript: dict) -> list[dict]:
    """Every artifact in `_filters/`, with its class and summary.

    A malformed artifact is LISTED, with its error where its summary
    would be, rather than hidden: a filter the author wrote and cannot
    see is worse than one they can see is broken."""
    directory = Path(manuscript["path"]) / FILTER_DIR
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        try:
            meta, body = parse_front_matter(text)
        except FilterError as err:
            out.append({"name": path.stem, "class": None, "state": "",
                        "prelude": None, "summaries": False,
                        "profiles": [], "summary": "", "error": str(err)})
            continue
        out.append({"name": path.stem, "class": meta["class"],
                    "state": meta["state"], "prelude": meta["prelude"],
                    "summaries": meta["summaries"],
                    "profiles": meta["profiles"],
                    "summary": summary_of(body), "error": None})
    return out


def load_filter(manuscript: dict, name: str) -> tuple[dict, str]:
    """`(meta, prompt_body)` for a ratified filter, or a LookupError
    naming the filters that do exist."""
    path = _filter_path(manuscript, name)
    if not path.is_file():
        known = ", ".join(f["name"] for f in list_filters(manuscript))
        raise LookupError(
            f"no filter '{name}' (defined: {known or 'none'}). Add one "
            "with 'filter add <name>', the artifact on stdin.")
    return parse_front_matter(path.read_text(encoding="utf-8"))


def show_filter(manuscript: dict, name: str) -> dict:
    path = _filter_path(manuscript, name)
    meta, body = load_filter(manuscript, name)
    return {"name": name, "path": str(path), "class": meta["class"],
            "state": meta["state"], "prelude": meta["prelude"],
            "summaries": meta["summaries"],
            "profiles": meta["profiles"],
            "prompt": body, "class_help": CLASS_HELP[meta["class"]]}
