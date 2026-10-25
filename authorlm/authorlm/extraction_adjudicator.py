"""The per-essay adjudicator (docs/extraction-redesign.md §4.5).

One call per essay, over every candidate its sections produced that
survived the eight gates (extraction.py). The gates settle what a rule
can: the span is in its paragraph, both ends of a relation sit there, no
retired name returns, no mirror doubles a kept edge. What is left is
judgment — would the author keep this? — and that is this call's.

Verdicts stay those of the screen this replaces (`adjudication.py`, which
phase 6 retires): `new | improves | subsumed | drop` for a concept, `new |
subsumed | drop | retype` for a relation, now with a reason per verdict,
kept in the candidate's `extraction_adjudication` evidence row. Only `new`
and `retype` reach the author; `improves` becomes a `note_update` proposal
through `proposals.create`. The adjudicator may not add candidates: a
verdict for an id it was not given is ignored, and a candidate it does not
judge is dropped with that said.

The model is chosen at run time (ruling §0.2, the author's words: "have
the system prompt me for what model I want to use. I don't always have api
credits"): `choose` offers `[extraction] adjudicator_choices` with each
one's estimated cost for the scope, priced through `usage.estimate` and
so through llmlm's price cache; `--adjudicator <model>` answers it. No
model id is written in this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .llm import LLMClient

PROMPT_PATH = Path(__file__).parent / "prompts" / "adjudication.md"
CONCEPT_VERDICTS = ("new", "improves", "subsumed", "drop")
LINK_VERDICTS = ("new", "subsumed", "drop", "retype")
NO_REASON = "(the adjudicator gave no reason)"

# The run-time estimate is made before the first call, so the candidate
# count it prices is an assumption, printed with it: four per section
# reaching the adjudicator, each ~150 tokens in and ~80 out; a section
# reply ~500 tokens out (§6.3).
SECTION_OUTPUT_TOKENS = 500
CANDIDATES_PER_SECTION = 4
CANDIDATE_TOKENS_IN = 150
VERDICT_TOKENS_OUT = 80
CHARS_PER_TOKEN = 4


def adjudicator_system(proposable: list[str] | tuple[str, ...] | None = None
                       ) -> str:
    """`prompts/adjudication.md`, with the relations a retype may name
    (the run's proposable set, or the §4.4 default)."""
    from .extraction import DEFAULT_PROPOSABLE

    relations = sorted(proposable if proposable is not None
                       else DEFAULT_PROPOSABLE)
    return (PROMPT_PATH.read_text(encoding="utf-8")
            .replace("<<PROPOSABLE_RELATIONS>>", str(relations)))


def adjudicator_llm(config: dict, model: str) -> LLMClient:
    """`[llm]`'s transport, the chosen model, purpose
    `extraction-adjudicator` in the usage ledger. Thinking is left at the
    model's default (§7)."""
    client = LLMClient(config)
    client.model = model
    client.purpose = "extraction-adjudicator"
    return client


# ------------------------------------------------------------ the payload

def _quote(text: str | None) -> str:
    return "“" + " ".join((text or "").split()) + "”"


def render_candidates(candidates: list[dict], gists: dict[int, str]
                      ) -> tuple[str, dict[str, dict]]:
    """(the CANDIDATES block, id → candidate). Concepts are c1…, links
    l1…; each shows its span, its paragraph's gist (context, never
    grounds), its score, the extractor's why, and what the gates found:
    already in the graph, or near a live concept (§4.7)."""
    from .extraction import _plain_heading

    ids: dict[str, dict] = {}
    lines: list[str] = []
    for prefix, kind in (("c", "concept"), ("l", "link")):
        for c in (x for x in candidates if x["type"] == kind):
            cid = f"{prefix}{sum(1 for k in ids if k[0] == prefix) + 1}"
            ids[cid] = c
            cited = list(c["_cited"])
            where = (f"§{c['unit']} {_plain_heading(c.get('section')) or ''}"
                     f" [{', '.join(str(n) for n in cited)}]").replace("  ", " ")
            if kind == "concept":
                head = f"[{cid}] CONCEPT {c['name']} ({c['kind']})"
            else:
                head = (f"[{cid}] RELATION {c['from']} —{c['relation']}→ "
                        f"{c['to']}")
            lines.append(f"{head} — score {c['score']:.2f} — {where}")
            if kind == "concept":
                lines.append(f"  notes: {c.get('notes') or '(none given)'}")
                if c.get("existing"):
                    lines.append(f"  in the graph as: {c['existing']}")
                elif c.get("near"):
                    lines.append(f"  near the live concept: {c['near']}")
            lines.append(f"  span: {_quote(c['span'])}")
            gist = next((gists[n] for n in cited if gists.get(n)), None)
            lines.append(f"  gist of the paragraph: {gist or '(none)'}")
            if c.get("why"):
                lines.append(f"  extractor's why: {c['why']}")
            lines.append("")
    return "\n".join(lines).rstrip() or "(no candidates)", ids


def render_payload(payload, candidates: list[dict]) -> tuple[str, dict]:
    """The user turn for one essay: its glossary and precedents (blocks G
    and P of the section calls, unchanged), the essay summary, then the
    candidates. Returns (text, id → candidate)."""
    unit = payload.summary_unit or {}
    essay = unit.get("essay") or {}
    gists = {p["n"]: p.get("gist") for p in unit.get("paragraphs") or []
             if not p.get("stale")}
    if essay:
        summary = (essay.get("summary") or "").strip()
        if essay.get("stale"):
            summary = "(STALE — the essay changed since this was written)\n" \
                      + summary
    else:
        summary = "SUMMARIES: none for essay"
    block, ids = render_candidates(candidates, gists)
    text = "\n".join([
        f"THE ESSAY: {payload.file} — “{payload.title}”", "",
        payload.glossary, payload.precedents,
        "ESSAY SUMMARY (context only)", summary or "(empty)", "",
        f"CANDIDATES ({len(ids)})", block])
    return text, ids


def parse_verdicts(reply, ids: dict[str, dict]) -> dict[str, dict]:
    """id → {verdict, reason, existing, notes, relation, reverse} for the
    ids the adjudicator was given and judged with a verdict its kind
    allows. Anything else in the reply is ignored."""
    out: dict[str, dict] = {}
    items = (reply or {}).get("verdicts") if isinstance(reply, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "").strip()
        if cid not in ids or cid in out:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        allowed = (CONCEPT_VERDICTS if ids[cid]["type"] == "concept"
                   else LINK_VERDICTS)
        if verdict not in allowed:
            continue
        out[cid] = {"verdict": verdict,
                    "reason": " ".join(str(item.get("reason") or "").split())
                    or NO_REASON,
                    "existing": str(item.get("existing") or "").strip(),
                    "notes": str(item.get("notes") or "").strip()[:300],
                    "relation": str(item.get("relation") or "").strip(),
                    "reverse": bool(item.get("reverse"))}
    return out


def adjudicate(llm: LLMClient, payload, candidates: list[dict],
               proposable) -> dict | None:
    """One call for one essay. Returns {candidate index → verdict} keyed
    by `id(candidate)`, or None when the call failed (the candidates are
    then unjudged, and nothing is written for them)."""
    if not candidates:
        return {}
    text, ids = render_payload(payload, candidates)
    reply = llm.complete_json(adjudicator_system(proposable), text)
    if not isinstance(reply, dict):
        return None
    judged = parse_verdicts(reply, ids)
    return {id(c): judged.get(cid) for cid, c in ids.items()}


# --------------------------------------------- the model, chosen at run time

def choices(knob: dict) -> list[str]:
    """`[extraction] adjudicator_choices`, the default model first."""
    out = list(knob.get("adjudicator_choices") or [])
    default = knob.get("adjudicator_model")
    if default and default not in out:
        out.insert(0, default)
    return out


def estimate_scope(essays: list[tuple[str, list]], proposable,
                   candidates: int | None = None) -> dict:
    """Token estimates for a run over `essays` [(file, payloads)]: the
    section calls exactly as they would be sent (chars / 4), and the
    adjudication calls with an assumed candidate count (or `candidates`,
    when the caller knows it, as the replay does)."""
    system = adjudicator_system(proposable)
    sections = sum(len(p) for _f, p in essays)
    ext_in = sum(len(p.system) + len(p.user) for _f, ps in essays for p in ps)
    n = (candidates if candidates is not None
         else CANDIDATES_PER_SECTION * sections)
    adj_in = sum(len(system) + len(ps[0].glossary) + len(ps[0].precedents)
                 + 2000 for _f, ps in essays if ps)
    return {"essays": len(essays), "sections": sections, "candidates": n,
            "assumed": candidates is None,
            "extraction_in": ext_in // CHARS_PER_TOKEN,
            "extraction_out": SECTION_OUTPUT_TOKENS * sections,
            "adjudication_in": adj_in // CHARS_PER_TOKEN
            + CANDIDATE_TOKENS_IN * n,
            "adjudication_out": VERDICT_TOKENS_OUT * n}


def cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Estimated dollars at `model`'s cached rates (`usage.estimate`,
    llmlm's price cache); None when the cache does not price it."""
    from . import usage

    return usage.estimate(model, tokens_in, tokens_out, 0, 0)


def choice_rows(config: dict, knob: dict, est: dict) -> list[dict]:
    """One row per offered model: its estimated adjudication cost for the
    scope, whether this machine has its key, whether llmlm has a profile
    for it (an unprofiled model gets a plain request, no JSON mode)."""
    from llmlm.llm import UNKNOWN_PROFILE, model_profile

    rows = []
    for model in choices(knob):
        probe = adjudicator_llm(config, model)
        rows.append({"model": model,
                     "cost": cost(model, est["adjudication_in"],
                                  est["adjudication_out"]),
                     "key_missing": probe._missing_key_var(),
                     "profiled": model_profile(model) is not UNKNOWN_PROFILE,
                     "default": model == knob.get("adjudicator_model")})
    return rows


def _money(value: float | None) -> str:
    return "price unknown" if value is None else f"≈ ${value:.2f}"


def render_choices(rows: list[dict], est: dict,
                   extraction_model: str | None = None,
                   extraction_cost: float | None = None) -> str:
    if est["sections"]:
        head = (f"Adjudicator for this run — {est['essays']} essay(s), "
                f"{est['sections']} section call(s) on {extraction_model} "
                f"({_money(extraction_cost)}).")
    else:
        head = (f"Adjudicator for this replay — {est['essays']} essay(s), "
                f"{est['candidates']} candidate(s) past the gates.")
    lines = [head + " Estimated adjudication cost"
             + (f", assuming ~{CANDIDATES_PER_SECTION} candidates per section "
                "reach it:" if est["assumed"]
                else f" for {est['candidates']} candidate(s):")]
    for i, row in enumerate(rows, 1):
        notes = []
        if row["default"]:
            notes.append("default")
        if row["key_missing"]:
            notes.append(f"no {row['key_missing']} set")
        if not row["profiled"]:
            notes.append("no llmlm profile: plain request")
        tail = f"  ({'; '.join(notes)})" if notes else ""
        lines.append(f"  {i}. {row['model']:<32} {_money(row['cost'])}{tail}")
    return "\n".join(lines)


def choose(config: dict, knob: dict, est: dict, *,
           answer: str | None = None,
           ask: Callable[[list[dict]], str | None] | None = None
           ) -> str | None:
    """The adjudicator model for this run, decided before the first call.

    `answer` (`--adjudicator <model>`) decides it outright — any model id,
    offered or not. `[extraction] adjudicator_prompt = "default"` takes
    `adjudicator_model` unasked. Otherwise `ask` is shown the rows and
    returns a model, or None to stop the run before anything is spent;
    with no `ask` (no terminal) the run cannot ask, so it refuses."""
    if answer:
        return answer
    rows = choice_rows(config, knob, est)
    if knob.get("adjudicator_prompt") == "default":
        if knob.get("adjudicator_model"):
            return knob["adjudicator_model"]
        raise LookupError("[extraction] adjudicator_prompt = \"default\" needs "
                          "[extraction] adjudicator_model")
    if not rows:
        raise LookupError("no adjudicator to offer — set [extraction] "
                          "adjudicator_choices (or pass --adjudicator <model>)")
    if ask is None:
        raise LookupError("this run would adjudicate, and the model is chosen "
                          "per run (ruling §0.2): pass --adjudicator <model> — "
                          "one of " + ", ".join(r["model"] for r in rows))
    return ask(rows)
