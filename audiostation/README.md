# audiostation

The AuthorLM audio studio: a Tauri v2 desktop app that turns the manifests
`authorlm audio export` writes into an ACX-ready audiobook with ElevenLabs
and ffmpeg. Design: `../authorlm/docs/audiobook-pipeline-design.md`.

AuthorLM owns the text, the casting and the structure. audiostation owns
the audio and its bookkeeping. The two never write the same file:

```
<manuscript>/_audio/
  audiobook.toml          the author's configuration (AuthorLM reads it)
  cast.md                 the author's cast table
  audiobook.json          AuthorLM writes — metadata, chapter order, credits, cover
  chapters/<stem>.json    AuthorLM writes — ordered sections with content ids
  state/<stem>.json       audiostation writes — id → audio files, request id, stitch
  audio/                  audiostation writes — <id>.<format>.mp3, <stem>.<format>.mp3
  silence/                audiostation writes — shared silence clips
  acx/                    audiostation writes — packaged deliverables
  _orphaned/              audio whose id no chapter references any more
```

The section id is a hash AuthorLM computes over the text, the resolved
voice parameters and the applicable pronunciation rules. Same id, same
audio; a changed paragraph, cast row or pronunciation is a new id, and
only that id needs generating.

## What it does

- **Open Audiobook Folder** reads `audiobook.json`, every chapter and its
  state, sweeps orphans, and watches the folder. When AuthorLM exports
  again the app reloads on its own and highlights what moved: a section
  whose text changed, whose parameters changed (and which), a new one,
  the ones removed, chapters added or removed, and book-level changes.
  A highlight clears when the new id has audio.
- **Generate** one section or **Generate All Remaining** for a chapter.
  Every request carries the neighbouring same-voice text for
  continuity, plus the neighbours' ElevenLabs request ids when they are
  under two hours old (the API's stitching window) and the model is not
  v3. The pronunciation dictionary locator from `audiobook.json` rides
  along. Text is never edited here — change it in the manuscript and
  export.
- **Stitch** a chapter with ffmpeg (two-pass EBU R128 loudness at
  −20 LUFS / −3 dBTP for the ACX qualities), play it, reveal it.
- **ACX Package**: the structural audit and the numbered, ID3-tagged
  package (opening credits, chapters, about the author, retail sample,
  closing credits, cover) under `_audio/acx/`.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Rust + Cargo | stable (1.77+) | `curl https://sh.rustup.rs -sSf \| sh` |
| Node.js | 18+ | nodejs.org |
| ffmpeg + ffprobe | any recent | `brew install ffmpeg` |
| Xcode Command Line Tools | latest | `xcode-select --install` |

## Development

```bash
npm install
npm run dev          # Tauri dev window with hot reload (npx tauri dev)
npm test             # vitest — the pure TypeScript helpers
cd src-tauri && cargo test    # Rust — loads the shared fixture
```

The Rust tests read `../../authorlm/tests/fixtures/audiobook/`, the same
folder the Python suite asserts the export reproduces. A field renamed on
one side fails the other in the same commit.

## Build

```bash
npm run build        # npx tauri build → src-tauri/target/release/bundle/macos/
```

## Settings

The gear icon holds one thing: the ElevenLabs API key (a current key
begins with `sk_`). It is stored under
`~/Library/Application Support/com.authorlm.audiostation/`. The model,
the voices, the quality and where audio goes all come from the audiobook
folder.

## Project structure

```
audiostation/
  src/                  TypeScript frontend
    main.ts             boot, view router, Rust event listeners
    book.ts             the book view: chapters, sections, ACX panel
    menu.ts             File menu: open / reload / reveal / close
    settings.ts         the API key
    types.ts            mirrors src-tauri/src/types.rs
    utils.ts            pure helpers (tested)
  src-tauri/src/
    lib.rs              Tauri setup and command registration
    types.rs            Book, Chapter, ChapterState, Master — the JSON contract
    book.rs             load, diff on reload, orphan sweep, state writes
    watcher.rs          folder watcher → reload → `book-changed` event
    commands.rs         generate, stitch, clear, audit, package, settings
    stitch.rs           ffmpeg: silence clips, loudness, concat, ID3-free helpers
  docs/design-decisions.md
```
