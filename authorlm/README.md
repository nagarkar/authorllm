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
  whole-book export. PDF and EPUB presentation is declared in the packaged
  `authorlm/publication/` Pandoc profiles (shared structure filter, LaTeX PDF
  typography, and EPUB CSS), while manuscript export values remain in
  `_exports/settings.toml`.
  Author, copyright owner, paperback ISBN, and hardcover ISBN are canonical
  manuscript metadata, set with `authorlm manuscript set`. ISBN-13 values are
  validated and stored as digits. PDF export is a confidential review copy by
  default (notice page, footer, and watermark); `--print-ready` explicitly
  produces the clean production PDF.

## Install / build

```bash
cd authorlm
pip install -e .              # gives you the `authorlm` command
pip install -e ".[llm,mcp]"   # …with the LLM and MCP extras in one step
pip install -e ".[gdocs]"     # Google Docs bridge and session-start reconcile
pip install -e ".[all]"       # everything above
```

Installing builds the web pages (the Triage App, the audiobook page, and the
pronunciation workbench) into the package, so it needs **Node and npm**: the
version in `.nvmrc` (`nvm use`), within each page's `engines` range.
The built pages are not tracked in git (`setup.py`). Without npm the install
stops, unless the pages are already built or `AUTHORLM_SKIP_WEB_BUILD=1` is
set (then the commands that serve a page say it has not been built). After a
frontend change or a `git pull`, rebuild a page with `npm run build` in its
`web/<page>/` directory, or reinstall. The MCP server is a long-running
process; after changing its code or rebuilding the app, restart it with
`pkill -f authorlm-mcp` (the MCP client respawns it on the next call).

