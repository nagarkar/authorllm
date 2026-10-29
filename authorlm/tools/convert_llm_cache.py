#!/usr/bin/env python3
"""One-off: convert authorlm's record/replay recordings to llmlm's cache format.

Old: `<sha256[:20]>.json` = {model, temperature, thinking_budget, max_tokens, effort, messages,
response, finish_reason, usage{input_tokens, output_tokens, cache_read_tokens, cache_write_tokens},
recorded_at}. New: `<messages_sha256>.json` = {model, temperature, messages, reply, tokens_in,
tokens_out, cache_read, cache_write, finish_reason, cost_usd, requested_at}, the file
`LLMClient` reads and writes since it went through llmlm (nagarkar/llmlm#1, step 7).

The new key is computed as the call computes it: the model's profile may drop the temperature and
the reasoning effort, and a drafting recording (no `temperature` sent) keys on none.

    python3 tools/convert_llm_cache.py tests/llm_cache [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from llmlm import llm as shared


def convert(entry: dict) -> tuple[str, dict]:
    model = entry["model"]
    profile = shared.model_profile(model)
    temperature = entry.get("temperature")
    drafting = "max_tokens" in entry or "effort" in entry
    if drafting or profile.temperature == shared.VENDOR_DEFAULT_ONLY:
        temperature = None
    effort = entry.get("effort") if profile.reasoning_effort else None
    key = shared.messages_sha256(
        model,
        temperature,
        entry["messages"],
        max_tokens=entry.get("max_tokens"),
        reasoning_effort=effort,
        thinking_budget=entry.get("thinking_budget"),
    )
    usage = entry.get("usage", {})
    return key, {
        "model": model,
        "temperature": temperature,
        "messages": shared.without_cache_control(entry["messages"]),
        "reply": entry["response"],
        "tokens_in": usage.get("input_tokens", 0),
        "tokens_out": usage.get("output_tokens", 0),
        "cache_read": usage.get("cache_read_tokens", 0),
        "cache_write": usage.get("cache_write_tokens", 0),
        "finish_reason": entry.get("finish_reason", "stop"),
        "cost_usd": None,
        "requested_at": entry.get("recorded_at", ""),
    }


def main(argv: list[str]) -> int:
    directory = Path(argv[1])
    dry = "--dry-run" in argv
    converted = 0
    for path in sorted(directory.glob("*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        if "response" not in entry:
            continue  # already new
        key, new = convert(entry)
        print(f"{path.name} -> {key}.json")
        if not dry:
            (directory / f"{key}.json").write_text(
                json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
            path.unlink()
        converted += 1
    print(f"{converted} converted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
