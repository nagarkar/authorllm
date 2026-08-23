# AuthorLM (MVP)

An editorial collaborator for philosophy writing that **learns your editorial
practice from evidence** — an implementation of the AuthorLM Specialization
RFC (v2.1) on the Common Core Decision Learning Engine architecture.

AuthorLM does not write for you. It observes your revisions, tracks your
ideas in a Concept Graph, proposes suggestions that explain themselves, and
learns editorial policies from how you review those suggestions. When it has
no evidence, it abstains.

## Requirements

- **Python 3.11+**. The core is pure standard library (SQLite, difflib)
  and works fully without an LLM or network.
- **LLM features** (concept extraction, policy distillation, bridge
  drafting): `pip install litellm` plus an API key for your chosen model
  (see Configuration).
- **MCP server** (Claude Code / MCP-client integration): `pip install mcp`.
- **Google Docs bridge** (`doc push/pull`, reconciliation):
  `pip install google-api-python-client google-auth-httplib2
  google-auth-oauthlib`, plus an OAuth client secret in
  `~/.authorlm/config.toml` under `[gdocs]` (one-time `authorlm doc auth`
  opens the consent browser).
- **Publishing exports** (docx/epub/pdf with embedded illustrations):
  `pandoc` — `brew install pandoc` on macOS. PDF additionally needs a
  TeX distribution with `xelatex` (MacTeX / TeX Live); the 8-bit
  `pdflatex` default cannot set the manuscript's unicode.
  `--chapters ascending,indic` builds just those chapters and everything
  filed under them in the TOC, into their own files alongside the
  whole-book export.

## Install / build

```bash
cd authorlm
pip install -e .              # gives you the `authorlm` command
pip install -e ".[llm,mcp]"   # …with the LLM and MCP extras in one step
pip install -e ".[gdocs]"     # Google Docs bridge and session-start reconcile
pip install -e ".[all]"       # everything above
```

There is no build step for normal installation; the compiled Triage App is
included in the package. Frontend contributors rebuild it with `npm install &&
npm run build` from `web/triage-app/`. The MCP server is a long-running
process; after changing its code or rebuilding the app, restart it with
`pkill -f authorlm-mcp` (the MCP client respawns it on the next call).

AuthorLM keeps **one global database in `~/.authorlm/`** (the database and
`config.toml`), so `authorlm` works from any directory and manuscripts can
live anywhere — register them with relative or absolute paths (they're
stored absolute). `-w/--workspace <dir>` overrides the data location, which
is mainly how the hermetic tests isolate themselves. `python3 main.py …`
still works identically.

For bash tab completion (commands, actions, flags, `--kind` values, and
manuscript names after `-m`), add to `~/.bash_profile`:

```bash
eval "$(authorlm completion)"
```

The script is generated from the argument parser at eval time, so it stays
in sync with the CLI automatically. `authorlm help` (or `help` inside the
shell) lists all commands.

## Quick start — the shell workflow (recommended)

Register your manuscript once, then work in the interactive shell with your
editor (e.g. Obsidian, opened on the manuscript folder) alongside:

```bash
authorlm init --name my-book --path manuscripts/my-book
authorlm shell
```

The shell **is** the authoring session: it opens with the learning
briefing, resumes an active session if one exists, and a background watcher
collects a revision automatically a few seconds after your saved edits
settle — you never run `collect` by hand. Inside the shell, type commands
without any prefix:

```
authorlm> intent declare "Introduce gravity as the curvature of fields"
authorlm> guide
[1] (bridge) Introduce 'Gravity'…  Draft to consider: …
   … now write in your editor; the shell logs collected revisions …
authorlm> review 1 modify --explain "Used the tide metaphor instead"
authorlm> intent complete gravity --outcome "Gravity introduced via tides"
authorlm> session end
authorlm> exit
```

(`review 1 accept` works as shorthand for `review 1 --accept`. Prefer
manual control? `authorlm shell --no-watch`, or skip the shell entirely and
run `authorlm watch` in a second terminal.)

## Quick start — step by step

