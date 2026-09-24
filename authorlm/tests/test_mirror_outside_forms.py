"""Margin approval must not splice into a local pause form.

`_replace_pending` mirrors old→final into the local file. When that file
is mid filter/critique pause, an unanchored first-match replace rewrites
the OLD half of `<<…>>{{…}}` and resolve later collapses the foreign form
to the corrupted half — dropping the author's green rewording.

Run: python3 tests/test_mirror_outside_forms.py
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, gdocs, threads as th
from authorlm.cli import main as cli_main
from authorlm.db import ko_fields
from tests.test_passes import _SurgicalDocFake


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_pure_mirror() -> None:
    print("mirror_outside_forms:")
    old = "The refrain holds."
    green = "The chorus breaks, and that is the point."
    margin_new = "The refrain fails."

    inside = (f"Lead.\n\n"
              f"{th.render_pending(old + ' It always has.', green)}\n\n"
              f"Tail.\n")
    check("substring inside a pause form is refused",
          th.mirror_outside_forms(inside, old, margin_new) is None)

    exact = f"Lead.\n\n{th.render_pending(old, green)}\n\nTail.\n"
    check("exact OLD half of a pause form is refused",
          th.mirror_outside_forms(exact, old, margin_new) is None)
    check("pause green half is still intact after refuse",
          th.pending_forms(exact)[0]["new"] == green)

    plain = "Lead.\n\nThe refrain holds.\n\nTail.\n"
    out = th.mirror_outside_forms(plain, old, margin_new)
    check("plain canonical text still mirrors",
          out == "Lead.\n\nThe refrain fails.\n\nTail.\n", repr(out))

    mixed = (f"{th.render_pending(old + ' It always has.', green)}\n\n"
             f"{old}\n")
    mixed_out = th.mirror_outside_forms(mixed, old, margin_new)
    check("plain occurrence after a form still mirrors",
          mixed_out is not None
          and mixed_out.endswith("The refrain fails.\n")
          and th.pending_forms(mixed_out)[0]["new"] == green,
          repr(mixed_out))

    check("absent probe returns None",
          th.mirror_outside_forms("plain", "missing", "x") is None)


def test_replace_pending_preserves_local_pause(root: Path) -> None:
    print("gdocs._replace_pending leaves local pause forms alone:")
    old = "The claim is thin."
    filter_green = "The claim is the set of cases that matter."
    margin_new = "The claim is firmer."
    essay = (f"# Solo\n\n"
             f"{th.render_pending(old, filter_green)}\n\n"
             f"Tail paragraph.\n")
    ws = root / "pause-ws"
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

    planted = (f"# Solo\n\n"
               f"{th.render_pending(old, margin_new)}\n\n"
               f"Tail paragraph.\n")
    fake = _SurgicalDocFake([("book", ""), ("solo.md", planted)])
    bridge = gdocs.manuscript_bridge(manuscript)
    meta = gdocs._mapping(db, manuscript)
    links = meta.setdefault(bridge.meta_key, {})
    links["_master_id"] = fake.master_id
    links["_container_tab"] = "tab-1"
    links["solo.md"] = {"tab_id": "tab-2", "checked_out": False,
                        "pushed_hash": None}
    gdocs._save_mapping(db, manuscript, meta)

    thread = ko_fields("dt")
    thread.update(
        manuscript_id=manuscript["id"], origin_type="author_comment",
        origin_id="c-pause", file="solo.md",
        proposed_old=old, proposed_new=margin_new,
        note="strengthen", state="proposed",
        our_reply_ids="[]",
    )
    db.insert("doc_threads", thread)

    ok = gdocs._replace_pending(
        db, manuscript, thread, margin_new, fake, fake, bridge)
    local_after = (ms / "solo.md").read_text()
    check("_replace_pending reports success (Doc cleaned)", ok)
    check("local pause form is byte-identical",
          local_after == essay, repr(local_after))
    check("local green half is still the filter rewording",
          th.pending_forms(local_after)[0]["new"] == filter_green,
          th.pending_forms(local_after))
    tab = fake.tab_text("solo.md")
    check("Doc tab carries the margin approval",
          margin_new in tab and "<<" not in tab, tab)


def main() -> None:
    test_pure_mirror()
    with tempfile.TemporaryDirectory() as tmp:
        test_replace_pending_preserves_local_pause(Path(tmp))
    print("all ok")


if __name__ == "__main__":
    main()
