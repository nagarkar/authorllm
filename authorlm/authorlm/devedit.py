"""Developmental edit: one whole-book developmental edit as a run folder, and the verbs over it.

A run lives at `<manuscript>/_devedit/<run-id>/` and is the only place its
state lives. This module is the only writer of that folder; the
`developmental-editing` skill drives the steps, `authorlm devedit` is the
chat surface, and the audio page's Dev edit mode (`dispatch`) is the review
surface. A run moves through these steps, each recorded in `state.json` with
its status, who ran it (authorlm or a session subagent), the model and effort
the run reported, and the hashes of the files it read:

0. a new run archives every earlier run out of the manuscript folder
   (`archive_runs`), and `audit` lists what must be fixed first.
1. coverage: the files the run edits (every file the TOC lists, front
   matter included) and any the author moves to reference only, confirmed
   by the author before anything runs.
2. stages: the book in stages from the TOC (`stages`): front matter, then
   one stage per part. In each stage, one essay at a time in reading order:
   its summary rebuilt, findings, checks, the author's review, accepted items applied, its
   summary rebuilt, its notebook entry. Then the stage's cross-book
   diagnosis and letter over every stage so far, the author's decisions,
   reruns of the essays the letter touches under the ledger, and the
   author closes the stage.
3. integrate: once, after the last stage, for drift across the book.

Decisions:
- Run state lives in the manuscript folder, `_devedit/<run-id>/`, beside
  `_critiques`, and travels with the manuscript. (owner)
- Coverage comes from `toc.toml` and is confirmed by the author before any
  step runs; only files the TOC lists are covered, and `outside` files never
  are. (owner)
- Nothing in authorlm's stored notes is authoritative: an essay's summary is
  rebuilt at the start of its pass and again after its items are applied,
  never the whole book up front; the term index is computed from the text.
  (owner, 2026-11-09)
- A letter is written at the end of each stage, over every stage so far
  (never the stages still to come), and judges what that much of the book
  delivers against what it promises, maps every concept as introduced, asserted, demonstrated
  or assumed, and lists at most ten problems, each with evidence, why it
  matters, a remedy and its dependencies. (owner)
- Each letter decision is binding or provisional; a later step may raise a
  provisional one for reconsideration, never override either. (owner)
- Strengths the letter names are preserved, not frozen: a later step may
  move a strong passage or add a transition, never weaken what makes it
  strong. (owner)
- An essay agent is given its essay's essay, section and paragraph summaries,
  the essay summaries of the essays already edited (later ones by title
  only), and the terms the edited essays established; it fetches anything
  else through authorlm verbs. (owner, 2026-11-09)
- The term index grows with the edit: it is computed from the finished
  essays only, never the current or later ones. (owner, 2026-11-09)
- Within a run the standard holds still: the ledger records every finding
  as addressed, declined, dropped or open; a rerun or a letter closes what
  was addressed and never reverses, re-raises or raises the bar on a
  closed finding. A new run starts with an empty ledger. (owner, 2026-11-09)
- Essays are edited one at a time in reading order. One essay's pass is:
  findings, checks, the author's review, the accepted items applied, its
  summary rebuilt, then a notebook entry (`note`); the next essay's pack is
  refused until then (`--trial` excepted). The notebook is the shared
  memory: each agent's reasoning and the author's feedback, condensed on
  purpose, readable and correctable by the author. (owner, 2026-11-09)
- Every agent works within the standing context: the file's style law (prose
  aspects only), the audience and market profiles and the example-arcs list;
  it reads manuscript text only through `read`, which serves covered files
  alone (author ruling 2026-11-09: nothing outside the final manuscript); the
  structural style law binds the proposals, sentence-level law waits for the
  line edit. Essay agents run one at a time in reading order, each given the
  arcs the earlier agents proposed. (owner, 2026-11-09)
- The integrator reads every proposed item together after the essay agents,
  from a deterministic grouping by term, concept, dependency and paragraph,
  and makes the items mutually consistent. (owner)
- Items are tiered must, should and could, with no cap. Every item is shown
  with a short reason (`why`, five to ten words); the full reason,
  the finding and the checker's note come when the author asks. (owner,
  2026-11-09, replacing "the reason only when asked")
- A verdict applies the item to the manuscript file only if the original
  text is still there exactly once; otherwise the item is stale and is
  checked again. Enforced by: tests/test_devedit.py::test_apply_refuses_changed_text.

Terms:
- **run**: one developmental edit of one manuscript, the folder
  `_devedit/<run-id>/`.
- **item**: one proposed change: an exact original passage and its proposed
  replacement, or a structural move stated in one line.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import structure
from .terms import Terms

STEPS = ("coverage", "stages", "integrate")
TIERS = ("must", "should", "could")
VERDICTS = ("accept", "reject", "defer")
EFFORTS = ("low", "medium", "high")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ runs

def root(manuscript: dict) -> Path:
    return Path(manuscript["path"]) / "_devedit"


def runs(manuscript: dict) -> list[str]:
    base = root(manuscript)
    return sorted(p.name for p in base.iterdir()
                  if (p / "state.json").exists()) if base.exists() else []


def run_dir(manuscript: dict, run_id: str | None = None) -> Path:
    """The named run, or the latest one. LookupError when there is none."""
    ids = runs(manuscript)
    if run_id is None:
        if not ids:
            raise LookupError("no developmental edit run yet — "
                              "'authorlm devedit new' starts one")
        run_id = ids[-1]
    if run_id not in ids:
        raise LookupError(f"no run '{run_id}' (runs: {', '.join(ids) or 'none'})")
    return root(manuscript) / run_id


ARCHIVE_ROOT = Path.home() / ".authorlm" / "archive"


def archive_runs(manuscript: dict, archive_root: Path | None = None) -> list[Path]:
    """Move every existing run out of the manuscript folder, into
    `<archive_root>/<manuscript>/devedit-<run>`: a new run starts from a
    clean slate, and no agent can read an old run's letter, decisions or
    findings (author ruling 2026-11-09). Moved, never deleted."""
    import shutil

    dest_root = (archive_root or ARCHIVE_ROOT) / manuscript["name"]
    moved = []
    for run_id in runs(manuscript):
        dest = dest_root / f"devedit-{run_id}"
        n = 1
        while dest.exists():
            n += 1
            dest = dest_root / f"devedit-{run_id}-{n}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(root(manuscript) / run_id), str(dest))
        moved.append(dest)
    return moved


def new_run(manuscript: dict, today: str | None = None,
            archive_root: Path | None = None) -> Path:
    """A fresh run folder named by date, with every step pending and the
    coverage proposed from the TOC but not yet confirmed. Every earlier run
    is archived first (`archive_runs`): one run at a time, from a clean
    slate."""
    archived = archive_runs(manuscript, archive_root)
    base = root(manuscript)
    stem = today or datetime.now().strftime("%Y-%m-%d")
    run_id, n = stem, 1
    while (base / run_id).exists():
        n += 1
        run_id = f"{stem}-{n}"
    folder = base / run_id
    files = _disk_files(manuscript)
    state = {"run": run_id, "manuscript": manuscript["name"], "created": _now(),
             "coverage": {**propose_coverage(files), "confirmed": None},
             "steps": {s: {"status": "pending"} for s in STEPS},
             "stages": {}, "archived": [str(p) for p in archived]}
    _write_json(folder / "state.json", state)
    return folder


def state(folder: Path) -> dict:
    return _read_json(folder / "state.json", None) or {}


def _save_state(folder: Path, value: dict) -> None:
    _write_json(folder / "state.json", value)


# ------------------------------------------------------------------ coverage

def _disk_files(manuscript: dict) -> dict[str, str]:
    from .revisions import read_manuscript_files
    return read_manuscript_files(Path(manuscript["path"]))


def propose_coverage(files: dict[str, str]) -> dict[str, list[str]]:
    """`edit`: every file the TOC lists, in reading order: front matter,
    each part, back matter (author ruling 2026-11-09: the front matter is
    the first stage and is edited). `reference` starts empty; the author
    may move a file there before confirming (read, never edited). Files
    declared `outside` are in neither, and neither is a file the TOC does
    not list."""
    toc = files.get(structure.TOC_FILENAME, "")
    listed = {name for name, _depth in structure.parse_toc_tree(toc)}
    order, _unlisted = structure.reading_order(files)
    return {"edit": [n for n in order if n in listed], "reference": []}


def move_coverage(folder: Path, file: str, to: str) -> dict:
    """Move a covered file between `edit` and `reference` before coverage
    is confirmed: the TOC's matter is where a file sits in the book, and
    the author may still want front matter such as a Preface edited."""
    if to not in ("edit", "reference"):
        raise ValueError("a file moves to 'edit' or 'reference'")
    st = state(folder)
    cov = st["coverage"]
    if cov.get("confirmed"):
        raise RuntimeError("coverage is already confirmed; start a new run "
                           "to change it")
    if file not in cov["edit"] + cov["reference"]:
        raise LookupError(f"'{file}' is not covered (the TOC lists it as "
                          "outside, or not at all)")
    other = "reference" if to == "edit" else "edit"
    if file in cov[other]:
        cov[other].remove(file)
        files = _disk_files_for(folder)
        order = {n: i for i, n in enumerate(structure.reading_order(files)[0])}
        cov[to] = sorted(cov[to] + [file], key=lambda n: order.get(n, 999))
    _save_state(folder, st)
    return cov


def _disk_files_for(folder: Path) -> dict[str, str]:
    from .revisions import read_manuscript_files
    return read_manuscript_files(folder.parent.parent)


def confirm_coverage(folder: Path) -> dict:
    st = state(folder)
    st["coverage"]["confirmed"] = _now()
    st["steps"]["coverage"] = {"status": "done", "finished": _now()}
    _save_state(folder, st)
    return st["coverage"]


def covered(folder: Path) -> list[str]:
    cov = state(folder).get("coverage") or {}
    if not cov.get("confirmed"):
        raise RuntimeError("coverage is not confirmed — review it with "
                           "'authorlm devedit coverage' and confirm with "
                           "'authorlm devedit coverage --lock'")
    return list(cov["edit"]) + list(cov["reference"])


# ------------------------------------------------------------------ steps

def mark_step(folder: Path, manuscript: dict, step: str, status: str, *,
              executor: str | None = None, model: str | None = None,
              effort: str | None = None, note: str | None = None) -> dict:
    """Record a step's status. Marking it done records the hash of every
    covered file, so a later edit to one of them shows the step as stale."""
    if step not in STEPS:
        raise ValueError(f"no step '{step}' (one of {', '.join(STEPS)})")
    if status not in ("pending", "running", "done"):
        raise ValueError("status is pending, running or done")
    if effort and effort not in EFFORTS:
        raise ValueError(f"effort is one of {', '.join(EFFORTS)}")
    st = state(folder)
    entry = dict(st["steps"].get(step) or {})
    entry["status"] = status
    if status == "running":
        entry["started"] = _now()
    if status == "done":
        entry["finished"] = _now()
        files = _disk_files(manuscript)
        names = covered(folder) if step != "coverage" else []
        entry["inputs"] = {n: _sha(files[n]) for n in names if n in files}
    for key, value in (("executor", executor), ("model", model),
                       ("effort", effort), ("note", note)):
        if value:
            entry[key] = value
    st["steps"][step] = entry
    _save_state(folder, st)
    return entry


def status(folder: Path, manuscript: dict) -> list[dict]:
    """Each step with its recorded status, shown as stale when a file it
    read has changed since it finished."""
    st = state(folder)
    files = _disk_files(manuscript)
    out = []
    for step in STEPS:
        entry = dict(st["steps"].get(step) or {"status": "pending"})
        changed = [n for n, h in (entry.get("inputs") or {}).items()
                   if _sha(files.get(n, "")) != h]
        if entry["status"] == "done" and changed:
            entry["status"] = "stale"
            entry["changed"] = changed
        out.append({"step": step, **entry})
    return out


def next_step(folder: Path, manuscript: dict) -> str | None:
    for row in status(folder, manuscript):
        if row["status"] != "done":
            return row["step"]
    return None


AGENT_STEPS = ("crossbook", "letter", "essays", "integrate", "check")


def agent_settings(config: dict) -> dict[str, dict]:
    """Each agent step's `session_model`, `model` and `effort` from
    `[devedit]`, for the skill to read before it starts the step's agents.
    A step the table does not name has no setting, and the skill asks."""
    table = config.get("devedit", {}) or {}
    out = {}
    for step in AGENT_STEPS:
        row = table.get(step) or {}
        if row.get("effort") and row["effort"] not in EFFORTS:
            raise ValueError(f"[devedit] {step}: effort '{row['effort']}' is "
                             f"not one of {', '.join(EFFORTS)}")
        out[step] = {k: row[k] for k in ("session_model", "model", "effort")
                     if row.get(k)}
    return out


# ------------------------------------------------------------------ terms

def term_index(folder: Path, manuscript: dict) -> list[dict]:
    """The term index over the essays whose pass is finished, from their
    current (edited) text. It grows as the edit moves through the book: an
    essay's agent sees the terms the finished essays established, never
    terms from its own or later essays (author ruling 2026-11-09). Computed
    on every call, never stored."""
    files = _disk_files(manuscript)
    done = set(finished(folder))
    texts = {n: files[n] for n in covered(folder) if n in done and n in files}
    if not texts:
        return []
    return [{"term": t.name, "count": t.count, "files": t.files,
             "locations": [[loc.file, loc.section, loc.paragraph]
                           for loc in t.locations],
             "definitions": [[loc.file, loc.paragraph, sentence]
                             for loc, sentence in t.definitions]}
            for t in Terms(texts).index(min_count=2, min_files=1)]


# ------------------------------------------------------------------ context packs

def _essay_summary(db, manuscript: dict, file: str) -> str | None:
    row = db.one("SELECT summary FROM essay_summaries WHERE manuscript_id = ? "
                 "AND file = ?", (manuscript["id"], file))
    return row["summary"] if row else None


PROFILES = ("audience", "market", "examples")


def _profile(manuscript: dict, key: str) -> str:
    path = Path(manuscript["path"]) / "_profiles" / f"{key}.md"
    if not path.exists():
        return ""
    # Nest the profile's own headings under the pack's section heading.
    return re.sub(r"^(#+)", r"##\1", path.read_text(encoding="utf-8").strip(),
                  flags=re.M)


def _standing_context(db, manuscript: dict, file: str | None) -> list[str]:
    """What every agent works within: the file's style law (prose aspects,
    when a file is named), the audience and market profiles, and the
    example-arcs list. No other profile reaches an agent: the synopsis, a
    pitch or old drafts are outputs or working files, not sources.
    Each is said to be missing rather than silently left out."""
    from . import styles

    out: list[str] = []
    if file is not None:
        law = styles.render(db, manuscript["id"], file)
        out += ["## Style law for this file", "",
                law or f"(no style guide is attached to {file})", ""]
    titles = {"audience": "Audience: who the prose is written for",
              "market": "Market: positioning and comparable titles "
                        "(audience governs the prose where they differ)",
              "examples": "Example arcs: cases the book tells and develops"}
    for key in PROFILES:
        out += [f"## {titles[key]}", "",
                _profile(manuscript, key) or f"(no _profiles/{key}.md)", ""]
    return out


def _earlier_arcs(folder: Path, names: list[str], file: str) -> list[str]:
    """The arcs essay agents earlier in reading order proposed in this run
    (the `arcs` of their findings files), so this essay can point back to
    a case or develop it a stage further instead of retelling it."""
    out: list[str] = []
    for name in names[:names.index(file)]:
        path = folder / "findings" / f"{Path(name).stem}.json"
        data = _read_json(path, None)
        arcs = data.get("arcs", []) if isinstance(data, dict) else []
        for arc in arcs:
            out.append(f"- **{arc.get('case', '?')}** ({name}, "
                       f"{arc.get('stage', '?')}): {arc.get('summary', '')}")
    if not out:
        return []
    return ["## Arcs proposed earlier in this run (not ratified)", "",
            "Cases earlier agents told. They are not ratified arcs: never "
            "extend one on your own; avoid retelling them; propose an arc to "
            "the author only if a case truly needs to return.", ""] + out + [""]


_FILE_REF = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:md|toml|txt|json))\b")


def _published_text(text: str) -> str:
    """The text with every `[Omit: all]` region removed: what reaches some
    output of the book. A region omitted from every output is the author's
    working note, not the manuscript, so a file it names opens nothing."""
    from .export import _REGION_CLOSE, _REGION_OPEN

    out, skipping = [], False
    for line in text.splitlines():
        if skipping:
            if _REGION_CLOSE.match(line.strip()):
                skipping = False
            continue
        m = _REGION_OPEN.match(line.strip())
        if m and m["kind"].lower() == "omit" and "all" in [
                n.strip().lower() for n in m["names"].split(",")]:
            skipping = True
            continue
        out.append(line)
    return "\n".join(out)


def _resolve_ref(base: Path, name: str, here: Path) -> Path | None:
    """A file name mentioned in a manuscript file, as a path: relative to the
    mentioning file, then to the manuscript folder, then the one file of that
    name anywhere in the manuscript folder. None when it names nothing, or
    several files, or a file outside the manuscript folder."""
    base = base.resolve()
    for cand in (here.parent / name, base / name):
        cand = cand.resolve()
        if cand.is_file():
            return cand if base in cand.parents else None
    if "/" in name:
        return None
    hits = [p for p in base.rglob(name) if p.is_file()]
    return hits[0].resolve() if len(hits) == 1 else None


def sources(folder: Path, manuscript: dict) -> dict[str, str]:
    """The run's core sources, by path in the manuscript folder, each with
    why it is one: "covered" (a file this run edits or reads), "profile" (a
    profile in the pack) or "run" (a file of this run's own folder). Any
    other file is read only by following a reference (`read` with `via`)."""
    base = Path(manuscript["path"]).resolve()
    out: dict[str, str] = {}
    for name in covered(folder):
        if (base / name).is_file():
            out[name] = "covered"
    for key in PROFILES:
        if (base / "_profiles" / f"{key}.md").is_file():
            out[f"_profiles/{key}.md"] = "profile"
    for path in sorted(folder.glob("*")) + sorted(folder.glob("stages/*/*")):
        if path.is_file():
            out[str(path.resolve().relative_to(base))] = "run"
    return out


def mentions(path: Path, base: Path) -> dict[str, str]:
    """The files a source names in its published text, {as written: path in
    the manuscript folder}. Names inside `[Omit: all]` regions, names that
    resolve to nothing or to several files, and files of another
    developmental edit run are left out."""
    base = base.resolve()
    out: dict[str, str] = {}
    if path.suffix != ".md":
        return out
    text = _published_text(path.read_text(encoding="utf-8", errors="replace"))
    for name in set(_FILE_REF.findall(text)):
        hit = _resolve_ref(base, name, path)
        if hit is None:
            continue
        rel = hit.relative_to(base)
        if rel.parts and rel.parts[0] == "_devedit":
            continue
        out[name] = str(rel)
    return out


def _locate(base: Path, file: str, here: Path) -> str | None:
    hit = _resolve_ref(base, file, here)
    return str(hit.relative_to(base.resolve())) if hit else None


def read(folder: Path, manuscript: dict, file: str,
         section: int | None = None, via: list[str] | None = None) -> str:
    """The only door an agent reads the manuscript folder through. A core
    source (`sources`) reads directly, a covered file whole or one section.
    Any other file reads only by following a reference: `via` is the chain
    of files the agent followed, starting at a covered file or a profile
    (never the run's generated files), each naming the
    next in its published text, the last naming `file`. A chain that does
    not hold is an error that says which link is false, so an agent cannot
    claim a reference that is not there. Drafts, exports, critiques and
    other runs that no source names stay closed."""
    from . import summaries as sums

    base = Path(manuscript["path"]).resolve()
    core = sources(folder, manuscript)
    via = list(via or [])

    def as_core(name: str) -> str | None:
        if name in core:
            return name
        hit = _locate(base, name, base / "toc.toml")
        return hit if hit in core else None

    target = as_core(file)
    if target is None:
        if not via:
            raise LookupError(
                f"'{file}' is not a core source of this run; a file a source "
                "names is read by following the reference: --via <the file "
                "that names it>. 'authorlm devedit read' lists the core sources")
        here = as_core(via[0])
        if here is None or core[here] == "run":
            raise LookupError(f"--via '{via[0]}' is not a covered file or a "
                              "profile; a chain of references starts at one "
                              "(the run's own files are generated, and open "
                              "nothing)")
        for nxt in via[1:] + [file]:
            named = mentions(base / here, base)
            hit = named.get(nxt) or next(
                (rel for rel in named.values() if rel == nxt), None)
            if hit is None:
                raise LookupError(f"'{here}' does not name '{nxt}' (outside "
                                  "[Omit: all] notes); that reference is not there")
            here = hit
        target = here
    text = (base / target).read_text(encoding="utf-8", errors="replace")
    if section is None:
        return text
    if core.get(target) != "covered":
        raise LookupError("--section reads a covered file only")
    verse = target in sums.verse_files_of(manuscript)
    for sec in sums.sections_of(text, verse_file=verse):
        if sec["section_ordinal"] == section:
            return "\n\n".join(p["text"] for p in sec["paragraphs"])
    raise LookupError(f"{target} has no section {section}")


def sources_listing(folder: Path, manuscript: dict) -> str:
    rows = sources(folder, manuscript)
    out = []
    for why, title in (("covered", "Covered files (the book this run edits "
                                    "or reads)"),
                       ("profile", "Profiles in the pack"),
                       ("run", "This run's files")):
        names = [n for n, w in rows.items() if w == why]
        if names:
            out += [f"## {title}", ""] + [f"- {n}" for n in names] + [""]
    out += ["Any other file is read only by following a reference: "
            "'devedit read <file> --via <the source that names it>' (a chain "
            "of --via for a file named by a file named by a source).", ""]
    return "\n".join(out)


NOTEBOOK = "notebook.md"
_NOTE_HEAD = re.compile(r"^## (\S+) — ", re.M)


def notebook(folder: Path) -> str:
    path = folder / NOTEBOOK
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def finished(folder: Path) -> list[str]:
    """The essays whose first pass is finished: each has a notebook entry,
    written after the author reviewed its items and they were applied."""
    return list(dict.fromkeys(_NOTE_HEAD.findall(notebook(folder))))


def pass_of(folder: Path, file: str) -> int:
    """The pass an essay is on: one more than its notebook entries. A pass
    after the first is a rerun under the findings ledger (`ledger`)."""
    return _NOTE_HEAD.findall(notebook(folder)).count(file) + 1


def findings_name(folder: Path, file: str) -> str:
    """The findings file an essay's current pass writes, without `.json`:
    the stem for the first pass, `<stem>--p<N>` for a rerun."""
    n = pass_of(folder, file)
    return Path(file).stem if n == 1 else f"{Path(file).stem}--p{n}"


def _file_of_findings(stem: str) -> str:
    return stem.split("--")[0] + ".md"


def note(folder: Path, file: str, text: str) -> str:
    """Close one pass on one essay with its notebook entry: what changed and
    why, the author's reasons where they declined or rewrote, terms fixed,
    arcs started or developed, questions left open. The shared memory every
    later agent reads (author ruling 2026-11-09)."""
    edit = list(state(folder)["coverage"]["edit"])
    if file not in edit:
        raise LookupError(f"'{file}' is not an edited file of this run")
    if not text.strip():
        raise ValueError("a notebook entry needs text")
    n = pass_of(folder, file)
    path = folder / NOTEBOOK
    head = "" if path.exists() else (
        "# Editor's notebook\n\nOne entry per pass on an essay, written when "
        "the pass is finished. Every later agent reads it.\n\n")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{head}## {file} — {_now()[:10]} — pass {n}\n\n"
                 f"{text.strip()}\n\n")
    return file


# ------------------------------------------------------------------ stages
#
# A run moves through the book in stages taken from the TOC, deterministically:
# the front matter is the first stage; each part (a top-level TOC entry with
# chapters under it) is one stage, its chapters with it; a top-level file with
# nothing under it after the first part joins the stage before it. Within a
# stage the essays are edited one at a time in reading order; then a letter
# covers every stage so far, the author answers its decisions, the essays it
# touches are rerun under the ledger, and the author closes the stage. The
# next stage's first essay waits for that close (author ruling 2026-11-09).

def _title_of(text: str, file: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return re.sub(r"[*_]", "", m.group(1)).strip() if m else file


def stages(folder: Path) -> list[dict]:
    """The run's stages: [{n, name, files: [edited and reference files, in
    reading order], edit: [...], closed}]. Built from the TOC and this run's
    coverage, so the same book always stages the same way."""
    files = _disk_files_for(folder)
    toc = files.get(structure.TOC_FILENAME, "")
    st = state(folder)
    cov = st["coverage"]
    in_run = set(cov["edit"]) | set(cov["reference"])
    tree = structure.parse_toc_tree(toc)
    tops: list[tuple[str, list[str]]] = []
    for i, (name, depth) in enumerate(tree):
        if depth == 0:
            tops.append((name, [name]))
        elif tops:
            tops[-1][1].append(name)
    groups: list[tuple[str, list[str]]] = []
    front: list[str] = []
    seen_part = False
    for name, members in tops:
        is_part = len(members) > 1
        if not seen_part and not is_part:
            front.append(name)
            continue
        if is_part:
            seen_part = True
            groups.append((_title_of(files.get(name, ""), name), list(members)))
        elif groups:
            groups[-1][1].append(name)
    if front:
        groups.insert(0, ("Front matter", front))
    closed = st.get("stages") or {}
    out = []
    for n, (name, members) in enumerate(groups):
        names = [m for m in members if m in in_run]
        if not names:
            continue
        out.append({"n": len(out), "name": name, "files": names,
                    "edit": [m for m in names if m in cov["edit"]],
                    "closed": (closed.get(str(len(out))) or {}).get("closed")})
    return out


def stage_of(folder: Path, file: str) -> dict | None:
    return next((s for s in stages(folder) if file in s["files"]), None)


def current_stage(folder: Path) -> dict | None:
    """The first stage not yet closed; None when every stage is closed."""
    return next((s for s in stages(folder) if not s["closed"]), None)


def stage_dir(folder: Path, n: int | None = None) -> Path:
    """Where a stage's letter, argument map, cross-book diagnosis and
    decisions live: `stages/<n>/`. Default: the current stage, else the
    last one."""
    if n is None:
        cur = current_stage(folder)
        rows = stages(folder)
        n = cur["n"] if cur else (rows[-1]["n"] if rows else 0)
    return folder / "stages" / str(n)


def stage_status(folder: Path) -> list[dict]:
    done = set(finished(folder))
    out = []
    for s in stages(folder):
        d = stage_dir(folder, s["n"])
        rows = _read_json(d / "decisions.json", [])
        out.append({**s, "finished": [f for f in s["edit"] if f in done],
                    "letter": (d / "letter.md").exists(),
                    "decisions": len(rows),
                    "answered": sum(1 for r in rows if r.get("answered"))})
    return out


def close_stage(folder: Path, n: int) -> dict:
    """The author closes a stage once every essay in it is finished, its
    letter is written and every decision is answered. The next stage's
    first essay waits for this."""
    cur = current_stage(folder)
    if cur is None or cur["n"] != n:
        raise ValueError(f"stage {n} is not the current stage"
                         + (f" (stage {cur['n']}, {cur['name']}, is)" if cur else ""))
    row = next(s for s in stage_status(folder) if s["n"] == n)
    waiting = [f for f in row["edit"] if f not in row["finished"]]
    if waiting:
        raise RuntimeError(f"stage {n} has essays without a finished pass: "
                           + ", ".join(waiting))
    if not row["letter"]:
        raise RuntimeError(f"stage {n} has no letter yet "
                           f"({stage_dir(folder, n) / 'letter.md'})")
    if row["answered"] < row["decisions"]:
        raise RuntimeError(f"stage {n} has {row['decisions'] - row['answered']} "
                           "unanswered decision(s)")
    st = state(folder)
    st.setdefault("stages", {})[str(n)] = {"closed": _now()}
    _save_state(folder, st)
    return {**row, "closed": st["stages"][str(n)]["closed"]}


def _gate(folder: Path, file: str) -> None:
    """Essays are edited one at a time, in reading order, stage by stage: an
    essay's pass starts only when every earlier stage is closed and every
    earlier essay has its notebook entry. A rerun of a finished essay is
    always open."""
    edit = list(state(folder)["coverage"]["edit"])
    if file not in edit:
        return
    mine = stage_of(folder, file)
    for s in stages(folder):
        if mine and s["n"] < mine["n"] and not s["closed"]:
            raise RuntimeError(
                f"{file} is in stage {mine['n']} ({mine['name']}); stage "
                f"{s['n']} ({s['name']}) is not closed: its letter, the "
                "author's decisions, the reruns, then 'devedit stage close "
                f"{s['n']}'")
    done = set(finished(folder))
    waiting = [f for f in edit[:edit.index(file)] if f not in done]
    if waiting:
        raise RuntimeError(f"{file} waits for {waiting[0]}: finish its pass "
                           "(review, apply, rebuild its summary, 'devedit "
                           "note') first, or pass --trial for a one-off trial")


# ------------------------------------------------------------------ arcs
#
# The arc step runs on an essay before its developmental edit (author
# ruling 2026-11-10). A dedicated arc agent judges the essay's current case
# (does it make the claim; does it assume what the essay says comes later;
# does it carry the point or decorate it), proposes two or three candidates
# (new, or the next stage of an existing arc) and asks the author questions.
# The author answers in chat or on the page; both write one record,
# `stages/<n>/arcs/<stem>.json`. Settling an arc approves it for now: it is
# written into the example-arcs list at once, and stays revisable. "No arc"
# is a valid outcome with a reason. The essay's edit pass takes the settled
# arc as given. Sermons and Papers skip the step. On an essay whose stage is
# closed the step is an arc revision: a settled change becomes ordinary
# items in `findings/<stem>--arc.json`, and nothing else is reopened.

ARC_EXEMPT_GUIDES = ("Sermons", "Papers")
ARC_OUTCOMES = ("arc", "none", "keep")


def arc_exempt(db, manuscript: dict, file: str) -> bool:
    """Sermons and Papers have no arc step: the arcs rule does not apply."""
    row = db.one("SELECT g.name FROM style_attachments a JOIN style_guides g "
                 "ON g.id = a.guide_id WHERE a.manuscript_id = ? AND a.file = ?",
                 (manuscript["id"], file))
    return bool(row) and str(row["name"]).startswith(ARC_EXEMPT_GUIDES)


def arc_path(folder: Path, file: str, stage: dict | None = None) -> Path:
    stage = stage or stage_of(folder, file)
    if stage is None:
        raise LookupError(f"'{file}' is in no stage of this run")
    return stage_dir(folder, stage["n"]) / "arcs" / f"{Path(file).stem}.json"


def arc_record(folder: Path, file: str, stage: dict | None = None) -> dict:
    stage = stage or stage_of(folder, file)
    if stage is None:
        raise LookupError(f"'{file}' is in no stage of this run")
    default = {"file": file,
               "pass": "arc-revision" if stage["closed"] else "arc",
               "rounds": [], "answers": [], "settled": None}
    return _read_json(arc_path(folder, file, stage), default)


def _save_arc(folder: Path, record: dict) -> dict:
    _write_json(arc_path(folder, record["file"]), record)
    return record


def arc_propose(folder: Path, file: str, data: dict) -> dict:
    """Record one round of the arc agent: `{judgment, candidates,
    questions}`. Each candidate: `{case, kind, stage, story, moral, fit,
    weakness, source, text}`; `stage` is "starts" or "develops"."""
    if not isinstance(data, dict):
        raise ValueError("an arc round is a JSON object")
    cands = data.get("candidates") or []
    for c in cands:
        missing = [k for k in ("case", "story", "moral", "fit") if not c.get(k)]
        if missing:
            raise ValueError(f"candidate '{c.get('case', '?')}' lacks "
                             + ", ".join(missing))
    rec = arc_record(folder, file)
    if rec.get("settled"):
        rec["settled"] = None  # a new round reopens a settled arc
    rec["rounds"].append({"at": _now(), "judgment": data.get("judgment", ""),
                          "candidates": cands,
                          "questions": data.get("questions") or []})
    return _save_arc(folder, rec)


def arc_answer(folder: Path, file: str, text: str, via: str = "chat") -> dict:
    """The author's answer, in their own words, to the latest round."""
    if not text.strip():
        raise ValueError("an answer needs text")
    rec = arc_record(folder, file)
    rec["answers"].append({"at": _now(), "round": len(rec["rounds"]),
                           "text": text.strip(), "via": via})
    return _save_arc(folder, rec)


