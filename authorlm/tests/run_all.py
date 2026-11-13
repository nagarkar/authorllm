"""The one way to run the whole test suite.

    cd authorlm && python3 tests/run_all.py            # everything hermetic
    cd authorlm && python3 tests/run_all.py --live     # plus test_live_llm.py
    cd authorlm && python3 tests/run_all.py --no-build # run without building

Runs every tests/test_*.py in its own subprocess and keeps going after a
failure, so a broken file never hides the files after it. Before that it
builds any web page that is missing (setup.build_pages); when it cannot, it
stops and says how, exit 2, because `test_audio.py`, `test_triage_app.py`
and `test_branding.py` read the built pages. `--no-build` runs anyway, and
those files fail and name the command.

Ends with a table per file and a summary: files passed, files failed, and
every check skipped with its reason. Exit 1 on any failure.

A SKIP IS A LINE. A test leaves a check out only for something it cannot
supply itself (no Google token in a cloud run, no pandoc on the machine), and
says so by printing one line, `SKIP: <what is left out and why>`. This runner
reads those lines, so no skip is silent. A file that needs something the repo can supply
fails instead. unittest's own skip prints no reason without -v, so it is
counted from its `skipped=N` and reported as unnamed.

A file that exits 0 having printed nothing ran nothing (no `__main__` guard,
say), and counts as a failure.

`test_live_llm.py` stays out unless `--live`: it sends any request with no
recording to the live, billed model, so it is an explicit opt-in (metalm
testing.md, Offline by default). The last line of the output says it was not
run, or, with `--live`, repeats its count of new live calls.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = "test_live_llm.py"
BUILD_COMMAND = "cd authorlm && python3 -c 'import setup; setup.build_pages()'"
TIMEOUT_S = 1800   # a re-record of the Doc fakes takes about ten minutes
TAIL_LINES = 40

_SKIP_LINE = re.compile(r"^\s*SKIP: (.+)$", re.MULTILINE)
_UNITTEST_SKIPS = re.compile(r"^(?:OK|FAILED) \(.*skipped=(\d+).*\)$", re.MULTILINE)
_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
# "All 1237 checks passed.", "all checks passed (8)", "22 checks passed."
_PASSED_LINE = re.compile(r"^(?:all\b|\d).*\bpassed\b.*$", re.IGNORECASE | re.MULTILINE)
_OK_LINE = re.compile(r"^\s*(?:\[ok\]|ok:|pass\s)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class FileResult:
    name: str
    exit_code: int | None       # None: stopped at the timeout
    seconds: float
    output: str
    skips: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and bool(self.output.strip())

    @property
    def count(self) -> str:
        """How many checks the file reports, or '?' if it does not say."""
        ran = _UNITTEST_RAN.findall(self.output)
        if ran:
            return ran[-1]
        lines = _PASSED_LINE.findall(self.output)
        number = re.search(r"\d+", lines[-1]) if lines else None
        if number:
            return number.group()
        oks = len(_OK_LINE.findall(self.output))
        return str(oks) if oks else "?"

    @property
    def verdict(self) -> str:
        if self.exit_code is None:
            return "FAIL (timeout)"
        if self.exit_code != 0:
            return f"FAIL (exit {self.exit_code})"
        return "ok" if self.output.strip() else "FAIL (no output)"


def parse_skips(output: str) -> tuple[str, ...]:
    """Every skip a file reported: its SKIP lines, plus any unittest skips,
    which print no reason."""
    skips = [line.strip() for line in _SKIP_LINE.findall(output)]
    unnamed = sum(int(n) for n in _UNITTEST_SKIPS.findall(output))
    if unnamed:
        skips.append(f"{unnamed} unittest skip(s), reason not printed; "
                     "run the file with -v to see them")
    return tuple(skips)


def _load_setup():
    spec = importlib.util.spec_from_file_location("authorlm_setup", ROOT / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # not __main__, so setup() does not run
    return module


def missing_pages(root: Path, built) -> list[str]:
    return [page for page, ok in built(root).items() if not ok]


def ensure_pages(root: Path, built, build,
                 log=lambda m: print(m, flush=True)) -> str | None:
    """Build the pages when any is missing. Returns None when they are all
    there afterwards, else the message to stop with."""
    missing = missing_pages(root, built)
    if not missing:
        return None
    log(f"web pages not built ({', '.join(missing)}): building them")
    try:
        build(root)
    except SystemExit as err:
        return (f"{err}\nThe tests need the built pages: fix that, then run "
                f"`{BUILD_COMMAND}`, or use --no-build to run without them "
                "and let the files that read them fail.")
    missing = missing_pages(root, built)
    if not missing:
        return None
    return (f"web pages still not built: {', '.join(missing)}. The tests need "
            f"them: run `{BUILD_COMMAND}` (needs Node at the version in "
            "authorlm/.nvmrc, and AUTHORLM_SKIP_WEB_BUILD unset), or use "
            "--no-build to run without them and let the files that read "
            "them fail.")


def test_env() -> dict[str, str]:
    """The environment every test file runs in. PYTHONPATH starts with this
    checkout's authorlm/, so a worktree tests its own package, not the one
    `pip install -e` points at (#425)."""
    # -O would turn the assert inside most files' check() into nothing.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONOPTIMIZE"}
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(ROOT), env.get("PYTHONPATH")) if p)
    return env


