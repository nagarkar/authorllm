"""The built web pages against their sources: whether each compiled page
(`authorlm/<out>/index.html`) is older than the files under
`web/<page>/` it is built from, and the rebuild `authorlm audio serve`
runs when it is.

The pages are build output, not tracked in git (setup.py builds them at
install). A merge that changes a page's sources therefore leaves the
served copy behind until someone builds it again. Staleness is by
modification time: git writes every file a merge or pull changes, so a
changed source is newer than the page built before it.

Decisions:
- The audiobook page rebuilds itself when `audio serve` starts
  (`audiobook_server.run`), not in a deploy script and not at request
  time. Starting the server is the one step every deploy already takes
  (the launch agent's `launchctl kickstart -k`, after a merge), the page
  is read from disk on every request, and building before the port opens
  means the restarted server never serves the old page or a half-written
  one. A fresh page costs one `stat` per source file.
- A failed or impossible rebuild (no npm on PATH, `npm` exits non-zero,
  a timeout) never stops the server: it serves what is built and says
  why in its log, and `authorlm doctor` flags the stale page.
- `npm ci` runs only when `node_modules/` is missing or older than the
  lockfile; otherwise only `npm run build`.
- AUTHORLM_SKIP_WEB_BUILD=1 turns the rebuild off, as it does at install.

Never imports: cli, api.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

PACKAGE = Path(__file__).resolve().parent
WEB = PACKAGE.parent / "web"
# web/<source dir> -> authorlm/<output dir>/index.html; the same map as
# setup.py's PAGES (a test holds them equal).
PAGES = {
    "audiobook": "audiobook_dist",
    "triage-app": "triage_dist",
    "workbench": "workbench_dist",
}
# Under web/<page>/ but not sources: installed packages and tsc's cache.
_NOT_SOURCES = ("node_modules",)
_NOT_SOURCE_SUFFIXES = (".tsbuildinfo",)
BUILD_TIMEOUT_S = 600


def source_files(page: str, web: Path = WEB) -> list[Path]:
    root = web / page
    if not root.is_dir():
        return []
    out = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] in _NOT_SOURCES or path.suffix in _NOT_SOURCE_SUFFIXES:
            continue
        if path.is_file():
            out.append(path)
    return out


def built_page(page: str, package: Path = PACKAGE) -> Path:
    return package / PAGES[page] / "index.html"


def page_state(page: str, package: Path = PACKAGE, web: Path = WEB) -> dict:
    """{page, state, detail}: state is `ok`, `missing` (never built),
    `stale` (a source is newer than the built page) or `no-sources`
    (an install without the web/ folder, where nothing can be compared)."""
    sources = source_files(page, web)
    built = built_page(page, package)
    if not sources:
        return {"page": page, "state": "no-sources",
                "detail": f"no sources at {web / page}"}
    if not built.is_file():
        return {"page": page, "state": "missing",
                "detail": f"{built} has not been built"}
    newest = max(sources, key=lambda p: p.stat().st_mtime)
    if newest.stat().st_mtime > built.stat().st_mtime:
        return {"page": page, "state": "stale",
                "detail": f"{newest.relative_to(web)} is newer than {built}"}
    return {"page": page, "state": "ok", "detail": str(built)}


def build_command(page: str) -> str:
    return f"cd web/{page} && npm ci && npm run build"


def rebuild_if_stale(page: str, package: Path = PACKAGE, web: Path = WEB,
                     npm: str | None = None, env: dict | None = None,
                     run: Callable = subprocess.run,
                     log: Callable[[str], None] = print) -> dict:
    """Build `page` when it is missing or stale. Returns {page, state,
    detail} with state `ok` (nothing to do), `rebuilt`, `skipped`
    (AUTHORLM_SKIP_WEB_BUILD=1, or no sources) or `failed`. Never raises
    for a build that cannot run or fails: the caller goes on serving."""
    state = page_state(page, package, web)
    if state["state"] in ("ok", "no-sources"):
        return {**state, "state": "ok" if state["state"] == "ok" else "skipped"}
    env = os.environ if env is None else env
    if env.get("AUTHORLM_SKIP_WEB_BUILD") == "1":
        log(f"  {page} page {state['state']} ({state['detail']}); "
            "AUTHORLM_SKIP_WEB_BUILD=1, not rebuilding")
        return {**state, "state": "skipped"}
    npm = npm if npm is not None else shutil.which("npm", path=env.get("PATH"))
    if not npm:
        detail = (f"{state['detail']}; npm is not on PATH, so it was not "
                  f"rebuilt — run `{build_command(page)}`")
        log(f"  {page} page {state['state']}: {detail}")
        return {**state, "state": "failed", "detail": detail}
    cwd = web / page
    lock = cwd / "package-lock.json"
    installed = cwd / "node_modules" / ".package-lock.json"
    steps = [[npm, "run", "build"]]
    if not installed.is_file() or (lock.is_file() and lock.stat().st_mtime
                                   > installed.stat().st_mtime):
        steps.insert(0, [npm, "ci"])
    log(f"  {page} page {state['state']} ({state['detail']}): rebuilding")
    for cmd in steps:
        try:
            r = run(cmd, cwd=str(cwd), capture_output=True, text=True,
                    timeout=BUILD_TIMEOUT_S, env=dict(env))
        except (OSError, subprocess.SubprocessError) as err:
            detail = f"`npm {' '.join(cmd[1:])}` did not run: {err}"
            log(f"  {page} page not rebuilt: {detail}")
            return {**state, "state": "failed", "detail": detail}
        if r.returncode:
            tail = "\n".join(((r.stdout or "") + (r.stderr or ""))
                             .strip().splitlines()[-15:])
            detail = f"`npm {' '.join(cmd[1:])}` exited {r.returncode}"
            log(f"  {page} page not rebuilt: {detail}\n{tail}")
            return {**state, "state": "failed", "detail": detail}
    after = page_state(page, package, web)
    if after["state"] != "ok":
        log(f"  {page} page still {after['state']} after the build: "
            f"{after['detail']}")
        return {**after, "state": "failed"}
    log(f"  {page} page rebuilt")
    return {**after, "state": "rebuilt"}


def doctor_checks(package: Path = PACKAGE, web: Path = WEB) -> list[dict]:
    """One check per page for `authorlm doctor`: {name, ok, state, detail,
    fix}. An install without web/ has nothing to compare and passes."""
    checks = []
    for page in PAGES:
        s = page_state(page, package, web)
        ok = s["state"] in ("ok", "no-sources")
        fix = "" if ok else (
            f"restart the server, which rebuilds it at start (launchctl "
            "kickstart -k gui/$(id -u)/com.nagarkar.authorlm.serve), or "
            f"{build_command(page)} (from authorlm/)"
            if page == "audiobook" else f"{build_command(page)} (from authorlm/)")
        checks.append({"name": f"web page: {page}", "ok": ok,
                       "state": s["state"], "detail": s["detail"], "fix": fix})
    return checks