def arc_settle(folder: Path, manuscript: dict, file: str, outcome: str, *,
               candidate: int | None = None, arc: dict | None = None,
               reason: str = "", via: str = "chat") -> dict:
    """Settle the essay's arc. `outcome` is "arc" (a candidate by its
    1-based number in the latest round, or an `arc` dict), "keep" (the
    current case stands) or "none" (no case; `reason` says why). An "arc"
    is approved for now and written into the example-arcs list."""
    if outcome not in ARC_OUTCOMES:
        raise ValueError(f"outcome must be one of {', '.join(ARC_OUTCOMES)}")
    rec = arc_record(folder, file)
    chosen = None
    if outcome == "arc":
        if arc is not None:
            chosen = dict(arc)
        else:
            if not rec["rounds"] or candidate is None:
                raise ValueError("settle an arc with a candidate number or an arc")
            cands = rec["rounds"][-1]["candidates"]
            if not 1 <= candidate <= len(cands):
                raise ValueError(f"candidate {candidate} is not in the latest "
                                 f"round (1–{len(cands)})")
            chosen = dict(cands[candidate - 1])
        for k in ("case", "story", "moral", "fit"):
            if not chosen.get(k):
                raise ValueError(f"the settled arc lacks {k}")
    elif outcome == "none" and not reason.strip():
        raise ValueError("'none' needs a reason")
    rec["settled"] = {"at": _now(), "outcome": outcome, "arc": chosen,
                      "reason": reason.strip(), "via": via}
    _save_arc(folder, rec)
    if chosen:
        approve_arc(manuscript, file, chosen)
    return rec


