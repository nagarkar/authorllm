"""The interlocutor — a third-person critical reading of the whole book from
inside a named tradition (design: docs/interlocutor-design.md).

Not a lens: a lens reads one essay and reports findings; an interlocutor
reads the book from inside Plato, or Epictetus, or Basilides, and produces
a REPORT — the objections that tradition's literature already holds
against claims the book makes, the misattributions the book commits about
the tradition, and for each objection whether the book as written already
meets it. The report lands on the critique road (`_critiques/`, then
`critique.import_manifest`), so every improvement it asks for is a
proposed intent with critic provenance and nothing is law until triaged.

The fabrication rule moves rather than lapses. The drafter may use no
world knowledge; this pass IS world knowledge, so the guard sits on the
citations: an objection with no locus is not an objection and is dropped
at import, the way an ungrounded lens finding is dropped at the door.

Nothing here calls a model. `run` assembles a payload for a clean-context
subagent (chat road, default model); `import` verifies what came back
against the manuscript on disk and lands it.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import tomllib
from pathlib import Path

from .concepts import concept_pattern, node_names
from .db import Database, loads
from .revisions import read_manuscript_files
from .structure import (content_files, matter_map, parse_toc_tree,
                        reading_order, select_chapters, TOC_FILENAME)

INTERLOCUTOR_DIR = "_interlocutors"
CRITIQUE_DIR = "_critiques"
DELIMITER = "---"
KEYS = ("terms", "engaged", "position")
TERM_KINDS = ("tradition", "book")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")


class InterlocutorError(ValueError):
    """A refusal that names what is wrong and what is legal."""


# ------------------------------------------------------------ the artifact

def _path(manuscript: dict, name: str) -> Path:
    return Path(manuscript["path"]) / INTERLOCUTOR_DIR / f"{name}.md"


def parse_artifact(text: str, files: dict[str, str] | None = None
                   ) -> tuple[dict, str]:
    """`(meta, prose)` from an interlocutor artifact.

    TOML front matter between `---` lines (the filter grammar, borrowed),
    then the prose prompt. Every refusal names the legal shape, because
    the author writes this by hand. `files`, when given, is the
    manuscript's file map and is used to refuse an `engaged` or
    `position` entry that names a file the manuscript does not have."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != DELIMITER:
        raise InterlocutorError(
            "an interlocutor needs TOML front matter — the first line must "
            f"be '{DELIMITER}'. The shortest legal artifact is:\n\n"
            f"{DELIMITER}\n[[terms]]\nname = \"prohairesis\"\n"
            f"kind = \"tradition\"\n{DELIMITER}\n\n# Who is reading\n…")
    end = None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == DELIMITER:
            end = i
            break
    if end is None:
        raise InterlocutorError(
            f"the front matter is never closed — add a '{DELIMITER}' line "
            "after the last key.")
    try:
        meta = tomllib.loads("\n".join(lines[start + 1:end]))
    except tomllib.TOMLDecodeError as err:
        raise InterlocutorError(
            f"the front matter is not valid TOML: {err}. Keys are TOML, so "
            "string values need quotes: kind = \"tradition\".") from None
    unknown = sorted(k for k in meta if k not in KEYS)
    if unknown:
        raise InterlocutorError(
            f"unknown front-matter key(s): {', '.join(unknown)}. An "
            f"interlocutor's front matter carries {', '.join(KEYS[:-1])} "
            f"and {KEYS[-1]}, nothing else.")

    raw_terms = meta.get("terms")
    if not isinstance(raw_terms, list) or not raw_terms:
        raise InterlocutorError(
            "`terms` is required and must hold at least one [[terms]] "
            "table: the deterministic scan is what the critic starts from, "
            "and an artifact with no terms would start from nothing.")
    terms: list[dict] = []
    seen: set[str] = set()
    for i, t in enumerate(raw_terms, 1):
        if not isinstance(t, dict) or not str(t.get("name", "")).strip():
            raise InterlocutorError(
                f"[[terms]] #{i} has no `name`. Each term is a table with "
                "name, kind (\"tradition\" or \"book\"), optional aliases, "
                "and for a book term a one-line reason.")
        name = str(t["name"]).strip()
        kind = t.get("kind")
        if kind not in TERM_KINDS:
            raise InterlocutorError(
                f"term '{name}': kind must be one of "
                f"{' or '.join(repr(k) for k in TERM_KINDS)} — 'tradition' "
                "for the critic's own vocabulary, 'book' for a term of THIS "
                "book the author declares adjacent to it.")
        aliases = t.get("aliases", [])
        if (not isinstance(aliases, list)
                or not all(isinstance(a, str) and a.strip() for a in aliases)):
            raise InterlocutorError(
                f"term '{name}': `aliases` is a list of strings.")
        reason = str(t.get("reason") or "").strip()
        if kind == "book" and not reason:
            raise InterlocutorError(
                f"term '{name}' is a book term and needs a `reason` — one "
                "line saying which of the critic's concepts it stands "
                "beside. The mapping is the author's claim, and a claim "
                "without its reason cannot be ratified.")
        extra = sorted(k for k in t if k not in ("name", "kind", "aliases",
                                                  "reason"))
        if extra:
            raise InterlocutorError(
                f"term '{name}': unknown key(s) {', '.join(extra)}.")
        key = name.lower()
        if key in seen:
            raise InterlocutorError(f"term '{name}' is listed twice.")
        seen.add(key)
        terms.append({"name": name, "kind": kind,
                      "aliases": [a.strip() for a in aliases],
                      "reason": reason})

    def file_list(key: str) -> list[str]:
        value = meta.get(key, [])
        if (not isinstance(value, list)
                or not all(isinstance(v, str) and v.strip() for v in value)):
            raise InterlocutorError(
                f"`{key}` is a list of manuscript files, e.g. "
                f"{key} = [\"epictetus.md\"].")
        out = [v.strip() for v in value]
        if files is not None:
            known = content_files(files)
            missing = [v for v in out if v not in known]
            if missing:
                raise InterlocutorError(
                    f"`{key}` names {', '.join(missing)}, which is not a "
                    "manuscript file. Only content files in the reading "
                    "order can be carried whole.")
        return out

    engaged = file_list("engaged")
    position = file_list("position")
    prose = "\n".join(lines[end + 1:]).strip()
    if not prose:
        raise InterlocutorError(
            "an interlocutor is its prompt — there is nothing after the "
            "front matter. The prose carries the corpus declaration, the "
            "persona, and the refusals.")
    return {"terms": terms, "engaged": engaged, "position": position}, prose


