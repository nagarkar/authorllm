"""tests/run_all.py, the suite's one runner, against fixture trees: throwaway
test files whose outcome is known, so nothing here needs Node, a model or the
real suite. Every test that ought to run runs; a silent skip or an early stop
is how one does not. (owner)

Run: python3 tests/test_run_all.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run_all  # noqa: E402

PASSED = 0
PAGES = ("audiobook_dist", "triage_dist", "workbench_dist")
SKIP_BUILD = {"AUTHORLM_SKIP_WEB_BUILD": "1"}
TMP = Path(tempfile.mkdtemp(prefix="authorlm-runall-"))


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def tree(files: dict[str, str], pages: tuple[str, ...] = PAGES) -> Path:
    root = Path(tempfile.mkdtemp(dir=TMP))
    (root / "tests").mkdir()
    for name, body in files.items():
        (root / "tests" / name).write_text(body)
    for page in pages:
        (root / "authorlm" / page).mkdir(parents=True)
        (root / "authorlm" / page / "index.html").write_text("<html>")
    return root


def run(root: Path, *flags: str, env: dict | None = None,
        ) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HERE / "run_all.py"), "--dir", str(root), *flags],
        capture_output=True, text=True, env={**os.environ, **(env or {})})


def last_line(done: subprocess.CompletedProcess) -> str:
    return done.stdout.strip().splitlines()[-1]


def all_passing() -> None:
    print("all passing:")
    done = run(tree({"test_ok.py": "print('all 3 checks passed')\n",
                     run_all.LIVE: "raise SystemExit('billed!')\n"}))
    check("exit 0, one row per file with its check count",
          done.returncode == 0 and "test_ok.py" in done.stdout
          and " 3 " in done.stdout.split("\n\n")[-2], done.stdout + done.stderr)
    check("the summary counts the files",
          "files: 1 passed, 0 failed" in done.stdout, done.stdout)
    check("the live file stays out by default, and the last line names it",
          "billed!" not in done.stdout
          and last_line(done).startswith(f"not run: tests/{run_all.LIVE}"),
          done.stdout)


def failures_do_not_stop_the_run() -> None:
    print("a failure:")
    done = run(tree({"test_a_bad.py": "print('line one')\n"
                                      "raise SystemExit('boom at the end')\n",
                     "test_b_ok.py": "print('fine')\n"}))
    check("non-zero exit, and the file after it still ran",
          done.returncode == 1 and "ok              test_b_ok.py" in done.stdout,
          done.stdout)
    check("the failure's own last lines are in the report",
          "boom at the end" in done.stdout, done.stdout)
    check("the summary names it",
          "files: 1 passed, 1 failed" in done.stdout
          and "failed: test_a_bad.py" in done.stdout, done.stdout)
    check("the last line still names the live file",
          last_line(done).startswith("not run:"), done.stdout)
    done = run(tree({"test_hang.py": "import time\ntime.sleep(60)\n"}),
               "--timeout", "1")
    check("a file past the timeout fails instead of holding up the run",
          done.returncode == 1 and "FAIL (timeout)" in done.stdout, done.stdout)


def a_silent_file_fails() -> None:
    print("a file that ran nothing:")
    done = run(tree({"test_no_main.py": "def test_never_called():\n    pass\n"}))
    check("exit 0 with no output is a failure, not a pass",
          done.returncode == 1 and "FAIL (no output)" in done.stdout, done.stdout)


def skips_reach_the_summary() -> None:
    print("skips:")
    done = run(tree({
        "test_s.py": "print('  SKIP: the epub checks, pandoc not on PATH')\n"
                     "print('  ok: a SKIP: inside a label is not a skip')\n",
        "test_u.py": "import unittest\n"
                     "class T(unittest.TestCase):\n"
                     "    def test_a(self):\n"
                     "        self.skipTest('no reason shown without -v')\n"
                     "unittest.main()\n"}))
    check("a skip does not fail the run", done.returncode == 0, done.stdout)
    summary = done.stdout[done.stdout.index("skipped checks:"):]
    check("every SKIP line is in the summary, with its file and reason",
          "test_s.py: the epub checks, pandoc not on PATH" in summary, summary)
    check("a label that merely contains the word is not counted",
          "inside a label" not in summary, summary)
    check("unittest's own skip shows up, counted, though it prints no reason",
          "test_u.py: 1 unittest skip(s)" in summary
          and "skipped checks: 2" in summary, summary)


def live_is_an_opt_in() -> None:
    print("--live:")
    body = "print('All 2 checks passed.')\nprint('LLM: 0 new live call(s).')\n"
    done = run(tree({"test_ok.py": "print('fine')\n", run_all.LIVE: body}), "--live")
    check("runs the live file, and the last line is its count of new calls",
          done.returncode == 0 and run_all.LIVE in done.stdout
          and last_line(done) == f"tests/{run_all.LIVE}: LLM: 0 new live call(s).",
          done.stdout)


def pages_get_built_or_the_run_stops() -> None:
    print("the web pages:")
    never = "raise SystemExit('a test ran')\n"
    root = tree({"test_x.py": never}, pages=PAGES[:2])
    done = run(root, env=SKIP_BUILD)
    check("a page that cannot be built stops the run, exit 2, before any test",
          done.returncode == 2 and "a test ran" not in done.stdout
          and "workbench" in done.stderr, done.stdout + done.stderr)
    check("...and says how", run_all.BUILD_COMMAND in done.stderr, done.stderr)
    done = run(root, "--no-build", env=SKIP_BUILD)
    check("--no-build runs anyway",
          done.returncode == 1 and "a test ran" in done.stdout, done.stdout)

    built_flags = {"audiobook": True, "triage-app": True, "workbench": False}
    calls = []

    def build(where: Path) -> None:
        calls.append(where)
        built_flags["workbench"] = True

    stop = run_all.ensure_pages(root, lambda _: dict(built_flags), build,
                                log=lambda _: None)
    check("a missing page is built, once, and the run goes on",
          stop is None and calls == [root], str(stop))
    calls.clear()
    stop = run_all.ensure_pages(root, lambda _: dict(built_flags), build,
                                log=lambda _: None)
    check("pages already there build nothing", stop is None and not calls)

    def broken(_: Path) -> None:
        raise SystemExit("authorlm: `npm ci` failed in web/workbench")

    built_flags["workbench"] = False
    stop = run_all.ensure_pages(root, lambda _: dict(built_flags), broken,
                                log=lambda _: None)
    check("a failed build stops with the build's own words and the command",
          stop is not None and "npm ci" in stop and run_all.BUILD_COMMAND in stop,
          str(stop))


def main_test() -> None:
    try:
        all_passing()
        failures_do_not_stop_the_run()
        a_silent_file_fails()
        skips_reach_the_summary()
        live_is_an_opt_in()
        pages_get_built_or_the_run_stops()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
