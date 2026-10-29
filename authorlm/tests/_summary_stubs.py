"""Canned replies for the three summary-hierarchy prompts, shared by the
hermetic suites that stand in for the provider over HTTP (test_e2e's
StubLLMHandler, test_summaries' EchoSummarizer). Each stub reads the user
message the real builder sends and answers in the shape the builder
parses, so a `summarize rebuild --all` runs all three levels offline.

`reply(system, user)` dispatches on the prompt's opening line and returns
None for a prompt that is not a summary prompt.
"""

from __future__ import annotations

import json
import re

PARAGRAPH_KEY = "You are indexing one section of a book-length"
SECTION_KEY = "Section summarizer"
ESSAY_KEY = "editor's working memory"


def paragraph_records(user: str) -> str:
    """One record per RECORD THESE number: a gist naming the paragraph,
    one `uses` mention whose span is the paragraph's first three words
    (verbatim, so the byte check keeps it)."""
    wanted = [int(n) for n in re.search(
        r"RECORD THESE: ([\d, ]+)", user).group(1).split(", ")]
    body = user.split("THE SECTION:\n\n", 1)[1]
    texts = {int(n): t for n, t in re.findall(
        r"^\[(\d+)\] (.*?)(?=\n\n\[\d+\] |\Z)", body, re.S | re.M)}
    out = []
    for n in wanted:
        span = " ".join(texts.get(n, "").split()[:3])
        out.append({"n": n, "gist": f"stub gist of paragraph {n}",
                    "claim": None, "move": "states",
                    "mentions": [{"term": "Choice", "span": span,
                                  "role": "uses"}] if span else [],
                    "definitions": [], "relations": [], "references": [],
                    "depends_on": []})
    return json.dumps(out)


def section_summary(user: str) -> str:
    """The four labels; POINT quotes the first body paragraph (so a test
    can see which TEXT the section was built from through the essay
    summary); MOVES places every COVER THESE number once."""
    heading = re.search(r"^SECTION: (.*)$", user, re.M).group(1)
    wanted = re.search(r"COVER THESE: ([\d, ]+)", user).group(1).split(", ")
    body = user.split("THE SECTION:\n\n", 1)[1]
    first = next((t for n, t in re.findall(
        r"^\[(\d+)\] (.*?)(?=\n\n\[\d+\] |\Z)", body, re.S | re.M)
        if n == wanted[0]), "")
    moves = "\n".join(f"[{n}] the stub's move for paragraph {n}" for n in wanted)
    return (f"POINT: The stub's point of {heading}: {first.strip()}\n"
            f"MOVES:\n{moves}\nDEPENDS ON: none\nLEAVES OPEN: none")


def essay_summary(user: str) -> str:
    """Cites section [1] only and writes no FLOW, so the structural
    coverage check has something real to be loud about."""
    unit = user.split("THE UNIT: ", 1)[1].split("\n", 1)[0]
    return (f"MOVES: [1] the stub's canned summary of {unit}.\n"
            "MOTIFS: none\nDOES NOT: none")


def reply(system: str, user: str) -> str | None:
    if system.startswith(PARAGRAPH_KEY):
        return paragraph_records(user)
    if SECTION_KEY in system.split("\n", 1)[0]:
        return section_summary(user)
    if ESSAY_KEY in system.split("\n", 1)[0]:
        return essay_summary(user)
    return None
