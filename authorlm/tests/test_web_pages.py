"""The built web pages against their sources (`authorlm/web_pages.py`):
staleness by modification time, the rebuild `audio serve` runs at start,
`authorlm doctor`'s page checks, and the help texts of the roads the
workflow retired (`filter run`/`prelude`, `lens run`, `write start`),
which since #527 always need --skip-reason.

Hermetic: a temporary tree and a fake npm runner; no Node, no network.
The CLI checks run `main.py` in a subprocess, reading only help and the
package's own files.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authorlm import audiobook_server, web_pages  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _touch(path: Path, mtime: float, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _tree(built_at: float | None = 2000, source_at: float = 1000,
          node_modules_at: float | None = 1500) -> tuple[Path, Path]:
    """(package, web) for the audiobook page alone."""
    root = Path(tempfile.mkdtemp())
    package, web = root / "authorlm", root / "web"
    src = web / "audiobook"
    _touch(src / "src" / "main.tsx", source_at)
    _touch(src / "package.json", source_at)
    _touch(src / "package-lock.json", source_at)
    if node_modules_at is not None:
        _touch(src / "node_modules" / ".package-lock.json", node_modules_at)
    if built_at is not None:
        _touch(package / "audiobook_dist" / "index.html", built_at)
    return package, web


class FakeRun:
    """Records each npm call; `npm run build` writes the page, as vite does."""

    def __init__(self, package: Path, code: int = 0, raises: Exception | None = None):
        self.package, self.code, self.raises, self.calls = package, code, raises, []

    def __call__(self, cmd, cwd=None, **_kw):
        self.calls.append(cmd[1:])
        if self.raises:
            raise self.raises
        if cmd[1:] == ["run", "build"] and not self.code:
            out = self.package / "audiobook_dist" / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("<html>new", encoding="utf-8")
        return SimpleNamespace(returncode=self.code, stdout="out", stderr="boom")


def test_pages_match_setup() -> None:
    spec = importlib.util.spec_from_file_location("authorlm_setup", ROOT / "setup.py")
    setup_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup_mod)
    check("web_pages.PAGES is setup.py's PAGES", web_pages.PAGES == setup_mod.PAGES,
          (web_pages.PAGES, setup_mod.PAGES))


def test_page_state() -> None:
    package, web = _tree()
    check("a page newer than every source is ok",
          web_pages.page_state("audiobook", package, web)["state"] == "ok")
    _touch(web / "audiobook" / "node_modules" / "x.js", 9000)
    _touch(web / "audiobook" / "tsconfig.app.tsbuildinfo", 9000)
    check("node_modules and tsc's cache are not sources",
          web_pages.page_state("audiobook", package, web)["state"] == "ok")
    _touch(web / "audiobook" / "src" / "main.tsx", 3000)
    s = web_pages.page_state("audiobook", package, web)
    check("a source newer than the page makes it stale, naming the source",
          s["state"] == "stale" and "main.tsx" in s["detail"], s)
    package, web = _tree(built_at=None)
    check("an unbuilt page is missing",
          web_pages.page_state("audiobook", package, web)["state"] == "missing")
    empty = Path(tempfile.mkdtemp())
    check("an install without web/ has no sources to compare",
          web_pages.page_state("audiobook", empty, empty / "web")["state"]
          == "no-sources")


def test_fresh_page_is_not_rebuilt() -> None:
    package, web = _tree()
    run = FakeRun(package)
    r = web_pages.rebuild_if_stale("audiobook", package, web, npm="npm", env={},
                                   run=run, log=lambda m: None)
    check("a fresh page runs nothing", r["state"] == "ok" and run.calls == [],
          (r, run.calls))


def test_stale_page_is_rebuilt() -> None:
    package, web = _tree(built_at=1000, source_at=2000, node_modules_at=3000)
    run = FakeRun(package)
    r = web_pages.rebuild_if_stale("audiobook", package, web, npm="npm", env={},
                                   run=run, log=lambda m: None)
    check("a stale page is rebuilt with `npm run build` alone",
          r["state"] == "rebuilt" and run.calls == [["run", "build"]], (r, run.calls))
    check("afterwards the page is ok",
          web_pages.page_state("audiobook", package, web)["state"] == "ok")


def test_npm_ci_when_packages_are_missing_or_old() -> None:
    for label, nm_at in (("missing", None), ("older than the lockfile", 500)):
        package, web = _tree(built_at=None, source_at=1000, node_modules_at=nm_at)
        run = FakeRun(package)
        r = web_pages.rebuild_if_stale("audiobook", package, web, npm="npm",
                                       env={}, run=run, log=lambda m: None)
        check(f"node_modules {label}: `npm ci` before the build",
              r["state"] == "rebuilt"
              and run.calls == [["ci"], ["run", "build"]], (r, run.calls))


def test_a_build_that_cannot_run_never_raises() -> None:
    package, web = _tree(built_at=1000, source_at=2000)
    logs: list[str] = []
    r = web_pages.rebuild_if_stale("audiobook", package, web, npm="",
                                   env={"PATH": ""}, run=FakeRun(package),
                                   log=logs.append)
    check("no npm: failed, and the log says how to build it",
          r["state"] == "failed" and any("npm ci && npm run build" in m
                                         for m in logs), (r, logs))
    r = web_pages.rebuild_if_stale("audiobook", package, web, npm="npm", env={},
                                   run=FakeRun(package, code=1), log=lambda m: None)
    check("npm exits non-zero: failed", r["state"] == "failed"
          and "exited 1" in r["detail"], r)
    r = web_pages.rebuild_if_stale(
        "audiobook", package, web, npm="npm", env={},
        run=FakeRun(package, raises=subprocess.TimeoutExpired("npm", 1)),
        log=lambda m: None)
    check("npm times out: failed", r["state"] == "failed", r)
    run = FakeRun(package)
    r = web_pages.rebuild_if_stale("audiobook", package, web, npm="npm",
                                   env={"AUTHORLM_SKIP_WEB_BUILD": "1"}, run=run,
                                   log=lambda m: None)
    check("AUTHORLM_SKIP_WEB_BUILD=1: skipped, nothing run",
          r["state"] == "skipped" and run.calls == [], (r, run.calls))


def test_server_start_survives_any_rebuild_error() -> None:
    original = web_pages.rebuild_if_stale

    def broken(*_a, **_kw):
        raise RuntimeError("disk on fire")

    web_pages.rebuild_if_stale = broken
    try:
        logs: list[str] = []
        r = audiobook_server.rebuild_page(log=logs.append)
    finally:
        web_pages.rebuild_if_stale = original
    check("audio serve's rebuild step reports the error and returns",
          r["state"] == "failed" and any("disk on fire" in m for m in logs),
          (r, logs))
    src = (ROOT / "authorlm" / "audiobook_server.py").read_text(encoding="utf-8")
    body = src[src.index("def run("):]
    check("run() rebuilds the page before it opens the port",
          body.index("rebuild_page()") < body.index("ThreadingHTTPServer("))


def test_doctor_checks() -> None:
    package, web = _tree(built_at=1000, source_at=2000)
    checks = {c["name"]: c for c in web_pages.doctor_checks(package, web)}
    page = checks["web page: audiobook"]
    check("doctor fails a stale audiobook page, with a fix",
          not page["ok"] and page["state"] == "stale"
          and "npm run build" in page["fix"] and "kickstart" in page["fix"], page)
    check("pages without sources pass",
          checks["web page: workbench"]["ok"], checks["web page: workbench"])
    package, web = _tree()
    check("doctor passes a fresh page",
          {c["name"]: c for c in web_pages.doctor_checks(package, web)}
          ["web page: audiobook"]["ok"])


def _cli(*argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "main.py", *argv], cwd=str(ROOT),
                          env=env, capture_output=True, text=True, timeout=120)


def test_doctor_verb() -> None:
    r = _cli("doctor", "--json")
    try:
        board = json.loads(r.stdout)
    except ValueError:
        board = None
    check("`authorlm doctor --json` prints its checks", board is not None
          and {"web page: audiobook", "web page: triage-app",
               "web page: workbench", "engine: claude-headless binary"}
          <= {c["name"] for c in board["checks"]},
          r.stdout + r.stderr)
    check("its exit code follows the checks",
          r.returncode == (0 if board["ok"] else 1), (r.returncode, board["ok"]))


def test_retired_road_help_needs_a_reason() -> None:
    """Since #527 the old roads always run outside the workflow, even at
    their own step (workflow/roads.py), so no help may say they open on
    their own there."""
    for argv, words in ((("filter", "--help"), "always run outside it"),
                        (("lens", "--help"), "always needs --skip-reason"),
                        (("write", "--help"), "always needs --skip-reason")):
        r = _cli(*argv)
        text = " ".join(r.stdout.split())
        check(f"`{' '.join(argv)}` says the road always needs --skip-reason",
              r.returncode == 0 and words in text, text[:400])
        for stale in ("open on their own", "opens on its own"):
            check(f"`{' '.join(argv)}` no longer says '{stale}'",
                  stale not in text)
    top = " ".join(_cli("--help").stdout.split())
    check("the top-level help does not say a road opens on its own",
          "opens on its own" not in top and "open on their own" not in top)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print(f"\n{len(tests)} test groups passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
