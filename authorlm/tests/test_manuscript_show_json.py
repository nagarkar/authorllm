"""`manuscript show --json`: the manuscript's id, identity, and export title in one JSON line,
for other tools (publishinglm keys its books on the id). Hermetic: a scratch workspace.
Run: python3 tests/test_manuscript_show_json.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import test_passes  # noqa: E402,F401  (pins config/.env away from the real ones)

from authorlm.cli import main as cli_main  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            cli_main(argv)
        except SystemExit as exc:
            code = 1 if isinstance(exc.code, str) else (exc.code or 0)
    return code, out.getvalue()


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        ws, ms = tmp / "ws", tmp / "ws" / "Book"
        ms.mkdir(parents=True)
        (ms / "one.md").write_text("# One\n\nText.\n", encoding="utf-8")
        (ms / "toc.toml").write_text('[[chapter]]\nfile = "one.md"\n', encoding="utf-8")
        run(["--workspace", str(ws), "init", "--name", "Book", "--path", str(ms),
             "--extraction", "off"])
        code, text = run(["--workspace", str(ws), "manuscript", "show", "--json"])
        assert code == 0, text
        shown = json.loads(text.strip().splitlines()[-1])
        assert shown["id"].startswith("ms-") and shown["name"] == "Book", shown
        assert shown["title"] == "Book" and shown["subtitle"] == "", shown  # default: the name
        code, text = run(["--workspace", str(ws), "manuscript", "show"])
        assert code == 0 and f"id: {shown['id']}" in text, text
        print("ALL CHECKS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
