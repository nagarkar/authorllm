# Multi-tenancy and true multi-manuscript — design

Status: DRAFT for grilling, 2026-09-02; the author's first-round rulings
are recorded in §7 and folded into the decisions. Nothing here is built.
Written against the working tree at 465b491 plus the uncommitted
resolve/footnote work.

Scope the author set: a few users besides the author; the chat interface
stays; a new manuscript starts with a reasonable set of filters, lenses and
profiles; manuscript properties are customizable; model selection and the
other machinery stay common.

## 0. The shape in one paragraph

A **tenant** is the user or organization using AuthorLM: billed, holding
keys, owning what its workspaces learn. A **workspace is the isolation
unit** — one directory, one SQLite database, one Google token, one usage
ledger, one backup ring, and the manuscripts under it; a tenant has one or
more. The engine already scopes 33 of its 35 tables by `manuscript_id`, so tenancy
does not touch the schema; it touches identity (`created_by` is the OS
user), file homes (config and `.env` are read from the repo checkout), the
default-manuscript rule (`get_manuscript(None)` refuses when two exist), and
the chat surface (the skill assumes one manuscript and a Bash prompt in the
repo). What is **common** ships inside the package: `config.toml`, prompts,
publication templates, the illustration craft file, triage profiles, the
Google client, and a new `kits/` directory of starter artifacts. What is
**per manuscript** is a properties registry on the `manuscripts` row plus
the underscore directories it already owns. The recommended path is staged:
make the author's own workspace truly multi-manuscript (needed regardless),
then hand guests the same package with a setup wizard and a Claude Code
plugin (workspace = their own `~/.authorlm`), and only then, if install support
or central spend control demands it, run the same package hosted behind a
remote MCP with a workspace router and one `run_cli` tool.

## 1. Where the system stands

| Layer | Today | Verdict |
|---|---|---|
| Database rows | Every table but `manuscripts` and `sources` carries `manuscript_id`; `unregister` purges one manuscript cleanly | Already manuscript-scoped. Nothing to add. |
| Manuscript default | `api.get_manuscript(None)` raises when more than one is registered; every MCP tool takes optional `manuscript`; `resolve_file` maps a path to its manuscript | The only single-manuscript rule in the engine. |
| Identity | `ko_fields.created_by = getpass.getuser()`; `sources` (author / critic / system) keyed by name; `clients` records *which chat*, not *who* | Single-author assumption. Needs a principal. |
| Workspace | `~/.authorlm/` holds `authorlm.db`, `gdocs_token.json`, `logs/`, `backups/`, `clients/`; `--workspace` / `AUTHORLM_WORKSPACE` already select it | The tenant boundary already exists under another name. |
| Manuscript directory | Arbitrary `path` column; SMSTTD lives at `authorlm/manuscripts/SMSTTD` (gitignored) with `_filters _lenses _profiles _concepts _critiques _drafts _exports _illustrations _assets`, `toc.toml`, `pronunciations.md`, `manifest.md` | Self-contained per manuscript. Good. |
| Project config | `authorlm/config.toml` in the repo: models per purpose, budget, usage, gdocs client secret as an absolute path on the author's machine, `[illustrations] image_size` | Common — except `image_size`, which is a manuscript property in the wrong home, and the secret path, which is machine-specific. |
| Prompts, triage profiles, publication templates, craft file | Package data, one copy | Common. `lenses.LENS_SYSTEM` and the MCP instructions say "philosophy manuscript"; the summarizer's ASSUMES/INTRODUCES vocabulary assumes an essay collection. Parametrize, do not fork. |
| Filters and lenses | Nine filters, five lenses, two profiles, all in SMSTTD's directories; `docs/filters.md` and `docs/lenses.md` are their checked-in copies; `testbench/_filters` is empty | A new manuscript starts with nothing. Needs kits. |
| Export title | `_exports/settings.toml` `title`, else the manuscript name | A property hiding in a sidecar. |
| Google Docs | One token per workspace; per-manuscript mapping in `manuscripts.metadata.gdocs`; installed-app OAuth flow | Per-tenant token already; hosted needs the web flow. |
| Chat surface | stdio MCP, one process per chat, `.mcp.json` at repo root; 1319-line skill with no SMSTTD hardcodes, but it runs CLI verbs through Bash in the repo checkout | Keep; make manuscript-aware; distribute as a plugin. |
| CLI | 46 verbs, `-m/--manuscript`, `--workspace` | Keep as is. |
| Sessions | One active session per manuscript, shared by every chat on the workspace | Fine per tenant; per-manuscript already. |
| Usage, budget, backups | Per workspace: `usage.jsonl`, dormant `[budget]` seam, 7-deep backup ring | Per tenant for free. Hosted adds off-box copies. |
| Improvements | `improvement_tasks` per manuscript | They are about the system, not a manuscript; guests' tasks must reach the author. |

