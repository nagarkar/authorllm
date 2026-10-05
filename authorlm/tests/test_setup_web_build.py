"""`setup.py` builds the web pages at install time, because the built
pages (`authorlm/*_dist/index.html`) are not tracked in git.

These checks run `build_pages` against a temporary tree with a fake npm
runner: no Node, no network. They pin when it builds, what it runs, and
that a missing npm stops the install unless the pages already exist or
AUTHORLM_SKIP_WEB_BUILD=1 says to go on without them.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("authorlm_setup", ROOT / "setup.py")
setup_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(setup_mod)  # not __main__, so setup() does not run


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _tree(built=(), node_modules=()) -> Path:
    root = Path(tempfile.mkdtemp())
    for src, out in setup_mod.PAGES.items():
        (root / "web" / src).mkdir(parents=True)
        if src in node_modules:
            (root / "web" / src / "node_modules").mkdir()
        if src in built:
            (root / "authorlm" / out).mkdir(parents=True)
            (root / "authorlm" / out / "index.html").write_text("<html>")
    return root


class FakeRun:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, cmd, cwd):
        self.calls.append((Path(cwd).name, cmd[1:]))
        bad = self.fail_on == (Path(cwd).name, cmd[1:])
        return SimpleNamespace(returncode=1 if bad else 0)


def _exit_message(fn) -> str | None:
    try:
        fn()
    except SystemExit as e:
        return str(e)
    return None


def test_pages_match_the_vite_out_dirs() -> None:
    for src, out in setup_mod.PAGES.items():
        cfg = (ROOT / "web" / src / "vite.config.ts").read_text(encoding="utf-8")
        check(f"web/{src} builds into authorlm/{out}",
              f'outDir: "../../authorlm/{out}"' in cfg)


def test_builds_every_page_from_a_clean_install() -> None:
    run = FakeRun()
    done = setup_mod.build_pages(_tree(node_modules=("workbench",)), npm="npm",
                                 env={}, run=run, log=lambda m: None)
    check("every page is built", done == list(setup_mod.PAGES), done)
    check("npm ci (even over an old node_modules), then build",
          run.calls == [("audiobook", ["ci"]), ("audiobook", ["run", "build"]),
                        ("triage-app", ["ci"]), ("triage-app", ["run", "build"]),
                        ("workbench", ["ci"]), ("workbench", ["run", "build"])],
          run.calls)


def test_node_version_is_declared_the_standard_way() -> None:
    import json
    nvm = (ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
    check(".nvmrc names a major version", nvm.isdigit(), nvm)
    for src in setup_mod.PAGES:
        d = ROOT / "web" / src
        pkg = json.loads((d / "package.json").read_text(encoding="utf-8"))
        engines = (pkg.get("engines") or {}).get("node", "")
        check(f"web/{src} declares engines.node", bool(engines), pkg)
        check(f"web/{src} enforces it (engine-strict)",
              "engine-strict=true" in (d / ".npmrc").read_text(encoding="utf-8"))
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        check(f"web/{src} pins no dependency to 'latest'",
              "latest" not in deps.values(), deps)
        check(f"web/{src} has a lockfile for npm ci",
              (d / "package-lock.json").is_file())


def test_a_failed_build_stops_the_install() -> None:
    run = FakeRun(fail_on=("triage-app", ["run", "build"]))
    msg = _exit_message(lambda: setup_mod.build_pages(
        _tree(), npm="npm", env={}, run=run, log=lambda m: None))
    check("a failed build raises SystemExit", msg is not None)
    check("the message names the page", "web/triage-app" in (msg or ""), msg)


def test_no_npm_and_pages_missing_stops_the_install() -> None:
    msg = _exit_message(lambda: setup_mod.build_pages(
        _tree(built=("audiobook",)), npm="", env={}, run=FakeRun(),
        log=lambda m: None))
    check("no npm with pages missing raises SystemExit", msg is not None)
    check("the message says Node and npm are needed",
          "needs Node and npm" in (msg or ""), msg)
    check("the message names the missing pages",
          "triage-app" in (msg or "") and "workbench" in (msg or "")
          and "audiobook," not in (msg or ""), msg)


def test_no_npm_reuses_pages_already_built() -> None:
    run, logs = FakeRun(), []
    done = setup_mod.build_pages(_tree(built=tuple(setup_mod.PAGES)), npm="",
                                 env={}, run=run, log=logs.append)
    check("nothing is built", done == [] and run.calls == [], run.calls)
    check("it says it reused them", any("reusing" in m for m in logs), logs)


def test_skip_env_builds_nothing() -> None:
    run = FakeRun()
    done = setup_mod.build_pages(_tree(), npm="npm",
                                 env={"AUTHORLM_SKIP_WEB_BUILD": "1"}, run=run,
                                 log=lambda m: None)
    check("AUTHORLM_SKIP_WEB_BUILD=1 builds nothing",
          done == [] and run.calls == [], run.calls)


def test_dist_is_not_tracked() -> None:
    ignore = (ROOT.parent / ".gitignore").read_text(encoding="utf-8")
    for out in setup_mod.PAGES.values():
        check(f"authorlm/authorlm/{out}/ is gitignored",
              f"authorlm/authorlm/{out}/" in ignore)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(t.__name__)
        t()
    print(f"\n{len(tests)} test groups passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