def add_interlocutor(manuscript: dict, name: str, text: str
                     ) -> tuple[Path, dict]:
    """Ratify an artifact. Validated whole BEFORE anything is written, so a
    refused add leaves no file."""
    if not _NAME.match(name or ""):
        raise InterlocutorError("interlocutor names are short kebab-case "
                                "slugs, e.g. 'epictetus'")
    if not (text or "").strip():
        raise InterlocutorError("an interlocutor is its artifact — pass it "
                                "(front matter and prose) on stdin")
    files = read_manuscript_files(Path(manuscript["path"]))
    meta, _prose = parse_artifact(text, files)
    path = _path(manuscript, name)
    path.parent.mkdir(exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path, meta


def list_interlocutors(manuscript: dict) -> list[dict]:
    directory = Path(manuscript["path"]) / INTERLOCUTOR_DIR
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.md")):
        try:
            meta, prose = parse_artifact(path.read_text(encoding="utf-8"))
            first = prose.split("\n", 1)[0].lstrip("# ").strip()
            out.append({"name": path.stem, "summary": first[:100],
                        "terms": len(meta["terms"]),
                        "engaged": meta["engaged"],
                        "position": meta["position"]})
        except InterlocutorError as err:
            out.append({"name": path.stem, "summary": f"!! {err}",
                        "terms": 0, "engaged": [], "position": []})
    return out


def load(manuscript: dict, name: str, files: dict[str, str] | None = None
         ) -> tuple[dict, str]:
    path = _path(manuscript, name)
    if not path.is_file():
        known = ", ".join(i["name"] for i in list_interlocutors(manuscript)) \
            or "none"
        raise LookupError(f"no interlocutor '{name}' (defined: {known})")
    return parse_artifact(path.read_text(encoding="utf-8"), files)


# ----------------------------------------------------------------- the scan

def _heading_text(unit: str) -> str | None:
    """The heading a unit opens with, markup stripped, or None."""
    first = (unit or "").lstrip().split("\n", 1)[0]
    stripped = first.lstrip("#")
    hashes = len(first) - len(stripped)
    if not (1 <= hashes <= 6 and stripped[:1] in (" ", "\t")):
        return None
    return _norm_heading(stripped)


def _norm_heading(text: str) -> str:
    return " ".join(re.sub(r"[*_`#]", "", text or "").split()).strip()


def _units(text: str) -> list[str]:
    from .passes import paragraphs_of

    return paragraphs_of(text)


def _part_map(files: dict[str, str]) -> dict[str, str | None]:
    """file → the depth-0 toc entry it sits under (itself, at the root)."""
    tree = parse_toc_tree(files.get(TOC_FILENAME) or "")
    out: dict[str, str | None] = {}
    current: str | None = None
    for name, depth in tree:
        if depth == 0:
            current = name
        out[name] = current
    return out


def scan(files: dict[str, str], terms: list[dict],
         selected: list[str] | None = None) -> list[dict]:
    """Step 1, deterministic: every unit that mentions a term or alias,
    in reading order. One hit per unit, naming every term it matched.
    Heading units set the section and are not themselves matched."""
    order, _ = reading_order(files)
    names = selected if selected is not None else order
    patterns = []
    for t in terms:
        for label in [t["name"], *t["aliases"]]:
            patterns.append((t["name"], t["kind"], concept_pattern(label)))
    parts = _part_map(files)
    matter = matter_map(files)
    hits: list[dict] = []
    for name in names:
        text = files.get(name)
        if text is None:
            continue
        heading: str | None = None
        for n, unit in enumerate(_units(text), 1):
            h = _heading_text(unit)
            if h is not None:
                heading = h
                continue
            matched: list[tuple[str, str]] = []
            for term, kind, pat in patterns:
                if pat.search(unit) and (term, kind) not in matched:
                    matched.append((term, kind))
            if matched:
                hits.append({"file": name, "part": parts.get(name),
                             "matter": matter.get(name, "main"),
                             "heading": heading, "n": n,
                             "terms": matched, "text": unit})
    return hits


MAX_TERMS = 12          # terms shown per run, sampled when more have hits
MAX_PER_TERM = 6        # locations shown per term, sampled when more exist


def sample_hits(hits: list[dict], max_terms: int = MAX_TERMS,
                max_per_term: int = MAX_PER_TERM,
                seed: int | None = None) -> dict:
    """Sample the scan two ways — across terms, then across each term's
    locations — so that a run points the critic at a readable number of
    paragraphs and a re-run points at different ones (author ruling
    2026-09-06: "every time I run the interlocutor, I might get new
    insights"). The full scan is kept for the coverage table; the seed is
    recorded so any run can be reproduced. A cap of 0 means no sampling
    on that axis."""
    import random

    if seed is None:
        seed = random.SystemRandom().randrange(1, 10**9)
    rng = random.Random(seed)
    by_term: dict[str, list[dict]] = {}
    for h in hits:
        for term, _kind in h["terms"]:
            by_term.setdefault(term, []).append(h)
    all_terms = list(by_term)
    chosen_terms = all_terms
    if max_terms and len(all_terms) > max_terms:
        chosen_terms = sorted(rng.sample(all_terms, max_terms),
                              key=all_terms.index)
    keys: set[tuple[str, int]] = set()
    shown: dict[str, int] = {}
    for term in chosen_terms:
        pool = by_term[term]
        picked = pool
        if max_per_term and len(pool) > max_per_term:
            picked = rng.sample(pool, max_per_term)
        shown[term] = len(picked)
        keys.update((h["file"], h["n"]) for h in picked)
    sampled = [h for h in hits if (h["file"], h["n"]) in keys]
    return {"hits": sampled, "seed": seed, "all_terms": all_terms,
            "chosen_terms": chosen_terms, "shown": shown,
            "totals": {t: len(v) for t, v in by_term.items()},
            "total_units": len(hits)}


def coverage_block(sample: dict) -> str:
    """One line per term: how many units mention it in the scanned files,
    how many of those this run shows, and where the rest live — so the
    critic knows what the sample is a sample OF and where to open a file
    whole."""
    if not sample["all_terms"]:
        return "(no term hits)"
    lines = [f"Scan: {sample['total_units']} unit(s) mention a term; this "
             f"run shows {len(sample['hits'])} of them — "
             f"{len(sample['chosen_terms'])} of {len(sample['all_terms'])} "
             f"term(s), at most {MAX_PER_TERM if sample['shown'] else 0} "
             f"location(s) each. Seed {sample['seed']} reproduces it.", ""]
    files_of: dict[str, dict[str, int]] = {}
    for h in sample.get("_all_hits", []):
        for term, _ in h["terms"]:
            files_of.setdefault(term, {})
            files_of[term][h["file"]] = files_of[term].get(h["file"], 0) + 1
    for term in sample["all_terms"]:
        n = sample["totals"][term]
        k = sample["shown"].get(term)
        where = ", ".join(f"{f} ×{c}" for f, c in sorted(
            files_of.get(term, {}).items(), key=lambda x: -x[1])[:6])
        status = (f"{k} of {n} shown" if k is not None
                  else f"{n} unit(s), none shown this run")
        lines.append(f"- {term}: {status}" + (f" — {where}" if where else ""))
    return "\n".join(lines)


def mentions_block(hits: list[dict], whole: set[str] = frozenset()) -> str:
    """The MENTIONS block: grouped by file and heading, each unit verbatim
    — except in a file the payload already carries whole, where only the
    location is given."""
    if not hits:
        return "(no unit in the scanned files mentions any term)"
    out: list[str] = []
    last_file = last_heading = object()
    for h in hits:
        if h["file"] != last_file:
            where = f"part: {h['part']}" if h["part"] else "unfiled"
            carried = " — carried whole above; locations only" \
                if h["file"] in whole else ""
            out.append(f"\n### {h['file']}  ({where}, matter: "
                       f"{h['matter']}){carried}")
            last_file, last_heading = h["file"], object()
        if h["heading"] != last_heading:
            out.append(f"\n#### §{h['heading'] or '(before any heading)'}")
            last_heading = h["heading"]
        terms = ", ".join(f"{t} ({k})" for t, k in h["terms"])
        out.append(f"\n¶{h['n']} — terms: {terms}")
        if h["file"] not in whole:
            out.append(h["text"])
    return "\n".join(out).strip()


# -------------------------------------------------------------- the payload

def _prompt() -> str:
    from . import prompt_registry

    return prompt_registry.by_name("interlocutor").text()


def _draft_prompt() -> str:
    from . import prompt_registry

    return prompt_registry.by_name("interlocutor-draft").text()


def _section(label: str, body: str) -> str:
    return f"\n\n## {label}\n\n{body.strip()}\n"


def _numbered(text: str) -> str:
    return "\n\n".join(f"[¶{n}] {u}" for n, u in enumerate(_units(text), 1))


def _terms_block(terms: list[dict]) -> str:
    lines = []
    for t in terms:
        also = f" (also: {', '.join(t['aliases'])})" if t["aliases"] else ""
        why = f" — {t['reason']}" if t["reason"] else ""
        lines.append(f"- {t['name']}{also} [{t['kind']}]{why}")
    return "\n".join(lines)


def _book_block(db: Database, manuscript: dict, files: dict[str, str],
                selected: list[str]) -> str:
    """Reading-order toc with each file's summary, marked `!!` when stale or
    missing. Ungated: a summary read can never take a run down."""
    from . import summaries as sums

    try:
        rows = sums.all_summaries(db, manuscript["id"])
    except Exception:
        rows = {}
    order, _ = reading_order(files)
    parts, matter = _part_map(files), matter_map(files)
    chosen = set(selected)
    out = []
    for name in order:
        row = rows.get(name)
        mark = ""
        if not row or not (row.get("summary") or "").strip():
            mark = "  !! no summary"
        elif row.get("source_hash") != sums._hash(files[name]):
            mark = "  !! summary stale"
        scope = "" if name in chosen else "  (outside this run's scope)"
        where = f"part: {parts.get(name)}" if parts.get(name) else "unfiled"
        words = len(files[name].split())
        out.append(f"[{name}]  {where}, matter: {matter.get(name, 'main')}, "
                   f"{words} words{scope}{mark}")
        if row and (row.get("summary") or "").strip():
            out.append(row["summary"].strip())
        out.append("")
    return "\n".join(out).strip()


def _concept_block(db: Database, manuscript: dict, terms: list[dict],
                   hits: list[dict]) -> str:
    """Ratified notes for every graph concept that IS a book-kind term
    the scan actually hit — the author's settled definition of the word
    the critic is being asked to read."""
    hit_terms = {t.lower() for h in hits for t, k in h["terms"]
                 if k == "book"}
    labels: dict[str, set[str]] = {}
    for t in terms:
        if t["kind"] == "book" and t["name"].lower() in hit_terms:
            labels[t["name"].lower()] = {t["name"].lower(),
                                         *(a.lower() for a in t["aliases"])}
    if not labels:
        return ""
    wanted = set().union(*labels.values())
    lines = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired'", (manuscript["id"],),
    ):
        names = {n.lower() for n in node_names(node)}
        if names & wanted:
            aliases = [a for a in loads(node["aliases"], []) if a]
            also = f" (also: {', '.join(sorted(aliases))})" if aliases else ""
            notes = (node["notes"] or "").strip() or "(no notes)"
            lines.append(f"- {node['name']}{also} — {notes}")
    return "\n".join(sorted(lines))


