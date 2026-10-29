# Exceptions to metalm

This repo adopted metalm with the gaps below grandfathered. New work follows metalm. A row goes when its gap is fixed, in the PR that fixes it.

## Grandfathered exceptions

| rule | guideline | where in repo | since |
|---|---|---|---|
| `CLAUDE.md` holds only the template; no line restates a metalm rule; named files exist | index.md#repo-layout | `CLAUDE.md` "Repo notes", moved from `AGENTS.md`: Response Style restates metalm's writing rules, the domain vocabulary belongs in a docstring, and it names `policies.py` (now `beliefs.py`) | adopted 2026-10-05 |
| Design lives in the code: area docs are package docstrings, one-module reasons sit in the module docstring | design.md#where-decisions-live | `authorlm/docs/*.md` (33 files, about 17K lines), `authorlm/README.md`, `specs/*.md`, `audiostation/docs/`, `authorlm/illustration-craft.md`, `authorlm/GEMINI.md` | adopted 2026-10-05 |
| No numbered decision log | design.md#where-decisions-live | `audiostation/docs/design-decisions.md` (§1–§7) | adopted 2026-10-05 |
| Plans, backlog and rejected options are issues or `(owner)` rules, never files | design.md#where-decisions-live | `authorlm/docs/design-backlog.md`, `authorlm/docs/design-record.md`, `.out-of-scope/legacy-data-fallbacks.md` | adopted 2026-10-05 |
| Owner rulings end with `(owner)`, carry no date, and leave no superseded text | design.md#where-decisions-live | No `(owner)` marker anywhere; dated "ruled …" / "ratified …" lines in code, docs and the skill (`authorlm/authorlm/cli.py:526`, `api.py:4195`, `loop.py:3`, `db.py:617`, `.claude/skills/authorlm/SKILL.md:325`) | adopted 2026-10-05 |
| Old decision references in code are replaced by the package name | design.md#moving-a-repo-to-docs-in-code-owner | About 500 `§N.N` citations into `authorlm/docs/` and `specs/`; `D-2`, `D-3`, `D4`, `D6`, `F-D2` in `api.py`, `filtering.py`, `llm.py` | adopted 2026-10-05 |
| Areas are packages whose docstrings carry Never imports, Decisions and Terms; the module map is an import contract | design.md#the-package-docstring | Flat package `authorlm/authorlm/` (84 modules, not under `src/`); 10 audiostation modules have no `//!` or JSDoc area doc; no `[tool.importlinter]`; import-contracts hook left out of `.pre-commit-config.yaml` | adopted 2026-10-05 |
| Terms are defined in the owning package docstring | design.md#the-package-docstring | `authorlm/docs/domain-vocabulary.md` | adopted 2026-10-05 |
| Generated docs list only areas | design.md#generated-docs | `docs/generated/index.md` also lists `authorlm/tests/` and `authorlm/tools/` modules as areas (they sit under `src = "authorlm"`) | adopted 2026-10-05 |
| Mermaid diagram for every lifecycle and every set of more than 3 interacting components | design.md#diagrams | No Mermaid in `authorlm/docs/`; state machines (margin threads, proposals, critique) are prose | adopted 2026-10-05 |
| CUJs are `cuj` markers on end-to-end tests; every core path has one | requirements.md#cujs-are-generated-from-the-code | `authorlm/tests/` (script-style, no pytest, no marker registered); `docs/generated/cujs.md` is empty | adopted 2026-10-05 |
| Settings in one Knob / Where / Value table | requirements.md#readability | `authorlm/config.toml` (knobs documented in comments) | adopted 2026-10-05 |
| Docs use none of the owner's banned words (Vocabulary in the owner's global `CLAUDE.md`; metalm-setup checks it with `grep -rniw 'refus\w*'`) | metalm-setup phase 6a | About 250 hits across `authorlm/docs/`, `authorlm/README.md`, `.claude/skills/authorlm/SKILL.md`, `specs/` | adopted 2026-10-05 |
| No user-specific details in tracked files | operations.md#served-apps | Home path at `authorlm/config.toml:225` and `authorlm/docs/editorllm-research.md:3`; port table and machine name at `.claude/skills/authorlm/SKILL.md:1517-1529` and `authorlm/docs/audiobook-review-design.md:381`; 5 matching lines in git history (rewrite is the owner's call) | adopted 2026-10-05 |
| Fail loudly; no swallowed exceptions or silent fallbacks | coding.md#failure | `authorlm/authorlm/api.py:131` (an unreadable `config.toml` warns and leaves the LLM off), `llm.py:262` | adopted 2026-10-05 |
| Strict config: an unknown section or key is an error naming it | coding.md#configuration-is-never-state | `authorlm/authorlm/api.py:135` | adopted 2026-10-05 |
| Config never moves by environment variable; test fakes are injected in-process | coding.md#where-env-lives | `authorlm/authorlm/paths.py:22` (`AUTHORLM_CONFIG`, `AUTHORLM_ENV`, `AUTHORLM_CRAFT`), `clients.py:776`, env pins at the top of each test file | adopted 2026-10-05 |
| Data objects are `@dataclass(frozen=True, slots=True)` | coding.md#data-objects | 21 `@dataclass` uses in `authorlm/authorlm/`, none with `slots=True` | adopted 2026-10-05 |
| Atomic writes: temp file plus rename | coding.md#idempotence-and-writes | `authorlm/authorlm/staging.py:190`, `manifest.py:176` (most of 73 `write_text` calls) | adopted 2026-10-05 |
| Only the adapter imports a vendor SDK | coding.md#external-services-and-outbound-actions | `authorlm/authorlm/export.py:317`, `usage.py:195`, `llm.py:1498`, `gdocs.py` | adopted 2026-10-05 |
| Retry only 429/529, bounded | coding.md#llm-calls | `authorlm/authorlm/llm.py:1153` retries every error | adopted 2026-10-05 |
| Prompts are versioned files `<role>_vN.md` with front matter; none inline | coding.md#llm-calls | `authorlm/authorlm/prompts/*.md` (14, unversioned); inline prompts per `prompt_registry.py:11` | adopted 2026-10-05 |
| Layout: `doctor` gate, `./<repo>.sh` launcher, Python 3.12, pytest and ruff in the dev extra | coding.md#layout | No `doctor` verb or launcher; `authorlm/pyproject.toml` `requires-python = ">=3.11"` | adopted 2026-10-05 |
| Zero-tolerance lint and format in pre-commit | coding.md#lint-format-types | ruff: 137 errors in 26 files; ruff-format: 100 files; both hooks left out of `.pre-commit-config.yaml` | adopted 2026-10-05 |
| Every POST passes the write check (`X-<App>` header, Origin equals Host, 64 KB cap) | coding.md#pages | `authorlm/authorlm/triage_server.py:48`, `workbench_server.py:45`, `audiobook_server.py:118` | adopted 2026-10-05 |
| Surfaces are thin over one API module, with a parity test across them | architecture.md#surfaces-and-stores | `authorlm/authorlm/cli.py:391` and `:1232` write the store; `api.py:3319` runs raw SQL; the parity test covers the MCP tool set only | adopted 2026-10-05 |
| `PRAGMA foreign_keys = ON`, `STRICT` tables, a view for every read across more than 3 tables | architecture.md#relational-design | `authorlm/authorlm/db.py:521` and its schema | adopted 2026-10-05 |
| Identity is a content hash; master data keeps a field-level change log | architecture.md#history-and-identity | `authorlm/authorlm/db.py:446` (random ids), `db.py:766` (update in place) | adopted 2026-10-05 |
| Toolchain: pytest, hypothesis, mutation testing; vitest for web pages | testing.md#toolchain | `authorlm/tests/` (scripts); `authorlm/web/*/` (no tests) | adopted 2026-10-05 |
| Key scrubbing in one shared setup; live tests only behind `AUTHORLM_LIVE=1` | testing.md#offline-by-default | Per-file env pins, absent in 8 files; `authorlm/tests/test_live_llm.py` has no live flag | adopted 2026-10-05 |
| Tests mirror the source; unit tests alongside integration | testing.md#where-tests-live | 22 test files for 84 modules; `gdocs.py` (4K lines) has no unit file | adopted 2026-10-05 |
| A test plan per area | testing.md#test-plans | Only `authorlm/docs/morph-test-plan.md` | adopted 2026-10-05 |
| `tailscale serve --https=<port>`, never the bare port | operations.md#tailscale-serve | `.claude/skills/authorlm/SKILL.md:1517`, `authorlm/authorlm/audiobook_server.py:10` and `:160`, `authorlm/docs/audiobook-review-design.md:116` | adopted 2026-10-05 |
| Served apps answer only this machine's Host (else 421); `doctor`, launch agent, plist and sandbox script | operations.md#served-apps | `authorlm/authorlm/audiobook_server.py:95`, `triage_server.py:43`, `workbench_server.py:40`; no `scripts/` | adopted 2026-10-05 |
| Agents do not start servers; pages are checked in real Chrome at desktop and 375 px | operations.md#launch-agents | `.claude/skills/authorlm/SKILL.md:604`, `:1479-1481` | adopted 2026-10-05 |
| Separate test and production credentials, with a confirmation phrase for paid calls | operations.md#credentials | `authorlm/tests/test_live_llm.py` | adopted 2026-10-05 |
| One object, one page | ui.md#one-object-one-page | Pronunciations are edited on both `authorlm/web/workbench/` and `authorlm/web/audiobook/` (two diverged `Workbench.tsx`) | adopted 2026-10-05 |
| Readable at every width | ui.md#readable-at-every-width | `authorlm/web/workbench/src/styles.css:5` (cards stretch to 980 px) | adopted 2026-10-05 |
| Findable: list cards say what is inside; no feature behind small text alone | ui.md#findable | `authorlm/web/triage-app/src/TriageApp.tsx:970`, `authorlm/web/audiobook/src/Workbench.tsx:458` | adopted 2026-10-05 |
| No unattended LLM spend without per-run and per-day caps | scheduling.md#scheduled-and-unattended-work | `authorlm/config.toml:219` and `authorlm/authorlm/budget.py:189` (`enforce` is off) | adopted 2026-10-05 |
| Scheduled work: written boundaries and spend cap; long verbs alert through `superlm-notify` | scheduling.md#scheduled-job-checklists | The daily-improvements cloud routine is defined outside the repo; no notify module | adopted 2026-10-05 |
| Install scripts: `--dry-run`, `--uninstall`, idempotent rerun, closing summary | install-scripts.md#rules | `authorlm/setup.py` (web-page build on install) | adopted 2026-10-05 |
