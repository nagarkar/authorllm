"""Live-LLM integration tests with record/replay (RFC §24.5 discipline).

Unlike tests/test_e2e.py (fully stubbed), this suite exercises the real
LiteLLM → Gemini path — but every unique payload is sent to the LLM at most
once. Responses are recorded in tests/llm_cache/*.json and replayed on
every later run, so:

- re-running the suite unchanged makes ZERO live calls;
- changing a prompt, the sample document, or the model re-records exactly
  the calls affected by that change (delete a cache file to force one).

The sample manuscript is generated fresh in a temp workspace on every run —
tests never touch user manuscripts and always start from the same default
state. Without GEMINI_API_KEY, the suite runs purely from the replay cache
and skips (exit 0, loudly) if the cache is incomplete.

Run: python3 tests/test_live_llm.py
"""

from __future__ import annotations

import os

# Pin the project-config and .env lookups away from the real ones by
# default, matching every other suite (test_api.py:14-18) — a bare import
# of this module must not accidentally read the repo's config.toml or
# .env before main_test() re-points AUTHORLM_CONFIG at this run's own
# workspace config below. AUTHORLM_ENV stays pinned here for the whole
# run: the suite's only documented way to supply a live key is an
# exported GEMINI_API_KEY (see the module docstring), never an ambient
# authorlm/.env. Without this pin, `cli.main` silently loaded a real key
# from .env on the very first `run()` call below — after `key_available`
# had already been computed and printed as absent — so the suite made
# live billed calls while its own banner denied it.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"

import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from authorlm.cli import main  # noqa: E402
from authorlm import paths as _paths  # noqa: E402

CACHE_DIR = REPO / "tests" / "llm_cache"
MODEL = "gemini/gemini-2.5-flash"
PASSED = 0

# Fixed sample text: stable bytes → stable payload hashes → cache hits.
SAMPLE_CH1 = """# Chapter 1 — Choice

Every act of thought begins with a choice. Before there is a world, before
there is a self, there is the primitive act of choosing: this rather than
that. To choose is to distinguish, and the distinction so drawn is the
visible form the choice takes.

## The primacy of distinction

Philosophers have long sought a first principle: substance, mind, will,
language. We propose something more austere: the act of distinction itself.
"""

SAMPLE_CH2 = """# Chapter 2 — Fields

A single distinction is inert, but distinctions do not come alone: each
distinction creates a space of further possible distinctions. We call this
space a field.

Think of a chessboard before the first move. The rules do not dictate a
game; they open a field of possible games. A field is generative rather
than a container: each position within it is a distinction that opens
further fields, which is how mere choice gives rise to a world.
"""


def run(workspace: Path, *argv: str, expect_exit: bool = False) -> str:
    buffer = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buffer):
            main(["--workspace", str(workspace), *argv])
    except SystemExit as err:
        code = 0 if err.code in (0, None) else 1
        if isinstance(err.code, str):
            buffer.write(err.code + "\n")
            code = 1
    output = buffer.getvalue()
    if expect_exit:
        assert code != 0, f"expected failure but succeeded: {argv}\n{output}"
    else:
        assert code == 0, f"command failed: {argv}\n{output}"
    return output


def run_stdin(workspace: Path, stdin_text: str, *argv: str,
              expect_exit: bool = False) -> str:
    """run() with stdin replaced — the write loop's brief and plan JSON
    travel over stdin."""
    old = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        return run(workspace, *argv, expect_exit=expect_exit)
    finally:
        sys.stdin = old


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def cache_usage() -> tuple[int, int, int]:
    """(calls, input_tokens, output_tokens) recorded in the replay cache."""
    calls = tokens_in = tokens_out = 0
    if CACHE_DIR.exists():
        for path in CACHE_DIR.glob("*.json"):
            usage = json.loads(path.read_text()).get("usage", {})
            calls += 1
            tokens_in += usage.get("input_tokens", 0)
            tokens_out += usage.get("output_tokens", 0)
    return calls, tokens_in, tokens_out


