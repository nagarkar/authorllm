# Audio voice configuration tests

Issue #173 and the owner rule in `authorlm/audio.py` govern these checks.
All fixtures are synthetic or checked-in copies; the tests use fake vendor
clients and require no paid calls or owner manuscript changes.

| Journey | Coverage | Expected result |
|---|---|---|
| Initialize audio configuration | `test_audio_voice_config.py` | The seed explicitly declares text, headings and credits as narrator; initialization fills missing files without changing existing bytes and refuses when both exist. |
| Export with missing or malformed configuration | `test_audio_voice_config.py`, `test_audio.py` | Errors name the field; missing file names `audio init`; unrelated malformed settings still report their own errors. |
| Change a book or chapter voice | `test_audio_voice_config.py` | Explicit roles resolve to complete speech parameters; chapter overrides affect only the exact file and never inherit from a parent. |
| Switch a passage speaker | `test_audio_voice_config.py`, `test_audio.py` | Plain tags persist until another tag or heading; headings reset following text to the file's role. |
| Reject invalid declarations | `test_audio_voice_config.py` | Unknown roles and missing targets refuse export by name. |
| Migrate a TOC voice | `test_audio_voice_config.py`, shared audiobook fixture | Legacy TOC voices name the configuration migration; moved fixture declarations produce unchanged golden JSON and section IDs. |
| Consume the resolved manifest | `test_audio.py`, audiostation Rust and TypeScript suites | Generation and both review surfaces retain the existing schema and resolved voice behavior. |

Run from `authorlm/`: `python3 tests/test_audio_voice_config.py` and
`python3 tests/test_audio.py`. Build the three web pages with `npm run build`
in each `web/{workbench,audiobook,triage-app}` directory first. From the repo
root run `metalm-gendocs --check`; regenerate with `metalm-gendocs` after
changing a documented rule. Full-suite results belong with the change's
test report, including any pre-existing failures.

## Verification record — 2026-10-06

The baseline had 22 Python suites; adding the voice configuration suite
makes 23, including its 11 tests. The independent voice suite failed before implementation
with 20 assertion failures and 3 errors across 8 tests; the primary error
was `unknown key text.voice`. Two added partial-initialization tests then
failed before the recovery change because init refused either existing
file; all 11 voice tests pass after implementation. `test_audio.py` reports
`All 218 checks passed.` after migration. Existing audio checks keep their non-voice
validation and golden expectations. The generation and review-page freshness setups now perform an
actual export before checking freshness because the migrated source
fixtures are newer than their unchanged golden manifests.

The baseline's pre-existing failures were `test_critique.py` and
`test_e2e.py` (subprocess exit -11), and stale generated documentation.
Rust had 18 passing tests and TypeScript had 17. Live LLM and ElevenLabs
vendor behavior remain untested here because this change only selects
roles before the existing vendor boundary; those suites require separate
explicit spending authorization.

Final full run: 23 Python suites ran; only `tests/test_critique.py` and
`tests/test_e2e.py` failed with their baseline subprocess exit -11. There
were no new failures. The audio suite passed all 218 checks and the voice
configuration suite passed all 11 tests. Rust passed 18 tests; TypeScript
passed 17. All three frontend builds succeeded. The built wheel contains
`authorlm/audiobook-defaults.toml`; generated-documentation freshness and
`git diff --check` passed. An independent review found no outstanding issue
after the partial-initialization recovery was added.