def approve_arc(manuscript: dict, file: str, arc: dict) -> None:
    """Write a settled arc into `_profiles/examples.md` under "Ratified
    arcs": a new entry, or a stage line on an existing one. Approved for
    now; any entry stays revisable."""
    path = Path(manuscript["path"]) / "_profiles" / "examples.md"
    text = path.read_text(encoding="utf-8") if path.exists() else (
        "# Example arcs\n\n## Ratified arcs\n\n## Candidates to review (not binding)\n")
    stage = "Develops" if arc.get("stage") == "develops" else "Starts"
    line = f"- {stage}: {file}: {arc['fit']}"
    head = f"### {arc['case']}"
    cand = text.find("\n## Candidates to review")
    ratified_end = cand if cand >= 0 else len(text)
    at = text.find(head, 0, ratified_end)
    if at >= 0:
        nxt = text.find("\n### ", at + len(head))
        end = min(e for e in (nxt if nxt >= 0 else len(text), ratified_end) if e >= 0)
        block = text[at:end].rstrip("\n")
        if line not in block:
            text = text[:at] + block + "\n" + line + "\n" + text[end:]
    else:
        kind = arc.get("kind") or "case"
        entry = (f"{head} ({kind})\n- Approved by the author {_now()[:10]} "
                 f"(arc step).\n- Summary: {arc['story']} Moral: {arc['moral']}\n"
                 + (f"- Source: {arc['source']}\n" if arc.get("source") else "")
                 + line + "\n\n")
        text = text[:ratified_end].rstrip("\n") + "\n\n" + entry + text[ratified_end:].lstrip("\n")
    path.write_text(text, encoding="utf-8")