## 2. Vocabulary

- **Tenant** — the user or organization using AuthorLM (ruling, §7): the
  unit that is billed, holds provider keys, and owns what its workspaces
  learn. A tenant has one or more workspaces. For the first guests a tenant
  is one student.
- **Workspace** — the isolation unit: one directory, one database, one
  Google token, one ledger, one or more manuscripts. `~/.authorlm` by
  default, `--workspace` for another. The word the code already uses.
- **Principal** — the person acting, resolved once per invocation:
  `{id, name, email, kind}`. Replaces `getpass.getuser()` everywhere a row
  is stamped or a `sources` row is named. Equal to the tenant today;
  diverges when an organization is the tenant or two people share a
  manuscript.
- **Manuscript** — unchanged: a registered directory plus its rows.
- **Kit** — a starter set of manuscript artifacts shipped in the package
  and *copied* into a new manuscript at `init`.
- **Property** — a per-manuscript setting in a typed registry, stored on the
  `manuscripts` row, consumed by a named verb.
- **Common** — anything read from the installed package, identical for
  every tenant.

## 3. Decisions

### D1. Workspace = the isolation unit; one database per workspace; the tenant above it

The hierarchy is tenant → workspaces → manuscripts. `open_db(workspace)`
already builds the path. Isolation is a file boundary, so a forgotten
`WHERE` clause can never leak across workspaces, backups and deletion are
directory operations, and the per-workspace usage ledger, logs and Google
token fall out unchanged. Cross-workspace reads (a tenant's or an admin's
usage roll-up) iterate directories.

The tenant itself is thin. Locally it is `[tenant]` in `workspace.toml`
(`id`, `kind = user | org`) and nothing else needs it. Hosted, `admin.db`
holds tenants, their workspaces, and tokens. Provider keys are issued per
tenant — a student's keys are their own; an organization tenant would issue
one per workspace.

Rejected: a `tenant_id` column. Thirty-five tables, several hundred query
sites, and one missed filter is a leak. SQLite has no row-level security to
catch it.

Layout of a workspace:

```
<workspace>/
  workspace.toml        # tenant, identity, default_manuscript (tiny)
  .env                  # API keys (local topology only; hosted keeps keys server-side)
  authorlm.db (+wal/shm)
  gdocs_token.json
  logs/  backups/  clients/
  manuscripts/<name>/   # default home for new manuscripts; `path` stays free-form
```

The author's SMSTTD keeps its current path; nothing forces a move.

### D2. A principal replaces the OS user

New module `identity.py`: `Principal` resolved from `workspace.toml
[identity]` locally, or from the auth token when hosted, held in a
contextvar for the invocation. `db.ko_fields` stamps `created_by =
principal.id`; `db.source(kind="author")` uses `principal.name`. Critic and
system sources are unchanged. The author's id stays `"nagarkar"` so
existing rows line up with new ones.

### D3. Four homes, not three

`paths.py` documents project config, workspace state, and secrets. Tenancy
adds the distinction the code has been blurring: **common** lives in the
*package* (not the repo checkout — for the author these are the same
directory, for a guest they are not). `config.toml`, `illustration-craft.md`,
the Google client secret and the kits become package data; `.env` moves to
the workspace. `AUTHORLM_CONFIG` / `AUTHORLM_ENV` / `AUTHORLM_CRAFT`
overrides stay as the test pins.

