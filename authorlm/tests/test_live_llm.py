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

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from authorlm.cli import main  # noqa: E402

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
        )

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

        # --- Policy distillation (live or replayed) ---
        out = run(ws, "review", "1", "--reject", "--explain",
                  "Always ground an abstract definition in a lived example "
                  "before formalizing it")
        check("explanation distilled into a candidate policy",
              "seeded a candidate policy" in out, out)
        distilled = out.split('seeded a candidate policy: "')[1].split('"')[0]
        print(f"  (distilled statement: {distilled!r})")

        # --- NONE path: a one-off remark should not become policy ---
        out = run(ws, "guide")
        if "policy_reminder" in out:
            out = run(ws, "review", "1", "--reject", "--explain",
                      "This was a one-off exception for this particular "
                      "chapter only, not a general practice of mine")
            check("one-off explanation not generalized into policy",
                  "seeded a candidate policy" not in out, out)

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
            if line.strip().startswith(("•", "↳", "policy", "outcome")):
                print(f"   {line}")

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