def arc_status(db, manuscript: dict, folder: Path) -> list[dict]:
    """Every covered edited essay in reading order with its arc state:
    exempt, settled (and how), open (a round waits for the author) or
    not started."""
    out = []
    # Stages are computed once: each call reads the TOC and every file.
    by_file = {f: s for s in stages(folder) for f in s["files"]}
    guides = {r["file"]: r["name"] for r in db.all(
        "SELECT a.file, g.name FROM style_attachments a JOIN style_guides g "
        "ON g.id = a.guide_id WHERE a.manuscript_id = ?", (manuscript["id"],))}
    for f in state(folder)["coverage"]["edit"]:
        if str(guides.get(f, "")).startswith(ARC_EXEMPT_GUIDES):
            out.append({"file": f, "state": "exempt"})
            continue
        if f not in by_file:
            continue
        rec = arc_record(folder, f, by_file[f])
        st = ("settled" if rec.get("settled") else
              "open" if rec["rounds"] else "not started")
        out.append({"file": f, "state": st, "pass": rec["pass"],
                    "outcome": (rec.get("settled") or {}).get("outcome")})
    return out


def arc_findings_name(file: str) -> str:
    """The findings file a settled arc's items go to: `<stem>--arc`."""
    return f"{Path(file).stem}--arc"


def _arcs_line(summary: str | None) -> str:
    m = re.search(r"^ARCS:\s*(.*)$", summary or "", re.M)
    return m.group(1).strip() if m else "(no ARCS line)"


