# The sweep framework: auditors, lenses, and where the tokens go

Ratified in conversation 2026-08-05 (EditorLLM research → chat-first
filter → taxonomy); vanguards built 2026-08-05: `sweep readiness` (pure),
`sweep ontology` (narrowing), `lens` (prompt-first + registration door).
The design backlog carries the still-unbuilt instances (tether, style
drift detector).

## Why a framework

Every sweep has the same spine: (1) assemble context deterministically
from the stores; (2) optionally ask a model one tight question; (3)
hygiene-gate the answer (verbatim-quote grounding — an ungrounded finding
the author rejects poisons the policy evidence); (4) route findings by
jurisdiction — prose advice → guidance suggestions (review loop),
conflicts with settled knowledge → proposals; (5) chat is the triage
surface. Auditors and lenses differ only in steps 1–2.

The chat-first premise shapes everything: AuthorLM's verdict surface is
conversation, so app affordances (anchored margin suggestions, job
consoles, sidebar badges, agent frameworks) are not built. What IS built
is deterministic verbs that a conversational agent can call cheaply and
whose findings flow into the one evidence loop.

## The three classes

### 1. Pure auditors — zero tokens

Deterministic checks over the DB and the text. No model at all.

- Instances: `sweep hygiene` (ungrounded concepts/edges, moot
  suggestions), `sweep readiness` (pre-publication checklist), the
  prerequisite-gap engine (predates the framework), staleness marking at
  collect, mention grounding, dangling-image stripping.
- **Token bill: zero. Time bill: milliseconds** (string scans over the
  manuscript + SQL). This is why some run *inline*: a pure check rides
  the event it belongs to (staleness rides collect; the dangling-ref
  strip rides pull) when it is O(text) cheap AND tied to that event's
  semantics. Checks with report-shaped output (readiness, the hygiene
  pile report) run on demand instead — inlining them would nag.
- Inline rule of thumb: inline when the event *invalidates* something
  (collect invalidates pending suggestions) — on demand when the author
  is *asking a question* (am I ready to publish?).

### 2. Narrowing auditors — deterministic-first, cheap-model core

The reference standard lives in AuthorLM's stores (ratified concept
notes, typed edges, style elements). The pipeline extracts every input
deterministically and asks the model ONLY the question that cannot be
answered by string matching: "does this passage contradict this claim?"

- Instances: `sweep ontology` (built), style drift detector (backlog).
- Narrowing, concretely (ontology): changed files (collect knows) →
  paragraphs ≥ 80 chars → concepts each paragraph mentions (word-boundary
  scan incl. aliases) → those concepts' notes + refutes/depends_on/
  contrasts_with edges. Only paragraphs that mention concept claims are
  sent, each with only its own claims. The model never sees "here's the
  file, find problems."
- **Token bill: scales with CHANGED TEXT × claim density**, not with
  manuscript size. A typical post-writing-session sweep is a few
  thousand input tokens on the flash tier — order 10⁻³ dollars. This is
  what makes the collect hook affordable as a future trigger.
- Output is schema-tight and hygiene-gated: quotes must be verbatim in
  the paragraph, named concepts must exist; everything else is dropped
  and counted (the drop count is itself a prompt-quality signal).
- Model: the configured `[llm] model` (Gemini Flash tier). Record/replay
  caching works because narrowed inputs are stable.

### 3. Lenses — prompt-first

The reference standard lives ONLY in the lens's ratified prompt
(`_lenses/<name>.md` — observation-invisible, author-editable, portable
as a file). Findings are guidance rows (kind `lens`, own batch) reviewed
via `lens review <n>` → `record_review` → evidence and policy learning,
identical to guide suggestions.

Two execution paths, one store:

- **Native** (`lens run <name> <file>`): AuthorLM assembles compact
  context (file text + ratified concept notes for mentioned concepts)
  and runs the configured cheap model. Right when the lens is a
  *pattern check the prompt fully specifies* — "flag sentences that
  assert without argument", "flag Sanskrit terms not in the glossary
  register". Token bill: one file + prompt + notes per run.
- **External** (`lens register <name> <file>`, findings JSON on stdin):
  the REGISTRATION DOOR. A Claude subagent (or the chat agent itself)
  does the thinking — world knowledge, multi-step judgment, web
  search — and submits findings through the door, where they pass the
  SAME hygiene gate into the SAME store. One evidence stream, no forks.

## Cheap model vs. Claude — the decision matrix

The question is not "which model is smarter" but *where the sweep's
quality comes from*:

| Signal | Run natively (flash tier) | Run as Claude (register) |
| :--- | :--- | :--- |
| Reference standard | in AuthorLM's stores | in the world / in judgment |
| Quality comes from | the assembled context | the model's knowledge & reasoning |
| Question shape | tight schema, per-pair verdicts | open-ended, needs synthesis |
| Frequency | often (per collect / per session) | occasional, deliberate passes |
| Needs tools (web, files across repos) | no | yes |
| Examples | ontology, style drift, glossary/terminology lenses | tether (scholarly record), "Kantian objections", exploratory concerns still being articulated |

Cost intuition: a narrowing auditor's bill scales with changed text; a
Claude pass's bill scales with judgment required (reading, searching,
reasoning). Running tether on flash would be *cheaper per token but
worthless* — its value is exactly the world knowledge and the
Error/Controversial/Alignment judgment call. Running the ontology sweep
on Claude would be *better per finding but pointless* — the narrowed
(passage, claim) comparison doesn't need it, and it runs too often to
pay for it. When a lens's prompt fully specifies the check → native;
when the prompt names a concern whose evaluation needs a mind → Claude
through the door.

Hybrid (the compose pattern): a native sweep produces candidates
cheaply; a Claude pass adversarially verifies or deepens only the
findings that survive the author's first glance. Spend the expensive
model where the cheap one found something.

## When to ask for a sweep (triggers)

Nothing fires without the author today; the natural prompts, in
escalating automation order:

1. **On demand, in chat** — "audit connections.md", "run the clarity
   lens on the sermons". The chat agent runs the verb; findings come
   back for verdicts. This is the current state.
2. **Cadence hooks the chat agent honors** (no code): after a pull that
   changed prose, offer the ontology sweep on the changed files; before
   an export, run readiness automatically (it is free); when a writing
   session closes with substantial new text, offer the relevant lenses.
3. **Collect-hook automation** (future, per backlog): ontology and
   style-drift as collect analyzers, like extraction — affordable
   because the token bill scales with changed text. Gate: build only
   after the on-demand versions prove their findings earn accepts (a
   sweep whose findings the author mostly dismisses must not be
   auto-run; the drop/dismiss rates in the evidence table are the
   go/no-go signal).

Readiness runs free, so: any time. Hygiene: after big extraction runs or
Doc reorganizations, and before triage sessions (it shrinks the pile).

## Traces

Every verb logs `{ts, surface, verb, action, manuscript, duration_ms,
ok, error}` to `~/.authorlm/logs/trace.jsonl` (see README "Logging &
traces"). Sweeps are exactly the operations whose cost profile matters —
the scheduled log reviewer watches for LLM-bound verbs slowing down,
error clusters, and sweeps whose duration/token cost stops matching this
document's claims.
