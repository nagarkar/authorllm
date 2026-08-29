"""Essay summaries (critique-pass design §4): reading order, autoregressive
conditioning, mark-don't-cascade staleness, rebuild semantics.

Run: python3 tests/test_summaries.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"

def _assert_offline() -> None:
    """Fail loudly if the real project config or .env leaks into a test.

    Before config moved into the repo, a test workspace simply had no
    config.toml, so the LLM was off and no suite could make a billed
    call. Now a checkout always HAS an enabled config, and that safety
    came from nothing but the pins above — so it is asserted, not
    assumed."""
    import os as _os

    from authorlm import paths as _paths

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"
    leaked = [v for v in _paths_vendor_vars() if _os.environ.get(v)]
    assert not leaked, f"test isolation broken: vendor keys in env: {leaked}"


def _paths_vendor_vars() -> list:
    from authorlm.llm import VENDOR_KEY_ENV

    return sorted(VENDOR_KEY_ENV.values())


import contextlib
import http.server
import io
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, summaries as sums  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


class EchoSummarizer(http.server.BaseHTTPRequestHandler):
    """Canned summarizer: replies with the unit name plus how many prior
    summaries it saw, so conditioning is observable."""
    calls: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        user = body["messages"][-1]["content"]
        if "THE UNIT: " not in user:
            # Any other LLM step (init's extraction, etc.): empty JSON reply.
            self._reply(json.dumps({"concepts": [], "links": [], "aliases": []}))
            return
        unit = user.split("THE UNIT: ", 1)[1].split("\n", 1)[0]
        prior_block = user.split("PRIOR SUMMARIES (reading order):\n", 1)[1] \
            .split("\n\nCONCEPTS", 1)[0]
        n_prior = (0 if "first unit" in prior_block
                   else sum(1 for line in prior_block.splitlines()
                            if line.startswith("[") and line.endswith("]")))
        EchoSummarizer.calls.append({"unit": unit, "n_prior": n_prior,
                                     "model": body.get("model"), "user": user})
        self._reply(f"MOVES: summary of {unit} conditioned on {n_prior} prior.")

    def _reply(self, content: str) -> None:
        payload = json.dumps({
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-summaries-"))
    server = http.server.HTTPServer(("127.0.0.1", 0), EchoSummarizer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        ws = root / "ws"
        ms = ws / "book"
        ms.mkdir(parents=True)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "title.md"\nmatter = "front"\n\n'
            '[[chapter]]\nfile = "part1.md"\n\n'
            '[[chapter]]\nfile = "alpha.md"\nparent = "part1.md"\n\n'
            '[[chapter]]\nfile = "beta.md"\nparent = "part1.md"\n\n'
            '[[chapter]]\nfile = "gamma.md"\n')
        for name in ("title", "part1", "alpha", "beta", "gamma"):
            (ms / f"{name}.md").write_text(f"# {name}\n\nText of {name}.\n")
        (ws / ".authorlm").mkdir()
        (ws / ".authorlm" / "config.toml").write_text(
            "[llm]\nenabled = true\nprovider = \"openai\"\n"
            f"base_url = \"http://127.0.0.1:{server.server_port}/v1\"\n"
            "model = \"general-model\"\n\n"
            "[critique]\nsummarizer_model = \"cheap-model\"\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        # Establish the v1 baseline explicitly. `init` used to do this as a
        # side effect of an LLM-enabled extraction pass picked up from a
        # leaked project config (closed by the AUTHORLM_CONFIG/AUTHORLM_ENV
        # pins above); with the suite correctly offline, `init` no longer
        # runs extraction, so "mark, don't cascade" below needs its own
        # explicit prior version to diff the alpha.md edit against.
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, {}, source="test")

        print("reading order & status:")
        order = [f for f, _ in sums.units(manuscript)]
        check("units follow toc reading order, front matter included",
              order == ["title.md", "part1.md", "alpha.md", "beta.md",
                        "gamma.md"], str(order))
        check("everything is missing before the first build",
              all(r["state"] == "missing" for r in sums.status(db, manuscript)))

        print("full rebuild:")
        import tomllib
        config = tomllib.loads((ws / ".authorlm" / "config.toml").read_text())
        llm = sums.summarizer_llm(config)
        check("[critique] summarizer_model overrides the general model",
              llm.model == "cheap-model")
        result = sums.rebuild(db, manuscript, llm)
        check("rebuild summarizes every unit in order",
              result["built"] == order and result["reused"] == [])
        check("the summarizer call used the cheap model",
              all(c["model"] == "cheap-model" for c in EchoSummarizer.calls))
        n_priors = [c["n_prior"] for c in EchoSummarizer.calls]
        check("each unit is conditioned on all prior summaries (autoregressive)",
              n_priors == [0, 1, 2, 3, 4], str(n_priors))
        stored = sums.all_summaries(db, manuscript["id"])
        check("upstream_hash actually reflects the prior-summary set each "
              "unit was conditioned on — distinct per unit, not a constant "
              "placeholder (it grows by one summary each step)",
              len({stored[f]["upstream_hash"] for f in order}) == len(order),
              str({f: stored[f]["upstream_hash"] for f in order}))
        check("all fresh after a full build",
              all(r["state"] == "fresh" for r in sums.status(db, manuscript)))

        print("length scaling (Task 1 — sponsor's ratio):")
        check("a ~1500-word essay scales to ~150-200 words",
              sums.target_length(" ".join(["w"] * 1500)) == (150, 200))
        check("a ~3000-word essay scales to ~300-400 words — longer essay, "
              "longer target",
              sums.target_length(" ".join(["w"] * 3000)) == (300, 400))
        check("a tiny unit is floored, not squeezed to ~0 words",
              sums.target_length("one two three") == (40, 50))
        check("a huge unit is capped, not left to grow unbounded",
              sums.target_length(" ".join(["w"] * 20000))
              == (sums.MAX_SUMMARY_WORDS - 10, sums.MAX_SUMMARY_WORDS))
        check("every built unit's prompt carries its own TARGET LENGTH line",
              all("TARGET LENGTH: 40-50 words" in c["user"]
                  for c in EchoSummarizer.calls),
              "\n".join(c["user"][:200] for c in EchoSummarizer.calls))

        print("paragraph coverage (Task 1 — no paragraph silently dropped):")
        check("every built unit's prompt numbers THE UNIT's paragraphs and "
              "states the count, so MOVES can be required to cite each one",
              all("PARAGRAPH COUNT: 2" in c["user"] and "[1] #" in c["user"]
                  and "[2] Text of" in c["user"] for c in EchoSummarizer.calls),
              "\n".join(c["user"] for c in EchoSummarizer.calls[:1]))

        print("paragraph coverage is VERIFIED, not just requested "
              "(sponsor addendum):")
        full = sums.paragraph_coverage(
            "MOVES: [1] states the claim; [2]-[3] develop it by example.", 3)
        check("a summary citing every paragraph reports full coverage",
              full == {"paragraph_count": 3, "cited": [1, 2, 3],
                       "missing": [], "out_of_range": [], "complete": True},
              str(full))
        gappy = sums.paragraph_coverage(
            "MOVES: [1] states the claim. [3] closes it.", 3)
        check("a summary omitting a paragraph reports exactly which one",
              gappy["missing"] == [2] and gappy["out_of_range"] == []
              and not gappy["complete"], str(gappy))
        invented = sums.paragraph_coverage(
            "MOVES: [1] states the claim. [7] invents a paragraph.", 2)
        check("a citation past PARAGRAPH COUNT is reported as out-of-range "
              "(the model invented a paragraph), not silently accepted",
              invented["out_of_range"] == [7] and invented["missing"] == [2]
              and not invented["complete"], str(invented))
        # The canned EchoSummarizer reply ("MOVES: summary of X conditioned
        # on N prior.") cites no paragraph numbers at all — so a real
        # rebuild against it must show every built unit as incomplete.
        # This is the live plumbing (summarize_unit -> rebuild), not just
        # the pure function above.
        check("rebuild() surfaces incomplete coverage per file — reported, "
              "never silently dropped and never blocking the rebuild",
              set(result.get("incomplete_coverage", {})) == set(order)
              and all(not cov["complete"] and cov["out_of_range"] == []
                      and cov["missing"] == [1, 2]
                      for cov in result["incomplete_coverage"].values()),
              str(result.get("incomplete_coverage")))
        check("coverage is reported, never enforced: an incomplete summary "
              "is still stored and the unit still reads fresh",
              all(r["state"] == "fresh" for r in sums.status(db, manuscript)))

        print("before/after context:")
        before, after = sums.before_after(db, manuscript, "alpha.md")
        check("before/after split around the target unit",
              [b["file"] for b in before] == ["title.md", "part1.md"]
              and [a["file"] for a in after] == ["beta.md", "gamma.md"]
              and all(b["state"] == "fresh" for b in before + after))

        print("mark, don't cascade:")
        calls_before_collect = len(EchoSummarizer.calls)
        (ms / "alpha.md").write_text("# alpha\n\nText of alpha, revised.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
        check("marking downstream stale makes NO summarizer call at all — "
              "'does not cascade' means no rebuild happens, not just that "
              "it happens later",
              len(EchoSummarizer.calls) == calls_before_collect)
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("the changed unit is stale by source_hash",
              states["alpha.md"] == "stale")
        check("downstream units are flagged upstream_stale (tolerated)",
              states["beta.md"] == "upstream_stale"
              and states["gamma.md"] == "upstream_stale")
        check("upstream units are untouched",
              states["title.md"] == "fresh" and states["part1.md"] == "fresh")

        print("rebuild_one (the confirmation-gate rebuild):")
        EchoSummarizer.calls.clear()
        sums.rebuild_one(db, manuscript, "alpha.md", llm)
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("only the one unit was re-summarized",
              [c["unit"] for c in EchoSummarizer.calls] == ["alpha.md"])
        check("it is fresh again; downstream stays upstream_stale",
              states["alpha.md"] == "fresh"
              and states["beta.md"] == "upstream_stale")
        check("rebuild_one clears staleness for THAT essay only — units "
              "further upstream are untouched, not just 'still fresh by "
              "coincidence'",
              states["title.md"] == "fresh" and states["part1.md"] == "fresh")

        print("before_after / status report truthfully mid-cascade (a "
              "genuinely partial rebuild — beta.md is not yet rebuilt):")
        before, after = sums.before_after(db, manuscript, "gamma.md")
        check("before gamma: alpha (just rebuilt) reads fresh, beta (not "
              "yet touched) still reads upstream_stale — before_after is "
              "not just 'all fresh' or 'all stale', it reflects the real "
              "per-file state mid-cascade",
              [b["file"] for b in before]
              == ["title.md", "part1.md", "alpha.md", "beta.md"]
              and {b["file"]: b["state"] for b in before}["alpha.md"] == "fresh"
              and {b["file"]: b["state"] for b in before}["beta.md"]
              == "upstream_stale",
              str(before))
        gamma_status = next(r for r in sums.status(db, manuscript)
                            if r["file"] == "gamma.md")
        check("status() agrees: gamma itself is upstream_stale mid-cascade",
              gamma_status["state"] == "upstream_stale")

        print("incremental rebuild (default) cascades only from the first "
              "non-fresh unit:")
        EchoSummarizer.calls.clear()
        result = sums.rebuild(db, manuscript, llm, only_missing_or_stale=True)
        check("fresh prefix reused, rest rebuilt in order",
              result["reused"] == ["title.md", "part1.md", "alpha.md"]
              and result["built"] == ["beta.md", "gamma.md"], str(result))
        check("everything fresh again",
              all(r["state"] == "fresh" for r in sums.status(db, manuscript)))

        print("CLI surface:")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "summarize", "status"])
        check("summarize status lists every unit with a state",
              buf.getvalue().count("fresh") >= 5)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "summarize", "show", "beta.md"])
        check("summarize show prints the stored summary",
              "summary of beta.md" in buf.getvalue())
        # `cli_main`'s config loader reads the project config.toml
        # (AUTHORLM_CONFIG), not `--workspace`'s local one — so this call
        # needs the pin pointed at the workspace config we wrote above (the
        # one with `base_url` aimed at EchoSummarizer) or it would either
        # find the LLM disabled (pinned to /nonexistent) or, unpinned,
        # silently attempt a real call to the provider named in the repo's
        # own config.toml. Scoped to this one call; restored immediately.
        prev_config = os.environ.get("AUTHORLM_CONFIG")
        os.environ["AUTHORLM_CONFIG"] = str(ws / ".authorlm" / "config.toml")
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli_main(["--workspace", str(ws), "summarize", "rebuild", "gamma.md"])
        finally:
            if prev_config is None:
                os.environ.pop("AUTHORLM_CONFIG", None)
            else:
                os.environ["AUTHORLM_CONFIG"] = prev_config
        check("summarize rebuild <file> reports the one-unit rebuild",
              "rebuilt gamma.md" in buf.getvalue())
        check("summarize rebuild <file> also surfaces incomplete paragraph "
              "coverage on the CLI (the echo stub cites no paragraph, so "
              "this must fire)",
              "paragraph coverage incomplete" in buf.getvalue()
              and "missing paragraph(s) [1, 2]" in buf.getvalue(),
              buf.getvalue())

        print("prompt artifact:")
        prompt = sums.summarizer_prompt()
        check("the summarizer prompt is a checked-in file with the labeled "
              "sections",
              all(k in prompt for k in ("MOVES:", "INTRODUCES:", "PROMISES:",
                                        "DEBTS:", "DOES NOT:")))

        print("prompt registry & discoverability:")
        from authorlm import prompt_registry as pr
        check("every registered prompt resolves to real text",
              all(len(p.text()) > 40 for p in pr.REGISTRY))
        check("registry knows the summarizer as a file prompt",
              pr.by_name("summarizer").file == "summarizer.md")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            # --workspace matters here even though "prompts" touches no
            # DB: cli.py's dispatch traces every command via
            # tracelog.record(workspace=args.workspace), which defaults to
            # the real home directory when unset — an unpinned call here
            # would write into the author's actual ~/.authorlm trace log.
            cli_main(["--workspace", str(ws), "prompts"])
        listing = buf.getvalue()
        check("'authorlm prompts' lists every prompt with its location",
              all(p.name in listing and p.location in listing
                  for p in pr.REGISTRY))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "prompts", "show", "summarizer"])
        check("'prompts show' prints the prompt text",
              "MOVES:" in buf.getvalue())
        for verb in ("summarize", "extract", "guide", "illus", "critique"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
                cli_main([verb, "--help"])
            assert "authorlm prompts" in buf.getvalue(), \
                f"{verb} --help lacks the LLM-prompt note"
        check("every LLM-using verb's --help points at the prompt registry",
              True)
    finally:
        server.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    _assert_offline()
    main_test()
