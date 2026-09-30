"""Hermetic tests: the budget's memoized day base keeps pace with flushes.

A long-lived process (the MCP server flushes after every tool call) must
count every earlier flush toward today's spend, not only the base read
at first use plus the current pending aggregate.

Run: python3 tests/test_budget_flush.py
"""

from __future__ import annotations

import os

# Offline suite: pin config, .env and client provenance, as every suite does.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASSED = 0
MODEL = "zz/budget-flush"


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def _assert_offline() -> None:
    from authorlm import paths as _paths

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"


def _use_config(root: Path, text: str) -> None:
    from authorlm import budget, usage

    path = root / f"cfg-{abs(hash(text)) % 100000}.toml"
    path.write_text(text, encoding="utf-8")
    os.environ["AUTHORLM_CONFIG"] = str(path)
    usage.reset()
    budget.reset()


def main_test() -> None:
    from authorlm import budget, clients, usage

    root = Path(tempfile.mkdtemp(prefix="authorlm-budget-flush-"))
    previous_config = os.environ.get("AUTHORLM_CONFIG")
    previous_workspace = clients._STATE["workspace"]
    try:
        ws = root / "ws"
        (ws / ".authorlm").mkdir(parents=True)
        clients.configure(workspace=str(ws))
        folder = usage.log_dir(str(ws))
        # $0.60 per call: 600 input tokens at $1e-3 each. That is over the
        # default expensive threshold, so a detail line is written too;
        # day_base excludes detail lines, so only the aggregate counts.
        usage._PRICE_OVERRIDE[MODEL] = {
            "input": 1e-3, "output": 0.0, "cache_read": 0.0,
            "cache_write": 0.0, "partial": False}

        # --- with [budget] enforce: flushes accumulate into the base ---
        _use_config(root, "[budget]\napi_dollars_per_day = 1.0\n"
                          "enforce = true\n")
        usage.record("llm", MODEL, 600, 0)
        usage.flush("a")
        first_gate = budget.gate("llm", MODEL)
        after_first = budget.day_total(folder, 0.0)
        check("after one $0.60 flush the day total is $0.60 and gate() "
              "still permits",
              first_gate is None and abs(after_first - 0.6) < 1e-6,
              f"gate={first_gate} total={after_first}")

        usage.record("llm", MODEL, 600, 0)
        usage.flush("b")
        after_second = budget.day_total(folder, 0.0)
        check("after a second $0.60 flush in the SAME process the day "
              "total includes both flushes (>= $1.20), not only the base "
              "cached before the first",
              after_second >= 1.2 - 1e-6, f"total={after_second}")
        raised = None
        try:
            budget.gate("llm", MODEL)
        except budget.BudgetExceeded as err:
            raised = err
        check("gate() refuses once the flushed spend is over the cap",
              raised is not None and "1.20" in str(raised), f"{raised}")

        # The in-process figure agrees with a fresh read of the ledger.
        budget._DAY_BASE.clear()
        reread = budget.day_base(folder)
        check("the kept-pace base agrees with a fresh read of the ledger",
              abs(reread - after_second) < 1e-6,
              f"reread={reread} cached={after_second}")

        # --- with no [budget]: flush never touches _DAY_BASE ---
        _use_config(root, "[llm]\nenabled = false\n")
        usage.record("llm", MODEL, 600, 0)
        usage.flush("c")
        check("with no [budget] section a flush never populates or "
              "updates the day-base memo",
              budget._DAY_BASE == {}, f"{budget._DAY_BASE}")

        # note_flushed never creates an entry and never raises.
        budget.reset()
        budget.note_flushed(folder, 5.0)
        budget.note_flushed(object(), "not-a-number")  # type: ignore[arg-type]
        check("note_flushed only updates an existing entry and swallows "
              "bad input",
              budget._DAY_BASE == {}, f"{budget._DAY_BASE}")
    finally:
        if previous_config is None:
            os.environ.pop("AUTHORLM_CONFIG", None)
        else:
            os.environ["AUTHORLM_CONFIG"] = previous_config
        usage._PRICE_OVERRIDE.pop(MODEL, None)
        usage.reset()
        budget.reset()
        clients._STATE["workspace"] = previous_workspace
        shutil.rmtree(root, ignore_errors=True)

    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
