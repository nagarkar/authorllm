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
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"

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
        bare = sums.drafting_context(db, manuscript, "alpha.md")
        check("with no summaries at all the drafting context still renders "
              "(it is the resume view, never a gate) but every entry is "
              "loudly marked, not quietly blank",
              bare.count("!! summary MISSING") == 4
              and bare.count("(no summary — run 'summarize rebuild')") == 4,
              bare)

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
        # Z-M1: the form the REAL summarizer writes is a cross-bracket
        # range, "[1]-[11]", not the in-bracket "[2-3]" the prompt's
        # example shows. Parsing only the latter counted every interior
        # paragraph as missing — the live rebuild reported 350+ missing
        # across 18 of 24 essays where the truth is 11 of 1,138.
        cross = sums.paragraph_coverage(
            "MOVES: [1]-[4] set up the problem; [6] closes it.", 6)
        check("a CROSS-BRACKET range counts every paragraph it spans, not "
              "just its two endpoints",
              cross["cited"] == [1, 2, 3, 4, 6] and cross["missing"] == [5]
              and cross["out_of_range"] == [], str(cross))
        check("an en-dash cross-bracket range, whitespace and all, parses too",
              sums._cited_paragraphs("[2] – [5]") == {2, 3, 4, 5},
              str(sums._cited_paragraphs("[2] – [5]")))
        check("the in-bracket range form still parses (unchanged)",
              sums._cited_paragraphs("[2-3]") == {2, 3})
        check("a bare hyphenated number in prose is NOT a citation — the "
              "brackets are what make it one",
              sums._cited_paragraphs("the argument of 3-4 pages") == set(),
              str(sums._cited_paragraphs("the argument of 3-4 pages")))
        check("a range consumes both its brackets, so a citation right "
              "after it is still read on its own",
              sums._cited_paragraphs("[1]-[3], then [7]") == {1, 2, 3, 7},
              str(sums._cited_paragraphs("[1]-[3], then [7]")))
        # The flip side of parsing ranges: one malformed blanket range can
        # now put ~100 out-of-range numbers into a coverage note, and that
        # note goes verbatim into the drafting payload.
        six_paras = "\n\n".join(f"Paragraph {i}." for i in range(1, 7))
        blanket = sums._coverage_note(
            {"summary": "MOVES: [1]-[100] covers the lot.", "state": "fresh"},
            six_paras)
        check("a malformed blanket range does not dump 94 numbers into the "
              "drafting payload — the list is capped and says how many "
              "more it is hiding",
              blanket.count("¶") == sums.COVERAGE_LIST_CAP
              and "…and 86 more" in blanket
              and "the essay has 6 paragraph(s)" in blanket, blanket)
        short = sums._coverage_note(
            {"summary": "MOVES: [1] only.", "state": "fresh"}, six_paras)
        check("a short list is NOT capped — no '…and N more' noise when "
              "every number already fits",
              "…and" not in short and short.count("¶") == 5, short)
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

        print("drafting context (§12.4 item 1 — the L1 glue) with LOUD "
              "coverage reporting (item 4 — informational, never blocking):")
        ctx = sums.drafting_context(db, manuscript, "alpha.md")
        check("BEFORE holds the settled prefix in reading order and AFTER "
              "the upcoming essays, each entry labeled with its file",
              ctx.index("BEFORE") < ctx.index("[title.md]")
              < ctx.index("[part1.md]") < ctx.index("AFTER")
              < ctx.index("[beta.md]") < ctx.index("[gamma.md]"), ctx)
        check("the essay being drafted is never an entry in its own context",
              "[alpha.md]" not in ctx, ctx)
        check("each entry carries its stored summary verbatim",
              "summary of title.md conditioned on 0 prior" in ctx
              and "summary of gamma.md conditioned on 4 prior" in ctx, ctx)
        check("AFTER is marked as forward-reference material only (§7's "
              "continuity contract), BEFORE as available",
              "forward-reference" in ctx.split("AFTER", 1)[1]
              and "AVAILABLE" in ctx.split("BEFORE", 1)[1].split("AFTER", 1)[0]
              and "NOT available" in ctx.split("AFTER", 1)[1], ctx)
        check("serialization is deterministic (§3: a silent invalidator "
              "forfeits the cache economics) — two renders of unchanged "
              "state are byte-identical, no timestamps, no run ids",
              ctx == sums.drafting_context(db, manuscript, "alpha.md"), ctx)
        check("incomplete paragraph coverage is reported LOUDLY per entry "
              "(the echo stub cites no paragraph number at all)",
              ctx.count("coverage INCOMPLETE") == 4
              and ctx.count("¶1, ¶2 uncited") == 4, ctx)
        check("...and it never blocks: the context still renders every "
              "summary in full (item 4's default is informational)",
              all(f"[{f}]" in ctx for f in
                  ("title.md", "part1.md", "beta.md", "gamma.md")), ctx)

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

        print("...and the drafting context says so LOUDLY — the less "
              "trustworthy entry must not be the quieter one:")
        ctx = sums.drafting_context(db, manuscript, "gamma.md")
        stale_block = ctx.split("[alpha.md]", 1)[1].split("[beta.md]", 1)[0]
        check("a STALE entry — exactly what the write_start gate refuses — "
              "carries a loud warning naming the fix",
              "!! summary STALE" in stale_block
              and "summarize rebuild" in stale_block, ctx)
        check("...and its summary text is still rendered: loud, not "
              "blocking (write status is the resume entry point)",
              "summary of alpha.md conditioned on 2 prior" in stale_block,
              ctx)
        fresh_block = ctx.split("[title.md]", 1)[1].split("[part1.md]", 1)[0]
        check("a FRESH entry carries no such warning — the marker means "
              "something because it is not on everything",
              "!! summary STALE" not in fresh_block
              and "!! summary MISSING" not in fresh_block, ctx)
        upstream_block = ctx.split("[beta.md]", 1)[1]
        check("an UPSTREAM_STALE entry carries no warning either — the "
              "gate tolerates it (its text did not move, only its "
              "conditioning), so the context must not cry wolf",
              "!! summary" not in upstream_block, ctx)

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

        print("deprecated status (§12.4 item 5): a departed essay's summary "
              "is respected as not-live, and RESURRECTED when the file "
              "returns to the toc:")
        from authorlm import passes as _passes
        gamma_text = (ms / "gamma.md").read_text()
        (ms / "gamma.md").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
        row = db.one("SELECT status FROM essay_summaries WHERE "
                     "manuscript_id = ? AND file = 'gamma.md'",
                     (manuscript["id"],))
        check("a departed essay's summary is marked deprecated, never "
              "deleted (commit 10497c9's semantics)",
              row is not None and row["status"] == "deprecated",
              str(dict(row)) if row else "no row")
        # It comes back byte-identical, so `source_hash` still matches —
        # the exact hazard: without status in the freshness computation the
        # row would read 'fresh' and silently condition a drafting pass
        # while the DB still says it is not a live summary.
        (ms / "gamma.md").write_text(gamma_text)
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("a returned essay whose row is still deprecated reads "
              "'deprecated', not 'fresh' — a matching source_hash must not "
              "let a not-live row pass as a live summary",
              states["gamma.md"] == "deprecated", str(states))
        _b, after = sums.before_after(db, manuscript, "beta.md")
        check("before_after (what the edit and drafting contexts read) "
              "reports the deprecated state per entry",
              {e["file"]: e["state"] for e in after}["gamma.md"]
              == "deprecated", str(after))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "summarize", "status"])
        check("summarize status renders the deprecated state (a new state "
              "must not KeyError the CLI's colour table)",
              "deprecated" in buf.getvalue(), buf.getvalue())
        dep_ctx = sums.drafting_context(db, manuscript, "beta.md")
        check("the drafting context marks a DEPRECATED entry loudly too — "
              "the third state the start gate refuses",
              "!! summary DEPRECATED" in dep_ctx.split("[gamma.md]", 1)[1],
              dep_ctx)
        ready = _passes.summaries_ready(db, manuscript, "beta.md")
        check("summaries_ready blocks on a deprecated summary exactly as "
              "on a missing or stale one",
              not ready["ok"] and ready["deprecated"] == ["gamma.md"],
              str({k: v for k, v in ready.items()
                   if k in ("ok", "missing", "stale", "deprecated")}))
        EchoSummarizer.calls.clear()
        resurrect = sums.rebuild(db, manuscript, llm, only_missing_or_stale=True)
        check("the incremental rebuild TARGETS the deprecated unit (it is "
              "not fresh) instead of reusing it",
              resurrect["built"] == ["gamma.md"], str(resurrect))
        check("rebuilding the returned essay flips its row back to "
              "'current' — the resurrect path",
              db.one("SELECT status FROM essay_summaries WHERE "
                     "manuscript_id = ? AND file = 'gamma.md'",
                     (manuscript["id"],))["status"] == "current")
        check("...and it reads fresh again, so the gate clears",
              {r["file"]: r["state"]
               for r in sums.status(db, manuscript)}["gamma.md"] == "fresh"
              and _passes.summaries_ready(db, manuscript, "beta.md")["ok"])

        print("placement-aware before_after (§12.4 item 3 — an essay with "
              "no toc entry yet):")
        # The design said an unplaced essay "raises LookupError from
        # before_after". It did not: structure.reading_order APPENDS an
        # on-disk file missing from toc.toml, so it landed at the END of
        # the reading order and every other essay read as settled context
        # behind it with nothing ahead — §7's forward-reference contract
        # inverted, silently, for exactly the file being drafted. That
        # silence is the bug; the position is not guessable, so it must be
        # refused.
        (ms / "delta.md").write_text("# delta\n\nText of delta.\n")
        order_now = [f for f, _ in sums.units(manuscript)]
        check("an unlisted-but-on-disk essay IS still appended to the "
              "reading order (structure.reading_order is unchanged)",
              order_now[-1] == "delta.md" and len(order_now) == 6,
              str(order_now))
        try:
            sums.before_after(db, manuscript, "delta.md")
            raised = None
        except LookupError as err:
            raised = str(err)
        check("...but before_after REFUSES it with no placement rather "
              "than silently treating it as the last essay, and names both "
              "remedies (a toc entry, or --after)",
              raised is not None and "no toc.toml entry" in raised
              and "--after" in raised and "--after start" in raised,
              str(raised))
        before, after = sums.before_after(db, manuscript, "delta.md",
                                          placement="alpha.md")
        check("placement=<existing file> splits the context as if delta sat "
              "immediately after that file",
              [b["file"] for b in before] == ["title.md", "part1.md",
                                              "alpha.md"]
              and [a["file"] for a in after] == ["beta.md", "gamma.md"],
              str(([b["file"] for b in before], [a["file"] for a in after])))
        before, after = sums.before_after(db, manuscript, "delta.md",
                                          placement=sums.PLACEMENT_START)
        check("placement=PLACEMENT_START puts the new essay first — nothing "
              "settled behind it, the whole book ahead as forward-reference "
              "material",
              before == []
              and [a["file"] for a in after] == ["title.md", "part1.md",
                                                 "alpha.md", "beta.md",
                                                 "gamma.md"], str(after))
        try:
            sums.before_after(db, manuscript, "delta.md",
                              placement="no-such-file.md")
            raised = None
        except LookupError as err:
            raised = str(err)
        check("an unknown placement target is refused, never guessed at",
              raised is not None and "no-such-file.md" in raised, str(raised))
        try:
            sums.before_after(db, manuscript, "never-existed.md")
            raised = None
        except LookupError as err:
            raised = str(err)
        check("a file that is not on disk at all still raises LookupError "
              "with no placement (the unchanged path)",
              raised is not None and "never-existed.md" in raised, str(raised))
        # UC-A exercises the same function from a new angle: `write start
        # --new` runs the whole freshness gate BEFORE the file is created,
        # so the target is not merely unlisted, it is not on disk. That
        # works because `rest = [f for f in order if f != file]` never
        # requires `file` to be in the reading order when a placement is
        # given — and it is what lets a blocked start leave the disk
        # untouched (design-usecases §1.1.5, RISK K1).
        before, after = sums.before_after(db, manuscript, "never-existed.md",
                                          placement="alpha.md")
        check("WITH a placement, an essay that does not exist yet splits "
              "the context correctly and appears in neither half — this is "
              "what lets 'write start --new' gate before it creates",
              [b["file"] for b in before] == ["title.md", "part1.md",
                                              "alpha.md"]
              and [a["file"] for a in after] == ["beta.md", "gamma.md",
                                                 "delta.md"],
              str(([b["file"] for b in before], [a["file"] for a in after])))
        ctx = sums.drafting_context(db, manuscript, "delta.md",
                                    placement="alpha.md")
        check("drafting_context carries the placement through, and says so "
              "in its header so the drafting model knows where it stands",
              "placed after alpha.md" in ctx
              and ctx.index("[alpha.md]") < ctx.index("AFTER")
              < ctx.index("[beta.md]"), ctx)
        check("PLACEMENT_START renders its own header",
              "placed first" in sums.drafting_context(
                  db, manuscript, "delta.md",
                  placement=sums.PLACEMENT_START))
        (ms / "delta.md").unlink()

        print("in-flight context — parallel writeups (design §14):")
        # A file with an ACTIVE writeup is not described by its bytes on
        # disk: `write start` truncated it and left a placeholder there.
        # What it contributes to every other essay's context is its
        # PINNED text, out of the version the writeup pinned.
        from authorlm import api as _api

        # beta.md gets a paragraph count of its own (4), so a coverage
        # note computed from the PINNED text cannot be mistaken for one
        # computed from the placeholder (which has 2).
        BETA_TEXT = ("# beta\n\nText of beta.\n\nA second paragraph.\n\n"
                     "A third paragraph.\n")
        (ms / "beta.md").write_text(BETA_TEXT)
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
            sums.rebuild(db, manuscript, llm, only_missing_or_stale=True)
        check("fixture: everything is fresh before any writeup opens",
              all(r["state"] == "fresh" for r in sums.status(db, manuscript)),
              str(sums.status(db, manuscript)))
        api.define_style_guide(db, manuscript, "House")
        api.attach_style(db, manuscript, "beta.md", "House")
        intent = api.declare_intent(db, manuscript, "Rewrite beta")["intent"]
        with contextlib.redirect_stdout(io.StringIO()):
            started = api.write_start(db, manuscript, config, "beta.md",
                                      intent["id"][:8])
        beta_wu = started["writeup"]["id"]

        disk = dict(sums.units(manuscript))
        ctx_texts = dict(sums.context_units(db, manuscript))
        check("S10 — units() is still DISK truth: it returns the "
              "placeholder the writeup left behind",
              disk["beta.md"] == _api.PLACEHOLDER, repr(disk["beta.md"]))
        check("S10 — context_units() is CONTEXT truth: the same file "
              "contributes its PINNED pre-rewrite text instead. The two "
              "must never quietly merge — that split is the whole design",
              ctx_texts["beta.md"] == BETA_TEXT
              and ctx_texts["beta.md"] != disk["beta.md"],
              repr(ctx_texts["beta.md"]))

        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("S1 — an in-flight file whose stored summary matches its "
              "PINNED text reads 'rewriting', not 'stale' (which is what "
              "blocked every other writeup)",
              states["beta.md"] == "rewriting", str(states))
        before, after = sums.before_after(db, manuscript, "gamma.md")
        beta_entry = {e["file"]: e for e in before + after}["beta.md"]
        check("S1 — and what is served is the PRE-REWRITE summary, flagged "
              "as in flight",
              beta_entry["state"] == "rewriting"
              and beta_entry["in_flight"] is True
              and "summary of beta.md" in (beta_entry["summary"] or ""),
              str(beta_entry))

        crit = _passes.summaries_ready(db, manuscript, "gamma.md")
        draft = _passes.summaries_ready(db, manuscript, "gamma.md",
                                        placement="alpha.md")
        keys = ("ok", "missing", "stale", "deprecated", "inflight")
        check("S2 — GATE PARITY: the drafting call shape (placement=…) and "
              "the critique call shape return the identical verdict on the "
              "same state. Both gates call this one predicate; two gates "
              "disagreeing about one row is how this system tells lies",
              {k: crit[k] for k in keys} == {k: draft[k] for k in keys}
              and crit["ok"] and crit["inflight"] == ["beta.md"],
              str(({k: crit[k] for k in keys}, {k: draft[k] for k in keys})))

        ctx = sums.drafting_context(db, manuscript, "gamma.md")
        check("S7 — the drafting context opens with the in-flight header, "
              "naming the essay and saying plainly, in the author's own "
              "words, that this is slightly suboptimal",
              "1 essay in this context is being written right now" in ctx
              and "beta.md — mid-rewrite" in ctx
              and "slightly suboptimal" in ctx
              and "will not be in their context either" in ctx, ctx)
        beta_block = ctx.split("[beta.md] (rewriting)", 1)[1]
        check("S7 — and the entry carries its OWN !! label, so a reader who "
              "scrolls past the header still cannot mistake a pre-rewrite "
              "summary for a current one",
              "!! MID-REWRITE" in beta_block
              and "PRE-REWRITE essay" in beta_block, ctx)

        note = sums._coverage_note(beta_entry, ctx_texts["beta.md"])
        check("S9 — coverage for a 'rewriting' entry is measured against "
              "the PINNED paragraphs (4), not the placeholder's (2): "
              "against the placeholder it would report a fiction",
              note is not None and "¶1, ¶2, ¶3, ¶4 uncited" in note,
              str(note))
        check("S9 — and that is the note the rendered context carries",
              "¶1, ¶2, ¶3, ¶4 uncited" in beta_block, ctx)

        EchoSummarizer.calls.clear()
        sums.rebuild_one(db, manuscript, "beta.md", llm)
        prompt_user = EchoSummarizer.calls[-1]["user"]
        body = prompt_user.split("THE UNIT: beta.md\n\n", 1)[1]
        numbered, _npar = sums._numbered_paragraphs(BETA_TEXT)
        check("S5 — a rebuild WHILE the file is in flight summarizes the "
              "pinned text pulled from the database, never the placeholder "
              "on disk. The refusal prints this remedy, so it has to work "
              "— getting it wrong would store a confident summary of "
              "nothing and mark it fresh",
              body == numbered and _api.MARKER not in prompt_user, body)
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("S5 — and afterwards the entry settles at 'rewriting', NOT "
              "'fresh': it may not pretend to describe the file on disk",
              states["beta.md"] == "rewriting", str(states))
        check("S5 — it is idempotent: the same rebuild lands on the same "
              "state",
              sums.rebuild_one(db, manuscript, "beta.md", llm) is not None
              and {r["file"]: r["state"]
                   for r in sums.status(db, manuscript)}["beta.md"]
              == "rewriting")

        db.conn.execute("DELETE FROM essay_summaries WHERE manuscript_id = ? "
                        "AND file = 'beta.md'", (manuscript["id"],))
        db.conn.commit()
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("S3 — in flight but with NO stored summary is still 'missing': "
              "nothing corresponds to the pinned text, so there is nothing "
              "honest to serve",
              states["beta.md"] == "missing", str(states))
        crit = _passes.summaries_ready(db, manuscript, "gamma.md")
        draft = _passes.summaries_ready(db, manuscript, "gamma.md",
                                        placement="alpha.md")
        check("S3 — both gates still refuse it, identically",
              not crit["ok"] and not draft["ok"]
              and crit["missing"] == draft["missing"] == ["beta.md"]
              and crit["inflight_refused"] == ["beta.md"],
              str((crit["missing"], draft["missing"])))
        msg = _passes.summaries_message(crit, "edit pass")
        check("S3 — and the refusal says what the rebuild will actually "
              "read, so its own remedy does not read as 'summarize the "
              "placeholder'",
              "beta.md is mid-rewrite" in msg
              and "PRE-REWRITE text from the writeup's pinned version" in msg
              and "not the placeholder on disk" in msg, msg)
        sums.rebuild_one(db, manuscript, "beta.md", llm)

        with contextlib.redirect_stdout(io.StringIO()):
            api.write_abandon(db, manuscript, config, prefix=beta_wu[:8])
        check("S4 fixture — abandon put the pinned text back byte for byte",
              (ms / "beta.md").read_text() == BETA_TEXT,
              repr((ms / "beta.md").read_text()))
        BETA_EDITED = BETA_TEXT + "\nA fourth paragraph, typed before the "\
                                  "writeup opened.\n"
        (ms / "beta.md").write_text(BETA_EDITED)
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
            api.write_start(db, manuscript, config, "beta.md",
                            intent["id"][:8])
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        ready = _passes.summaries_ready(db, manuscript, "gamma.md")
        check("S4 — in flight, but the stored summary matches neither disk "
              "nor the pinned text: 'stale', and still refused by both "
              "gates. In flight is not a licence to serve a lie",
              states["beta.md"] == "stale" and not ready["ok"]
              and ready["stale"] == ["beta.md"]
              and "beta.md is mid-rewrite" in _passes.summaries_message(
                  ready, "drafting pass"), str(states))
        sums.rebuild_one(db, manuscript, "beta.md", llm)
        check("S4 — and the printed remedy clears it, landing on "
              "'rewriting'",
              {r["file"]: r["state"]
               for r in sums.status(db, manuscript)}["beta.md"] == "rewriting")

        with contextlib.redirect_stdout(io.StringIO()):
            api.write_start(db, manuscript, config, "epsilon.md",
                            intent["id"][:8], after="alpha.md",
                            brief="A new essay, written from nothing at all.",
                            new=True, style="House")
        check("S8 fixture — --new creates the file EMPTY: the placeholder "
              "marks a TRUNCATION, and --new truncates nothing",
              (ms / "epsilon.md").exists()
              and (ms / "epsilon.md").read_text() == "")
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("S8 — a file a writeup is writing for the FIRST time reads "
              "'unwritten'",
              states["epsilon.md"] == "unwritten", str(states))
        ready = _passes.summaries_ready(db, manuscript, "gamma.md")
        check("S8 — and it no longer blocks every other writeup and every "
              "critique run (regression: it used to read 'missing' and "
              "refuse until the new essay was finished)",
              ready["ok"] and "epsilon.md" not in ready["missing"],
              str(ready))
        ctx = sums.drafting_context(db, manuscript, "gamma.md")
        eps_block = ctx.split("[epsilon.md] (unwritten)", 1)[1]
        check("S8 — its entry says there is no pre-rewrite text, NOT 'run "
              "summarize rebuild' — a rebuild cannot help here, and saying "
              "so would be a lie",
              "BEING WRITTEN FOR THE FIRST TIME" in eps_block
              and "no pre-rewrite text" in eps_block
              and "(no summary — run 'summarize rebuild')" not in eps_block,
              ctx)

        EchoSummarizer.calls.clear()
        result = sums.rebuild(db, manuscript, llm, only_missing_or_stale=True)
        check("S6 — the full rebuild summarizes the in-flight REWRITE from "
              "its pinned text and SKIPS the one that has none, reporting "
              "it instead of quietly summarizing an empty file",
              "beta.md" in result["built"]
              and result["skipped_inflight"] == ["epsilon.md"]
              and "epsilon.md" not in result["built"], str(result))
        check("S6 — no summarizer call was ever made for it",
              all(c["unit"] != "epsilon.md" for c in EchoSummarizer.calls),
              str([c["unit"] for c in EchoSummarizer.calls]))
        try:
            sums.rebuild_one(db, manuscript, "epsilon.md", llm)
            raised = None
        except LookupError as err:
            raised = str(err)
        check("S6 — and the SINGULAR verb refuses it too: summarizing "
              "nothing must not be reachable by either route",
              raised is not None and "epsilon.md" in raised
              and "no pre-rewrite text to summarize" in raised, str(raised))

        (ms / "zeta.md").write_text("# zeta\n\nText of zeta.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
            sums.rebuild(db, manuscript, llm, only_missing_or_stale=True)
        zeta_summary = sums.all_summaries(
            db, manuscript["id"])["zeta.md"]["summary"]
        (ms / "zeta.md").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
        check("S11 fixture — the departed file's row is deprecated, not "
              "deleted",
              db.one("SELECT status FROM essay_summaries WHERE "
                     "manuscript_id = ? AND file = 'zeta.md'",
                     (manuscript["id"],))["status"] == "deprecated")
        with contextlib.redirect_stdout(io.StringIO()):
            api.write_start(db, manuscript, config, "zeta.md",
                            intent["id"][:8], after="alpha.md",
                            brief="A different essay that reuses the name.",
                            new=True, style="House")
        states = {r["file"]: r["state"] for r in sums.status(db, manuscript)}
        check("S11 — a recreated filename carrying a DEPRECATED row reads "
              "'unwritten': unwritten outranks deprecated, because that row "
              "summarizes a different essay that used to have this name",
              states["zeta.md"] == "unwritten", str(states))
        _b, aft = sums.before_after(db, manuscript, "alpha.md")
        zeta_entry = {e["file"]: e for e in aft}["zeta.md"]
        check("S11 — and that old summary is NOT served: serving it would "
              "be the exact lie 'deprecated' exists to prevent",
              zeta_entry["summary"] is None
              and zeta_summary not in sums.drafting_context(
                  db, manuscript, "alpha.md"), str(zeta_entry))
        rows = {r["file"]: r for r in sums.status(db, manuscript)}
        check("F11 — and status() reports NO word count for it either. A "
              "count is a claim that a summary is being offered for this "
              "essay; zeta.md's row is a real, non-empty summary of the "
              "essay that used to have this name, so borrowing its length "
              "would put a number beside an entry that serves nothing",
              rows["zeta.md"]["state"] == "unwritten"
              and rows["zeta.md"]["words"] == 0
              and len(zeta_summary.split()) > 0,
              str((rows["zeta.md"], zeta_summary)))
        check("F11 — while a real entry still reports its length, so the "
              "guard did not simply zero the column",
              rows["alpha.md"]["words"] > 0, str(rows["alpha.md"]))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "summarize", "status"])
        check("the CLI renders both new states (a new state must not "
              "KeyError the colour table)",
              "rewriting" in buf.getvalue() and "unwritten" in buf.getvalue(),
              buf.getvalue())

        print("parallel safety — ONE capture per invocation (sponsor "
              "addendum, after a live incident):")
        # The sponsor's ask, verbatim: "load up all the stale essays into
        # memory at once, so if some other session messes with one in
        # parallel, it is less likely to cause a problem like it did this
        # time." The property already holds; these pin it, because it is
        # invisible in the code and one refactor away from being lost.

        # P1 — the rebuild snapshot. Mutate a LATER essay on disk while
        # the pass is running; it must be summarized from the text the
        # pass captured before its first model call, not from the bytes
        # that landed underneath it mid-run.
        api.attach_style(db, manuscript, "gamma.md", "House")
        gamma_before = (ms / "gamma.md").read_text()
        EchoSummarizer.calls.clear()
        mutated = ["# gamma\n\nSomething another session wrote mid-rebuild.\n"]

        def mutate_once(f):
            if mutated:
                (ms / "gamma.md").write_text(mutated.pop())

        sums.rebuild(db, manuscript, llm, progress=mutate_once)
        gamma_row = sums.all_summaries(db, manuscript["id"])["gamma.md"]
        gamma_prompt = next(c["user"] for c in EchoSummarizer.calls
                            if c["unit"] == "gamma.md")
        check("P1 — a full rebuild summarizes the SNAPSHOT text it took "
              "before its first call, not the bytes another session wrote "
              "underneath it mid-run (a rebuild runs for minutes; a "
              "per-unit re-read would summarize a truncation as an empty "
              "essay and store it as fresh)",
              "Something another session wrote" not in gamma_prompt
              and "Text of gamma." in gamma_prompt, gamma_prompt)
        check("P1 — and the stored source_hash is the hash of THAT text, "
              "so the entry honestly reads 'stale' against the changed "
              "disk rather than 'fresh' against a text it never read",
              gamma_row["source_hash"] == sums._hash(gamma_before)
              and {r["file"]: r["state"]
                   for r in sums.status(db, manuscript)}["gamma.md"]
              == "stale",
              str((gamma_row["source_hash"], sums._hash(gamma_before))))
        (ms / "gamma.md").write_text(gamma_before)

        # P4 — the sponsor's actual incident: the later essay is not
        # merely edited mid-run, it is TRUNCATED by a parallel
        # `write start`. The snapshot holds its pre-truncation text, which
        # is exactly the text that writeup pinned — so the hash stored by
        # the rebuild is the hash the in-flight logic looks for.
        gamma_intent = api.declare_intent(
            db, manuscript, "Rewrite gamma in another session")["intent"]
        started_mid = []

        def start_writeup_once(f):
            if f == "beta.md" and not started_mid:
                started_mid.append(True)
                with contextlib.redirect_stdout(io.StringIO()):
                    api.write_start(db, manuscript, config, "gamma.md",
                                    gamma_intent["id"][:8])

        EchoSummarizer.calls.clear()
        sums.rebuild(db, manuscript, llm, progress=start_writeup_once)
        check("P4 — a parallel `write start` that truncates a later essay "
              "MID-RUN does not corrupt its summary: the snapshot's "
              "pre-truncation text is what gets summarized, and the "
              "placeholder never reaches the summarizer",
              started_mid
              and _api.MARKER not in next(
                  c["user"] for c in EchoSummarizer.calls
                  if c["unit"] == "gamma.md"),
              str([c["unit"] for c in EchoSummarizer.calls]))
        check("P4 — and the hash the rebuild stored is the hash the "
              "in-flight logic looks for, so the entry lands on "
              "'rewriting' rather than 'stale': the snapshot text and the "
              "writeup's pinned text are the same text",
              {r["file"]: r["state"]
               for r in sums.status(db, manuscript)}["gamma.md"]
              == "rewriting",
              str(sums.status(db, manuscript)))

        # P2 / P3 — one capture per invocation, counted. Invisible in the
        # code, so it is pinned here: rebuild_one used to read the disk
        # twice (once for the order, once for the texts), and write_start
        # used to read it once for the gate and again for the context it
        # printed — a window in which a parallel session could change an
        # essay AFTER the gate approved it and BEFORE it was served.
        def counting(target_module, name, shared=None):
            real = getattr(target_module, name)
            calls = shared if shared is not None else []

            def wrapper(*args, **kwargs):
                calls.append(name)
                return real(*args, **kwargs)

            setattr(target_module, name, wrapper)
            return calls, lambda: setattr(target_module, name, real)

        # Both ways this module can read the reading order and its texts.
        # (`_concept_slice`'s own read, inside summarize_unit, goes
        # through neither and is not what is being counted.)
        order_reads: list = []
        _, restore_units = counting(sums, "units", order_reads)
        _, restore_ctx = counting(sums, "_context_units", order_reads)
        try:
            sums.rebuild_one(db, manuscript, "alpha.md", llm)
        finally:
            restore_units()
            restore_ctx()
        check("P2 — rebuild_one captures the reading order and its texts "
              "ONCE. It used to call units() twice, once for the order "
              "and once for the texts: two disk passes a parallel writer "
              "can interleave, and two chances for the order and the "
              "texts to disagree about the same manuscript",
              order_reads == ["_context_units"], str(order_reads))

        api.attach_style(db, manuscript, "alpha.md", "House")
        captures, restore = counting(sums, "_context_units", [])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                api.write_start(db, manuscript, config, "alpha.md",
                                intent["id"][:8])
        finally:
            restore()
        check("P3 — `write start` takes exactly ONE capture of the "
              "manuscript's context text, shared by the freshness gate "
              "and by the drafting context it prints. Two reads would let "
              "a parallel session truncate a neighbour between the gate "
              "approving it and the author being handed it as approved "
              "context — the exact lie the gate exists to prevent",
              len(captures) == 1, f"{len(captures)} captures")

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
        # The REAL list, not a hardcoded five of it. A new LLM verb added
        # to cli.LLM_VERBS was silently unchecked here, and the closing
        # `check(..., True)` asserted nothing at all — it counted a pass
        # whatever the loop above had found.
        from authorlm.cli import LLM_VERBS

        missing = []
        for verb in sorted(LLM_VERBS):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
                cli_main([verb, "--help"])
            if "authorlm prompts" not in buf.getvalue():
                missing.append(verb)
        check("every LLM-using verb's --help points at the prompt "
              "registry — every verb in cli.LLM_VERBS, not a sample",
              not missing, f"missing the note: {missing}")
    finally:
        server.shutdown()
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    _assert_offline()
    main_test()
