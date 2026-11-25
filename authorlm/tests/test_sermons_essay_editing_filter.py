"""The library filter `sermons-essay-editing` carries the house rules of
the sermons-essay-editing skill (Seven More Sermons to the Dead, the
modern-English essays), and runs through the ordinary global-filter flow.

Hermetic: a temp workspace, the real filter library, and a prelude driven
by a piped reply (the chat path), so no model is called.
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api  # noqa: E402
from authorlm import filters as flt  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

NAME = "sermons-essay-editing"
PASSED = 0

# One phrase per rule of the source skill, copied from it: a rule that is
# reworded away or dropped from the filter fails here by name.
SKILL_RULES = {
    "voice: declarative": "Authoritative, declarative, non-defensive",
    "voice: em dashes": "Em dashes\n  are welcome",
    "voice: no hedges": "No hedges and no meta-commentary in the body",
    "voice: concrete image": "Prefer the concrete image to the abstract "
                             "formulation",
    "sentences: wordy": "another\n  puts herself first",
    "sentences: verb objects": "Every verb that needs an object gets one",
    "sentences: vary nouns": "without blurring who is meant",
    "sentences: no restating": "Never state the same idea twice in a row "
                               "in different words",
    "sentences: definitions once": "state a definition once",
    "sentences: key lines": "Every question is an\n  experiment, and every "
                            "answer an experience.",
    "sentences: aphorisms": "The horizon is eternal",
    "sentences: book vocabulary": "whether a better\n  choice exists",
    "structure: stories and claims": "introduced either by a question it "
                                     "answers or by a claim it illustrates",
    "structure: consequences": "Don't cut the outcome of a story",
    "structure: transitions": "What, then, is a\n  possibility? An image "
                              "will help.",
    "structure: starkest stakes": "the\n  last meal",
    "structure: metaphor": "roads stay roads",
    "structure: technical terms": "clock\n  time",
    "structure: examples": "not from the reader's distaste",
    "structure: empirical claims": "criminals\n  always get caught",
    "structure: closing line": "it need not bridge to the next\n  essay",
    "structure: subheadings": "The Limits of Measurement",
    "references: sequencing": "use only\n  terms defined up to the essay's "
                              "position",
    "references: back-reference": "As the commentary on\n  Sermon One "
                                  "showed",
    "references: footnotes or inline": "inline when the\n  reference is "
                                       "itself a definition",
    "references: name sources": "Lotus Sutra,\n  Mencius, Prashna Upanishad",
    "references: parables unnamed": "own parables stay unnamed",
    "references: Sindhu": "the land\n  the Sindhu waters",
    "references: Nietzsche": "Nietzsche is never named or cited\n  as "
                             "authority in the body",
    "references: sermons": "Sermons are the primary authority",
    "footnotes: marker format": "format `[^Ex]`",
    "footnotes: uses": "Mill vs. this standard",
    "footnotes: verify": "written from memory for verification against a\n"
                         "  source",
    "workflow: diagnose first": "Diagnose first, with specific citations",
    "workflow: pushback": "frank pushback with counterexamples",
    "workflow: soundness": "one definition per term, examples actually "
                           "judged by the stated\n  standard",
    "workflow: length": "Remove a story only if\n  another already does "
                        "its work",
    "scope: sermons elsewhere": "kjv-bunyan-style",
}

# The owner's rulings of 2026-11-25 (SMSTTD style laws se-ef3dbcd6d7bf and
# se-29c4a9a82d22), where the filter departs from the source skill: his
# words, and the reading of each rule that follows from them.
RULINGS = {
    "V1 carries the em-dash ruling": "Em dashes are welcome as long as they "
        "are used to explain something inline and the explanation is "
        "expendable. Never stack multiple em-dash pairs in one sentence.",
    "S5 quotes the aphorism ruling": "An aphorism either closes an "
        "explanation or starts one, but never replaces one.",
    "S5 quotes the elevation ruling": "Elevating key lines is not aphoristic "
        "compression. We can have aphorisms and they are elevation.",
    "S5: start or end are both right": "its own line at the start or at the "
        "end of its explanation",
    "S5: position alone is never queried": "is never queried or moved for "
        "its position",
    "S5: only the buried or replacing line is asked about": "buried in the "
        "middle of its explanation, or stands in place of the explanation "
        "(ask)",
    "S5 keeps the restatement cut": "delete the weaker restatements "
                                    "elsewhere",
    "S6: a lead-up or an explanation after": "either a preceding sentence "
        "that leads to it or an explanation after it",
    "S6: only neither is a fault": "Only an aphorism with neither is a fault",
}

# The wording these rulings replaced; none of it may come back as a rule.
SUPERSEDED = ("never remove one for being a dash",
              "at the end of its section and would have to move",
              "The horizon is eternal\" needs a preceding sentence that "
              "leads to it.")

# What the skill asks for that a unit-by-unit filter cannot do; each is
# named in the filter's Boundaries section so the gap is stated, not silent.
BOUNDARIES = ("Revision markup", "Placeholders",
              "Section-by-section delivery", "Old-content triage",
              "Drafting")

RULE_IDS = ([f"V{i}" for i in range(1, 4)] + [f"S{i}" for i in range(1, 8)]
            + [f"A{i}" for i in range(1, 11)]
            + [f"R{i}" for i in range(1, 8)]
            + [f"F{i}" for i in range(1, 4)]
            + [f"P{i}" for i in range(1, 4)])

ESSAY = """# On Choosing

