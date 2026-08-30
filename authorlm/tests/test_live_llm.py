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

SECTIONS (AL/live-suite-sections). The suite is one continuous narrative
against a single workspace — 'bridge' declares the intent that 'episode'
later completes, 'belief' reviews the suggestion 'bridge' raised — so
sections are not independent test functions. What they ARE independent of
is EACH OTHER'S FAILURES: each section runs in its own try/except, and a
failure in one (a weak extraction reply, a changed prompt, …) no longer
aborts the sections after it. Every section always runs; failures are
collected and reported together at the end, with a non-zero exit code if
any failed. This is why the beat-draft section (rc/27) was unreachable
before this restructuring — an earlier section's assertion failure took
the whole script down with it, cache poisoning and all (see below).

Recording hygiene: `LLMClient.cache_dir` (authorlm/llm.py) is one fixed
path for the whole run — writing straight to CACHE_DIR/<hash>.json the
moment a live call returns, with no staging directory to redirect it to
without threading a per-section cache_dir through config.toml and every
`run()` call, which is more moving parts than the alternative: each
section snapshots CACHE_DIR's file set before it runs and, only if it
raises, deletes whatever is new in that set afterward. A section that
passes keeps every recording it made, exactly as before; a section that
fails leaves the cache exactly as it found it. This is what stops a weak
reply from pinning the suite deterministically red on replay (the
incident that motivated this file).

Run: python3 tests/test_live_llm.py
     python3 tests/test_live_llm.py --section beat-draft   # one section only
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


def _cache_files() -> set[Path]:
    """The replay cache's current file set — the unit recording hygiene
    tracks. Empty when CACHE_DIR doesn't exist yet (nothing recorded)."""
    return set(CACHE_DIR.glob("*.json")) if CACHE_DIR.exists() else set()


# ------------------------------------------------------------------ sections
#
# Each section is a plain function taking the shared `ctx` dict (the
# workspace paths and whether a live key is available — the same values
# every section used to close over as locals in one long main_test()).
# They are NOT independent of each other's DATA — 'belief' reviews the
# suggestion 'bridge' raised in this same session, 'episode' completes the
# intent 'bridge' declared — only independent of each other's FAILURES,
# which run_section() below is what provides.

def section_extraction(ctx: dict) -> None:
    """Extraction (live or replayed). Owns the one `init` call: this is
    also where the incident that motivated this file happened (a weak
    live reply recorded into the cache, then failing its own assertion)."""
    ws, ms = ctx["ws"], ctx["ms"]
    out = run(ws, "init", "--name", "sample", "--path", str(ms))
    if "Extraction produced nothing" in out:
        if not ctx["key_available"]:
            print(
                "SKIP: replay cache is incomplete for the current prompts "
                "and GEMINI_API_KEY is not set. Export the key once to "
                "record.")
            return
        raise AssertionError(f"extraction failed despite key being set:\n{out}")
    extracted = int(out.split("Extracted ")[1].split(" new concept")[0])
    check("extraction finds a philosophical vocabulary (≥3 concepts)",
          extracted >= 3, out)
    out = run(ws, "concept", "list")
    check("extracted concepts realize against the sample text",
          "realized" in out, out)


def section_bridge(ctx: dict) -> None:
    """Bridge drafting (live or replayed). Declares the intent and
    concept that 'episode' later completes against."""
    ws = ctx["ws"]
    run(ws, "concept", "add", "History")   # declared, absent from text
    run(ws, "intent", "declare", "Introduce histories")
    out = run(ws, "guide")
    check("bridge suggested for the declared-but-unwritten concept",
          "Introduce 'History'" in out, out)
    check("LLM drafted bridge text included", "Draft to consider" in out, out)


def section_belief(ctx: dict) -> None:
    """Belief distillation (live or replayed). Reviews suggestion [1] from
    'bridge's guide batch in this session — it will not find one to review
    if 'bridge' didn't run first (or failed before its 'guide' call)."""
    ws = ctx["ws"]
    out = run(ws, "review", "1", "--reject", "--explain",
              "Always ground an abstract definition in a lived example "
              "before formalizing it")
    check("explanation distilled into a candidate belief",
          "seeded a candidate belief" in out, out)
    distilled = out.split('seeded a candidate belief: "')[1].split('"')[0]
    print(f"  (distilled statement: {distilled!r})")


def section_none_path(ctx: dict) -> None:
    """NONE path (live or replayed): a one-off remark should not become a
    belief. Self-contained — it raises its own guide batch to review."""
    ws = ctx["ws"]
    out = run(ws, "guide")
    if "belief_reminder" in out:
        out = run(ws, "review", "1", "--reject", "--explain",
                  "This was a one-off exception for this particular "
                  "chapter only, not a general practice of mine")
        check("one-off explanation not generalized into belief",
              "seeded a candidate belief" not in out, out)


