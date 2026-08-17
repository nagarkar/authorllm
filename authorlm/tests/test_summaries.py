"""Essay summaries (critique-pass design §4): reading order, autoregressive
conditioning, mark-don't-cascade staleness, rebuild semantics.

Run: python3 tests/test_summaries.py
"""

from __future__ import annotations

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
                                     "model": body.get("model")})
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
        check("all fresh after a full build",
              all(r["state"] == "fresh" for r in sums.status(db, manuscript)))

        print("before/after context:")
        before, after = sums.before_after(db, manuscript, "alpha.md")
        check("before/after split around the target unit",
              [b["file"] for b in before] == ["title.md", "part1.md"]
              and [a["file"] for a in after] == ["beta.md", "gamma.md"]
              and all(b["state"] == "fresh" for b in before + after))

        print("mark, don't cascade:")
        (ms / "alpha.md").write_text("# alpha\n\nText of alpha, revised.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, config, source="test")
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
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["--workspace", str(ws), "summarize", "rebuild", "gamma.md"])
        check("summarize rebuild <file> reports the one-unit rebuild",
              "rebuilt gamma.md" in buf.getvalue())

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
            cli_main(["prompts"])
        listing = buf.getvalue()
        check("'authorlm prompts' lists every prompt with its location",
              all(p.name in listing and p.location in listing
                  for p in pr.REGISTRY))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_main(["prompts", "show", "summarizer"])
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
    main_test()