def main_test() -> None:
    # Load .env (a no-op today — AUTHORLM_ENV is pinned to /nonexistent
    # above) before reading GEMINI_API_KEY, so the banner below reflects
    # the same environment the LLM call itself will see. Computing
    # key_available before any env loading is what let the suite print
    # "absent" and then make a live call moments later.
    _paths.load_env()
    key_available = bool(os.environ.get("GEMINI_API_KEY"))
    cached_before, in_before, out_before = cache_usage()
    print(
        f"Live-LLM suite — model {MODEL}, replay cache {CACHE_DIR} "
        f"({cached_before} recorded), GEMINI_API_KEY "
        f"{'present' if key_available else 'absent (replay-only)'}"
    )

    root = Path(tempfile.mkdtemp(prefix="authorlm-live-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-choice.md").write_text(SAMPLE_CH1)
        (ms / "02-fields.md").write_text(SAMPLE_CH2)
        (ws / ".authorlm").mkdir()
        (ws / ".authorlm" / "config.toml").write_text(
            "# live-LLM test configuration\n"
            "[llm]\n"
            "enabled = true\n"
            f'model = "{MODEL}"\n'
            f'cache_dir = "{CACHE_DIR}"\n'
            # `write draft` deliberately does not fall back to [llm] model,
            # so the writing section is explicit here too. It runs on the
            # suite's own cheap MODEL rather than on Fable 5: the suite's
            # contract is that anyone can re-record its fixtures, and
            # billing a frontier model to re-record a plumbing test is not
            # that. What this proves is assembly → call → parse → register
            # against a real provider round trip; what it deliberately does
            # not prove is the drafting model's quality.
            "\n[writing]\n"
            f'model = "{MODEL}"\n'
            "max_tokens = 4000\n"
            'effort = "low"\n'
        )
        # AUTHORLM_CONFIG defaults to /nonexistent (see the module-level
        # pin above); repoint it at this run's own workspace config so
        # `cli.main` reads the `[llm]`/`cache_dir` settings just written,
        # not the repo's config.toml.
        os.environ["AUTHORLM_CONFIG"] = str(ws / ".authorlm" / "config.toml")

        # --- Extraction (live or replayed) ---
        out = run(ws, "init", "--name", "sample", "--path", str(ms))
        if "Extraction produced nothing" in out:
            if not key_available:
                print(
                    "SKIP: replay cache is incomplete for the current prompts and "
                    "GEMINI_API_KEY is not set. Export the key once to record."
                )
                return
            raise AssertionError(f"extraction failed despite key being set:\n{out}")

        extracted = int(out.split("Extracted ")[1].split(" new concept")[0])
        check("extraction finds a philosophical vocabulary (≥3 concepts)",
              extracted >= 3, out)
        out = run(ws, "concept", "list")
        check("extracted concepts realize against the sample text",
              "realized" in out, out)

        # --- Bridge drafting (live or replayed) ---
        run(ws, "session", "start")
        run(ws, "concept", "add", "History")   # declared, absent from text
        run(ws, "intent", "declare", "Introduce histories")
        out = run(ws, "guide")
        check("bridge suggested for the declared-but-unwritten concept",
              "Introduce 'History'" in out, out)
        check("LLM drafted bridge text included", "Draft to consider" in out, out)

        # --- Belief distillation (live or replayed) ---
        out = run(ws, "review", "1", "--reject", "--explain",
                  "Always ground an abstract definition in a lived example "
                  "before formalizing it")
        check("explanation distilled into a candidate belief",
              "seeded a candidate belief" in out, out)
        distilled = out.split('seeded a candidate belief: "')[1].split('"')[0]
        print(f"  (distilled statement: {distilled!r})")

        # --- NONE path: a one-off remark should not become belief ---
        out = run(ws, "guide")
        if "belief_reminder" in out:
            out = run(ws, "review", "1", "--reject", "--explain",
                      "This was a one-off exception for this particular "
                      "chapter only, not a general practice of mine")
            check("one-off explanation not generalized into belief",
                  "seeded a candidate belief" not in out, out)

        # --- Episode analysis (live or replayed): learn from actual edits ---
        (ms / "03-histories.md").write_text(
            "# Chapter 3 — Histories\n\n"
            "Consider a game of chess already twenty moves deep. The pieces "
            "hold not just positions but a story of how they came to stand "
            "there. That story is what we call a history.\n\n"
            "A history is the trace a trajectory leaves: the ordered record "
            "of distinctions actually drawn. Where the field is what could "
            "happen and the trajectory is what is happening, the history is "
            "what has happened — and can no longer be otherwise.\n"
        )
        run(ws, "collect")
        intent_id = None
        for line in run(ws, "intent", "list").splitlines():
            if "(active)" in line and "histories" in line:
                intent_id = line.split("[")[1].split("]")[0]
        out = run(ws, "intent", "complete", intent_id, "--outcome",
                  "Histories introduced via the chess story")
        check("episode analysis runs at intent completion",
              "Analyzed episode" in out, out)
        print("  (analysis output)")
        for line in out.splitlines():
            if line.strip().startswith(("•", "↳", "belief", "outcome")):
                print(f"   {line}")

        # --- write draft (live or replayed): the whole beat-drafting seam --
        run(ws, "style", "guide", "House")
        run(ws, "style", "add", "register",
            "Plain declarative English; no rhetorical questions.",
            "--guide", "House")
        run(ws, "style", "attach", "02-fields.md", "House")
        run(ws, "summarize", "rebuild", "--all")
        intent_id = run(ws, "intent", "declare",
                        "Rewrite the fields essay").split("[")[1].split("]")[0]
        run_stdin(ws, "The essay explains why a distinction opens a field.",
                  "write", "start", "02-fields.md", "--intent", intent_id)
        run_stdin(ws, json.dumps([
            {"role": "opener", "concepts": ["Choice"], "budget": 60,
             "notes": "claims a single distinction is inert on its own"},
            {"role": "close", "concepts": ["Choice"], "budget": 60,
             "notes": "claims the field is generative, not a container"},
        ]), "write", "plan")
        try:
            out = run(ws, "write", "draft")
        except AssertionError:
            if key_available:
                raise
            print("SKIP: no beat-draft response in the replay cache and "
                  "GEMINI_API_KEY is not set. Export the key once to record.")
        else:
            check("write draft parses the model's reply and registers the "
                  "beat through the ordinary propose path",
                  "WHY" in out and "SELF-CHECK" in out
                  and "Draft registered" in out
                  and "authorlm/prompts/beat-draft.md" in out, out)
            out = run_stdin(ws, "", "write", "accept")
            check("the drafted beat accepts and appends exactly as a "
                  "hand-written one does",
                  "accepted" in out
                  and (ms / "02-fields.md").read_text().strip(), out)
            run_stdin(ws, "", "write", "abandon")

        run(ws, "session", "end")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    cached_after, in_after, out_after = cache_usage()
    print(
        f"\nAll {PASSED} checks passed.\n"
        f"LLM: {cached_after - cached_before} new live call(s) "
        f"({in_after - in_before:,} in / {out_after - out_before:,} out tokens); "
        f"replay cache covers {cached_after} call(s) "
        f"({in_after:,} in / {out_after:,} out tokens total)."
    )


if __name__ == "__main__":
    main_test()