def section_episode(ctx: dict) -> None:
    """Episode analysis (live or replayed): learn from actual edits.
    Completes the 'Introduce histories' intent 'bridge' declared."""
    ws, ms = ctx["ws"], ctx["ms"]
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
        if "(active ·" in line and "histories" in line:
            intent_id = line.split("[")[1].split("]")[0]
    out = run(ws, "intent", "complete", intent_id, "--outcome",
              "Histories introduced via the chess story")
    check("episode analysis runs at intent completion",
          "Analyzed episode" in out, out)
    print("  (analysis output)")
    for line in out.splitlines():
        if line.strip().startswith(("•", "↳", "belief", "outcome")):
            print(f"   {line}")


def section_beat_draft(ctx: dict) -> None:
    """write draft (live or replayed): the whole beat-drafting seam.
    Self-contained: its own style guide, its own intent, its own file —
    the only cross-section need is manuscript registration + an active
    session, which bootstrap provides for every section (main_test)."""
    ws, ms = ctx["ws"], ctx["ms"]
    key_available = ctx["key_available"]
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


# Order matters (the narrative), but 'extraction' is handled separately in
# main_test() because it owns the one `init` call every other section also
# needs (manuscript registration) — see main_test()'s bootstrap comment.
SECTIONS: list[tuple[str, "object"]] = [
    ("bridge", section_bridge),
    ("belief", section_belief),
    ("none-path", section_none_path),
    ("episode", section_episode),
    ("beat-draft", section_beat_draft),
]
SECTION_NAMES = ["extraction"] + [name for name, _ in SECTIONS]


def main_test(only: str | None = None) -> None:
    if only is not None and only not in SECTION_NAMES:
        sys.exit(f"error: unknown --section '{only}'. Sections: "
                 f"{', '.join(SECTION_NAMES)}.")

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
        + (f", section={only!r}" if only else "")
    )

    root = Path(tempfile.mkdtemp(prefix="authorlm-live-"))
    failures: list[str] = []
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

        ctx = {"ws": ws, "ms": ms, "key_available": key_available}

        def run_section(name: str, fn) -> None:
            before = _cache_files()
            print(f"\n-- {name} --")
            try:
                fn(ctx)
            except Exception as err:  # noqa: BLE001 — collected, not fatal
                # Recording hygiene: only files this section itself wrote
                # are candidates for removal — anything recorded by an
                # earlier, PASSING section (or present before this run
                # started) is untouched.
                new_files = sorted(_cache_files() - before)
                for path in new_files:
                    path.unlink()
                removed = (f" (removed {len(new_files)} newly-recorded "
                          f"fixture(s): {', '.join(p.name for p in new_files)})"
                          if new_files else "")
                print(f"SECTION FAILED: {name} — {err}{removed}")
                failures.append(name)

        # Bootstrap: every section needs the manuscript registered and a
        # session active — neither is itself one of the six checked
        # sections, but 'extraction' is where registration happens because
        # it is also the one live call that can leave a bad recording
        # behind (the incident this file exists to fix), so it gets the
        # same try/except + hygiene treatment as any other section. A
        # `--section` run that isn't testing extraction registers with
        # `--no-extract`: it still needs the manuscript row, not a second
        # concept-extraction call it isn't there to test or pay for.
        if only in (None, "extraction"):
            run_section("extraction", section_extraction)
        else:
            run(ws, "init", "--name", "sample", "--path", str(ms),
                "--no-extract")
        run(ws, "session", "start")

        for name, fn in SECTIONS:
            if only is None or only == name:
                run_section(name, fn)

        try:
            run(ws, "session", "end")
        except AssertionError as err:
            # Cleanup, not a check: a real failure above already explains
            # why the workspace might be in a state 'session end' dislikes.
            print(f"note: 'session end' did not exit cleanly: {err}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    cached_after, in_after, out_after = cache_usage()
    stats = (
        f"LLM: {cached_after - cached_before} new live call(s) "
        f"({in_after - in_before:,} in / {out_after - out_before:,} out tokens); "
        f"replay cache covers {cached_after} call(s) "
        f"({in_after:,} in / {out_after:,} out tokens total)."
    )
    if failures:
        print(f"\n{PASSED} check(s) passed; {len(failures)} section(s) "
              f"FAILED: {', '.join(failures)}.")
        print(stats)
        sys.exit(1)
    print(f"\nAll {PASSED} checks passed.")
    print(stats)


if __name__ == "__main__":
    _only = None
    _argv = sys.argv[1:]
    if _argv:
        if len(_argv) == 2 and _argv[0] == "--section":
            _only = _argv[1]
        else:
            sys.exit(f"usage: {sys.argv[0]} [--section <name>]\n"
                     f"sections: {', '.join(SECTION_NAMES)}")
    main_test(_only)
