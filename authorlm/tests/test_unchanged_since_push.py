"""`gdocs.unchanged_since_push` is the audiobook page's Push skip door.

True only when the file's normalized, embed-free hash matches the last
`pushed_hash`. Wrong True skips a real edit; wrong False re-pushes every
unchanged sibling (2026-10-26: seven recap files for one changed line).
Local only — never calls Google.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("AUTHORLM_CONFIG", "/nonexistent/authorlm-unchanged/config.toml")
os.environ.setdefault("AUTHORLM_ENV", "/nonexistent/authorlm-unchanged/.env")
os.environ.setdefault("AUTHORLM_CLIENT", "none")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import api, gdocs
from authorlm.cli import main as cli_main


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _hash_text(text: str) -> str:
    normalized = gdocs.strip_embed_lines(gdocs.normalize_markdown(text))
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _fixture(root: Path):
    ws = root / "ws"
    ms = ws / "book"
    ms.mkdir(parents=True)
    text = "# Solo\n\nAlpha one.\n\nBeta two.\n"
    (ms / "solo.md").write_text(text, encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db)
    return db, manuscript, ms, text


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="authorlm-unchanged-") as tmp:
        root = Path(tmp)
        db, manuscript, ms, text = _fixture(root)

        check("missing mapping entry is not unchanged",
              gdocs.unchanged_since_push(db, manuscript, "solo.md") is False)

        meta = gdocs._mapping(db, manuscript)
        meta.setdefault("gdocs", {})["solo.md"] = {
            "tab_id": "t1", "pushed_hash": _hash_text(text)}
        gdocs._save_mapping(db, manuscript, meta)
        check("matching pushed_hash is unchanged",
              gdocs.unchanged_since_push(db, manuscript, "solo.md") is True)

        (ms / "solo.md").write_text("# Solo\n\nAlpha one edited.\n\nBeta two.\n",
                                    encoding="utf-8")
        check("local edit clears unchanged",
              gdocs.unchanged_since_push(db, manuscript, "solo.md") is False)

        (ms / "solo.md").write_text(text, encoding="utf-8")
        meta = gdocs._mapping(db, manuscript)
        meta["gdocs"]["solo.md"] = {"tab_id": "t1"}  # no pushed_hash
        gdocs._save_mapping(db, manuscript, meta)
        check("entry without pushed_hash is not unchanged",
              gdocs.unchanged_since_push(db, manuscript, "solo.md") is False)

        (ms / "solo.md").write_text(text, encoding="utf-8")
        meta = gdocs._mapping(db, manuscript)
        meta["gdocs"]["solo.md"] = {
            "tab_id": "t1", "pushed_hash": "deadbeefdeadbeef"}
        gdocs._save_mapping(db, manuscript, meta)
        check("stale pushed_hash is not unchanged",
              gdocs.unchanged_since_push(db, manuscript, "solo.md") is False)


if __name__ == "__main__":
    main()
    print("ok")
