# Audio voice configuration tests

Use temporary manuscripts and fake vendor clients; no owner data or paid calls.
The shared contract is in `authorlm/audio.py`; issue #173 supplies the requirements.

| Journey / feature | Automated coverage |
|---|---|
| Initialize, configure roles, override a chapter, switch a passage | `tests/test_audio_voice_config.py` |
| Export validation, TOC migration, stable manifest and section IDs | `tests/test_audio_voice_config.py`, `tests/test_audio.py` |
| Consume the manifest in generation and review surfaces | Python audio suites; audiostation Rust and TypeScript suites |

How to test today (2026-10-06):

- In `authorlm/`, build each `web/{workbench,audiobook,triage-app}` with `npm run build`.
- Run `python3 tests/test_audio_voice_config.py` (11 tests, `OK`) and `python3 tests/test_audio.py` (`All 218 checks passed.`).
- Suite size (2026-10-06): 23 offline Python modules; known gaps are subprocess exit -11 in `test_critique.py` and `test_e2e.py`.
- In `audiostation/`, run `npm test` (17 tests); in `audiostation/src-tauri/`, run `cargo test` (18 tests).
- At the repo root, run `metalm-gendocs --check` and `git diff --check` (exit 0).

Untested: live LLM and ElevenLabs behavior require separate spending approval;
interactive page listening and live manuscript migration remain manual checks.
