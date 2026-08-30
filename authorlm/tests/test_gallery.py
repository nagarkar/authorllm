"""Hermetic tests for tools/gallery.py — self-contained image-review pages.

Run: python3 tests/test_gallery.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"

def _assert_offline() -> None:
    """Fail loudly if the real project config or .env leaks into a test.

    Before config moved into the repo, a test workspace simply had no
    config.toml, so the LLM was off and no suite could make a billed
    call. Now a checkout always HAS an enabled config, and that safety
    came from nothing but the pins above — so it is asserted, not
    assumed."""
    import os as _os

    from authorlm import paths as _paths

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"
    leaked = [v for v in _paths_vendor_vars() if _os.environ.get(v)]
    assert not leaked, f"test isolation broken: vendor keys in env: {leaked}"


def _paths_vendor_vars() -> list:
    from authorlm.llm import VENDOR_KEY_ENV

    return sorted(VENDOR_KEY_ENV.values())


import base64
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASSED = 0
ROOT = Path(__file__).resolve().parents[2]
GALLERY_PATH = ROOT / "tools" / "gallery.py"


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def load_gallery():
    spec = importlib.util.spec_from_file_location("gallery", GALLERY_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def main_test() -> None:
    gallery = load_gallery()
    check("esc escapes &, <, and > for HTML text nodes",
          gallery.esc('a & b <c> "ok"') == "a &amp; b &lt;c&gt; \"ok\"")

    root = Path(tempfile.mkdtemp(prefix="authorlm-gallery-"))
    try:
        png = root / "slot.png"
        # Minimal 1x1 PNG.
        png.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
        fig = gallery.figure(png, "ascending.md — <monk> & idols")
        check("figure embeds a data URI and escapes the caption/alt",
              'src="data:image/png;base64,' in fig
              and "<figcaption>ascending.md — &lt;monk&gt; &amp; idols"
              in fig
              and 'alt="ascending.md — &lt;monk&gt; &amp; idols"' in fig,
              fig)
        fig_bare = gallery.figure(png, None)
        check("figure without caption uses the stem as alt and omits figcaption",
              'alt="slot"' in fig_bare and "<figcaption>" not in fig_bare,
              fig_bare)

        bad = root / "notes.txt"
        bad.write_text("not an image")
        try:
            gallery.figure(bad, "x")
            raised = False
        except SystemExit as err:
            raised = "unsupported image type" in str(err)
        check("figure rejects unsupported image types", raised)

        out = root / "page.html"
        spec = root / "spec.json"
        spec.write_text(json.dumps({
            "title": "Review <1>",
            "note": "Say yes & place",
            "images": [{"path": str(png),
                        "caption": "PROPOSED for basilides.md"}],
        }), encoding="utf-8")
        argv = ["gallery.py", "--out", str(out), "--json", str(spec)]
        old_argv, old_stdout = sys.argv, sys.stdout
        sys.argv, sys.stdout = argv, io.StringIO()
        try:
            gallery.main()
            printed = sys.stdout.getvalue()
        finally:
            sys.argv, sys.stdout = old_argv, old_stdout
        html = out.read_text(encoding="utf-8")
        check("JSON spec builds a titled page with note and embedded image",
              "<title>Review &lt;1&gt;</title>" in html
              and '<p class="note">Say yes &amp; place</p>' in html
              and "PROPOSED for basilides.md" in html
              and "data:image/png;base64," in html
              and out.name in printed,
              html[:500])

        out2 = root / "cli.html"
        argv = ["gallery.py", "--out", str(out2), "--title", "T",
                f"{png}::caption via ::"]
        sys.argv, sys.stdout = argv, io.StringIO()
        try:
            gallery.main()
        finally:
            sys.argv, sys.stdout = old_argv, old_stdout
        check("positional PATH::CAPTION partitions the caption",
              "caption via ::" in out2.read_text(encoding="utf-8"))

        argv = ["gallery.py", "--out", str(root / "empty.html"),
                "--title", "T"]
        sys.argv, sys.stdout = argv, io.StringIO()
        try:
            gallery.main()
            empty_ok = False
        except SystemExit as err:
            empty_ok = "no images given" in str(err)
        finally:
            sys.argv, sys.stdout = old_argv, old_stdout
        check("main refuses an empty image list", empty_ok)
    finally:
        import shutil
        shutil.rmtree(root, ignore_errors=True)

    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
