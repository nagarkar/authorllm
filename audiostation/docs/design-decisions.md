# audiostation — Design Decisions

The ratified design is `../../authorlm/docs/audiobook-pipeline-design.md`.
These notes cover only what is specific to the desktop app.

## 1. Where it lives

`audiostation/` is a sibling of the `authorlm/` Python package in the
authorllm repository, moved from `EditorLLM/desktop/` on 2026-09-04 with
its history. It is a self-contained Tauri project (own `package.json`
and `Cargo.toml`, no workspace with the parent). The one shared artifact
is the JSON fixture at `authorlm/tests/fixtures/audiobook/`, read by both
test suites.

## 2. Folder, not file; state, not manifest

The old app held one master manifest that it and the Google Apps Script
add-on both wrote into. That path is gone (no HTTP server, no clipboard
import, no v1/v2 loaders, no in-app text or voice editing). The app opens
a folder AuthorLM wrote and writes only `state/<stem>.json` and the audio
files. Done means: the section id has a state entry whose file exists.
There is no dirty flag; the id changes when anything that shapes the
audio changes.

## 3. Reload and highlights

A `notify` watcher on `audiobook.json` and `chapters/` debounces bursts
(400 ms) and reloads. The reload pairs old and new sections per chapter:
same id → unchanged; same folded text and a different id → parameters
changed (the card names which); different text at the aligned position
→ text changed; unpaired → new; gone → listed once in the chapter. Flags
live in memory and clear when the id has generated audio. Book-level
differences (order, credits, cover, dictionary version, cast, quality)
show in a bar under the toolbar.

**Since the last take, not since the last load (2026-09-04).** The
author's ask: see which paragraphs a pronunciation change dirtied, and
why, "so the reason matches up with my memory of what I did". Two
changes. (1) The card names each pronunciation rule that moved —
"Basilides: buh-SIL-ih-deez → basillydeez", "added Prohairesis →
pro-HY-ruh-sis", "removed Abraxas" — instead of the word
"pronunciation". (2) Every generation records what its take was made
from (`madeFrom` on the state entry: text, voice and settings, rules),
and on ANY load a section without audio is paired to the most recent
take with the same folded text and flagged from that snapshot, with the
take's time ("changed since Sep 4, 22:13: …"). So the flag appears cold
— an export while the app was closed, a relaunch — and it outranks what
a reload can see, because the state saw every take and the reload only
the previous file. The reload summary counts only what that reload
found moved. The orphan sweep keeps a replaced id's state entry while a
live paragraph with the same text still awaits its take: that entry is
the memory the badge reads from, and it goes when the paragraph is
generated. Takes recorded before this date carry no snapshot and pair
by id only.

## 4. Continuity

`previous_text` / `next_text` always; `previous_request_ids` /
`next_request_ids` when the neighbours' stored `generatedAt` is under
two hours old and the model is not `eleven_v3`. The `request-id`
response header is stored per section. The same-voice "blocked until
prior generated" rule of the old app is gone: text conditioning makes
out-of-order regeneration acceptable, and Generate All still runs in
order.

## 5. Settings

Only the ElevenLabs API key, in the app's `tauri-plugin-store` file. The
model, the voices, the quality, the output location — all come from the
folder, so there is nothing else to configure and nothing to drift.

## 6. Mac only initially

Unchanged from the first iteration: `platforms: ["macOS"]`, no Windows
code signing yet. The Rust code is platform-agnostic.

## 7. An equal door beside AuthorLM's generation (2026-09-25)

Generation, retake and stitch now also exist as `authorlm audio` verbs,
with a served review page the author reaches from the phone
(`../../authorlm/docs/audiobook-review-design.md`). The app is not
retired: the author's ruling was "I don't want it to go away completely
until we trust the new UI". Both programs write `state/<stem>.json` in
this app's schema and the same `audio/<id>.<quality>.mp3` files, so a
take made by either is done for both.

Two consequences here. (1) The watcher also watches `state/` — the
exclusion in §3 assumed this app was the only writer; a reload repaints
and never generates, so watching our own writes costs a repaint per take
and nothing else. (2) The rule: do not press Generate here on a chapter
the server is rendering. The server serialises its own renders on one
worker; this app has no lock, and the same-minute race is the one case
the id-done check cannot catch.

The quality switch and Clear Lower Quality stay in this app but the book
has one quality now (`[tts].quality = "mp3_44100_192"`); the switch is
only ever set to it.