def imported_from() -> Path:
    """Where `import authorlm` resolves under test_env()."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import authorlm, os; print(os.path.dirname(authorlm.__file__))"],
        cwd=str(ROOT), env=test_env(), capture_output=True, text=True, check=True)
    return Path(out.stdout.strip()).resolve()


def run_file(root: Path, path: Path, timeout: float = TIMEOUT_S) -> FileResult:
    env = test_env()
    started = time.monotonic()
    # A file, not a pipe: a child that leaves a daemon holding the pipe open
    # must not make this wait past its exit.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out:
        try:
            code = subprocess.run(
                [sys.executable, str(path.relative_to(root))], cwd=str(root),
                env=env, stdin=subprocess.DEVNULL, stdout=out,
                stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = None
        out.seek(0)
        output = out.read()
    return FileResult(path.name, code, time.monotonic() - started, output,
                      parse_skips(output))


def report(results: list[FileResult]) -> str:
    width = max(len(r.name) for r in results)
    rows = [f"{'file':<{width}}  {'result':<15} {'checks':>6} {'skips':>5} {'secs':>6}"]
    for r in results:
        rows.append(f"{r.name:<{width}}  {r.verdict:<15} {r.count:>6} "
                    f"{len(r.skips):>5} {r.seconds:>6.1f}")
    failed = [r for r in results if not r.passed]
    skips = [(r.name, s) for r in results for s in r.skips]
    counted = [int(r.count) for r in results if r.count != "?"]
    rows += ["", f"files: {len(results) - len(failed)} passed, {len(failed)} failed",
             f"checks reported: {sum(counted)} ({len(results) - len(counted)} "
             "file(s) report no count)"]
    if failed:
        rows.append("failed: " + ", ".join(r.name for r in failed))
    rows.append(f"skipped checks: {len(skips)}")
    rows += [f"  {name}: {reason}" for name, reason in skips]
    return "\n".join(rows)


def failure_tails(results: list[FileResult]) -> str:
    parts = []
    for r in results:
        if r.passed:
            continue
        tail = r.output.strip().splitlines()[-TAIL_LINES:]
        parts.append(f"--- {r.name}: {r.verdict}, last lines of its output ---")
        parts += [f"    {line}" for line in tail]
    return "\n".join(parts)


def live_line(results: list[FileResult], live: bool) -> str:
    if not live:
        return (f"not run: tests/{LIVE} (sends billed model calls for any "
                "missing recording; --live includes it)")
    result = next((r for r in results if r.name == LIVE), None)
    if result is None:
        return f"--live: tests/{LIVE} is not there, so it did not run"
    stats = [l for l in result.output.splitlines() if l.startswith("LLM:")]
    return f"tests/{LIVE}: {stats[-1] if stats else result.verdict}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run every tests/test_*.py and report per file.")
    parser.add_argument("--live", action="store_true",
                        help=f"include {LIVE}, which bills for missing recordings")
    parser.add_argument("--no-build", action="store_true",
                        help="run without building missing web pages; the "
                             "files that read them fail and name the command")
    parser.add_argument("--dir", type=Path, default=ROOT,
                        help="the directory holding tests/ and the pages "
                             "(default: this checkout's authorlm/)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S,
                        help="seconds one file may take before it fails "
                             f"(default {TIMEOUT_S})")
    args = parser.parse_args(argv)
    root = args.dir.resolve()

    package = imported_from()
    print(f"authorlm imported from {package}", flush=True)
    if not package.is_relative_to(ROOT.parent):
        print(f"STOPPED, no test ran: authorlm is imported from {package}, "
              f"not from the checkout running the tests ({ROOT.parent}).",
              file=sys.stderr)
        return 2

    if not args.no_build:
        setup = _load_setup()
        stop = ensure_pages(root, setup.built, setup.build_pages)
        if stop:
            print(f"STOPPED, no test ran: {stop}", file=sys.stderr)
            return 2

    files = sorted((root / "tests").glob("test_*.py"))
    results = []
    for path in files:
        if path.name == LIVE and not args.live:
            continue
        result = run_file(root, path, args.timeout)
        results.append(result)
        print(f"{result.verdict:<15} {path.name} ({result.seconds:.1f}s)", flush=True)
    if not results:
        print(f"no tests/test_*.py under {root}", file=sys.stderr)
        return 2

    tails = failure_tails(results)
    if tails:
        print("\n" + tails)
    print("\n" + report(results))
    print(live_line(results, args.live))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
