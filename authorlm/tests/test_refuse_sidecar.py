"""`structure.refuse_sidecar` is the named door every filter / lens /
intent verb shares for `pronunciations.md` and `manifest.md`.

Those files are markdown and Doc-mirrored, so a generic "not in reading
order" refusal would be misleading. This helper must refuse by name with
one stable message — three call sites, one string — or the dictionary
can silently reclassify as an essay. Nothing in the suite called it
directly before this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm.structure import (
    SIDECAR_FILES,
    TOC_FILENAME,
    content_files,
    is_sidecar,
    is_structural,
    refuse_sidecar,
)


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_predicates() -> None:
    for name in SIDECAR_FILES:
        check(f"{name} is a sidecar", is_sidecar(name))
        check(f"{name} is structural", is_structural(name))
    check("toc.toml is structural but not a sidecar",
          is_structural(TOC_FILENAME) and not is_sidecar(TOC_FILENAME))
    check("an essay is neither",
          not is_sidecar("essay.md") and not is_structural("essay.md"))


def test_content_files_strips_structural() -> None:
    files = {
        TOC_FILENAME: "[[chapter]]\nfile = \"a.md\"\n",
        "pronunciations.md": "| Term | Say it | Note |\n",
        "manifest.md": "# Manifest\n",
        "a.md": "prose",
        "b.md": "more",
    }
    got = content_files(files)
    check("content_files keeps only prose",
          got == {"a.md": "prose", "b.md": "more"}, repr(got))


def test_refuse_raises_for_each_sidecar() -> None:
    for name in SIDECAR_FILES:
        try:
            refuse_sidecar(name)
        except ValueError as err:
            msg = str(err)
            check(f"{name} refusal names the file", name in msg, msg)
            check(f"{name} refusal says pronunciation dictionary",
                  "pronunciation dictionary" in msg, msg)
            check(f"{name} refusal forbids filter/lens/critique",
                  "no filter" in msg and "no lens" in msg
                  and "no critique" in msg, msg)
        else:
            raise AssertionError(f"{name} was not refused")


def test_refuse_allows_essay() -> None:
    refuse_sidecar("essay.md")
    refuse_sidecar("part/chapter.md")
    check("non-sidecars pass through", True)


def main() -> None:
    for t in (test_predicates, test_content_files_strips_structural,
              test_refuse_raises_for_each_sidecar, test_refuse_allows_essay):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