Common (package): `config.toml`, `prompts/`, `kits/`, `publication/`,
`triage_profiles/`, `illustration-craft.md`, `google-client.json`, the skill.
Tenant (workspace): `workspace.toml`, database, token, logs, backups, `.env`.
Manuscript (directory + row): everything under `_*/`, `toc.toml`,
`pronunciations.md`, properties, style guides, graph, beliefs, Doc mapping.

### D4. The current manuscript

Three layers, checked in order:

1. An explicit `manuscript=` argument (tools) or `-m` (CLI), as today.
2. The connection's selection: new tool `use_manuscript(name)` sets it for
   the life of the MCP process (stdio: one process per chat; hosted: keyed
   by the MCP session). `authorlm use <name>` writes it for the CLI.
3. `workspace.toml default_manuscript`, which `use` also updates.

`get_manuscript(None)` keeps refusing when none of the three resolves and
more than one manuscript exists — the refusal message lists them and names
`use_manuscript`. Every tool result and every CLI verb's first line carries
the manuscript name, so a transcript is never ambiguous about which book a
verdict landed on. `resolve_file` stays the path door and, when it resolves
a file in a different manuscript from the current one, says so rather than
switching silently.

`get_briefing` defaults to the current manuscript and adds one line naming
any other manuscript in the tenant with an open session.

### D5. A properties registry on the manuscript row

Add `manuscripts.properties` (JSON) and a registry `api.PROPERTIES`, one
row per key: type, default, validator, and the verb that consumes it. The
six existing columns (author, copyright owner, two ISBNs, trim, bleed) stay
where they are and appear in the registry with `storage = column`; new keys
go in the JSON. `manuscript show/set` and `get/set_manuscript_metadata`
read the registry and refuse unknown keys.

| Key | Type | Consumer | Today |
|---|---|---|---|
| `title`, `subtitle` | text | export, Doc master name, title page | `_exports/settings.toml` |
| `language` | BCP-47 | pandoc `lang`, hyphenation, summarizer | hardcoded English |
| `kind` | `essays` \| `monograph` \| `sermons` \| … | kit choice; the noun in prompts ("a manuscript of {kind}") | "philosophy manuscript" in code |
| `unit_noun` | text, default `essay` | reports, summaries, briefing ("24 essays") | hardcoded |
| `illustrations.size` | `WxH` | `illus render` | `config.toml [illustrations] image_size` |
| `illustrations.aspect` | e.g. `3:2` | placement prompts | comment in config |
| `publication.overlay` | bool | export looks in `<ms>/_publication/` first | — |
| `docs.folder_id` | text | gdocs | `metadata.gdocs` |
| `kit` | `essays/1` | `init`, later `kit diff` | — |

Rejected: a `manuscript.toml` at the manuscript root as the source of
truth. It would be a second source beside the row, invisible to the Doc
mirror, and the row is where `manuscript set` already writes. A
`manuscript.toml` appears only in the portable bundle (§4.5).

### D6. Kits: the starter set

`authorlm/kits/<kit>/` mirrors a manuscript root: `_filters/`, `_lenses/`,
`_profiles/`, `toc.toml`, `pronunciations.md`, plus `kit.toml` (name,
version, the `kind` it serves, a ratification checklist of style aspects).
`authorlm init --kit essays` copies it — copies, never links: the author
edits their own artifacts, and a kit that later changes must not rewrite a
filter someone has ratified. Each copied artifact carries `kit =
"essays/1"` in its front matter so its origin is visible.

The rule for a kit artifact: it may name only what the harness supplies —
STYLE LAW, CONCEPT NOTES, PROTECTED TERMS, the AUDIENCE and MARKET
profiles, essay summaries — never a part, an essay, or a term of one book.
SMSTTD's carve-out "the Sermons' register" becomes "any unit whose
effective style guide declares an assertoric or prophetic register", which
is the same rule stated through the machinery.

Classification of the live SMSTTD set:

| Ships in `kits/essays` | Stays SMSTTD-only |
|---|---|
| Filters: `duplicate-words`, `audio-friendly`, `claim-status`, `image-then-paraphrase`, `metaphor-consistency`, `modal-register`, `reader-load`, `history-not-quality` | Filter: `spiral-support` (written for the essay on God) |
| Lenses: `metaphor-as-mechanism`, `repetition-across-parts`, `pitch`, `historical-reach`, `uncited-apparatus` | — |
| Profiles: `audience.md`, `market.md` as templates with the section headings and the question each answers | The filled SMSTTD profiles |
| `toc.toml` with the matter/parent grammar in comments; empty `pronunciations.md` | — |