def arc_pack(db, manuscript: dict, folder: Path, file: str) -> str:
    """What the arc agent is given: the standing context (style law,
    audience, market, the example-arcs list), the arc sources, the arcs of
    every essay before this one in reading order (never later ones: what
    each tells, its moral and how it serves its essay, settled or not),
    this essay's full text, and this essay's arc record so far."""
    names = covered(folder)
    if file not in names:
        raise LookupError(f"'{file}' is not covered by this run")
    rec = arc_record(folder, file)
    out = [f"# Arc context for {file}", "",
           f"Arc step ({rec['pass']}). Record your round with "
           f"'authorlm devedit arc propose {file} --json <file>'.", ""]
    out += _standing_context(db, manuscript, file)
    out += ["## Arc sources (candidates, not binding)", "",
            _profile(manuscript, "arc-sources") or "(no _profiles/arc-sources.md: "
            "ask the author what the source of stories, quotes and metaphors is)", ""]
    out += ["## Arcs told so far, in reading order", ""]
    for other in names[:names.index(file)]:
        try:
            orec = arc_record(folder, other)
        except LookupError:
            orec = {"settled": None}
        settled = orec.get("settled")
        tag = (f"settled ({settled['outcome']})" if settled else "not settled yet")
        out.append(f"- **{other}** [{tag}]: {_arcs_line(_essay_summary(db, manuscript, other))}")
        if settled and settled.get("arc"):
            a = settled["arc"]
            out.append(f"  - settled arc: {a['case']}: {a['story']} Moral: "
                       f"{a['moral']}. Serves: {a['fit']}")
        if settled and settled["outcome"] == "none":
            out.append(f"  - no arc: {settled['reason']}")
    out += ["", "## This essay's summary (ARCS line)", "",
            _arcs_line(_essay_summary(db, manuscript, file)), "",
            "## This essay", "", _disk_files(manuscript)[file].strip(), ""]
    if rec["rounds"] or rec["answers"]:
        out += ["## This essay's arc record so far", "",
                "```json", json.dumps({k: rec[k] for k in ("rounds", "answers", "settled")},
                                      ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ audit

def audit(db, manuscript: dict, folder: Path) -> list[str]:
    """What must be fixed before a run starts, found mechanically: a profile
    the pack carries that is missing; an edited file with no style guide;
    an active prose style rule that cites a retired rule; a profile or
    prose style rule that names a file the TOC declares outside the book,
    by its file name or its title. The agent-read audit (staleness against
    the author's rulings) is the skill's step 0 and builds on this list."""
    from . import styles

    base = Path(manuscript["path"])
    files = _disk_files(manuscript)
    toc = files.get(structure.TOC_FILENAME, "")
    problems: list[str] = []
    for key in PROFILES:
        if not (base / "_profiles" / f"{key}.md").exists():
            problems.append(f"profile missing: _profiles/{key}.md")
    cov = state(folder)["coverage"]
    attached = {r["file"] for r in db.all(
        "SELECT file FROM style_attachments WHERE manuscript_id = ?",
        (manuscript["id"],))}
    for name in cov["edit"]:
        if name not in attached:
            problems.append(f"no style guide attached: {name} (it would get "
                            "the house rules only)")
    laws = [dict(r) for r in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? AND status = 'active'",
        (manuscript["id"],)) if r["aspect"] in styles.PROSE_ASPECTS]
    active = {r["id"][:8] for r in laws}
    for r in laws:
        for ref in set(re.findall(r"se-[0-9a-f]{5}", r["statement"])):
            if ref not in active:
                problems.append(f"style rule {r['id'][:8]} cites {ref}, "
                                "which is retired")
    outside_names: dict[str, str] = {}
    m = re.search(r"^outside\s*=\s*\[(.*?)\]", toc, re.M | re.S)
    for item in re.findall(r'"([^"]+)"', m.group(1) if m else ""):
        if item.endswith(".md") and item in files:
            title = re.sub(r"^(The|A|An)\s+", "", _title_of(files[item], item))
            outside_names[item] = title.split(":")[0].strip()
    texts = {f"_profiles/{k}.md": _profile(manuscript, k) for k in PROFILES}
    texts.update({f"style rule {r['id'][:8]}": r["statement"] for r in laws})
    for where, text in texts.items():
        for item, title in outside_names.items():
            hit = (re.search(rf"\b{re.escape(item)}\b", text)
                   or (len(title) > 3 and re.search(rf"\b{re.escape(title)}\b", text)))
            if hit:
                problems.append(f"{where} names {hit.group(0)!r}, which the TOC "
                                f"declares outside the book ({item})")
    return problems


# ------------------------------------------------------------------ ledger

def ledger(folder: Path, file: str | None = None) -> list[dict]:
    """Every finding of this run with what became of it, from the gathered
    items, the checks and the author's verdicts: "addressed" (an item was
    accepted), "declined" (the author rejected every item, with reasons),
    "dropped" (the checker dropped every item), or "open". The standard a
    run holds itself to: a rerun or a letter reads it, closes what was
    addressed, and never reverses or raises the bar on a closed finding."""
    items = _read_json(folder / "items.json", [])
    q = {i["id"]: i for i in queue(folder)}
    groups: dict[str, list[dict]] = {}
    for item in items:
        key = item["id"].rsplit(".", 1)[0]
        groups.setdefault(key, []).append(item)
    out = []
    for key, members in groups.items():
        f = members[0]["file"]
        if file and f != file:
            continue
        in_queue = [i for i in members if i["id"] in q]
        verdicts = [q[i["id"]].get("verdict") for i in in_queue]
        if "accept" in verdicts:
            status_ = "addressed"
        elif not in_queue and all((folder / "checks" / f"{i['id']}.json").exists()
                                  for i in members):
            status_ = "dropped"
        elif in_queue and all(v == "reject" for v in verdicts):
            status_ = "declined"
        else:
            status_ = "open"
        out.append({"key": key, "file": f, "finding": members[0].get("finding", ""),
                    "tier": min((i.get("tier", "could") for i in members),
                                key=TIERS.index),
                    "status": status_,
                    "reasons": [q[i["id"]].get("reason_given", "")
                                for i in in_queue
                                if q[i["id"]].get("verdict") == "reject"
                                and q[i["id"]].get("reason_given")]})
    return out


def _ledger_lines(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        why = f" Author: {'; '.join(r['reasons'])}" if r["reasons"] else ""
        out.append(f"- [{r['status']}] ({r['file']}, {r['tier']}) "
                   f"{r['finding']}{why}")
    return out


def essay_pack(db, manuscript: dict, folder: Path, file: str,
               trial: bool = False) -> str:
    """What an essay agent is given (Markdown): the file's effective style
    law (prose aspects), the audience profile, the example-arcs list and
    the arcs earlier essays' agents proposed in this run; this essay's
    essay summary, each section's summary with its paragraph gists, every
    other covered essay's essay summary, and the term index entries for
    this essay's terms with where else they occur."""
    from . import summaries as sums

    names = covered(folder)
    if file not in names:
        raise LookupError(f"'{file}' is not covered by this run")
    files = _disk_files(manuscript)
    text = files[file]
    verse = file in sums.verse_files_of(manuscript)
    if not trial:
        _gate(folder, file)
    n = pass_of(folder, file)
    if not trial and n == 1 and not arc_exempt(db, manuscript, file) \
            and not arc_record(folder, file).get("settled"):
        raise RuntimeError(f"{file}: its arc is not settled yet — run the arc "
                           f"step first ('devedit arc pack {file}')")
    stage = stage_of(folder, file)
    out = [f"# Context for {file}", "",
           f"Pass {n} on this essay"
           + (f", stage {stage['n']} ({stage['name']})" if stage else "")
           + f". Write your findings to findings/{findings_name(folder, file)}.json.",
           ""]
    if trial:
        out += ["(Trial pack: earlier essays' passes may not be finished.)", ""]
    out += _standing_context(db, manuscript, file)
    book = re.sub(r"\A# [^\n]*\n+", "", notebook(folder))
    out += ["## Editor's notebook", "",
            re.sub(r"^(#+)", r"#\1", book, flags=re.M) if book
            else "(empty: this is the first essay's pass)", ""]
    out += _earlier_arcs(folder, names, file)
    settled = arc_record(folder, file).get("settled") if stage else None
    if settled:
        a = settled.get("arc")
        out += ["## This essay's settled arc (the author's; take it as given)", "",
                (f"**{a['case']}** ({a.get('stage', 'starts')}): {a['story']} "
                 f"Moral: {a['moral']}. Serves: {a['fit']}"
                 + (f"\n\nText: {a['text']}" if a.get("text") else "")) if a else
                ("Keep the essay's current case." if settled["outcome"] == "keep"
                 else f"No case in this essay: {settled['reason']}"),
                "", "Do not add, replace or extend a case; revise only the "
                "wording of the settled one.", ""]
    mine = ledger(folder, file)
    if mine or n > 1:
        out += [f"## The ledger for this essay (pass {n})", "",
                "Earlier findings on this essay and what became of them. "
                "Check each open one first; never reverse, re-raise or raise "
                "the bar on one that is addressed, declined or dropped.", ""]
        out += _ledger_lines(mine) or ["(no earlier findings)"]
        out.append("")
    if stage:
        letter = stage_dir(folder, stage["n"]) / "letter.md"
        if letter.exists():
            out += [f"## The letter for stage {stage['n']}", "",
                    re.sub(r"^(#+)", r"##\1", letter.read_text(encoding="utf-8").strip(),
                           flags=re.M), ""]
    out += ["## This essay", "",
           _essay_summary(db, manuscript, file) or "(no essay summary)", ""]
    sections = sums._current_section_rows(db, manuscript["id"], file)
    records = sums._current_paragraph_rows(db, manuscript["id"], file)
    for sec in sums.sections_of(text, verse_file=verse):
        row = sections.get(sec["section_hash"])
        out += [f"### §{sec['section_ordinal']} {sec['heading'] or '(preamble)'}",
                "", (row["summary"] if row else "(no section summary)"), ""]
        for p in sec["paragraphs"]:
            if p["kind"] == "heading":
                continue
            rec = records.get(p["para_hash"])
            gist = sums._record_of(rec)["gist"] if rec else "(no record)"
            out.append(f"- ¶{p['ordinal']}: {gist}")
        out.append("")
    done = [f for f in names if f in set(finished(folder)) and f != file]
    out += ["## The essays already edited", ""]
    for other in done:
        out += [f"### {other}", "",
                _essay_summary(db, manuscript, other) or "(no essay summary)", ""]
    if not done:
        out += ["(none yet)", ""]
    later = [f for f in names if f not in done and f != file]
    if later:
        out += ["## Still to come (titles only, not yet edited)", ""]
        out += [f"- {f}: {_title_of(files.get(f, ''), f)}" for f in later]
        out.append("")
    terms = term_index(folder, manuscript)
    used = [t for t in terms if re.search(rf"\b{re.escape(t['term'])}", text)]
    out += ["## Terms the edited essays established, that this essay uses", ""]
    for t in used:
        defs = [d[2] for d in t["definitions"]][:1]
        out.append(f"- **{t['term']}** ({', '.join(t['files'])})"
                   + (f": {defs[0]}" if defs else ""))
    if not used:
        out.append("(none yet)")
    return "\n".join(out) + "\n"


def book_pack(db, manuscript: dict, folder: Path, stage: int | None = None) -> str:
    """What a stage's cross-book agent and letter are given: the standing
    context, the editor's notebook, the ledger, and the essay and section
    summaries of every file in this stage and the stages before it, in
    reading order; never the stages still to come."""
    from . import summaries as sums

    files = _disk_files(manuscript)
    rows = stages(folder)
    cur = current_stage(folder)
    upto = stage if stage is not None else (cur["n"] if cur else
                                            (rows[-1]["n"] if rows else 0))
    scope = [f for s in rows if s["n"] <= upto for f in s["files"]]
    out = [f"# The book so far: stages 0 to {upto}", ""]
    out += _standing_context(db, manuscript, None)
    book = re.sub(r"\A# [^\n]*\n+", "", notebook(folder))
    out += ["## Editor's notebook", "",
            re.sub(r"^(#+)", r"#\1", book, flags=re.M) if book else "(empty)", ""]
    out += ["## The ledger", "",
            "Every finding so far and what became of it. A letter never "
            "reopens one that is addressed, declined or dropped.", ""]
    out += _ledger_lines([r for r in ledger(folder) if r["file"] in scope]) \
        or ["(no findings yet)"]
    out.append("")
    for name in scope:
        text = files.get(name, "")
        verse = name in sums.verse_files_of(manuscript)
        out += [f"## {name}", "", _essay_summary(db, manuscript, name)
                or "(no essay summary)", ""]
        sections = sums._current_section_rows(db, manuscript["id"], name)
        for sec in sums.sections_of(text, verse_file=verse):
            row = sections.get(sec["section_hash"])
            if row:
                out += [f"### §{sec['section_ordinal']} "
                        f"{sec['heading'] or '(preamble)'}", "", row["summary"], ""]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ letter decisions

def decisions(folder: Path, stage: int | None = None) -> list[dict]:
    """A stage's letter decisions (default: the current stage), each {n,
    question, options, recommended, answer, reason, binding, answered}."""
    return _read_json(stage_dir(folder, stage) / "decisions.json", [])


def decide(folder: Path, n: int, answer: str, reason: str = "",
           binding: bool = True, stage: int | None = None) -> dict:
    rows = decisions(folder, stage)
    row = next((r for r in rows if r["n"] == n), None)
    if row is None:
        raise LookupError(f"no decision {n}")
    if row.get("options") and answer not in row["options"]:
        raise ValueError(f"decision {n}'s answer is one of: "
                         + ", ".join(row["options"]))
    row.update(answer=answer, reason=reason.strip(), binding=binding,
               answered=_now())
    _write_json(stage_dir(folder, stage) / "decisions.json", rows)
    if "_manuscript" in _APPLY_CTX:
        row["applied"] = _apply_decision(folder, _APPLY_CTX["_manuscript"],
                                         row, stage)
        _write_json(stage_dir(folder, stage) / "decisions.json", rows)
    return row


# The manuscript a decision's edits apply to; set by `decide_and_apply`.
_APPLY_CTX: dict = {}


def decide_and_apply(folder: Path, manuscript: dict, n: int, answer: str,
                     reason: str = "", binding: bool = True,
                     stage: int | None = None) -> dict:
    """Answer a decision and apply the exact edits the letter attached to
    the chosen option (`edits`: {option: [{file, original, proposed}]}), as
    accepting an item does: each lands in the local file only if its
    original occurs exactly once, and goes into the queue as an accepted,
    applied item so the ledger, the push list and the page see it. An
    option with no edits needs drafting: its essays are rerun (author ruling
    2026-11-09)."""
    _APPLY_CTX["_manuscript"] = manuscript
    try:
        return decide(folder, n, answer, reason, binding, stage)
    finally:
        _APPLY_CTX.clear()


def _apply_decision(folder: Path, manuscript: dict, row: dict,
                    stage: int | None) -> list[dict]:
    edits = (row.get("edits") or {}).get(row["answer"]) or []
    if not edits:
        return []
    s = stage if stage is not None else int(stage_dir(folder).name)
    items = queue(folder)
    out = []
    for k, e in enumerate(edits, 1):
        item = {"id": f"stage{s}-d{row['n']}.{k}", "file": e["file"],
                "tier": "must", "original": e["original"],
                "proposed": e["proposed"],
                "why": e.get("why") or f"Your decision {row['n']}: {row['answer']}",
                "reason": f"Decision {row['n']}: {row['question']} Answer: {row['answer']}",
                "finding": row["question"], "order": 0}
        try:
            apply_item(manuscript, item)
        except RuntimeError as err:
            out.append({"id": item["id"], "file": e["file"], "error": str(err)})
            continue
        now = _now()
        item.update(verdict="accept", final=e["proposed"], decided=now,
                    applied=now, pushed=None, reason_given=row.get("reason", ""))
        items = [i for i in items if i["id"] != item["id"]] + [item]
        out.append({"id": item["id"], "file": e["file"]})
    _write_json(folder / "queue.json", items)
    return out


# ------------------------------------------------------------------ items
#
# The agents write JSON; these functions are the deterministic steps between
# them. An essay agent writes `findings/<file>.json`: {findings, arcs}, where
# findings is a list, each {finding, letter_ref, items: [{tier, original,
# proposed, move, reason}]}, and arcs lists the cases the essay starts or
# develops once its items are applied, each {case, stage, summary} (stage is
# "starts" or "develops"). A bare list of findings is read as {findings}.
# Essay agents run one at a time in reading order, so each pack carries the
# arcs every earlier agent proposed (`_earlier_arcs`).
# `gather` numbers every item into `items.json`; `group` writes the
# integrator's `groups.json`; the integrator writes `integrated.json` (items
# kept, edited, merged or dropped, with `links`); each checker writes
# `checks/<id>.json` {result: pass|fix|drop|reconsider, proposed?, reason};
# `build_queue` turns what passed into `queue.json`.

def _first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", (text or "").strip(), re.S)
    return (m.group(1) if m else (text or "")).strip()


def why_of(item: dict, limit: int = 12) -> str:
    """The short reason shown with every item in the review: the agent's
    `why` (five to ten words), else the reason's first words."""
    text = item.get("why") or _first_sentence(item.get("reason", ""))
    words = text.split()
    return " ".join(words[:limit]) + (" …" if len(words) > limit else "")


def _findings_of(data: Any) -> list[dict]:
    """The findings in a findings file: the file itself when it is a bare
    list, else its `findings`."""
    return data if isinstance(data, list) else (data or {}).get("findings", [])


def gather(folder: Path) -> list[dict]:
    order = {name: i for i, name in enumerate(covered(folder))}
    items: list[dict] = []
    for path in sorted((folder / "findings").glob("*.json"),
                       key=lambda p: (order.get(_file_of_findings(p.stem), 999),
                                      p.stem)):
        file = _file_of_findings(path.stem)
        for f_i, finding in enumerate(_findings_of(_read_json(path, []))):
            for i_i, item in enumerate(finding.get("items") or []):
                tier = item.get("tier", "could")
                if tier not in TIERS:
                    raise ValueError(f"{path.name}: tier '{tier}' is not one of "
                                     + ", ".join(TIERS))
                items.append({
                    "id": f"{path.stem}-{f_i + 1}.{i_i + 1}", "file": item.get("file", file),
                    "tier": tier, "original": item.get("original"),
                    # An original with no proposal is a cut: proposed "".
                    "proposed": (item.get("proposed") or "")
                    if item.get("original") else item.get("proposed"),
                    "move": item.get("move"),
                    "reason": item.get("reason", ""),
                    "why": item.get("why") or "",
                    "finding": finding.get("finding", ""),
                    "letter_ref": finding.get("letter_ref"),
                    "order": order.get(item.get("file", file), 999) * 10000
                             + _position(folder, item.get("file", file),
                                         item.get("original"))})
    _write_json(folder / "items.json", items)
    return items


def _position(folder: Path, file: str, original: str | None) -> int:
    """Where the original text sits in its file (characters / 100), so the
    review walks a file front to back; 0 when it cannot be found."""
    if not original:
        return 0
    try:
        body = (folder.parent.parent / file).read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    at = body.find(original)
    return max(at, 0) // 100


def group(folder: Path) -> list[dict]:
    """The integrator's input: items grouped wherever two of them could
    conflict — they use the same term, touch the same concept of the
    argument map, sit where one concept is relied on and another is
    justified, or touch the same paragraph. Groups of one are left out."""
    items = _read_json(folder / "items.json", [])
    files = _disk_files_for(folder)
    done = set(finished(folder))
    texts = {n: files[n] for n in covered(folder) if n in done and n in files}
    terms = [t.name for t in Terms(texts).index(min_count=2, min_files=1)] \
        if texts else []
    concepts = [c for path in sorted(folder.glob("stages/*/argument-map.json"))
                for c in _read_json(path, [])]
    groups: dict[tuple[str, str], set[str]] = {}

    def add(kind: str, key: str, item_id: str) -> None:
        groups.setdefault((kind, key), set()).add(item_id)

    from .terms import mention_pattern
    patterns = {t: mention_pattern(t) for t in terms}
    for item in items:
        text = " ".join(x for x in (item.get("original"), item.get("proposed"),
                                    item.get("move")) if x)
        for term, pattern in patterns.items():
            if pattern.search(text):
                add("term", term, item["id"])
        for c in concepts:
            name = c.get("concept", "")
            if name and mention_pattern(name).search(text):
                add("concept", name, item["id"])
            related = {c.get("introduced"), c.get("justified"),
                       *(c.get("used_by") or [])}
            if name and item["file"] in related:
                add("dependency", name, item["id"])
        if item.get("original"):
            add("paragraph", f"{item['file']}@{item['order']}", item["id"])
    out = [{"kind": kind, "key": key, "items": sorted(ids)}
           for (kind, key), ids in sorted(groups.items()) if len(ids) > 1]
    _write_json(folder / "groups.json", out)
    return out


def build_queue(folder: Path) -> list[dict]:
    """`queue.json` from the items and their checks: a pass keeps the item,
    a fix takes the checker's proposed text, a drop removes it, and a
    reconsider keeps it with the checker's reason in front. An item with no
    check yet is held back and named. The items are the integrator's
    (`integrated.json`) once it has run at the end of the run; until then,
    during the one-essay-at-a-time passes, the gathered `items.json`."""
    integrated = _read_json(folder / "integrated.json", None)
    if integrated is None:
        integrated = _read_json(folder / "items.json", None)
    if integrated is None:
        raise RuntimeError("no items yet — 'devedit gather' writes them")
    out, unchecked = [], []
    for item in integrated:
        check = _read_json(folder / "checks" / f"{item['id']}.json", None)
        if check is None:
            unchecked.append(item["id"])
            continue
        result = check.get("result")
        if result == "drop":
            continue
        item = dict(item)
        if result == "fix" and check.get("proposed") is not None:
            item["proposed"] = check["proposed"]
        if result == "reconsider":
            item["reconsider"] = check.get("reason", "")
        item["check"] = {"result": result, "reason": check.get("reason", "")}
        out.append(item)
    previous = {i["id"]: i for i in queue(folder)}
    for item in out:
        old = previous.get(item["id"])
        if old and old.get("verdict"):
            item.update({k: old[k] for k in ("verdict", "final", "reason_given",
                                             "decided", "applied", "pushed")
                         if k in old})
    _write_json(folder / "queue.json", ordered(out))
    if unchecked:
        raise RuntimeError(f"{len(unchecked)} item(s) have no check yet and were "
                           f"held back: {', '.join(unchecked[:10])}"
                           + (" …" if len(unchecked) > 10 else ""))
    return out


# ------------------------------------------------------------------ queue

_TIER_RANK = {t: i for i, t in enumerate(TIERS)}


def queue(folder: Path) -> list[dict]:
    return _read_json(folder / "queue.json", [])


def ordered(items: list[dict]) -> list[dict]:
    """Review order: reading order of file, then position in it, so the
    author walks each essay front to back; tier only breaks ties."""
    return sorted(items, key=lambda i: (i.get("order", 0),
                                        _TIER_RANK.get(i.get("tier"), 9), i["id"]))


def pending(folder: Path) -> list[dict]:
    return [i for i in ordered(queue(folder)) if not i.get("verdict")]


def apply_item(manuscript: dict, item: dict, text: str | None = None) -> str:
    """Replace the item's original passage with `text` (the proposed text
    when None) in its file. The original must occur exactly once; anything
    else means the file changed since the item was made, and nothing is
    written. Returns the file name."""
    path = Path(manuscript["path"]) / item["file"]
    body = path.read_text(encoding="utf-8")
    original = item["original"]
    count = body.count(original)
    if count != 1:
        raise RuntimeError(
            f"item {item['id']}: its original text occurs {count} times in "
            f"{item['file']} now (expected once) — the file changed; the "
            "item needs checking again")
    new = text if text is not None else (item.get("proposed") or "")
    if not new:
        # A cut: remove the passage, and the blank line it leaves when it
        # was a whole paragraph.
        whole = original + "\n\n"
        if body.count(whole) == 1:
            original = whole
    path.write_text(body.replace(original, new, 1), encoding="utf-8")
    return item["file"]


def verdict(folder: Path, manuscript: dict, item_id: str, verdict_: str, *,
            text: str | None = None, reason: str = "",
            paragraph: str | None = None) -> dict:
    """Record the author's verdict on one item. Accept applies it (with
    `text` when the author rewrote the proposal); reject asks for a reason;
    defer leaves it for later."""
    if verdict_ not in VERDICTS:
        raise ValueError(f"verdict is one of {', '.join(VERDICTS)}")
    items = queue(folder)
    item = next((i for i in items if i["id"] == item_id), None)
    if item is None:
        raise LookupError(f"no item {item_id}")
    if verdict_ == "reject" and not reason.strip():
        raise ValueError("a rejection needs the author's reason")
    if verdict_ != "accept":
        # Rejected or deferred with an edit: nothing is applied; the author's
        # text is kept beside the reason for the next round to work from.
        authored = paragraph if paragraph is not None else text
        if authored is not None:
            item["author_text"] = authored
        text = paragraph = None
    if verdict_ == "accept" and paragraph is not None and item.get("original"):
        # The author edited the whole paragraph on the card. When the text
        # around the passage is untouched, keep the edit to the passage;
        # otherwise the edited paragraph replaces the paragraph.
        ctx = _paragraph_context(item, manuscript)
        if ctx.get("context_missing"):
            raise RuntimeError(f"item {item_id}: its paragraph is no longer "
                               "in the file; the item needs checking again")
        pre, suf = ctx["prefix"], ctx["suffix"]
        # A line break typed on the card starts a new paragraph: Markdown and
        # the Doc both need a blank line, or the tab and the file disagree.
        paragraph = re.sub(r"[ \t]*\n[ \t]*(?=\S)", "\n\n",
                           re.sub(r"\n{2,}", "\n", paragraph.strip("\n")))
        if paragraph.startswith(pre) and paragraph.endswith(suf) \
                and len(paragraph) >= len(pre) + len(suf):
            text = paragraph[len(pre):len(paragraph) - len(suf)]
        else:
            whole = dict(item, original=pre + item["original"] + suf)
            apply_item(manuscript, whole, paragraph)
            item["applied"] = _now()
            item["pushed"] = None
            text = paragraph
            item["final_is_paragraph"] = True
    if verdict_ == "accept" and item.get("original") and not item.get("applied"):
        apply_item(manuscript, item, text)
        item["applied"] = _now()
        item["pushed"] = None
    item.update(verdict=verdict_, reason_given=reason.strip(),
                final=text if text is not None else item.get("proposed"),
                decided=_now())
    _write_json(folder / "queue.json", items)
    with (folder / "verdicts.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({k: item.get(k) for k in
                              ("id", "file", "verdict", "final",
                               "reason_given", "author_text", "decided")},
                             ensure_ascii=False) + "\n")
    return item


