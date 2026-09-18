"""Emptied green half is a decline (filter-pass design §8.3).

Docs styles {{new}} green. Documented Doc-settle UX: leave alone to
accept, empty the green half to decline, reword to revise. Before the
fix, emptying {{}} deleted the paragraph (accepted empty rewrite) and
deleting the green run left bare <<old>> in the manuscript.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authorlm import api, passes
from authorlm.cli import main as cli_main
import contextlib
import io


PASSED = 0


def check(label: str, cond: bool, detail="") -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  OK  {label}")
    else:
        print(f"  FAIL  {label}: {detail}")
        raise SystemExit(1)


def main() -> None:
    old = "Omega closes the essay on a falling cadence."
    written = [{"id": "t1", "proposed_old": old, "proposed_new": "SIX REDONE."}]

    print("final_text_from_marked: emptied / orphan / accept:")
    emptied = "Lead.\n\n<<" + old + ">>{{}}\n\nTail.\n"
    final, forms = passes.final_text_from_marked(emptied, written=written)
    check("<<old>>{{}} keeps the old paragraph (no leftover braces)",
          final == "Lead.\n\n" + old + "\n\nTail.\n" and forms == [],
          repr(final))

    orphan = "Lead.\n\n<<" + old + ">>\n\nTail.\n"
    final2, forms2 = passes.final_text_from_marked(orphan, written=written)
    check("bare <<old>> (Docs deleted the green run) collapses to old",
          final2 == "Lead.\n\n" + old + "\n\nTail.\n" and forms2 == [],
          repr(final2))

    wrapped = "Lead.\n\n~~<<" + old + ">>~~\n\nTail.\n"
    final3, forms3 = passes.final_text_from_marked(wrapped, written=written)
    check("~~-wrapped orphan <<old>> also collapses to old",
          final3 == "Lead.\n\n" + old + "\n\nTail.\n" and forms3 == [],
          repr(final3))

    kept = "Lead.\n\n<<" + old + ">>{{SIX REDONE.}}\n\nTail.\n"
    final4, forms4 = passes.final_text_from_marked(kept, written=written)
    check("untouched form still accepts the new half",
          final4 == "Lead.\n\nSIX REDONE.\n\nTail.\n" and len(forms4) == 1,
          repr(final4))

    print("record_resolution: emptied green → declined evidence:")
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        essay = "# T\n\nAlpha opens.\n\n" + old + "\n"
        (ms / "solo.md").write_text(essay)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        mid = manuscript["id"]
        from authorlm.db import ko_fields
        row = ko_fields("dt")
        row.update(
            manuscript_id=mid, origin_type="filter",
            origin_id="run:1", file="solo.md",
            proposed_old=old, proposed_new="SIX REDONE.",
            note="why", state="written",
            metadata=json.dumps({"anchor_paragraph": 3, "kind": "replace"}))
        tid = row["id"]
        db.insert("doc_threads", row)

        thread = dict(db.one("SELECT * FROM doc_threads WHERE id = ?", (tid,)))
        final, forms = passes.final_text_from_marked(emptied, written=[thread])
        diffs = passes.record_resolution(
            db, mid, "solo.md", forms, origin_type="filter",
            evidence_type="filter_edit", final_text=final)
        state = db.one("SELECT state FROM doc_threads WHERE id = ?",
                       (tid,))["state"]
        signals = [r["signal"] for r in db.all(
            "SELECT signal FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type = 'filter_edit'", (mid,))]
        check("emptied form closes as declined with no revise diff",
              state == "declined" and diffs == [] and signals == ["declined"],
              f"state={state} diffs={diffs} signals={signals}")

    print("diff_push comparison: author {{…}} is not false drift:")
    from authorlm import threads as th
    para = "The template expands {{title}} at render time."
    pending = ("<<Alpha opens the essay and says a thing.>>"
               "{{Alpha opens, saying it once.}}")
    check("strip_replacements preserves author braces (strip_pending would not)",
          th.strip_replacements(para)[0] == para
          and th.strip_pending(para)[0] != para,
          (th.strip_replacements(para)[0], th.strip_pending(para)[0]))
    check("strip_replacements still collapses real <<old>>{{new}} to old",
          th.strip_replacements(pending)[0]
          == "Alpha opens the essay and says a thing.",
          th.strip_replacements(pending)[0])

    print(f"\nALL CHECKS PASSED ({PASSED})")


if __name__ == "__main__":
    main()
