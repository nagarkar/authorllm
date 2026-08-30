"""Registry of every LLM prompt AuthorLM sends, and where each lives.

Prompts are reviewable artifacts (critique-pass design §4/§5): the author
must be able to find, read, and change the words that shape what the
model does. `authorlm prompts` lists this registry; `prompts show <name>`
prints one. Two kinds:

- file prompts: `authorlm/prompts/<name>.md` — edit the file, no code
  change; the next run picks it up.
- analyzer prompts: the `prompt` field in a versioned
  `authorlm/triage_profiles/*.json` artifact.
- inline prompts: string constants inside a module — edit that module.
  These are the pre-registry legacy; each migrates to a file when its
  verb is next touched.

Every verb that consults the LLM says so in its --help and points here.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str            # registry key
    verbs: str           # which CLI verbs / MCP tools send it
    purpose: str         # one line
    file: str | None = None     # prompts/<file> when file-backed
    module: str | None = None   # authorlm/<module>.py:<symbol> when rendered in code
    json_file: str | None = None
    json_field: str | None = None

    @property
    def location(self) -> str:
        if self.file:
            return f"authorlm/prompts/{self.file}"
        if self.json_file:
            return f"authorlm/{self.json_file}:{self.json_field}"
        return f"authorlm/{self.module}"

    @property
    def artifact_kind(self) -> str:
        return "file" if self.file or self.json_file else "inline"

    def text(self) -> str:
        if self.file and self.module:
            mod_name, _, symbol = self.module.partition(":")
            module = importlib.import_module(f"authorlm.{mod_name[:-3]}")
            value = getattr(module, symbol)
            return value() if callable(value) else value
        if self.file:
            return (PROMPTS_DIR / self.file).read_text(encoding="utf-8")
        if self.json_file:
            assert self.json_field is not None
            value = json.loads((Path(__file__).parent / self.json_file).read_text())
            return value[self.json_field]
        mod_name, _, const = self.module.partition(":")
        mod = importlib.import_module(f"authorlm.{mod_name[:-3]}")
        value = getattr(mod, const)
        return value() if callable(value) else value


REGISTRY: list[Prompt] = [
    Prompt("summarizer", "summarize rebuild; critique resolve",
           "essay summaries — the editor's working memory of each unit",
           file="summarizer.md", module=None),
    Prompt("beat-draft", "write draft",
           "one beat of the author's prose from the layered drafting payload "
           "(style law, book context, plan, graph, accepted text, beat spec)",
           file="beat-draft.md", module=None),
    Prompt("editor", "critique run",
           "the critique pass's per-essay edit proposals (paragraph-aligned "
           "JSON: keep/replace/insert + suggestions)",
           file="editor.md", module=None),
    Prompt("triage-context", "triage-app Analyze",
           "build one reusable ontology and consistency map from the pinned manuscript",
           module="triage_analysis.py:ONTOLOGY_SYSTEM"),
    Prompt("triage-contract", "triage-app Analyze",
           "constrain batch scoring and evidence citations to structured output",
           module="triage_analysis.py:ANALYSIS_SYSTEM"),
    Prompt("triage-concept-keepability", "triage-app Concepts",
           "score concept keepability against manuscript evidence",
           json_file="triage_profiles/concept-keepability.v1.json",
           json_field="prompt"),
    Prompt("triage-edge-keepability", "triage-app Edges",
           "score directed edge keepability against manuscript evidence",
           json_file="triage_profiles/edge-keepability.v1.json",
           json_field="prompt"),
    Prompt("extraction", "extract; init; collect (auto)",
           "mine concepts, relations, and aliases from manuscript text",
           file="extraction.md", module="extraction.py:extraction_system"),
    Prompt("adjudication", "extract; collect (auto) — only when "
           "[extraction] adjudicate = true",
           "step two: rule each extraction candidate new | improves | "
           "subsumed | drop before it can reach the author's triage queue",
           file="adjudication.md", module="adjudication.py:adjudication_system"),
    Prompt("extraction-edges", "extract --edges",
           "relations only, among known concepts",
           file=None, module="extraction.py:EDGES_ONLY_SYSTEM"),
    Prompt("extraction-aliases", "extract --aliases",
           "aliasing statements only",
           file=None, module="extraction.py:ALIASES_ONLY_SYSTEM"),
    Prompt("episode-analysis", "intent complete; analyze; complete_intent",
           "reconstruct the editorial decisions an episode's edits show",
           file=None, module="analysis.py:ANALYSIS_SYSTEM"),
    Prompt("belief-distill", "review (explained verdicts); review_suggestion; "
           "proposal dismissal",
           "match an author's explanation to an existing belief, distil a new "
           "one, or decline",
           file="belief-distill.md"),
    Prompt("triage-screen", "proposal screen; scan_illustrations",
           "cut queued proposals that violate the author's active law",
           file="triage-screen.md"),
    Prompt("margin-distill", "doc pull (comment verdicts); illus triage",
           "distill a batch of margin/placement explanations into a "
           "scoped candidate",
           file="margin-distill.md"),
    Prompt("guidance-draft", "guide; get_guidance",
           "draft a bridge paragraph for a guidance suggestion",
           file=None, module="guidance.py:BRIDGE_DRAFT_SYSTEM"),
    Prompt("lens", "analyze --lens; analyze_episodes",
           "run a reflective-analysis lens over the record (this preamble "
           "+ the author's _lenses/<name>.md file)",
           file=None, module="lenses.py:LENS_SYSTEM"),
    Prompt("ontology-sweep", "sweep ontology; run_sweep",
           "graph hygiene: duplicates, mis-typed nodes, missing edges",
           file=None, module="sweeps.py:ONTOLOGY_SYSTEM"),
    Prompt("illus-placement", "illus scan; scan_illustrations",
           "spot-find illustration placements against ratified criteria",
           file="illus-placement.md", module="placement.py:placement_system"),
    Prompt("illus-arbiter", "illus scan (revision judgement)",
           "judge whether an existing tag should be revised",
           file="illus-arbiter.md", module="placement.py:arbiter_system"),
]


def by_name(name: str) -> Prompt:
    for p in REGISTRY:
        if p.name == name:
            return p
    raise LookupError(f"no prompt named '{name}' — 'authorlm prompts' lists them")


HELP_NOTE = ("Uses an LLM prompt — 'authorlm prompts' lists every prompt "
             "and where to edit it.")