What, then, is a possibility? An image will help.

A girl stands before two roads. The child looks at one, and the child \
looks at the other, and the child cannot decide.

Perhaps it may be said that criminals always get caught.
"""

REGISTRY = ("position: first essay of Part II\n"
            "metaphors: roads (units 2)\n"
            "stories and images: the girl at two roads (unit 2), "
            "introduced by a question it answers; lesson none")


def check(label: str, condition: bool, context: object = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _text(payload: object) -> str:
    """A payload's four blocks as one string."""
    return "\n".join(text for _name, text in payload.blocks)


def check_artifact() -> None:
    old = os.environ.pop(flt.LIBRARY_ENV, None)
    try:
        meta, body = flt.load_filter({"path": "/nonexistent"}, NAME)
        shown = flt.show_filter({"path": "/nonexistent"}, NAME)
    finally:
        if old is not None:
            os.environ[flt.LIBRARY_ENV] = old
    check("the filter is global: each unit is judged against the registry",
          meta["class"] == "global", meta)
    check("it asks for the neighbouring essays' summaries (vocabulary "
          "sequencing and back-references turn on them)",
          meta["summaries"] is True, meta)
    check("it joins no shared reading and declares no prelude key",
          meta["reading"] is None and meta["prelude"] is None, meta)
    check("it is a library filter", shown["source"] == "library", shown)
    flat = _flat(body)
    for label, phrase in SKILL_RULES.items():
        check(f"carries the skill's rule, {label}", _flat(phrase) in flat,
              phrase)
    for label, phrase in RULINGS.items():
        check(f"owner's ruling, {label}", _flat(phrase) in flat, phrase)
    check("each changed rule says it departs from the source skill",
          flat.count("Departs from the source skill") == 3
          and flat.count("the owner's ruling of 2026-11-25") == 3)
    for phrase in SUPERSEDED:
        check(f"superseded wording is gone: {phrase[:40]}",
              phrase not in flat, phrase)
    for rule in RULE_IDS:
        check(f"rule {rule} is defined", f"**{rule} " in body, rule)
    for name in BOUNDARIES:
        check(f"boundary stated: {name}", f"**{name}" in body, name)
    check("the prompt holds no reserved form characters",
          not any(mark in body for mark in ("<<", ">>", "{{", "}}")))
    check("the prelude is defined (a global filter's registry)",
          "## The registry: the prelude" in body)


def check_runs_as_a_global_filter() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-sermons-filter-"))
    old = os.environ.pop(flt.LIBRARY_ENV, None)
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "choosing.md").write_text(ESSAY)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        man = api.get_manuscript(db)
        rows = {r["name"]: r for r in flt.list_filters(man)}
        check("a new manuscript lists it with no install step",
              rows[NAME]["source"] == "library"
              and rows[NAME]["error"] is None, rows.get(NAME))
        check("filter list shows its title",
              rows[NAME]["summary"] == "Seven More Sermons: essay editing",
              rows[NAME]["summary"])

        asked = api.filter_prelude(db, man, {}, NAME, "choosing.md")
        check("the prelude asks for the registry (nothing frozen yet)",
              asked["payload"] is not None and asked["registry"] is None,
              str(asked)[:400])
        prelude = _text(asked["payload"])
        check("the prelude payload carries the filter and the essay",
              "terms used early" in prelude
              and "A girl stands before two roads." in prelude,
              prelude[:400])
        check("the prelude payload carries the neighbouring-essays block",
              "NEIGHBOURING ESSAYS" in prelude)
        frozen = api.filter_prelude(db, man, {}, NAME, "choosing.md",
                                    reply=json.dumps({"registry": REGISTRY}))
        check("a piped registry is frozen into the run",
              frozen["registry"] == REGISTRY, frozen)

        run = api.filter_run(db, man, {}, NAME, "choosing.md")
        payload = _text(run["payload"])
        check("the run payload carries the rules, the frozen registry and "
              "every unit",
              "**R5 Avoid the word" in payload and REGISTRY in payload
              and "criminals always get caught" in payload, payload[:400])
    finally:
        if old is not None:
            os.environ[flt.LIBRARY_ENV] = old
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    for t in (check_artifact, check_runs_as_a_global_filter):
        print(f"{t.__name__}:")
        t()
    print(f"ALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