def _whole_block(files: dict[str, str], names: list[str]) -> str:
    return "\n\n".join(f"### {n}\n\n{_numbered(files[n])}" for n in names)


def _today() -> str:
    return _dt.date.today().isoformat()


def build_payload(db: Database, manuscript: dict, name: str,
                  file_names: list[str] | None = None,
                  engaged: list[str] = (), position: list[str] = (),
                  out: Path | None = None, max_terms: int = MAX_TERMS,
                  max_per_term: int = MAX_PER_TERM,
                  seed: int | None = None) -> dict:
    """The run's payload. No model call. The scan is deterministic; the
    MENTIONS shown are a SAMPLE of it (terms, then locations per term),
    fresh each run unless `seed` is given, and the coverage table says
    what the sample is a sample of.

    `file_names` narrows the scan (a chapter or a part opener selects its
    descendants); `engaged` and `position` ADD to the artifact's own lists
    — the flags exist because "the connections essay" is the common case
    and not the rule."""
    files = read_manuscript_files(Path(manuscript["path"]))
    meta, prose = load(manuscript, name, files)
    known = content_files(files)
    for label, extra in (("--engaged", engaged), ("--position", position)):
        bad = [f for f in extra if f not in known]
        if bad:
            raise LookupError(f"{label} names {', '.join(bad)}, which is "
                              "not a manuscript file")
    engaged_all = [*meta["engaged"], *[f for f in engaged
                                       if f not in meta["engaged"]]]
    position_all = [*meta["position"], *[f for f in position
                                         if f not in meta["position"]]]
    selected = (select_chapters(files, file_names) if file_names
                else reading_order(files)[0])
    all_hits = scan(files, meta["terms"], selected)
    sample = sample_hits(all_hits, max_terms, max_per_term, seed)
    sample["_all_hits"] = all_hits
    hits = sample["hits"]
    whole = set(engaged_all) | set(position_all)
    coverage = coverage_block(sample)
    mentions = coverage + "\n\n" + mentions_block(hits, whole)

    out = Path(out) if out else None
    report_path = f"{out}.report.md" if out else "<payload>.report.md"
    manifest_path = f"{out}.manifest.json" if out else "<payload>.manifest.json"
    scope = ", ".join(file_names) if file_names else "whole book"

    blocks = [_prompt().strip()]
    blocks.append(_section("THE INTERLOCUTOR (ratified by the author)", prose))
    blocks.append(_section(
        "TERMS (the scan's vocabulary — `book` terms are the AUTHOR'S "
        "mapping of this book's words onto the tradition, not evidence)",
        _terms_block(meta["terms"])))
    blocks.append(_section(
        "THE BOOK (reading order; a `!!` summary is not to be trusted)",
        _book_block(db, manuscript, files, selected)))
    if position_all:
        blocks.append(_section(
            "AUTHOR POSITION (carried whole — the authoritative statement "
            "of what the book holds)", _whole_block(files, position_all)))
    if engaged_all:
        blocks.append(_section(
            "ENGAGED (carried whole — where the book has already engaged "
            "this interlocutor)", _whole_block(files, engaged_all)))
    notes = _concept_block(db, manuscript, meta["terms"], all_hits)
    if notes:
        blocks.append(_section(
            "CONCEPT NOTES (the author's ratified definitions of the book "
            "terms the scan hit)", notes))
    blocks.append(_section(
        f"MENTIONS (a SAMPLE of the scan — {len(hits)} of "
        f"{len(all_hits)} unit(s); scope: {scope}; seed {sample['seed']}. "
        "The coverage table says where the rest are; open a file whole "
        "when a term's unshown units matter)",
        mentions))
    readable = "\n".join(f"- {n}" for n in selected)
    blocks.append(_section(
        "MANUSCRIPT ROOT (you may open any file below whole, and must "
        "record every one you open in your Baseline)",
        f"{Path(manuscript['path']).resolve()}\n\n{readable}"))
    blocks.append(_section(
        "WRITE TO",
        f"Report (markdown): {report_path}\nManifest (JSON): {manifest_path}\n"
        f"Manuscript name for the report title: {manuscript['name']}\n"
        f"Interlocutor: {name}\nDate: {_today()}\nScope: {scope}\n"
        f"Seed: {sample['seed']}"))
    payload = "".join(blocks).strip() + "\n"
    result = {"payload": payload, "mentions": mentions, "hits": hits,
              "all_hits": all_hits, "seed": sample["seed"],
              "sampled_terms": sample["chosen_terms"],
              "all_terms": sample["all_terms"],
              "selected": selected, "engaged": engaged_all,
              "position": position_all, "scope": scope,
              "report_path": report_path, "manifest_path": manifest_path,
              "size": len(payload)}
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        Path(f"{out}.mentions.md").write_text(mentions + "\n",
                                              encoding="utf-8")
        result["out"] = str(out)
    return result