AuthorLM keeps **one database per workspace** — by default `~/.authorlm/`
— so `authorlm` works from any directory and manuscripts can live anywhere
(register them with relative or absolute paths; they're stored absolute).
See [Where your data lives](#where-your-data-lives) to put it elsewhere.
`python3 main.py …` works identically to `authorlm …`.

For bash tab completion (commands, actions, flags, `--kind` values, and
manuscript names after `-m`), add to `~/.bash_profile`:

```bash
eval "$(authorlm completion)"
```

The script is generated from the argument parser at eval time, so it stays
in sync with the CLI automatically. `authorlm help` (or `help` inside the
shell) lists all commands.

## Where your data lives

AuthorLM stores its database, backups, logs, and Google token in a
*workspace*: a directory with a `.authorlm/` folder inside. By default
that is your home directory, so the database is `~/.authorlm/authorlm.db`.

To put it somewhere else (another folder, an external SSD), run `setup`
once from the checkout:

```bash
authorlm setup                                          # asks where
authorlm setup --workspace ~/writing --yes              # no questions
authorlm setup --workspace "/Volumes/Crucial X6/authorlm" --yes
```

This records `AUTHORLM_WORKSPACE=<dir>` in the checkout's gitignored
`.env` and creates the database there. Both the CLI and the MCP server
read that line, so they always open the same database.

**Moving a workspace:** copy the `.authorlm/` folder to its new parent,
run `setup --workspace <new parent>`, then `pkill -f authorlm-mcp`.

**If the database is missing**, for example because the drive is
unplugged, every command stops and tells you so. Nothing ever creates an
empty database silently; only `setup` (or `init` with an explicit `-w`)
starts a new one.

For a one-off command against another workspace, `-w DIR` overrides the
`.env` setting. Precedence is `-w`, then `AUTHORLM_WORKSPACE` (shell
environment or `.env`), then home.

## Quick start — the shell workflow (recommended)

Register your manuscript once, then work in the interactive shell with your
editor (e.g. Obsidian, opened on the manuscript folder) alongside:

```bash
authorlm init --name my-book --path manuscripts/my-book \
  --author "Your Byline" --copyright-owner "Your Legal Name"
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
| `setup [--workspace DIR] [--yes]` | Point this checkout at a workspace (recorded as `AUTHORLM_WORKSPACE` in `.env`) and create its database; asks where when `--workspace` is omitted |
| `init --name N --path DIR [identity options]` | Register a manuscript directory (`.md`/`.txt`) with optional `--author`, `--copyright-owner`, `--paperback-isbn`, and `--hardcover-isbn`; with an LLM enabled, auto-extracts concepts (`--no-extract` to skip) |
| `manuscript show/set` | Inspect or update canonical publication identity (`--author`, `--copyright-owner`, and format-specific ISBN-13 fields); the CLI and MCP tools share this record |
| `export pdf [--print-ready]` | Export a PDF; confidential review notice, footer, and watermark are on by default, while `--print-ready` omits all three |
| `audio init/export/check` | The audiobook (docs/audiobook-pipeline-design.md): seed `_audio/audiobook.toml` and `cast.md` once; compile `audiobook.json` and `chapters/*.json` for the audiostation app (only what changed is rewritten; section ids hash text, voice and pronunciations so a change regenerates exactly what it touched); the readiness report from sources to generated state |
| `audio voices/audition/cast set` | Free voice discovery with preview clips into an audition gallery (`--library` for the shared library); paid auditions on the book's own words (`--file F --paragraphs N-M`, cost confirmed first); write one row of `cast.md` |
| `audio dictionary push` | `pronunciations.md` → the ElevenLabs dictionary named in `audiobook.toml`, append-only with replacement, after showing the plan |
| `audio say <term> [--say S,S] [--cast K] [--dictionary] [--settle]` | The fast pronunciation loop from the terminal: the word's own sentence, in the voice that says it, with each respelling substituted inline as the alias rule would (no push); plays the clips; `--settle` writes the row |
| `workbench [--port N] [--no-open]` | Serve the pronunciation workbench on a local port and open it, like `triage-app`: every word in the table (or a new one from the book), every voice that says it, a respelling box, Regenerate and listen per voice (the respelling, the voice's default with no rule, or via the pushed dictionary), Save to `pronunciations.md`, Remove a row (and its one remote rule) when the default sounds better, Push to ElevenLabs, Export. Built from `web/workbench/` into `authorlm/workbench_dist/` |
| `audiostation [--dry-run]` | Open the audiostation studio on the manuscript's `_audio/` folder: a built `.app` bundle if there is one, else the dev server (`AUDIOSTATION_APP` overrides) |
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
| `plan` | Writing plan: placement for every unrealized concept (near realized graph neighbors, in TOC reading order), prerequisites first, with intents and precedents |
| `doc list/add/retire/revive` | Mechanical chapter management: list files (with the concepts each introduces and Google Docs link state), scaffold a new chapter (`--title`), archive one to `_retired/` (history stays replayable), bring it back |
| `doc push/pull <file>`, `doc auth` | Google Docs bridge (markdown only): `push` normalizes the local file and creates/updates a linked Doc inside an auto-created per-manuscript Drive folder ("AuthorLM — <name>"; move it anywhere later, links are id-based) (checked out — edit there); `pull` exports the Doc, normalizes away export churn, writes the file, and collects. Requires `[gdocs] client_secret` in config.toml; `doc auth` runs the one-time browser consent. Local files remain the system of record |
| `doc create-manuscript` | Combine every chapter (reading order per `toc.toml`) into a single `_exports/<Manuscript Name>.md` and one Google Doc of the same name (`--title` overrides). Both are transient, push-only artifacts: re-exporting updates the same file and the same Doc (never reconciled or pulled; a Doc deleted in Drive is simply recreated). Works without Drive auth — the Doc half is skipped with a note |
| `sweep hygiene [--apply]` | Deterministic hygiene (zero tokens): ungrounded extracted concepts/edges, moot pending suggestions; `--apply` retires/rejects them |
| `sweep readiness` | Pre-publication checklist (pure auditor, zero tokens): unrendered slots, open proposals, active intents, toc coverage, checkouts, export settings, pandoc |
| `sweep ontology [file]` | Narrowing auditor: changed (or one file's) paragraphs vs. settled Concept Graph claims — deterministic narrowing, one cheap-model judgment, findings arrive as `incongruence` proposals (see docs/sweep-framework.md) |
| `lens add/list/run/register/review` | Author-ratified lenses (`_lenses/*.md` prompts): `run` executes natively on the configured model; `register` is the door for findings produced by an external agent (JSON on stdin); `review <n>` records verdicts as evidence |
| `interlocutor add/list/show/draft/run/import` | A tradition reads the whole book (`_interlocutors/*.md`, TOML front matter + prose; design `docs/interlocutor-design.md`): `run` scans for the ratified terms and writes a payload for a clean-context subagent (no model call); `import` verifies the report against the manuscript, lands it in `_critiques/`, and imports its improvements as proposed intents with critic provenance |
| `guide` | Generate explained suggestions, or abstain |
| `review N --accept/--reject/--modify/--defer [--explain]` | Review a suggestion; explanations seed candidate policies |
| `policy list` / `policy answer <id> "..."` | Inspect learned policies; answer their outstanding questions |
| `status`, `log`, `history` | Inspect state, transitions, versions |

## Triage App analyzer profiles

Run `authorlm triage-app -m <manuscript>`: it serves the compiled app on a
local port and opens it in the browser (the MCP App road was removed
2026-09-04; it never rendered in the client the author uses). Built-in,
versioned profiles live in
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


**Reading order & the TOC.** A `toc.toml` in the manuscript root is
structural, not prose: its ordered `[[chapter]]` tables define the
authoritative reading order used by prerequisite checks, definition
precedence (a concept's primary location is its first reading-order
appearance; if that text is deleted, the location re-points and you're
told), plan placement, and hierarchical extraction:

```toml
[[chapter]]
file = "preface.md"
matter = "front"     # optional: front | main (default) | back

[[chapter]]
file = "chapter1.md"

[[chapter]]
file = "section1a.md"
parent = "chapter1.md"   # optional: nests this file under chapter1.md
```

Files not listed fall to the end in alphabetical order and are flagged in
the briefing. Without a parseable `toc.toml`, the whole reading order is
alphabetical. A `toc.md` is not read as a TOC.
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
payloads far below the cap; a scope that exceeds it runs as several bounded
passes instead of being truncated. Only `extract --edges-only` still sends
one payload cut at the cap.

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

Beside it, `db-perf.jsonl` is the layer underneath: the trace log says a
verb took 900 ms, this one says where those 900 ms went. Every query
through the `Database` surface is timed and aggregated in memory per
normalized SQL shape, and **one** line per CLI process / MCP tool call
records `{n, total_ms, max_ms}` per shape plus the client provenance join
key; any single query over `[db] slow_ms` (default 100) also writes its
own line tagged `"slow": true`. On by default (`[db] perf_log`,
config.toml), best-effort, same 5 MB rotation. Read it with:

```bash
authorlm dbperf --days 30 --top 15
```

which prints the top shapes by total time and by worst single query, the
per-day trend, the slow-log tail and per-client attribution — no
one-off measurement, and no need to open the database.

Beside both, `usage.jsonl` is the third question: not which verbs ran and
not where their time went, but **where their money went**. Every model
call folds its tokens into an in-memory aggregate keyed by
`purpose | model` — `purpose` being the config section that chose the
model — and one line per CLI process / MCP tool call records
`{n, in, out, cache r/w, est_cost}` plus the same client join key; any
single call estimated over `[usage] expensive_usd` (default 0.50) writes
its own line tagged `"expensive": true`. On by default (`[usage] ledger`),
best-effort, same 5 MB rotation. Read it with:

```bash
authorlm usage --days 7
authorlm usage --days 30 --by verb        # or client | purpose | model | day
authorlm usage --sweep                    # close the books first
```

Two quantities land in it and they are **never summed**. `kind: "api"` is
billable, and its dollar figure is an **estimate from litellm's public
price list, not your invoice** — a model litellm cannot price records its
tokens and a `null` cost, never a guess, and the report names what it
could not price. `kind: "chat"` is what a Claude Code subscription
absorbed, swept incrementally from the session transcript (counts only,
never content, and never a transcript path); it carries **no dollar
figure at all**, because a subscription does not bill per token.

The chat sweep is opportunistic — at the flush point that already exists,
for the chat currently driving AuthorLM, at most once per session per
`[usage] sweep_interval_seconds`. No daemon, no thread, no cron. The
`SessionEnd` hook adds one forced sweep before it unlinks the marker,
which is the only place a chat's final turns are counted; without the hook
the report says so rather than under-counting silently.

The scheduled cloud reviewer ("AuthorLM daily improvement PR") runs
against a fresh GitHub checkout and cannot see `~/.authorlm` — it reads
the snapshot committed at `_diagnostics/trace.jsonl` instead. Refresh it
whenever you want the next run to see current telemetry:

```bash
cp ~/.authorlm/logs/trace.jsonl authorlm/_diagnostics/trace.jsonl
```

## Client provenance — which chat did this

`source_id` records *whose judgment* originated a knowledge object. This
records *which conversation* the author was having when it was born — a
different question, and one that only matters once several chats share
one workspace, which they now do.

Every row created from 2026-08-30 carries a small stamp in its metadata:
`{"client": {"engine", "session", "precision"}}`. The reconstruction
payload — label, start time, transcript path — is written once per client
per AuthorLM session, on the session row. A writeup also keeps a
touched-by list, so a writeup resumed by a second chat shows the seam.
Nothing gates on any of it, and no verb errors if it is missing.

```bash
authorlm provenance wu-3f0c          # which chats touched this object
authorlm provenance --client a1ea8c7 # invert: what did this chat touch
authorlm provenance --clients        # every chat we have seen
```

`precision` says what the claim is worth. `exact` means the identity
travelled with the invocation. `ambient` means it was inferred from
workspace state and may misattribute when several chats are open — and
when two are live at once the resolver **declines to name one at all**
rather than guess.

**Turning it off.** `AUTHORLM_CLIENT=none` disables detection entirely;
the test suites set it, and so can you. Session ids and transcript paths
are machine-local personal data, so if you share a database backup, that
is the switch. `AUTHORLM_CLIENT=engine:session:label` (or a JSON object)
forces a specific identity instead.

**The hook is optional.** On Claude Code the chat is attributed *exactly*
with no hook at all — `CLAUDE_CODE_SESSION_ID` reaches both Bash-tool
subprocesses and stdio MCP servers. The `SessionStart` / `SessionEnd`
hooks only add the label, the start time and the transcript path (and are
the fallback if that undocumented variable ever disappears). Hooks
execute code, so nothing is installed for you — print the snippet and
paste it yourself:

```bash
authorlm client-hook --print-setup
```

Put it in this repo's `.claude/settings.json` (committable; binds every
chat started in this project) or in `~/.claude/settings.json` (all
projects, personal, not committable).

**Rows older than the stamp.** Nothing is backfilled — correlating old
rows to old transcripts by timestamp is a confident guess across parallel
chats, which is exactly the failure this exists to prevent, so
`provenance` prints a footnote instead. The pre-provenance method still
works, and is exact when it hits: Claude Code stores one JSONL per
session at `~/.claude/projects/<cwd-with-slashes-replaced-by-dashes>/<session_id>.jsonl`,
and AuthorLM ids appear verbatim in tool results, so grep the transcripts
for the id — the filename *is* the session id:

```bash
grep -l 'wu-3f0c9a12e4b7' ~/.claude/projects/-Users-you-repos-authorllm/*.jsonl
```

It is silent when the transcript has been deleted or the work was done
from the bare CLI. That is the whole technique, and the stamp does not
replace it.

## Data

Everything lives in `<workspace>/.authorlm/authorlm.db` (SQLite; see
[Where your data lives](#where-your-data-lives)). Historical objects —
versions, transitions, intents, reviews, evidence — are immutable; reading
the tables reads like the history of the book (RFC §18.7). See
[docs/MVP.md](docs/MVP.md) for scope and design decisions.

## Known MVP limitations

- Without an LLM, seeded candidate policies use your explanation verbatim
  as the policy statement (with an LLM they are distilled).
- Concept realization is word-boundary matching (plural-tolerant); it does
  not disambiguate homonyms.
- `extract --edges-only` sends a single payload capped at
  `extraction_max_chars` (default 24k characters) and truncates anything
  beyond it; other extractions split oversized scopes into multiple
  bounded passes rather than truncating.
- Inferred intents, replay, and cross-manuscript long-term memory are
  schema-ready but deferred (RFC growth stages 7–8).
