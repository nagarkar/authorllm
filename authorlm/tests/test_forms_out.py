"""`forms_page._forms_out` is the conflict-land hold: open producer forms
or other origin forms on the tab block landing until they are merged.

A missed hold lets a conflict overwrite a tab that still has forms out;
a false hold blocks a ready conflict. Also pins the thin helpers the
hold and conflict cards share (`ruling_of`, `_sides_key`, `_embed_lines`).
Hermetic — `open_threads` / `_other_forms` stubbed; no workspace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import forms_page, gdocs  # noqa: E402
from authorlm.forms_page import (  # noqa: E402
    RULING_KEY, _embed_lines, _forms_out, _sides_key, ruling_of,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_forms_out_none_when_tab_is_clear() -> None:
    with mock.patch.object(forms_page, "open_threads",
                           return_value={}) as open_fn, \
         mock.patch.object(forms_page, "_other_forms",
                           return_value={}) as other_fn:
        got = _forms_out("db", {"id": 1, "name": "ms"}, "one.md")
    check("open_threads asked for this file",
          open_fn.call_args.args[2] == "one.md", open_fn.call_args)
    check("_other_forms asked for this file",
          other_fn.call_args.args[2] == "one.md", other_fn.call_args)
    check("clear tab → None", got is None, got)


def test_forms_out_names_open_producer_forms() -> None:
    threads = {"one.md": [{"id": "t1"}, {"id": "t2"}]}
    with mock.patch.object(forms_page, "open_threads", return_value=threads), \
         mock.patch.object(forms_page, "_other_forms", return_value={}):
        got = _forms_out("db", {"id": 1}, "one.md")
    check("plural open forms",
          got is not None and "2 open forms" in got, got)
    check("says the conflict lands only after those forms merge",
          got is not None and "lands only once those forms are merged" in got,
          got)

    with mock.patch.object(forms_page, "open_threads",
                           return_value={"one.md": [{"id": "t1"}]}), \
         mock.patch.object(forms_page, "_other_forms", return_value={}):
        one = _forms_out("db", {"id": 1}, "one.md")
    check("singular open form",
          one is not None and "1 open form" in one
          and "forms" not in one.split("out.")[0], one)


def test_forms_out_names_other_origin_forms_and_joins() -> None:
    with mock.patch.object(forms_page, "open_threads",
                           return_value={"one.md": [{"id": "t1"}]}), \
         mock.patch.object(forms_page, "_other_forms",
                           return_value={"critique": 2, "margin": 1}):
        got = _forms_out("db", {"id": 1}, "one.md")
    check("joins open forms with other origins",
          got is not None
          and "1 open form" in got
          and "2 critique forms" in got
          and "1 margin form" in got
          and " and " in got, got)


def test_forms_out_others_only_when_no_producer_forms() -> None:
    with mock.patch.object(forms_page, "open_threads", return_value={}), \
         mock.patch.object(forms_page, "_other_forms",
                           return_value={"critique": 1}):
        got = _forms_out("db", {"id": 1}, "one.md")
    check("others alone still hold",
          got is not None and "1 critique form" in got
          and "open form" not in got, got)


def test_ruling_of_reads_page_ruling() -> None:
    check("missing metadata → None", ruling_of({}) is None)
    check("empty metadata → None",
          ruling_of({"metadata": "{}"}) is None)
    ruling = {"verdict": "accept", "text": "", "seen": "x", "at": "t"}
    got = ruling_of({"metadata": json.dumps({RULING_KEY: ruling})})
    check("returns the held ruling", got == ruling, got)


def test_sides_key_is_stable_hash_of_three_sides() -> None:
    a = _sides_key("local\n", "base\n", "doc\n")
    b = _sides_key("local\n", "base\n", "doc\n")
    c = _sides_key("local\n", "base\n", "other\n")
    check("same sides → same key", a == b == gdocs.text_hash(
        "local\n" + "\x00" + "base\n" + "\x00" + "doc\n"), a)
    check("changed side → new key", a != c, (a, c))


def test_embed_lines_keeps_only_illustration_embeds() -> None:
    text = (
        "A paragraph.\n"
        "![fig](./_illustrations/a.png)\n"
        "![skip](https://example.com/x.png)\n"
        "![ok](_illustrations/b.png)\n"
        "trailing\n"
    )
    got = _embed_lines(text)
    check("keeps relative _illustrations embeds only",
          got == ["![fig](./_illustrations/a.png)",
                  "![ok](_illustrations/b.png)"], got)


def main() -> None:
    for test in (
        test_forms_out_none_when_tab_is_clear,
        test_forms_out_names_open_producer_forms,
        test_forms_out_names_other_origin_forms_and_joins,
        test_forms_out_others_only_when_no_producer_forms,
        test_ruling_of_reads_page_ruling,
        test_sides_key_is_stable_hash_of_three_sides,
        test_embed_lines_keeps_only_illustration_embeds,
    ):
        print(f"{test.__name__}:")
        test()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
