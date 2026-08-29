"""API-layer tests + CLI/MCP parity checklist (P11).

Exercises `authorlm.api` directly (the logic layer beneath every surface)
and asserts the MCP server exposes the expected hand-curated tool set.

Run: python3 tests/test_api.py
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
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, concepts, guidance as guidance_module  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.db import ko_fields, loads  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def check_show_verbs() -> None:
    """Every entity verb exposes `show`, spelled the same way.

    Four separate sessions hit a missing `show` on a different noun before
    this was made uniform, so the check is on the parser rather than on
    memory."""
    from authorlm.cli import build_parser

    commands = build_parser()._subparsers._group_actions[0].choices
    for verb in ("concept", "intent", "improve", "style", "belief",
                 "proposal", "critique", "lens", "illus", "doc"):
        actions = [a for a in commands[verb]._actions if a.dest == "action"]
        choices = list(actions[0].choices) if actions else []
        check(f"`{verb}` exposes a 'show' action",
              "show" in choices, f"{verb} actions: {choices}")


def check_broken_pipe() -> None:
    """`authorlm <listing> | head` must exit quietly.

    Python raises BrokenPipeError when the reader closes early, and raises a
    SECOND time flushing stdout at interpreter shutdown — which is what
    prints 'Exception ignored in: <_io.TextIOWrapper>' after the traceback.
    Both have to be swallowed, and the handler must survive a captured
    stdout that has no fileno()."""
    import io as _io

    from authorlm.cli import main as _main

    class ExplodesOnWrite(_io.StringIO):
        def write(self, text):  # noqa: D102
            raise BrokenPipeError(32, "Broken pipe")

    class ExplodesOnFlush(_io.StringIO):
        """Short output stays buffered, so the pipe only breaks when the
        interpreter flushes at shutdown — the case a try/except around the
        command misses entirely."""

        def flush(self):  # noqa: D102
            raise BrokenPipeError(32, "Broken pipe")

    for label, stream in (("while writing", ExplodesOnWrite),
                          ("at the shutdown flush", ExplodesOnFlush)):
        try:
            with contextlib.redirect_stdout(stream()):
                _main(["--help"])
            check(f"a pipe closed {label} exits without raising", True)
        except SystemExit as err:
            check(f"a pipe closed {label} exits 0, not a traceback",
                  err.code in (0, None), f"exit code {err.code}")
        except BrokenPipeError:
            check(f"a pipe closed {label} exits 0, not a traceback", False,
                  "BrokenPipeError escaped main()")


def check_config_parity() -> None:
    """ORCH-3: api.load_config and cli._load_config must resolve the
    same config.

    Before the fix, api.load_config(workspace) read
    <workspace-or-home>/.authorlm/config.toml while cli._load_config
    already read paths.config_path() (AUTHORLM_CONFIG override, else the
    project's config.toml) plus paths.load_env(). The two diverged: the
    CLI had the LLM on and the MCP server (which calls api.load_config)
    had it silently off. Pin AUTHORLM_CONFIG at a throwaway config with
    the LLM enabled and prove api.load_config sees it regardless of the
    `workspace` argument passed in — that's what proves it no longer
    keys off workspace/home."""
    import argparse

    from authorlm import cli as cli_module

    prev_config = os.environ.get("AUTHORLM_CONFIG")
    prev_env = os.environ.get("AUTHORLM_ENV")
    cfg_root = Path(tempfile.mkdtemp(prefix="authorlm-config-parity-"))
    try:
        pinned_config = cfg_root / "config.toml"
        pinned_config.write_text(
            '[llm]\nenabled = true\nmodel = "gemini/gemini-2.5-flash"\n'
        )
        pinned_env = cfg_root / ".env"
        pinned_env.write_text("")
        os.environ["AUTHORLM_CONFIG"] = str(pinned_config)
        os.environ["AUTHORLM_ENV"] = str(pinned_env)

        # An unrelated workspace, far from the pinned config directory —
        # neither this workspace nor $HOME holds a config.toml.
        unrelated_ws = cfg_root / "unrelated-workspace"
        unrelated_ws.mkdir()
        args = argparse.Namespace(workspace=str(unrelated_ws))

        from_api = api.load_config(str(unrelated_ws))
        from_cli = cli_module._load_config(args)

        check("api.load_config and cli._load_config resolve the same config",
              from_api == from_cli, f"api={from_api!r} cli={from_cli!r}")
        check("api.load_config picks up the AUTHORLM_CONFIG pin, not "
              "workspace/home",
              from_api.get("llm", {}).get("enabled") is True, str(from_api))
    finally:
        if prev_config is None:
            os.environ.pop("AUTHORLM_CONFIG", None)
        else:
            os.environ["AUTHORLM_CONFIG"] = prev_config
        if prev_env is None:
            os.environ.pop("AUTHORLM_ENV", None)
        else:
            os.environ["AUTHORLM_ENV"] = prev_env
        shutil.rmtree(cfg_root, ignore_errors=True)


def check_extraction_failure_traced() -> None:
    """OPS-2: a raising run_extraction must leave a trace, not just a
    silently-empty result.

    `collect(..., analyze=True)` and `write_complete` both swallow
    run_extraction's exceptions with a bare `except Exception:` — by
    design, extraction failure must never block observation — but before
    this fix nothing recorded that it happened: `collect`/`write_complete`
    returned the same shape as a genuinely empty extraction pass, so a
    silently-broken LLM pipeline was indistinguishable from a quiet day.
    This does not change control flow (still swallowed, still returns
    `{}`/`None`) — it only asserts a `trace.jsonl` record now exists."""
    import io
    import json as _tjson

    from authorlm import api as _api
    from authorlm import tracelog

    root = Path(tempfile.mkdtemp(prefix="authorlm-ops2-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-draft.md").write_text("# Draft\n\nOriginal text.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = _api.open_db(str(ws))
        manuscript = _api.get_manuscript(db)
        config = {"llm": {"enabled": True, "model": "gemini/gemini-2.5-flash"}}
        trace_path = tracelog.log_dir(str(ws)) / "trace.jsonl"

        def boom(*a, **kw):
            raise RuntimeError("stub extraction failure")

        # --- collect(..., analyze=True): api.py's first swallow site ---
        prev_run_extraction = _api.run_extraction
        _api.run_extraction = boom
        try:
            (ms / "01-draft.md").write_text("# Draft\n\nChanged text.\n")
            report = _api.collect(db, manuscript, config, analyze=True)
        finally:
            _api.run_extraction = prev_run_extraction
        check("collect() with a raising run_extraction still returns "
              "(swallow behaviour unchanged)",
              "auto_analysis" not in report, report)
        entries = [_tjson.loads(line)
                  for line in trace_path.read_text().splitlines()]
        collect_entry = entries[-1]
        check("collect()'s swallowed extraction failure is traced",
              collect_entry["verb"] == "extraction"
              and collect_entry["surface"] == "api"
              and collect_entry["manuscript"] == "book"
              and collect_entry["ok"] is False
              and "stub extraction failure" in collect_entry["error"],
              collect_entry)

        # --- write_complete: api.py's second swallow site ---
        _api.define_style_guide(db, manuscript, "house")
        _api.attach_style(db, manuscript, "01-draft.md", "house")
        declared = _api.declare_intent(db, manuscript, "Finish the draft")
        intent_id = declared["intent"]["id"]
        _api.write_start(db, manuscript, config, "01-draft.md", intent_id[:8])

        _api.run_extraction = boom
        try:
            result = _api.write_complete(db, manuscript, config)
        finally:
            _api.run_extraction = prev_run_extraction
        check("write_complete() with a raising run_extraction still "
              "returns (swallow behaviour unchanged)",
              result["extraction"] is None, result)
        entries = [_tjson.loads(line)
                  for line in trace_path.read_text().splitlines()]
        complete_entry = entries[-1]
        check("write_complete()'s swallowed extraction failure is traced",
              complete_entry["verb"] == "extraction"
              and complete_entry["surface"] == "api"
              and complete_entry["manuscript"] == "book"
              and complete_entry["ok"] is False
              and "stub extraction failure" in complete_entry["error"],
              complete_entry)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_extraction_prompt_provenance() -> None:
    """The author asked: when extraction (or its adjudication pass) runs,
    say which prompt files shaped it, so they can go read them. The list
    must be derived from prompt_registry.REGISTRY (never a hardcoded
    filename), must reflect the run that actually happened — adjudication
    appears whenever the call was actually made (opted in, not an
    aliases-only pass), including a call that ran but came back with
    nothing usable — and CLI and MCP must report the identical set, since
    both surfaces just forward the same `prompt_files` field returned by
    `extraction.extract_concepts`."""
    import io

    from authorlm import api as _api
    from authorlm import cli as cli_module
    from authorlm import extraction
    from authorlm import mcp_server
    from authorlm import prompt_registry as pr

    repo_root = Path(__file__).resolve().parent.parent

    def _on_disk(location: str) -> bool:
        # module-backed locations carry a ":SYMBOL" suffix after the real
        # file path (prompt_registry.Prompt.location) — strip it before
        # checking existence.
        return (repo_root / location.split(":", 1)[0]).is_file()

    check("every registered prompt's location resolves to a real file "
          "on disk",
          all(_on_disk(p.location) for p in pr.REGISTRY),
          str([p.location for p in pr.REGISTRY if not _on_disk(p.location)]))

    extraction_loc = pr.by_name("extraction").location
    adjudication_loc = pr.by_name("adjudication").location
    edges_loc = pr.by_name("extraction-edges").location
    aliases_loc = pr.by_name("extraction-aliases").location

    root = Path(tempfile.mkdtemp(prefix="authorlm-promptprov-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "00-seed.md").write_text("# Seed\n\nNothing to see here.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = _api.open_db(str(ws))
        manuscript = _api.get_manuscript(db, "book")

        def two_context_paragraphs(name: str) -> str:
            return (f"# {name}\n\n" + (f"{name} appears here. " * 4)
                    + f"\n\n## Later\n\n" + (f"{name} returns here. " * 4)
                    + "\n")

        class ExtractOnlyLLM:
            """No `.config` attribute — adjudication.enabled() reads
            `getattr(llm, 'config', None) or {}`, so this stays off."""
            enabled = True
            extraction_max_chars = 24000

            def __init__(self, concept_name: str):
                self.concept_name = concept_name

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": self.concept_name, "kind": "concept",
                     "notes": "a definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        class AdjudicatingLLM:
            """Opts into adjudication and answers both LLM calls the pass
            makes: extraction (plain payload) then adjudication (payload
            starts with 'CANDIDATE ...', per adjudication._render_candidates)."""
            enabled = True
            extraction_max_chars = 24000

            def __init__(self, concept_name: str):
                self.config = {"extraction": {"adjudicate": True}}
                self.concept_name = concept_name
                self.calls = 0

            def complete_json(self, system, user, thinking_budget=None):
                self.calls += 1
                if user.startswith("CANDIDATE"):
                    return {"concepts": [
                        {"name": self.concept_name, "verdict": "new"}],
                            "links": []}
                return {"concepts": [
                    {"name": self.concept_name, "kind": "concept",
                     "notes": "a definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        # --- (a)+(b): adjudication OFF — only extraction.md is reported,
        # via the CLI surface (_run_extraction's printed output). ---
        (ms / "01-cli-off.md").write_text(two_context_paragraphs("CliOffConcept"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(db, manuscript, ExtractOnlyLLM("CliOffConcept"),
                                       files=["01-cli-off.md"])
        cli_off_out = buf.getvalue()
        check("CLI reports the extraction prompt file after a plain pass",
              f"Prompts: {extraction_loc}" in cli_off_out, cli_off_out)
        check("CLI does NOT report adjudication.md when adjudication "
              "never ran",
              adjudication_loc not in cli_off_out, cli_off_out)

        # --- same scenario via MCP: adjudication OFF. ---
        (ms / "02-mcp-off.md").write_text(two_context_paragraphs("McpOffConcept"))
        prev_workspace = mcp_server._WORKSPACE
        prev_llm = mcp_server._llm
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_server._llm = lambda: ExtractOnlyLLM("McpOffConcept")
            mcp_off = mcp_server.extract_concepts(
                files=["02-mcp-off.md"], manuscript="book")
        finally:
            mcp_server._WORKSPACE = prev_workspace
            mcp_server._llm = prev_llm
        check("MCP extract_concepts carries prompt_files as structured "
              "data, matching the CLI's set when adjudication is off",
              mcp_off["ok"] is True
              and mcp_off["result"]["prompt_files"] == [extraction_loc],
              str(mcp_off))

        # --- (b): adjudication ON, via CLI — both files listed. ---
        (ms / "03-cli-on.md").write_text(two_context_paragraphs("CliOnConcept"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(db, manuscript, AdjudicatingLLM("CliOnConcept"),
                                       files=["03-cli-on.md"])
        cli_on_out = buf.getvalue()
        check("CLI reports BOTH extraction.md and adjudication.md when "
              "adjudication actually ran",
              f"Prompts: " in cli_on_out
              and extraction_loc in cli_on_out
              and adjudication_loc in cli_on_out,
              cli_on_out)

        # --- same scenario via MCP: adjudication ON — identical set. ---
        (ms / "04-mcp-on.md").write_text(two_context_paragraphs("McpOnConcept"))
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_server._llm = lambda: AdjudicatingLLM("McpOnConcept")
            mcp_on = mcp_server.extract_concepts(
                files=["04-mcp-on.md"], manuscript="book")
        finally:
            mcp_server._WORKSPACE = prev_workspace
            mcp_server._llm = prev_llm
        check("MCP reports the same prompt set as the CLI when "
              "adjudication ran (deterministic: reflects the actual run)",
              mcp_on["ok"] is True
              and set(mcp_on["result"]["prompt_files"])
              == {extraction_loc, adjudication_loc},
              str(mcp_on))

        # --- CORRECTION (it-b6fd1a7e5ea0 follow-up): adjudication that
        # RAN but came back with nothing usable (malformed reply here;
        # zero candidates or a model failure hit the same `verdicts is
        # None` path inside adjudication.adjudicate()) must still report
        # adjudication.md. The Sponsor's own trade: "as long as the
        # adjudicator prompts are visible every time it runs, I'll be
        # able to figure out if there is something wrong with the
        # prompt" — every time it RUNS, not every time it succeeds. ---
        class AdjudicatingButEmptyLLM:
            """Opts into adjudication; the adjudication call itself
            returns a non-dict reply, so adjudication.adjudicate() comes
            back with verdicts=None — exercising the 'ran but empty'
            path, distinct from 'never ran'."""
            enabled = True
            extraction_max_chars = 24000

            def __init__(self, concept_name: str):
                self.config = {"extraction": {"adjudicate": True}}
                self.concept_name = concept_name

            def complete_json(self, system, user, thinking_budget=None):
                if user.startswith("CANDIDATE"):
                    return "not a dict"  # malformed adjudication reply
                return {"concepts": [
                    {"name": self.concept_name, "kind": "concept",
                     "notes": "a definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        (ms / "07-empty.md").write_text(two_context_paragraphs("EmptyAdjConcept"))
        empty_result = extraction.extract_concepts(
            db, manuscript, AdjudicatingButEmptyLLM("EmptyAdjConcept"),
            files=["07-empty.md"])
        check("adjudication.md is reported even when the adjudication "
              "call ran but returned nothing usable",
              empty_result is not None
              and adjudication_loc in empty_result["prompt_files"],
              str(empty_result))
        check("a run where adjudication ran but yielded nothing is "
              "distinctly flagged (adjudication_empty), not silently "
              "indistinguishable from a clean pass",
              empty_result.get("adjudication_empty") is True,
              str(empty_result))

        (ms / "08-flag-off.md").write_text(two_context_paragraphs("FlagOffConcept"))
        flag_off_result = extraction.extract_concepts(
            db, manuscript, ExtractOnlyLLM("FlagOffConcept"),
            files=["08-flag-off.md"])
        check("adjudication_empty stays False when adjudication never "
              "ran at all (flag off) — 'never ran' and 'ran but empty' "
              "must stay distinguishable",
              flag_off_result is not None
              and flag_off_result.get("adjudication_empty") is False
              and adjudication_loc not in flag_off_result["prompt_files"],
              str(flag_off_result))

        # --- same 'ran but empty' scenario via the CLI surface: the
        # prompt still appears on the Prompts line, plus a distinct note. ---
        (ms / "09-cli-empty.md").write_text(two_context_paragraphs("CliEmptyConcept"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(
                db, manuscript, AdjudicatingButEmptyLLM("CliEmptyConcept"),
                files=["09-cli-empty.md"])
        cli_empty_out = buf.getvalue()
        check("CLI still lists adjudication.md when the call ran but "
              "returned nothing usable",
              adjudication_loc in cli_empty_out, cli_empty_out)
        check("CLI surfaces a distinct note for 'ran but empty', so it "
              "reads differently from a clean adjudicated pass",
              "adjudication ran but the model returned nothing usable"
              in cli_empty_out, cli_empty_out)

        # --- edges-only and aliases-only runs report THEIR prompt, not
        # the base 'extraction' one — the flags change which prompt the
        # run actually used. ---
        _api.add_concept(db, manuscript, "EdgeAlpha", kind="concept")
        _api.add_concept(db, manuscript, "EdgeBeta", kind="concept")
        (ms / "05-edges.md").write_text(
            "# Edges\n\nEdgeAlpha relates closely to EdgeBeta in this text.\n")

        class EdgesLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [
                    {"from": "EdgeAlpha", "relation": "depends_on",
                     "to": "EdgeBeta"}]}

            def stats_line(self):
                return None

        edges_result = extraction.extract_concepts(
            db, manuscript, EdgesLLM(), files=["05-edges.md"], edges_only=True)
        check("an --edges run reports extraction-edges' prompt, not the "
              "base extraction prompt",
              edges_result is not None
              and edges_result["prompt_files"] == [edges_loc],
              str(edges_result))

        _api.add_concept(db, manuscript, "AliasGamma", kind="concept")
        _api.add_concept(db, manuscript, "AliasGammaAlt", kind="concept")
        sentence = "AliasGammaAlt is another name for AliasGamma in this text."
        (ms / "06-aliases.md").write_text(f"# Aliases\n\n{sentence}\n")

        class AliasesLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "AliasGammaAlt", "canonical": "AliasGamma",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        aliases_result = extraction.extract_concepts(
            db, manuscript, AliasesLLM(), files=["06-aliases.md"],
            aliases_only=True)
        check("an --aliases run reports extraction-aliases' prompt, not "
              "the base extraction prompt",
              aliases_result is not None
              and aliases_result["prompt_files"] == [aliases_loc],
              str(aliases_result))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_extraction_skip_reasons() -> None:
    """it-b6fd1a7e5ea0: extraction lumped five unrelated skip causes into
    one `skipped` counter and one CLI word — "malformed" — which was
    simply false for two of them: an unknown relation (VALID_RELATIONS)
    and an unknown endpoint (not yet a known concept) are the extractor's
    own deliberate policy, not a bad model reply. This proves the split:
    each cause lands in its own bucket, the CLI never calls a policy
    rejection "malformed", and the three buckets always reconstitute the
    OLD single total exactly for the same input — reporting changed,
    skip BEHAVIOR did not."""
    import io

    from authorlm import api as _api
    from authorlm import cli as cli_module
    from authorlm import extraction

    root = Path(tempfile.mkdtemp(prefix="authorlm-skipreasons-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "00-seed.md").write_text("# Seed\n\nNothing to see here.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms)])
        db = _api.open_db(str(ws))
        manuscript = _api.get_manuscript(db, "book")

        _api.add_concept(db, manuscript, "KnownAlpha", kind="concept")
        _api.add_concept(db, manuscript, "KnownBeta", kind="concept")
        (ms / "01-skips.md").write_text(
            "# Skips\n\nKnownAlpha relates to KnownBeta in this passage. "
            "KnownAlpha also touches Ghost here.\n")

        class SkipMixLLM:
            """One payload exercising all five original skip sites: two
            malformed concepts, two malformed links, one unknown-relation
            link, one unknown-endpoint link, two malformed aliases."""
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {
                    "concepts": [
                        "not-a-dict",           # malformed: not a dict
                        {"kind": "concept"},    # malformed: no name
                    ],
                    "links": [
                        "not-a-dict",           # malformed: not a dict
                        {"from": "KnownAlpha", "relation": "depends_on",
                         "to": "KnownAlpha"},   # malformed: src == dst
                        {"from": "KnownAlpha", "relation": "not-a-real-relation",
                         "to": "KnownBeta"},    # unknown relation (policy)
                        {"from": "KnownAlpha", "relation": "depends_on",
                         "to": "Ghost"},        # unknown endpoint (Ghost unknown)
                    ],
                    "aliases": [
                        "not-a-dict",           # malformed: not a dict
                        {"alias": "", "canonical": "", "sentence": ""},
                    ],
                }

            def stats_line(self):
                return None

        result = extraction.extract_concepts(
            db, manuscript, SkipMixLLM(), files=["01-skips.md"])

        check("an unknown-relation item lands in skipped_unknown_relation, "
              "NOT skipped_malformed",
              result is not None and result["skipped_unknown_relation"] == 1,
              str(result))
        check("an unknown-endpoint item lands in skipped_unknown_endpoint, "
              "NOT skipped_malformed",
              result["skipped_unknown_endpoint"] == 1, str(result))
        check("a non-dict item (concept/link/alias) lands in "
              "skipped_malformed",
              result["skipped_malformed"] == 6, str(result))
        check("the three buckets sum EXACTLY to the old single 'skipped' "
              "total for the same input — reporting split, skip BEHAVIOR "
              "unchanged",
              result["skipped_malformed"] + result["skipped_unknown_endpoint"]
              + result["skipped_unknown_relation"] == result["skipped"]
              and result["skipped"] == 8,
              str(result))

        # --- CLI wording: only the malformed bucket may say "malformed";
        # the other two are named for what they actually are. ---
        (ms / "02-skips.md").write_text(
            "# Skips2\n\nKnownAlpha relates to KnownBeta in this passage too. "
            "KnownAlpha also touches Ghost2 here.\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_module._run_extraction(db, manuscript, SkipMixLLM(),
                                       files=["02-skips.md"])
        cli_out = buf.getvalue()
        check("CLI reports the malformed count under the word 'malformed'",
              "skipped 6 malformed item(s)" in cli_out, cli_out)
        check("CLI names the unknown-relation skips honestly, not as "
              "'malformed'",
              "1 relationship(s) named an unrecognized relation" in cli_out,
              cli_out)
        check("CLI names the unknown-endpoint skips honestly, not as "
              "'malformed'",
              "1 relationship(s) named an unknown concept" in cli_out,
              cli_out)

        # --- MCP carries the same three buckets as structured data,
        # resolved in the same one place extraction.py already computes
        # them — no separate MCP-side logic to drift. ---
        from authorlm import mcp_server

        (ms / "03-skips.md").write_text(
            "# Skips3\n\nKnownAlpha relates to KnownBeta in this passage as "
            "well. KnownAlpha also touches Ghost3 here.\n")
        prev_workspace = mcp_server._WORKSPACE
        prev_llm = mcp_server._llm
        mcp_server._WORKSPACE = str(ws)
        try:
            mcp_server._llm = lambda: SkipMixLLM()
            mcp_result = mcp_server.extract_concepts(
                files=["03-skips.md"], manuscript="book")
        finally:
            mcp_server._WORKSPACE = prev_workspace
            mcp_server._llm = prev_llm
        check("MCP extract_concepts carries the split skip buckets as "
              "structured data, matching the CLI/direct-call shape",
              mcp_result["ok"] is True
              and mcp_result["result"]["skipped_malformed"] == 6
              and mcp_result["result"]["skipped_unknown_relation"] == 1
              and mcp_result["result"]["skipped_unknown_endpoint"] == 1,
              str(mcp_result))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_backup_and_restore() -> None:
    """OPS-4: the knowledge store has no backup, no integrity check, no
    verified copy today. This proves the fix actually works, not just
    that it runs:

    - A backup taken via the SQLite backup API genuinely RESTORES: after
      the original `.db`/`-wal`/`-shm` files are deleted outright, the
      restored file is queryable through the application layer and holds
      the exact row written before the backup.
    - Skip-if-unchanged: a second backup with no intervening write is
      skipped, not duplicated.
    - Rotation: the 8th distinct backup evicts the 1st; only the 7 most
      recent ever remain on disk.
    - Failure path: an unwritable backups directory fails loudly
      (ok=False, a stderr warning) but never blocks session start.
    - Latency against a realistic (~56 MB) database is measured and
      printed, not just asserted under some threshold blindly.
    - A corrupted store is caught by the new readiness "database sound"
      item instead of crashing the sweep."""
    import contextlib as _ctx
    import io as _io
    import time as _time

    from authorlm import backup, sessions as bses
    from authorlm.db import ko_fields as _ko_fields

    root = Path(tempfile.mkdtemp(prefix="authorlm-backup-"))
    try:
        # --- setup: a real manuscript + one row of real data ---
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-draft.md").write_text("# Draft\n\nSome text.\n")
        db = api.open_db(str(ws))
        manuscript = api.register_manuscript(db, "book", str(ms))
        concept = _ko_fields("cn")
        concept.update(manuscript_id=manuscript["id"], name="Gravity",
                       kind="concept", status="declared", introduced_in=None,
                       notes="The pull of consequence.", aliases="[]")
        db.insert("concept_nodes", concept)

        # --- session start triggers exactly one backup, via the SQLite
        # backup API against the live connection (WAL-safe) ---
        session = bses.start_session(db, manuscript["id"])
        check("starting a session performs a backup (not skipped: it's "
              "the first one)",
              session["backup"]["ok"] and not session["backup"]["skipped"],
              session["backup"])
        bdir = backup.backup_dir(db.path)
        made = sorted(bdir.glob("authorlm-*.db"))
        check("exactly one backup file exists in <workspace>/.authorlm/"
              "backups/ after session start", len(made) == 1, made)
        backup_path = made[0]

        # --- RESTORE_PROOF: destroy the original (.db + WAL + SHM), then
        # prove the backup alone reconstitutes a working, queryable store ---
        db.conn.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(db.path) + suffix)
            if candidate.exists():
                candidate.unlink()
        check("the original .db/-wal/-shm are gone (real loss, not a "
              "copy-elsewhere)", not db.path.exists())

        shutil.copy2(backup_path, db.path)
        restored = api.open_db(str(ws))
        integrity = restored.all("PRAGMA integrity_check")
        check("the restored file is a sound SQLite database "
              "(PRAGMA integrity_check)",
              len(integrity) == 1 and integrity[0][0] == "ok", integrity)
        restored_manuscript = api.get_manuscript(restored, "book")
        check("the restored store is queryable through the application "
              "layer (api.get_manuscript)",
              restored_manuscript["name"] == "book")
        restored_row = restored.one(
            "SELECT name, notes FROM concept_nodes WHERE manuscript_id = ? "
            "AND name = 'Gravity'", (restored_manuscript["id"],))
        check("the exact row written before the backup survives restore "
              "verbatim",
              restored_row is not None
              and restored_row["notes"] == "The pull of consequence.",
              dict(restored_row) if restored_row else None)
        restored.update("concept_nodes", concept["id"],
                        {"notes": "revised after restore"})
        reread = restored.one("SELECT notes FROM concept_nodes WHERE id = ?",
                              (concept["id"],))
        check("the restored database accepts new writes — it's a live "
              "store, not an inert blob",
              reread["notes"] == "revised after restore")
        restored.conn.close()

        # --- skip-if-unchanged: a second backup with no write between is
        # skipped, not duplicated ---
        skip_ws = root / "ws-skip"
        skip_ms = skip_ws / "manuscript"
        skip_ms.mkdir(parents=True)
        (skip_ms / "01.md").write_text("# hi\n")
        skip_db = api.open_db(str(skip_ws))
        api.register_manuscript(skip_db, "book", str(skip_ms))
        first = backup.perform_backup(skip_db)
        check("the first-ever backup is never skipped",
              first["ok"] and not first["skipped"], first)
        second = backup.perform_backup(skip_db)
        check("a second backup with no intervening write is skipped, so "
              "identical snapshots don't evict useful history",
              second["ok"] and second["skipped"], second)
        skip_bdir = backup.backup_dir(skip_db.path)
        check("the skipped backup left exactly one file behind",
              len(list(skip_bdir.glob("authorlm-*.db"))) == 1)
        skip_node = _ko_fields("cn")
        skip_node.update(manuscript_id="does-not-matter", name="Mutation",
                         kind="concept", status="declared",
                         introduced_in=None, notes="", aliases="[]")
        skip_db.insert("concept_nodes", skip_node)
        third = backup.perform_backup(skip_db)
        check("a real change since the last backup is never skipped",
              third["ok"] and not third["skipped"], third)
        check("the changed-content backup produced a second file",
              len(list(skip_bdir.glob("authorlm-*.db"))) == 2)

        # --- rotation: the 8th distinct backup evicts the 1st; only the
        # 7 most recent are ever kept, and nothing is deleted before the
        # new backup is safely in place ---
        rot_ws = root / "ws-rotate"
        rot_ms = rot_ws / "manuscript"
        rot_ms.mkdir(parents=True)
        (rot_ms / "01.md").write_text("# hi\n")
        rot_db = api.open_db(str(rot_ws))
        api.register_manuscript(rot_db, "book", str(rot_ms))
        made_paths = []
        for i in range(9):
            node = _ko_fields("cn")
            node.update(manuscript_id="rotate", name=f"Concept {i}",
                       kind="concept", status="declared", introduced_in=None,
                       notes=f"distinct payload {i}", aliases="[]")
            rot_db.insert("concept_nodes", node)
            result = backup.perform_backup(rot_db)
            check(f"rotation backup #{i + 1} succeeds and is not skipped "
                  "(content changed each time)",
                  result["ok"] and not result["skipped"], result)
            made_paths.append(Path(result["path"]))
            if i == 6:  # 7 kept so far — 1st is still present, none evicted
                check("before the 8th backup, all 7 made so far are still "
                      "kept (retention isn't over-eager)",
                      made_paths[0].exists(), [p.name for p in made_paths])
            if i == 7:  # the 8th backup just landed
                check("the 8th backup evicts exactly the 1st (oldest)",
                      not made_paths[0].exists()
                      and all(p.exists() for p in made_paths[1:8]),
                      [p.name for p in made_paths])
            _time.sleep(0.002)  # keep the microsecond-resolution names distinct
        rot_bdir = backup.backup_dir(rot_db.path)
        remaining = sorted(rot_bdir.glob("authorlm-*.db"))
        check("rotation keeps exactly the 7 most recent backups",
              len(remaining) == backup.KEEP, remaining)
        check("the 8th backup evicted the 1st (oldest two of nine gone: "
              "8 made it past #7's retention, so #1 and #2 are evicted)",
              not made_paths[0].exists() and not made_paths[1].exists()
              and all(p.exists() for p in made_paths[2:]),
              [p.name for p in made_paths])

        # --- failure path: an unwritable backups directory fails loudly
        # but never blocks the session ---
        fail_ws = root / "ws-fail"
        fail_ms = fail_ws / "manuscript"
        fail_ms.mkdir(parents=True)
        (fail_ms / "01.md").write_text("# hi\n")
        fail_db = api.open_db(str(fail_ws))
        fail_manuscript = api.register_manuscript(fail_db, "book", str(fail_ms))
        data_dir = fail_db.path.parent  # <workspace>/.authorlm
        os.chmod(data_dir, 0o500)  # read+execute only: mkdir("backups") fails
        try:
            stderr_capture = _io.StringIO()
            with _ctx.redirect_stderr(stderr_capture):
                fail_result = backup.run(fail_db)
            check("a backup into an unwritable directory reports failure, "
                  "not success", fail_result["ok"] is False, fail_result)
            check("the failure is printed loudly to stderr — never "
                  "swallowed silently",
                  "AUTHORLM BACKUP FAILED" in stderr_capture.getvalue(),
                  stderr_capture.getvalue())
            fail_session = bses.start_session(fail_db, fail_manuscript["id"])
            check("a failed backup never blocks the author's session from "
                  "starting",
                  fail_session["status"] == "active"
                  and fail_session["backup"]["ok"] is False,
                  fail_session)
        finally:
            os.chmod(data_dir, 0o700)  # restore so cleanup can rmtree it

        # --- latency against a realistic (~56 MB) database ---
        lat_ws = root / "ws-latency"
        lat_ms = lat_ws / "manuscript"
        lat_ms.mkdir(parents=True)
        (lat_ms / "01.md").write_text("# hi\n")
        lat_db = api.open_db(str(lat_ws))
        lat_manuscript = api.register_manuscript(lat_db, "book", str(lat_ms))
        # ~56 MB, matching ~/.authorlm/authorlm.db's real size (RFC OPS-4):
        # 60 rows of ~1 MB of text in manuscript_versions.files.
        blob = "x" * (1024 * 1024)
        with lat_db.transaction():
            for i in range(60):
                version = _ko_fields("mv")
                version.update(manuscript_id=lat_manuscript["id"],
                               version_no=i, checksum=f"c{i}",
                               files=blob, source="synthetic", session_id=None)
                lat_db.insert("manuscript_versions", version)
        lat_db.conn.execute("PRAGMA wal_checkpoint(FULL)")
        db_size_mb = lat_db.path.stat().st_size / (1024 * 1024)
        started = _time.monotonic()
        lat_result = backup.perform_backup(lat_db)
        measured_s = _time.monotonic() - started
        check(f"backing up a {db_size_mb:.1f} MB database succeeds",
              lat_result["ok"] and not lat_result["skipped"], lat_result)
        check("backing up a realistic-size database completes fast enough "
              "not to make the shell feel broken (< 10s, generous bound)",
              measured_s < 10, measured_s)
        print(f"  ok: measured backup latency for {db_size_mb:.1f} MB db "
              f"= {measured_s:.3f}s (internal timer: "
              f"{lat_result['elapsed_s']}s)")

        # --- corruption is caught by readiness's new "database sound"
        # item instead of crashing the sweep ---
        from authorlm import sweeps

        corrupt_ws = root / "ws-corrupt"
        corrupt_ms = corrupt_ws / "manuscript"
        corrupt_ms.mkdir(parents=True)
        (corrupt_ms / "01.md").write_text("# hi\n")
        corrupt_db = api.open_db(str(corrupt_ws))
        corrupt_manuscript = api.register_manuscript(
            corrupt_db, "book", str(corrupt_ms))
        for i in range(3000):
            node = _ko_fields("cn")
            node.update(manuscript_id=corrupt_manuscript["id"],
                       name=f"Concept {i}", kind="concept",
                       status="declared", introduced_in=None,
                       notes="x" * 300, aliases="[]")
            corrupt_db.insert("concept_nodes", node)
        corrupt_db.conn.execute("PRAGMA wal_checkpoint(FULL)")
        corrupt_db.conn.close()
        size = corrupt_db.path.stat().st_size
        with open(corrupt_db.path, "r+b") as handle:
            handle.seek(int(size * 0.7))
            chunk = handle.read(4096)
            handle.seek(int(size * 0.7))
            handle.write(bytes(b ^ 0xFF for b in chunk))
        reopened = api.open_db(str(corrupt_ws))
        report = sweeps.readiness(reopened, corrupt_manuscript)
        corrupt_item = next(i for i in report["items"]
                            if i["check"] == "database sound")
        check("readiness catches a corrupted store via 'database sound' "
              "instead of crashing the sweep",
              corrupt_item["ok"] is False
              and "database sound" in report["blocking"],
              corrupt_item)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _guard_fixture(prefix: str):
    """Common workspace/manuscript setup for the Q-remediation guard tests
    (check_alias_guard / check_alias_dedupe / check_note_group /
    check_note_materiality below). Caller is responsible for
    shutil.rmtree(root, ignore_errors=True) in a finally block."""
    import io

    root = Path(tempfile.mkdtemp(prefix=prefix))
    ws = root / "ws"
    ms = ws / "manuscript"
    ms.mkdir(parents=True)
    (ms / "00-seed.md").write_text("# Seed\n\nNothing to see here.\n")
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", "book",
                  "--path", str(ms)])
    db = api.open_db(str(ws))
    manuscript = api.get_manuscript(db, "book")
    return root, ws, ms, db, manuscript


def check_alias_guard() -> None:
    """Q/alias-guard: never propose an alias whose name is already a live
    concept — refused at the extraction.py creation site and recorded as
    system-provenance evidence instead of silently dropped.

    Ratified against measured evidence on the author's live graph: 13 of
    20 open alias proposals named a concept that was already its own live
    concept (e.g. 'History' -> 'Rebirth', with 'History' itself live)."""
    from authorlm import extraction

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-guard-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "AliasHistory", kind="concept")
        api.add_concept(db, manuscript, "AliasRebirth", kind="concept")
        sentence = "AliasHistory is what we mean by AliasRebirth in this text."
        (ms / "01-alias.md").write_text(f"# Alias\n\n{sentence}\n")

        class LiveAliasLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "AliasHistory", "canonical": "AliasRebirth",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        alias_before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        alias_result = extraction.extract_concepts(
            db, manuscript, LiveAliasLLM(), files=["01-alias.md"],
            aliases_only=True)
        alias_after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        check("an alias whose name is already a live concept creates no "
              "proposal",
              alias_result is not None and alias_after == alias_before,
              str(alias_result))
        check("extract_concepts reports exactly one refused fold",
              alias_result is not None
              and alias_result.get("alias_folds_refused") == 1,
              str(alias_result))
        alias_evidence = db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_fold_refused'", (mid,))
        check("the refusal is recorded as low-weight system-provenance "
              "evidence, not silently dropped",
              len(alias_evidence) == 1 and alias_evidence[0]["weight"] == "low",
              str([dict(r) for r in alias_evidence]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_alias_dedupe() -> None:
    """Q/alias-dedupe: one alias name may not have two open, contradictory
    canonicals — 'Prophecy' -> 'Birth-based Superstition' and 'Prophecy' ->
    'Essentialism' cannot both stand open simultaneously (measured on the
    author's live graph: exactly this pair). An exact repeat (same alias,
    same canonical) collapses to the standing row instead of piling up a
    second one."""
    from authorlm import proposals as prop

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-dedupe-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "AliasHistory", kind="concept")
        alias_target = db.one(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (mid, "AliasHistory"))["id"]
        first = prop.create(db, mid, "alias", alias_target,
                            {"alias": "Prophecy",
                             "canonical": "Birth-based Superstition",
                             "sentence": "Prophecy is birth-based superstition."})
        check("the first open canonical for an alias name is created",
              first is not None)
        second = prop.create(db, mid, "alias", alias_target,
                             {"alias": "Prophecy", "canonical": "Essentialism",
                              "sentence": "Prophecy is mere essentialism."})
        check("a second, contradictory canonical for the same alias name "
              "is refused while the first is still open",
              second is None)
        repeat = prop.create(db, mid, "alias", alias_target,
                             {"alias": "Prophecy",
                              "canonical": "Birth-based Superstition",
                              "sentence": "A differently-worded restatement."})
        check("an exact repeat (same alias, same canonical, different "
              "sentence) collapses instead of piling up a second row",
              repeat is None)
        open_alias_rows = [r for r in prop.open_proposals(db, mid)
                          if r["kind"] == "alias"]
        check("only the first alias proposal for 'Prophecy' ever reached "
              "the queue",
              len(open_alias_rows) == 1, str(open_alias_rows))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_note_group() -> None:
    """Q/note-group: several open note_update proposals on the same
    concept present as ONE grouped decision with every candidate note, not
    N separate ones — storage is untouched, each candidate keeps its own
    id and the author picks, nothing is auto-chosen. Ratified against
    measured evidence on the author's live graph: 31 of 64 open proposals
    compete, unpresented, over just 11 concepts (e.g. 'History' x4)."""
    from authorlm import proposals as prop

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-note-group-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "GroupedConcept", kind="concept",
                        notes="original note")
        grouped_target = db.one(
            "SELECT id FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (mid, "GroupedConcept"))["id"]
        candidate_notes = [
            "A first rewrite arguing the concept is fundamentally relational.",
            "A second rewrite arguing the concept is fundamentally temporal.",
            "A third rewrite arguing the concept is fundamentally structural.",
        ]
        for note in candidate_notes:
            created = prop.create(db, mid, "note_update", grouped_target,
                                  {"name": "GroupedConcept",
                                   "current_note": "original note",
                                   "proposed_note": note,
                                   "current_kind": "concept",
                                   "proposed_kind": "concept"})
            check(f"competing note_update candidate is created: {note[:30]}…",
                  created is not None)
        grouped_rows = prop.group_open(prop.open_proposals(db, mid))
        group_entry = next(r for r in grouped_rows
                           if r["kind"] == "note_update_group"
                           and r["target"] == grouped_target)
        check("three competing note_update proposals present as ONE "
              "grouped decision, not three",
              len(group_entry["members"]) == 3, str(group_entry))
        summary, details = prop.describe(group_entry)
        check("the grouped decision's summary names how many candidates "
              "are on the table",
              "3 candidate notes" in summary, summary)
        check("every candidate note appears in the rendered details, each "
              "addressable by its own id",
              all(any(m["id"][:8] in line and
                      loads(m["payload"], {})["proposed_note"][:20] in line
                      for line in details)
                  for m in group_entry["members"]),
              str(details))
        listed = api.list_proposals(db, manuscript, kind="note_update")
        listed_group = next(
            (r for r in listed["open"] if r["kind"] == "note_update_group"),
            None)
        check("list_proposals (the api/MCP surface) groups them too, "
              "without changing how they are stored",
              listed_group is not None
              and db.one(
                  "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
                  "manuscript_id = ? AND kind = 'note_update' AND "
                  "state = 'open'", (mid,))["n"] == 3,
              str(listed.get("open")))
        # Each candidate is still individually actionable by its own id.
        one_member = group_entry["members"][0]
        adopt_msg = prop.adopt(db, mid, one_member)
        check("a single candidate inside a group still adopts on its own id",
              "Updated 'GroupedConcept'" in adopt_msg, adopt_msg)
        still_open = [r for r in prop.open_proposals(db, mid)
                     if r["kind"] == "note_update" and r["target"] == grouped_target]
        check("adopting one candidate leaves its siblings open for their "
              "own review (no auto-picked winner)",
              len(still_open) == 2, str(still_open))
    finally:
        shutil.rmtree(root, ignore_errors=True)




def check_note_materiality() -> None:
    """Q/note-materiality: a case/whitespace-only change, or a >=0.90
    near-paraphrase, is refused at note_update creation (one observed
    proposal decapitalized the term of art 'Quality' to 'quality' — a
    live style-law violation); a genuine refinement in the 0.75-0.90 band
    still gets through (no over-cutting; the adjudicator already rules on
    that band correctly)."""
    from authorlm import extraction
    from authorlm.loop import similarity

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-note-materiality-")
    try:
        mid = manuscript["id"]
        blocked_case = extraction._note_update_blocked("Quality", "quality")
        check("a case-only note change is refused (the 'Quality' -> "
              "'quality' degradation)",
              blocked_case is not None, str(blocked_case))
        blocked_ws = extraction._note_update_blocked(
            "the   term  of   art", "the term of art")
        check("a whitespace-only note change is refused",
              blocked_ws is not None, str(blocked_ws))

        base_note = (
            "The clasp of every opposite, the ground of becoming, the "
            "shape that holds all form, the light within shadow, the "
            "silence beneath depth, the stillness at the origin of "
            "things, the quiet heart of paradox.")
        near_paraphrase = (
            "The clasp of every opposite, the ground of becoming, the "
            "shape that holds all form, the light within shadow, the "
            "silence beneath depth, the stillness at the source of "
            "things, the quiet heart of paradox.")
        refinement = (
            "The clasp of every opposite, the ground of becoming, the "
            "shape that holds all pattern, the light within shadow, the "
            "silence beneath distance, the stillness at the origin of "
            "things, the quiet heart of paradox.")
        para_score = similarity(base_note, near_paraphrase)
        refine_score = similarity(base_note, refinement)
        check(f"fixture calibration: the paraphrase pair scores >=0.90 "
              f"(measured {para_score:.3f})", para_score >= 0.90,
              str(para_score))
        check(f"fixture calibration: the refinement pair sits in the "
              f"0.75-0.90 band (measured {refine_score:.3f})",
              0.75 <= refine_score < 0.90, str(refine_score))
        blocked_para = extraction._note_update_blocked(base_note, near_paraphrase)
        check("a near-paraphrase (>=0.90 similarity) is refused",
              blocked_para is not None, str(blocked_para))
        allowed_refine = extraction._note_update_blocked(base_note, refinement)
        check("a genuine refinement in the 0.75-0.90 band is NOT refused "
              "— the floor must not become a ceiling (guard against "
              "over-cutting; do not lower it to 0.75)",
              allowed_refine is None, str(allowed_refine))

        # End-to-end through extract_concepts, not just the helper: the
        # near-paraphrase creates no proposal and is reported refused; the
        # 0.75-0.90 refinement still reaches the queue as a real proposal.
        api.add_concept(db, manuscript, "MaterialTerm", kind="concept",
                        notes=base_note)
        (ms / "02-materiality-block.md").write_text(
            f"# M\n\nMaterialTerm: {near_paraphrase}\n")

        class NearParaphraseLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "MaterialTerm", "kind": "concept",
                     "notes": near_paraphrase}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        note_before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'note_update'", (mid,))["n"]
        block_result = extraction.extract_concepts(
            db, manuscript, NearParaphraseLLM(),
            files=["02-materiality-block.md"])
        note_after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'note_update'", (mid,))["n"]
        check("end-to-end: a >=0.90 paraphrase creates no note_update "
              "proposal", note_after == note_before, str(block_result))
        check("end-to-end: extract_concepts reports the materiality "
              "refusal",
              block_result is not None
              and len(block_result.get("materiality_refused") or []) == 1
              and "MaterialTerm" in block_result["materiality_refused"][0],
              str(block_result))

        (ms / "03-materiality-allow.md").write_text(
            f"# M2\n\nMaterialTerm: {refinement}\n")

        class RefinementLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "MaterialTerm", "kind": "concept",
                     "notes": refinement}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        allow_result = extraction.extract_concepts(
            db, manuscript, RefinementLLM(),
            files=["03-materiality-allow.md"])
        note_after_allow = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'note_update'", (mid,))["n"]
        check("end-to-end: a genuine ~0.80-similarity refinement still "
              "reaches the queue as a real note_update proposal",
              note_after_allow == note_after + 1, str(allow_result))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_alias_guide_flip() -> None:
    """Q/alias-guide-flip: the ALIAS GUIDE now requires the canonical to
    already be a known concept and the alias to be a name that is NOT yet
    its own known concept — the naming-ceremony case ("we call it The
    Chid"). Measured against the author's 20 open alias proposals: 14
    alias names were LIVE concepts (genuine merges mislabelled as
    aliases), 6 were RETIRED, and 0 were the naming-ceremony case the old
    guide actually forbade ("BOTH names are known concepts"). This proves
    the positive case: a genuinely new name bestowed on a known concept
    now PRODUCES a real, adoptable alias proposal — without it the guard
    would leave the whole feature dead."""
    from authorlm import extraction, proposals as prop
    from authorlm.concepts import get_concept as _get_concept

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-flip-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "FlipCanonical", kind="concept",
                        notes="the established term")
        sentence = "We call it FlipBestowed, the settled name for FlipCanonical."
        (ms / "01-flip.md").write_text(f"# Flip\n\n{sentence}\n")

        class NamingCeremonyLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "FlipBestowed", "canonical": "FlipCanonical",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        check("the bestowed name does not exist as a concept before "
              "extraction",
              _get_concept(db, mid, "FlipBestowed") is None)
        before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        result = extraction.extract_concepts(
            db, manuscript, NamingCeremonyLLM(), files=["01-flip.md"],
            aliases_only=True)
        after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        check("a naming ceremony for a genuinely new name PRODUCES an "
              "alias proposal",
              result is not None and after == before + 1, str(result))
        row = db.one(
            "SELECT * FROM knowledge_proposals WHERE manuscript_id = ? "
            "AND kind = 'alias' AND state = 'open'", (mid,))
        payload = loads(row["payload"], {})
        check("the produced proposal is flagged as a new-name naming "
              "ceremony, not a merge",
              payload.get("alias_is_new") is True, str(payload))
        summary, details = prop.describe(dict(row))
        check("describe() phrases adoption as 'add', not 'merge', for a "
              "naming ceremony",
              "adopt = add:" in " ".join(details), details)

        message = prop.adopt(db, mid, dict(row))
        check("adopting the naming-ceremony proposal ADDS the name rather "
              "than attempting a merge",
              "recorded as a new name for 'FlipCanonical'" in message,
              message)
        canonical = db.one(
            "SELECT * FROM concept_nodes WHERE manuscript_id = ? AND name = ?",
            (mid, "FlipCanonical"))
        check("the canonical concept's aliases now include the bestowed "
              "name",
              "FlipBestowed" in loads(canonical["aliases"], []),
              canonical["aliases"])
        check("no second concept_nodes row was created for the bestowed "
              "name (it is an alias, not a new node)",
              db.one(
                  "SELECT COUNT(*) AS n FROM concept_nodes WHERE "
                  "manuscript_id = ? AND lower(name) = lower(?)",
                  (mid, "FlipBestowed"))["n"] == 0)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_alias_retired_guard() -> None:
    """Q/alias-retired-guard: an alias whose name is a RETIRED concept is
    also refused, not just one whose name is a live concept. Retired names
    are banned from re-entering the graph (extraction.py's `banned` set in
    the concepts loop); an alias would be a side door around that ban, so
    it is refused and recorded under a signal DISTINCT from the
    live-concept fold (Q/alias-guard) — the two reasons stay separable in
    the evidence: 6 of the author's 20 open alias proposals named a
    retired concept."""
    from authorlm import extraction

    root, ws, ms, db, manuscript = _guard_fixture("authorlm-alias-retired-")
    try:
        mid = manuscript["id"]
        api.add_concept(db, manuscript, "RetiredGuardCanon", kind="concept")
        api.add_concept(db, manuscript, "RetiredGuardName", kind="concept")
        db.update(
            "concept_nodes",
            db.one("SELECT id FROM concept_nodes WHERE manuscript_id = ? "
                   "AND name = ?", (mid, "RetiredGuardName"))["id"],
            {"status": "retired"})
        sentence = ("We once called RetiredGuardCanon by the name "
                    "RetiredGuardName.")
        (ms / "01-retired.md").write_text(f"# Retired\n\n{sentence}\n")

        class RetiredAliasLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [], "links": [], "aliases": [
                    {"alias": "RetiredGuardName",
                     "canonical": "RetiredGuardCanon",
                     "sentence": sentence}]}

            def stats_line(self):
                return None

        before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        result = extraction.extract_concepts(
            db, manuscript, RetiredAliasLLM(), files=["01-retired.md"],
            aliases_only=True)
        after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals WHERE "
            "manuscript_id = ? AND kind = 'alias'", (mid,))["n"]
        check("an alias naming a retired concept creates no proposal",
              result is not None and after == before, str(result))
        check("extract_concepts reports exactly one retired-name refusal, "
              "separately from live-concept folds",
              result is not None
              and result.get("alias_retired_refused") == 1
              and result.get("alias_folds_refused") == 0,
              str(result))
        retired_evidence = db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_retired_refused'", (mid,))
        check("the retired-name refusal is recorded under its own signal",
              len(retired_evidence) == 1
              and retired_evidence[0]["weight"] == "low", str(
                  [dict(r) for r in retired_evidence]))
        live_evidence = db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'extraction_adjudication' "
            "AND signal = 'alias_fold_refused'", (mid,))
        check("no live-concept-fold evidence is recorded for a retired-name "
              "refusal — the two reasons stay separable",
              len(live_evidence) == 0, str([dict(r) for r in live_evidence]))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_vanished_directory_guard() -> None:
    """it-258752ea91f9: a manuscript root that has gone missing (deleted
    directory, unmounted volume, etc.) must never be read the same as the
    author having deleted every file in it. `read_manuscript_files`
    returns {} for a missing root with no error, which — before this fix —
    fed straight into collect_revision() and made every tracked file look
    removed. The auto-collect mass-deletion guard only ever ran when
    `auto=True`, and _catch_up (reached by MCP get_briefing/get_guidance
    with no human present) always passes auto=False, so this was the
    exact unattended path that silently retired unconfirmed hypotheses."""
    import json as _json

    from authorlm.revisions import ManuscriptRootUnreadable

    root = Path(tempfile.mkdtemp(prefix="authorlm-vanished-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01.md").write_text("# Opening\n\nA placeholder paragraph.\n")
        db = api.open_db(str(ws))
        manuscript = api.register_manuscript(db, "book", str(ms))
        api.collect(db, manuscript, {})  # v1 baseline, no concepts yet

        # An unconfirmed extracted hypothesis (what extraction.py stamps
        # before the author has confirmed it), realized once its name
        # actually lands in the text — realization only runs on a version
        # that is actually recorded, so the checksum must change too.
        hypothesis = api.add_concept(db, manuscript, "Gravity")
        db.update("concept_nodes", hypothesis["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        (ms / "01.md").write_text(
            "# Opening\n\nA placeholder paragraph. Gravity is discussed early.\n")
        api.collect(db, manuscript, {})  # v2: realizes Gravity
        check("fixture: the hypothesis is realized before the directory "
              "vanishes",
              db.one("SELECT status FROM concept_nodes WHERE id = ?",
                     (hypothesis["id"],))["status"] == "realized")
        version_before = db.one(
            "SELECT version_no FROM manuscript_versions WHERE "
            "manuscript_id = ? ORDER BY version_no DESC LIMIT 1",
            (manuscript["id"],))["version_no"]

        shutil.rmtree(ms)  # the reported repro: the directory itself vanishes

        raised = None
        try:
            # This is exactly the shape _catch_up calls: auto defaults to
            # False, so no human is standing by to see a 'staged' prompt.
            api.collect(db, manuscript, {})
        except Exception as err:  # noqa: BLE001 — inspected below
            raised = err
        check("a non-auto collect against a vanished directory refuses "
              "instead of silently recording an all-files-removed version",
              isinstance(raised, ManuscriptRootUnreadable), repr(raised))

        after = db.one("SELECT status FROM concept_nodes WHERE id = ?",
                       (hypothesis["id"],))
        check("the unconfirmed hypothesis was NOT retired by the "
              "unreadable directory — that is not an editorial act",
              after["status"] == "realized", dict(after))

        version_after = db.one(
            "SELECT version_no FROM manuscript_versions WHERE "
            "manuscript_id = ? ORDER BY version_no DESC LIMIT 1",
            (manuscript["id"],))["version_no"]
        check("no new version was recorded for the vanished directory",
              version_after == version_before, (version_before, version_after))

        # --- _catch_up must surface hypotheses_dropped/vanished ---
        # Restore the directory, add a confirmed concept and another
        # unconfirmed hypothesis, realize both, then genuinely remove
        # their text (a real editorial deletion, not a vanished root) and
        # prove _catch_up's slimmer return shape still carries both
        # fields through to MCP get_briefing/get_guidance.
        ms.mkdir(parents=True)
        (ms / "01.md").write_text(
            "# Opening\n\nChoice enters here. A Fleeting Aside too.\n")
        confirmed_concept = api.add_concept(db, manuscript, "Choice")
        fleeting = api.add_concept(db, manuscript, "Fleeting Aside")
        db.update("concept_nodes", fleeting["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        api.collect(db, manuscript, {})  # realizes both
        check("fixture: both concepts realized before their text is removed",
              db.one("SELECT status FROM concept_nodes WHERE id = ?",
                     (confirmed_concept["id"],))["status"] == "realized"
              and db.one("SELECT status FROM concept_nodes WHERE id = ?",
                        (fleeting["id"],))["status"] == "realized")
        (ms / "01.md").write_text("# Opening\n\nNeither name remains.\n")

        caught = api._catch_up(db, manuscript, {})
        check("_catch_up surfaces the vanished (confirmed) proposal, not "
              "just version_no/transitions/new_files",
              caught is not None and caught.get("vanished") == ["Choice"],
              caught)
        check("_catch_up surfaces hypotheses_dropped (unconfirmed, retired "
              "quietly)",
              caught is not None
              and caught.get("hypotheses_dropped") == ["Fleeting Aside"],
              caught)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_unregister_safety() -> None:
    """it-f661015221b0: cmd_unregister deletes every manuscript-scoped row
    with no transaction and no backup. An interruption partway through
    the delete loop must leave the manuscript intact (not half-purged),
    and a fresh backup must exist before the delete is attempted at all."""
    import io as _io
    import sqlite3 as _sqlite3
    import types as _types

    from authorlm import backup, cli as cli_module
    from authorlm import proposals as prop_mod

    root = Path(tempfile.mkdtemp(prefix="authorlm-unreg-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01.md").write_text("# Opening\n\nGravity and Choice both appear.\n")
        db = api.open_db(str(ws))
        manuscript = api.register_manuscript(db, "book", str(ms))
        api.add_concept(db, manuscript, "Gravity")
        choice = api.add_concept(db, manuscript, "Choice")
        api.link_concepts(db, manuscript, "Choice", "permits", "Gravity")
        api.collect(db, manuscript, {})  # populates manuscript_versions
        # ORCH-2: knowledge_proposals was one of the 8 tables missing from
        # the old hand-maintained MANUSCRIPT_TABLES list — a row here
        # proves the schema-derived replacement actually purges it too.
        prop_mod.create(db, manuscript["id"], "vanished", choice["id"],
                        {"name": "Choice", "was_in": "01.md"})

        def _counts() -> dict:
            out = {}
            for table in cli_module._manuscript_tables(db):
                key = "id" if table == "manuscripts" else "manuscript_id"
                out[table] = db.one(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE {key} = ?",
                    (manuscript["id"],))["c"]
            return out

        before = _counts()
        check("fixture has rows across multiple manuscript-scoped tables "
              "before unregister",
              before["concept_nodes"] == 2 and before["concept_edges"] == 1
              and before["manuscript_versions"] == 1
              and before["manuscripts"] == 1
              and before["knowledge_proposals"] == 1, before)
        check("no backup exists yet — this workspace never opened a "
              "session",
              not backup.backup_dir(db.path).exists()
              or not list(backup.backup_dir(db.path).glob("authorlm-*.db")))

        # Simulate an interruption partway through the purge loop: boom
        # on the DELETE against concept_nodes, which sits in the middle
        # of MANUSCRIPT_TABLES — tables before it in the list would have
        # already had their DELETE issued under the old untransacted code.
        # sqlite3.Connection attributes are read-only, so the boom is a
        # thin proxy substituted for db.conn, not a monkeypatched method.
        real_conn = db.conn

        class _BoomConn:
            def execute(self, sql, *a, **kw):
                if sql.startswith("DELETE FROM concept_nodes"):
                    raise _sqlite3.OperationalError("simulated interruption")
                return real_conn.execute(sql, *a, **kw)

            def __getattr__(self, name):
                return getattr(real_conn, name)

        db.conn = _BoomConn()
        orig_open_db = cli_module._open_db
        cli_module._open_db = lambda args: db
        try:
            args = _types.SimpleNamespace(name="book", manuscript=None)
            raised = None
            try:
                cli_module.cmd_unregister(args)
            except SystemExit as err:
                raised = err
            except Exception as err:  # noqa: BLE001 — inspected below
                raised = err
            check("an interrupted unregister propagates rather than "
                  "silently completing",
                  raised is not None and not isinstance(raised, SystemExit),
                  repr(raised))
        finally:
            cli_module._open_db = orig_open_db
            db.conn = real_conn
            # Deliberately NOT rolling back here: a real interruption
            # (process kill, disk full) gets no such courtesy either. If
            # cmd_unregister wraps its loop in db.transaction(), the
            # transaction() context manager itself already rolled back
            # in its own except-clause before the exception reached us —
            # so this is a no-op post-fix and a faithful simulation
            # pre-fix.

        # Without db.transaction(), sqlite3's implicit (deferred) tx
        # means the tables deleted before the interruption are gone from
        # THIS connection's own view already — a read-your-own-writes
        # corruption, before any crash-recovery question even arises.
        mid = _counts()
        check("immediately after the interruption, on the very same "
              "connection, the manuscript is not left in a torn state — "
              "tables ordered before the interruption point were not "
              "actually purged",
              mid == before, {"before": before, "mid": mid})

        # And it must not become PERMANENT the next time anything else
        # commits on this connection (the ordinary case for a CLI process
        # that keeps going, or a long-lived MCP connection).
        from authorlm.db import ko_fields as _ko_fields

        sentinel = _ko_fields("cn")
        sentinel.update(manuscript_id="unrelated-manuscript", name="Sentinel",
                        kind="concept", status="declared", introduced_in=None,
                        notes=None, aliases="[]")
        db.insert("concept_nodes", sentinel)  # commits at transaction depth 0
        after = _counts()
        check("an interrupted unregister leaves the manuscript fully "
              "intact even after a later, unrelated commit on the same "
              "connection — nothing purged, not even the tables deleted "
              "before the interruption point",
              after == before, {"before": before, "after": after})

        made = list(backup.backup_dir(db.path).glob("authorlm-*.db"))
        check("a backup was taken before the (interrupted) delete began",
              len(made) == 1, made)

        # --- happy path: an uninterrupted unregister still purges
        # everything, and the transaction/backup addition doesn't change
        # that outcome ---
        cli_module._open_db = lambda args: db
        try:
            with contextlib.redirect_stdout(_io.StringIO()):
                cli_module.cmd_unregister(_types.SimpleNamespace(
                    name="book", manuscript=None))
        finally:
            cli_module._open_db = orig_open_db
        final = _counts()
        check("an uninterrupted unregister still purges every "
              "manuscript-scoped row",
              all(v == 0 for v in final.values()), final)
        made_final = list(backup.backup_dir(db.path).glob("authorlm-*.db"))
        check("the successful unregister also left a backup behind "
              "(taken before the delete, same as the interrupted run)",
              len(made_final) >= 1, made_final)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_backup_on_active_session() -> None:
    """it-0bffe1ff657b: backup.run() only ever fired from the branch of
    start_session() that actually INSERTs a new session row (sessions.py).
    With a session already active — the normal state for the entire span
    of a working day, since the author does not re-run 'session start'
    once one is open — that branch never runs, so no backup is ever
    taken during the day the database is actually being changed. The
    documented trigger ('roughly once per working day') was false
    whenever a session was open. Reproduced live: CLI 'session start'
    against an already-active session exited 1 with 'A session is
    already active' and wrote no authorlm-*.db.

    Fix: back up on resume too — the branch that finds an existing
    session and is about to refuse now backs up first. skip-if-unchanged
    (backup.py) means a rapid double 'session start' costs nothing extra
    once the first backup that day has already captured the state."""
    from authorlm import backup, sessions as bses

    root = Path(tempfile.mkdtemp(prefix="authorlm-backup-trigger-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01.md").write_text("# hi\n")
        db = api.open_db(str(ws))
        manuscript = api.register_manuscript(db, "book", str(ms))

        bses.start_session(db, manuscript["id"])
        bdir = backup.backup_dir(db.path)
        check("starting the first session performs a backup",
              len(list(bdir.glob("authorlm-*.db"))) == 1)

        # A real change since the first backup, so the resume backup
        # below has something new to capture (not skipped as identical).
        node = ko_fields("cn")
        node.update(manuscript_id=manuscript["id"], name="Gravity",
                    kind="concept", status="declared", introduced_in=None,
                    notes="", aliases="[]")
        db.insert("concept_nodes", node)

        raised = None
        try:
            bses.start_session(db, manuscript["id"])
        except ValueError as err:
            raised = err
        check("starting a session while one is already active still "
              "raises exactly as before — no confirmation prompt, no "
              "silent takeover of the existing session",
              raised is not None and "already active" in str(raised),
              repr(raised))

        made = sorted(bdir.glob("authorlm-*.db"))
        check("a backup fires even when a session is already active — "
              "this is the normal state for the whole of a working day, "
              "which is exactly when the database is changing",
              len(made) == 2, made)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_summary_deprecation() -> None:
    """Sponsor, verbatim: "If a essay is no longer present, it should
    not be considered stale - it should have a 'deprecated' status or
    similar (see other similar objects in the db and their status field
    enums)." essay_summaries.status ('current' | 'deprecated') follows
    the codebase's existing status-enum idiom (concept_nodes,
    knowledge_proposals, doc_threads). Additive schema only (db.py); the
    transition is triggered from collect() (api.py) — summaries.py
    itself is untouched, since sums.status()/before_after() are already
    driven off the CURRENT toc reading order and so structurally never
    see a departed file's row (verified: the live '22 stale' figure was
    already correct before this fix — this closes the latent hazard for
    when a summary DOES outlive its file, never delete it, never let
    it read as an ordinary live summary)."""
    import hashlib

    from authorlm import summaries as sums

    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    root = Path(tempfile.mkdtemp(prefix="authorlm-summary-deprecation-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        text_a = "# A\n\nFirst essay, about to be removed.\n"
        text_b = "# B\n\nSecond essay, about to be edited.\n"
        (ms / "01-a.md").write_text(text_a)
        (ms / "02-b.md").write_text(text_b)
        db = api.open_db(str(ws))
        manuscript = api.register_manuscript(db, "book", str(ms))
        api.collect(db, manuscript, {})

        def _insert_summary(file: str, text: str) -> dict:
            row = ko_fields("es")
            row.update(manuscript_id=manuscript["id"], file=file,
                      summary=f"summary of {file}", source_hash=_hash(text),
                      upstream_hash=_hash(""), upstream_stale=0)
            db.insert("essay_summaries", row)
            return row

        row_a = _insert_summary("01-a.md", text_a)
        row_b = _insert_summary("02-b.md", text_b)
        check("a freshly-inserted summary defaults to status 'current' "
              "(additive column, no back-compat break)",
              db.one("SELECT status FROM essay_summaries WHERE id = ?",
                     (row_a["id"],))["status"] == "current")

        # B stays present but its text changes (a NORMAL stale summary —
        # must be unaffected by deprecation). A is deleted outright — its
        # file leaves the toc (no toc.toml here, so reading order is the
        # alphabetical fallback over files actually on disk).
        (ms / "02-b.md").write_text(text_b + "\nA new paragraph.\n")
        (ms / "01-a.md").unlink()
        report = api.collect(db, manuscript, {})

        check("collect deprecates the departed file's summary and "
              "reports the transition",
              report.get("summaries_deprecated") == ["01-a.md"], str(report))
        dep_row = db.one("SELECT * FROM essay_summaries WHERE id = ?",
                         (row_a["id"],))
        check("the deprecated summary is KEPT, never deleted — it is "
              "the record of an essay that existed",
              dep_row is not None and dep_row["status"] == "deprecated",
              dict(dep_row) if dep_row else None)

        states = {r["file"]: r for r in sums.status(db, manuscript)}
        check("a deprecated summary drops out of the stale/status "
              "listing entirely — it is no longer in the toc, so it "
              "never again counts as stale",
              "01-a.md" not in states, states)
        check("a normal stale summary (file still present, text edited) "
              "is unaffected: still reports 'stale', stays status "
              "'current' — deprecation never touches it",
              states.get("02-b.md", {}).get("state") == "stale"
              and db.one("SELECT status FROM essay_summaries WHERE id = ?",
                        (row_b["id"],))["status"] == "current",
              states)

        # Idempotent: nothing left to change, so the next collect is a
        # no-op and never re-reports (or re-touches) the deprecation.
        report2 = api.collect(db, manuscript, {})
        check("re-collecting with nothing new doesn't re-report an "
              "already-deprecated summary",
              not report2.get("summaries_deprecated"), report2)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main_test() -> None:
    check_broken_pipe()
    check_show_verbs()
    check_config_parity()
    check_extraction_failure_traced()
    check_extraction_prompt_provenance()
    check_extraction_skip_reasons()
    check_backup_and_restore()
    check_vanished_directory_guard()
    check_unregister_safety()
    check_backup_on_active_session()
    check_summary_deprecation()
    check_alias_guard()
    check_alias_dedupe()
    check_note_group()
    check_note_materiality()
    check_alias_guide_flip()
    check_alias_retired_guard()
    root = Path(tempfile.mkdtemp(prefix="authorlm-api-"))
    try:
        ws = root / "ws"
        ms = ws / "manuscript"
        ms.mkdir(parents=True)
        (ms / "01-choice.md").write_text(
            "# Opening\n\nGravity is discussed early.\n\nEvery act begins with choice.\n"
        )
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(ws), "init", "--name", "book",
                      "--path", str(ms), "--author", "Ada Author",
                      "--copyright-owner", "Ada Author LLC",
                      "--paperback-isbn", "979-8-90452-354-1",
                      "--hardcover-isbn", "979-8-90452-351-0"])

        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db)
        check("get_manuscript resolves the single manuscript",
              manuscript["name"] == "book")
        check("init records publication identity on the manuscript",
              manuscript["author"] == "Ada Author"
              and manuscript["copyright_owner"] == "Ada Author LLC"
              and manuscript["paperback_isbn"] == "9798904523541"
              and manuscript["hardcover_isbn"] == "9798904523510")
        try:
            api.register_manuscript(
                db, "book", str(ms), author="Duplicate Author")
            duplicate_registration_refused = False
        except ValueError:
            duplicate_registration_refused = True
        check("application API owns manuscript registration invariants",
              duplicate_registration_refused)
        try:
            api.register_manuscript(
                db, "missing-book", str(ws / "does-not-exist"))
            invalid_path_refused = False
        except ValueError:
            invalid_path_refused = True
        check("application API refuses a nonexistent manuscript directory",
              invalid_path_refused)
        same_isbn_ms = ws / "same-isbn-book"
        same_isbn_ms.mkdir()
        try:
            api.register_manuscript(
                db, "same-isbn-book", str(same_isbn_ms),
                paperback_isbn="979-8-90452-354-1",
                hardcover_isbn="979-8-90452-354-1")
            same_registration_isbn_refused = False
        except ValueError:
            same_registration_isbn_refused = True
        check("registration requires distinct physical-format ISBNs",
              same_registration_isbn_refused)
        updated_identity = api.update_manuscript_metadata(
            db, manuscript, author="A. Author",
            copyright_owner="Author House LLC")
        check("publication identity updates through the application API",
              updated_identity["author"] == "A. Author"
              and updated_identity["copyright_owner"] == "Author House LLC"
              and api.list_manuscripts(db)[0]["author"] == "A. Author")
        try:
            api.update_manuscript_metadata(
                db, manuscript, paperback_isbn="979-8-90452-354-2")
            invalid_isbn_refused = False
        except ValueError:
            invalid_isbn_refused = True
        check("publication identity refuses an invalid ISBN-13 checksum",
              invalid_isbn_refused
              and api.get_manuscript(db)["paperback_isbn"]
              == "9798904523541")
        try:
            api.update_manuscript_metadata(
                db, manuscript, hardcover_isbn="4006381333931")
            non_isbn_ean_refused = False
        except ValueError:
            non_isbn_ean_refused = True
        check("publication identity requires the ISBN-13 book prefix",
              non_isbn_ean_refused)
        try:
            cli_main(["--workspace", str(ws), "manuscript", "set",
                      "--hardcover-isbn", "979-8-90452-354-1"])
            same_cli_isbn_refused = False
        except SystemExit as err:
            same_cli_isbn_refused = (
                "paperback and hardcover ISBNs must be different" in str(err))
        check("CLI requires distinct physical-format ISBNs",
              same_cli_isbn_refused
              and api.get_manuscript(db)["hardcover_isbn"]
              == "9798904523510")
        identity_out = io.StringIO()
        with contextlib.redirect_stdout(identity_out):
            cli_main(["--workspace", str(ws), "manuscript", "set",
                      "--author", "Author Penname"])
        manuscript = api.get_manuscript(db)
        check("manuscript identity is editable through the CLI",
              manuscript["author"] == "Author Penname"
              and manuscript["copyright_owner"] == "Author House LLC"
              and "Author Penname" in identity_out.getvalue())

        # --- export settings: quotes/backslashes round-trip through TOML ---
        from authorlm.export import load_settings, set_setting

        set_setting(manuscript, "title", 'A "Great" Book')
        set_setting(manuscript, "reference_docx", "C:\\styles\\ref.docx")
        check("quotes and backslashes in settings round-trip through TOML",
              load_settings(manuscript)["title"] == 'A "Great" Book'
              and load_settings(manuscript)["reference_docx"]
              == "C:\\styles\\ref.docx")
        # Reset: the pandoc export test below runs on this same
        # manuscript and must not inherit the fake reference docx.
        set_setting(manuscript, "reference_docx", "")

        # --- trace log: shape, error truncation, rotation, never-raises ---
        import json as _tjson

        from authorlm import tracelog

        trace_ws = root / "trace-ws"
        tracelog.record("get_status", surface="cli", workspace=str(trace_ws),
                        manuscript="book", duration_ms=12, ok=True)
        trace_path = tracelog.log_dir(str(trace_ws)) / "trace.jsonl"
        entry = _tjson.loads(trace_path.read_text().splitlines()[0])
        check("record() writes one well-formed JSONL entry",
              entry["verb"] == "get_status" and entry["surface"] == "cli"
              and entry["manuscript"] == "book" and entry["duration_ms"] == 12
              and entry["ok"] is True and "ts" in entry, entry)

        tracelog.record("get_briefing", surface="mcp", workspace=str(trace_ws),
                        ok=False, error="boom" * 200)
        entries = [_tjson.loads(line)
                  for line in trace_path.read_text().splitlines()]
        check("errors are recorded and truncated to 500 chars",
              entries[-1]["ok"] is False and len(entries[-1]["error"]) == 500)

        trace_path.write_text("x" * (tracelog.MAX_BYTES + 1))
        tracelog.record("collect_revision", surface="cli",
                        workspace=str(trace_ws))
        rotated = trace_path.with_suffix(".jsonl.1")
        check("oversized trace rotates to .jsonl.1; a fresh file starts",
              rotated.exists() and rotated.stat().st_size > tracelog.MAX_BYTES
              and len(trace_path.read_text().splitlines()) == 1)

        blocked = root / "blocked-file"
        blocked.write_text("not a directory")
        tracelog.record("get_status", surface="cli",
                        workspace=str(blocked / "ws"))
        check("a broken trace destination never raises", True)
        check("resolve_file maps a bare filename",
              api.resolve_file(db, "01-choice.md")["name"] == "book")
        check("resolve_file rejects foreign paths",
              api.resolve_file(db, "/tmp/nowhere.md") is None)

        session, created = api.ensure_session(db, manuscript, client_id="mcp-test123")
        check("ensure_session lazily opens and stamps client id", created
              and "mcp-test123" in (session["metadata"] or ""))
        session2, created2 = api.ensure_session(db, manuscript)
        check("ensure_session reuses the active session",
              not created2 and session2["id"] == session["id"])

        api.add_concept(db, manuscript, "Gravity")
        api.add_concept(db, manuscript, "Choice")
        api.link_concepts(db, manuscript, "Choice", "permits", "Gravity")

        report = api.collect(db, manuscript, {})
        check("collect returns a structured report",
              report["version_no"] == 1 and report["transitions"]
              and any(n["name"] == "Gravity" for n in report["realized"]))
        check("collect reports prerequisite gaps structurally",
              len(report["gaps_after"]) == 1
              and report["gaps_after"][0]["second"] == "Gravity")

        # --- massive_deletions boundary conditions (auto-collect safety net) ---
        # Isolated manuscript: the shrink_ratio/min_chars math needs an exact,
        # uncluttered file history.
        md_root = root / "md-ws"
        md_ms = md_root / "manuscript"
        md_ms.mkdir(parents=True)
        big_text = "Paragraph. " * 200  # 2200 chars, well over min_chars
        (md_ms / "safety.md").write_text(big_text)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(md_root), "init", "--name", "safety",
                      "--path", str(md_ms)])
        md_db = api.open_db(str(md_root))
        md_manuscript = api.get_manuscript(md_db)
        api.collect(md_db, md_manuscript, {})  # v1 baseline: 2200 chars

        (md_ms / "safety.md").write_text(big_text[:1760])  # 80% remains
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("auto-collect proceeds under the shrink_ratio threshold",
              "staged" not in report)

        (md_ms / "safety.md").write_text(big_text[:50])  # ~97% removed vs. v2
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("auto-collect stages a massive deletion instead of collecting",
              report.get("staged") == [{"file": "safety.md", "removed_percent": 97}])

        report = api.collect(md_db, md_manuscript, {}, auto=False)
        check("a manual collect proceeds through a massive deletion",
              "staged" not in report and "unchanged" not in report)

        (md_ms / "tiny.md").write_text("x" * 100)  # under min_chars
        api.collect(md_db, md_manuscript, {})
        (md_ms / "tiny.md").write_text("")
        report = api.collect(md_db, md_manuscript, {}, auto=True)
        check("files under min_chars are exempt from the deletion safety net",
              "staged" not in report)

        # --- FAIL-3: read_manuscript_files tolerates a non-UTF-8 file
        # instead of raising and taking down collect() — the recovery path
        # must not die on the failure it is meant to recover from. Every
        # file present on disk must still appear in the result: a dropped
        # entry reads to massive_deletions() as a 100%-shrunk file (via
        # disk.get(name, "")) and would misfire the mass-deletion guard.
        # Exercised at the revisions.py layer directly (collect_revision /
        # massive_deletions) to isolate this from the unrelated, separately
        # scoped decode call in illus.maintain_excerpts (also invoked by
        # api.collect(), not part of this finding). ---
        from authorlm.revisions import (collect_revision as collect_rev_bad_utf8,
                                        massive_deletions as massive_del_bad_utf8,
                                        read_manuscript_files as read_files_bad_utf8)

        bad_root = root / "badbytes-ws"
        bad_ms = bad_root / "manuscript"
        bad_ms.mkdir(parents=True)
        (bad_ms / "01-good.md").write_text("Good paragraph. " * 40)  # >min_chars
        (bad_ms / "02-bad.md").write_bytes(b"# Heading\n\xff\xfe not valid utf-8\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(bad_root), "init", "--name", "badbytes",
                      "--path", str(bad_ms)])
        bad_db = api.open_db(str(bad_root))
        bad_manuscript = api.get_manuscript(bad_db)

        files = read_files_bad_utf8(bad_ms)
        check("read_manuscript_files does not drop a file it cannot decode",
              set(files) == {"01-good.md", "02-bad.md"}, str(sorted(files)))
        check("the unreadable file's content is a replacement, not empty "
              "(an empty string would read as a full deletion)",
              "�" in files["02-bad.md"] and files["02-bad.md"] != "",
              repr(files["02-bad.md"]))

        v1 = collect_rev_bad_utf8(bad_db, bad_manuscript, None)
        check("collect_revision() completes through a mixed-encoding "
              "manuscript instead of raising",
              v1 is not None and v1["version_no"] == 1, str(v1))
        stored_files = loads(v1["files"], {})
        check("the unreadable file is still present in the persisted "
              "snapshot, not silently dropped as a phantom deletion",
              "02-bad.md" in stored_files, str(sorted(stored_files)))

        # Re-collecting unchanged content must not look like a deletion.
        flagged = massive_del_bad_utf8(bad_db, bad_manuscript)
        check("massive_deletions does not flag an unreadable-but-unchanged "
              "file as a mass deletion",
              flagged == [], str(flagged))

        # --- FAIL-3 (end-to-end): the checks above exercise
        # read_manuscript_files directly, which is exactly how this gap
        # survived — api.collect() runs illus.maintain_excerpts BEFORE
        # read_manuscript_files, and maintain_excerpts had its own bare
        # `path.read_text(encoding="utf-8")` with no error handling.
        # api.collect() wraps that call in `except OSError` only, and
        # UnicodeDecodeError is a ValueError, not an OSError, so it was
        # not caught: a single bad byte in any manuscript file crashed
        # api.collect() end-to-end — the recovery path itself. This test
        # goes through api.collect() to prove that path survives, using a
        # fresh workspace so it is not coupled to the direct-layer
        # assertions above. ---
        e2e_root = root / "e2e-badbytes-ws"
        e2e_ms = e2e_root / "manuscript"
        e2e_ms.mkdir(parents=True)
        (e2e_ms / "01-good.md").write_text("Good paragraph. " * 40)
        (e2e_ms / "02-bad.md").write_bytes(b"# Heading\n\xff\xfe not valid utf-8\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(e2e_root), "init", "--name", "e2ebadbytes",
                      "--path", str(e2e_ms)])
        e2e_db = api.open_db(str(e2e_root))
        e2e_manuscript = api.get_manuscript(e2e_db)

        report = api.collect(e2e_db, e2e_manuscript, {})
        check("api.collect() completes end-to-end through a manuscript "
              "containing invalid UTF-8, instead of raising",
              "staged" not in report, str(report))
        v1 = e2e_db.one(
            "SELECT * FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1",
            (e2e_manuscript["id"],))
        stored_files = loads(v1["files"], {}) if v1 else {}
        check("the file with invalid UTF-8 is snapshotted by api.collect(), "
              "not silently dropped as a phantom deletion",
              "02-bad.md" in stored_files, str(sorted(stored_files)))
        # --- detect_transitions in isolation (T5, risk-register §3): no
        # existing test calls the diff/opcode logic directly, only through
        # the full collect() pipeline. Isolated manuscript, own history. ---
        from authorlm.revisions import collect_revision, detect_transitions

        dt_root = root / "dt-ws"
        dt_ms = dt_root / "manuscript"
        dt_ms.mkdir(parents=True)
        dt_db = api.open_db(str(dt_root))

        # Paragraph reorder: difflib has no "moved" concept, so swapping two
        # adjacent paragraphs is reported as the moved paragraph's text
        # being inserted at its new position and deleted from its old one —
        # not a single "reorder" transition. Characterizing, not endorsing.
        dt_ms1 = dt_root / "reorder"
        dt_ms1.mkdir()
        row_reorder = api.register_manuscript(dt_db, "reorder", str(dt_ms1))
        (dt_ms1 / "a.md").write_text(
            "# Heading\n\nP1 text here.\n\nP2 text here.\n\nP3 text here.\n")
        v1 = collect_revision(dt_db, row_reorder, None, source="test")
        (dt_ms1 / "a.md").write_text(
            "# Heading\n\nP2 text here.\n\nP1 text here.\n\nP3 text here.\n")
        v2 = collect_revision(dt_db, row_reorder, None, source="test")
        reorder_trans = detect_transitions(dt_db, row_reorder["id"], dict(v1), v2)
        check("paragraph reorder is an insert of the moved text at its new "
              "position plus a delete at its old one, not a single move",
              [(t["kind"], t["location"]) for t in reorder_trans]
              == [("insert", "a.md#Heading"), ("delete", "a.md#Heading")],
              str([(t["kind"], t["location"], t["summary"])
                   for t in reorder_trans]))
        check("the reorder's insert/delete pair both carry the moved "
              "paragraph's own text, not the paragraph it displaced",
              "P2 text here." in reorder_trans[0]["summary"]
              and "P2 text here." in reorder_trans[1]["summary"])

        # Two non-adjacent hunks in one file: each rewritten paragraph is
        # its own transition, not merged into a single spanning edit.
        dt_ms2 = dt_root / "hunks"
        dt_ms2.mkdir()
        row_hunks = api.register_manuscript(dt_db, "hunks", str(dt_ms2))
        (dt_ms2 / "a.md").write_text(
            "# Heading\n\nP1 unchanged.\n\nP2 original.\n\nP3 unchanged.\n\n"
            "P4 original.\n\nP5 unchanged.\n")
        h1 = collect_revision(dt_db, row_hunks, None, source="test")
        (dt_ms2 / "a.md").write_text(
            "# Heading\n\nP1 unchanged.\n\nP2 REWRITTEN.\n\nP3 unchanged.\n\n"
            "P4 REWRITTEN.\n\nP5 unchanged.\n")
        h2 = collect_revision(dt_db, row_hunks, None, source="test")
        hunk_trans = detect_transitions(dt_db, row_hunks["id"], dict(h1), h2)
        check("two non-adjacent hunks in one file produce two separate "
              "rewrite transitions, not one spanning edit",
              len(hunk_trans) == 2
              and all(t["kind"] == "rewrite" for t in hunk_trans)
              and "P2 REWRITTEN" in hunk_trans[0]["summary"]
              and "P4 REWRITTEN" in hunk_trans[1]["summary"],
              str([(t["kind"], t["summary"]) for t in hunk_trans]))

        # Heading-less file: location falls back to the bare filename, no
        # '#heading' suffix, since _nearest_heading finds nothing to anchor to.
        dt_ms3 = dt_root / "noheading"
        dt_ms3.mkdir()
        row_noheading = api.register_manuscript(dt_db, "noheading", str(dt_ms3))
        (dt_ms3 / "b.md").write_text(
            "Just prose, no headings at all.\n\nSecond paragraph.\n")
        n1 = collect_revision(dt_db, row_noheading, None, source="test")
        (dt_ms3 / "b.md").write_text(
            "Just prose, no headings at all.\n\nSecond paragraph, edited.\n")
        n2 = collect_revision(dt_db, row_noheading, None, source="test")
        noheading_trans = detect_transitions(
            dt_db, row_noheading["id"], dict(n1), n2)
        check("a heading-less file's transition location is the bare "
              "filename, with no '#heading' suffix",
              len(noheading_trans) == 1
              and noheading_trans[0]["location"] == "b.md",
              str([(t["kind"], t["location"]) for t in noheading_trans]))

        # --- history show/restore (MVP.md 'Deliberately deferred': version
        # access & restoration — implementable at any time, no schema change) ---
        try:
            api.get_version(md_db, md_manuscript, 99)
            check("get_version rejects an unknown version number", False)
        except LookupError as err:
            check("get_version rejects an unknown version number", "v99" in str(err))

        v1 = api.get_version(md_db, md_manuscript, 1)
        check("get_version returns the requested snapshot",
              loads(v1["files"], {})["safety.md"] == big_text)

        restored = api.restore_version(md_db, md_manuscript, 1, {})
        check("restore_version writes the old content back to disk",
              (md_ms / "safety.md").read_text() == big_text)
        check("restore_version deletes files absent from the restored version",
              not (md_ms / "tiny.md").exists())
        check("restoring advances history rather than rewinding it",
              restored["version_no"] == 6
              and restored["transitions"])
        latest = api.get_version(md_db, md_manuscript, 6)
        check("the restored snapshot is itself a new, real version",
              loads(latest["files"], {})["safety.md"] == big_text
              and "tiny.md" not in loads(latest["files"], {}))

        # --- BUG-2 / A1 regression: restore_version must snapshot the
        # current on-disk state into history BEFORE unlinking/overwriting
        # it, so any work the author had on disk but never collected is
        # still recoverable afterward (a self-contained fixture, so it
        # cannot be confused with the version-numbering narrative above) ---
        rv_root = root / "rv-ws"
        rv_ms = rv_root / "manuscript"
        rv_ms.mkdir(parents=True)
        (rv_ms / "only.md").write_text("Original collected content.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(rv_root), "init", "--name", "rv",
                      "--path", str(rv_ms)])
        rv_db = api.open_db(str(rv_root))
        rv_manuscript = api.get_manuscript(rv_db)
        api.collect(rv_db, rv_manuscript, {})  # v1

        # The author edits the tracked file AND starts a brand-new file —
        # neither ever collected. This is the exact state a live session
        # sits in between autosaves; restore_version must not be the thing
        # that erases it.
        (rv_ms / "only.md").write_text("EDITED BUT NEVER COLLECTED.\n")
        (rv_ms / "new-uncollected.md").write_text(
            "A NEW FILE, NEVER COLLECTED.\n")

        api.restore_version(rv_db, rv_manuscript, 1, {})
        check("restore_version still restores the target version's content",
              (rv_ms / "only.md").read_text()
              == "Original collected content.\n"
              and not (rv_ms / "new-uncollected.md").exists())

        rv_versions = rv_db.all(
            "SELECT files FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no", (rv_manuscript["id"],))
        recovered = any(
            loads(v["files"], {}).get("only.md")
            == "EDITED BUT NEVER COLLECTED.\n"
            and loads(v["files"], {}).get("new-uncollected.md")
            == "A NEW FILE, NEVER COLLECTED.\n"
            for v in rv_versions)
        check("restore_version snapshots the pre-destruction disk state "
              "into history before overwriting/deleting it (BUG-2 / A1)",
              recovered,
              [sorted(loads(v["files"], {})) for v in rv_versions])

        try:
            api.diff_versions(md_db, md_manuscript, older=1, newer=99)
            check("diff_versions rejects an unknown version number", False)
        except LookupError as err:
            check("diff_versions rejects an unknown version number",
                  "v99" in str(err))

        forward = api.diff_versions(md_db, md_manuscript, older=1, newer=3)
        backward = api.diff_versions(md_db, md_manuscript, older=3, newer=1)
        check("diff_versions normalizes reversed (newer, older) arguments",
              backward["old"] == forward["old"] == "v1"
              and backward["new"] == forward["new"] == "v3"
              and backward["files"] == forward["files"])

        declared = api.declare_intent(db, manuscript, "Expand on gravity")
        check("declare_intent returns intent + preview",
              declared["intent"]["statement"] == "Expand on gravity"
              and declared["preview"]["matched"][0]["name"] == "Gravity")
        typo = api.intent_preview(db, manuscript, "Discuss gravety maybe")
        check("preview fuzzy-suggests on typos", "Gravity" in typo["suggestions"])

        # --- intent complete/abandon: active-state + lookup errors ---
        life = api.declare_intent(db, manuscript, "Lifecycle probe intent")
        life_id = life["intent"]["id"]
        finished = api.complete_intent(db, manuscript, life_id[:8], "wrapped up")
        check("complete_intent marks an active intent completed with outcome",
              finished["intent"]["id"] == life_id
              and db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                         (life_id,))["status"] == "completed"
              and db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                         (life_id,))["outcome"] == "wrapped up")
        try:
            api.complete_intent(db, manuscript, life_id[:8], "again")
            check("complete_intent rejects an already-completed intent", False)
        except ValueError as err:
            check("complete_intent rejects an already-completed intent",
                  "already completed" in str(err))
        check("rejected re-complete leaves the original outcome intact",
              db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                     (life_id,))["outcome"] == "wrapped up")

        drop = api.declare_intent(db, manuscript, "Abandon-then-complete probe")
        drop_id = drop["intent"]["id"]
        api.abandon_intent(db, manuscript, drop_id[:8], "changed mind")
        try:
            api.complete_intent(db, manuscript, drop_id[:8], "should fail")
            check("complete_intent rejects an abandoned intent", False)
        except ValueError as err:
            check("complete_intent rejects an abandoned intent",
                  "already abandoned" in str(err))
        check("rejected complete-after-abandon preserves abandonment reason",
              db.one("SELECT status, outcome FROM declared_intents WHERE id = ?",
                     (drop_id,))["status"] == "abandoned"
              and db.one("SELECT outcome FROM declared_intents WHERE id = ?",
                         (drop_id,))["outcome"] == "changed mind")
        try:
            api.abandon_intent(db, manuscript, drop_id[:8], "again")
            check("abandon_intent rejects a non-active intent", False)
        except ValueError as err:
            check("abandon_intent rejects a non-active intent",
                  "already abandoned" in str(err))
        try:
            api.complete_intent(db, manuscript, "zzzzzzzz", None)
            check("complete_intent rejects an unknown prefix", False)
        except LookupError:
            check("complete_intent rejects an unknown prefix", True)

        guidance = api.guide(db, manuscript, session)
        check("guide returns structured suggestions",
              not guidance["abstained"] and guidance["suggestions"])
        reviewed = api.review(db, manuscript, session, 1, "accepted",
                              "Right place for it")
        check("review records and returns evidence effects",
              reviewed["guidance"]["batch_index"] == 1)
        try:
            api.review(db, manuscript, session, 1, "accepted", None)
            check("double review raises", False)
        except ValueError:
            check("double review raises", True)

        briefing = api.get_briefing(db, manuscript)
        check("briefing is a serializable dict",
              "learning_velocity" in briefing)

        # --- INV-15: a belief retired inside the briefing window must not
        # reappear under "newly seeded candidate beliefs" — that is the
        # author's explicit "no" shown back as a fresh finding. ---
        retired_belief_row = ko_fields("pol")
        retired_belief_row.update(
            manuscript_id=manuscript["id"],
            statement="INV-15 regression: a belief the author retires.",
            status="candidate", confidence=0.5, supporting=1,
            contradicting=0, outstanding_questions="[]",
            source="review-explanation", source_id=db.source("author"))
        db.insert("editorial_beliefs", retired_belief_row)
        api.retire_belief(db, manuscript, retired_belief_row["id"][:8],
                          "author said no")
        briefing_after_retire = api.get_briefing(db, manuscript)
        check("INV-15: a belief retired within the window is absent from "
              "the briefing's newly-seeded list",
              retired_belief_row["id"] not in
              {b["id"] for b in briefing_after_retire["new_beliefs"]},
              str(briefing_after_retire["new_beliefs"]))
        diff = api.diff_versions(db, manuscript)
        check("diff_versions returns per-file line lists",
              diff["new"] == "v1" and "01-choice.md" in diff["files"])

        # New chapter files must be acknowledged distinctly, not buried
        # in transitions (session start prints report["new_files"]).
        (ms / "late-arrival.md").write_text("# **Late**\n\nA new chapter.\n")
        late = api.collect(db, manuscript, {})
        check("collect surfaces new files distinctly",
              late.get("new_files") == ["late-arrival.md"],
              str(late.get("new_files")))
        (ms / "late-arrival.md").unlink()
        api.collect(db, manuscript, {})

        from authorlm import extraction

        extraction_prompt = extraction.extraction_system()
        check("extraction prompt keeps definitions out of concept names",
              '"name" is the shortest stable label' in extraction_prompt
              and "Do not promote\n  the definiens" in extraction_prompt
              and 'emit name "Hard\nFloor"' in extraction_prompt)

        graph = api.list_concepts(db, manuscript)
        check("list_concepts returns nodes and named edges",
              len(graph["nodes"]) == 2
              and graph["edges"][0]["from_name"] == "Choice")
        api.confirm_concept(db, manuscript, "Gravity", kind="metaphor")
        check("confirm_concept retypes",
              api.show_concept(db, manuscript, "Gravity")["node"]["kind"] == "metaphor")
        check("definition is not a concept-node kind",
              "definition" not in concepts.NODE_KINDS)
        check("defines remains a graph relation",
              "defines" in extraction.VALID_RELATIONS)
        check("definition remains a guidance kind",
              "definition" in guidance_module.GUIDANCE_KINDS)
        try:
            api.add_concept(db, manuscript, "Legacy Definition", kind="definition")
            check("add_concept rejects the retired definition kind", False)
        except ValueError as err:
            check("add_concept rejects the retired definition kind",
                  "invalid concept kind 'definition'" in str(err))
        try:
            api.confirm_concept(db, manuscript, "Gravity", kind="definition")
            check("confirm_concept rejects the retired definition kind", False)
        except ValueError as err:
            check("confirm_concept rejects the retired definition kind",
                  "invalid concept kind 'definition'" in str(err))

        retired_twin = ko_fields("cn")
        retired_twin.update(
            manuscript_id=manuscript["id"], name="Gravity", kind="concept",
            status="retired", introduced_in=None, notes="retired twin",
            aliases="[]", source_id=db.source("author"),
        )
        db.insert("concept_nodes", retired_twin)
        check("exact-name lookup prefers the live concept over a retired twin",
              concepts.get_concept(db, manuscript["id"], "Gravity")["id"]
              != retired_twin["id"])

        # --- concept/edge/proposal curation via api (the CLI has its own
        # code paths for these, so api.* itself was never exercised) ---
        api.add_concept(db, manuscript, "Scratch")
        retired = api.retire_concept(db, manuscript, "Scratch")
        check("retire_concept retires a concept with no edges",
              retired == {"name": "Scratch", "edges_retired": 0})
        try:
            api.retire_concept(db, manuscript, "Scratch")
            check("retiring an already-retired concept raises", False)
        except ValueError as err:
            check("retiring an already-retired concept raises", "already retired" in str(err))
        try:
            api.retire_concept(db, manuscript, "Nobody")
            check("retiring an unknown concept raises", False)
        except LookupError:
            check("retiring an unknown concept raises", True)

        from authorlm import concepts as cg

        api.add_concept(db, manuscript, "Freedom")
        inferred = cg.link_concepts(db, manuscript["id"], "Gravity", "supports", "Freedom",
                                    status="inferred")
        confirmed = api.confirm_edge(db, manuscript, inferred["id"][:8])
        check("confirm_edge declares an inferred edge",
              confirmed == {"from_name": "Gravity", "relation": "supports", "to_name": "Freedom"})
        check("confirm_edge persisted the status change",
              api.show_concept(db, manuscript, "Freedom")["edges"][0]["status"] == "declared")
        # D4: an individual author verdict on an edge must record evidence,
        # exactly like confirm_concept/retire_concept already do — and the
        # row must be attributed to the author (edge_triage is not in
        # SYSTEM_EVIDENCE_TYPES), never to the system.
        confirm_edge_evidence = db.one(
            "SELECT e.evidence_type, e.signal, e.supports_belief, s.kind "
            "AS source_kind FROM evidence e JOIN sources s ON s.id = e.source_id "
            "WHERE e.manuscript_id = ? AND e.evidence_type = 'edge_triage' "
            "ORDER BY e.created_at DESC LIMIT 1", (manuscript["id"],))
        check("confirm_edge records edge_triage evidence",
              confirm_edge_evidence is not None, str(confirm_edge_evidence))
        check("confirm_edge evidence carries signal 'confirmed'",
              confirm_edge_evidence["signal"] == "confirmed", str(confirm_edge_evidence))
        check("confirm_edge evidence is attributed to the author",
              confirm_edge_evidence["source_kind"] == "author", str(confirm_edge_evidence))
        check("confirm_edge evidence does not target a belief",
              confirm_edge_evidence["supports_belief"] is None, str(confirm_edge_evidence))
        inferred2 = cg.link_concepts(db, manuscript["id"], "Freedom", "supports", "Choice",
                                     status="inferred")
        rejected = api.reject_edge(db, manuscript, inferred2["id"][:8])
        check("reject_edge rejects an inferred edge",
              rejected == {"from_name": "Freedom", "relation": "supports", "to_name": "Choice"})
        reject_edge_evidence = db.one(
            "SELECT e.evidence_type, e.signal, e.supports_belief, s.kind "
            "AS source_kind FROM evidence e JOIN sources s ON s.id = e.source_id "
            "WHERE e.manuscript_id = ? AND e.evidence_type = 'edge_triage' "
            "ORDER BY e.created_at DESC LIMIT 1", (manuscript["id"],))
        check("reject_edge records edge_triage evidence",
              reject_edge_evidence is not None, str(reject_edge_evidence))
        check("reject_edge evidence carries signal 'rejected'",
              reject_edge_evidence["signal"] == "rejected", str(reject_edge_evidence))
        check("reject_edge evidence is attributed to the author",
              reject_edge_evidence["source_kind"] == "author", str(reject_edge_evidence))
        try:
            api.confirm_edge(db, manuscript, "zzzzzzzz")
            check("confirming an unknown edge prefix raises", False)
        except LookupError:
            check("confirming an unknown edge prefix raises", True)

        from authorlm import proposals as prop

        open_proposal = prop.create(db, manuscript["id"], "revival", "Scratch",
                                    {"name": "Scratch"})
        listed = api.list_proposals(db, manuscript)["open"]
        check("list_proposals surfaces an open proposal with summary/details",
              any(p["id"] == open_proposal["id"] and "revive retired concept" in p["summary"]
                  for p in listed))
        try:
            api.resolve_proposal(db, manuscript, open_proposal["id"][:8], "bogus")
            check("resolving a proposal with an unknown action raises", False)
        except ValueError:
            check("resolving a proposal with an unknown action raises", True)
        try:
            api.resolve_proposal(db, manuscript, "zzzzzzzz", "dismiss")
            check("resolving an unknown proposal prefix raises", False)
        except LookupError:
            check("resolving an unknown proposal prefix raises", True)
        resolved = api.resolve_proposal(db, manuscript, open_proposal["id"][:8], "dismiss",
                                        "not needed")
        check("resolve_proposal dismisses and returns a message",
              resolved["proposal_id"] == open_proposal["id"] and resolved["message"])
        check("dismissed proposal no longer appears in list_proposals",
              not any(p["id"] == open_proposal["id"]
                      for p in api.list_proposals(db, manuscript)["open"]))

        # --- briefing: focus areas and TOC completeness (§21.6, §20.3) ---
        api.link_concepts(db, manuscript, "Choice", "motivates", "Freedom")
        (ms / "02-extra.md").write_text("# Extra\n\nSome unrelated prose.\n")
        # toc.toml, not toc.md: the TOC became structural TOML after this
        # test was written, and a stray toc.md is simply not a TOC — the
        # assertion below then reads an empty unlisted set and passes
        # vacuously in the wrong direction.
        (ms / "toc.toml").write_text('[[chapter]]\nfile = "01-choice.md"\n')
        api.collect(db, manuscript, {})
        briefing2 = api.get_briefing(db, manuscript)
        check("briefing surfaces an unrealized concept's dependents as a focus area",
              any(area["node"]["name"] == "Freedom"
                  and any(r["name"] == "Choice" for r in area["related"])
                  for area in briefing2["focus_areas"]),
              briefing2["focus_areas"])
        check("briefing flags a manuscript file missing from toc.toml",
              briefing2["toc_unlisted"] == ["02-extra.md"], briefing2["toc_unlisted"])
        # Restore the fixture: this block borrows the shared manuscript, and
        # the Doc tests below assert exact tab sets and tab ORDER, so an
        # extra content file left behind cascades into unrelated failures.
        (ms / "02-extra.md").unlink()
        (ms / "toc.toml").unlink()
        api.collect(db, manuscript, {})

        closed = api.close_session(db, manuscript)
        check("close_session ends the active session",
              api.status(db, manuscript)["session"] is None
              and closed["id"] == session["id"])

        # --- idle session expiry ---
        check("expire_idle_session is a no-op with no active session",
              api.expire_idle_session(db, manuscript, {}) is None)
        idle_session, _ = api.ensure_session(db, manuscript)
        db.update("sessions", idle_session["id"],
                  {"started_at": "2020-01-01T00:00:00.000000Z"})
        check("expire_idle_session honors a configured idle_hours threshold",
              api.expire_idle_session(
                  db, manuscript, {"session": {"idle_hours": 999999}}) is None)
        expired = api.expire_idle_session(db, manuscript, {})
        check("expire_idle_session closes a session idle past the default threshold",
              expired is not None
              and expired["session"]["id"] == idle_session["id"]
              and expired["idle_hours"] > 3
              and api.status(db, manuscript)["session"] is None)

        idle_session2, _ = api.ensure_session(db, manuscript)
        db.update("sessions", idle_session2["id"], {"started_at": "not-a-date"})
        check("expire_idle_session tolerates an unparseable timestamp",
              api.expire_idle_session(db, manuscript, {}) is None)
        api.close_session(db, manuscript)

        # --- Google Docs bridge: normalizer + stub-service round trip ---
        from authorlm.gdocs import normalize_markdown, pull_doc, push_doc

        messy = ("# Title\r\n\r\n\r\n\r\nSome \\-escaped \\. text here.   \n"
                 "*  a bullet\n\nEnds with nbsp.")
        clean = normalize_markdown(messy)
        check("normalizer canonicalizes Docs-export quirks",
              clean == ("# Title\n\nSome -escaped . text here.\n"
                        "- a bullet\n\nEnds with nbsp.\n"), repr(clean))
        check("normalizer is idempotent", normalize_markdown(clean) == clean)

        # An empty heading paragraph in the Doc (e.g. a blank Subtitle
        # line) must be dropped — never merged into the next heading
        # ('## ##', it-7b127d3164ff).
        empty_heading = ("# **Title**\n\n## \n\n"
                         "## Septem Plus Sermones Ad Mortuos, 2026\n")
        check("normalizer drops empty headings instead of merging",
              normalize_markdown(empty_heading)
              == ("# **Title**\n\n"
                  "## Septem Plus Sermones Ad Mortuos, 2026\n"),
              repr(normalize_markdown(empty_heading)))

        # --- illustration slots: tag grammar + scan-derived registry ---
        from authorlm.illus import (desc_hash, parse_tag, slot_report,
                                    strip_dangling)

        tag = parse_tag(
            "[Illustration: a tracker kneeling | caption: The tracker]")
        check("illustration tag grammar parses prompt and caption",
              tag["prompt"] == "a tracker kneeling"
              and tag["caption"] == "The tracker", str(tag))
        check("caption and whitespace stay outside slot identity",
              desc_hash("a tracker kneeling")
              == desc_hash("a  tracker\tkneeling")
              and parse_tag("[Illustration: a tracker kneeling]")["caption"]
              is None
              and parse_tag("prose mentioning [Illustration: x] inline")
              is None)

        scratch = root / "illus-scratch"
        scratch.mkdir()
        (scratch / "ch.md").write_text(
            "# C\n\n[Illustration: two turns in opposite order]\n")
        rep = slot_report(scratch)
        check("scan reports an unrendered slot with file and line",
              rep["unrendered"] == [{"file": "ch.md", "line": 3,
                                     "prompt": "two turns in opposite order"}]
              and not rep["orphaned"], str(rep))
        ill_dir = scratch / "_illustrations"
        ill_dir.mkdir()
        h = desc_hash("two turns in opposite order")
        candidate = f"two-turns-in-opposite-{h}-0000-01.png"
        (ill_dir / candidate).write_bytes(b"")
        check("a matching candidate marks the slot rendered",
              not slot_report(scratch)["unrendered"])
        (scratch / "ch.md").write_text(
            "# C\n\n[Illustration: two turns, reworded]\n")
        rep = slot_report(scratch)
        check("editing the prompt un-renders the slot and orphans the file",
              rep["unrendered"][0]["prompt"] == "two turns, reworded"
              and rep["orphaned"] == [candidate], str(rep))

        # A `⇢` present but not matching the ref grammar (e.g. it doesn't
        # end in a bare 'name.md') must be reported loudly, never
        # silently folded into the prompt text as if it were an ordinary
        # undecorated description (it-d70ece778f55: a corrupted doc-pull
        # round trip produced exactly this shape — an excerpt spliced to
        # a stray '.md' name immediately followed by a '.png' name, with
        # no whitespace, so the ref half fails _REF and used to vanish
        # without a trace).
        garbled = ("The interior of a temple ⇢ "
                  "a-field-of-fallen.mda-square-temple-of.png")
        tag = parse_tag(f"[Illustration: {garbled}]")
        check("a malformed ⇢ ref is flagged, not silently swallowed",
              tag["malformed_ref"] is True and tag["ref"] is None
              and tag["prompt"] == garbled, str(tag))
        check("a well-formed ⇢ ref is never flagged as malformed",
              parse_tag("[Illustration: excerpt ⇢ some-file.md]")
              ["malformed_ref"] is False)
        check("a plain undecorated description is never flagged",
              parse_tag("[Illustration: plain description]")
              ["malformed_ref"] is False)
        (scratch / "ch.md").write_text(f"# C\n\n[Illustration: {garbled}]\n")
        rep = slot_report(scratch)
        check("slot_report surfaces the malformed ref as its own signal",
              rep["malformed_refs"] == [{"file": "ch.md", "line": 3,
                                         "prompt": garbled}],
              str(rep))

        dirty = ("Prose kept. ![][image1]\n\n"
                 "[image1]: <data:image/png;base64,abc>\n")
        clean, refs = strip_dangling(dirty)
        check("dangling Doc image refs strip cleanly and are reported",
              "image1" not in clean and "Prose kept." in clean
              and len(refs) == 2, repr((clean, refs)))

        # A collect surfaces unrendered slots so the author never has to
        # remember to ask ("new illustrations found").
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n[Illustration: choice as a forking path]\n")
        report = api.collect(db, manuscript, {})
        check("collect reports new illustration slots",
              report["illustrations"]["unrendered"][0]["prompt"]
              == "choice as a forking path"
              and api.compact_collect(report)["illustrations"]
              == report["illustrations"], str(report.get("illustrations")))

        # --- illus rendering: candidates, pinning, style law, prune ---
        import struct
        import zlib

        from authorlm import illus as illus_mod
        from authorlm.revisions import (read_manuscript_files
                                        as read_files_for_test,
                                        strip_embed_lines
                                        as strip_embed_lines_for_test)

        def tiny_png() -> bytes:
            def chunk(t, d):
                return (struct.pack(">I", len(d)) + t + d
                        + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
            return (b"\x89PNG\r\n\x1a\n"
                    + chunk(b"IHDR",
                            struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
                    + chunk(b"IEND", b""))

        gen_calls = []

        def fake_gen(prompt, input_png):
            gen_calls.append((prompt, input_png))
            return tiny_png()

        slot = illus_mod.find_slot(ms, "forking path")[0]
        h = slot["desc_hash"]
        r1 = illus_mod.render_slot(db, manuscript, slot, {},
                                   generator=fake_gen)
        first = f"choice-as-a-forking-{h}-0000-01.png"
        choice_text = (ms / "01-choice.md").read_text()
        check("render writes candidate 01 and embeds the first render",
              r1["written"] == [first] and not r1["had_embed"]
              and f"![](_illustrations/{first})" in choice_text, str(r1))
        blob = (ms / "_illustrations" / first).read_bytes()
        check("candidate PNG carries the prompt as metadata",
              b"authorlm:prompt" in blob
              and "choice as a forking path".encode() in blob)
        check("embed lines are invisible to observation",
              "_illustrations" not in
              read_files_for_test(ms)["01-choice.md"]
              and api.collect(db, manuscript, {}).get("unchanged") is True)

        r2 = illus_mod.render_slot(db, manuscript, slot, {}, count=2,
                                   generator=fake_gen)
        check("re-render appends numbered candidates, embed stays pinned",
              [n[-6:-4] for n in r2["written"]] == ["02", "03"]
              and r2["had_embed"]
              and illus_mod.embed_target(
                  (ms / "01-choice.md").read_text(), h) == first, str(r2))

        api.add_style_law(db, manuscript, "illustration",
                              "woodcut, high-contrast linework",
                              file="01-choice.md")
        law = illus_mod.illustration_law(db, manuscript["id"], "01-choice.md")
        shash = illus_mod.style_hash(law)
        check("illustration law renders from illustration-aspect elements",
              "woodcut" in law and shash != "0000"
              and illus_mod.style_hash("") == "0000")
        status = [s for s in illus_mod.slot_status(db, manuscript)
                  if s["file"] == "01-choice.md"][0]
        check("a new illustration rule marks the embedded slot stale-style",
              status["state"] == "stale-style"
              and status["candidates"] == 3, str(status))

        r3 = illus_mod.render_slot(db, manuscript, slot, {}, from_n=1,
                                   generator=fake_gen)
        check("render --from passes the source image and the new law",
              gen_calls[-1][1] is not None
              and law in gen_calls[-1][0]
              and r3["written"] == [
                  f"choice-as-a-forking-{h}-{shash}-04.png"], str(r3))
        assembled = illus_mod.effective_prompt(db, manuscript, slot)
        check("effective_prompt is byte-identical to what the renderer "
              "sends",
              assembled["composed"] == gen_calls[-1][0]
              and assembled["style_hash"] == shash
              and assembled["law"] and assembled["law"] in
              assembled["composed"], str(assembled))

        illus_mod.set_embed(ms / "01-choice.md", h, r3["written"][0])
        removed = illus_mod.prune(manuscript)
        check("prune removes every unpicked candidate, keeps the pick",
              sorted(removed) == sorted([first,
                  f"choice-as-a-forking-{h}-0000-02.png",
                  f"choice-as-a-forking-{h}-0000-03.png"])
              and (ms / "_illustrations" / r3["written"][0]).exists(),
              str(removed))

        # --- capture_embeds/reembed: the pick survives a Doc round trip ---
        embed_root = root / "illus-embed-scratch"
        (embed_root / "_illustrations").mkdir(parents=True)
        (embed_root / "ch.md").write_text(
            "# C\n\n[Illustration: a lone tracker]\n")
        eh = illus_mod.desc_hash("a lone tracker")
        cand1 = f"a-lone-tracker-{eh}-0000-01.png"
        cand2 = f"a-lone-tracker-{eh}-0000-02.png"
        (embed_root / "_illustrations" / cand1).write_bytes(b"")
        (embed_root / "_illustrations" / cand2).write_bytes(b"")
        bare = (embed_root / "ch.md").read_text()

        never_picked = illus_mod.reembed(bare, embed_root)
        check("a never-picked slot falls back to the newest candidate",
              illus_mod.embed_target(never_picked, eh) == cand2, never_picked)

        pick = {eh: cand1}
        embedded = illus_mod.reembed(bare, embed_root, prior=pick)
        check("a prior pick wins over the newest candidate",
              illus_mod.embed_target(embedded, eh) == cand1, embedded)
        check("capture_embeds recovers the exact picked candidate",
              illus_mod.capture_embeds(embedded) == pick,
              illus_mod.capture_embeds(embedded))
        check("reembed on already-embedded text never doubles the line",
              illus_mod.reembed(embedded, embed_root, prior=pick) == embedded,
              illus_mod.reembed(embedded, embed_root, prior=pick))

        (embed_root / "_illustrations" / cand1).unlink()
        fallback = illus_mod.reembed(bare, embed_root, prior=pick)
        check("a pick whose file is gone falls back to the newest candidate",
              illus_mod.embed_target(fallback, eh) == cand2, fallback)

        # In-memory Drive + Docs fake for the tabbed master-Doc model:
        # one object serves as both `service` and `docs_service`. Master
        # Doc state is tabs of literal markdown; the temp-import Doc is
        # modeled as one plain-text paragraph per line, and export renders
        # each tab under a '# **<title>**' heading — the same shape
        # split_tabbed_export expects from the real API.
        from authorlm.gdocs import FOLDER_MIME, doc_status

        class FakeRequest:
            def __init__(self, payload):
                self._payload = payload

            def execute(self):
                return self._payload

        class FakeFiles:
            def __init__(self, state):
                self.state = state

            def create(self, body=None, media_body=None, fields=None):
                self.state["counter"] += 1
                fid = f"doc-{self.state['counter']}"
                if media_body is not None:  # markdown import (temp/export)
                    self.state["uploads"][fid] = media_body.getbytes(
                        0, media_body.size()).decode("utf-8")
                elif (body or {}).get("mimeType") == FOLDER_MIME:
                    self.state["folders"].append(fid)
                else:  # a native Doc, born with the blank default tab
                    self.state["tab_counter"] += 1
                    tab_id = f"tab-{self.state['tab_counter']}"
                    self.state["docs"][fid] = [
                        {"id": tab_id, "title": "Tab 1", "text": ""}]
                return FakeRequest({"id": fid})

            def update(self, fileId=None, media_body=None):
                self.state["uploads"][fileId] = media_body.getbytes(
                    0, media_body.size()).decode("utf-8")
                return FakeRequest({})

            def delete(self, fileId=None):
                self.state["uploads"].pop(fileId, None)
                return FakeRequest({})

            def export(self, fileId=None, mimeType=None):
                def as_markdown(text):
                    # The real exporter separates doc paragraphs with
                    # blank lines; the fake stores one paragraph per
                    # line (empties tolerated for legacy set_tab text).
                    paras = [ln for ln in text.split("\n") if ln.strip()]
                    return "\n\n".join(paras) + ("\n" if paras else "")

                flat = self.state["docs"][fileId]

                def dfs(parent_id):
                    # The real export concatenates tabs in document tab
                    # order — a DFS of the tab tree (container, then its
                    # nested children, then the next root sibling) — NOT
                    # raw creation order, which can differ once a tab is
                    # nested under a parent created earlier (it-x7-2:
                    # this used to silently diverge from documents().get(),
                    # which already walks the tree correctly).
                    ordered = []
                    for t in flat:
                        if t.get("parent") == parent_id:
                            ordered.append(t)
                            ordered.extend(dfs(t["id"]))
                    return ordered

                whole = "\n".join(
                    f"# **{t['title']}**\n\n{as_markdown(t['text'])}"
                    for t in dfs(None))
                return FakeRequest(whole.encode("utf-8"))

        class FakeDocuments:
            def __init__(self, state):
                self.state = state

            def get(self, documentId=None, includeTabsContent=None):
                if documentId in self.state["uploads"]:
                    content = [{"paragraph": {"elements": [
                        {"textRun": {"content": line + "\n"}}]}}
                        for line in
                        self.state["uploads"][documentId].splitlines()]
                    return FakeRequest({"body": {"content": content}})
                def paragraphs(text):
                    items, pos = [], 1
                    for line in text.split("\n"):
                        content = line + "\n"
                        items.append({
                            "startIndex": pos,
                            "endIndex": pos + len(content),
                            "paragraph": {"elements": [
                                {"startIndex": pos,
                                 "endIndex": pos + len(content),
                                 "textRun": {"content": content}}]}})
                        pos += len(content)
                    return items

                flat = self.state["docs"][documentId]

                def node(t):
                    return {"tabProperties": {"tabId": t["id"],
                                              "title": t["title"]},
                            "documentTab": {"body": {"content":
                                paragraphs(t["text"]) if t["text"] else []}},
                            "childTabs": [node(c) for c in flat
                                          if c.get("parent") == t["id"]]}

                return FakeRequest({"tabs": [node(t) for t in flat
                                             if not t.get("parent")]})

            def batchUpdate(self, documentId=None, body=None):
                tabs = self.state["docs"][documentId]

                def tab(tid):
                    return next(t for t in tabs if t["id"] == tid)

                replies = []
                for req in (body or {}).get("requests", []):
                    if "addDocumentTab" in req:
                        self.state["tab_counter"] += 1
                        props = req["addDocumentTab"]["tabProperties"]
                        new = {"id": f"tab-{self.state['tab_counter']}",
                               "title": props["title"], "text": "",
                               "parent": props.get("parentTabId")}
                        tabs.append(new)
                        replies.append({"addDocumentTab": {
                            "tabProperties": {"tabId": new["id"]}}})
                        continue
                    replies.append({})
                    if "deleteTab" in req:
                        doomed = tab(req["deleteTab"]["tabId"])
                        gone = {doomed["id"]}
                        tabs.remove(doomed)
                        while True:  # the API deletes the whole subtree
                            orphans = [t for t in tabs
                                       if t.get("parent") in gone]
                            if not orphans:
                                break
                            for t in orphans:
                                gone.add(t["id"])
                                tabs.remove(t)
                    elif "updateDocumentTabProperties" in req:
                        props = req["updateDocumentTabProperties"][
                            "tabProperties"]
                        moved = tab(props["tabId"])
                        if "parentTabId" in props:
                            moved["parent"] = props["parentTabId"] or None
                        tabs.remove(moved)
                        tabs.insert(props["index"], moved)
                    elif "deleteContentRange" in req:
                        tab(req["deleteContentRange"]["range"]
                            ["tabId"])["text"] = ""
                    elif "insertText" in req:
                        target = tab(req["insertText"]["location"]["tabId"])
                        loc = req["insertText"]["location"]
                        if "index" in loc:
                            i = loc["index"] - 1
                            target["text"] = (target["text"][:i]
                                              + req["insertText"]["text"]
                                              + target["text"][i:])
                        else:
                            target["text"] += req["insertText"]["text"]
                return FakeRequest({"replies": replies})

        class FakeComments:
            def __init__(self, state):
                self.state = state

            def list(self, fileId=None, pageToken=None, includeDeleted=None,
                     fields=None):
                return FakeRequest({"comments": [
                    c for c in self.state["comments"].values()
                    if not c.get("resolved")]})

        class FakeReplies:
            def __init__(self, state):
                self.state = state

            def create(self, fileId=None, commentId=None, body=None,
                       fields=None):
                self.state["reply_counter"] += 1
                reply = {"id": f"r-{self.state['reply_counter']}",
                         "content": body["content"],
                         "author": {"displayName": "author"}}
                self.state["comments"][commentId]["replies"].append(reply)
                if body.get("action") == "resolve":
                    self.state["comments"][commentId]["resolved"] = True
                return FakeRequest({"id": reply["id"]})

        class FakeGoogle:
            def __init__(self):
                self.state = {"counter": 0, "tab_counter": 0,
                              "comments": {}, "reply_counter": 0,
                              "folders": [], "docs": {}, "uploads": {}}
                self._files = FakeFiles(self.state)
                self._documents = FakeDocuments(self.state)

            def files(self):
                return self._files

            def documents(self):
                return self._documents

            def comments(self):
                return FakeComments(self.state)

            def replies(self):
                return FakeReplies(self.state)

            def add_comment(self, comment_id, quoted, content):
                self.state["comments"][comment_id] = {
                    "id": comment_id, "content": content,
                    "quotedFileContent": {"value": quoted},
                    "resolved": False, "replies": [],
                    "author": {"displayName": "author"},
                    "createdTime": "2026-08-08T00:00:00Z"}

            def author_reply(self, comment_id, content):
                self.state["comments"][comment_id]["replies"].append(
                    {"content": content,
                     "author": {"displayName": "author"}})

            def set_tab(self, title, text):  # simulate an edit in Docs
                for tabs in self.state["docs"].values():
                    for t in tabs:
                        if t["title"] == title:
                            t["text"] = text

        stub = FakeGoogle()
        pushed = push_doc(db, manuscript, "01-choice.md",
                          service=stub, docs_service=stub)
        check("push creates folder, master Doc, and the file's tab",
              pushed["created"] and pushed["doc_id"] == "doc-2"
              and "tab=" in pushed["url"])
        manuscript = api.get_manuscript(db)  # refresh metadata
        status = doc_status(db, manuscript)
        check("push mapping persisted: checkout, folder, master, tab id",
              status["01-choice.md"]["checked_out"] is True
              and status["01-choice.md"]["tab_id"]
              and status["_folder_id"] == "doc-1"
              and status["_master_id"] == "doc-2")
        master = stub.state["docs"]["doc-2"]
        tab_text = {t["title"]: t["text"] for t in master}
        check("master carries the container tab, one tab per file, and "
              "the manifest; default tab retired",
              [t["title"] for t in master]
              == ["book", "01-choice.md", "manifest"],
              str([t["title"] for t in master]))
        def as_tab(md: str) -> str:
            # A pushed tab carries no blank paragraphs (it-69b61b6d7fa2):
            # markdown's separator lines are dropped in transplant.
            return "\n".join(ln for ln in md.split("\n") if ln.strip()) + "\n"

        check("push transplants the file's markdown into its tab, "
              "embed lines stripped, blank separators dropped",
              tab_text["01-choice.md"]
              == as_tab(strip_embed_lines_for_test(
                  (ms / "01-choice.md").read_text()))
              and "_illustrations" not in tab_text["01-choice.md"]
              and "[Illustration:" in tab_text["01-choice.md"]
              and not stub.state["uploads"])  # temp import Doc deleted
        check("manifest tab names the manuscript and doc incarnation",
              "Manuscript: book" in tab_text["manifest"]
              and "Doc version: 1" in tab_text["manifest"])
        stub.set_tab("01-choice.md",
                     "# Title\n\nEdited in Docs \\- with escapes.\n")
        pulled = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("pull writes normalized export and clears checkout",
              pulled["changed"] == ["01-choice.md"]
              and (ms / "01-choice.md").read_text()
              == "# Title\n\nEdited in Docs - with escapes.\n"
              and doc_status(db, manuscript)["01-choice.md"]["checked_out"]
              is False)
        pushed2 = push_doc(db, manuscript, "01-choice.md",
                           service=stub, docs_service=stub)
        check("second push reuses the master Doc (no new folder or Doc)",
              not pushed2["created"] and pushed2["doc_id"] == "doc-2"
              and stub.state["folders"] == ["doc-1"]
              and list(stub.state["docs"]) == ["doc-2"])

        # --- sync_tab_structure: push-direction TOC↔tab orchestration ---
        from authorlm.gdocs import _mapping, _save_mapping, sync_tab_structure

        manuscript = api.get_manuscript(db)
        no_toc = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure skips when toc.toml is absent",
              no_toc == {"skipped": "no toc.toml"}, str(no_toc))
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n')
        synced = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure records insync when TOC matches Doc tabs",
              synced == {"insync": True}, str(synced))
        meta = _mapping(db, manuscript)
        check("insync sync persists _tab_structure as the agreed base",
              meta["gdocs"].get("_tab_structure")
              == [["01-choice.md", None]],
              str(meta["gdocs"].get("_tab_structure")))

        (ms / "02-fork.md").write_text("# Fork\n\nAnother essay.\n")
        push_doc(db, manuscript, "02-fork.md",
                 service=stub, docs_service=stub)
        manuscript = api.get_manuscript(db)

        # X7-2 regression: split_tabbed_export's documented contract says
        # "content headings, even H1s, pass through untouched" unless they
        # name a known tab — but a prose H1 that happens to repeat some
        # OTHER tab's title used to be treated as that tab's boundary too,
        # truncating the essay at the collision point. Put such a heading
        # inside 01-choice.md's prose, matching the manifest tab's title
        # (a real boundary that sits further down the true tab order, past
        # 02-fork.md — not the essay's own immediate neighbor), and prove
        # pull keeps everything after it.
        collision_body = (
            "# Title\n\n"
            "manifest\n\n"
            "Prose survives.\n\n"
            "# manifest\n\n"
            "More prose after the collision, never truncated.\n")
        stub.set_tab("01-choice.md", collision_body)
        collided = pull_doc(db, manuscript, "01-choice.md", service=stub,
                            docs_service=stub)
        pulled_text = (ms / "01-choice.md").read_text()
        check("a prose heading matching another tab's title never "
              "truncates the essay at the collision point (it-x7-2)",
              collided["changed"] == ["01-choice.md"]
              and "More prose after the collision, never truncated."
              in pulled_text, str(collided) + "\n" + pulled_text)
        check("the colliding heading itself passed through as ordinary "
              "content, not consumed as a boundary",
              "# manifest" in pulled_text, pulled_text)
        check("the real manifest tab is untouched by the collision",
              "Manuscript: book" in
              next(t["text"] for t in stub.state["docs"]["doc-2"]
                   if t["title"] == "manifest"))

        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "02-fork.md"\n')
        base_sync = sync_tab_structure(db, manuscript, stub)
        check("sync after adding a linked chapter lands insync (or moves)",
              base_sync.get("insync") is True
              or isinstance(base_sync.get("moved"), int),
              str(base_sync))
        # Force a conflict: Doc order and TOC both diverge from the base.
        meta = _mapping(db, manuscript)
        meta["gdocs"]["_tab_structure"] = [
            ["01-choice.md", None], ["02-fork.md", None]]
        _save_mapping(db, manuscript, meta)
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "02-fork.md"\n\n'
            '[[chapter]]\nfile = "01-choice.md"\n')
        tabs = stub.state["docs"]["doc-2"]
        # Put 02-fork before 01-choice among root-level md tabs by
        # rebuilding flat order while leaving container/manifest alone.
        md_tabs = [t for t in tabs if t["title"].endswith(".md")]
        others = [t for t in tabs if not t["title"].endswith(".md")]
        # Doc side: 02 then 01 (matches neither base nor the swapped TOC
        # wait — TOC is also 02 then 01. Use a third ordering via parent
        # so Doc ≠ TOC ≠ base.
        by_title = {t["title"]: t for t in md_tabs}
        by_title["02-fork.md"]["parent"] = by_title["01-choice.md"]["id"]
        stub.state["docs"]["doc-2"] = others + [
            by_title["01-choice.md"], by_title["02-fork.md"]]
        conflicted = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure refuses when Doc and TOC both moved",
              conflicted.get("skipped", "").startswith(
                  "Doc tab order also changed"),
              str(conflicted))

        # No master: skip before any Docs call.
        meta = _mapping(db, manuscript)
        master = meta["gdocs"].pop("_master_id")
        _save_mapping(db, manuscript, meta)
        no_master = sync_tab_structure(db, manuscript, stub)
        check("sync_tab_structure skips when there is no master Doc",
              no_master == {"skipped": "no master doc"}, str(no_master))
        meta["gdocs"]["_master_id"] = master
        # Drop the second chapter so later single-file reconcile/export
        # assertions stay focused on 01-choice.md.
        meta["gdocs"].pop("02-fork.md", None)
        meta["gdocs"]["_tab_structure"] = [["01-choice.md", None]]
        _save_mapping(db, manuscript, meta)
        stub.state["docs"]["doc-2"] = [
            t for t in stub.state["docs"]["doc-2"]
            if t["title"] != "02-fork.md"]
        (ms / "02-fork.md").unlink(missing_ok=True)
        (ms / "toc.toml").unlink(missing_ok=True)

        # Two-sided edit: local changed since push AND the tab differs
        # from what was pushed → conflict, skipped unless forced.
        (ms / "01-choice.md").write_text("# Title\n\nLocal divergence.\n")
        manuscript = api.get_manuscript(db)
        stub.set_tab("01-choice.md", "# Title\n\nEdited in Docs again.\n")
        report = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("two-sided edits conflict, file untouched",
              report["conflicts"] == ["01-choice.md"]
              and "Local divergence" in (ms / "01-choice.md").read_text(),
              str(report))
        forced = pull_doc(db, manuscript, "01-choice.md", service=stub,
                          force=True)
        check("pull --force takes the Doc's version",
              forced["changed"] == ["01-choice.md"]
              and "Edited in Docs again" in (ms / "01-choice.md").read_text())

        # A Doc-pasted image exports as a dangling ![][imageN] ref: the
        # pull strips it (the bridge cannot transport images) and warns.
        stub.set_tab("01-choice.md",
                     "# Title\n\nProse kept. ![][image9]\n\n"
                     "[image9]: <data:image/png;base64,abc>\n")
        pulled_img = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("pull strips Doc-pasted image refs and warns",
              pulled_img["dangling_images"]["01-choice.md"]
              and "image9" not in (ms / "01-choice.md").read_text()
              and "Prose kept." in (ms / "01-choice.md").read_text(),
              str(pulled_img))

        # Embed round trip: the embed line never reaches the Doc, and a
        # pull restores the pinned pick under its tag.
        picked_name = r3["written"][0]
        (ms / "01-choice.md").write_text(
            "# Title\n\n[Illustration: choice as a forking path]\n"
            f"![](_illustrations/{picked_name})\n\nProse below.\n")
        push_doc(db, manuscript, "01-choice.md",
                 service=stub, docs_service=stub)
        tab_now = next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == "01-choice.md")
        check("push keeps the tag but never the embed line",
              "[Illustration: choice as a forking path]" in tab_now
              and "_illustrations" not in tab_now, tab_now)
        stub.set_tab("01-choice.md", tab_now.replace(
            "Prose below.", "Prose below, edited in the Doc."))
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        round_tripped = (ms / "01-choice.md").read_text()
        check("pull re-inserts the pinned embed under its tag",
              f"[Illustration: choice as a forking path]\n"
              f"![](_illustrations/{picked_name})" in round_tripped
              and "edited in the Doc" in round_tripped, round_tripped)

        # --- session-start reconciliation: all four outcomes ---
        from authorlm.gdocs import reconcile

        manuscript = api.get_manuscript(db)
        report = reconcile(db, manuscript, stub)
        check("reconcile: in sync after a pull",
              report["in_sync"] == ["01-choice.md"] and not report["conflicts"],
              str(report))

        (ms / "01-choice.md").write_text("# Title\n\nLocal-only progress.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: local-only change without a Docs service is pending",
              report["pending_push"] == ["01-choice.md"], str(report))
        report = reconcile(db, manuscript, stub, docs_service=stub)
        check("reconcile: only-local change auto-pushes",
              report["pushed"] == ["01-choice.md"]
              and next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == "01-choice.md")
              == "# Title\nLocal-only progress.\n", str(report))
        # The tab now reflects the push; the next reconcile sees sync.
        report = reconcile(db, manuscript, stub)
        check("reconcile: after push, next start is in sync",
              report["in_sync"] == ["01-choice.md"], str(report))
        stub.set_tab("01-choice.md", "# Title\n\nDoc-only revision now.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: only-Doc change auto-pulls",
              report["pulled"] == ["01-choice.md"]
              and "Doc-only revision" in (ms / "01-choice.md").read_text(),
              str(report))
        (ms / "01-choice.md").write_text("# Title\n\nBoth sides now differ.\n")
        stub.set_tab("01-choice.md", "# Title\n\nDoc went another way.\n")
        report = reconcile(db, manuscript, stub)
        check("reconcile: two-sided edits conflict, files untouched",
              report["conflicts"] == ["01-choice.md"]
              and "Both sides now differ" in (ms / "01-choice.md").read_text(),
              str(report))

        # Missing tab ≠ empty Doc: a renamed/deleted export section must
        # never auto-pull "" over a local file that still matches the base.
        safe = "# Title\n\nSafe prose that must survive a missing tab.\n"
        (ms / "01-choice.md").write_text(safe)
        push_doc(db, manuscript, "01-choice.md",
                 service=stub, docs_service=stub)
        essay_tab = next(t for t in stub.state["docs"]["doc-2"]
                         if t["title"] == "01-choice.md")
        essay_tab["title"] = "01-choice.md.ORPHAN"
        report = reconcile(db, manuscript, stub)
        check("reconcile: missing Doc tab never wipes local",
              (ms / "01-choice.md").read_text() == safe
              and report["pulled"] == []
              and any(e["file"] == "01-choice.md" for e in report["errors"]),
              str(report))
        # Restore the Doc/local body later margin-thread checks expect —
        # the missing-tab push left "Safe prose…" which cannot attribute
        # a quote of "Doc went another way".
        essay_tab["title"] = "01-choice.md"
        restored = "# Title\n\nDoc went another way.\n"
        stub.set_tab("01-choice.md", restored)
        (ms / "01-choice.md").write_text(restored)

        # --- doc pull truncation on a base-less mapped file (T2,
        # risk-register BUG-1 repro): ensure_master gives every file in
        # reading order a tab, but only the pushed file gets a
        # pushed_hash. A pull of "everything mapped" then sees the
        # never-pushed file's empty tab as "changed" with no recorded
        # base and overwrites the local file with nothing. Own
        # workspace, own FakeGoogle instance — isolated from the shared
        # fixture above. CHARACTERIZATION repro (risk-register §3, T2):
        # this stays green as today's behavior, not a specification,
        # until the Sponsor authorizes the three_way base-less guard.
        t2_root = root / "t2-ws"
        t2_ms = t2_root / "manuscript"
        t2_ms.mkdir(parents=True)
        (t2_ms / "a.md").write_text("# A\n\nOriginal a content.\n")
        (t2_ms / "b.md").write_text(
            "# B\n\nOriginal b content, never individually pushed.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(t2_root), "init", "--name", "t2book",
                      "--path", str(t2_ms), "--no-extract"])
        t2_db = api.open_db(str(t2_root))
        t2_manuscript = api.get_manuscript(t2_db)
        t2_stub = FakeGoogle()
        pushed_a = push_doc(t2_db, t2_manuscript, "a.md",
                            service=t2_stub, docs_service=t2_stub)
        master_tabs = t2_stub.state["docs"][pushed_a["doc_id"]]
        b_tab = next(t for t in master_tabs if t["title"] == "b.md")
        check("ensure_master gave the never-pushed file b.md its own tab "
              "too — empty, since only a.md was actually pushed",
              b_tab["text"] == "", str(master_tabs))
        t2_manuscript = api.get_manuscript(t2_db)  # refresh metadata
        report = pull_doc(t2_db, t2_manuscript,
                          service=t2_stub, docs_service=t2_stub)
        check("a pull of everything mapped treats the base-less empty tab "
              "as 'changed' (no pushed_hash to compare against) and "
              "overwrites the never-pushed local file with nothing",
              "b.md" in report["changed"]
              and (t2_ms / "b.md").read_text() == "",
              str(report))
        check("meanwhile the actually-pushed file is untouched",
              "a.md" not in report["changed"]
              and "Original a content" in (t2_ms / "a.md").read_text())

        # --- gdocs failure path: documents().get() outage during reconcile
        # (T6, risk-register §3) — reconcile's tab-listing pass is wrapped
        # in a bare try/except (gdocs.py reconcile, ~line 1610-1616); this
        # pins that it actually behaves as documented: the session survives
        # (no exception escapes), the failure surfaces as a per-file error
        # instead of being silently swallowed, and an in-sync local file is
        # left untouched rather than being mistaken for a change. ---
        class FailingDocsService:
            def documents(self):
                class _Boom:
                    def get(self, **kwargs):
                        raise RuntimeError("simulated Docs API outage")
                return _Boom()

        manuscript = api.get_manuscript(db)
        before_text = (ms / "01-choice.md").read_text()
        try:
            report = reconcile(db, manuscript, stub,
                               docs_service=FailingDocsService())
            reconcile_survived = True
        except Exception:
            reconcile_survived = False
        check("reconcile survives a documents().get() failure during tab "
              "listing instead of raising and killing the session",
              reconcile_survived)
        check("the failure surfaces as a per-file error, not silently "
              "swallowed",
              any(e.get("file") == "(tab listing)"
                  and "simulated Docs API outage" in e.get("error", "")
                  for e in report["errors"]), str(report))
        check("the local file is untouched — reconcile wrote nothing "
              "despite the tab-listing failure",
              (ms / "01-choice.md").read_text() == before_text)
        check("a file that was actually in sync is still reported in_sync "
              "despite the tab-listing failure (resolved via the plain "
              "export, independent of the failed tab walk)",
              "01-choice.md" in report["in_sync"], str(report))

        # --- margin threads: propose in-context; canonical stays old ---
        from authorlm import threads as th
        from authorlm.gdocs import propose_change

        strip_out = th.strip_pending("A <<old>>{{new}} B")
        check("pending grammar strips to canonical old; strays warn",
              strip_out == ("A old B", [])
              and th.strip_pending("stray << here")[1], str(strip_out))
        check("verdict grammar is deterministic, whole-reply only",
              th.classify_reply("Go ahead!") == "approve"
              and th.classify_reply("no") == "decline"
              and th.classify_reply("go ahead but soften it")
              == "conversation"
              and th.is_ours("AuthorLM: proposed — x")
              and not th.is_ours("looks wrong to me"))

        # Resolve helpers the critique/margin paths share: empty-old
        # insertions, strikethrough wrappers, and form enumeration that
        # must not double-count the {{new}} half of a replace.
        check("render_pending with empty old is the insertion form",
              th.render_pending("", "bridge") == "{{bridge}}"
              and th.render_insertion("bridge") == "{{bridge}}")
        wrapped = "Lead.\n\n~~<<old span>>~~{{new span}}\n\nTail.\n"
        check("approved_text keeps {{new}} halves (incl. ~~-wrapped replaces)",
              th.approved_text(wrapped) == "Lead.\n\nnew span\n\nTail.\n",
              th.approved_text(wrapped))
        check("strip_pending on ~~-wrapped replaces still yields OLD",
              th.strip_pending(wrapped)[0] == "Lead.\n\nold span\n\nTail.\n",
              th.strip_pending(wrapped))
        with_insert = ("Para one.\n\n{{inserted paragraph}}\n\n"
                       "<<swap me>>{{swapped}}\n\nPara three.\n")
        stripped_ins, _ = th.strip_pending(with_insert)
        check("strip_pending drops paragraph insertions byte-clean",
              stripped_ins == "Para one.\n\nswap me\n\nPara three.\n",
              stripped_ins)
        check("approved_text keeps paragraph insertions and replace news",
              th.approved_text(with_insert)
              == ("Para one.\n\ninserted paragraph\n\n"
                  "swapped\n\nPara three.\n"),
              th.approved_text(with_insert))
        forms = th.pending_forms(with_insert)
        check("pending_forms lists replace + insert once each, doc order",
              [(f["kind"], f["old"], f["new"]) for f in forms]
              == [("insert", "", "inserted paragraph"),
                  ("replace", "swap me", "swapped")],
              str(forms))
        nested = "<<keep {{this}} literal>>{{replacement}}"
        check("pending_forms does not treat replace's {{new}} as an insert",
              th.pending_forms(nested)
              == [{"kind": "replace", "old": "keep {{this}} literal",
                   "new": "replacement", "start": 0,
                   "end": len(nested)}],
              str(th.pending_forms(nested)))

        pull_doc(db, manuscript, "01-choice.md", service=stub, force=True)
        stub.add_comment("c-1", "Doc went another way",
                         "Can we make this stronger?")
        pulled_c = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("comments are ingested but never auto-resolved",
              any(c["comment_id"] == "c-1" for c in pulled_c["comments"])
              and not stub.state["comments"]["c-1"]["resolved"]
              and pulled_c["threads"]["counts"] == {},
              str(pulled_c.get("comments")))

        prop = propose_change(
            db, manuscript, "c-1", old="Doc went another way.",
            new="The Doc chose a firmer road.", note="strengthen per comment",
            service=stub, docs_service=stub)
        tab_now = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                       if t2["title"] == "01-choice.md")
        check("propose wraps the anchor and inserts the proposal in place",
              "<<Doc went another way.>>{{The Doc chose a firmer road.}}"
              in tab_now
              and stub.state["comments"]["c-1"]["replies"][0]["content"]
              .startswith("AuthorLM: proposed")
              and th.get_thread(db, manuscript["id"], "c-1")["state"]
              == "proposed", tab_now)

        pulled_p = pull_doc(db, manuscript, "01-choice.md", service=stub)
        local_now = (ms / "01-choice.md").read_text()
        check("canonical local text stays OLD while the proposal pends",
              "01-choice.md" in pulled_p["unchanged"]
              and "Doc went another way." in local_now
              and "{{" not in local_now, local_now)
        check("re-pull is idempotent; the ledger reports the thread",
              not pulled_p["comments"]
              and pulled_p["threads"]["counts"].get("proposed") == 1,
              str(pulled_p["threads"]))

        stub.author_reply("c-1", "go ahead")
        pulled_v = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("author verdicts surface classified for the state machine",
              pulled_v["thread_replies"] == [
                  {"comment_id": "c-1", "state": "proposed",
                   "reply": "go ahead", "verdict": "approve"}],
              str(pulled_v.get("thread_replies")))

        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\nA new closing thought.\n")
        pushed_s = push_doc(db, manuscript, "01-choice.md",
                            service=stub, docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("surgical diff push edits around open threads",
              pushed_s.get("mode") == "diff" and pushed_s.get("ops") == 1
              and "A new closing thought." in tab_after
              and "<<Doc went another way.>>" in tab_after
              and not stub.state["comments"]["c-1"]["resolved"], tab_after)

        # Local-only edit after surgical push must be local_ahead — not a
        # false CONFLICT. diff_push once hashed join(paras) without the
        # trailing newline normalize_markdown adds; three_way then saw
        # both sides off the base and invited --force data loss.
        pre_local = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(
            pre_local + "\nLocal after surgical push.\n")
        after_surg = pull_doc(db, manuscript, "01-choice.md", service=stub)
        check("local-only edit after surgical push is local_ahead, "
              "not conflict",
              "01-choice.md" in after_surg.get("local_ahead", [])
              and "01-choice.md" not in after_surg.get("conflicts", [])
              and "Local after surgical push."
              in (ms / "01-choice.md").read_text(),
              str(after_surg))
        (ms / "01-choice.md").write_text(pre_local)

        original_local = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(original_local.replace(
            "Doc went another way.", "Doc went a third way."))
        try:
            push_doc(db, manuscript, "01-choice.md",
                     service=stub, docs_service=stub)
            check("diff push refuses edits overlapping pending spans",
                  False)
        except LookupError as err:
            check("diff push refuses edits overlapping pending spans",
                  "pending margin thread" in str(err))
        (ms / "01-choice.md").write_text(original_local)

        # X7-2 (diff_push parity): diff_push's own tab_markdown() helper
        # split the master export using only mapped essay files + the
        # manifest as boundaries and no order= — unlike pull_doc/reconcile,
        # which include every live tab title (prompt tabs, the reserved
        # 'illustrations' tab) with positional order. A tab that sits
        # right after an essay in the export but isn't in that shrunken
        # set is invisible as a boundary, so its heading and body get
        # swallowed into the essay's own exported section.
        from authorlm.gdocs import ILLUS_SENTINEL, ILLUS_TAB_TITLE, diff_push

        docs2 = stub.state["docs"]["doc-2"]
        choice_idx = next(i for i, t in enumerate(docs2)
                          if t["title"] == "01-choice.md")
        # Same parent as the essay tab itself (its containing manuscript
        # tab) — 01-choice.md is nested, so a sibling placed elsewhere at
        # root level (e.g. parent=None) would land after the WHOLE
        # container's subtree in export order, not right after the essay.
        choice_parent = docs2[choice_idx].get("parent")
        stub.state["tab_counter"] += 1
        illus_id = f"tab-{stub.state['tab_counter']}"
        docs2.insert(choice_idx + 1, {"id": illus_id,
                                      "title": ILLUS_TAB_TITLE,
                                      "text": ILLUS_SENTINEL,
                                      "parent": choice_parent})
        stub.state["tab_counter"] += 1
        prompt_id = f"tab-{stub.state['tab_counter']}"
        docs2.insert(choice_idx + 2, {"id": prompt_id,
                                      "title": "bleeding-prompt.md",
                                      "text": "a prompt that must not bleed",
                                      "parent": illus_id})
        try:
            pre_bleed_local = (ms / "01-choice.md").read_text()
            (ms / "01-choice.md").write_text(
                pre_bleed_local + "\nA note added near the boundary.\n")
            try:
                diff_push(db, manuscript, "01-choice.md", stub, stub)
                bleed_survived, bleed_error = True, ""
            except LookupError as err:
                bleed_survived, bleed_error = False, str(err)
            check("diff push of an essay immediately followed by another "
                  "tab (an illustration prompt tab, here) survives the "
                  "round trip instead of the adjacent tab's heading/body "
                  "bleeding into this essay's exported section",
                  bleed_survived, bleed_error)
            if bleed_survived:
                choice_tab_text = next(
                    t["text"] for t in stub.state["docs"]["doc-2"]
                    if t["title"] == "01-choice.md")
                prompt_tab_text = next(
                    t["text"] for t in stub.state["docs"]["doc-2"]
                    if t["title"] == "bleeding-prompt.md")
                check("the pushed essay tab carries the new local "
                      "paragraph and nothing bled in from the adjacent "
                      "prompt tab",
                      "A note added near the boundary." in choice_tab_text
                      and "bleeding-prompt.md" not in choice_tab_text
                      and "a prompt that must not bleed"
                      not in choice_tab_text,
                      choice_tab_text)
                check("the adjacent prompt tab's own content is untouched",
                      prompt_tab_text == "a prompt that must not bleed",
                      prompt_tab_text)
            (ms / "01-choice.md").write_text(pre_bleed_local)
        finally:
            # Clean up the scratch tabs — a real 'illustrations' tab gets
            # created for real later in this suite (push_prompt_tabs) and
            # must not collide with this orphaned stand-in.
            docs2[:] = [t for t in docs2
                       if t["id"] not in (illus_id, prompt_id)]

        # Drive entity-encodes quotes and bodies; fetch unescapes them
        # (it-49edf0c323d1).
        from authorlm.gdocs import fetch_open_comments
        stub.add_comment("c-ent", "one&#39;s own", "entities &#39;here&#39;")
        fetched = {c["id"]: c for c in fetch_open_comments(stub, "doc-2")}
        check("comment quotes and bodies are HTML-unescaped at fetch",
              fetched["c-ent"]["quotedFileContent"]["value"] == "one's own"
              and fetched["c-ent"]["content"] == "entities 'here'",
              str(fetched.get("c-ent")))
        stub.state["comments"]["c-ent"]["resolved"] = True

        # A comment the author resolves BY HAND in the Doc UI (not through
        # 'doc decide' or a written-thread receipt) must not stay marked
        # 'ingested' forever (it-ce3f6078b674). harvest_comments has to
        # reconcile its own stale rows against Drive's current open set,
        # not just add to them — and it must do this even when the open
        # set comes back EMPTY, which is exactly what an author resolving
        # everything by hand produces.
        from authorlm.gdocs import harvest_comments, manuscript_bridge

        # docs_service=None throughout: this block only exercises ingest +
        # reconcile. The master Doc carries other open threads (c-1) mid
        # verdict elsewhere in this suite — passing a live docs_service
        # would also run advance_threads doc-wide and apply THEIR pending
        # verdicts as a side effect of this unrelated harvest call.
        stub.add_comment("c-hand", "a phrase", "resolve me by hand")
        harvest_report = {}
        harvest_comments(db, manuscript, "doc-2", stub, None,
                         manuscript_bridge(manuscript), harvest_report)
        row = db.one("SELECT state FROM doc_comments WHERE manuscript_id = ? "
                     "AND comment_id = ?", (manuscript["id"], "c-hand"))
        check("a fresh comment is ingested as open",
              row is not None and row["state"] == "ingested", str(row))
        replies_before = len(stub.state["comments"]["c-hand"]["replies"])
        stub.state["comments"]["c-hand"]["resolved"] = True  # author, in the Doc
        harvest_report2 = {}
        harvest_comments(db, manuscript, "doc-2", stub, None,
                         manuscript_bridge(manuscript), harvest_report2)
        row2 = db.one("SELECT state FROM doc_comments WHERE manuscript_id = ? "
                      "AND comment_id = ?", (manuscript["id"], "c-hand"))
        check("harvest reconciles a hand-resolved comment to 'resolved'",
              row2 is not None and row2["state"] == "resolved", str(row2))
        check("reconciliation is reported to the caller",
              "c-hand" in harvest_report2.get("comments_reconciled", []),
              str(harvest_report2))
        check("reconciling a hand-resolved comment posts no reply — the "
              "author already closed it in the Doc",
              len(stub.state["comments"]["c-hand"]["replies"])
              == replies_before)

        # Export escaping survives the strip (markers arrive as \<\<
        # with ~~ strikethrough in the markdown export).
        exported = ("~~\\<\\<the old way.\\>\\>~~"
                    "{{the new way.}}")
        stripped_exp = th.strip_pending(normalize_markdown(exported))
        check("export-escaped pending spans strip to canonical old",
              stripped_exp == ("the old way.\n", []), str(stripped_exp))

        # Nested braces in proposal text would truncate at the first }} —
        # refuse before any Doc write.
        try:
            th.assert_no_pending_markers("safe old", "f(x)={{a}}")
            check("propose refuses delimiter-bearing new text", False)
        except ValueError as err:
            check("propose refuses delimiter-bearing new text",
                  "pending-change grammar" in str(err)
                  and ("{{" in str(err) or "}}" in str(err)), str(err))
        try:
            th.render_pending("<<already marked>>", "new")
            check("render_pending refuses delimiter-bearing old text", False)
        except ValueError as err:
            check("render_pending refuses delimiter-bearing old text",
                  "<<" in str(err), str(err))

        # Approve with the author's in-place edit: modified acceptance.
        stub.author_reply("c-1", "AuthorLM: proposed — courtesy")
        tab = next(t2 for t2 in stub.state["docs"]["doc-2"]
                   if t2["title"] == "01-choice.md")
        tab["text"] = tab["text"].replace(
            "{{The Doc chose a firmer road.}}",
            "{{The Doc took the firmer road.}}")
        stub.author_reply("c-1", "go ahead")
        pulled_a = pull_doc(db, manuscript, "01-choice.md", service=stub,
                            docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        thread_a = th.get_thread(db, manuscript["id"], "c-1")
        check("approve applies the author-edited version and closes",
              "The Doc took the firmer road." in tab_after
              and "<<" not in tab_after
              and thread_a["state"] == "cleaned"
              and thread_a["proposed_new"] == "The Doc took the firmer road."
              and stub.state["comments"]["c-1"]["resolved"]
              and any(a["action"] == "cleaned"
                      for a in pulled_a["thread_actions"]), tab_after)
        check("approved text lands in the local file",
              "The Doc took the firmer road."
              in (ms / "01-choice.md").read_text())

        # Decline reverts and closes.
        stub.add_comment("c-3", "firmer road", "hmm")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-3",
                       old="The Doc took the firmer road.",
                       new="A road of iron.", note="try iron",
                       service=stub, docs_service=stub)
        stub.author_reply("c-3", "revert")
        pull_doc(db, manuscript, "01-choice.md", service=stub,
                 docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("decline reverts the span and closes the thread",
              "A road of iron." not in tab_after
              and "The Doc took the firmer road." in tab_after
              and "<<" not in tab_after
              and th.get_thread(db, manuscript["id"], "c-3")["state"]
              == "declined"
              and stub.state["comments"]["c-3"]["resolved"], tab_after)

        # Withdraw: author resolves a proposed thread without a verdict.
        stub.add_comment("c-4", "firmer road", "or gold?")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-4",
                       old="The Doc took the firmer road.",
                       new="A road of gold.", note="try gold",
                       service=stub, docs_service=stub)
        stub.state["comments"]["c-4"]["resolved"] = True
        pull_doc(db, manuscript, "01-choice.md", service=stub,
                 docs_service=stub)
        tab_after = next(t2["text"] for t2 in stub.state["docs"]["doc-2"]
                         if t2["title"] == "01-choice.md")
        check("author-resolving a proposal withdraws and reverts it",
              "A road of gold." not in tab_after
              and "<<" not in tab_after
              and th.get_thread(db, manuscript["id"], "c-4")["state"]
              == "withdrawn", tab_after)

        # Step 4: terminal verdicts are the third evidence channel.
        ev = [dict(r) for r in db.all(
            "SELECT * FROM evidence WHERE manuscript_id = ? "
            "AND evidence_type = 'margin_thread'", (manuscript["id"],))]
        signals = sorted(e["signal"] for e in ev)
        check("margin verdicts land as evidence "
              "(modified/declined/withdrawn)",
              signals == ["declined", "modified", "withdrawn"]
              and any("became" in e["target"] for e in ev
                      if e["signal"] == "modified"), str(signals))

        # Chat door: an explained decline; without an LLM the guardrail
        # seeds nothing — the evidence still lands verbatim.
        import json as _mjson

        from authorlm.gdocs import decide_thread
        stub.add_comment("c-5", "firmer road", "colour?")
        pull_doc(db, manuscript, "01-choice.md", service=stub)
        propose_change(db, manuscript, "c-5",
                       old="The Doc took the firmer road.",
                       new="A road of copper.", note="try copper",
                       service=stub, docs_service=stub)
        decided = decide_thread(db, manuscript, "c-5", "decline",
                                reason="Copper is the wrong register here",
                                service=stub, docs_service=stub, llm=None)
        ev2 = db.one(
            "SELECT * FROM evidence WHERE manuscript_id = ? AND "
            "evidence_type='margin_thread' AND signal='declined' "
            "ORDER BY created_at DESC", (manuscript["id"],))
        check("chat-decided decline records the reason verbatim and "
              "seeds nothing without the distiller",
              decided["state"] == "declined"
              and _mjson.loads(ev2["metadata"] or "{}").get("explanation")
              == "Copper is the wrong register here"
              and stub.state["comments"]["c-5"]["resolved"]
              and db.one(
                  "SELECT COUNT(*) AS n FROM editorial_beliefs "
                  "WHERE manuscript_id = ? AND source = 'margin-thread'",
                  (manuscript["id"],))["n"] == 0, str(decided))

        # --- critique_diff_write: accepted forms → Doc; failure isolation ---
        from authorlm.gdocs import critique_diff_write

        critique_body = (
            "# Critique Target\n\n"
            "First body paragraph for replace.\n\n"
            "Second body paragraph stays.\n\n"
            "Third body paragraph for insert after.\n"
        )
        (ms / "07-critique-write.md").write_text(critique_body)
        push_doc(db, manuscript, "07-critique-write.md",
                 service=stub, docs_service=stub)
        local_before = (ms / "07-critique-write.md").read_text()

        def _crit_thread(old, new, anchor, kind, state="accepted"):
            row = ko_fields("dt")
            row.update(
                manuscript_id=manuscript["id"], origin_type="critique",
                origin_id=f"crit-write:{row['id'][:8]}",
                file="07-critique-write.md", anchor_quote=None,
                proposed_old=old, proposed_new=new, note="why",
                state=state, our_reply_ids="[]",
                last_author_reply_id=None, scope_kind="file",
                scope_ref="07-critique-write.md",
                metadata=_mjson.dumps({
                    "kind": kind, "anchor_paragraph": anchor,
                    "intent_id": "i1", "original_new": new}))
            db.insert("doc_threads", row)
            return dict(db.one("SELECT * FROM doc_threads WHERE id = ?",
                               (row["id"],)))

        t_rep = _crit_thread(
            "First body paragraph for replace.",
            "First body paragraph, carefully revised.",
            2, "replace")
        t_ins = _crit_thread(
            "", "A bridging paragraph, newly inserted.",
            4, "insert")
        t_bad = _crit_thread(
            "This text is nowhere in the essay.",
            "Should fail loudly.",
            2, "replace")
        t_rej = _crit_thread(
            "Second body paragraph stays.",
            "Should never appear.",
            3, "replace", state="rejected")

        result = critique_diff_write(
            db, manuscript, "07-critique-write.md",
            [t_rep, t_ins, t_bad, t_rej], stub, stub)
        tab_cw = next(t["text"] for t in stub.state["docs"]["doc-2"]
                      if t["title"] == "07-critique-write.md")
        written_ids = {t["id"] for t in result["written"]}
        failed_ids = {t["id"] for t, _ in result["failed"]}
        check("critique_diff_write marks accepted replace+insert; "
              "isolates missing-span failure; skips rejected",
              t_rep["id"] in written_ids and t_ins["id"] in written_ids
              and t_bad["id"] in failed_ids
              and t_rej["id"] not in written_ids
              and t_rej["id"] not in failed_ids
              and ("<<First body paragraph for replace.>>"
                   "{{First body paragraph, carefully revised.}}") in tab_cw
              and "{{A bridging paragraph, newly inserted.}}" in tab_cw
              and "Should never appear" not in tab_cw
              and "Should fail loudly" not in tab_cw,
              f"written={written_ids} failed={result['failed']!r} "
              f"tab={tab_cw!r}")
        check("critique_diff_write leaves local file as OLD (pristine)",
              (ms / "07-critique-write.md").read_text() == local_before)
        check("critique_diff_write reports a Doc tab URL",
              "doc-2" in (result.get("url") or "")
              and "tab=" in (result.get("url") or ""),
              str(result.get("url")))

        # Out-of-range insert: write always push-rebuilds from local first,
        # so a second call starts clean; the bad thread fails alone.
        t_oor = _crit_thread(
            "", "orphan insert", 99, "insert")
        result2 = critique_diff_write(
            db, manuscript, "07-critique-write.md", [t_oor], stub, stub)
        tab_after_fail = next(
            t["text"] for t in stub.state["docs"]["doc-2"]
            if t["title"] == "07-critique-write.md")
        check("out-of-range insert anchor fails in isolation",
              not result2["written"]
              and result2["failed"]
              and "out of range" in result2["failed"][0][1],
              str(result2["failed"]))
        check("a failed-only write push-rebuilds the tab to local OLD",
              "First body paragraph for replace." in tab_after_fail
              and "{{" not in tab_after_fail
              and "orphan insert" not in tab_after_fail,
              tab_after_fail)
        check("local stays pristine after a failed-only write",
              (ms / "07-critique-write.md").read_text() == local_before)
        # Drop the fixture before later toc/export asserts that expect
        # only the chapters they declare.
        (ms / "07-critique-write.md").unlink()

        # --- single-manuscript export (doc create-manuscript) ---
        from authorlm.export import combined_markdown, export_manuscript

        (ms / "00-intro.md").write_text("# Intro\n\nWelcome.\n")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n')
        manuscript = api.get_manuscript(db)
        text, order, unlisted = combined_markdown(manuscript)
        check("combined markdown follows toc.toml reading order",
              order == ["01-choice.md", "00-intro.md"] and not unlisted
              and text.index("firmer road") < text.index("Welcome"), text)

        local_only = export_manuscript(db, manuscript, service=None)
        export_path = ms / "_exports" / "book.md"
        check("export writes _exports/<name>.md even without Drive",
              local_only["doc_id"] is None and export_path.read_text() == text)

        exported = export_manuscript(db, manuscript, service=stub)
        check("export creates the manuscript Doc in the existing folder",
              exported["created"] and exported["doc_id"] is not None
              and stub.state["folders"] == ["doc-1"])
        exported2 = export_manuscript(db, manuscript, service=stub)
        check("re-export updates the same Doc (no new one)",
              not exported2["created"]
              and exported2["doc_id"] == exported["doc_id"])

        # A Doc deleted by hand in Drive is transient — recreated on export.
        class Gone(Exception):
            resp = type("R", (), {"status": 404})()

        original_update = stub._files.update
        stub._files.update = lambda fileId=None, media_body=None: (_ for _ in ()).throw(Gone())
        exported3 = export_manuscript(db, manuscript, service=stub)
        stub._files.update = original_update
        check("a hand-deleted export Doc is recreated",
              exported3["created"]
              and exported3["doc_id"] != exported["doc_id"])

        titled = export_manuscript(db, manuscript, service=None, title="My Book")
        check("retitled export replaces the stale local file",
              (ms / "_exports" / "My Book.md").exists()
              and not export_path.exists()
              and titled["doc_title"] == "My Book")

        report = reconcile(db, api.get_manuscript(db), stub)
        check("reconcile never touches the push-only export entry",
              "_export" not in report["in_sync"] + report["pulled"]
              + report["pushed"] + report["conflicts"]
              + [e["file"] for e in report["errors"]], str(report))

        # --- publishing exports: variants, settings, local pandoc ---
        from authorlm.export import (export_published, load_settings,
                                     publish_markdown, set_setting)

        (ms / "00-intro.md").write_text(
            "An opening epigraph.\n\n# Intro\n\nWelcome.\n\n"
            "[Illustration: a winding path | caption: The path]\n\n"
            "[Illustration: an unrendered idea]\n")
        path_hash = illus_mod.desc_hash("a winding path")
        winding = f"a-winding-path-{path_hash}-0000-01.png"
        (ms / "_illustrations" / winding).write_bytes(tiny_png())
        text, order, warnings = publish_markdown(manuscript, "images")
        check("images variant embeds candidates with captions, keeps "
              "unrendered slots as notes and warns",
              f"![The path](_illustrations/{winding})" in text
              and "[Illustration: an unrendered idea]" in text
              and any("unrendered" in w for w in warnings), text)
        stripped_text, _, _ = publish_markdown(manuscript, "stripped")
        check("stripped variant removes every tag (audio-clean)",
              "[Illustration" not in stripped_text
              and "Welcome." in stripped_text)
        slots_text, _, _ = publish_markdown(manuscript, "slots")
        check("slots variant keeps tags verbatim as production notes",
              "[Illustration: a winding path | caption: The path]"
              in slots_text)

        set_setting(manuscript, "variant", "slots")
        check("export settings persist in _exports/settings.toml",
              load_settings(manuscript)["variant"] == "slots"
              and (ms / "_exports" / "settings.toml").exists())
        try:
            set_setting(manuscript, "author", "Legacy Byline")
            legacy_author_refused = False
        except LookupError:
            legacy_author_refused = True
        check("author identity cannot drift into export settings",
              legacy_author_refused)
        try:
            set_setting(manuscript, "nope", "x")
            refused = False
        except LookupError:
            refused = True
        check("unknown export settings are refused", refused)

        # A PDF essay begins at its FILE boundary, including any epigraph or
        # other pre-heading matter. Heading levels control scale only: an H1
        # inside a file must not define the essay boundary.
        (ms / "title.md").write_text("# **The Book**\n\nA subtitle.\n")
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "title.md"\nmatter = "front"\n\n'
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n')
        manuscript = api.get_manuscript(db)
        from types import SimpleNamespace as _SimpleNamespace
        from unittest.mock import patch as _patch

        with (_patch("shutil.which", return_value="/usr/bin/pandoc"),
              _patch("subprocess.run", return_value=_SimpleNamespace(
                  returncode=0, stderr="")) as pandoc_run):
            pdf_result = export_published(
                db, manuscript, fmt="pdf", variant="images")
        pdf_command = pandoc_run.call_args.args[0]
        pdf_markdown = Path(pdf_result["markdown"]).read_text()
        pdf_metadata = [pdf_command[i + 1]
                        for i, arg in enumerate(pdf_command[:-1])
                        if arg == "--metadata"]
        check("PDF export defaults to a confidential review copy",
              pdf_result["mode"] == "review"
              and "authorlm-review-copy=true" in pdf_metadata
              and "author=Author Penname" in pdf_metadata
              and "copyright-owner=Author House LLC" in pdf_metadata
              and any(item.startswith("subject=Copyright © ")
                      for item in pdf_metadata)
              and any(item.startswith("copyright-year=")
                      for item in pdf_metadata),
              str(pdf_command))
        check("PDF export starts the whole essay before its epigraph",
              "::: {.authorlm-file .authorlm-essay}\n"
              "An opening epigraph.\n\n# Intro"
              in pdf_markdown, pdf_markdown)
        check("Pandoc input carries semantics, never writer markup",
              "::: {.authorlm-file .authorlm-title-page}" in pdf_markdown
              and "# **The Book**" in pdf_markdown
              and "# Intro" in pdf_markdown
              and "\\Huge" not in pdf_markdown
              and "\\newpage" not in pdf_markdown,
              pdf_markdown)
        defaults = [pdf_command[i + 1]
                    for i, arg in enumerate(pdf_command[:-1])
                    if arg == "--defaults"]
        check("PDF formatting is selected through Pandoc defaults",
              [Path(path).name for path in defaults]
              == ["common.yaml", "pdf.yaml"], str(pdf_command))

        with (_patch("shutil.which", return_value="/usr/bin/pandoc"),
              _patch("subprocess.run", return_value=_SimpleNamespace(
                  returncode=0, stderr="")) as print_run):
            print_result = export_published(
                db, manuscript, fmt="pdf", variant="images",
                print_ready=True)
        print_command = print_run.call_args.args[0]
        print_metadata = [print_command[i + 1]
                          for i, arg in enumerate(print_command[:-1])
                          if arg == "--metadata"]
        check("print-ready PDF suppresses every review-copy instruction",
              print_result["mode"] == "print"
              and not any(item.startswith("authorlm-review-copy=")
                          for item in print_metadata)
              and "author=Author Penname" in print_metadata
              and any(item.startswith("subject=Copyright © ")
                      for item in print_metadata), str(print_command))
        from authorlm.cli import build_parser as _build_parser
        print_args = _build_parser().parse_args(
            ["export", "pdf", "--print-ready"])
        check("CLI exposes the explicit print-ready escape hatch",
              print_args.print_ready is True)
        try:
            export_published(
                db, manuscript, fmt="epub", variant="images",
                print_ready=True)
            non_pdf_print_ready_refused = False
        except ValueError:
            non_pdf_print_ready_refused = True
        check("publication interface restricts print-ready mode to PDF",
              non_pdf_print_ready_refused)

        import shutil as _shutil
        if _shutil.which("pandoc"):
            import subprocess as _subprocess
            review_filter = (Path(__file__).resolve().parent.parent
                             / "authorlm" / "publication" / "review.lua")
            filtered = _subprocess.run(
                ["pandoc", pdf_result["markdown"], "--from",
                 "markdown+smart+footnotes+fenced_divs", "--lua-filter",
                 str(review_filter), "--metadata",
                 "authorlm-review-copy=true", "--metadata",
                 "author=Author Penname", "--metadata",
                 "copyright-owner=Author House LLC", "-t", "plain"],
                capture_output=True, text=True)
            check("review filter inserts the legal notice ahead of the title",
                  filtered.returncode == 0
                  and filtered.stdout.index(
                      "CONFIDENTIAL PREPUBLICATION REVIEW DRAFT")
                  < filtered.stdout.index("The Book")
                  and "Author House LLC" in filtered.stdout,
                  filtered.stderr or filtered.stdout[:1000])
            structured_latex = _subprocess.run(
                ["pandoc", pdf_result["markdown"], "--from",
                 "markdown+smart+footnotes+fenced_divs", "--lua-filter",
                 str(review_filter.parent / "structure.lua"),
                 "--lua-filter", str(review_filter), "--metadata",
                 "authorlm-review-copy=true", "--metadata",
                 "author=Author Penname", "--metadata",
                 "copyright-owner=Author House LLC", "-t", "latex"],
                capture_output=True, text=True)
            check("title page suppresses native numbering before its heading",
                  structured_latex.returncode == 0
                  and structured_latex.stdout.index(
                      "\\thispagestyle{empty}")
                  < structured_latex.stdout.index(
                      "\\AuthorLMBookTitle{}"),
                  structured_latex.stderr or structured_latex.stdout[:1000])
            published = export_published(db, manuscript, fmt="docx",
                                         variant="images")
            docx = Path(published["docx"])
            import zipfile as _zipfile
            with _zipfile.ZipFile(docx) as word:
                core_properties = word.read(
                    "docProps/core.xml").decode("utf-8")
            check("pandoc docx export carries canonical identity",
                  docx.exists() and docx.stat().st_size > 1000,
                  str(published))
            check("DOCX properties carry canonical publication identity",
                  "Author Penname" in core_properties
                  and "Author House LLC" in core_properties,
                  core_properties)
        else:
            print("  note: pandoc not on PATH — docx conversion untested "
                  "in this run")
        (ms / "title.md").unlink()

        # --- chapter-scoped publishing: a part of the book, same build ---
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n\n'
            '[[chapter]]\nfile = "02-aside.md"\nparent = "00-intro.md"\n')
        (ms / "02-aside.md").write_text("# Aside\n\nA filed thought.\n")
        manuscript = api.get_manuscript(db)
        part, part_order, _ = publish_markdown(manuscript, "images",
                                               ["00-intro"])
        check("naming a parent builds it with its TOC descendants, "
              "and nothing else",
              part_order == ["00-intro.md", "02-aside.md"]
              and "A filed thought." in part
              and "firmer road" not in part, str(part_order))
        try:
            publish_markdown(manuscript, "images", ["no-such"])
            refused = False
        except LookupError:
            refused = True
        check("an unknown chapter name is refused, not silently empty",
              refused)
        if _shutil.which("pandoc"):
            whole = export_published(db, manuscript, fmt="md",
                                     variant="images")
            scoped = export_published(db, manuscript, fmt="epub",
                                      variant="images", only=["00-intro"])
            epub = Path(scoped["epub"])
            with _zipfile.ZipFile(epub) as book:
                css = "\n".join(
                    book.read(name).decode()
                    for name in book.namelist() if name.endswith(".css"))
                xhtml = "\n".join(
                    book.read(name).decode()
                    for name in book.namelist() if name.endswith(".xhtml"))
                package = "\n".join(
                    book.read(name).decode()
                    for name in book.namelist() if name.endswith(".opf"))
            check("a chapter epub lands beside the whole-book export "
                  "without overwriting it",
                  epub.exists() and epub.stat().st_size > 1000
                  and scoped["markdown"] != whole["markdown"]
                  and Path(whole["markdown"]).exists(), str(scoped))
            check("EPUB uses semantic file breaks and declarative CSS",
                  "authorlm-file + .authorlm-file" in css
                  and 'class="authorlm-file authorlm-essay"' in xhtml
                  and xhtml.index("An opening epigraph.")
                  < xhtml.index("Intro"), xhtml[:1000])
            check("EPUB metadata carries canonical publication identity",
                  "Author Penname" in package
                  and "Author House LLC" in package, package[:1000])

        # --- hygiene: deterministic filters + retroactive sweep ---
        import json as _json

        from authorlm import hygiene
        from authorlm.concepts import link_concepts

        ghost = api.add_concept(db, manuscript, "Spectral Machinery")
        db.update("concept_nodes", ghost["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        link_concepts(db, manuscript["id"], "Spectral Machinery",
                      "creates", "Choice", status="inferred")
        hygiene_files = read_files_for_test(ms)
        swept = hygiene.sweep(db, manuscript, hygiene_files)
        check("sweep flags ungrounded extracted concepts and their edges",
              any(c["name"] == "Spectral Machinery"
                  for c in swept["ungrounded_concepts"])
              and any("Spectral Machinery" in e["unmentioned"]
                      for e in swept["ungrounded_edges"]), str(swept))
        check("sweep never flags grounded or author-confirmed material",
              not any(c["name"] == "Choice"
                      for c in swept["ungrounded_concepts"]))

        # Staleness: a bridge suggestion whose concept the author then
        # writes is marked stale at collect — review never offers a no-op.
        session, _ = api.ensure_session(db, manuscript)
        api.add_concept(db, manuscript, "Undertow")
        api.declare_intent(db, manuscript, "Introduce Undertow in ch1")
        guided = api.guide(db, manuscript, session)
        check("guide proposes the declared, unrealized concept",
              any("Undertow" in r["suggestion"]
                  for r in guided["suggestions"]), str(guided))
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n\nThe Undertow pulls every choice back toward habit.\n")
        report = api.collect(db, manuscript, {})
        check("collect marks the now-moot suggestion stale",
              any("Undertow" in s["suggestion"]
                  for s in report.get("suggestions_stale", []))
              and api.compact_collect(report)["suggestions_stale"]
              == report["suggestions_stale"], str(report.get(
                  "suggestions_stale")))
        row = db.one(
            "SELECT * FROM guidance_history WHERE manuscript_id = ? "
            "AND state = 'stale'", (manuscript["id"],))
        check("the stale state is persisted on the guidance row",
              row is not None and "Undertow" in row["suggestion"])

        # --- recurrence bar (ratified 2026-08-08): same-paragraph
        # collapse; >=2 contexts spanning >=2 sections or files ---
        bar_files = {
            "a.md": ("# One\n\nThe Undertow pulls. The Undertow drags.\n\n"
                     "Plain prose here.\n\n## Two\n\nMore prose.\n"),
            "b.md": "# Other\n\nNothing relevant.\n",
        }
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("twice in one paragraph is one context — fails the bar",
              not ok and stats["contexts"] == 1, str(stats))
        bar_files["a.md"] += "\nThe Undertow returns in section two.\n"
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("two contexts across two sections of one file pass the bar",
              ok and stats["contexts"] == 2 and stats["sections"] == 2,
              str(stats))
        bar_files["a.md"] = ("# One\n\nThe Undertow pulls.\n\n"
                             "The Undertow drags on, same section.\n")
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("two contexts in ONE section of one file fail the spread",
              not ok and stats["contexts"] == 2 and stats["sections"] == 1,
              str(stats))
        bar_files["b.md"] = "# Other\n\nThe Undertow crosses files.\n"
        ok, stats = hygiene.passes_recurrence_bar(bar_files, ["Undertow"])
        check("cross-file recurrence passes the bar",
              ok and stats["files"] == 2, str(stats))

        # --- policy belief lifecycle (Laplace confidence + promote/demote) ---
        from authorlm import beliefs as pol

        check("Laplace confidence is (s+1)/(s+c+2)",
              pol._confidence(0, 0) == 0.5
              and pol._confidence(3, 0) == 0.8
              and pol._confidence(0, 3) == 0.2)
        check("retired status is sticky under further evidence",
              pol._lifecycle_status("retired", 10, 0, 0.9) == "retired")
        check("low confidence with enough observations retires a candidate",
              pol._lifecycle_status("candidate", 0, 4,
                                    pol._confidence(0, 4)) == "retired")
        check("validated demotes when confidence falls below demote bar",
              pol._lifecycle_status("validated", 2, 2,
                                    pol._confidence(2, 2)) == "candidate")
        check("candidate promotes only with support AND confidence bars",
              pol._lifecycle_status("candidate", 3, 0,
                                    pol._confidence(3, 0)) == "validated"
              and pol._lifecycle_status("candidate", 2, 0,
                                        pol._confidence(2, 0))
              == "candidate")

        # The sweep reports unconfirmed gated nodes that fail the bar —
        # mentioned (not ungrounded) but single-context.
        motif = api.add_concept(db, manuscript, "Fleeting Motif",
                                kind="metaphor")
        db.update("concept_nodes", motif["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\nA Fleeting Motif appears exactly once.\n")
        swept = hygiene.sweep(db, manuscript, read_files_for_test(ms))
        check("sweep separates below-bar from ungrounded",
              any(c["name"] == "Fleeting Motif" and c["contexts"] == 1
                  for c in swept["below_bar"])
              and not any(c["name"] == "Fleeting Motif"
                          for c in swept["ungrounded_concepts"])
              and not any(c["name"] == "Spectral Machinery"
                          for c in swept["below_bar"]), str(swept["below_bar"]))

        # A vanished UNCONFIRMED hypothesis dies quietly (retired +
        # reported); only author-confirmed knowledge earns a proposal
        # (it-8f24c757d3c7).
        fleeting = api.add_concept(db, manuscript, "Fleeting Phrase")
        db.update("concept_nodes", fleeting["id"], {"metadata": _json.dumps(
            {"origin": "extracted", "confirmed": False})})
        with_phrase = (ms / "01-choice.md").read_text()
        (ms / "01-choice.md").write_text(
            with_phrase + "\nThe Fleeting Phrase appears here once.\n")
        api.collect(db, manuscript, {})
        (ms / "01-choice.md").write_text(with_phrase)
        report = api.collect(db, manuscript, {})
        check("vanished unconfirmed hypotheses are dropped, not proposed",
              report["hypotheses_dropped"] == ["Fleeting Phrase"]
              and "Fleeting Phrase" not in report["vanished"]
              and db.one("SELECT status FROM concept_nodes WHERE id = ?",
                         (fleeting["id"],))["status"] == "retired"
              and db.one(
                  "SELECT id FROM knowledge_proposals WHERE kind = 'vanished' "
                  "AND target = ?", (fleeting["id"],)) is None,
              str(report.get("hypotheses_dropped")))

        # --- sweep framework vanguards: readiness, ontology, lens ---
        from authorlm import lenses, sweeps

        ready = sweeps.readiness(db, manuscript)
        checks_present = {i["check"] for i in ready["items"]}
        check("readiness sweep covers the registries deterministically",
              {"illustrations rendered", "proposals settled",
               "toc covers every file", "pandoc available",
               "export settings set"} <= checks_present
              and isinstance(ready["ready"], bool), str(ready))
        check("readiness flags the unrendered slot as a blocker",
              not ready["ready"]
              and "illustrations rendered" in ready["blocking"], str(ready))
        sound_item = next(i for i in ready["items"] if i["check"] == "database sound")
        check("readiness runs PRAGMA integrity_check against the live store "
              "and reports a healthy database as sound (OPS-4)",
              sound_item["ok"] and sound_item["detail"] == "ok", str(sound_item))

        class FakeLLM:
            enabled = True

            def __init__(self, payload):
                self.payload = payload
                self.system = self.user = None

            def complete_json(self, system, user):
                self.system, self.user = system, user
                return self.payload

            def stats_line(self):
                return None

        # --- episode analysis: learn editorial judgment from observed edits ---
        from authorlm.analysis import MAX_DECISIONS, analyze_pending

        an_root = root / "analyze-ws"
        an_ms = an_root / "manuscript"
        an_ms.mkdir(parents=True)
        opening = ("# Opening\n\nChoice is the hinge of becoming.\n")
        (an_ms / "01-choice.md").write_text(opening)
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(an_root), "init", "--name", "analyze",
                      "--path", str(an_ms)])
        an_db = api.open_db(str(an_root))
        an_mscript = api.get_manuscript(an_db)
        api.collect(an_db, an_mscript, {})  # baseline version
        an_session, _ = api.ensure_session(an_db, an_mscript)
        an_declared = api.declare_intent(
            an_db, an_mscript, "Develop the notion of Becoming")
        an_intent_id = an_declared["intent"]["id"]
        sailor = ("Like a sailor tacking, becoming threads through what "
                  "choice has opened.")
        (an_ms / "01-choice.md").write_text(opening + f"\n\n{sailor}\n")
        (an_ms / "02-new.md").write_text(
            f"# New chapter\n\n{sailor} The fresh prose must reach the analyzer.\n")
        api.collect(an_db, an_mscript, {})

        progress_calls = []
        analysis_llm = FakeLLM({
            "decisions": (
                [{"action": "opened the section with a sailing metaphor",
                  "pattern": "Open concept introductions with a lived metaphor"}]
                + [{"action": f"local move {i}", "pattern": None}
                   for i in range(1, MAX_DECISIONS)]
                + [{"action": "ninth decision must be truncated",
                    "pattern": "Should not seed"}]
                + [{"action": "", "pattern": "empty action skipped"},
                   "not-a-dict"]
            ),
            "outcome": "Developed the opening metaphor",
        })
        completed = api.complete_intent(
            an_db, an_mscript, an_intent_id[:8], "done", llm=analysis_llm)
        summaries = completed["analysis"]
        check("complete_intent runs episode analysis over observed edits",
              len(summaries) == 1
              and summaries[0]["intent"] == "Develop the notion of Becoming"
              and summaries[0]["outcome"] == "Developed the opening metaphor"
              and len(summaries[0]["decisions"]) == MAX_DECISIONS
              and summaries[0]["decisions"][0]["pattern"]
              == "Open concept introductions with a lived metaphor",
              str(summaries))
        check("analysis prompt carries declared intent and file_added prose",
              "DECLARED INTENT: Develop the notion of Becoming"
              in analysis_llm.user
              and "NEW TEXT:" in analysis_llm.user
              and "fresh prose must reach the analyzer" in analysis_llm.user,
              analysis_llm.user[:800])
        seeded = an_db.one(
            "SELECT * FROM editorial_beliefs WHERE manuscript_id = ? "
            "AND statement = ?",
            (an_mscript["id"],
             "Open concept introductions with a lived metaphor"))
        check("pattern seeds a medium-weight episode_analysis candidate",
              seeded is not None and seeded["status"] == "candidate"
              and seeded["source"] == "episode-analysis"
              and an_db.one(
                  "SELECT weight, evidence_type FROM evidence "
                  "WHERE supports_belief = ?", (seeded["id"],)
              )["weight"] == "medium"
              and an_db.one(
                  "SELECT evidence_type FROM evidence "
                  "WHERE supports_belief = ?", (seeded["id"],)
              )["evidence_type"] == "episode_analysis")
        check("analysis is idempotent once the episode is marked analyzed",
              api.analyze(an_db, an_mscript, analysis_llm) == [])

        # Malformed decisions must not crash; episode stays pending for retry.
        an2 = api.declare_intent(an_db, an_mscript, "Second episode for retry")
        (an_ms / "01-choice.md").write_text(
            (an_ms / "01-choice.md").read_text()
            + "\n\nA second observed edit for the retry path.\n")
        api.collect(an_db, an_mscript, {})
        api.complete_intent(an_db, an_mscript, an2["intent"]["id"][:8],
                            "closed without llm")
        bad_llm = FakeLLM({"decisions": None, "outcome": "should not land"})
        # Force a pending episode: strip analysis metadata if complete wrote none
        # (no llm on complete above → episode closed but unanalyzed).
        pending_before = analyze_pending(an_db, an_mscript, bad_llm)
        check("null decisions do not crash and leave a completed analysis "
              "with no decisions",
              len(pending_before) == 1
              and pending_before[0]["decisions"] == []
              and pending_before[0]["outcome"] == "should not land",
              str(pending_before))

        an3 = api.declare_intent(an_db, an_mscript, "Third episode unavailable LLM")
        (an_ms / "01-choice.md").write_text(
            (an_ms / "01-choice.md").read_text()
            + "\n\nA third edit awaiting a usable LLM reply.\n")
        api.collect(an_db, an_mscript, {})
        api.complete_intent(an_db, an_mscript, an3["intent"]["id"][:8], None)
        none_llm = FakeLLM(None)
        progress_calls.clear()

        def _progress(index, total, statement):
            progress_calls.append((index, total, statement))

        stuck = analyze_pending(an_db, an_mscript, none_llm, progress=_progress)
        ep3 = an_db.one(
            "SELECT metadata FROM editorial_episodes WHERE intent_id = ?",
            (an3["intent"]["id"],))
        check("non-dict LLM reply leaves the episode pending for retry",
              stuck == []
              and not loads(ep3["metadata"], {}).get("analysis")
              and progress_calls
              and progress_calls[0][2] == "Third episode unavailable LLM",
              str({"stuck": stuck, "meta": ep3["metadata"],
                   "progress": progress_calls}))
        check("disabled LLM is a no-op over pending episodes",
              analyze_pending(an_db, an_mscript,
                              type("Off", (), {"enabled": False})()) == [])

        api.add_concept(db, manuscript, "Tremor",
                        notes="The Tremor is never caused; it causes.")
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text()
            + "\n\nSome say the Tremor is caused by the collision of "
              "qualities, and that its cause can be measured precisely "
              "by any patient observer of the fields.\n")
        grounded_quote = ("Some say the Tremor is caused by the collision "
                         "of qualities")
        onto_llm = FakeLLM({"findings": [
            {"item": 1, "concept": "Tremor", "quote": grounded_quote,
             "claim": "The Tremor is never caused; it causes.",
             "why": "The text gives the Tremor a cause."},
            {"item": 1, "concept": "Tremor",
             "quote": "a sentence that is not in the paragraph",
             "claim": "x", "why": "hallucinated"},
        ]})
        onto = sweeps.ontology(db, manuscript, onto_llm, file="01-choice.md")
        check("ontology narrows deterministically and gates findings",
              onto["pairs"] >= 1 and onto["findings"] == 1
              and onto["dropped_ungrounded"] == 1
              and "SETTLED" in onto_llm.user
              and "never caused" in onto_llm.user, str(onto))
        from authorlm import proposals as props
        incongruence = [r for r in props.open_proposals(db, manuscript["id"])
                        if r["kind"] == "incongruence"]
        check("incongruence findings land as proposals with a verdict "
              "grammar",
              len(incongruence) == 1
              and "Tremor" in props.describe(incongruence[0])[0]
              and "Acknowledged" in props.adopt(db, manuscript["id"],
                                                incongruence[0]))

        # --- BUG-20: an `item` index of 0 (or negative) must be rejected,
        # not silently wrapped by Python's negative-index behaviour onto a
        # DIFFERENT paragraph. `pairs[int(f.get("item",0)) - 1]` with
        # item=0 computes index -1, which Python resolves to the last pair
        # instead of raising IndexError, so the bounds guard never fires. ---
        zero_item_llm = FakeLLM({"findings": [
            {"item": 0, "concept": "Tremor", "quote": grounded_quote,
             "claim": "The Tremor is never caused; it causes.",
             "why": "an out-of-range item must not bind to any paragraph"},
        ]})
        onto_zero = sweeps.ontology(db, manuscript, zero_item_llm,
                                    file="01-choice.md")
        check("BUG-20: item=0 is dropped instead of wrapping onto the "
              "last pair via negative indexing",
              onto_zero["findings"] == 0
              and onto_zero["dropped_ungrounded"] == 1, str(onto_zero))
        neg_item_llm = FakeLLM({"findings": [
            {"item": -1, "concept": "Tremor", "quote": grounded_quote,
             "claim": "x", "why": "a negative item must not bind either"},
        ]})
        onto_neg = sweeps.ontology(db, manuscript, neg_item_llm,
                                   file="01-choice.md")
        check("BUG-20: a negative item is dropped, not indexed from the end",
              onto_neg["findings"] == 0
              and onto_neg["dropped_ungrounded"] == 1, str(onto_neg))

        lenses.add_lens(manuscript, "clarity",
                        "Flag sentences that assert a claim without "
                        "argument or example.")
        check("lens ratified into _lenses/ and listed",
              lenses.list_lenses(manuscript)[0]["name"] == "clarity")
        lens_llm = FakeLLM({"findings": [
            {"quote": grounded_quote, "note": "Asserted without argument."},
            {"quote": "nowhere text", "note": "hallucinated"},
        ]})
        session, _ = api.ensure_session(db, manuscript)
        run = lenses.run_lens(db, manuscript, session, "clarity",
                              "01-choice.md", lens_llm)
        check("lens run gates findings and stores its own batch",
              len(run["findings"]) == 1 and run["dropped_ungrounded"] == 1
              and run["findings"][0]["kind"] == "lens"
              and "THE LENS" in lens_llm.system
              and "never caused; it causes" in lens_llm.system, str(run))
        verdict = api.review(db, manuscript, session, 1, "rejected",
                             "Assertion is fine here — the sermon register "
                             "argues by declaration.", kinds=("lens",))
        check("lens verdicts flow through record_review as evidence",
              verdict["review"]["decision"] == "rejected"
              and db.one("SELECT state FROM guidance_history WHERE id = ?",
                         (run["findings"][0]["id"],))["state"] != "proposed")
        registered = lenses.register_findings(
            db, manuscript, session, "clarity", "01-choice.md",
            [{"quote": grounded_quote, "note": "External agent finding."},
             {"quote": "still nowhere", "note": "dropped"}])
        check("registration door hygiene-gates external findings into the "
              "same store",
              len(registered["findings"]) == 1
              and registered["dropped_ungrounded"] == 1
              and _json.loads(registered["findings"][0]["metadata"])[
                  "source"] == "external")

        # --- illustration placement pipeline: scan → stage → triage ---
        from authorlm import placement

        anchor_para = ("The road is a ladder laid flat, and every step "
                       "asks again.")
        filler = " ".join(["The road runs on and the walker keeps walking "
                           "toward what is not yet."] * 120)
        (ms / "04-road.md").write_text(
            f"# **The Road**\n\n{filler}\n\n{anchor_para}\n")
        api.collect(db, manuscript, {})
        api.add_style_law(db, manuscript, "illustration-placement",
                              "Concretize a recurring metaphor once, at "
                              "its strongest occurrence.",
                              file="04-road.md")
        spot_llm = FakeLLM({"proposals": [
            {"anchor": anchor_para,
             "description": "a ladder lying flat along a road, rungs "
                            "receding to the horizon",
             "criterion": "2", "rationale": "metaphor at its strongest",
             "revises": None},
            {"anchor": "no such text anywhere",
             "description": "dropped", "criterion": "2",
             "rationale": "bad anchor", "revises": None},
        ]})
        report = placement.scan(db, manuscript, spot_llm,
                                files=["04-road.md"])
        check("spot-finder stages verbatim-verified proposals and drops "
              "unverifiable anchors",
              len(report["staged"]) == 1
              and report["dropped_unverifiable"] == 1
              and "PLACEMENT LAW" in spot_llm.user
              and "BUDGET" in spot_llm.user
              and "strongest occurrence" in spot_llm.user, str(report))
        check("scan is idempotent against open proposals",
              placement.scan(db, manuscript, spot_llm,
                             files=["04-road.md"])["staged"] == [])
        staged = placement.open_proposals(db, manuscript["id"])[0]
        result = placement.decide(
            db, manuscript, staged["id"], "accept",
            revised_description="a ladder lying flat along an empty road")
        text_now = (ms / "04-road.md").read_text()
        check("modified acceptance writes the revised tag after the anchor",
              result["modified"]
              and ("[Illustration: a ladder lying flat along an empty "
                   "road]") in text_now
              and text_now.index(anchor_para)
              < text_now.index("[Illustration: a ladder"), text_now[-300:])
        ev_row = db.one(
            "SELECT * FROM evidence WHERE evidence_type = 'illus_triage' "
            "ORDER BY created_at DESC")
        check("triage revision lands as modified evidence with the diff",
              ev_row["signal"] == "modified"
              and "became" in ev_row["target"], str(dict(ev_row)))
        # Reject with a reason; then stale when the anchor vanishes.
        spot_llm2 = FakeLLM({"proposals": [
            {"anchor": anchor_para, "description": "second idea",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, spot_llm2, files=["04-road.md"])
        p2 = placement.open_proposals(db, manuscript["id"])[0]
        placement.decide(db, manuscript, p2["id"], "reject",
                         reason="Too decorative for this essay.")
        check("rejection records the author's reason verbatim",
              _json.loads(db.one(
                  "SELECT metadata FROM evidence WHERE evidence_type = "
                  "'illus_triage' ORDER BY created_at DESC")["metadata"])
              ["explanation"] == "Too decorative for this essay.")
        check("a rejected proposal never resurrects on re-scan",
              placement.scan(db, manuscript, spot_llm2,
                             files=["04-road.md"])["staged"] == [])
        spot_llm3 = FakeLLM({"proposals": [
            {"anchor": anchor_para, "description": "third idea",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, spot_llm3, files=["04-road.md"])
        (ms / "04-road.md").write_text(
            (ms / "04-road.md").read_text().replace(anchor_para,
                                                    "The road changed."))
        staled = placement.sweep_stale(db, manuscript)
        check("proposals whose anchor vanished go stale, never guessed",
              len(staled) == 1
              and not placement.open_proposals(db, manuscript["id"]))

        # --- externalized descriptions: ⇢ ref grammar, hash stability,
        # machine-maintained excerpts, offers ---
        from authorlm import illus as il

        long_desc = ("a brass orrery on a scarred oak table, " * 8
                     + "lit from the left by a single candle")
        (ms / "06-orrery.md").write_text(
            "# **Orrery**\n\nBody.\n\n"
            f"[Illustration: {long_desc} | caption: the orrery]\n")
        api.collect(db, manuscript, {})
        inline_slot = il.find_slot(ms, "orrery")[0]
        h_before = inline_slot["desc_hash"]
        offers = il.externalize_offers(ms)
        check("a 50+ word inline description draws an externalize offer",
              any("longer than 50 words" in o["reason"] for o in offers),
              str(offers))
        out = il.externalize(ms, inline_slot)
        ref_slot = il.find_slot(ms, "orrery")[0]
        check("externalize keeps desc_hash (renders survive) and refs "
              "the prompts file",
              out["desc_hash"] == h_before
              and ref_slot["desc_hash"] == h_before
              and ref_slot["ref"] == out["ref"]
              and (ms / "_illustrations" / "prompts" / out["ref"]).exists()
              and ref_slot["caption"] == "the orrery"
              and "⇢" in (ms / "06-orrery.md").read_text())
        # Edit the excerpt in the tag: maintenance discards the edit.
        tampered = (ms / "06-orrery.md").read_text().replace(
            "a brass orrery on a scarred", "a HAND-EDITED excerpt on a scarred")
        (ms / "06-orrery.md").write_text(tampered)
        fixes = il.maintain_excerpts(ms)
        check("excerpt edits are discarded and called out",
              any(f.get("discarded_edit") for f in fixes)
              and "HAND-EDITED" not in (ms / "06-orrery.md").read_text())
        # Edit the canonical file: excerpt refreshes, desc_hash moves.
        (ms / "_illustrations" / "prompts" / out["ref"]).write_text(
            "a silver orrery beneath a cracked dome\n")
        il.maintain_excerpts(ms)
        moved = il.find_slot(ms, "silver orrery")[0]
        check("editing the prompts file is canonical: hash moves, "
              "excerpt follows",
              moved["desc_hash"] != h_before
              and moved["excerpt"].startswith("a silver orrery"))
        # Note: craft_text ignores `workspace` — the craft file moved from
        # <workspace>/.authorlm/ to the project root with the rest of the
        # editorial config (authorlm/paths.py); every caller reads the
        # one repo-shared file now. `workspace=str(root)` is kept below
        # only because craft_text still accepts (and ignores) the param.
        check("craft file seeds and serves the active model's carveout",
              "bound attributes" in il.craft_text(
                  {"llm": {"image_model": "gpt-image-2"}},
                  workspace=str(root))
              and "Renders lettering reliably" in il.craft_text(
                  {"llm": {"image_model": "gpt-image-2"}},
                  workspace=str(root))
              and "Renders lettering reliably" not in il.craft_text(
                  {"llm": {"image_model": "other/model"}},
                  workspace=str(root)))
        api.collect(db, manuscript, {})

        # --- Doc mirror: the reserved 'illustrations' tab tree ---
        from authorlm.gdocs import (ILLUS_DISPLAY_PREFIX, ILLUS_SENTINEL,
                                    ILLUS_TAB_TITLE, push_prompt_tabs)

        manuscript = api.get_manuscript(db)
        ref = out["ref"]
        prompt_path = ms / "_illustrations" / "prompts" / ref
        display = ILLUS_DISPLAY_PREFIX + ref
        prom = push_prompt_tabs(db, manuscript, stub, stub)
        master = stub.state["docs"]["doc-2"]
        illus_tab = next(t for t in master if t["title"] == ILLUS_TAB_TITLE)
        slug_tab = next(t for t in master if t["title"] == ref)
        check("full push mirrors the prompt file into the reserved tree",
              prom["created"] == [ref] and not prom["updated"]
              and slug_tab.get("parent") == illus_tab["id"]
              and illus_tab.get("parent") is None
              and slug_tab["text"].strip()
              == "a silver orrery beneath a cracked dome"
              and illus_tab["text"] == ILLUS_SENTINEL, str(prom))
        manuscript = api.get_manuscript(db)
        links_now = doc_status(db, manuscript)
        entry_now = links_now["_illusprompt/" + ref]
        check("reserved mapping keys record the tab and the pushed base",
              entry_now["tab_id"] == slug_tab["id"]
              and entry_now["pushed_hash"]
              and links_now["_illustrations_tab"] == illus_tab["id"],
              str(entry_now))

        temp_docs_before = stub.state["counter"]
        prom2 = push_prompt_tabs(db, manuscript, stub, stub)
        check("an unchanged prompt pushes nothing (changed-only)",
              not prom2["created"] and not prom2["updated"]
              and not prom2["pruned"]
              and stub.state["counter"] == temp_docs_before, str(prom2))

        # Doc-side edit → pull writes the canonical file; collect's
        # excerpt maintenance then follows the new text.
        stub.set_tab(ref, "a silver orrery beneath a shattered dome")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("a Doc-side prompt edit pulls into the canonical file, "
              "never adopted as an essay",
              display in rep["changed"]
              and prompt_path.read_text()
              == "a silver orrery beneath a shattered dome\n"
              and not rep.get("adopted")
              and ref not in doc_status(db, api.get_manuscript(db)),
              str(rep))
        il.maintain_excerpts(ms)
        check("the owning tag's excerpt follows the pulled text",
              il.find_slot(ms, "shattered dome")[0]["excerpt"]
              .startswith("a silver orrery beneath a shattered"), "")

        prompt_path.write_text("a bronze orrery in candlelight\n")
        stub.set_tab(ref, "a golden orrery at dawn")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("two-sided prompt edits conflict, file untouched",
              display in rep["conflicts"]
              and "bronze" in prompt_path.read_text(), str(rep))
        forced = pull_doc(db, manuscript, ref, service=stub,
                          docs_service=stub, force=True)
        check("targeted prompt pull resolves by slug; --force takes the Doc",
              display in forced["changed"]
              and prompt_path.read_text() == "a golden orrery at dawn\n",
              str(forced))

        prompt_path.write_text("a golden orrery at dusk\n")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("a local-only prompt edit is kept (tab stale, not rival)",
              display in rep["local_ahead"]
              and "dusk" in prompt_path.read_text(), str(rep))
        prom3 = push_prompt_tabs(db, manuscript, stub, stub)
        slug_tab = next(t for t in stub.state["docs"]["doc-2"]
                        if t["title"] == ref)
        check("push rewrites only the drifted prompt tab",
              prom3["updated"] == [ref] and not prom3["created"]
              and slug_tab["text"].strip() == "a golden orrery at dusk",
              str(prom3))

        # A hand-made tab in the reserved tree: warned, never adopted.
        stub.state["tab_counter"] += 1
        stub.state["docs"]["doc-2"].append(
            {"id": f"tab-{stub.state['tab_counter']}", "title": "scratch.md",
             "text": "a hand-made note", "parent": illus_tab["id"]})
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        links_after = doc_status(db, api.get_manuscript(db))
        check("hand-made tabs under the reserved tree are inert: warned, "
              "no essay adoption, no local file",
              rep.get("prompt_unknown") == ["scratch.md"]
              and not rep.get("adopted")
              and "scratch.md" not in links_after
              and not (ms / "scratch.md").exists(), str(rep))
        prom4 = push_prompt_tabs(db, manuscript, stub, stub)
        check("push leaves the hand-made tab untouched",
              prom4["unknown"] == ["scratch.md"] and not prom4["pruned"],
              str(prom4))

        slug_tab["title"] = "renamed-by-hand.md"
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("a renamed prompt tab is skipped and called out",
              rep.get("prompt_renamed") == [(ref, "renamed-by-hand.md")]
              and "dusk" in prompt_path.read_text(), str(rep))
        slug_tab["title"] = ref

        prompt_path.unlink()
        prom5 = push_prompt_tabs(db, manuscript, stub, stub)
        check("a locally deleted prompt file prunes its tab and mapping",
              prom5["pruned"] == [ref]
              and all(t["title"] != ref
                      for t in stub.state["docs"]["doc-2"])
              and "_illusprompt/" + ref
              not in doc_status(db, api.get_manuscript(db)), str(prom5))
        prompt_path.write_text("a golden orrery at dusk\n")
        prom6 = push_prompt_tabs(db, manuscript, stub, stub)
        check("the file's return recreates its tab (Doc-side deletions "
              "recover the same way)",
              prom6["created"] == [ref], str(prom6))

        stub.set_tab(ref, "a silver orrery reconsidered")
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("reconcile auto-pulls a Doc-side prompt edit",
              display in report["pulled"]
              and prompt_path.read_text()
              == "a silver orrery reconsidered\n", str(report))
        prompt_path.write_text("a silver orrery reconsidered twice\n")
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("reconcile auto-pushes a local-only prompt edit",
              display in report["pushed"]
              and next(t["text"] for t in stub.state["docs"]["doc-2"]
                       if t["title"] == ref).strip()
              == "a silver orrery reconsidered twice", str(report))
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("prompt tabs settle in sync at session start",
              display in report["in_sync"], str(report))

        # A hand-made tab is not a manuscript file, but it IS a split
        # boundary: without that, its heading and body are swallowed by
        # whichever tab precedes it in the export and then auto-pulled
        # over that file as if the author had written them
        # (it-307dc1279a7e — a real 'Tab 31' after the last prompt tab).
        settled = prompt_path.read_text()
        stub.state["docs"]["doc-2"].append(
            {"id": "tab-handmade", "title": "Tab 31",
             "text": "notes to self\nnot part of any chapter",
             "parent": None})
        report = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("a hand-made tab is a split boundary — the preceding file "
              "keeps its own text and the tab is reported, not merged",
              prompt_path.read_text() == settled
              and "Tab 31" not in prompt_path.read_text()
              and display in report["in_sync"]
              and "Tab 31" in report.get("ignored_tabs", []), str(report))
        stub.state["docs"]["doc-2"].pop()
        il.maintain_excerpts(ms)

        # --- prompt preview embeds: ![](../…) derived machinery ---
        from authorlm.illus import load_prompts

        orrery_slot = il.find_slot(ms, "reconsidered twice")[0]
        cand = f"a-silver-orrery-{orrery_slot['desc_hash']}-0000-01.png"
        (ms / "_illustrations" / cand).write_bytes(tiny_png())
        ok_pin = il.set_embed(ms / "06-orrery.md",
                              orrery_slot["desc_hash"], cand,
                              il.load_prompts(ms))
        check("set_embed finds an externalized slot by canonical hash",
              ok_pin and f"![](_illustrations/{cand})"
              in (ms / "06-orrery.md").read_text(), "")
        fixes = il.maintain_excerpts(ms)
        check("maintenance mirrors the essay's pick into the prompt file",
              any(f.get("embed_synced") for f in fixes)
              and prompt_path.read_text().endswith(
                  f"\n\n![](../{cand})\n"), prompt_path.read_text())
        check("preview embeds are invisible to identity",
              load_prompts(ms)[ref]
              == "a silver orrery reconsidered twice\n"
              and il.find_slot(ms, "reconsidered twice")[0]["desc_hash"]
              == orrery_slot["desc_hash"], "")
        prom7 = push_prompt_tabs(db, manuscript, stub, stub)
        check("a preview embed alone never re-pushes; the Doc never "
              "sees it",
              not prom7["updated"] and not prom7["created"]
              and "![](" not in next(
                  t["text"] for t in stub.state["docs"]["doc-2"]
                  if t["title"] == ref), str(prom7))
        stub.set_tab(ref, "a silver orrery, final wording")
        rep = pull_doc(db, manuscript, service=stub, docs_service=stub)
        ptext = prompt_path.read_text()
        check("pull rewrites the text and keeps the preview embed",
              display in rep["changed"]
              and ptext.startswith("a silver orrery, final wording\n")
              and ptext.rstrip().endswith(f"![](../{cand})"), ptext)
        (ms / "06-orrery.md").write_text(
            (ms / "06-orrery.md").read_text().replace(
                f"![](_illustrations/{cand})\n", ""))
        il.maintain_excerpts(ms)
        check("unpinning in the essay clears the preview embed",
              "![](" not in prompt_path.read_text(),
              prompt_path.read_text())

        # --- X7-3: pre-pull recovery point for _illustrations/prompts/ ---
        # These files hold the CANONICAL author text — collect_revision
        # skips '_'-prefixed dirs on purpose, so nothing else snapshots
        # them, yet a pull overwrites them from the Doc. Prove: (1) a
        # forced pull genuinely destroys the local text, (2) the author
        # can actually get it back, (3) the snapshot table doesn't flood
        # on ordinary observation or on repeated no-op pulls.
        def _prompt_version_rows():
            return db.all(
                "SELECT * FROM illustration_prompt_versions WHERE "
                "manuscript_id = ? ORDER BY version_no", (manuscript["id"],))

        precious_text = ("The author's carefully chosen words for this "
                         "orrery scene, never to be lost.\n")
        prompt_path.write_text(precious_text)
        before_rows = len(_prompt_version_rows())
        stub.set_tab(ref, "Doc-side wording that will destroy the above")
        destroyed = pull_doc(db, manuscript, service=stub, docs_service=stub,
                             force=True)
        check("X7-3 PRE-FIX REGRESSION: a forced pull overwrites the local "
              "prompt file — the author's precious text is gone from disk",
              display in destroyed["changed"]
              and precious_text != prompt_path.read_text(),
              prompt_path.read_text())
        check("the pre-pull snapshot was taken before the destructive "
              "write (one new row, holding the about-to-be-lost text)",
              len(_prompt_version_rows()) == before_rows + 1
              and loads(_prompt_version_rows()[-1]["files"], {}).get(ref)
              == precious_text,
              [dict(r) for r in _prompt_version_rows()])
        recovered = il.restore_prompts(db, manuscript)
        check("X7-3 RECOVERY: restore_prompts writes the exact pre-pull "
              "text back to disk — the author gets it back",
              ref in recovered["restored"]
              and prompt_path.read_text() == precious_text,
              prompt_path.read_text())
        settled_rows = len(_prompt_version_rows())
        settled = pull_doc(db, manuscript, service=stub, docs_service=stub)
        check("after recovery, an ordinary (non-forced) pull leaves the "
              "restored text alone (three-way sees it as local-ahead, not "
              "a silent re-overwrite)",
              display in settled["local_ahead"]
              and prompt_path.read_text() == precious_text, str(settled))
        check("no flooding: a pull whose disk state matches the last "
              "snapshot adds no new row (skip-if-unchanged, like backup.py "
              "and collect_revision)",
              len(_prompt_version_rows()) == settled_rows,
              [dict(r) for r in _prompt_version_rows()])
        collect_rows_before = len(_prompt_version_rows())
        api.collect(db, manuscript, {})
        check("ordinary observation (api.collect) never touches the "
              "illustration-prompt snapshot table — _illustrations/ stays "
              "observation-invisible, no version-history flooding",
              len(_prompt_version_rows()) == collect_rows_before)
        try:
            il.restore_prompts(db, manuscript, version_no=999999)
            raise AssertionError("expected LookupError")
        except LookupError as err:
            check("restoring a nonexistent snapshot version raises, "
                  "never guesses",
                  "999999" not in str(err) or "no illustration-prompt"
                  in str(err), str(err))

        # --- X7-3-cli: 'illus restore' / 'illus versions' via cli_main ---
        # The recovery mechanism only counts if the author can reach it
        # without hand-running Python — prove the CLI verb itself,
        # end-to-end through cli_main, not the API function directly.
        cli_precious = "A second precious edit, restored via the CLI.\n"
        prompt_path.write_text(cli_precious)
        stub.set_tab(ref, "Doc wording that would destroy the CLI text")
        pull_doc(db, manuscript, service=stub, docs_service=stub, force=True)
        check("X7-3-cli PRE-FIX REGRESSION setup: the destructive pull "
              "still clobbers the file",
              cli_precious != prompt_path.read_text(), prompt_path.read_text())

        versions_out = io.StringIO()
        with contextlib.redirect_stdout(versions_out):
            cli_main(["--workspace", str(ws), "illus", "versions"])
        versions_text = versions_out.getvalue()
        check("'illus versions' lists the snapshots (version, timestamp, "
              "file count, triggering source) — how the author picks "
              "--version N meaningfully",
              "pre-doc-pull" in versions_text
              and "file(s)" in versions_text
              and "v1" in versions_text, versions_text)

        restore_out = io.StringIO()
        with contextlib.redirect_stdout(restore_out):
            cli_main(["--workspace", str(ws), "illus", "restore"])
        restore_text = restore_out.getvalue()
        check("X7-3-cli RECOVERY: 'illus restore' actually restores the "
              "destroyed prompt file, end-to-end through the CLI (not "
              "just the Python API)",
              prompt_path.read_text() == cli_precious, prompt_path.read_text())
        check("the restore CLI prints the plan — which file(s), from "
              "which snapshot — BEFORE doing the overwrite",
              ref in restore_text
              and "Restoring illustration prompts from snapshot v"
              in restore_text, restore_text)
        check("the restore CLI warns plainly that existing content will "
              "be overwritten",
              "OVERWRITES" in restore_text, restore_text)
        check("the restore CLI states the restore is additive (not a "
              "wholesale revert) — newer prompt files are left alone",
              "not a wholesale revert" in restore_text
              and "left untouched" in restore_text, restore_text)

        bad_out = io.StringIO()
        bad_err = None
        try:
            with contextlib.redirect_stdout(bad_out):
                cli_main(["--workspace", str(ws), "illus", "restore",
                         "--version", "999999"])
        except SystemExit as exc:
            bad_err = str(exc)
        check("a nonexistent --version produces a clean error message, "
              "never a traceback",
              bad_err is not None and "999999" in bad_err
              and "no illustration-prompt snapshot" in bad_err
              and not bad_out.getvalue(), bad_err)

        # --- comment-anchor safety: pushes route around open comments ---
        from authorlm.gdocs import comment_bearing, manuscript_bridge

        (ms / "06-orrery.md").write_text(
            "# Orrery\n\nBrass planets on brass rails.\n\n"
            "A distinctive orrery sentence to anchor a comment.\n")
        push_doc(db, manuscript, "06-orrery.md", service=stub,
                 docs_service=stub)  # rebuild path: margin is empty
        stub.add_comment("c-anchor", "distinctive orrery sentence",
                         "Please reconsider this line.")
        (ms / "06-orrery.md").write_text(
            "# Orrery\n\nBrass planets on brass rails, polished.\n\n"
            "A distinctive orrery sentence to anchor a comment.\n")
        guarded = push_doc(db, manuscript, "06-orrery.md", service=stub,
                           docs_service=stub)
        check("a push on a comment-bearing tab goes surgical",
              guarded.get("mode") == "diff", str(guarded))
        stub.add_comment("c-nowhere", "a quote matching no file at all",
                         "Where does this belong?")
        check("an unattributable anchor makes every mapped tab bearing",
              comment_bearing(db, manuscript,
                              manuscript_bridge(manuscript),
                              "01-choice.md", stub) is True, "")
        stub.state["comments"]["c-anchor"]["resolved"] = True
        stub.state["comments"]["c-nowhere"]["resolved"] = True
        cleared = push_doc(db, manuscript, "06-orrery.md", service=stub,
                           docs_service=stub)
        check("a settled margin restores the rebuild path",
              "mode" not in cleared, str(cleared))

        # Critique pending forms are invisible to open_threads (author_comment
        # only). A rebuild push during the pause would wipe <<old>>{{new}} /
        # {{insert}} forms — including the author's Doc post-edits — and
        # session-start reconcile auto-push takes the same path.
        from authorlm.db import ko_fields as _ko_dt

        pending_tab = (
            "# Orrery\n"
            "<<Brass planets on brass rails, polished.>>"
            "{{Brass planets, post-edited in Docs.}}\n\n"
            "{{A bridging insert the author refined.}}\n\n"
            "A distinctive orrery sentence to anchor a comment.\n")
        stub.set_tab("06-orrery.md", pending_tab)
        crit = _ko_dt("dt")
        crit.update(
            manuscript_id=manuscript["id"], origin_type="critique",
            origin_id="pass:06-orrery.md:1", file="06-orrery.md",
            proposed_old="Brass planets on brass rails, polished.",
            proposed_new="Brass planets, post-edited in Docs.",
            note="critique", state="written", our_reply_ids="[]",
            metadata='{"kind":"replace","anchor_paragraph":1}')
        db.insert("doc_threads", crit)
        tab_before = next(t["text"] for t in stub.state["docs"]["doc-2"]
                          if t["title"] == "06-orrery.md")
        raised = None
        try:
            push_doc(db, manuscript, "06-orrery.md", service=stub,
                     docs_service=stub)
        except LookupError as err:
            raised = str(err)
        tab_after = next(t["text"] for t in stub.state["docs"]["doc-2"]
                         if t["title"] == "06-orrery.md")
        check("push refuses while critique forms are written (no wipe)",
              raised is not None
              and "critique pending forms" in raised
              and "critique resolve" in raised
              and tab_after == tab_before
              and "post-edited in Docs" in tab_after
              and "{{A bridging insert the author refined.}}" in tab_after,
              str({"raised": raised, "tab": tab_after[:120]}))
        db.update("doc_threads", crit["id"], {"state": "cleaned"})

        # --- session start is no longer blind to the margin ---
        stub.add_comment("c-fresh", "distinctive orrery sentence",
                         "Is brass the right metal?")
        rep_h = reconcile(db, api.get_manuscript(db), stub,
                          docs_service=stub)
        check("reconcile harvests open comments at session start",
              any("right metal" in c["content"]
                  for c in rep_h.get("comments", [])), str(rep_h))
        rep_h2 = reconcile(db, api.get_manuscript(db), stub,
                           docs_service=stub)
        check("re-reconcile never re-ingests the same comment",
              not rep_h2.get("comments"), str(rep_h2.get("comments")))

        # --- scoped concept fetch + batch curation (token plan) ---
        api.add_concept(db, manuscript, "Winding Path",
                        notes="the road that bends")
        api.add_concept(db, manuscript, "Iron Gate",
                        notes="entry that resists")
        api.add_concept(db, manuscript, "Brass Planets",
                        notes="the orrery's cargo")
        api.link_concepts(db, manuscript, "Winding Path", "leads_to",
                          "Iron Gate")
        over = api.concept_overview(db, manuscript)
        check("unscoped fetch is a summary, never a dump",
              "nodes" not in over and over["node_count"] >= 3
              and "Winding Path" in over["recently_added"]
              and "narrow" in over["guidance"].lower(), str(over)[:200])
        hit = api.scoped_concepts(db, manuscript, query="iron gate")
        check("query scope returns the matching slice only",
              hit["node_count"] == 1
              and any("Iron Gate" in str(n) for n in hit["nodes"]),
              str(hit))
        sliced = api.scoped_concepts(db, manuscript, file="06-orrery.md")
        check("file scope selects concepts realized in the essay, "
              "edges restricted to the slice",
              sliced["node_count"] == 1
              and any("Brass Planets" in str(n) for n in sliced["nodes"])
              and sliced["edge_count"] == 0, str(sliced))
        batch = api.curate_concepts(db, manuscript, [
            {"op": "confirm", "name": "Winding Path"},
            {"op": "alias", "name": "Iron Gate", "aliases": ["Portcullis"]},
            {"op": "retire", "name": "Brass Planets"},
            {"op": "retire", "name": "No Such Concept"},
            {"op": "frobnicate", "name": "x"},
        ])
        check("batch curation applies in order, isolating failures",
              batch["applied"] == 3 and batch["failed"] == 2
              and batch["results"][3]["ok"] is False
              and "unknown op" in batch["results"][4]["error"], str(batch))
        check("batch results landed in the graph",
              api.scoped_concepts(db, manuscript,
                                  query="portcullis")["node_count"] == 1,
              "")
        cleanup = api.curate_concepts(db, manuscript, [
            {"op": "retire", "name": "Winding Path"},
            {"op": "retire", "name": "Iron Gate"},
        ])
        check("cleanup batch retires the scaffolding",
              cleanup["applied"] == 2 and cleanup["failed"] == 0,
              str(cleanup))

        # Deterministic guards: toc exclusion + pacing budget + arbiter.
        (ms / "toc.toml").write_text(
            '[[chapter]]\nfile = "01-choice.md"\n\n'
            '[[chapter]]\nfile = "00-intro.md"\n'
            'illustrations = "none"\n\n'
            '[[chapter]]\nfile = "04-road.md"\n')
        report = placement.scan(db, manuscript, spot_llm2,
                                files=["00-intro.md"])
        check("toc illustrations=none files are never scanned",
              report["calls"] == 0 and report["files"] == [], str(report))
        (ms / "05-tiny.md").write_text(
            "# **Tiny**\n\nShort words here.\n\n"
            "[Illustration: existing emblem]\n")
        tiny_report = placement.scan(db, manuscript, spot_llm2,
                                     files=["05-tiny.md"])
        check("a chapter at its pacing budget is skipped deterministically",
              tiny_report["calls"] == 0
              and tiny_report["skipped_at_budget"] == ["05-tiny.md"],
              str(tiny_report))
        # Arbiter: two duplicate-metaphor proposals across files; the
        # arbiter keeps one, cuts the other with a reason — no evidence.
        anchor_choice = "And the second is like unto the first."
        (ms / "01-choice.md").write_text(
            (ms / "01-choice.md").read_text() + f"\n\n{anchor_choice}\n")
        dup0 = FakeLLM({"proposals": [
            {"anchor": "The road changed.",
             "description": "a ladder to the clouds",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, dup0, files=["04-road.md"])
        dup1 = FakeLLM({"proposals": [
            {"anchor": anchor_choice, "description": "a ladder to the sky",
             "criterion": "2", "rationale": "r", "revises": None}]})
        placement.scan(db, manuscript, dup1, files=["01-choice.md"])
        pid = [r["id"] for r in placement.open_proposals(db, manuscript["id"])
               if r["file"] == "01-choice.md"][0]
        ev_before = db.one("SELECT COUNT(*) AS n FROM evidence "
                           "WHERE evidence_type = 'illus_triage'")["n"]
        arb_llm = FakeLLM({"cut": [{"id": pid,
                                    "reason": "duplicate ladder home"}]})
        arb = placement.arbitrate(db, manuscript, arb_llm)
        cut_row = dict(db.one(
            "SELECT * FROM illus_proposals WHERE id = ?", (pid,)))
        check("arbiter cuts duplicates with a reason, never as evidence",
              [c["id"] for c in arb["arbiter_cut"]] == [pid]
              and cut_row["state"] == "rejected"
              and _json.loads(cut_row["metadata"])["by"] == "arbiter"
              and db.one("SELECT COUNT(*) AS n FROM evidence WHERE "
                         "evidence_type = 'illus_triage'")["n"] == ev_before,
              str(arb))

        # Revision acceptance must treat the new description as literal text
        # — a string-template re.sub interprets \1/\U as group refs/escapes.
        from authorlm.db import ko_fields as _ko

        (ms / "06-revise.md").write_text(
            "# **Revise**\n\nAn emblem stands at the gate.\n\n"
            "[Illustration: old emblem at the gate]\n")
        rev_row = _ko("ip")
        rev_row.update(
            manuscript_id=manuscript["id"], file="06-revise.md",
            anchor="An emblem stands at the gate.",
            description=r"emblem under C:\Users\author\figs with \1 mark",
            criterion="2", rationale="clearer",
            revises="old emblem at the gate", state="proposed")
        db.insert("illus_proposals", rev_row)
        rev_result = placement.decide(db, manuscript, rev_row["id"], "accept")
        rev_text = (ms / "06-revise.md").read_text()
        check("revision accept writes backslash-rich descriptions literally",
              rev_result["state"] == "accepted"
              and r"C:\Users\author\figs with \1 mark" in rev_text
              and "[Illustration: old emblem at the gate]" not in rev_text,
              rev_text)

        # Multi-pass extraction must not advance the watermark when a later
        # pass returns None — otherwise the missed payload is skipped forever.
        from authorlm.extraction import extract_concepts

        class FlakyLLM:
            enabled = True
            extraction_max_chars = 80

            def __init__(self):
                self.calls = 0

            def complete_json(self, system, user, thinking_budget=None):
                self.calls += 1
                if self.calls == 1:
                    return {"concepts": [
                        {"name": "AlphaConcept", "kind": "definition",
                         "notes": "first payload"}],
                            "links": [], "aliases": []}
                return None  # second payload fails

            def stats_line(self):
                return None

        # Two distinct contexts, not one paragraph repeated: the recurrence
        # bar admits a concept only when it appears in separate contexts,
        # and eight repeats in a single paragraph is one context — the
        # rule landed after this test was written.
        (ms / "07-alpha.md").write_text(
            "# Alpha\n\n" + ("AlphaConcept appears here. " * 4) + "\n\n"
            "## Later\n\n" + ("AlphaConcept returns here. " * 4) + "\n")
        (ms / "08-beta.md").write_text(
            "# Beta\n\n" + ("BetaConcept appears here. " * 8) + "\n")
        api.collect(db, manuscript, {})
        latest = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        before_meta = _json.loads(manuscript["metadata"] or "{}")
        flaky = FlakyLLM()
        multi = extract_concepts(
            db, manuscript, flaky, files=["07-alpha.md", "08-beta.md"])
        after_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        check("multi-pass LLM miss leaves watermark unadvanced",
              multi is not None
              and multi.get("incomplete") is True
              and after_meta.get("last_extracted_version")
              == before_meta.get("last_extracted_version")
              and after_meta.get("last_extracted_version") != latest["id"],
              str({"multi": multi, "before": before_meta, "after": after_meta,
                   "latest": latest["id"], "calls": flaky.calls}))
        check("first multi-pass payload still commits its concepts",
              db.one("SELECT id FROM concept_nodes WHERE manuscript_id = ? "
                     "AND name = ?",
                     (manuscript["id"], "AlphaConcept")) is not None,
              str(multi))

        # --- BUG-7: extraction must never overwrite the author's ratified
        # concept notes, even when a materially different paraphrase comes
        # back for text the author actually changed. ---
        (ms / "09-ratified.md").write_text(
            "# Ratified\n\nRatifiedTerm is discussed at length here.\n"
        )
        api.add_concept(db, manuscript, "RatifiedTerm", kind="concept",
                        notes="AUTHOR RATIFIED TEXT")
        note_proposals_before = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals "
            "WHERE manuscript_id = ? AND kind = 'note_update'",
            (manuscript["id"],))["n"]

        class NoteOverwriteLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "RatifiedTerm", "kind": "concept",
                     "notes": "machine paraphrase of the definition"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        extract_concepts(db, manuscript, NoteOverwriteLLM(),
                         files=["09-ratified.md"])
        ratified_after = concepts.get_concept(db, manuscript["id"], "RatifiedTerm")
        note_proposals_after = db.one(
            "SELECT COUNT(*) AS n FROM knowledge_proposals "
            "WHERE manuscript_id = ? AND kind = 'note_update'",
            (manuscript["id"],))["n"]
        check("extraction never overwrites the author's ratified concept notes",
              ratified_after["notes"] == "AUTHOR RATIFIED TEXT",
              f"notes became: {ratified_after['notes']!r}")
        check("a materially different extraction files a note_update "
              "proposal instead of applying itself",
              note_proposals_after == note_proposals_before + 1,
              f"before={note_proposals_before} after={note_proposals_after}")

        # --- BUG-8: a hit on a concept's ALIAS (not its primary name) must
        # not be treated as a brand-new concept — that discards a confirmed
        # concept's metadata (including 'confirmed': True) and reopens it to
        # silent auto-retirement. ---
        (ms / "10-alias.md").write_text(
            "# Alias\n\nAliasSecondary shows up in the prose here.\n\n"
            "## Elsewhere\n\nAliasSecondary appears again in a different "
            "section, clearing the recurrence bar.\n"
        )
        api.add_concept(db, manuscript, "AliasPrimary", kind="concept",
                        notes="primary notes")
        api.confirm_concept(db, manuscript, "AliasPrimary")
        api.alias_concept(db, manuscript, "AliasPrimary", ["AliasSecondary"])
        primary_meta_before = _json.loads(
            concepts.get_concept(db, manuscript["id"], "AliasPrimary")["metadata"]
            or "{}")
        check("fixture concept starts confirmed",
              primary_meta_before.get("confirmed") is True,
              str(primary_meta_before))

        class AliasHitLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "AliasSecondary", "kind": "concept",
                     "notes": "some notes about the alias"}],
                        "links": [], "aliases": []}

            def stats_line(self):
                return None

        extract_concepts(db, manuscript, AliasHitLLM(), files=["10-alias.md"])
        primary_after = concepts.get_concept(db, manuscript["id"], "AliasPrimary")
        primary_meta_after = _json.loads(primary_after["metadata"] or "{}")
        check("an alias hit does not reset the primary concept's 'confirmed' flag",
              primary_meta_after.get("confirmed") is True,
              f"metadata became: {primary_meta_after}")
        check("an alias hit does not fork a duplicate concept node",
              db.one("SELECT COUNT(*) AS n FROM concept_nodes WHERE "
                     "manuscript_id = ? AND lower(name) = lower(?)",
                     (manuscript["id"], "AliasSecondary"))["n"] == 0)

        # --- BUG-21: a model-shaped-but-malformed reply (a list where a
        # string kind is expected, null where an empty list is expected)
        # must not abort the extraction pass with a TypeError. ---
        (ms / "11-coerce.md").write_text(
            "# Coerce\n\nCoerceTarget needs a name here.\n\n"
            "## Elsewhere\n\nCoerceTarget appears again in a different "
            "section, clearing the recurrence bar.\n"
        )

        class MalformedLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": [
                    {"name": "CoerceTarget", "kind": ["concept"]}],
                        "links": None, "aliases": []}

            def stats_line(self):
                return None

        coerce_error = None
        try:
            extract_concepts(db, manuscript, MalformedLLM(),
                             files=["11-coerce.md"])
        except TypeError as err:
            coerce_error = err
        check("a malformed 'kind' (list, not str) does not abort extraction",
              coerce_error is None, str(coerce_error))
        coerced_node = concepts.get_concept(db, manuscript["id"], "CoerceTarget")
        check("the malformed kind coerces to the 'concept' default",
              coerced_node is not None and coerced_node["kind"] == "concept",
              str(coerced_node))

        # --- BUG-21 follow-up: a null 'concepts' payload (as opposed to a
        # malformed item inside a present list) must be treated as a FAILED
        # pass, not a clean "nothing found" pass. Coercing null to []
        # (the original BUG-21 fix) let the watermark advance on a broken
        # reply, permanently skipping the section on every future
        # incremental run — a silent, unrecoverable content skip. ---
        (ms / "12-nullconcepts.md").write_text(
            "# NullConcepts\n\nNullConceptsTarget needs a name here.\n\n"
            "## Elsewhere\n\nNullConceptsTarget appears again in a "
            "different section, clearing the recurrence bar.\n"
        )

        class NullConceptsLLM:
            enabled = True
            extraction_max_chars = 24000

            def complete_json(self, system, user, thinking_budget=None):
                return {"concepts": None, "links": [], "aliases": []}

            def stats_line(self):
                return None

        api.collect(db, manuscript, {})
        nullconcepts_latest = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        # Query the DB directly rather than trusting the long-lived in-memory
        # `manuscript` dict: several extractions upstream in this same test
        # (FlakyLLM's first payload, NoteOverwriteLLM, AliasHitLLM,
        # MalformedLLM/BUG-21) have already advanced the real watermark in
        # the DB since `manuscript` was first bound.
        nullconcepts_before_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        nullconcepts_result = extract_concepts(
            db, manuscript, NullConceptsLLM(), files=["12-nullconcepts.md"])
        nullconcepts_after_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        check("a null 'concepts' payload is reported as a failed pass, "
              "not a clean miss",
              nullconcepts_result is None, str(nullconcepts_result))
        check("a null 'concepts' payload does not advance the watermark "
              "(the section stays eligible for re-mining)",
              nullconcepts_after_meta.get("last_extracted_version")
              == nullconcepts_before_meta.get("last_extracted_version")
              and nullconcepts_after_meta.get("last_extracted_version")
              != nullconcepts_latest["id"],
              str({"before": nullconcepts_before_meta,
                   "after": nullconcepts_after_meta,
                   "latest": nullconcepts_latest["id"]}))
        check("a null 'concepts' payload creates no concept node",
              concepts.get_concept(db, manuscript["id"], "NullConceptsTarget")
              is None)

        # --- BUG-5: the watermark write must re-read metadata from the DB
        # immediately before writing, not merge into an in-memory dict
        # captured before a multi-minute LLM call — otherwise a concurrent
        # write (e.g. a Google Docs mapping update) is clobbered wholesale. ---
        from authorlm.extraction import _set_extraction_watermark
        from authorlm.gdocs import _save_mapping

        stale_manuscript = dict(manuscript)  # snapshot "before the LLM call"
        _save_mapping(db, manuscript, {"gdocs": {"_master_id": "doc-survives"}})
        watermark_latest = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        _set_extraction_watermark(db, stale_manuscript, watermark_latest["id"])
        watermark_final_meta = _json.loads(
            db.one("SELECT metadata FROM manuscripts WHERE id = ?",
                   (manuscript["id"],))["metadata"] or "{}")
        check("the watermark write re-reads fresh metadata instead of "
              "clobbering a concurrent write with a stale in-memory copy",
              watermark_final_meta.get("gdocs", {}).get("_master_id")
              == "doc-survives"
              and watermark_final_meta.get("last_extracted_version")
              == watermark_latest["id"],
              str(watermark_final_meta))
        manuscript = api.get_manuscript(db)  # refresh metadata

        # --- prerequisite-gap first mentions: terms of art, not casual words ---
        # Repro from improvement task it-e34cf5227223: 'wandered through time
        # and space' must not count as the first mention of concept 'Space'.
        from authorlm.guidance import _first_mentions
        gap_files = {"01.md": (
            "The Dead wandered through time and space in search of redemption.\n\n"
            "The mutable we name the realm of qualities.\n\n"
            "When ye discern difference in position and direction, ye name it Space.\n"
        )}
        gap_names = {"n-space": ["Space"], "n-realm": ["Realm of Qualities"]}
        mentions = _first_mentions(gap_files, gap_names)
        check("casual word use does not count as a concept mention",
              mentions["n-realm"] < mentions["n-space"],
              f"positions: {mentions}")
        check("multi-word names still match case-insensitively",
              "n-realm" in mentions)
        check("a concept only ever used casually has no first mention",
              "n-space" not in _first_mentions(
                  {"01.md": "They wandered through time and space.\n"},
                  {"n-space": ["Space"]}))
        heading_pos = _first_mentions(
            {"01.md": "## Discernment\n\nVirtue is chosen for effectiveness.\n",
             "02.md": "Discernment is the measure of distinctions.\n"},
            {"n-disc": ["Discernment"]})
        check("capitalization from headings and sentence starts counts",
              "n-disc" in heading_pos and heading_pos["n-disc"] < 20)
        from authorlm.guidance import PREREQUISITE_FIRST
        check("leads_to is an ordering relation (cause introduced before effect)",
              "leads_to" in PREREQUISITE_FIRST)

        # --- self-improvement tasks ---
        try:
            api.file_improvement(db, title="x", evidence="e", given="g",
                                 observed="", expected="v")
            check("file_improvement rejects an empty repro field", False)
        except ValueError:
            check("file_improvement rejects an empty repro field", True)

        task = api.file_improvement(
            db, title="Gap false positive on common words",
            evidence="'space' matched casually at file.md:9",
            given="a manuscript using 'time and space' casually before the concept intro",
            observed="prerequisite gap reported for Space",
            expected="casual word use must not count as a concept mention",
            manuscript=manuscript,
        )
        check("file_improvement stamps manuscript and session provenance",
              task["manuscript_id"] == manuscript["id"]
              and task["status"] == "open")
        import json as _json
        stamp = _json.loads(task["metadata"])
        latest_version = db.one(
            "SELECT id FROM manuscript_versions WHERE manuscript_id = ? "
            "ORDER BY version_no DESC LIMIT 1", (manuscript["id"],))
        check("filing stamps the current manuscript version (Time Machine)",
              stamp.get("manuscript_version_id") == latest_version["id"]
              and stamp.get("manuscript_version_no") is not None)
        check("filing stamps the source tree's git commit (Time Machine)",
              len(stamp.get("git_commit", "")) == 40
              and "git_dirty" in stamp)
        check("improvement tasks are listed as active",
              any(t["id"] == task["id"] for t in api.list_improvements(db, "active")))

        bundled = api.improvement_bundle(db, task["id"])
        check("improvement_bundle is self-contained and marks in_progress",
              bundled["status"] == "in_progress"
              and "file.md:9" in bundled["bundle"]
              and "casual word use must not count" in bundled["bundle"]
              and "tests/test_api.py" in bundled["bundle"])
        check("bundle carries the filing-time context (Time Machine)",
              stamp["git_commit"][:12] in bundled["bundle"]
              and latest_version["id"] in bundled["bundle"]
              and "git diff" in bundled["bundle"])
        rebundled = api.improvement_bundle(db, task["id"])
        check("re-emitting a bundle never regresses status",
              rebundled["status"] == "in_progress")

        try:
            api.resolve_improvement(db, task["id"], "close")
            check("close without a proposed resolution is refused", False)
        except ValueError:
            check("close without a proposed resolution is refused", True)
        api.resolve_improvement(db, task["id"], "propose",
                                note="tightened concept_pattern; test_gap_casual_words")
        closed = api.resolve_improvement(db, task["id"], "close")
        check("propose then close resolves with the fix on record",
              closed["status"] == "resolved")
        try:
            api.resolve_improvement(db, task["id"], "dismiss", note="n/a")
            check("a settled task cannot be re-transitioned", False)
        except ValueError:
            check("a settled task cannot be re-transitioned", True)

        task2 = api.file_improvement(db, title="t2", evidence="e2", given="g2",
                                     observed="o2", expected="x2")
        try:
            api.resolve_improvement(db, task2["id"], "dismiss")
            check("dismissal without a reason is refused", False)
        except ValueError:
            check("dismissal without a reason is refused", True)
        dismissed = api.resolve_improvement(db, task2["id"], "dismiss",
                                            note="intended behavior")
        check("dismissal with a reason lands with the reason on record",
              dismissed["status"] == "dismissed"
              and api.list_improvements(db, "dismissed")[0]["resolution"]
              == "intended behavior")
        check("improvement tasks never leak into briefings",
              "improvement" not in str(api.get_briefing(db, manuscript)).lower())

        # --- render / shell / triage resilience (live incidents 2026-08-10) ---
        import base64
        import contextlib
        import io
        import json as _rjson
        import sys
        import types
        import urllib.error
        from unittest import mock

        from authorlm import gdocs as gdocs_mod
        from authorlm import llm as llm_mod
        from authorlm import shell as shell_mod
        from authorlm.structure import matter_map, reading_order

        class _ImageResp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                png_b64 = base64.b64encode(b"PNGDATA").decode("ascii")
                return _rjson.dumps({
                    "candidates": [{"content": {"parts": [
                        {"inlineData": {"data": png_b64}}]}}]
                }).encode()

        retry_calls = {"n": 0}

        def urlopen_timeout_then_ok(request, timeout=None):
            retry_calls["n"] += 1
            retry_calls["timeout"] = timeout
            if retry_calls["n"] == 1:
                raise TimeoutError("stalled read")
            return _ImageResp()

        with mock.patch("urllib.request.urlopen", urlopen_timeout_then_ok):
            png = llm_mod._gemini_image("gemini-img", "a ladder", None,
                                        "fake-key", 17)
        check("gemini image call retries once after TimeoutError",
              png == b"PNGDATA" and retry_calls["n"] == 2
              and retry_calls["timeout"] == 17, str(retry_calls))

        http_calls = {"n": 0}

        def urlopen_http_error(request, timeout=None):
            http_calls["n"] += 1
            raise urllib.error.HTTPError(
                "https://example.test", 500, "boom", hdrs=None,
                fp=io.BytesIO(b'{"error":"no"}'))

        try:
            with mock.patch("urllib.request.urlopen", urlopen_http_error):
                llm_mod._gemini_image("gemini-img", "prompt", None,
                                      "fake-key", 17)
            check("gemini HTTPError is not retried as a transient stall",
                  False)
        except RuntimeError as err:
            check("gemini HTTPError is not retried as a transient stall",
                  http_calls["n"] == 1 and "500" in str(err), str(err))

        def boom_main(argv):
            raise RuntimeError("network down")

        out = io.StringIO()
        with mock.patch("authorlm.cli.main", boom_main), \
                contextlib.redirect_stdout(out):
            shell_mod._dispatch(["--workspace", str(ws)], ["status"])
        check("shell dispatch returns to the prompt after a command crash",
              "error (RuntimeError): network down" in out.getvalue(),
              out.getvalue())

        class DistillLLM:
            enabled = True

            def __init__(self):
                self.calls = []

            def complete(self, system, user):
                self.calls.append((system, user))
                return ('SCOPE: manuscript\n'
                        'STATEMENT: "Prefer concrete metaphors over '
                        'decorative ones."\n')

        distill_llm = DistillLLM()
        before_policies = db.one(
            "SELECT COUNT(*) AS n FROM editorial_beliefs "
            "WHERE manuscript_id = ?", (manuscript["id"],))["n"]
        check("distill_batch no-ops without items or an enabled LLM",
              placement.distill_batch(db, manuscript, [], distill_llm) is None
              and placement.distill_batch(
                  db, manuscript, [("01-choice.md", "rejected: x")],
                  types.SimpleNamespace(enabled=False)) is None)
        candidate = placement.distill_batch(
            db, manuscript,
            [("01-choice.md", "rejected: too decorative"),
             ("04-road.md", "description revised: «a» became «b»")],
            distill_llm)
        check("distill_batch makes one combined distiller call for the sitting",
              candidate is not None
              and len(distill_llm.calls) == 1
              and "too decorative" in distill_llm.calls[0][1]
              and "became «b»" in distill_llm.calls[0][1]
              and "at least two" in distill_llm.calls[0][1]
              and db.one(
                  "SELECT COUNT(*) AS n FROM editorial_beliefs "
                  "WHERE manuscript_id = ?",
                  (manuscript["id"],))["n"] == before_policies + 1,
              str(candidate))

        check("Google API socket timeout constant is 60s",
              gdocs_mod.HTTP_TIMEOUT_SECONDS == 60)
        fake_auth = types.ModuleType("google_auth_httplib2")
        seen_http = {}

        class _AuthorizedHttp:
            def __init__(self, creds, http=None):
                seen_http["creds"] = creds
                seen_http["http"] = http

        fake_auth.AuthorizedHttp = _AuthorizedHttp
        sys.modules["google_auth_httplib2"] = fake_auth
        try:
            with mock.patch.object(gdocs_mod, "get_credentials",
                                   return_value=object()):
                gdocs_mod._authorized_http({}, None, False)
            check("authorized Google HTTP client carries the socket timeout",
                  getattr(seen_http.get("http"), "timeout", None)
                  == gdocs_mod.HTTP_TIMEOUT_SECONDS,
                  str(seen_http))
        finally:
            sys.modules.pop("google_auth_httplib2", None)

        # --- SEC-3: cached Google OAuth tokens must be written 0600, not
        # world-readable, at both write sites (post-refresh and initial
        # consent) -- a long-lived refresh token readable by any other
        # local account grants full read/write of every manuscript Doc. ---
        import stat

        class _FakeGoogleCreds:
            def __init__(self, expired, refresh_token, valid_after_refresh=True):
                self.expired = expired
                self.refresh_token = refresh_token
                self.valid = not expired
                self._valid_after_refresh = valid_after_refresh

            def refresh(self, request):
                self.valid = self._valid_after_refresh
                self.expired = False

            def to_json(self):
                return _rjson.dumps({"token": "secret-refresh-token"})

        gdocs_sec3_ws = root / "gdocs-sec3-ws"
        gdocs_sec3_ws.mkdir()
        token_path = gdocs_sec3_ws / ".authorlm" / "gdocs_token.json"
        token_path.parent.mkdir(parents=True)
        token_path.write_text('{"token": "stale"}')

        with mock.patch(
            "google.oauth2.credentials.Credentials.from_authorized_user_file",
            return_value=_FakeGoogleCreds(expired=True, refresh_token="rt")):
            gdocs_mod.get_credentials({}, workspace=str(gdocs_sec3_ws))
        refreshed_mode = stat.S_IMODE(token_path.stat().st_mode)
        check("SEC-3: a refreshed Google token is written 0600, not "
              "world-readable",
              refreshed_mode == 0o600, oct(refreshed_mode))

        token_path.unlink()
        secret_path = gdocs_sec3_ws / "client_secret_fake.json"
        secret_path.write_text("{}")
        fake_flow = types.SimpleNamespace(
            run_local_server=lambda port=0: _FakeGoogleCreds(
                expired=False, refresh_token=None))
        with mock.patch(
            "google_auth_oauthlib.flow.InstalledAppFlow"
            ".from_client_secrets_file",
            return_value=fake_flow):
            gdocs_mod.get_credentials(
                {"gdocs": {"client_secret": str(secret_path)}},
                workspace=str(gdocs_sec3_ws))
        consented_mode = stat.S_IMODE(token_path.stat().st_mode)
        check("SEC-3: a freshly-consented Google token is written 0600 too",
              consented_mode == 0o600, oct(consented_mode))

        # toc.toml matter attributes + liberal parse (never raise)
        matter_files = {
            "toc.toml": (
                '[[chapter]]\nfile = "front.md"\nmatter = "front"\n\n'
                '[[chapter]]\nfile = "body.md"\n\n'
                '[[chapter]]\nfile = "app.md"\nmatter = "back"\n\n'
                '[[chapter]]\nfile = "weird.md"\nmatter = "sideways"\n'),
            "front.md": "f", "body.md": "b", "app.md": "a", "weird.md": "w",
            "orphan.md": "o",
        }
        check("matter_map reads front/main/back and defaults unknown/orphan",
              matter_map(matter_files) == {
                  "front.md": "front", "body.md": "main", "app.md": "back",
                  "weird.md": "main", "orphan.md": "main"},
              str(matter_map(matter_files)))
        broken = {"toc.toml": "[[chapter]\nnot toml", "z.md": "z", "a.md": "a"}
        ordered, missing = reading_order(broken)
        check("broken toc.toml degrades to alphabetical order, never raises",
              ordered == ["a.md", "z.md"] and missing == [],
              f"{ordered=} {missing=}")

        # --- belief curation: convert_belief happy path + error guards ---
        from authorlm import beliefs as pol

        api.define_style_guide(db, manuscript, "Curation guide")
        seeded = pol.seed_candidate_belief(
            db, manuscript["id"], "Prefer short paragraphs in dialogue.",
            source="test")
        converted = api.convert_belief(
            db, manuscript, seeded["id"], "formatting",
            guide="Curation guide", reason="now enforced as style law")
        check("convert_belief retires the belief and links a style law",
              converted["status"] == "retired" and converted["style_element"])
        row = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                     (seeded["id"],))
        curation = loads(row["metadata"], {}).get("curation", {})
        check("converted policy's metadata records the linkage and reason",
              row["status"] == "retired"
              and curation.get("action") == "converted"
              and curation.get("style_element") == converted["style_element"]
              and curation.get("reason") == "now enforced as style law")
        evidence = db.one(
            "SELECT * FROM evidence WHERE supports_belief = ? "
            "AND evidence_type = 'belief_curation'",
            (seeded["id"],))
        check("conversion writes a belief_curation evidence row",
              evidence is not None and evidence["signal"] == "converted")
        try:
            api.convert_belief(db, manuscript, seeded["id"], "formatting")
            check("converting an already-retired policy is refused", False)
        except LookupError:
            check("converting an already-retired policy is refused", True)
        try:
            api.convert_belief(db, manuscript, "no-such-prefix", "formatting")
            check("converting an unknown policy prefix is refused", False)
        except LookupError:
            check("converting an unknown policy prefix is refused", True)

        # --- style-element prefix ambiguity guard: add/retire/move must
        #     never silently act on whichever row SQLite returns first
        #     when a prefix matches more than one active element ---
        api.define_style_guide(db, manuscript, "Ambiguity guide")
        amb1 = api.add_style_law(db, manuscript, "tone", "Amb element one.",
                                     guide_name="Ambiguity guide")
        amb2 = api.add_style_law(db, manuscript, "tone", "Amb element two.",
                                     guide_name="Ambiguity guide")
        check("style element ids share the common 'se-' literal prefix",
              amb1["id"].startswith("se-") and amb2["id"].startswith("se-"))
        try:
            api.retire_style_law(db, manuscript, "se")
            check("retire refuses an ambiguous prefix", False)
        except LookupError as err:
            check("retire refuses an ambiguous prefix", "ambiguous" in str(err))
        check("neither element was retired by the ambiguous attempt",
              db.one("SELECT status FROM style_laws WHERE id = ?",
                     (amb1["id"],))["status"] == "active"
              and db.one("SELECT status FROM style_laws WHERE id = ?",
                         (amb2["id"],))["status"] == "active")
        try:
            api.move_style_law(db, manuscript, "se", file="01-choice.md")
            check("move refuses an ambiguous prefix", False)
        except LookupError as err:
            check("move refuses an ambiguous prefix", "ambiguous" in str(err))
        try:
            api.add_style_law(db, manuscript, "tone", "New with bad override",
                                  guide_name="Ambiguity guide", overrides="se")
            check("add_style_element refuses an ambiguous override prefix", False)
        except LookupError as err:
            check("add_style_element refuses an ambiguous override prefix",
                  "ambiguous" in str(err))
        retired_amb = api.retire_style_law(db, manuscript, amb1["id"])
        check("a full unique id still resolves and retires",
              retired_amb["id"] == amb1["id"] and retired_amb["status"] == "retired")

        # --- docs.py: list_docs batches its per-file concept lookup + doc
        #     lifecycle error paths (ambiguous query, retired-name collision,
        #     revive collision) ---
        from authorlm import docs as docs_mod

        (ms / "60-alphadoc.md").write_text("# Alpha\n\nAlphaTopic appears here.\n")
        (ms / "61-betadoc.md").write_text("# Beta\n\nBetaTopic appears here.\n")
        api.add_concept(db, manuscript, "AlphaTopic", notes="x")
        api.add_concept(db, manuscript, "BetaTopic", notes="y")
        api.collect(db, manuscript, {})
        listing = docs_mod.list_docs(db, manuscript)
        by_file = {d["file"]: d["concepts"] for d in listing["active"]}
        check("list_docs attributes each concept to its introducing file only",
              by_file.get("60-alphadoc.md") == ["AlphaTopic"]
              and by_file.get("61-betadoc.md") == ["BetaTopic"],
              by_file)

        (ms / "62-zzzprefixtest-one.md").write_text("# One\n\n")
        (ms / "63-zzzprefixtest-two.md").write_text("# Two\n\n")
        try:
            docs_mod.retire_doc(manuscript, "zzzprefixtest")
            check("retire_doc refuses an ambiguous query", False)
        except LookupError as err:
            check("retire_doc refuses an ambiguous query", "ambiguous" in str(err))
        (ms / "62-zzzprefixtest-one.md").unlink()
        (ms / "63-zzzprefixtest-two.md").unlink()

        docs_mod.retire_doc(manuscript, "60-alphadoc.md")
        try:
            docs_mod.add_doc(manuscript, "60-alphadoc")
            check("add_doc refuses a name retired but not revived", False)
        except FileExistsError as err:
            check("add_doc refuses a name retired but not revived",
                  "revive it instead" in str(err))
        (ms / "60-alphadoc.md").write_text("# Reborn\n\n")
        try:
            docs_mod.revive_doc(manuscript, "60-alphadoc")
            check("revive_doc refuses when the manuscript already has that file",
                  False)
        except FileExistsError as err:
            check("revive_doc refuses when the manuscript already has that file",
                  "already exists in the manuscript" in str(err))
        (ms / "60-alphadoc.md").unlink()
        docs_mod.revive_doc(manuscript, "60-alphadoc")
        check("revive_doc restores the file once the collision clears",
              (ms / "60-alphadoc.md").exists())

        # --- MCP payload caps (diff_versions / list_policies) ---
        # Extracted helpers: chat context blew up to 185 KB / 52 KB on
        # mature manuscripts before f3264e8; regressions reintroduce
        # token blowups or silent stubbing mistakes.
        from authorlm.mcp_server import cap_diff_files, compact_belief_rows

        long_file = [f"line-{i}" for i in range(10)]
        capped = cap_diff_files({"a.md": list(long_file)}, max_lines=4)
        check("diff cap truncates a long file and names the escape hatch",
              len(capped["a.md"]) == 5
              and capped["a.md"][:4] == long_file[:4]
              and "(+6 more lines" in capped["a.md"][-1]
              and "file='a.md'" in capped["a.md"][-1],
              str(capped["a.md"][-1]))

        budgeted = cap_diff_files({
            "a.md": ["1", "2"],
            "b.md": ["1", "2"],
            "c.md": ["1", "2"],
            "d.md": ["1", "2"],  # spent reaches budget (max_lines*4 == 8)
            "late.md": [f"l{i}" for i in range(20)],
        }, max_lines=2)
        check("diff cap stubs later files once the whole-answer budget is spent",
              budgeted["d.md"] == ["1", "2"]
              and len(budgeted["late.md"]) == 1
              and "20 changed line(s)" in budgeted["late.md"][0]
              and "file='late.md'" in budgeted["late.md"][0],
              str(budgeted))

        untouched = {"x.md": ["only", "two"]}
        check("diff cap with max_lines<=0 leaves the payload untouched",
              cap_diff_files(dict(untouched), max_lines=0) == untouched)

        policy_rows = [
            {"id": "pol-a", "statement": "Prefer short definitions",
             "status": "candidate", "confidence": 0.4,
             "supporting": 1, "contradicting": 0,
             "questions": ["when?"], "source": "review-explanation"},
            {"id": "pol-b", "statement": "Keep metaphors sparse",
             "status": "validated", "confidence": 0.8,
             "supporting": 5, "contradicting": 0,
             "questions": [], "source": "episode_analysis"},
        ]
        compact = compact_belief_rows(policy_rows)
        check("list_policies compact drops questions/provenance fields",
              compact["belief_count"] == 2
              and set(compact["beliefs"][0]) == {
                  "id", "statement", "status", "confidence",
                  "supporting", "contradicting"}
              and "questions" not in compact["beliefs"][0],
              str(compact))
        filtered = compact_belief_rows(policy_rows, status="validated")
        check("list_policies status= filter keeps only matching rows",
              filtered["belief_count"] == 1
              and filtered["beliefs"][0]["id"] == "pol-b",
              str(filtered))
        verbose = compact_belief_rows(policy_rows, verbose=True)
        check("list_policies verbose=True returns full rows",
              "belief_count" not in verbose
              and verbose["beliefs"][0]["questions"] == ["when?"]
              and verbose["beliefs"][0]["source"] == "review-explanation",
              str(verbose))

        # --- Critique Doc mark requests (pure; feed critique_diff_write) ---
        from authorlm.gdocs import (_mark_insert_requests,
                                    _mark_replace_requests, _utf16_len)

        replace_reqs = _mark_replace_requests("tab-1", 10, 14, "old", "new")
        check("replace mark inserts wrappers then styles strike + green",
              replace_reqs[0]["insertText"]["text"] == ">>{{new}}"
              and replace_reqs[0]["insertText"]["location"]["index"] == 14
              and replace_reqs[1]["insertText"]["text"] == "<<"
              and replace_reqs[1]["insertText"]["location"]["index"] == 10
              and replace_reqs[2]["updateTextStyle"]["textStyle"]
              .get("strikethrough") is True
              and replace_reqs[2]["updateTextStyle"]["range"]["endIndex"]
              == 10 + 4 + _utf16_len("old")
              and "foregroundColor" in replace_reqs[3]["updateTextStyle"]
              ["textStyle"],
              str(replace_reqs))
        # Non-BMP characters occupy two UTF-16 code units in the Docs API.
        emoji_old, emoji_new = "old😀", "new🎉"
        emoji_reqs = _mark_replace_requests("tab-1", 0, _utf16_len(emoji_old),
                                            emoji_old, emoji_new)
        strike_end = emoji_reqs[2]["updateTextStyle"]["range"]["endIndex"]
        green_end = emoji_reqs[3]["updateTextStyle"]["range"]["endIndex"]
        check("replace mark indexes use UTF-16 lengths for non-BMP text",
              strike_end == 4 + _utf16_len(emoji_old)
              and green_end == strike_end + 4 + _utf16_len(emoji_new)
              and _utf16_len(emoji_old) == len(emoji_old) + 1,
              f"strike_end={strike_end} green_end={green_end}")

        insert_reqs = _mark_insert_requests("tab-1", 5, "added")
        check("insert mark writes a green {{new}} paragraph at the boundary",
              insert_reqs[0]["insertText"]["text"] == "\n{{added}}"
              and insert_reqs[0]["insertText"]["location"]["index"] == 5
              and insert_reqs[1]["updateTextStyle"]["range"]["startIndex"]
              == 6
              and "foregroundColor" in insert_reqs[1]["updateTextStyle"]
              ["textStyle"],
              str(insert_reqs))

        # --- Editorial verbs lazy-open a session (CLI helper) ---
        from authorlm import sessions as ses
        from authorlm.cli import _ensure_session

        active = ses.active_session(db, manuscript["id"])
        if active is not None:
            ses.end_session(db, manuscript["id"])
        check("no active session before lazy-open",
              ses.active_session(db, manuscript["id"]) is None)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            _ensure_session(db, manuscript)
        opened = ses.active_session(db, manuscript["id"])
        check("editorial _ensure_session opens a session when none is active",
              opened is not None and "opened session" in buf.getvalue(),
              buf.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as buf2:
            _ensure_session(db, manuscript)
        check("editorial _ensure_session reuses an already-active session",
              ses.active_session(db, manuscript["id"])["id"] == opened["id"]
              and buf2.getvalue() == "",
              buf2.getvalue())

        # --- Doc tab↔TOC sync helpers (pure; no Google) ---
        # These gate push/pull hierarchy safety: wrong classification
        # rewrites toc.toml, adopts scratch tabs as essays, or treats a
        # conflict as a safe pull.
        import hashlib as _hashlib

        from authorlm.gdocs import (
            ILLUS_TAB_TITLE,
            MANIFEST_TITLE,
            _match_prompt,
            classify_structure,
            classify_tabs,
            illus_subtree,
            next_tab_move,
            plan_prompt_sync,
            prompt_links,
            rewrite_toc_from_doc,
            three_way,
            walk_tabs,
        )

        check("next_tab_move is a no-op when order already matches",
              next_tab_move(["a", "b", "c"], ["a", "b", "c"]) is None)
        step = next_tab_move(["b", "a", "c"], ["a", "b", "c"])
        check("next_tab_move proposes one index swap toward desired order",
              step == {"updateDocumentTabProperties": {
                  "tabProperties": {"tabId": "a", "index": 0},
                  "fields": "index"}}, str(step))
        # Ids absent from desired stay at the end and do not drive moves.
        keep_tail = next_tab_move(["x", "a", "b"], ["b", "a"])
        check("next_tab_move ignores extras not in desired",
              keep_tail["updateDocumentTabProperties"]["tabProperties"]
              ["tabId"] == "b", str(keep_tail))

        local_h = _hashlib.sha256(b"local").hexdigest()[:16]
        tab_h = _hashlib.sha256(b"tab").hexdigest()[:16]
        check("three_way: identical sides are unchanged",
              three_way("same", "same", "deadbeef") == "unchanged")
        check("three_way: no base + drift → Doc wins (changed)",
              three_way("tab", "local", None) == "changed")
        check("three_way: only tab moved off base → safe pull",
              three_way("tab", "local", local_h) == "changed")
        check("three_way: only local moved → keep local (local_ahead)",
              three_way("tab", "local", tab_h) == "local_ahead")
        check("three_way: both moved → conflict",
              three_way("tab", "local", "deadbeefcafebabe") == "conflict")

        tabs_cls = [
            ("t1", "01-choice.md"),
            ("t2", "renamed-title.md"),
            ("t3", "brand-new.md"),
            ("t4", "04-road.md"),
            ("t5", MANIFEST_TITLE),
            ("t6", "scratch-notes"),
            ("t7", "dup.md"),
            ("t8", "dup.md"),
            ("t9", "live.md"),
        ]
        links_cls = {
            "01-choice.md": {"tab_id": "t1"},
            "04-road.md": {"tab_id": "t-old"},  # known file, unknown tab id
            "was.md": {"tab_id": "t2"},         # title drifted off filename
            "live.md": {"tab_id": "t-live"},    # live mapped tab still in Doc
        }
        # Inject the live mapped tab so ambiguous duplicate detection fires.
        tabs_cls.append(("t-live", "live.md"))
        classed = classify_tabs(tabs_cls, links_cls, {"04-road.md"})
        check("classify_tabs: rename when mapped id's title drifted",
              classed["renamed"] == [("was.md", "renamed-title.md")],
              str(classed))
        check("classify_tabs: adopt unknown .md with no local/link",
              classed["adopted"] == [("brand-new.md", "t3")], str(classed))
        check("classify_tabs: readopt when title matches existing file",
              classed["readopted"] == [("04-road.md", "t4")], str(classed))
        check("classify_tabs: ignore manifest and non-.md titles",
              MANIFEST_TITLE in classed["ignored"]
              and "scratch-notes" in classed["ignored"], str(classed))
        check("classify_tabs: duplicate unknown titles and live dupes "
              "are ambiguous",
              classed["ambiguous"].count("dup.md") == 2
              and "live.md" in classed["ambiguous"], str(classed))

        pairs_a = [("a.md", None), ("b.md", "a.md")]
        pairs_b = [("b.md", None), ("a.md", None)]
        check("classify_structure: identical pairs are insync",
              classify_structure(pairs_a, pairs_a, None) == "insync")
        check("classify_structure: local matches base → doc_moved",
              classify_structure(pairs_b, pairs_a, pairs_a) == "doc_moved")
        check("classify_structure: doc matches base → local_moved",
              classify_structure(pairs_a, pairs_b, pairs_a) == "local_moved")
        both = classify_structure(
            [("x.md", None)], [("y.md", None)], pairs_a)
        check("classify_structure: both sides off base → conflict",
              both == "conflict", both)
        # No base: pure Doc additions sharing the local spine → doc_moved.
        local_spine = [("a.md", None)]
        doc_with_add = [("a.md", None), ("new.md", None)]
        check("classify_structure: no base + Doc-only additions → doc_moved",
              classify_structure(doc_with_add, local_spine, None)
              == "doc_moved")
        check("classify_structure: no base + disagreeing common spine "
              "→ unsynced",
              classify_structure([("b.md", None)], local_spine, None)
              == "unsynced")

        tab_props: list = []
        doc_pairs: list = []
        walk_tabs([
            {"tabProperties": {"tabId": "r1", "title": "part"},
             "childTabs": [
                 {"tabProperties": {"tabId": "c1", "title": "01.md"},
                  "childTabs": []},
                 {"tabProperties": {"tabId": "c2", "title": "notes"},
                  "childTabs": [
                      {"tabProperties": {"tabId": "c3", "title": "02.md"},
                       "childTabs": []},
                  ]},
             ]},
        ], tab_props, doc_pairs)
        check("walk_tabs lists every tab id/title",
              tab_props == [("r1", "part"), ("c1", "01.md"),
                            ("c2", "notes"), ("c3", "02.md")],
              str(tab_props))
        # Non-md 'part'/'notes' are transparent: both essays sit at root
        # because neither has an md ancestor.
        check("walk_tabs: non-.md tabs are transparent to parent chain",
              doc_pairs == [("01.md", None), ("02.md", None)],
              str(doc_pairs))
        nested_props: list = []
        nested_pairs: list = []
        walk_tabs([{
            "tabProperties": {"tabId": "p", "title": "01.md"},
            "childTabs": [{
                "tabProperties": {"tabId": "s", "title": "scratch"},
                "childTabs": [{
                    "tabProperties": {"tabId": "c", "title": "02.md"},
                    "childTabs": [],
                }],
            }],
        }], nested_props, nested_pairs)
        check("walk_tabs: md parent passes through a scratch nest",
              nested_pairs == [("01.md", None), ("02.md", "01.md")],
              str(nested_pairs))

        toc_ws = root / "toc-rewrite"
        toc_ws.mkdir()
        (toc_ws / "toc.toml").write_text(
            '[[chapter]]\nfile = "a.md"\nmatter = "front"\n\n'
            '[[chapter]]\nfile = "local-only.md"\nmatter = "back"\n\n'
            '[[chapter]]\nfile = "b.md"\n')
        new_tree = rewrite_toc_from_doc(
            {"path": str(toc_ws)},
            [("b.md", None), ("a.md", None)],
            [("a.md", 0), ("local-only.md", 0), ("b.md", 0)])
        rewritten = (toc_ws / "toc.toml").read_text()
        check("rewrite_toc_from_doc follows Doc order and keeps "
              "never-pushed locals after their predecessor",
              [n for n, _ in new_tree]
              == ["b.md", "a.md", "local-only.md"], str(new_tree))
        check("rewrite_toc_from_doc preserves matter attributes",
              'matter = "front"' in rewritten
              and 'matter = "back"' in rewritten, rewritten)

        # Illustration prompt Doc mirror planning.
        check("prompt_links strips the reserved mapping prefix",
              prompt_links({
                  "_illusprompt/slot.md": {"tab_id": "p1"},
                  "01.md": {"tab_id": "e1"},
                  "_illusprompt/bad": "not-a-dict",
              }) == {"slot.md": {"tab_id": "p1"}})
        root_id, children, ids = illus_subtree([
            {"tabProperties": {"tabId": "essay", "title": "01.md"},
             "childTabs": []},
            {"tabProperties": {"tabId": "ill", "title": ILLUS_TAB_TITLE},
             "childTabs": [
                 {"tabProperties": {"tabId": "p1", "title": "slot.md"},
                  "childTabs": [
                      {"tabProperties": {"tabId": "nested",
                                         "title": "extra"},
                       "childTabs": []},
                  ]},
             ]},
        ], {})
        check("illus_subtree finds the reserved root by title",
              root_id == "ill"
              and children == [("p1", "slot.md")]
              and ids == {"ill", "p1", "nested"},
              f"{root_id=} {children=} {ids=}")
        # Remembered id wins over title when both exist.
        root_by_id, _, _ = illus_subtree([
            {"tabProperties": {"tabId": "remembered",
                               "title": "not-the-title"},
             "childTabs": []},
            {"tabProperties": {"tabId": "other", "title": ILLUS_TAB_TITLE},
             "childTabs": []},
        ], {"_illustrations_tab": "remembered"})
        check("illus_subtree prefers the remembered illustrations tab id",
              root_by_id == "remembered", root_by_id)

        text_a = "description one"
        hash_a = _hashlib.sha256(text_a.encode()).hexdigest()[:16]
        plan = plan_prompt_sync(
            {"slot.md": text_a, "new.md": "fresh"},
            {"slot.md": {"tab_id": "p1", "pushed_hash": "stalehash0000000"},
             "gone.md": {"tab_id": "p-gone"}},
            [("p1", "renamed.md"), ("p-hand", "handmade.md"),
             ("p-gone", "gone.md")],
        )
        check("plan_prompt_sync: rewrite on hash mismatch + rename detect",
              plan["rewrite"] == ["slot.md"]
              and plan["renamed"] == [("slot.md", "renamed.md")],
              str(plan))
        check("plan_prompt_sync: create for local-only and vanished tabs",
              plan["create"] == ["new.md"], str(plan))
        check("plan_prompt_sync: prune mapped-but-deleted locals; "
              "unknown leaves hand-made tabs",
              plan["prune"] == [("gone.md", "p-gone")]
              and plan["unknown"] == ["handmade.md"], str(plan))
        # Unchanged hash → no rewrite.
        plan_ok = plan_prompt_sync(
            {"slot.md": text_a},
            {"slot.md": {"tab_id": "p1", "pushed_hash": hash_a}},
            [("p1", "slot.md")])
        check("plan_prompt_sync: matching hash skips rewrite",
              plan_ok == {"create": [], "rewrite": [], "prune": [],
                          "unknown": [], "renamed": []},
              str(plan_ok))

        names = ["alpha-slot.md", "beta-other.md"]
        check("_match_prompt resolves exact, display-prefix, and unique "
              "substring",
              _match_prompt(names, "alpha-slot.md") == "alpha-slot.md"
              and _match_prompt(
                  names, "_illustrations/prompts/beta-other.md")
              == "beta-other.md"
              and _match_prompt(names, "alpha") == "alpha-slot.md"
              and _match_prompt(names, "a") is None)  # ambiguous

        # --- virgin session start: no baseline → skip opening collect ---
        virgin_ws = root / "virgin-ws"
        virgin_ms = virgin_ws / "manuscript"
        virgin_ms.mkdir(parents=True)
        (virgin_ms / "start.md").write_text("# Start\n\nFresh prose.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(virgin_ws), "init",
                      "--name", "virgin", "--path", str(virgin_ms),
                      "--no-extract"])
        virgin_db = api.open_db(str(virgin_ws))
        virgin_m = api.get_manuscript(virgin_db)
        check("init --no-extract leaves a virgin manuscript (no baseline)",
              virgin_db.one(
                  "SELECT id FROM manuscript_versions "
                  "WHERE manuscript_id = ? LIMIT 1",
                  (virgin_m["id"],)) is None)
        (virgin_ms / "start.md").write_text(
            "# Start\n\nFresh prose, already edited.\n")
        out_start = io.StringIO()
        with contextlib.redirect_stdout(out_start):
            cli_main(["--workspace", str(virgin_ws), "session", "start"])
        start_txt = out_start.getvalue()
        check("virgin session start skips opening collect "
              "(no version created)",
              "Collected" not in start_txt
              and "file_added" not in start_txt
              and virgin_db.one(
                  "SELECT id FROM manuscript_versions "
                  "WHERE manuscript_id = ? LIMIT 1",
                  (virgin_m["id"],)) is None,
              start_txt)
        # After a real collect, session start must acknowledge changes.
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(virgin_ws), "session", "end"])
            cli_main(["--workspace", str(virgin_ws), "collect"])
        (virgin_ms / "extra.md").write_text("# Extra\n\nNew chapter.\n")
        out_start2 = io.StringIO()
        with contextlib.redirect_stdout(out_start2):
            cli_main(["--workspace", str(virgin_ws), "session", "start"])
        start2 = out_start2.getvalue()
        check("session start with a baseline collects and reports "
              "new chapters",
              "file_added" in start2 or "extra.md" in start2, start2)
        # --- style_show: effective law composition (guide inheritance,
        #     override displacement, unattached-file fallback) ---
        # Its own manuscript: this block asserts what an UNATTACHED file
        # inherits from THE root guide, which only means anything where
        # this block's guide is the only root. The shared manuscript has
        # collected several root guides from earlier blocks by now.
        style_ws = root / "style-ws"
        style_root = style_ws / "manuscript"
        style_root.mkdir(parents=True)
        (style_root / "97-styled.md").write_text("# Styled\n\nProse.\n")
        (style_root / "98-child.md").write_text("# Child\n\nMore prose.\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli_main(["--workspace", str(style_ws), "init",
                      "--name", "styled", "--path", str(style_root),
                      "--no-extract"])
        sdb = api.open_db(str(style_ws))
        sm = api.get_manuscript(sdb)
        bare = api.style_show(sdb, sm, "97-styled.md")
        # Earlier blocks in this suite define guides of their own, so a
        # fresh file is not lawless by the time this runs. Assert what the
        # check is actually about — the house rule added below is absent
        # beforehand — instead of depending on fixture order.
        check("style_show on a fresh file carries none of the house law",
              not any("second person" in e["statement"]
                      for e in bare["elements"]), str(bare["elements"]))

        api.define_style_guide(sdb, sm, "house")
        api.add_style_law(sdb, sm, "register",
                              "Address the reader in second person.",
                              guide_name="house")
        unattached = api.style_show(sdb, sm, "97-styled.md")
        check("an unattached file inherits the root guide's law",
              "Address the reader in second person."
              in [e["statement"] for e in unattached["elements"]]
              and "STYLE GUIDE for 97-styled.md" in unattached["rendered"]
              and "[register] Address the reader in second person."
              in unattached["rendered"])

        api.define_style_guide(sdb, sm, "dialogue", parent="house")
        api.attach_style(sdb, sm, "98-child.md", "dialogue")
        guide_el = api.add_style_law(sdb, sm, "tone",
                                         "Keep dialogue clipped.",
                                         guide_name="dialogue")
        attached = api.style_show(sdb, sm, "98-child.md")
        check("an attached file inherits its guide's law plus its ancestors'",
              {"Keep dialogue clipped.",
               "Address the reader in second person."}
              <= {e["statement"] for e in attached["elements"]})

        api.add_style_law(sdb, sm, "tone",
                              "Prefer terse fragments.", file="98-child.md",
                              overrides=guide_el["id"])
        overridden = api.style_show(sdb, sm, "98-child.md")
        check("a file-local override displaces the guide element it names",
              {e["statement"] for e in overridden["elements"]}
              == {"Prefer terse fragments.",
                  "Address the reader in second person."})

        # --- MCP tool envelope (T4, risk-register §3): _guard's real
        # contract at the @mcp.tool() seam — (LookupError, ValueError,
        # RuntimeError) become an {"ok": False, "error": ...} dict every
        # other exception type propagates instead of being swallowed. ---
        from authorlm import mcp_server

        # _guard's tracelog write defaults to ~/.authorlm when _WORKSPACE
        # is None (its untouched default here) — pin it to a throwaway
        # workspace first so this test can never write into the author's
        # real trace log.
        guard_ws = root / "mcp-guard-ws"
        guard_ws.mkdir()
        prev_workspace = mcp_server._WORKSPACE
        mcp_server._WORKSPACE = str(guard_ws)
        try:
            for exc_cls, message in (
                (LookupError, "no such manuscript"),
                (ValueError, "bad argument"),
                (RuntimeError, "operation failed"),
            ):
                def boom(exc_cls=exc_cls, message=message):
                    raise exc_cls(message)
                result = mcp_server._guard(boom)
                check(f"_guard maps a bare {exc_cls.__name__} to an "
                      "{ok: False, error} envelope",
                      result == {"ok": False, "error": message}, str(result))

            def crash():
                raise TypeError("not one of the guarded error types")
            try:
                mcp_server._guard(crash)
                propagated = False
            except TypeError as err:
                propagated = str(err) == "not one of the guarded error types"
            check("_guard re-raises exception types outside its guarded "
                  "set instead of swallowing them into an ok:false envelope",
                  propagated)

            ok_result = mcp_server._guard(lambda: {"value": 42})
            check("_guard wraps a successful call as {ok: True, result}",
                  ok_result == {"ok": True, "result": {"value": 42}})
        finally:
            mcp_server._WORKSPACE = prev_workspace

        # --- CLI/MCP parity checklist ---
        from authorlm.mcp_server import mcp
        tool_names = {t.name for t in mcp._tool_manager.list_tools()}
        expected = {
            "list_manuscripts", "get_manuscript_metadata",
            "set_manuscript_metadata", "resolve_file", "get_status",
            "declare_intent",
            "list_intents", "complete_intent", "abandon_intent",
            "collect_revision", "get_guidance", "review_suggestion",
            "get_briefing", "get_concepts", "add_concept", "link_concepts",
            "confirm_concept", "retire_concept", "curate_concepts",
            "confirm_edge", "reject_edge",
            "list_proposals", "reconcile_proposals", "screen_proposals",
            "resolve_proposal",
            "analyze_episodes",
            "diff_versions", "list_beliefs", "close_session",
            "extract_concepts", "get_plan", "get_doc_links",
            "file_improvement", "list_improvements", "improvement_bundle",
            "resolve_improvement", "alias_concept", "merge_concepts",
            "retire_belief", "demote_belief", "merge_beliefs", "convert_belief_to_law",
            "define_style_guide", "add_style_law", "retire_style_law",
            "attach_style", "get_style", "get_profile", "run_sweep",
            "get_illustration_prompt", "scan_illustrations",
            "triage_illustrations",
            "import_critique", "critique_status", "list_critique_items",
            "triage_critique", "list_critique_edits", "triage_critique_edits",
            "move_style_law", "open_triage_app", "triage_app_request",
        }
        check("MCP exposes the full hand-curated tool set",
              expected == tool_names,
              f"missing: {expected - tool_names}; extra: {tool_names - expected}")
        for tool in mcp._tool_manager.list_tools():
            check_desc = bool(tool.description and len(tool.description) > 40)
            assert check_desc, f"tool {tool.name} lacks a real description"
        check("every MCP tool carries a substantive description", True)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