Style law ships as **no rows** — law is declared, not inherited — but the
kit's checklist (register, lexicon, syntax, tone, figure, citation,
illustration) drives the onboarding interview in D8. The kit directory
replaces `docs/filters.md` and `docs/lenses.md` as the checked-in copy of
the generic artifacts; those two files keep only the SMSTTD-only ones.

**The first kit is `paper`, not `essays`** (ruling, §7: the first guests
are high-school students writing papers in geography, physics and literary
arts). `essays`, derived from SMSTTD as above, is the author's own kit and
ships second. The `paper` kit is designed against one real assignment, and
differs from `essays` in shape rather than in machinery:

- One file, `toc.toml` with a single chapter, `unit_noun = section`,
  `illustrations = "none"` by default. Math support (TeX in `$…$`) already
  covers the physics case.
- Profiles: `audience.md` names the reader the assignment names;
  `assignment.md` replaces `market.md` and holds the teacher's prompt and
  rubric verbatim — the `reader-load` and `pitch` artifacts read it the
  way they read the audience profile today.
- From the generic set: `duplicate-words`, `claim-status`,
  `modal-register`, `reader-load`, `pitch`, `uncited-apparatus`,
  `historical-reach`. Dropped as book-shaped: `audio-friendly`,
  `repetition-across-parts`, the metaphor pair, `image-then-paraphrase`.
  New and paper-specific: a `thesis-support` filter (does each section
  earn its place against the stated thesis, which is the paper's declared
  intent) and a `citation-form` filter driven by the `citation_style`
  property.
- A `_publication/` overlay with school formatting: double spacing,
  name/course/date header, page numbers, the citation style. `export pdf`
  is the deliverable.
- Subject is not a kit. `subject` is a property (`geography`, `physics`,
  `literary arts`) that enters the prompts as a noun; the assignment
  profile carries everything else the subject changes.

Properties the paper kit adds to the registry: `subject`, `citation_style`
(`MLA` | `APA` | `Chicago`), `assignment.due` (informational only). Fiction
stays deferred: the concept graph and the ASSUMES/INTRODUCES summaries
model an argument, not a plot, and literary-arts papers are arguments about
literature, which fits.

### D7. Publication overlay

`export` resolves `pdf-header.tex`, `book-header.tex`, `epub.css` and the
YAML profiles from `<manuscript>/_publication/` before the package copy,
file by file. The package copies lose their SMSTTD-specific comments. The
review watermark text, notice page and footer read `title` and `author`
from properties.

### D8. The chat surface: plugin plus manuscript-aware skill

The skill and the MCP registration become a Claude Code **plugin** in its
own small git repository (`authorlm-plugin`: `skills/authorlm/SKILL.md`,
`.mcp.json`, an install hook that checks `authorlm --version`). Guests
install it; the author's `.claude/skills/authorlm` becomes a symlink or the
plugin's dev path. One skill, common to every tenant.

Skill changes:

- **Session start.** `list_manuscripts`; one → use it; several and no
  default → ask once, then `use_manuscript`. Say the manuscript's name in
  the first reply of every session.
- **Every relayed result names the manuscript** when the tenant has more
  than one.
- **Onboarding a manuscript** — a new protocol section, shaped by the kit.
  For `essays`: what is it and for whom (fills `audience.md`, `market.md`),
  kind and unit noun, the terms of art the book redefines (seed concept
  nodes), register and lexicon (ratify the first root-guide laws one aspect
  at a time from the kit checklist), illustration shape, trim size. For
  `paper`: paste the assignment (fills `assignment.md`), subject, citation
  style, the thesis (recorded as the first declared intent), and the
  register the assignment asks for. Then `init --kit`, `manuscript set …`,
  and — if prose already exists — `extract` and `summarize rebuild`. The
  interview's answers are recorded verbatim as the evidence behind each
  law.
- **The verb door.** Locally the verbs run through Bash as now. Hosted,
  the same verbs run through `run_cli` (D9). The MCP server's
  `instructions` state which door is live; the skill says "run the verb"
  and names both doors once.