def position_on_record(manuscript: dict) -> list[str]:
    """The `position` files the installed interlocutors agree on — every
    file any of them carries, ordered by how many carry it, then by
    reading order. The author's position is a property of the book, not
    of one critic, so a new artifact is seeded from what is on record."""
    counts: dict[str, int] = {}
    for row in list_interlocutors(manuscript):
        for f in row["position"]:
            counts[f] = counts.get(f, 0) + 1
    if not counts:
        return []
    files = read_manuscript_files(Path(manuscript["path"]))
    order, _ = reading_order(files)
    rank = {n: i for i, n in enumerate(order)}
    return sorted(counts, key=lambda f: (-counts[f], rank.get(f, 10**6)))


def build_draft_payload(db: Database, manuscript: dict, name: str,
                        engaged: list[str], out: Path | None = None) -> dict:
    """The bootstrap payload for a NEW critic's artifact (design §2.1).
    Refuses without an engaged essay: the comparison is the author's
    input, and a critic nobody has written about waits for one."""
    if not _NAME.match(name or ""):
        raise InterlocutorError("interlocutor names are short kebab-case "
                                "slugs, e.g. 'basilides'")
    if not engaged:
        raise InterlocutorError(
            "a draft needs at least one --engaged essay: the concept "
            "comparison is the author's input, never the subagent's guess. "
            "For a critic no essay engages yet, write a rough comparison "
            "first and pass it as --engaged.")
    files = read_manuscript_files(Path(manuscript["path"]))
    known = content_files(files)
    bad = [f for f in engaged if f not in known]
    if bad:
        raise LookupError(f"--engaged names {', '.join(bad)}, which is not "
                          "a manuscript file")
    exemplars = []
    on_record: list[str] = []
    for row in list_interlocutors(manuscript):
        if row["terms"]:
            exemplars.append(f"### _interlocutors/{row['name']}.md\n\n"
                             + _path(manuscript, row["name"]).read_text(
                                 encoding="utf-8").strip())
            on_record.append(f"- {row['name']}: "
                             f"{', '.join(row['position']) or '(none)'}")
    suggested = position_on_record(manuscript)
    concepts = []
    for node in db.all(
        "SELECT * FROM concept_nodes WHERE manuscript_id = ? "
        "AND status != 'retired' ORDER BY name", (manuscript["id"],),
    ):
        aliases = [a for a in loads(node["aliases"], []) if a]
        also = f" (also: {', '.join(sorted(aliases))})" if aliases else ""
        concepts.append(f"- {node['name']}{also}")
    out = Path(out) if out else None
    artifact_path = f"{out}.artifact.md" if out else "<payload>.artifact.md"
    blocks = [_draft_prompt().strip()]
    blocks.append(_section("EXEMPLARS (ratified artifacts on this manuscript)",
                           "\n\n".join(exemplars) or "(none installed yet)"))
    blocks.append(_section(
        "POSITION ON RECORD (what the installed interlocutors carry whole as "
        "the author's position — set `position` to the suggestion unless "
        "the author has said otherwise)",
        ("\n".join(on_record) + f"\n\nSuggested: position = "
         f"{json.dumps(suggested)}") if on_record
        else "(no interlocutor installed yet; leave `position` empty and "
             "the author will set it)"))
    blocks.append(_section(
        "ENGAGED (the author's own comparison — the mapping comes from here)",
        _whole_block(files, engaged)))
    blocks.append(_section(
        "THE BOOK'S CONCEPTS (ratified names and aliases — a `book` term "
        "must be one of these, spelled as it is here)",
        "\n".join(concepts) or "(no concepts on record)"))
    blocks.append(_section(
        "WRITE TO", f"Artifact: {artifact_path}\nInterlocutor: {name}\n"
        f"Manuscript: {manuscript['name']}"))
    payload = "".join(blocks).strip() + "\n"
    result = {"payload": payload, "artifact_path": artifact_path,
              "size": len(payload)}
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
        result["out"] = str(out)
    return result


