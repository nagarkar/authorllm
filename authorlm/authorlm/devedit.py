"""Developmental edit: one whole-book developmental edit as a run folder, and the verbs over it.

A run lives at `<manuscript>/_devedit/<run-id>/` and is the only place its
state lives. This module is the only writer of that folder; the
`developmental-editing` skill drives the steps, `authorlm devedit` is the
chat surface, and the audio page's Dev edit mode (`dispatch`) is the review
surface. A run moves through these steps, each recorded in `state.json` with
its status, who ran it (authorlm or a session subagent), the model and effort
the run reported, and the hashes of the files it read:

1. coverage: the files the run edits and the files it reads only as
   reference, from `toc.toml`, confirmed by the author before anything runs.
2. summaries: `summarize rebuild` brought up to date for every covered file.
3. terms: the term index (`terms.Terms.index`), snapshotted to `terms.json`.
4. crossbook: contradictions, redundancy and ordering problems across the
   whole book, appendix papers included, written to `crossbook.md`.
5. letter: `letter.md`, `argument-map.md` and the proposed decisions in
   `decisions.json`.
6. decisions: the author's answers, each binding or provisional.
7. essays: one finding file per edited essay under `findings/`.
8. integrate: the deterministic grouping in `groups.json`, then the
   integrator's reconciled items.
9. check: each item checked against the current text; `queue.json`.
10. review: the author's verdicts, applied to the manuscript files.

Decisions:
- Run state lives in the manuscript folder, `_devedit/<run-id>/`, beside
  `_critiques`, and travels with the manuscript. (owner)
- Coverage comes from `toc.toml` and is confirmed by the author before any
  step runs; only files the TOC lists are covered, and `outside` files never
  are. (owner)
- Nothing in authorlm's stored notes is authoritative: summaries are rebuilt
  before use and the term index is computed from the text. (owner)
- The editorial letter judges what the whole manuscript delivers against
  what it promises, maps every concept as introduced, asserted, demonstrated
  or assumed, and lists at most ten problems, each with evidence, why it
  matters, a remedy and its dependencies. (owner)
- Each letter decision is binding or provisional; a later step may raise a
  provisional one for reconsideration, never override either. (owner)
- Strengths the letter names are preserved, not frozen: a later step may
  move a strong passage or add a transition, never weaken what makes it
  strong. (owner)
- An essay agent is given its essay's essay, section and paragraph summaries,
  every other essay's essay summary, and the term index entries for its
  terms; it fetches anything else through authorlm verbs. (owner)
- The integrator reads every proposed item together after the essay agents,
  from a deterministic grouping by term, concept, dependency and paragraph,
  and makes the items mutually consistent. (owner)
- Items are tiered must, should and could, with no cap; the reason behind an
  item is shown only when the author asks for it. (owner)
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

STEPS = ("coverage", "summaries", "terms", "crossbook", "letter", "decisions",
         "essays", "integrate", "check", "review")
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


def _run_sort_key(name: str) -> tuple[str, int]:
    """Order same-day runs by their numeric suffix: `YYYY-MM-DD` is the
    first of that day, then `-2`, `-3`, … so `-10` sorts after `-9`. Plain
    lexicographic order would put `-10` between `-1` and `-2` and make
    `run_dir()` default to a stale run once a tenth same-day folder exists."""
    if len(name) > 10 and name[10] == "-" and name[11:].isdigit():
        return (name[:10], int(name[11:]))
    return (name, 1)


def runs(manuscript: dict) -> list[str]:
    base = root(manuscript)
    if not base.exists():
        return []
    return sorted((p.name for p in base.iterdir()
                   if (p / "state.json").exists()), key=_run_sort_key)


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


def new_run(manuscript: dict, today: str | None = None) -> Path:
    """A fresh run folder named by date, with every step pending and the
    coverage proposed from the TOC but not yet confirmed."""
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
             "steps": {s: {"status": "pending"} for s in STEPS}}
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
    """`edit`: every main-matter file the TOC lists, in reading order.
    `reference`: the TOC's front and back matter, read for the book's promises
    and for contradictions but never edited. Files declared `outside` are in
    neither, and neither is a file the TOC does not list."""
    toc = files.get(structure.TOC_FILENAME, "")
    listed = {name for name, _depth in structure.parse_toc_tree(toc)}
    order, _unlisted = structure.reading_order(files)
    attrs = structure.toc_attrs(toc)
    edit, reference = [], []
    for name in order:
        if name not in listed:
            continue
        matter = (attrs.get(name) or {}).get("matter", "main")
        (edit if matter == "main" else reference).append(name)
    return {"edit": edit, "reference": reference}


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
    """The term index over the covered files, written to `terms.json` as
    the snapshot the agents of this run read."""
    files = _disk_files(manuscript)
    texts = {n: files[n] for n in covered(folder) if n in files}
    rows = [{"term": t.name, "count": t.count, "files": t.files,
             "locations": [[loc.file, loc.section, loc.paragraph]
                           for loc in t.locations],
             "definitions": [[loc.file, loc.paragraph, sentence]
                             for loc, sentence in t.definitions]}
            for t in Terms(texts).index()]
    _write_json(folder / "terms.json", rows)
    return rows


# ------------------------------------------------------------------ context packs

def _essay_summary(db, manuscript: dict, file: str) -> str | None:
    row = db.one("SELECT summary FROM essay_summaries WHERE manuscript_id = ? "
                 "AND file = ?", (manuscript["id"], file))
    return row["summary"] if row else None


def essay_pack(db, manuscript: dict, folder: Path, file: str) -> str:
    """What an essay agent is given (Markdown): this essay's essay summary,
    each section's summary with its paragraph gists, every other covered
    essay's essay summary, and the term index entries for this essay's
    terms with where else they occur."""
    from . import summaries as sums

    names = covered(folder)
    if file not in names:
        raise LookupError(f"'{file}' is not covered by this run")
    files = _disk_files(manuscript)
    text = files[file]
    verse = file in sums.verse_files_of(manuscript)
    out = [f"# Context for {file}", "", "## This essay", "",
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
    out += ["## The other essays", ""]
    for other in names:
        if other != file:
            out += [f"### {other}", "",
                    _essay_summary(db, manuscript, other) or "(no essay summary)", ""]
    terms = _read_json(folder / "terms.json", [])
    mine = [t for t in terms if file in t["files"]]
    out += ["## Terms this essay uses, and where else they occur", ""]
    for t in mine:
        elsewhere = sorted({f for f in t["files"] if f != file})
        out.append(f"- **{t['term']}** ({t['count']}×): "
                   + (", ".join(elsewhere) if elsewhere else "only here"))
    return "\n".join(out) + "\n"


def book_pack(db, manuscript: dict, folder: Path) -> str:
    """What the cross-book agent is given: every covered file's essay
    summary and section summaries, in reading order."""
    from . import summaries as sums

    files = _disk_files(manuscript)
    out = ["# The book in summary", ""]
    for name in covered(folder):
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

def decisions(folder: Path) -> list[dict]:
    """The letter's decisions, each {n, question, options, recommended,
    answer, reason, binding, answered}."""
    return _read_json(folder / "decisions.json", [])


def decide(folder: Path, n: int, answer: str, reason: str = "",
           binding: bool = True) -> dict:
    rows = decisions(folder)
    row = next((r for r in rows if r["n"] == n), None)
    if row is None:
        raise LookupError(f"no decision {n}")
    if row.get("options") and answer not in row["options"]:
        raise ValueError(f"decision {n}'s answer is one of: "
                         + ", ".join(row["options"]))
    row.update(answer=answer, reason=reason.strip(), binding=binding,
               answered=_now())
    _write_json(folder / "decisions.json", rows)
    return row


# ------------------------------------------------------------------ items
#
# The agents write JSON; these functions are the deterministic steps between
# them. An essay agent writes `findings/<file>.json`: a list of findings, each
# {finding, letter_ref, items: [{tier, original, proposed, move, reason}]}.
# `gather` numbers every item into `items.json`; `group` writes the
# integrator's `groups.json`; the integrator writes `integrated.json` (items
# kept, edited, merged or dropped, with `links`); each checker writes
# `checks/<id>.json` {result: pass|fix|drop|reconsider, proposed?, reason};
# `build_queue` turns what passed into `queue.json`.

def gather(folder: Path) -> list[dict]:
    order = {name: i for i, name in enumerate(covered(folder))}
    items: list[dict] = []
    for path in sorted((folder / "findings").glob("*.json"),
                       key=lambda p: order.get(p.stem + ".md", 999)):
        file = path.stem + ".md"
        for f_i, finding in enumerate(_read_json(path, [])):
            for i_i, item in enumerate(finding.get("items") or []):
                tier = item.get("tier", "could")
                if tier not in TIERS:
                    raise ValueError(f"{path.name}: tier '{tier}' is not one of "
                                     + ", ".join(TIERS))
                items.append({
                    "id": f"{path.stem}-{f_i + 1}.{i_i + 1}", "file": item.get("file", file),
                    "tier": tier, "original": item.get("original"),
                    "proposed": item.get("proposed"), "move": item.get("move"),
                    "reason": item.get("reason", ""),
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
    terms = [t["term"] for t in _read_json(folder / "terms.json", [])]
    concepts = _read_json(folder / "argument-map.json", [])
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
    """`queue.json` from the integrated items and their checks: a pass keeps
    the item, a fix takes the checker's proposed text, a drop removes it,
    and a reconsider keeps it with the checker's reason in front. An item
    with no check yet is held back and named."""
    integrated = _read_json(folder / "integrated.json", None)
    if integrated is None:
        raise RuntimeError("no integrated.json yet — the integrator writes it")
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
    """Review order: tier, then reading order of file, then position; linked
    items stay together behind the first of their group."""
    return sorted(items, key=lambda i: (_TIER_RANK.get(i.get("tier"), 9),
                                        i.get("order", 0), i["id"]))


def pending(folder: Path) -> list[dict]:
    return [i for i in ordered(queue(folder)) if not i.get("verdict")]


def _manuscript_file(manuscript: dict, file: str, item_id: str) -> Path:
    """Resolve an item's `file` under the manuscript root. Absolute paths,
    `..` segments, and anything that resolves outside the root are refused:
    gather trusts the agent-supplied `file`, and accept must not write
    outside the manuscript (or replace the root via an absolute join)."""
    if not isinstance(file, str) or not file.strip():
        raise ValueError(f"item {item_id}: file is missing")
    candidate = Path(file)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(
            f"item {item_id}: file '{file}' is not a manuscript-relative path")
    root = Path(manuscript["path"]).resolve()
    path = (root / candidate).resolve()
    if not path.is_relative_to(root):
        raise ValueError(
            f"item {item_id}: file '{file}' is outside the manuscript")
    return path


def apply_item(manuscript: dict, item: dict, text: str | None = None) -> str:
    """Replace the item's original passage with `text` (the proposed text
    when None) in its file. The original must occur exactly once; anything
    else means the file changed since the item was made, and nothing is
    written. Returns the file name."""
    path = _manuscript_file(manuscript, item["file"], item["id"])
    body = path.read_text(encoding="utf-8")
    original = item["original"]
    count = body.count(original)
    if count != 1:
        raise RuntimeError(
            f"item {item['id']}: its original text occurs {count} times in "
            f"{item['file']} now (expected once) — the file changed; the "
            "item needs checking again")
    replacement = item["proposed"] if text is None else text
    if not isinstance(replacement, str):
        raise ValueError(
            f"item {item['id']}: accept needs proposed text "
            "(or --text); proposed is missing")
    path.write_text(body.replace(original, replacement, 1), encoding="utf-8")
    return item["file"]


def verdict(folder: Path, manuscript: dict, item_id: str, verdict_: str, *,
            text: str | None = None, reason: str = "") -> dict:
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
    if verdict_ == "accept" and item.get("original"):
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
                               "reason_given", "decided")},
                             ensure_ascii=False) + "\n")
    return item


def unpushed(folder: Path) -> list[str]:
    """Files with accepted items not yet pushed to the Doc."""
    return sorted({i["file"] for i in queue(folder)
                   if i.get("applied") and not i.get("pushed")})


def mark_pushed(folder: Path, file: str) -> None:
    items = queue(folder)
    for i in items:
        if i.get("file") == file and i.get("applied") and not i.get("pushed"):
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
                "coverage": state(folder).get("coverage"),
                "decisions": decisions(folder),
                "counts": _counts(folder), "unpushed": unpushed(folder)}
    if method == "letter":
        letter = folder / "letter.md"
        amap = folder / "argument-map.md"
        return {"letter": letter.read_text(encoding="utf-8") if letter.exists() else "",
                "map": amap.read_text(encoding="utf-8") if amap.exists() else "",
                "decisions": decisions(folder)}
    if method == "save":
        # The author edits the letter and the argument map in place on the
        # page; the run keeps their words.
        name = {"letter": "letter.md", "map": "argument-map.md"}.get(str(params.get("doc")))
        if not name:
            return {"error": "save takes doc 'letter' or 'map'"}
        (folder / name).write_text(str(params.get("text") or ""), encoding="utf-8")
        return {"saved": name}
    if method == "decide":
        try:
            row = decide(folder, int(params["n"]), str(params["answer"]),
                         str(params.get("reason") or ""),
                         bool(params.get("binding", True)))
        except (LookupError, ValueError) as err:
            return {"error": str(err)}
        return {"decision": row}
    if method == "next":
        rest = pending(folder)
        return {"item": _for_page(rest[0] if rest else None), "left": len(rest),
                "counts": _counts(folder), "unpushed": unpushed(folder)}
    if method == "verdict":
        try:
            item = verdict(folder, manuscript, str(params["id"]),
                           str(params["verdict"]), text=params.get("text"),
                           reason=str(params.get("reason") or ""))
        except (LookupError, RuntimeError, ValueError) as err:
            return {"error": str(err)}
        return {"item": _for_page(item), "unpushed": unpushed(folder)}
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
    return {"error": f"unknown method '{method}'"}


def _counts(folder: Path) -> dict:
    items = queue(folder)
    out: dict[str, int] = {"total": len(items)}
    for tier in TIERS:
        out[tier] = sum(1 for i in items if i.get("tier") == tier and not i.get("verdict"))
    for v in VERDICTS:
        out[v] = sum(1 for i in items if i.get("verdict") == v)
    return out


def _for_page(item: dict | None) -> dict | None:
    if item is None:
        return None
    out = dict(item)
    if item.get("original") is not None and item.get("proposed") is not None:
        out["diff"] = word_diff(item["original"], item["proposed"])
    return out