Rejected: claude.ai custom connectors as the guest surface. No skill file,
no Bash, no plugin — the entire protocol layer would have to move into
tool descriptions.

### D9. Topology: local first, hosted second, same package

**T1 — local, workspace = the guest's own `~/.authorlm`.** Confirmed by
the rulings: guests have Claude Code, edit locally, and carry their own
keys. Each guest: `pip install "authorlm[all] @ git+…"`, the plugin, and
`authorlm setup`, a wizard that creates the workspace, asks name and email
(the principal and, for now, the tenant), writes `.env` from the guest's
keys, runs `doctor` to name what is missing, and offers the Google consent
flow. Keys are **per guest**, so each carries its own spend cap at the
provider and the guest's bill is the guest's; `config.toml` shipping inside
the package is what "model selection stays common" means here. The Google
client secret ships as package data (Google treats installed-app secrets
as non-confidential); a guest who wants the Doc road is added as a test
user on the GCP project until the app is verified. The Doc road is
optional: `setup --no-docs` leaves it off, and filter and critique
proposals are then ruled on locally.

The install burden is the risk for this audience: Python 3.11, the
package, the plugin, and — for `export pdf` — pandoc and a TeX
distribution on a student's machine. `doctor` names each gap with the
one-line fix; whether PDF export instead runs elsewhere is an open
question (§7).

What T1 cannot give: a central view of usage, server-side backups, and
zero-install.

**T2 — hosted, tenant = a directory on the author's server.** One process
serving remote MCP (FastMCP streamable-HTTP) plus a small HTTP surface:

- `admin.db` beside the tenants: tenants, hashed bearer tokens, per-tenant
  budget, status. `authorlm-admin tenant create|suspend|usage`.
- Auth middleware: token → tenant → workspace path; sets the tenant and
  principal contextvars; `open_db` reads the contextvar. A request that
  resolves no tenant gets no tool list.
- **`run_cli(argv: list[str], stdin: str = "")`** — one tool that runs
  `cli.main` in-process with `--workspace` forced to the tenant and cwd set
  to it, returning stdout, stderr and exit code. This is what keeps the
  skill's flows (write loop, filter road, `doc push`, export) intact
  without re-expressing 46 verbs as tools. Denylist: `shell`, `watch`,
  `setup`, `unregister`, `dbperf`, `usage --workspace`; anything
  interactive. Outputs over the MCP result cap are written to
  `_exports/` and returned as a path plus tail.
- **Path confinement.** Every path argument — `resolve_file`, `critique
  import`, `illus` output, `-m` — resolves under the tenant root or is
  refused. `--workspace` is not accepted from the wire.
- **Files.** `files_put` / `files_get` tools for small text; an HTTP
  endpoint with the same token for PDFs and images.
- **Google.** A second OAuth client of type *web* in the same project;
  `GET /oauth/start` → Google → `/oauth/callback` writes the tenant's
  token. `doc` verbs run with `interactive=False` and, lacking a token,
  return the start URL.
- **Keys and budget.** Provider keys live server-side; the dormant
  `[budget]` seam turns on per tenant with `enforce = true`, caps read from
  `admin.db`.
- **Backups.** The existing ring per tenant, plus a nightly off-box copy
  (the productionization campaign's open finding, now with a reason to
  close it).
- **Editing stays local** (ruling, §7). Hosted needs a small sync client
  that mirrors the guest's manuscript directory to the workspace's
  server-side copy, the way the Docs bridge mirrors to Google: the
  database already holds full-text versions, so the local directory is the
  working copy and the server holds the record. Docs-only editing is not
  the plan.
- **Chat consumption** (`usage.chat`) cannot see a guest's transcripts; the
  ledger records API spend only for hosted tenants.
- **claude.ai connectors** become an option here, not before: a hosted
  server is a URL a guest can add to their own claude.ai account, with the
  skill uploaded there as a custom skill. Evaluate against the plugin once
  hosted exists.

The author keeps running locally either way; T2 is for guests. Nothing in
T2 changes the package except the server module, the admin CLI, and the
contextvar-aware `open_db` / `identity`.