def _swap(manuscript: dict, item: dict, old: str, new: str) -> None:
    """Replace `old` (text this run put in the file) with `new`, only when
    `old` occurs exactly once; otherwise the file changed since and nothing
    is written."""
    path = Path(manuscript["path"]) / item["file"]
    body = path.read_text(encoding="utf-8")
    count = body.count(old)
    if count != 1:
        raise RuntimeError(
            f"item {item['id']}: the text it applied occurs {count} times in "
            f"{item['file']} now (expected once) — the file changed since the "
            "verdict; nothing was changed, edit the file by hand")
    path.write_text(body.replace(old, new, 1), encoding="utf-8")


def decided_stage(folder: Path) -> dict | None:
    """The stage the Decided tab shows: the current one, else the last."""
    cur = current_stage(folder)
    if cur:
        return cur
    rows = stages(folder)
    return rows[-1] if rows else None


def decided(folder: Path, stage: dict | None = None, *,
            every: bool = False) -> list[dict]:
    """Items with a verdict, newest decision first, scoped to one stage
    (default `decided_stage`): items on that stage's files, plus the items
    its letter decisions applied (ids `stage<n>-d<k>.<m>`). `every` lists the
    whole run."""
    rows = [i for i in queue(folder) if i.get("verdict")]
    if not every:
        stage = stage or decided_stage(folder)
        if stage is not None:
            files, prefix = set(stage["files"]), f"stage{stage['n']}-d"
            rows = [i for i in rows if str(i.get("id", "")).startswith(prefix)
                    or (i.get("file") in files
                        and not re.match(r"stage\d+-d", str(i.get("id", ""))))]
    return sorted(rows, key=lambda i: i.get("decided") or "", reverse=True)


