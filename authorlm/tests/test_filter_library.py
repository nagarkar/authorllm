"""The shared filter library: `authorlm/library/filters/` is read in
place by every manuscript, and a manuscript's own `_filters/<name>.md`
overrides a library filter of the same name.

Hermetic: AUTHORLM_FILTER_LIBRARY points at a temp library, except the
last check, which parses the real library so a malformed shared filter
fails here rather than at someone's first run.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import filters as flt


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _artifact(title: str, klass: str = "global") -> str:
    return f'---\nclass = "{klass}"\n---\n\n# {title}\n\nThe prompt.\n'


def test_library_seen_and_overridden() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lib = Path(tmp) / "lib"
        lib.mkdir()
        (lib / "shared-one.md").write_text(_artifact("Shared one"))
        (lib / "shared-two.md").write_text(_artifact("Shared two"))
        (lib / "Not_A_Name.md").write_text(_artifact("Bad name"))
        ms = {"path": str(Path(tmp) / "book")}
        Path(ms["path"]).mkdir()
        old = os.environ.get(flt.LIBRARY_ENV)
        os.environ[flt.LIBRARY_ENV] = str(lib)
        try:
            rows = {r["name"]: r for r in flt.list_filters(ms)}
            check("a manuscript with no _filters/ sees every library filter",
                  set(rows) == {"shared-one", "shared-two"}, sorted(rows))
            check("library rows say so",
                  all(r["source"] == "library" for r in rows.values()))
            meta, body = flt.load_filter(ms, "shared-one")
            check("load_filter reads a library filter in place",
                  meta["class"] == "global" and "Shared one" in body, body)
            shown = flt.show_filter(ms, "shared-two")
            check("show_filter names the library path and source",
                  shown["source"] == "library"
                  and shown["path"] == str(lib / "shared-two.md"), shown)

            path, _meta = flt.add_filter(ms, "shared-one",
                                         _artifact("Book's own", "sequential"))
            check("filter add of a library name writes to the manuscript",
                  path == Path(ms["path"]) / "_filters" / "shared-one.md"
                  and "Shared one" in (lib / "shared-one.md").read_text())
            rows = {r["name"]: r for r in flt.list_filters(ms)}
            check("the manuscript's own copy overrides the library's",
                  rows["shared-one"]["source"] == "manuscript"
                  and rows["shared-one"]["class"] == "sequential"
                  and rows["shared-two"]["source"] == "library", rows)
            check("an override is listed once",
                  [r["name"] for r in flt.list_filters(ms)]
                  == ["shared-one", "shared-two"])

            try:
                flt.load_filter(ms, "no-such")
                check("a missing filter raises", False)
            except LookupError as err:
                check("a missing filter names library filters too",
                      "shared-two" in str(err), err)

            os.environ[flt.LIBRARY_ENV] = str(Path(tmp) / "absent")
            check("an absent library leaves the manuscript alone",
                  [r["name"] for r in flt.list_filters(ms)] == ["shared-one"])
        finally:
            if old is None:
                os.environ.pop(flt.LIBRARY_ENV, None)
            else:
                os.environ[flt.LIBRARY_ENV] = old


def test_real_library_parses() -> None:
    old = os.environ.pop(flt.LIBRARY_ENV, None)
    try:
        lib = flt.library_dir()
        names = sorted(p.stem for p in lib.glob("*.md"))
        check("the repo library holds the strunk-and-white filters",
              {"strunk-and-white-concision", "strunk-and-white-grammar",
               "strunk-and-white-mechanics"} <= set(names), names)
        for path in sorted(lib.glob("*.md")):
            meta, _body = flt.parse_front_matter(path.read_text("utf-8"))
            check(f"{path.name} parses", meta["class"] in flt.CLASSES)
    finally:
        if old is not None:
            os.environ[flt.LIBRARY_ENV] = old


def main() -> None:
    for t in (test_library_seen_and_overridden, test_real_library_parses):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
