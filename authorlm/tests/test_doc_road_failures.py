"""The Doc-road failures of 2026-10-30 (discernment.md, SMSTTD),
reproduced against the surgical double. Each scenario asserts the
behaviour the author needs, so on a code base with the defect it FAILS;
the runner reports every scenario and exits non-zero if any failed.

1. resolve leaves forms in the tab (a known new half "." from an old
   thread matches the end of every form that ends in a full stop)
2. a paragraph held for an open comment is reverted locally by resolve
3. an unterminated form: pull, merge and decide
4. doc merge drops the image lines under illustration tags
5. a push after a stale or approved margin thread

Hermetic: Google is `_GoogleFake`, the fake checked against the live
recordings in tests/doc_cache/ (test_doc_fakes_vs_google.py).
Run: python3 tests/test_doc_road_failures.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_doc_road_verbs import (  # noqa: E402
    _GoogleFake, _comment, _fixture, _google, _run)

from authorlm import api, gdocs  # noqa: E402
from authorlm import threads as th  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


class Failed(AssertionError):
    pass


def expect(label: str, condition: bool, context: str = "") -> None:
    if not condition:
        raise Failed(f"{label}\n{context}")


def scenario(name: str):
    def wrap(fn):
        def run(root: Path) -> None:
            try:
                fn(root)
            except Failed as err:
                RESULTS.append((name, False, str(err)))
            except Exception:  # noqa: BLE001 — a crash is a failure too
                RESULTS.append((name, False, traceback.format_exc()))
            else:
                RESULTS.append((name, True, ""))
        run.__name__ = fn.__name__
        return run
    return wrap


ESSAY = ("# **Discernment**\n\n"
         "They are two tendencies of one reality, defined together.\n\n"
         "We must be watchful. Habit carries discernment forward.\n\n"
         "Now consider the other side. Where discernment deepens, "
         "distinctions appear.\n\n"
         "The question is always the same. Does it deepen the capacity "
         "to distinguish and become? Or does it narrow it?\n")

MIXED = [
    # whole-paragraph replacement
    {"old": "They are two tendencies of one reality, defined together.",
     "new": "They are the two tendencies of one reality, each defined "
            "by the other.", "note": "r1"},
    # in-paragraph (part of a paragraph) replacement
    {"old": "We must be watchful.",
     "new": "A tradition turned to stone is momentum.", "note": "r2"},
    # heading addition
    {"old": "", "new": "## **Momentum and Alignment**", "note": "r3",
     "anchor_paragraph": 3},
    # replacement of the paragraph the additions follow
    {"old": "Now consider the other side. Where discernment deepens, "
            "distinctions appear.",
     "new": "Momentum does not keep itself.", "note": "r4"},
    # several additions at one anchor
    {"old": "", "new": "This danger can be met.", "note": "r5",
     "anchor_paragraph": 4},
    {"old": "", "new": "The greater danger lies elsewhere.", "note": "r6",
     "anchor_paragraph": 4},
    {"old": "", "new": "Alignment answers this danger.", "note": "r7",
     "anchor_paragraph": 4},
    # in-sentence replacement
    {"old": "Does it deepen the capacity to distinguish and become?",
     "new": "Does it deepen the capacity to distinguish and become, "
            "alongside everything that is choosing?", "note": "r8"},
]


def _old_thread(db, manuscript, new: str, old: str = "x, y",
                state: str = "cleaned", origin: str = "external") -> None:
    """A settled thread from an earlier round (discernment.md had one
    whose new half was '.', 2026-10-29)."""
    row = ko_fields("dt")
    row.update(
        manuscript_id=manuscript["id"], origin_type=origin,
        origin_id=f"old:{new}:{state}", file="solo.md", anchor_quote=None,
        proposed_old=old, proposed_new=new, note="old", state=state,
        our_reply_ids="[]", last_author_reply_id=None, scope_kind="file",
        scope_ref="solo.md", metadata=json.dumps({"kind": "replace"}))
    db.insert("doc_threads", row)


def _stage_resolve(root: Path, name: str, before=None, between=None):
    ws, db, manuscript, ms, fake = _fixture(root, name, ESSAY,
                                            fake_cls=_GoogleFake)
    # An open comment elsewhere in the tab, as discernment.md had: it
    # sends every push of this tab down the surgical (diff) path.
    fake.margin = [_comment("c-09", "The question is always the same.")]
    if before:
        before(db, manuscript, fake, ms)
    staged = api.stage_revisions(db, manuscript, "solo.md", MIXED,
                                 "ending-r1", fake, fake)
    expect("all eight forms staged",
           len(staged["written"]) == 8 and not staged["failed"],
           str(staged["failed"]) + "\n" + fake.tab_text("solo.md"))
    if between:
        between(db, manuscript, fake, ms)
    with contextlib.redirect_stdout(io.StringIO()):
        done = api.resolve_revisions(db, manuscript, "solo.md", "ending-r1",
                                     fake, fake)
    return done, fake, ms


def _tab_clean(fake) -> bool:
    tab = fake.tab_text("solo.md")
    return not any(m in tab for m in ("<<", ">>", "{{", "}}"))


# ----------------------------------------------------------------- 1


@scenario("1a stage+resolve, mixed forms, all accepted")
def all_accepted(root: Path) -> None:
    done, fake, ms = _stage_resolve(root, "s1a")
    final = (ms / "solo.md").read_text()
    expect("eight accepted", done["accepted"] == 8, json.dumps(done,
                                                               default=str))
    expect("the tab holds no form", _tab_clean(fake), fake.tab_text("solo.md"))
    expect("the local file holds every new half",
           all(r["new"].replace("**", "") in final.replace("**", "")
               for r in MIXED), final)


@scenario("1b ... with an old settled thread whose new half is '.'")
def dot_thread(root: Path) -> None:
    done, fake, ms = _stage_resolve(
        root, "s1b",
        before=lambda db, m, f, ms: _old_thread(db, m, "."))
    expect("the tab holds no form, and resolve does not say it is clean "
           "when it is not",
           _tab_clean(fake) and not done["tab_still_marked"],
           json.dumps(done, default=str) + "\n" + fake.tab_text("solo.md"))


@scenario("1c ... one modified, one declined")
def modified_declined(root: Path) -> None:
    def between(db, m, fake, ms):
        fake.edit("tab-2", "{{This danger can be met.}}",
                  "{{This danger can be met, and planned for.}}")
        fake.edit("tab-2", "{{Alignment answers this danger.}}", "")

    done, fake, ms = _stage_resolve(root, "s1c", between=between)
    final = (ms / "solo.md").read_text()
    expect("6 accepted, 1 modified, 1 declined",
           (done["accepted"], done["modified"], done["declined"])
           == (6, 1, 1), json.dumps(done, default=str))
    expect("the modified wording landed, the declined one did not",
           "This danger can be met, and planned for." in final
           and "Alignment answers this danger." not in final, final)
    expect("the tab holds no form", _tab_clean(fake), fake.tab_text("solo.md"))


# ----------------------------------------------------------------- 2


@scenario("2 a held paragraph survives the next resolve")
def held_then_resolve(root: Path) -> None:
    ws, db, manuscript, ms, fake = _fixture(root, "s2", ESSAY,
                                            fake_cls=_GoogleFake)
    fake.margin = [_comment("c-01", "Habit carries discernment forward.")]
    mine = ESSAY.replace("Habit carries discernment forward.",
                         "Habit carries discernment forward. A new line "
                         "the author wrote.")
    (ms / "solo.md").write_text(mine)
    out = gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                         docs_service=fake, guard=True)
    expect("push holds the commented paragraph and says so",
           bool(out.get("held")), json.dumps(out, default=str))
    revs = [{"old": "Does it deepen the capacity to distinguish and "
                    "become?", "new": "Does it deepen us?", "note": "r"}]
    api.stage_revisions(db, manuscript, "solo.md", revs, "o", fake, fake)
    with contextlib.redirect_stdout(io.StringIO()):
        done = api.resolve_revisions(db, manuscript, "solo.md", "o",
                                     fake, fake)
    final = (ms / "solo.md").read_text()
    expect("the resolve landed", done["landed"] and "Does it deepen us?"
           in final, json.dumps(done, default=str))
    expect("the held local edit is still in the file",
           "A new line the author wrote." in final, final)


# ----------------------------------------------------------------- 3


UNTERMINATED = ("We must be watchful.[^D1] Habit carries discernment "
                "forward.[^D2]")


def _unterminated(root: Path, name: str):
    essay = ESSAY.replace(
        "We must be watchful. Habit carries discernment forward.",
        UNTERMINATED)
    ws, db, manuscript, ms, fake = _fixture(root, name, essay,
                                            fake_cls=_GoogleFake)
    # Two margin proposals; the first lost its closing braces in the Doc.
    fake.margin = [_comment("c-01", "We must be watchful."),
                   _comment("c-02", "Now consider the other side.")]
    fake.edit("tab-2", "We must be watchful.",
              "<<We must be watchful.>>{{We must keep watch.")
    fake.edit("tab-2", "Now consider the other side.",
              "<<Now consider the other side.>>{{Consider the other "
              "side.}}")
    th.create_thread(db, manuscript["id"], "c-01", "solo.md",
                     "We must be watchful.", "We must be watchful.",
                     "We must keep watch.", "n", None, scope_kind="file",
                     scope_ref="solo.md")
    th.create_thread(db, manuscript["id"], "c-02", "solo.md",
                     "Now consider the other side.",
                     "Now consider the other side.",
                     "Consider the other side.", "n", None,
                     scope_kind="file", scope_ref="solo.md")
    return ws, db, manuscript, ms, fake, essay


@scenario("3a unterminated form: pull keeps the text between forms")
def unterminated_pull(root: Path) -> None:
    ws, db, manuscript, ms, fake, essay = _unterminated(root, "s3a")
    rep = gdocs.pull_doc(db, manuscript, query="solo.md", service=fake,
                         docs_service=fake, with_comments=False)
    text = (ms / "solo.md").read_text()
    expect("no conflict is reported", not rep["conflicts"],
           json.dumps(rep, default=str))
    expect("the local file keeps every paragraph and footnote tag",
           "Habit carries discernment forward.[^D2]" in text
           and "Now consider the other side." in text, text)


@scenario("3b unterminated form: decide --approve writes no garbled text")
def unterminated_decide(root: Path) -> None:
    ws, db, manuscript, ms, fake, essay = _unterminated(root, "s3b")
    before = (ms / "solo.md").read_text()
    try:
        gdocs.decide_thread(db, manuscript, "c-01", "approve",
                            service=fake, docs_service=fake)
    except Exception as err:  # noqa: BLE001 — a refusal is acceptable
        text = (ms / "solo.md").read_text()
        expect("a refusal leaves the file as it was", text == before,
               str(err))
        return
    text = (ms / "solo.md").read_text()
    expect("the local file holds no marker and keeps the next "
           "paragraph's text",
           "<<" not in text and "{{" not in text and "}}" not in text
           and "Now consider the other side." in text
           and "Habit carries discernment forward.[^D2]" in text, text)


@scenario("3c unterminated form: doc merge keeps the text")
def unterminated_merge(root: Path) -> None:
    ws, db, manuscript, ms, fake, essay = _unterminated(root, "s3c")
    out = gdocs.doc_merge(db, manuscript, "solo.md", fake, fake)
    merged = Path(out["out"]).read_text()
    expect("the draft keeps every paragraph and footnote tag",
           "Habit carries discernment forward.[^D2]" in merged
           and "Now consider the other side" in merged, merged)


# ----------------------------------------------------------------- 4


ILLUS = (ESSAY + "\n[Illustration: a lone tracker ⇢ lone.md]\n"
         "![](_illustrations/lone-0a1b2c3d-01.png)\n")


@scenario("4 doc merge keeps the image lines")
def merge_images(root: Path) -> None:
    ws, db, manuscript, ms, fake = _fixture(
        root, "s4", gdocs.strip_embed_lines(ILLUS), fake_cls=_GoogleFake)
    (ms / "_illustrations").mkdir(exist_ok=True)
    (ms / "_illustrations" / "lone-0a1b2c3d-01.png").write_bytes(b"\x89PNG")
    (ms / "solo.md").write_text(ILLUS.replace("Now consider",
                                              "Now, consider"))
    fake.edit("tab-2", "The question is always", "The question is ever")
    out = gdocs.doc_merge(db, manuscript, "solo.md", fake, fake)
    merged = Path(out["out"]).read_text()
    expect("both sides' edits merged",
           "Now, consider" in merged and "The question is ever" in merged,
           merged)
    expect("the image line under the illustration tag is kept",
           "![](_illustrations/lone-0a1b2c3d-01.png)" in merged, merged)


# ----------------------------------------------------------------- 5


def _leftover(root: Path, name: str, state: str):
    ws, db, manuscript, ms, fake = _fixture(root, name, ESSAY,
                                            fake_cls=_GoogleFake)
    fake.margin = [_comment("c-01", "We must be watchful.")]
    fake.edit("tab-2", "We must be watchful.",
              "<<We must be watchful.>>{{We must keep watch.}}")
    row = th.create_thread(db, manuscript["id"], "c-01", "solo.md",
                           "We must be watchful.", "We must be watchful.",
                           "We must keep watch.", "n", None,
                           scope_kind="file", scope_ref="solo.md")
    th.set_state(db, row, state)
    keep = "We must keep watch." if state == "cleaned" else \
        "We must be watchful."
    (ms / "solo.md").write_text(ESSAY.replace(
        "We must be watchful.", keep).replace(
        "Habit carries", "Habit, edited locally, carries"))
    return db, manuscript, ms, fake


@scenario("5a push after an approved thread whose form was left")
def push_after_cleaned(root: Path) -> None:
    db, manuscript, ms, fake = _leftover(root, "s5a", "cleaned")
    gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                   docs_service=fake, guard=False)
    expect("the push lands and the form is gone",
           "Habit, edited locally, carries" in fake.tab_text("solo.md")
           and _tab_clean(fake), fake.tab_text("solo.md"))


@scenario("5b push after a stale thread whose form was left")
def push_after_stale(root: Path) -> None:
    db, manuscript, ms, fake = _leftover(root, "s5b", "stale")
    out = gdocs.push_doc(db, manuscript, "solo.md", service=fake,
                         docs_service=fake, guard=False)
    # The thread's comment is still open on that paragraph, so the edit
    # may be held (and reported); what must not happen is a push that can
    # only go through by hand surgery or --rebuild.
    expect("the push goes through, the stale form is taken out, and the "
           "edit either lands or is reported as held",
           _tab_clean(fake) and (
               "Habit, edited locally, carries" in fake.tab_text("solo.md")
               or out.get("held")),
           json.dumps(out, default=str) + "\n" + fake.tab_text("solo.md"))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="authorlm-failures-"))
    try:
        for fn in (all_accepted, dot_thread, modified_declined,
                   held_then_resolve, unterminated_pull, unterminated_decide,
                   unterminated_merge, merge_images, push_after_cleaned,
                   push_after_stale):
            fn(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    for name, ok, why in RESULTS:
        print(f"{'pass' if ok else 'FAIL'}  {name}")
        if not ok:
            print("      " + why.strip().replace("\n", "\n      ")[:1500])
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