```bash
authorlm init --name my-book --path manuscripts/on-becoming

authorlm session start                     # learning briefing
authorlm intent declare "Introduce trajectories"

authorlm concept add Trajectory            # sketch ideas in the Concept Graph
authorlm concept link Field permits Trajectory

authorlm collect                           # immutable, checksummed observation
authorlm guide                             # explained suggestions, or abstention

authorlm review 1 --accept
authorlm review 2 --reject --explain "Too formal too early"

# … write, then:
authorlm collect
authorlm intent complete <id> --outcome "Trajectories introduced"
authorlm session end                       # prints your learning velocity
```

## Obsidian integration

Open the manuscript folder as an Obsidian vault and run:

```bash
authorlm export-obsidian
```

Each concept becomes a stub note in `_concepts/` with its relationships as
wikilinks, so **Obsidian's built-in graph view renders your Concept Graph**.
Re-export any time (stale stubs are replaced; your own notes are never
touched). Folders starting with `_` or `.` are invisible to observation, so
the stubs — and Obsidian's `.obsidian/`/`.trash/` internals — never leak
into manuscript revisions.

With several manuscripts registered, add `-m <name>` (or `--manuscript`)
to any command, before or after the subcommand:
`authorlm session start -m my-book`. Similarly `-w` is short for
`--workspace`.

Next time you run `session start`, the briefing reports what AuthorLM
learned: policies strengthened or weakened, newly realized concepts,
inferred relationships awaiting your confirmation, contradictions, and
outstanding questions.

## Commands

