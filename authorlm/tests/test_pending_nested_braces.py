"""Regression: post-edited green halves may nest `{{…}}` (set notation).

Modified acceptance (margin-threads design) lets the author rewrite the
`{{new}}` half in place before resolve/approve. A non-greedy PENDING
regex and `find('}}')` closed at the first delimiter, so elaborating
the green run with `{{a, b}}` truncated the proposal, left residual
markers in the essay, and approved_text ate the inner braces via a
second INSERTION pass.

Plant-time `assert_no_pending_markers` still refuses delimiter-bearing
machine proposals; this suite locks the *scanner* that honours an
author post-edit already on the tab.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, gdocs, passes, threads as th
from authorlm.cli import main as cli_main
from authorlm.db import ko_fields
from tests.test_passes import _SurgicalDocFake


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _approve_nested(root: Path) -> None:
    """`gdocs._replace_pending` must locate the full nested green span."""
    print("gdocs._replace_pending with nested green half:")
    essay = ("# Solo\n\n"
             "The claim is thin.\n\n"
             "Tail paragraph.\n")
    ws = root / "approve-ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    (ms / "solo.md").write_text(essay)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, {})

    planted = ("# Solo\n\n"
               "<<The claim is thin.>>"
               "{{The claim is the set {{a, b}} of cases.}}\n\n"
               "Tail paragraph.\n")
    fake = _SurgicalDocFake([("book", ""), ("solo.md", planted)])
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault("gdocs", {})
    links["_master_id"] = "doc-fake"
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": False,
                        "pushed_hash": None}
    gdocs._save_mapping(db, manuscript, meta)

    thread = ko_fields("dt")
    thread.update(
        manuscript_id=manuscript["id"], origin_type="author_comment",
        origin_id="c-nested", file="solo.md", anchor_quote=None,
        proposed_old="The claim is thin.",
        proposed_new="The claim is thin.",
        note="test", state="proposed", our_reply_ids="[]",
        last_author_reply_id=None, scope_kind="file", scope_ref="solo.md",
        metadata=json.dumps({}))
    db.insert("doc_threads", thread)

    from authorlm.gdocs import manuscript_bridge

    bridge = manuscript_bridge(manuscript)
    ok = gdocs._replace_pending(
        db, manuscript, thread,
        "The claim is thin.",  # triggers actual_new read-back
        fake, fake, bridge)
    tab = fake.tab_text("solo.md")
    check("_replace_pending succeeds against a nested green half", ok)
    check("approve writes the full nested prose, no residual markers",
          "The claim is the set {{a, b}} of cases." in tab
          and "<<" not in tab and ">>{{" not in tab,
          tab)
    local = (ms / "solo.md").read_text()
    check("local file mirrors the nested acceptance",
          "The claim is the set {{a, b}} of cases." in local
          and "The claim is thin." not in local,
          local)
    refreshed = th.get_thread(db, manuscript["id"], "c-nested")
    check("thread.proposed_new records the author's nested post-edit",
          refreshed["proposed_new"]
          == "The claim is the set {{a, b}} of cases.",
          refreshed["proposed_new"])


def main() -> None:
    print("pending-form nested braces (post-edit green half):")
    marked = ("<<The claim is thin.>>"
              "{{The claim is the set {{a, b}} of cases.}}")
    forms = th.pending_forms(marked)
    check("pending_forms keeps the full nested green half",
          len(forms) == 1
          and forms[0]["kind"] == "replace"
          and forms[0]["old"] == "The claim is thin."
          and forms[0]["new"] == "The claim is the set {{a, b}} of cases."
          and forms[0]["end"] == len(marked),
          forms)
    strip, warns = th.strip_pending(marked)
    check("strip_pending collapses to OLD with no residual markers",
          strip == "The claim is thin." and not warns,
          (strip, warns))
    check("approved_text keeps nested braces in the green half",
          th.approved_text(marked)
          == "The claim is the set {{a, b}} of cases.")

    written = [{"proposed_old": "The claim is thin.",
                "proposed_new": "The claim is thin."}]
    final, matched = passes.final_text_from_marked(marked, written=written)
    check("final_text_from_marked accepts the post-edited nested green",
          final == "The claim is the set {{a, b}} of cases."
          and len(matched) == 1
          and matched[0]["new"] == final,
          final)

    # Insertion form: critique new-paragraph with nested braces.
    insert = "Lead.\n\n{{see {{a, b}} here}}\n\nTail.\n"
    iforms = th.pending_forms(insert)
    check("insertion pending_forms keeps nested braces",
          len(iforms) == 1 and iforms[0]["kind"] == "insert"
          and iforms[0]["new"] == "see {{a, b}} here",
          iforms)
    check("strip_pending drops a nested insertion paragraph cleanly",
          th.strip_pending(insert)[0] == "Lead.\n\nTail.\n")
    check("approved_text keeps nested insertion content",
          th.approved_text(insert) == "Lead.\n\nsee {{a, b}} here\n\nTail.\n")

    # Old half may already contain braces (literal); collapse must not
    # re-scan them as a fresh insertion and delete them.
    nested_old = "<<keep {{this}} literal>>{{replacement}}"
    check("pending_forms preserves braces inside the OLD half",
          th.pending_forms(nested_old)[0]["old"] == "keep {{this}} literal")
    check("strip_pending keeps braces that lived inside OLD",
          th.strip_pending(nested_old)[0] == "keep {{this}} literal")

    # ~~-wrapped replace (markdown export of strikethrough) still works.
    wrapped = "Lead.\n\n~~<<old span>>~~{{new span}}\n\nTail.\n"
    check("~~-wrapped replace still strips to OLD",
          th.strip_pending(wrapped)[0] == "Lead.\n\nold span\n\nTail.\n")
    check("~~-wrapped replace still approves to NEW",
          th.approved_text(wrapped) == "Lead.\n\nnew span\n\nTail.\n")

    # close_braced is the shared primitive _replace_pending uses.
    body = "prefix{{The set {{a, b}} of cases.}}suffix"
    end = th.close_braced(body, len("prefix{{"))
    check("close_braced balances nested pairs",
          end == len("prefix{{The set {{a, b}} of cases.}}")
          and body[len("prefix{{"):end - 2] == "The set {{a, b}} of cases.",
          end)
    check("close_braced returns None when unclosed",
          th.close_braced("{{no close", 2) is None)

    # Plant-time guard unchanged: machine proposals still refuse nested
    # delimiters (defense in depth for every write path).
    try:
        th.assert_no_pending_markers("safe", "f(x)={{a}}")
        raised = False
    except ValueError:
        raised = True
    check("assert_no_pending_markers still refuses nested plant-time new",
          raised)

    with tempfile.TemporaryDirectory() as tmp:
        _approve_nested(Path(tmp))

    print("all nested-brace checks passed")


if __name__ == "__main__":
    main()