# --------------------------------------------------------------- the report

_FINDING = re.compile(r"^###\s+([OM]\d+)\.\s*(.*)$")
_MARK = re.compile(r"\[(sure|check)\]\s*$")
_KEY = re.compile(r"^([A-Z][A-Za-z ]{1,30}):\s*(.*)$")
_BASELINE = re.compile(r"^-\s+`?([^`\s]+\.md)`?\s*(?:[—–-]\s*(.*))?$")
_FILE_REF = re.compile(r"`([^`]+\.md)`")
_QUOTE = re.compile(r"[\"“]([^\"”]+)[\"”]")
KNOWN_KEYS = ("Bears on", "The objection", "Verdict", "Where addressed",
              "Inference", "Missing premise", "Improvement", "Passage",
              "What the tradition holds", "Contested", "Repair",
              "Footnote")
VERDICTS = ("addressed", "partial", "unaddressed")


def parse_report(text: str) -> dict:
    """The subagent's report, read back. Liberal about prose, strict about
    the lines the gate needs: finding headings, the key lines, and the
    Baseline list."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    baseline: list[dict] = []
    findings: list[dict] = []
    section = None
    current: dict | None = None
    key: str | None = None
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("## "):
            section = line[3:].strip().lower()
            current, key = None, None
            continue
        m = _FINDING.match(line)
        if m:
            fid, rest = m.group(1), m.group(2).strip()
            mark = _MARK.search(rest)
            locus = None
            title = rest
            if mark:
                head = rest[:mark.start()].rstrip()
                parts = re.split(r"\s+[—–]\s+", head, maxsplit=1)
                if len(parts) == 2 and parts[1].strip():
                    title, locus = parts[0].strip(), parts[1].strip()
            current = {"id": fid, "kind": "objection" if fid[0] == "O"
                       else "misattribution", "title": title,
                       "locus": locus, "mark": mark.group(1) if mark else None,
                       "fields": {}}
            findings.append(current)
            key = None
            continue
        if section == "baseline":
            b = _BASELINE.match(line.strip())
            if b:
                what = (b.group(2) or "").strip()
                heading = None
                if what and what.lower() != "whole":
                    heading = re.sub(r"\s*¶.*$", "", what.lstrip("§ ")).strip()
                baseline.append({"file": b.group(1), "heading": heading,
                                 "whole": (not what) or what.lower() == "whole"})
            continue
        if current is None:
            continue
        k = _KEY.match(line)
        if k and k.group(1) in KNOWN_KEYS:
            key = k.group(1)
            current["fields"][key] = k.group(2).strip()
            continue
        if key and line.strip() and not line.startswith("#"):
            current["fields"][key] += " " + line.strip()
        elif not line.strip():
            key = None
    for f in findings:
        v = (f["fields"].get("Verdict") or "").strip().lower()
        f["verdict"] = next((x for x in VERDICTS if v.startswith(x)), None)
    return {"baseline": baseline, "findings": findings}


def _flat(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _quotes_of(field: str) -> list[tuple[str | None, str]]:
    """[(file, quote)] pairs from a `Bears on:` / `Passage:` / `Where
    addressed:` line. A line may carry several pairs — "`a.md` §X — "…";
    `b.md` §Y — "…"" — so each quote is paired with the NEAREST file
    reference before it, never with the first one on the line. (The first
    live run dropped five sound findings before this was fixed.)"""
    if not field:
        return []
    refs = [(m.start(), m.group(1)) for m in _FILE_REF.finditer(field)]
    out = []
    for q in _QUOTE.finditer(field):
        file = None
        for pos, name in refs:
            if pos < q.start():
                file = name
        out.append((file, q.group(1).strip()))
    return out


def _headings_of(text: str) -> set[str]:
    return {h for h in (_heading_text(u) for u in _units(text)) if h}


def verify_report(files: dict[str, str], parsed: dict) -> dict:
    """The gate. Baseline failures refuse the whole import; per-finding
    failures drop the finding and are counted."""
    known = content_files(files)
    flats = {n: _flat(t) for n, t in known.items()}
    baseline_errors: list[str] = []
    for b in parsed["baseline"]:
        if b["file"] not in known:
            baseline_errors.append(f"baseline names {b['file']}, which is "
                                   "not a manuscript file")
            continue
        if b["heading"] is not None:
            if _norm_heading(b["heading"]).lower() not in {
                    h.lower() for h in _headings_of(known[b["file"]])}:
                baseline_errors.append(
                    f"baseline names §{b['heading']} in {b['file']}, and "
                    "that file has no such heading")
    kept: list[dict] = []
    dropped: list[dict] = []

    def quote_ok(field_name: str, f: dict, required: bool) -> str | None:
        field = f["fields"].get(field_name, "")
        if not field:
            return f"no `{field_name}:` line" if required else None
        pairs = _quotes_of(field)
        if not pairs:
            return f"`{field_name}:` carries no quoted passage"
        for file, q in pairs:
            if file is None or file not in known:
                return (f"`{field_name}:` quote has no manuscript file "
                        f"named in backticks before it: “{q[:60]}”")
            if _flat(q) not in flats[file]:
                return (f"`{field_name}:` quote is not verbatim in {file}: "
                        f"“{q[:60]}…”" if len(q) > 60 else
                        f"`{field_name}:` quote is not verbatim in {file}: "
                        f"“{q}”")
            f.setdefault("files", set()).add(file)
        return None

    for f in parsed["findings"]:
        reason = None
        if not f["locus"] or not f["mark"]:
            reason = ("no locus — the heading must end `— <locus> [sure]` "
                      "or `[check]`")
        elif f["kind"] == "objection":
            if f["verdict"] is None:
                reason = "no `Verdict:` of addressed | partial | unaddressed"
            else:
                reason = quote_ok("Bears on", f, required=True)
                if reason is None and f["verdict"] == "addressed":
                    reason = quote_ok("Where addressed", f, required=True)
                elif reason is None:
                    reason = quote_ok("Where addressed", f, required=False)
                if reason is None and f["verdict"] != "addressed" \
                        and not f["fields"].get("Improvement"):
                    reason = f"{f['verdict']} with no `Improvement:` line"
        else:
            reason = quote_ok("Passage", f, required=True)
        if reason:
            dropped.append({"id": f["id"], "title": f["title"],
                            "reason": reason})
        else:
            kept.append(f)
    return {"baseline_errors": baseline_errors, "kept": kept,
            "dropped": dropped}


_UNIT_ID = re.compile(r"^([OM]\d+)\b")


def gate_manifest(manifest: dict, kept: list[dict],
                  files: dict[str, str]) -> tuple[dict, list[dict]]:
    """Join manifest items to surviving findings. An item that points at a
    dropped or unknown finding, at an `addressed` objection, or at a scope
    the manuscript does not have, is dropped and counted."""
    if not isinstance(manifest, dict) or "source" not in manifest \
            or not isinstance(manifest.get("items"), list):
        raise InterlocutorError(
            "the manifest must be {\"source\": {\"name\", \"detail\"}, "
            "\"items\": [...]} — the critique manifest shape")
    by_id = {f["id"]: f for f in kept}
    known = content_files(files)
    items, dropped = [], []
    for item in manifest["items"]:
        unit = str(item.get("unit", ""))
        m = _UNIT_ID.match(unit)
        why = None
        if item.get("kind") != "intent":
            why = "interlocutor items are intents; other kinds are refused"
        elif not m or m.group(1) not in by_id:
            why = ("unit does not open with the id of a surviving finding "
                   f"({unit[:40]!r})")
        elif by_id[m.group(1)].get("verdict") == "addressed":
            why = f"{m.group(1)} is addressed — an addressed objection asks " \
                  "for nothing"
        elif item.get("scope") is not None and item["scope"] not in known:
            why = f"scope {item['scope']!r} is not a manuscript file"
        elif not str(item.get("text", "")).strip():
            why = "empty text"
        if why:
            dropped.append({"unit": unit, "reason": why})
        else:
            items.append({"kind": "intent", "unit": unit,
                          "ordinal": int(item.get("ordinal") or
                                         len(items) + 1),
                          "scope": item.get("scope"),
                          "text": str(item["text"]).strip()})
    return {"source": manifest["source"], "items": items}, dropped


def import_run(db: Database, manuscript: dict, name: str,
               payload: Path) -> dict:
    """Verify what the subagent wrote beside `payload`, land it in
    `_critiques/`, and import the manifest through the critique road."""
    from . import critique

    payload = Path(payload)
    report_path = Path(f"{payload}.report.md")
    manifest_path = Path(f"{payload}.manifest.json")
    mentions_path = Path(f"{payload}.mentions.md")
    for p in (report_path, manifest_path):
        if not p.is_file():
            raise InterlocutorError(f"nothing to import: {p} is missing — "
                                    "the subagent writes both files beside "
                                    "the payload")
    files = read_manuscript_files(Path(manuscript["path"]))
    load(manuscript, name, files)          # the artifact must still parse
    body = report_path.read_text(encoding="utf-8")
    parsed = parse_report(body)
    gate = verify_report(files, parsed)
    if gate["baseline_errors"]:
        raise InterlocutorError(
            "import refused — the baseline does not match the manuscript:\n"
            + "\n".join(f"  - {e}" for e in gate["baseline_errors"]))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise InterlocutorError(f"the manifest is not valid JSON: {err}") \
            from None
    manifest, dropped_items = gate_manifest(manifest, gate["kept"], files)

    date = _today()
    mentions = mentions_path.read_text(encoding="utf-8").strip() \
        if mentions_path.is_file() else "(mentions sidecar missing)"
    verdicts = {v: sum(1 for f in gate["kept"]
                       if f["kind"] == "objection" and f["verdict"] == v)
                for v in VERDICTS}
    header = [
        f"<!-- interlocutor: {name}; date: {date}; manuscript: "
        f"{manuscript['name']} -->",
        f"# {name} reads {manuscript['name']} — {date}",
        "",
        f"Objections kept: {sum(verdicts.values())} "
        f"(addressed {verdicts['addressed']}, partial {verdicts['partial']}, "
        f"unaddressed {verdicts['unaddressed']}); misattributions kept: "
        f"{sum(1 for f in gate['kept'] if f['kind'] == 'misattribution')}; "
        f"findings dropped at the gate: {len(gate['dropped'])}; manifest "
        f"items dropped: {len(dropped_items)}; imported: "
        f"{len(manifest['items'])}.",
    ]
    if gate["dropped"]:
        header.append("")
        header.append("Dropped at the gate:")
        header.extend(f"- {d['id']} {d['title']}: {d['reason']}"
                      for d in gate["dropped"])
    header.append("")
    header.append("## Mentions (deterministic scan)")
    header.append("")
    header.append(mentions)
    header.append("")
    header.append("---")
    header.append("")
    final = "\n".join(header) + body.strip() + "\n"

    crit_dir = Path(manuscript["path"]) / CRITIQUE_DIR
    crit_dir.mkdir(exist_ok=True)
    stem = f"{date}-interlocutor-{name}"
    landed_md = crit_dir / f"{stem}.md"
    landed_json = crit_dir / f"{stem}.json"
    n = 2
    while landed_md.exists() or landed_json.exists():
        landed_md = crit_dir / f"{stem}-{n}.md"
        landed_json = crit_dir / f"{stem}-{n}.json"
        n += 1
    landed_md.write_text(final, encoding="utf-8")
    landed_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False)
                           + "\n", encoding="utf-8")
    imported = critique.import_manifest(db, manuscript["id"], manifest)
    return {"interlocutor": name, "report": str(landed_md),
            "manifest": str(landed_json), "kept": gate["kept"],
            "dropped": gate["dropped"], "items_dropped": dropped_items,
            "verdicts": verdicts, "imported": imported}