def revise(folder: Path, manuscript: dict, item_id: str, verdict_: str, *,
           text: str | None = None, reason: str = "") -> dict:
    """Change the verdict on an item already decided. Leaving an accept
    undoes it in the file (the applied text back to the original); a new
    accept applies, or swaps the applied text for the author's new `text`.
    Every file change is checked (the text being replaced must occur once)
    and leaves the file waiting for a push. Logged as a revision."""
    if verdict_ not in VERDICTS:
        raise ValueError(f"verdict is one of {', '.join(VERDICTS)}")
    items = queue(folder)
    item = next((i for i in items if i["id"] == item_id), None)
    if item is None:
        raise LookupError(f"no item {item_id}")
    if not item.get("verdict"):
        raise ValueError(f"item {item_id} has no verdict yet; give it one first")
    if verdict_ == "reject" and not reason.strip():
        raise ValueError("a rejection needs the author's reason")
    if verdict_ != "accept":
        # Rejected or deferred with an edit: nothing is applied; the author's
        # text is kept beside the reason for the next round to work from.
        if text is not None:
            item["author_text"] = text
        text = None
    was_applied = bool(item.get("applied") and item.get("original"))
    applied_text = item.get("final") if item.get("final") is not None else item.get("proposed")
    final = item.get("proposed") if text is None else text
    changed = False
    if verdict_ == "accept" and item.get("original"):
        if was_applied:
            if final != applied_text:
                _swap(manuscript, item, applied_text, final)
                changed = True
        else:
            apply_item(manuscript, item, final)
            changed = True
        item["applied"] = _now() if changed or not was_applied else item["applied"]
        item.pop("reverted", None)
    elif was_applied:
        _swap(manuscript, item, applied_text, item["original"])
        changed = True
        item["applied"] = None
        item["reverted"] = _now()
    if changed:
        item["pushed"] = None
    item.update(verdict=verdict_, reason_given=reason.strip(),
                final=final if verdict_ == "accept" else item.get("proposed"),
                decided=_now(), revised=True)
    _write_json(folder / "queue.json", items)
    with (folder / "verdicts.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({**{k: item.get(k) for k in
                                 ("id", "file", "verdict", "final",
                                  "reason_given", "decided")},
                              "revision": True}, ensure_ascii=False) + "\n")
    return item