| Command | Purpose |
| :--- | :--- |
| `shell` | Interactive authoring session: briefing, prefix-free commands, auto-collect watcher (`--no-watch`, `--debounce N`) |
| `watch` | Standalone auto-collect on manuscript changes (Ctrl-C to stop) |
| `export-obsidian` | Export the Concept Graph as wikilinked stub notes for Obsidian's graph view (`--dir` to override `_concepts/`) |
| `init --name N --path DIR` | Register a manuscript directory (`.md`/`.txt`); with an LLM enabled, auto-extracts concepts (`--no-extract` to skip) |
| `unregister <name>` | **Delete** a manuscript and all its data — clean slate for prototyping and hermetic tests (files on disk untouched) |
| `extract [file…] [--full] [--edges-only]` | LLM-extract concepts and relationships — incremental by default (only files changed since last extraction); `--edges-only` re-mines relationships among existing concepts without touching the concept inventory |
| `session start` / `session end` | Authoring session; opens with the learning briefing, closes with learning velocity |
| `briefing` | Re-print the learning briefing any time |
| `intent declare/complete/list` | Declared intents — authoritative observations that drive guidance |
| `concept add/link/list` | Grow the Concept Graph (`--kind`: concept, objection, example, metaphor, question, historical_reference, mathematical_construct, syllogism). A concept's ratified definition belongs in its notes |
| `concept triage` | Rapid-fire review of unconfirmed extracted concepts (`k`eep, `r`etire, `n` reword, `s`kip, or a kind key to retype), then inferred relationships (`k`onfirm, `r`eject, `f`lip direction, or a relation number/name to retype). `--nodes` / `--edges` restrict to one lane |
| `concept triage --deterministic [--apply]` | Zero-token bulk triage. Reports mechanically safe alias merges, retirements, and edge rejections; `--apply` executes the current plan atomically. Existing staged decisions and concepts attached to settled relationships are protected. `--nodes` / `--edges` restrict the plan |
| `triage-app` | Open the local Concept/Edge Triage App. Database rows appear immediately; named analyzer profiles score on demand in bounded batches; deterministic recommendations can be staged for review; author decisions remain separate until **Apply selected**. If Google Docs are linked, analysis offers an explicit reconcile preflight rather than pulling silently |
| `concept confirm <name> [--kind K]` | Confirm an extracted concept (optionally retyping it) |
| `concept confirm --all` / `--all-kind K` | Bulk-confirm unconfirmed concepts (all, or by kind) |
| `concept retire <name>… / --all-kind K` | Retire one or more concepts; `--all-kind` sweeps unconfirmed extracted ones |
| `concept confirm <edge> <relation>` | Promote an inferred relationship to declared |
| `concept unconfirm <name-or-edge>` | Undo a confirmation — back to hypothesis, re-enters triage |
| `proposal list/review/accept/dismiss` | Conflicts between new material and settled knowledge: reframed definitions, retired concepts recurring, rejected edges argued again, retired policies gathering support. Diff-gated — only text you actually changed can generate one; dismissed proposals never return verbatim |
| `concept reject-edge <edge>` | Reject an inferred relationship (kept for history) |
| `concept retire <name>` | Remove a concept (and its edges) from view and reasoning; kept for history, revived by `concept add` |
| `concept list --all` | Include retired/rejected items in the listing |
| `collect` | Snapshot revision → detect transitions → build episode → update graph → prerequisite-gap delta. The watcher path stages large deletions (>30% of a file) instead of committing them; a manual `collect` always proceeds |
| `diff [vN [vM]] [file]` | Colored unified diff between collected versions (default: the last two) |
| `plan [--draft]` | Writing plan: placement for every unrealized concept (near realized graph neighbors, in TOC reading order), prerequisites first, with intents and precedents; `--draft` writes opening stubs to `_drafts/` |
| `doc list/add/retire/revive` | Mechanical chapter management: list files (with the concepts each introduces and Google Docs link state), scaffold a new chapter (`--title`), archive one to `_retired/` (history stays replayable), bring it back |
| `doc push/pull <file>`, `doc auth` | Google Docs bridge (markdown only): `push` normalizes the local file and creates/updates a linked Doc inside an auto-created per-manuscript Drive folder ("AuthorLM — <name>"; move it anywhere later, links are id-based) (checked out — edit there); `pull` exports the Doc, normalizes away export churn, writes the file, and collects. Requires `[gdocs] client_secret` in config.toml; `doc auth` runs the one-time browser consent. Local files remain the system of record |
| `doc create-manuscript` | Combine every chapter (reading order per `toc.md`) into a single `_exports/<Manuscript Name>.md` and one Google Doc of the same name (`--title` overrides). Both are transient, push-only artifacts: re-exporting updates the same file and the same Doc (never reconciled or pulled; a Doc deleted in Drive is simply recreated). Works without Drive auth — the Doc half is skipped with a note |
| `sweep hygiene [--apply]` | Deterministic hygiene (zero tokens): ungrounded extracted concepts/edges, moot pending suggestions; `--apply` retires/rejects them |
| `sweep readiness` | Pre-publication checklist (pure auditor, zero tokens): unrendered slots, open proposals, active intents, toc coverage, checkouts, export settings, pandoc |
| `sweep ontology [file]` | Narrowing auditor: changed (or one file's) paragraphs vs. settled Concept Graph claims — deterministic narrowing, one cheap-model judgment, findings arrive as `incongruence` proposals (see docs/sweep-framework.md) |
| `lens add/list/run/register/review` | Author-ratified lenses (`_lenses/*.md` prompts): `run` executes natively on the configured model; `register` is the door for findings produced by an external agent (JSON on stdin); `review <n>` records verdicts as evidence |
| `guide` | Generate explained suggestions, or abstain |
| `review N --accept/--reject/--modify/--defer [--explain]` | Review a suggestion; explanations seed candidate policies |
| `policy list` / `policy answer <id> "..."` | Inspect learned policies; answer their outstanding questions |
| `status`, `log`, `history` | Inspect state, transitions, versions |

## Triage App analyzer profiles

Run `authorlm triage-app -m <manuscript>` for the standalone app, or call the
MCP tool `open_triage_app` to render the same compiled interface in an
MCP-Apps-capable chat host. Built-in, versioned profiles live in
`authorlm/triage_profiles/`; manuscript overrides live in
`<manuscript>/_triage/profiles/` and replace a built-in only when `id` and
`version` match. A profile declares its `triage_type`, prompt, output columns,
batch size, and optional `model`. Every run stores the resolved profile snapshot
and hash, so later file edits do not rewrite history.

Opening the app costs no tokens. **Analyze all** means unscored/outdated rows in
the current filter; **Reanalyze all** deliberately replaces current assessments.
Completed batches persist, but unfinished calls are neither queued nor resumed
automatically. Analysis is advisory and cannot stage a graph decision. Google
Docs reconciliation is also explicit: the analysis preflight can safely
pull/push one-sided changes and surfaces two-sided conflicts untouched, or the
author can choose the current local snapshot.

**Find safe recommendations** runs the same zero-token rules as deterministic
CLI triage and exposes their action, rule, and reason without changing the
database. Recommendations are transient and are rechecked against the current
manuscript and graph when staged. The review-state filter separates recommended,
staged, and unreviewed rows; selecting a row is independent of staging it.
Staging a recommendation creates a normal persistent draft. Only **Apply
selected** mutates the graph.

## Deterministic bulk triage

`authorlm concept triage --deterministic -m <manuscript>` scans both pending
concepts and inferred edges without an LLM call. It reports only decisions that
follow from stored aliases, exact duplicates, manuscript mention/recurrence
rules, or structurally invalid and duplicate relationships. The command is a
dry run unless `--apply` is present; application rechecks row versions and
commits the complete plan in one transaction. `--nodes` and `--edges` limit the
scan to one lane.

Machine decisions are recorded as `deterministic_triage` system evidence, not
as author concept/edge verdicts, so they do not teach the extraction prompt a
false author preference. A staged row, open proposal, or author-settled edge
protects the affected concept from automatic retirement.

## Margin threads (Doc comment conversations)

Comments in the master Doc are working conversations, not one-shot
feedback. A pull ingests new comments (never auto-resolving them); the
collaborator drafts a fix and registers it with `doc propose`, which
edits the tab into `<<old>>{{new}}` — old struck through, new in green,
the author's comment still anchored. The author replies a verdict
keyword in the margin ("go ahead", "lgtm" / "no", "revert"), edits the
green text first if they wish (modified acceptance — their words win),
or resolves the thread to withdraw the proposal. The next pull executes
the verdict, closes the thread with a receipt, and mirrors the result
locally. Pushes on thread-bearing tabs are surgical paragraph diffs
(read-back proven), so anchors survive. Every terminal verdict lands as
evidence; explained verdicts (`doc decide … --reason`) can seed scoped
candidate policies through a decline-by-default distiller.
Design: docs/margin-threads-design.md.

## How learning works

- **Accepting / modifying** a suggestion strengthens the policies it relied
  on; **rejecting** weakens them. Confidence is Laplace-smoothed support.
- An **explained** rejection or modification seeds a *candidate policy* from
  your explanation — you contribute evidence, never edit policy directly.
- Candidate policies resurface as reminders (once per session); repeated
  cross-session support (≥3, confidence ≥0.7) **validates** them; sustained
  contradiction retires them. Promotion is deliberately conservative.
- Concepts you declare become **realized** when they appear in the text;
  repeated co-occurrence of concepts creates **inferred** edges that await
  your confirmation in the briefing.

## Configuration (optional LLM)

The default provider is **LiteLLM** (in-process SDK), which routes to any
model LiteLLM supports. For Gemini:

```bash
pip install litellm
export GEMINI_API_KEY=...        # add to your shell profile to persist
```

Create `.authorlm/config.toml` in your workspace (TOML — comments with `#`
are native):

```toml
# AuthorLM configuration
[llm]
enabled = true
model = "gemini/gemini-2.5-flash"
```


**Reading order & the TOC.** A `toc.md` in the manuscript root is
structural, not prose: its ordered file references (e.g. `1. preface.md`)
define the authoritative reading order used by prerequisite checks,
definition precedence (a concept's primary location is its first
reading-order appearance; if that text is deleted, the location re-points
and you're told), plan placement, and hierarchical extraction. Files not
listed fall back to alphabetical order and are flagged in the briefing.
Concepts whose text vanishes entirely become proposals: adopt retires
them, dismiss keeps them as declared placeholders. Full extractions larger
than `extraction_max_chars` run hierarchically — one bounded pass per file
in reading order, each carrying the inventory of concepts found so far.

**Session-start Docs reconciliation.** When a session or shell starts and
linked Google Docs exist, AuthorLM three-way compares each file (agreed
base / local / Doc): in-sync files clear their checkout, a Doc-only change
auto-pulls (and collects), a local-only change auto-pushes, and two-sided
edits are announced as CONFLICTs and left untouched (resolve by hand or
`doc pull --force`). Never opens a consent browser; skips politely if
Drive isn't authorized; disable with `[gdocs] reconcile_on_start = false`.

Sessions expire lazily: if an active session has been idle longer than
`[session] idle_hours` (default 3) in `config.toml`, the next shell launch
or `session start` closes it retroactively at its last activity time and
runs episode analysis — you never need to remember `session end`.

Optional LLM settings: `"timeout_seconds"` (default 120) and
`"extraction_max_chars"` (default 24000) — the cap on manuscript text sent
per extraction call, balancing cost against coverage (concept selection
also degrades on very long inputs). Incremental extraction usually keeps
payloads far below the cap; when a payload does exceed it you get an
explicit truncation warning suggesting per-file extraction.

Any LiteLLM model string works (`gpt-4o-mini`, `claude-sonnet-5`,
`ollama/llama3`, …) with the matching key exported. Alternatively, point at
any OpenAI-compatible HTTP endpoint (LiteLLM proxy, Ollama, LM Studio) with
zero Python dependencies:

```toml
[llm]
enabled = true
provider = "openai"
base_url = "http://localhost:4000/v1"
model = "gpt-4o-mini"
api_key_env = "AUTHORLM_LLM_KEY"
```

What the LLM unlocks:

- **Concept extraction** — at `init` (or via `extract`), concepts and
  relationships are bootstrapped from the manuscript. Extraction targets
  *load-bearing units of thought* (RFC §21.3) and types nodes by kind —
  people and works cited in passing become `historical_reference`, not
  concepts, and never drive prerequisite suggestions. Extracted nodes and
  relationships are **hypotheses awaiting your confirmation**: they appear
  in the briefing and in `concept list` as "unconfirmed" until you
  `concept confirm <name>` (optionally retyping with `--kind`) or
  `concept retire <name>` them — your declared knowledge always outranks
  extraction. Use `concept triage` to sweep a large batch with one
  keystroke per node. **Extraction learns from your triage**: every
  decision is recorded as evidence, and the next extraction prompt carries
  your rejections ("never extract these"), your retype precedents, your
  confirmed exemplars, your rejected relationships, and your relation
  corrections (original ⇒ corrected). Relations have strict definitions in
  the prompt; a link is proposed only when the text asserts it, and
  unknown relations are dropped rather than coerced. Retired concepts are
  also hard-blocked — the model can propose them again, but they will
  never re-enter the graph.
- **Episodic precedents in guidance** — when suggesting a concept
  introduction, guidance retrieves how you handled the concept's
  already-realized *graph neighbors*: the analyzed decision sequences of
  the episodes that realized them ("Precedent — when you worked on
  'Field', you: opened with a lived example; then followed with the formal
  definition"). Retrieval is graph-guided (§21.8), falling back to your
  most recent analyzed episodes; because the explanation feeds the
  drafting prompt, drafted text follows your demonstrated approach.
- **Episode analysis** — when an episode closes (`intent complete` /
  `session end`, or `analyze` for backfill), the LLM reads what you
  actually wrote — the declared intent and the observed transitions — and
  infers the editorial decisions they show ("opened with a lived example
  before the formal definition"). Generalizable patterns become candidate
  policies (source `episode-analysis`) that strengthen across independent
  episodes, so the system learns your editorial judgment from your
  *writing*, not just from your reactions to its suggestions. Inferred
  decisions are stored on the episode; everything is a hypothesis and
  visible in the briefing — nothing validates without accumulation.
- **Policy distillation** — your review explanations are distilled into
  short normative policy statements (the verbatim explanation is preserved
  as evidence); the LLM may also decline to generalize a one-off remark.
- **Bridge drafting** — the top bridge suggestion includes a drafted
  paragraph to consider.

Everything degrades gracefully: if the LLM is disabled, unconfigured, or
unreachable, AuthorLM warns once and falls back to deterministic,
evidence-only behavior. The LLM enriches guidance; it is never load-bearing.

Every step that consults the LLM ends with a brief usage line, e.g.
`LLM: 1 live call(s) (702 in / 1,203 out tokens) — 2 replayed from cache`,
and the test suites report totals the same way.

## Testing

```bash
python3 tests/test_api.py       # API layer + CLI/MCP parity, hermetic
python3 tests/test_e2e.py       # hermetic: stub LLM server, no network/keys
python3 tests/test_loop.py      # proposal learning loop, hermetic (scripted LLM)
python3 tests/test_live_llm.py  # real LLM path, with record/replay
```

**API suite**: exercises `authorlm.api` (the logic layer beneath every
surface) directly — including the Google Docs bridge against an in-memory
Drive/Docs fake — and asserts the MCP server exposes the exact
hand-curated tool set (extend the `expected` list there when adding a
tool).

**Hermetic suite** (five scenarios): the full editorial loop
(RFC Appendix A shape), prerequisite-gap detection, unanswered objections,
guard rails, and LLM features + unregister against a stub OpenAI-compatible
server — no network or keys needed.

**Live-LLM suite** (record/replay): exercises the real LiteLLM → Gemini
path against a generated sample manuscript (never your manuscripts; reset
to the same default state every run). Every unique payload is sent to the
LLM **at most once** — responses are recorded in `tests/llm_cache/*.json`
and replayed thereafter, so an unchanged suite makes zero live calls and
runs without a key. Changing a prompt, the sample text, or the model
re-records exactly the affected calls (or delete a cache file to force
one). With no key and an incomplete cache, the suite skips loudly.

## Logging & traces

Every CLI command and MCP tool call appends one JSON line to
`~/.authorlm/logs/trace.jsonl` (or `<workspace>/.authorlm/logs/` under
`-w`): timestamp, surface (cli/mcp), verb, action, manuscript, duration in
milliseconds, ok/error. Always on, zero dependencies, best-effort (a
broken trace never breaks the operation), and size-rotated at 5 MB to
`trace.jsonl.1`.

The log is designed to be **read by agents**, not just humans: a
scheduled reviewer periodically scans it for slow operations (e.g. p95
duration per verb, LLM-bound verbs that got slower) and repeated errors,
and proposes optimizations or flags defects for the author to confirm.
Useful one-liners:

```bash
# slowest operations of the last run
jq -r '[.duration_ms, .verb, .action // ""] | @tsv' ~/.authorlm/logs/trace.jsonl | sort -rn | head

# errors only
jq -c 'select(.ok == false)' ~/.authorlm/logs/trace.jsonl | tail
```

The scheduled cloud reviewer ("AuthorLM daily improvement PR") runs
against a fresh GitHub checkout and cannot see `~/.authorlm` — it reads
the snapshot committed at `_diagnostics/trace.jsonl` instead. Refresh it
whenever you want the next run to see current telemetry:

```bash
cp ~/.authorlm/logs/trace.jsonl authorlm/_diagnostics/trace.jsonl
```

## Data

Everything lives in `.authorlm/authorlm.db` (SQLite). Historical objects —
versions, transitions, intents, reviews, evidence — are immutable; reading
the tables reads like the history of the book (RFC §18.7). See
[docs/MVP.md](docs/MVP.md) for scope and design decisions.

## Known MVP limitations

- Without an LLM, seeded candidate policies use your explanation verbatim
  as the policy statement (with an LLM they are distilled).
- Concept realization is word-boundary matching (plural-tolerant); it does
  not disambiguate homonyms.
- Extraction reads at most the first ~24k characters of the manuscript.
- Inferred intents, replay, and cross-manuscript long-term memory are
  schema-ready but deferred (RFC growth stages 7–8).
