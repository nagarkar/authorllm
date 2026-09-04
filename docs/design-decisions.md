# EditorLLM Desktop — Design Decisions

## 1. Monorepo structure

`desktop/` lives as a subfolder of the `EditorLLM` Git repository. It has its
own `package.json` and `Cargo.toml` and is **not** wired into an npm workspace
or Cargo workspace with the parent repo. This keeps the boundary explicit:

- The parent repo is GAS/Node; it has no knowledge of Rust or Tauri.
- `desktop/` is a fully self-contained Tauri project that can be opened
  independently in VS Code or run with `cargo tauri dev` from within the folder.

Revisit if CI or dependency conflicts arise — at that point, a Cargo workspace
and/or npm workspaces may be warranted.

## 2. GAS → desktop handoff: POST to localhost

The GAS add-on sends an `AudioManifest` JSON payload via:

```
POST http://127.0.0.1:3847/manifest
Content-Type: application/json
```

Before sending, GAS calls `GET /status` to confirm the desktop app is running.
If `/status` times out or returns an error, GAS falls back to copying the
manifest JSON to the clipboard so the user can paste it manually.

**Why not a custom URL scheme (`editorllm://`)?**  
URL scheme registration requires OS-level installer steps (plist entries on Mac,
registry keys on Windows) and can prompt security dialogs. A local HTTP server
is simpler, works immediately without installation steps, and is easily testable
with `curl`.

## 3. Voice stitching constraint

ElevenLabs' Projects API is designed for sequential generation of sections per
voice. To preserve audio consistency (prosody, pacing), the desktop app enforces:

> A speech section with voice X can only be generated if **all prior speech
> sections with the same voice X** have already been successfully generated
> (have an `elevenLabsRequestId`) **and are not dirty**.

This constraint is enforced in both the frontend (`manifest.ts:
computeBlockedSections`) and the backend (`commands.rs: get_blocked_sections`).

**Dirty state:** Editing a section's text after generation sets `isDirty = true`
and clears the section's "done" status. Subsequent same-voice sections are then
blocked until the dirty section is regenerated.

**Rationale:** Without this constraint a user could reorder or re-record
individual sections and lose the prosodic continuity that ElevenLabs' context
window provides across a project.

## 4. Mac only initially

The initial target is macOS only (`"platforms": ["macOS"]` in
`capabilities/default.json`, `minimumSystemVersion: "10.15"` in bundle config).

Reasons:
- The primary users of EditorLLM are on Mac.
- WKWebView (Mac) handles H.264 natively, which is important for future video
  preview features.
- Avoids Windows code-signing complexity in the first iteration.

Windows/Linux can be added later — Tauri v2 supports them; the Rust code is
already platform-agnostic.

## 5. Settings storage

Settings (ElevenLabs API key, output directory, default TTS model) are stored
via `tauri-plugin-store` in the app's config directory:

- **Mac:** `~/Library/Application Support/com.editorllm.desktop/`

The API key is stored as a plain string in the Tauri store file. This is
acceptable for a single-user desktop tool where the file is protected by OS
file permissions. If stronger protection is needed in a future version,
`tauri-plugin-stronghold` (encrypted store) or the macOS Keychain via
`tauri-plugin-keychain` are the upgrade path.

## 6. No custom URL scheme

The desktop app does **not** register a custom URL scheme (`editorllm://`).

Custom URL schemes require:
1. A `CFBundleURLTypes` entry in the app's `Info.plist` (Mac).
2. The app to be installed (not just run from the built binary during
   development).
3. Careful handling of OS security prompts when another app triggers the scheme.

The local HTTP server approach (`127.0.0.1:3847`) avoids all of this and is
simpler to develop, test, and debug. `curl -X POST http://127.0.0.1:3847/manifest
-d @manifest.json` is a sufficient test harness.
