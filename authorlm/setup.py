"""Build the web pages into the package when it is installed.

The pages (`authorlm/{audiobook,triage,workbench}_dist/index.html`) are
build output, so they are not tracked in git. Every install, wheel or
editable, runs `npm ci` (a clean install from the committed lockfile)
and `npm run build` in each `web/<page>/`. That needs Node and npm at a
version in each package.json's `engines` range: `.nvmrc` names one, and
each `.npmrc` sets engine-strict, so a wrong Node stops `npm ci`.
Without npm the install stops and says so, unless every page is already built (then they are reused
with a warning) or AUTHORLM_SKIP_WEB_BUILD=1 (install without them; the
commands that serve a page then say it has not been built).

Everything else about the package lives in pyproject.toml.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# web/<source dir> -> authorlm/<output dir>/index.html (each vite.config.ts outDir)
PAGES = {
    "audiobook": "audiobook_dist",
    "triage-app": "triage_dist",
    "workbench": "workbench_dist",
}


def built(root: Path = ROOT) -> dict[str, bool]:
    return {src: (root / "authorlm" / out / "index.html").is_file()
            for src, out in PAGES.items()}


def build_pages(root: Path = ROOT, npm: str | None = None,
                env: dict | None = None, run=subprocess.run,
                log=lambda m: print(m, file=sys.stderr)) -> list[str]:
    """Build every page. Returns the pages built; raises SystemExit when a
    page cannot be built and is not already there."""
    env = os.environ if env is None else env
    if env.get("AUTHORLM_SKIP_WEB_BUILD") == "1":
        log("authorlm: AUTHORLM_SKIP_WEB_BUILD=1, not building the web pages")
        return []
    npm = npm if npm is not None else shutil.which("npm")
    if not npm:
        missing = [src for src, ok in built(root).items() if not ok]
        if missing:
            raise SystemExit(
                "authorlm: building the web pages needs Node and npm, which "
                f"were not found (missing: {', '.join(missing)}). Install "
                "Node (the version in authorlm/.nvmrc) and retry, or set "
                "AUTHORLM_SKIP_WEB_BUILD=1 to install without the pages.")
        log("authorlm: npm not found; reusing the web pages already built")
        return []
    done = []
    for src in PAGES:
        cwd = root / "web" / src
        for cmd in ([npm, "ci"], [npm, "run", "build"]):
            log(f"authorlm: {' '.join(cmd[1:])} in web/{src}")
            r = run(cmd, cwd=cwd)
            if r.returncode:
                raise SystemExit(f"authorlm: `npm {' '.join(cmd[1:])}` "
                                 f"failed in web/{src}")
        done.append(src)
    return done


if __name__ == "__main__":
    from setuptools import setup
    from setuptools.command.build_py import build_py

    class BuildPyWithPages(build_py):
        def run(self):
            build_pages()
            super().run()

    setup(cmdclass={"build_py": BuildPyWithPages})
