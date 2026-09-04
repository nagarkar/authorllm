# EditorLLM Desktop

A Tauri v2 desktop application for editing and generating audio from manifests
produced by the EditorLLM Google Docs add-on.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Rust + Cargo | stable (1.77+) | `curl https://sh.rustup.rs -sSf \| sh` |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Tauri CLI | v2 | `cargo install tauri-cli --version "^2"` |
| Xcode Command Line Tools | latest | `xcode-select --install` |

## Development

```bash
# From the desktop/ directory:

# 1. Install JS dependencies
npm install

# 2. Start the Tauri dev server (opens a native window with hot-reload)
npm run dev
# or equivalently:
cargo tauri dev
```

The Vite dev server starts on `http://localhost:5173` and the Tauri shell wraps
it in a native macOS window.

## Build (release)

```bash
npm run build
# or:
cargo tauri build
```

The signed `.app` bundle is written to `src-tauri/target/release/bundle/macos/`.

## Project structure

```
desktop/
  src/                  TypeScript frontend
    main.ts             App entry point + view router
    manifest.ts         Manifest editor view
    settings.ts         Settings view
    types.ts            Shared TS types (mirrors Rust structs)
  src-tauri/
    src/
      lib.rs            Tauri app setup
      main.rs           Binary entry point
      http_server.rs    Axum HTTP server (port 3847)
      commands.rs       Tauri IPC commands
      types.rs          Rust serde types (mirrors TS types)
    tauri.conf.json     Tauri configuration
    Cargo.toml          Rust dependencies
    capabilities/
      default.json      macOS capability grants
  index.html            App shell
  styles.css            Global styles
  package.json          JS dependencies + scripts
  tsconfig.json         TypeScript config
  vite.config.ts        Vite build config
  docs/
    design-decisions.md Architecture notes
```

## Receiving manifests from the GAS add-on

The app listens for `POST http://127.0.0.1:3847/manifest`. The GAS add-on can
check liveness first:

```bash
# Check the app is running
curl http://127.0.0.1:3847/status
# → {"running":true,"version":"0.1.0"}

# Send a manifest
curl -X POST http://127.0.0.1:3847/manifest \
  -H "Content-Type: application/json" \
  -d @path/to/manifest.json
```

## Voice stitching constraint

A speech section can only be generated if all prior sections with the **same
voice** have already been generated (and are not dirty). Blocked sections show
a red status dot and a disabled Generate button with a tooltip explaining why.

See `docs/design-decisions.md` for the full rationale.

## ElevenLabs integration (not yet implemented)

`commands.rs` contains stubs for:
- `generate_section` — generate audio for one section
- `generate_all_remaining` — generate all pending sections in order
- `stitch_audio` — concatenate all generated sections into a single file

Each stub logs a TODO message and returns an error. Look for `// --- STUB ---`
comments to find the implementation entry points.

## Settings

Open Settings via the gear icon (top-right). Settings are stored in
`~/Library/Application Support/com.editorllm.desktop/`.

- **ElevenLabs API Key** — required for generation
- **Output Directory** — where generated `.mp3` files are saved
- **Default TTS Model** — `eleven_multilingual_v2` | `eleven_turbo_v2_5` | `eleven_flash_v2_5`