Recommendation: build §6 phases 0–2 and onboard the first guests on T1.
Move to T2 when a guest cannot install, or when provider key caps stop
being enough control over spend. T2's largest pieces (`run_cli`, the
router, confinement) reuse everything T1 builds.

Rejected: Postgres and a `tenant_id` schema; re-expressing every CLI verb
as an MCP tool (the CLI is the state machine and the skill's vocabulary —
`run_cli` preserves both); a thin remote CLI (a second client to keep in
step, and the skill would still need a door the guest has installed). If a
cloud database with local files is ever wanted, the routes that keep this
design are the sync client above or an embedded replica of the
per-workspace database; Postgres with a schema per workspace is the
mechanical fallback and never needs a tenant column.

### D10. Improvements travel to the author

`file_improvement` stays per manuscript (the evidence is manuscript-bound)
and gains `authorlm improvements export` — a JSON bundle a guest sends
back. Hosted, `authorlm-admin improvements` reads every tenant. Filing
GitHub issues from it is a later verb.

### D11. What a workspace learns belongs to its author

Ruling, §7: validated beliefs and ratified laws never inform the common
kit; they are the writing author's IP. Consequences: no verb reads
beliefs, laws, evidence or the graph across workspaces, ever, including
admin verbs; the kit evolves by the AuthorLM author's hand only, from
their own manuscripts; `improvements export` (D10) carries the
`improvement_tasks` rows and nothing else — no manuscript text, no
beliefs — and shows the guest the bundle before it leaves the machine.

### D12. Co-authoring later, attribution now

Ruling, §7: one person per manuscript for now, two later. The cheap
preparation is to stamp the principal on every row from day one (D2) and
to give each principal their own `sources` author row, so that when a
second person joins a manuscript the history already says who ruled what.
The shared-session behaviour in the operational notes (one active session
per manuscript, every chat sharing it) becomes a design problem at that
point and is deferred until then.

## 4. Component changes

### 4.1 `paths.py`
`config_path()`, `craft_path()`, `client_secret_path()` resolve to package
data; `env_path()` and a new `workspace_config_path()` resolve to the
workspace. `project_dir()` remains for tests and the editable install.

### 4.2 `identity.py` (new)
`Principal`, `current()`, `configure(principal)`. Read by `db.ko_fields`,
`db.source`, `clients.row_stamp` (adds `principal` to the stamp), and the
server's auth layer. Never raises; an unresolved principal is
`{"id": "unknown"}` and `authorlm doctor` says so.

### 4.3 `api.py`
`PROPERTIES` registry; `get_property` / `set_properties`; `get_manuscript`
three-layer resolution; `create_manuscript(name, kit, properties)`
replacing the `init` internals; `use_manuscript`. `load_config` reads
package data. Prompt assembly takes `kind` and `unit_noun` from properties
wherever the code says "essay" or "philosophy".

### 4.4 `kits/` (new package data) and `kit.py`
`list_kits`, `apply_kit(kit, manuscript_root)`, front-matter stamping.

### 4.5 Portable bundle (`manuscript bundle export|import`)
The manuscript directory plus a `manuscript.toml` (properties) and a JSON
dump of its rows (graph, beliefs, laws, evidence, summaries, Doc mapping,
versions). Moves a manuscript between tenants and topologies without
touching another tenant's database. Not needed for the first guests; needed
the first time one migrates from T1 to T2.

### 4.6 `mcp_server.py`
`use_manuscript`, manuscript name stamped on every result, `run_cli`
(hosted only), instructions parametrized by `kind`. Stdio unchanged for
local use.

### 4.7 `server.py` and `admin.py` (new, T2 only)
As in D9.

### 4.8 Skill and plugin
As in D8. The plugin repo carries the skill, `.mcp.json` variants for
local and remote, and a README with the three install steps.

## 5. Migrating the author's own state (by hand, no shims)

1. Add the `properties` column. Set SMSTTD `title` from
   `_exports/settings.toml`, then drop the key from that file. Set
   `illustrations.size = "1536x1024"` on SMSTTD; delete `[illustrations]
   image_size` from `config.toml`.
2. Write `~/.authorlm/workspace.toml` with `identity.id = "nagarkar"`,
   name and email, and `default_manuscript = "SMSTTD"`.
