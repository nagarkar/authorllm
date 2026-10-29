"""The critique editor's output contract holds against wrong SHAPES, not
only wrong values: a list element that is not a JSON object, or an
`insertions`/`suggestions` field that is not a list, is a ContractError
(so run_editor retries with the violation named), never an
AttributeError that aborts the run.

Run: python3 tests/test_passes_contract_shapes.py
"""

from __future__ import annotations

import os

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import passes  # noqa: E402

PASSED = 0
SOURCE = ["The only paragraph of a tiny essay about nothing much."]


def check(label: str, condition: bool) -> None:
    global PASSED
    if not condition:
        raise AssertionError(f"FAILED: {label}")
    PASSED += 1
    print(f"  ok: {label}")


def keep_entry() -> dict:
    return {"n": 1, "echo": passes.echo_of(SOURCE[0]), "action": "keep"}


def raises_contract(raw) -> bool:
    try:
        passes.validate_output(raw, SOURCE)
    except passes.ContractError:
        return True
    return False


class StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete_json(self, system, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def ctx() -> dict:
    return {"style": "", "intents": [], "beliefs": [],
            "concepts": {"nodes": []}, "learnings": [], "before": [],
            "after": [], "file": "tiny.md", "paragraphs": SOURCE}


def main_test() -> None:
    print("wrong shapes are contract violations:")
    check("a paragraphs entry that is a bare string",
          raises_contract({"paragraphs": ["keep"]}))
    check("a paragraphs entry that is null",
          raises_contract({"paragraphs": [None]}))
    check("an insertions element that is a string",
          raises_contract({"paragraphs": [keep_entry()],
                           "insertions": ["x"]}))
    check("insertions that is a string, not a list",
          raises_contract({"paragraphs": [keep_entry()],
                           "insertions": "oops"}))
    check("a suggestions element that is a number",
          raises_contract({"paragraphs": [keep_entry()],
                           "suggestions": [42]}))
    check("suggestions that is a string, not a list",
          raises_contract({"paragraphs": [keep_entry()],
                           "suggestions": "oops"}))

    print("valid output still parses:")
    ok = passes.validate_output(
        {"paragraphs": [keep_entry()],
         "insertions": [{"after": 1, "new": "More."}],
         "suggestions": [{"kind": "other", "text": "Say more."}]}, SOURCE)
    check("keep + one insertion + one suggestion",
          ok["edits"] == [] and len(ok["insertions"]) == 1
          and len(ok["suggestions"]) == 1)

    print("run_editor retries on a wrong shape:")
    llm = StubLLM([{"paragraphs": ["bad"]}, {"paragraphs": [keep_entry()]}])
    result = passes.run_editor(llm, ctx())
    check("second reply accepted after the first is rejected",
          result == {"edits": [], "insertions": [], "suggestions": []}
          and len(llm.prompts) == 2
          and "YOUR PREVIOUS RESPONSE WAS REJECTED" in llm.prompts[1])
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