def unpushed(folder: Path) -> list[str]:
    """Files with applied (or undone) items not yet pushed to the Doc."""
    return sorted({i["file"] for i in queue(folder)
                   if (i.get("applied") or i.get("reverted"))
                   and not i.get("pushed")})


def mark_pushed(folder: Path, file: str) -> None:
    items = queue(folder)
    for i in items:
        if (i.get("file") == file and (i.get("applied") or i.get("reverted"))
                and not i.get("pushed")):
            i["pushed"] = _now()
    _write_json(folder / "queue.json", items)


# ------------------------------------------------------------------ word diff

# A word with the space after it, so a changed run of words is one run.
_TOKEN = re.compile(r"^\s+|\S+\s*")


def word_diff(original: str, proposed: str) -> list[tuple[str, str]]:
    """(op, text) runs, op one of '=', '+', '-': what the review shows in
    bold (added) and strikethrough (removed)."""
    import difflib

    a, b = _TOKEN.findall(original), _TOKEN.findall(proposed)
    out: list[tuple[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b,
                                                      autojunk=False).get_opcodes():
        if op == "equal":
            out.append(("=", "".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(("-", "".join(a[i1:i2])))
            if j2 > j1:
                out.append(("+", "".join(b[j1:j2])))
    return out


def markdown_diff(original: str, proposed: str) -> str:
    """The chat form of an item: additions **bold**, removals ~~struck~~."""
    parts = []
    for op, text in word_diff(original, proposed):
        if op == "=":
            parts.append(text)
        elif text.strip():
            mark = "**" if op == "+" else "~~"
            lead = text[:len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            parts.append(f"{lead}{mark}{text.strip()}{mark}{trail}")
        else:
            parts.append(text if op == "+" else "")
    return "".join(parts)


# ------------------------------------------------------------------ page

def dispatch(method: str, params: dict[str, Any], workspace: str | None = None
             ) -> dict:
    """The page's API (`/api/devedit`). Every method names the manuscript;
    `run` is optional (latest). The database, the manuscript and the Google
    services are the audio page's session's, so both modes share one."""
    from . import audiobook

    session = audiobook.session(workspace, params.get("manuscript"))
    manuscript = session.manuscript
    try:
        folder = run_dir(manuscript, params.get("run"))
    except LookupError as err:
        return {"error": str(err)}
    if method == "status":
        return {"run": folder.name, "steps": status(folder, manuscript),
                "stages": stage_status(folder),
                "coverage": state(folder).get("coverage"),
                "decisions": decisions(folder),
                "counts": _counts(folder), "unpushed": unpushed(folder)}
    if method == "letter":
        letter = stage_dir(folder) / "letter.md"
        amap = stage_dir(folder) / "argument-map.md"
        return {"letter": letter.read_text(encoding="utf-8") if letter.exists() else "",
                "map": amap.read_text(encoding="utf-8") if amap.exists() else "",
                "decisions": decisions(folder)}
    if method == "save":
        # The author edits the letter and the argument map in place on the
        # page; the run keeps their words.
        name = {"letter": "letter.md", "map": "argument-map.md"}.get(str(params.get("doc")))
        if not name:
            return {"error": "save takes doc 'letter' or 'map'"}
        stage_dir(folder).mkdir(parents=True, exist_ok=True)
        (stage_dir(folder) / name).write_text(str(params.get("text") or ""),
                                              encoding="utf-8")
        return {"saved": name}
    if method == "decide":
        try:
            row = decide_and_apply(folder, manuscript, int(params["n"]),
                                   str(params["answer"]),
                         str(params.get("reason") or ""),
                         bool(params.get("binding", True)))
        except (LookupError, ValueError) as err:
            return {"error": str(err)}
        return {"decision": row}
    if method == "next":
        rest = pending(folder)
        # Skipped cards (the page's own list) wait at the back; when only
        # skipped cards are left, they come round again in order.
        skip = list(params.get("skip") or [])
        shown = [i for i in rest if i["id"] not in skip] or sorted(
            rest, key=lambda i: skip.index(i["id"]) if i["id"] in skip else -1)
        return {"item": _for_page(shown[0] if shown else None, manuscript), "left": len(rest),
                "counts": _counts(folder), "unpushed": unpushed(folder)}
    if method == "verdict":
        try:
            item = verdict(folder, manuscript, str(params["id"]),
                           str(params["verdict"]), text=params.get("text"),
                           reason=str(params.get("reason") or ""),
                           paragraph=params.get("paragraph"))
        except (LookupError, RuntimeError, ValueError) as err:
            return {"error": str(err)}
        return {"item": _for_page(item, manuscript), "unpushed": unpushed(folder)}
    if method == "decided":
        every = bool(params.get("all"))
        stage = None if every else decided_stage(folder)
        return {"items": [_for_page(i, manuscript) for i in decided(folder, stage, every=every)],
                "stage": {"n": stage["n"], "name": stage["name"]} if stage else None,
                "unpushed": unpushed(folder)}
    if method == "revise":
        try:
            item = revise(folder, manuscript, str(params["id"]),
                          str(params["verdict"]), text=params.get("text"),
                          reason=str(params.get("reason") or ""))
        except (LookupError, RuntimeError, ValueError) as err:
            return {"error": str(err)}
        return {"item": _for_page(item, manuscript), "unpushed": unpushed(folder)}
    if method in ("push", "pull"):
        from . import gdocs

        file = str(params["file"])
        _config, service, docs_service = session._doc_services()
        db = session._thread_db()
        if method == "pull":
            report = gdocs.pull_doc(db, manuscript, file, service=service,
                                    docs_service=docs_service)
            return {"pulled": report.get("changed", []),
                    "conflicts": report.get("conflicts", [])}
        try:
            # Guarded, as on the audio page: a tab edited since the last
            # push is never overwritten; pull first.
            gdocs.push_doc(db, manuscript, file, service=service,
                           docs_service=docs_service, guard=True)
        except Exception as err:  # the guard and Google's errors both land here
            return {"error": str(err)}
        mark_pushed(folder, file)
        return {"pushed": file, "unpushed": unpushed(folder)}
    if method == "arcs":
        db = session._thread_db()
        open_ = []
        by_file = {f: s for s in stages(folder) for f in s["files"]}
        for row in arc_status(db, manuscript, folder):
            if row["state"] == "open":
                rec = arc_record(folder, row["file"], by_file[row["file"]])
                open_.append({"file": row["file"], "pass": rec["pass"],
                              "round": rec["rounds"][-1], "rounds": len(rec["rounds"]),
                              "answers": rec["answers"]})
        return {"open": open_, "status": arc_status(db, manuscript, folder)}
    if method == "arc_answer":
        try:
            rec = arc_answer(folder, str(params["file"]), str(params.get("text") or ""),
                             via="page")
        except (LookupError, ValueError) as err:
            return {"error": str(err)}
        return {"answers": rec["answers"]}
    if method == "arc_settle":
        try:
            rec = arc_settle(folder, manuscript, str(params["file"]),
                             str(params["outcome"]),
                             candidate=int(params["candidate"]) if params.get("candidate") else None,
                             reason=str(params.get("reason") or ""), via="page")
        except (LookupError, ValueError) as err:
            return {"error": str(err)}
        return {"settled": rec["settled"]}
    return {"error": f"unknown method '{method}'"}


def _counts(folder: Path) -> dict:
    items = queue(folder)
    out: dict[str, int] = {"total": len(items)}
    for tier in TIERS:
        out[tier] = sum(1 for i in items if i.get("tier") == tier and not i.get("verdict"))
    for v in VERDICTS:
        out[v] = sum(1 for i in items if i.get("verdict") == v)
    return out


def _for_page(item: dict | None, manuscript: dict | None = None) -> dict | None:
    if item is None:
        return None
    out = dict(item)
    out["why"] = why_of(item)
    if item.get("original") is not None and item.get("proposed") is not None:
        out["diff"] = word_diff(item["original"], item["proposed"])
        if manuscript is not None:
            out.update(_paragraph_context(item, manuscript))
    return out


def _paragraph_context(item: dict, manuscript: dict) -> dict:
    """The whole paragraph(s) of the item's file around its passage, so the
    card shows a fragment in its context. A paragraph is a block between
    blank lines. `prefix`/`suffix` are the paragraph text before and after
    the passage; `paragraph` reads as the file does now and
    `paragraph_proposed` with the proposed text in place. An accepted item's
    file holds its final text, so that is looked for when the original is
    gone. Nothing found: `context_missing`."""
    path = Path(manuscript["path"]) / item["file"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"context_missing": True}
    original, proposed = item["original"], item.get("proposed") or ""
    here, now = original, original
    at = text.find(original) if original else -1
    if at < 0 and item.get("verdict") == "accept" and item.get("final"):
        here = now = item["final"]
        at = text.find(here)
    if at < 0:
        return {"context_missing": True}
    breaks = [m for m in re.finditer(r"\n[ \t]*\n", text)]
    start = max((m.end() for m in breaks if m.end() <= at), default=0)
    end = min((m.start() for m in breaks if m.start() >= at + len(here)),
              default=len(text.rstrip("\n")))
    prefix, suffix = text[start:at], text[at + len(here):end]
    return {"prefix": prefix, "suffix": suffix,
            "paragraph": prefix + now + suffix,
            "paragraph_proposed": prefix + proposed + suffix}
