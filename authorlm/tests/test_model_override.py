"""`summarize rebuild --model --effort` reaches the API layer.

The override is set on authorlm's clients (`summaries.clients`), but what
matters is what litellm is asked to send. So the spy wraps
`litellm.completion` itself, the last call before HTTP, and still runs the
real function with litellm's own `mock_response`: the request is built and
validated exactly as a live one would be, and nothing is billed.

    python3 tests/test_model_override.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import litellm  # noqa: E402

from authorlm import summaries, usage  # noqa: E402

CONFIG = {"llm": {"enabled": True, "provider": "litellm"},
          "critique": {"summarizer_model": "openai/gpt-5.6-luna"}}


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _spy_calls(run) -> list[dict]:
    """Every kwargs dict litellm.completion received while `run()` ran."""
    seen: list[dict] = []
    real_completion, real_cost = litellm.completion, litellm.completion_cost

    def spy(**kwargs):
        seen.append(dict(kwargs))
        return real_completion(**kwargs, mock_response="ok")

    litellm.completion = spy
    litellm.completion_cost = lambda **_kw: 0.0
    usage._LEDGER = usage._OFF
    try:
        run()
    finally:
        litellm.completion, litellm.completion_cost = real_completion, real_cost
        usage.reset()
    return seen


def test_model_and_effort_reach_litellm() -> None:
    llms = summaries.clients(CONFIG, model="anthropic/claude-opus-5",
                             effort="high")
    calls = _spy_calls(lambda: [llm.complete("system", f"user {level}")
                                for level, llm in llms.items()])
    check("one request per level", len(calls) == 3, len(calls))
    check("every request names the override model",
          all(c["model"].endswith("claude-opus-5") for c in calls),
          [c["model"] for c in calls])
    check("every request carries the override effort",
          all(c.get("reasoning_effort") == "high" for c in calls),
          [c.get("reasoning_effort") for c in calls])


def test_no_override_keeps_configured_model() -> None:
    llms = summaries.clients(CONFIG)
    calls = _spy_calls(lambda: llms["section"].complete("system", "user"))
    check("the configured section model is sent",
          len(calls) == 1 and calls[0]["model"].endswith("gpt-5.6-luna"),
          [c["model"] for c in calls])
    check("no effort is sent without an override",
          "reasoning_effort" not in calls[0], calls[0].get("reasoning_effort"))


def test_effort_alone_keeps_configured_model() -> None:
    llms = summaries.clients(CONFIG, effort="medium")
    calls = _spy_calls(lambda: llms["essay"].complete("system", "user"))
    check("the configured essay model is sent",
          len(calls) == 1 and calls[0]["model"].endswith("gpt-5.6-luna"),
          [c["model"] for c in calls])
    check("with the override effort",
          calls[0].get("reasoning_effort") == "medium",
          calls[0].get("reasoning_effort"))


def main() -> None:
    tests = [test_model_and_effort_reach_litellm,
             test_no_override_keeps_configured_model,
             test_effort_alone_keeps_configured_model]
    for fn in tests:
        print(fn.__name__)
        fn()
    print(f"OK — {len(tests)} model-override cases")


if __name__ == "__main__":
    main()