3. Move `authorlm/.env` to `~/.authorlm/.env`. Move the Google client
   secret into the package as `google-client.json`; delete the absolute
   path from `config.toml`.
4. Copy the generic filters and lenses into `kits/essays/`, rewriting
   their SMSTTD carve-outs through STYLE LAW. SMSTTD's own copies are
   untouched. Trim `docs/filters.md` and `docs/lenses.md` to the
   SMSTTD-only artifacts.
5. Leave `testbench` registered; it becomes the kit's test fixture.

## 6. Build order

Each phase ships on its own and the author benefits from 0 and 1 before
any guest exists.

| Phase | Delivers | Touches | Size |
|---|---|---|---|
| 0 — multi-manuscript in one workspace | D4 current manuscript, D5 registry with title and illustration size, D7 overlay, skill session-start rule, migration §5 steps 1–2 | api, db, cli, mcp_server, export, illus, skill | M |
| 1 — the `paper` kit and onboarding | D6 kit directory and `init --kit paper`, the two paper-specific filters, the `assignment` profile, the school `_publication/` overlay, prompt parametrization by `kind`/`unit_noun`/`subject`, onboarding protocol in the skill | kits, kit.py, lenses, filtering, summaries, briefing, export, skill | M |
| 2 — guest packaging (T1) | D2 principal, D3 homes, `authorlm setup` with `doctor` and `--no-docs`, plugin repo, D10/D11 export, install docs | paths, identity, db, cli, pyproject, plugin | M |
| 3 — hosted (T2) | D9: server, admin.db, auth, router, `run_cli`, confinement, files, web OAuth, per-tenant budget, off-box backups | server, admin, mcp_server, gdocs, budget, backup | L |
| 4 — later | the `essays` kit from SMSTTD (§5 step 4), tenant members and co-authoring (D12), sync client, bundle import/export, `kit diff`, admin usage dashboard, issue filing from improvements | — | — |

## 7. Rulings and open questions

Recorded 2026-09-02, the author's words kept where they carry the ruling.

| Asked | Ruled |
|---|---|
| Who are the first guests? | "High schoolers who want to write papers in high school — geography, physics, even literary arts." → the `paper` kit is first (D6). |
| Do guests have Claude Code? | Yes. → T1 is the first topology (D9). |
| Who pays for model calls? | "Per guest keys pay for LLM calls." → keys per guest, caps at the provider (D9). |
| Local editing or Docs only? | "Guests will edit locally." → Doc road optional; hosted needs a sync client (D9). |
| Two people on one manuscript? | "Not for now, but it will be the case in the future." → attribution now, membership later (D12). |
| Do learned beliefs and laws feed the kit? | "This becomes author IP." → never (D11). |
| Is "workspace" the tenant word? | "A tenant can have more than one workspace, so tenant is a user or organization using AuthorLM." → tenant above workspace (D1, §2). |

Open, second round:

1. **Drafting policy for students.** The beat loop drafts prose; guidance,
   lenses and filters propose. Schools have rules about which of those a
   student may use. Proposal: `kit.toml` carries a `verbs.disabled` list,
   and the `paper` kit ships with beat drafting off and proposals on; the
   student can turn drafting on knowingly. Your call, and worth asking a
   teacher.
2. **Which assignment** the paper kit is designed against. One real prompt
   and rubric, ideally from a first guest.
3. **PDF export on student machines.** Pandoc plus TeX is a heavy install
   for this audience. Alternatives: a Markdown-to-DOCX export (pandoc
   alone, no TeX; the DOCX road already exists for the Doc bridge), or
   exports run on your machine or the hosted server later.
4. **Google Docs for students** — offered or omitted at setup? The design
   makes it optional; the default is yours.
5. **An organization tenant** (a school): who holds the keys, who sees
   usage, and whether a teacher is a principal on a student's workspace.
   Not needed for the first guests; the vocabulary now has room for it.

## 8. Summary of rejected alternatives

- `tenant_id` column and one shared database.
- `manuscript.toml` as a source of truth.
- Linked (not copied) kit artifacts.
- claude.ai connectors as the guest surface.
- Forty-six MCP tools in place of `run_cli`; a remote-mode thin CLI.
- Postgres.
