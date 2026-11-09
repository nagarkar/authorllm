"""`devedit.apply_item` must see the original passage exactly once.

`test_devedit` covers accept-with-rewrite and the zero-occurrence case
(file rewritten by hand). The other failure mode — the original now
appears twice — would otherwise replace only the first hit and leave a
stale twin, silently corrupting the essay. Pin both refusals and the
single-hit replace directly on `apply_item`.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import devedit


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _ms(tmp: Path, body: str) -> tuple[dict, Path]:
    root = tmp / "MS"
    root.mkdir(parents=True)
    path = root / "one.md"
    path.write_text(body, encoding="utf-8")
    return {"name": "MS", "id": "ms", "path": str(root)}, path


def test_apply_item_requires_a_unique_original() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ms, path = _ms(Path(tmp), "# One\n\nSame line.\n\nSame line.\n")
        item = {"id": "a1", "file": "one.md",
                "original": "Same line.", "proposed": "NEW."}
        try:
            devedit.apply_item(ms, item)
            check("duplicate original is refused", False)
        except RuntimeError as err:
            check("duplicate original names the count and file",
                  "occurs 2 times" in str(err) and "one.md" in str(err), err)
        check("duplicate refuse leaves the file untouched",
              path.read_text(encoding="utf-8").count("Same line.") == 2)

        ms0, path0 = _ms(Path(tmp) / "gone", "# One\n\nRewritten.\n")
        item0 = {"id": "a0", "file": "one.md",
                 "original": "Same line.", "proposed": "NEW."}
        try:
            devedit.apply_item(ms0, item0)
            check("missing original is refused", False)
        except RuntimeError as err:
            check("missing original names a zero count",
                  "occurs 0 times" in str(err), err)
        check("missing refuse leaves the author's text",
              "Rewritten." in path0.read_text(encoding="utf-8"))

        ms1, path1 = _ms(Path(tmp) / "ok",
                         "# One\n\nIt moves all.\n\nKeep me.\n")
        item1 = {"id": "a2", "file": "one.md",
                 "original": "It moves all.", "proposed": "It moves everything."}
        check("unique original returns the file name",
              devedit.apply_item(ms1, item1) == "one.md")
        body = path1.read_text(encoding="utf-8")
        check("unique original is replaced once with the proposal",
              "It moves everything." in body and "It moves all." not in body
              and "Keep me." in body, body)
        # Author rewrite overrides the proposal.
        ms2, path2 = _ms(Path(tmp) / "rewrite",
                         "# One\n\nIt moves all.\n")
        item2 = {"id": "a3", "file": "one.md",
                 "original": "It moves all.", "proposed": "It moves everything."}
        devedit.apply_item(ms2, item2, text="It moves the whole.")
        check("explicit text wins over proposed",
              path2.read_text(encoding="utf-8") == "# One\n\nIt moves the whole.\n")


def main() -> None:
    print("test_apply_item_requires_a_unique_original:")
    test_apply_item_requires_a_unique_original()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
