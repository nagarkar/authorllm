# Audiobook review and generation from the phone — design

Ratified 2026-09-25 after a grilling session (2026-09-23 to 09-25).
Amends `audiobook-pipeline-design.md` §10, §11 and §16: generation moves
from audiostation into AuthorLM, and a served page becomes the author's
working surface. Everything else in that document stands, above all §6
(the section id) and §8 (continuity).

## 0. What this is

The author's ask, verbatim from the dictation that opened the session:

> Right now, the only way to trigger audio generation is using the desktop
> application … I need to make sure that I can do that on my phone or I
> can do that in the chat interface. … we need to provide some kind of a
> way to trigger the generation of audio for the next round … with simple
> local … text to speech interaction to verify that the text is okay and
> should be processed using the professional online API … making sure
> that we don't waste tokens or money on the generation.

Three products:

1. **A free preview stage.** Every paragraph the export produces is
   read by macOS `say` before a cent is spent, so the author hears the
   exact text ElevenLabs would receive, respellings included.
2. **One surface that works on the phone and the Mac**, served by
   AuthorLM on the Mac and reached from the phone over Tailscale. Taps
   execute immediately; there is no queue of decisions and no
   acknowledgement machinery.
3. **Generation in Python.** `audio generate`, `retake`, `stitch`,
   `preview`, `status` and `serve` are AuthorLM verbs. audiostation
   stays as an equal second door until the author retires it.

## 1. Rulings (in the order they were made)

1. **Python owns generation.** "Python is good." Python already had
   the ElevenLabs client, the ids and the state reader; the Rust
   generate path was two hundred lines with nothing Rust-specific.
2. **The road is a page, not chat verbs.** The author pointed at
   gamelm's phone review page. First ruled as an Artifact with the `db`
   capability and a scheduled sync (the gamelm shape); **reversed in
   ruling 12**.
3. **One page per chapter, with a dropdown to switch chapters.**
4. **One quality per book, set high.** "It's probably ok to use the one
   quality for the book (which would have to be a very good quality),
   as long as we have the Mac say option for tts implemented and the
   preview is available." Quality becomes `mp3_44100_192`. The old
   app's quality switch and Clear Lower Quality go.
5. **Previews carry no verdict.** "This is a preview and if I don't
   like something I'll need to fix the text." One voice, "a polished
   British male voice": Daniel.
6. **The Doc gate is reconcile-then-export.** `gdocs.reconcile` (the
   session-start three-way compare) runs before every export; a
   conflicted file blocks its chapter. An approval carries the section
   id, and an id that has left the export is stale and never rendered.
7. **Takes carry no verdict either**, "other than whether I like it or
   do not like it": Retake on a paragraph, Stitch on a chapter. Notes
   on retakes were proposed and dropped.
8. **The paragraph is the unit.** "I will need to render specific
   paragraphs and so we should be rendering one paragraph at a time
   with the option to render multiple paragraphs." Already the design
   (§6): a section is one paragraph; a dictionary fix moves only the
   ids of paragraphs that carry the word.
9. **Addressing.** "I'm unlikely to ever write in a chat interface that
   I want to generate a 'chapter X paragraph 3'." The page's tap carries
   the id. In chat the author names a paragraph by its words and the
   session resolves it with `audio status <chapter> --find <words>`.
10. **Equal doors with three guards.** "I don't want it to go away
    completely until we trust the new UI." "I don't want to generate
    the audio twice when there is no change." Both writers share
    `state/`; see §5.
11. **Cadence.** Free steps (reconcile, export, previews) refresh on
    their own; paid renders only on the author's gesture. With the
    served page this is a background job in the server, not a cron.
12. **Served page over Tailscale, not an Artifact.** "I would rather
    that we simplify the state management and if tailscale allows us to
    do that, then it's worth building it in." It does. The Artifact
    road needed a decisions database, per-decision acknowledgements, a
    sync lock with heartbeat, cue sheets and 15 MB splits — all of it
    only because a published page cannot reach the Mac. A page served
    by the Mac needs none of it.
13. **YAGNI cuts.** The ACX audit and package stay in Rust until
    audiostation retires; no new MCP tools (the session runs the CLI);
    no per-decision notes. Kept after challenge: preview rate follows
    the cast row's speed (one flag); the whole book is previewed on the
    first run ("I don't mind a one time tax of an hour").

## 2. Why not the Artifact road

A published Artifact runs in a sandbox whose content security policy
blocks every network call except the artifact's own database and
comments — on the phone and on the Mac alike; publishing is what makes
the page unable to reach localhost. So a tap could only be recorded, and
a process on the Mac had to read the record later. That separation in
time is the whole reason gamelm has D37 (decisions), D42
(acknowledgements) and D43 (the lock). Audio adds bytes on top: the
artifact caps (15 MB per file, 64 MB per version, 255 files) forced
per-chapter concatenation with cue sheets and a 32 kbps transcode.

A page served by AuthorLM has none of those constraints. The trade is
that the Mac must be awake with the server running, and the phone must
be on the tailnet. The author accepted that trade for simplicity.

## 3. The server and the page

`authorlm audio serve -m <manuscript> [--port 8792] [--no-open]` is the
audiobook twin of `authorlm workbench`: a dependency-free
`http.server` on a loopback port, one compiled single-file React page
(`web/audiobook/` → `authorlm/audiobook_dist/index.html`), JSON over
`POST /api/audiobook` dispatched to `audiobook.dispatch(method, params)`.
Audio files are served by `GET /audio/<stem>/<id>` (take) and
`GET /preview/<id>` from the manuscript's `_audio/` folder, range
requests honoured so the phone can seek.

**Reaching it from the phone.** The server binds 127.0.0.1 and stays
there. Tailscale exposes it: `tailscale serve --bg 8792` publishes it as
`https://<mac>.<tailnet>.ts.net` to devices on the tailnet only, with a
certificate Tailscale manages. Nothing in AuthorLM knows about
Tailscale; the recipe lives in the SKILL. Installing Tailscale on the
Mac and the phone is the author's act.

**One worker.** Every act that writes (`refresh`, `generate`, `retake`,
`stitch`) is a job on a single queue drained by one thread. Jobs run in
order; the page polls `status` and paints progress per paragraph. This
is the Python side of guard 10.3 for free: two taps on the same id
become two jobs, and the second finds the take present and does
nothing.

**The page**, chapter by chapter:

- Header: book title, chapter dropdown (every chapter; the current one
  selected), `n/N generated`, characters remaining on the account,
  and the Doc state after the last refresh (`in sync`, `pulled`,
  `conflict: <file>`).
- **Refresh from Doc**: enqueues reconcile → export → previews for any
  new id → the page reloads. Also runs on server start and whenever a
  chapter is opened whose export is older than its sources.
- One card per paragraph: ordinal, heading or speech badge, the spoken
  text, cast key and voice name, the pronunciations applied, and the
  reason line when the id moved since a take was made ("changed since
  Sep 4, 22:13: Basilides: buh-SIL-ih-deez → basillydeez"). Two
  players: **Preview** (Daniel) and **Take** (ElevenLabs) when it
  exists. One button: **Generate** (no take) or **Retake** (take
  present), each showing the character count it would spend.
- **Continuous play** over previews or over takes: the page walks the
  chapter from the card it was started on, highlighting the card it is
  reading, so a Retake is one tap without hunting.
- Chapter bar: **Generate remaining** with its total cost, **Stitch**
  (enabled when every paragraph has a take), the stitched file's player
  and duration when it exists, and "stale" when a take is newer than
  the stitch.
- A job strip: what the worker is doing now and what is queued.

Dropped from the old app: settings, open/reload/reveal/close, the
quality switch, Clear Lower Quality, the "book changed" and "removed
since reload" bars, the ACX panel. Text is never edited here (§10.2 of
the pipeline design stands).

## 4. Verbs

Existing verbs (`init`, `export`, `check`, `voices`, `audition`, `cast
set`, `dictionary push`, `say`, `workbench`) are unchanged. The
`audiostation` launcher stays while the app does. New:

| Verb | Does | Cost |
|---|---|---|
| `audio preview <chapter> [-p 12,14-16] [--all]` | Renders Daniel previews for every paragraph that lacks one (keyed by id) into `_audio/preview/<id>.mp3`. `--all` for every chapter. | free |
| `audio generate <chapter> -p 12,14-16 \| --remaining [--dry-run]` | Renders the named paragraphs, or every one without a take. Prints the character count before and after. Never renders an id that has a take. | paid |
| `audio retake <chapter> -p 12` | Re-renders a paragraph that has a take and replaces it. | paid |
| `audio stitch <chapter>` | ffmpeg concat with the silence clips, two-pass EBU R128 to −20 LUFS / −3 dBTP, one `<stem>.<quality>.mp3`; records duration and loudness in state. Ported from `stitch.rs`. | free |
| `audio status [chapter] [--find <words>]` | The board (§7). `--find` lists paragraphs whose text contains the words, with ordinal and first words, for the session to resolve a chat request. | free |
| `audio serve` | §3. | — |

Rules across all verbs: a chapter is named by stem; a paragraph by its
ordinal in the chapter (`-p`), ids accepted too. A paid verb prints its
cost and then runs — a CLI call is the author's word, so there is no
confirmation prompt. Every verb prints one line per fact, since its
output lands in a chat context.

The session's protocol for a chat request ("regenerate the Basilides
paragraph in becker"): `audio status becker --find Basilides`; if one
match, run the verb; if several, list them by first words and ask.

## 5. State and the three guards

`state/<stem>.json` keeps the schema in `audiostation/src-tauri/src/types.rs`
exactly — `sections{id → audioFiles{quality → path}, requestId,
generatedAt, madeFrom}`, `stitched`, `durationSecs`, `loudnessLufs`,
`truePeakDbtp`. Python writes the same fields so audiostation reads a
Python take as its own and vice versa. The shared fixture under
`tests/fixtures/audiobook/` gains a `state/` folder that both suites
load; a field renamed on one side fails the other.

"Done" is unchanged: the id has a state entry whose file exists at the
book's quality. Continuity (§8 of the pipeline design) is unchanged:
neighbour text always, neighbour request ids within two hours and not
on v3. Python computes the reason line the way `book.rs::flags_from_state`
does: a section without a take is paired to the most recent take with
the same folded text, and the badge names each rule that moved.

The three guards, for two writers on one state:

1. **audiostation's watcher also watches `state/`.** Today it watches
   only `audiobook.json` and `chapters/` so as not to reload on its own
   writes; with a second writer that reload is how the app learns of a
   take it did not make. A reload repaints and never generates. About
   ten lines in `watcher.rs`.
2. **Python re-reads the chapter's state from disk immediately before
   every render**, and `generate` skips a done id. Only `retake`
   re-renders a done id.
3. **The rule**: do not press Generate in audiostation on a chapter
   while the server is rendering that chapter. The server's single
   worker serialises its own side; the app has no lock. Reloads cannot
   break this, because reloads do not generate.

No new fields in state. Retake notes were dropped, so a retake is a
replaced file and a new `generatedAt`.

## 6. Previews

- Text: the export's section text with each applicable respelling
  substituted inline (`substitute_alias`, as the workbench does), so the
  preview also checks the pronunciation table. `say` cannot read an
  ElevenLabs dictionary.
- Voice and rate from a new `[preview]` table in `audiobook.toml`,
  seeded by `audio init` and added by hand to existing books:

  ```toml
  [preview]
  voice = "Daniel"     # a macOS voice; say -v '?' lists them
  rate = 175           # words per minute, multiplied by the cast row's speed
  ```

  "Daniel (Enhanced)" is a free Apple download under System Settings →
  Accessibility → Spoken Content and reads noticeably better; the
  author installs it if wanted, the name in the table stays `Daniel`.
- Render: `say -v Daniel -r <rate> -o <tmp>.aiff <text>`, then
  `ffmpeg -i <tmp>.aiff -ac 1 -b:a 48k _audio/preview/<id>.mp3`.
  About 80 KB a paragraph. Keyed by id; a changed paragraph gets a new
  preview and the old one is deleted on the next sweep. Nothing is kept
  because nothing cost anything.
- Whole book on the first `audio preview --all` (~9 hours of speech,
  an hour of machine time); the server previews new ids as part of
  Refresh.

## 7. Example rows

`POST /api/audiobook` — a Generate tap:

```json
{ "method": "generate", "params": { "stem": "becker", "ids": ["f8c494533e989c5ae66688000bb97f45"] } }
→ { "ok": true, "result": { "job": 41, "characters": 1204, "queued": 1 } }
```

`status` for the page (abridged):

```json
{ "chapter": { "stem": "becker", "title": "Becker", "quality": "mp3_44100_192",
               "doc": "in sync", "exportedAt": "2026-09-26T03:02:11Z",
               "stitched": null, "generated": 12, "total": 38 },
  "sections": [
    { "id": "f8c4…", "ordinal": 14, "kind": "speech", "text": "…", "cast": "narrator",
      "voiceName": "Antoni", "pronunciations": [{"term": "Basilides", "say": "ba-SIL-i-deez"}],
      "preview": "/preview/f8c4….mp3", "take": null,
      "changedSince": { "at": "2026-09-04T22:13:00Z",
                        "reasons": ["Basilides: buh-SIL-ih-deez → ba-SIL-i-deez"] } } ],
  "jobs": { "running": { "id": 41, "kind": "generate", "stem": "becker", "done": 0, "of": 1 },
            "queued": [] },
  "account": { "remaining": 2318406 } }
```

`audio status becker`, as it prints:

```
becker  Doc in sync · exported 2026-09-26 03:02 · 38 paragraphs
  38 previewed · 12 generated at mp3_44100_192 · 26 remaining (31,204 chars)
  2 moved since their take (¶ 9 text; ¶ 14 Basilides: buh-SIL-ih-deez → ba-SIL-i-deez)
  not stitched
```

`audio generate becker -p 14`, as it prints:

```
becker ¶ 14 "After centuries of silent torment…"  1,204 characters
rendered f8c494533e989c5ae66688000bb97f45 in 6.1 s · request fcvtTrKI…
spent 1,204 · 2,317,202 remaining
```

`audiobook.toml` changes for SMSTTD, by hand: `quality =
"mp3_44100_192"` and the `[preview]` table. The two chapter1 paragraphs
at 128 render again at 192; their 128 files become orphans and are
swept.

## 8. Build order

1. **Generation core in Python.** `audio generate`, `retake`, `stitch`,
   `status` (with `--find`); the state writer against the Rust schema;
   guard 2; the state fixture shared with the Rust tests; the reason
   line derivation. Rust: guard 1 (watcher). Verified on chapter1 at
   192 against a live key.
2. **Previews.** `[preview]` in the config, `audio preview`, the alias
   substitution, the sweep of previews whose id is gone. First
   whole-book run.
3. **The server and the page.** `audio serve`, the worker queue,
   `web/audiobook/` compiled to `audiobook_dist/`, every control in §3,
   continuous play, range-served audio. Verified in the in-app browser
   pane, then on the phone over Tailscale.
4. **The SKILL.** Every verb's invocation, the `--find` protocol for
   chat requests, the `tailscale serve` recipe, the equal-doors rule.

Slices 1 and 2 are independent. Slice 3 needs both.

## 9. Tests

- `tests/test_audio.py` grows: state round-trip against the fixture;
  `generate` skips a done id and renders a moved one; `retake`
  replaces; `stitch` on the fixture chapter with silence clips; the
  reason line for a rule change, a text change and a cast change;
  `--find` resolution; preview text carries the substituted alias.
- `cargo test` loads the same `state/` fixture.
- The server's dispatch is tested the way `test_triage_app.py` tests
  the triage transport: methods in, JSON out, no sockets.
- Live ElevenLabs calls stay out of the suite; the author's budget for
  live checks is the one already set (≤ 2,000 characters a round).

## 10. Deliberately not done

- Porting the ACX audit and package to Python — Rust keeps them until
  audiostation retires.
- New MCP tools for any of this.
- Notes on retakes; keeping rejected takes.
- An Artifact-published page, cue sheets, 15 MB splits, a decisions
  store, acknowledgements, a sync lock, a scheduled task.
- Exposing the server on the LAN without Tailscale.
- Retiring audiostation. It is an equal door until the author says
  otherwise; its Generate path, ACX panel and highlights stay.

## 12. Implementation status (2026-09-25)

Built the same day the design was ratified, in the §8 order, all
uncommitted with the rest of the working tree:

- **Slice 1.** `authorlm/generation.py` (state in the Rust schema,
  guard 2, `generate`, `retake`, `stitch` ported from `stitch.rs` with
  the FNV stitch key bit-identical, `status` with `--find`, the reason
  line as `book.rs::flags_from_state`); CLI actions `generate`, `retake`,
  `stitch`, `status`; `ElevenLabs.tts` takes the neighbour request ids.
  Rust: the watcher watches `state/`, `ReloadSummary.takes_changed`
  counts the other writer's takes, a state-only burst that changed
  nothing is dropped, otherwise the window gets `state-changed` and
  repaints. Fixture: `state/kindness.json` remembers a take of the last
  paragraph at another speed with a rule since removed, and both suites
  assert the same flag (Python 206 checks, Rust 18 tests). Verified live:
  chapter1 at `mp3_44100_192`, 612 characters, stitched two-pass.
- **Slice 2.** `[preview]` in the config and the seed; `audio preview`
  on four workers; the whole SMSTTD book rendered once.
- **Slice 3.** `authorlm/audiobook.py` (the session, one worker, the
  jobs, `audio_file_for`), `authorlm/audiobook_server.py` (range-served
  audio, `POST /api/audiobook`), `web/audiobook/` → `audiobook_dist/`,
  `audio serve`. Verified in the in-app browser at desktop and phone
  widths on the live folder.
- **Slice 4.** The SKILL's audiobook section rewritten: every verb, the
  `--find` protocol, the equal-doors rule, the Tailscale recipe.

Deviations from §3, each deliberate:

- **Generate remaining and Retake ask once**, inline, with the cost.
  The design said one tap; a chapter-level render can be tens of
  thousands of characters and a retake replaces a take, so both get a
  second tap. A single paragraph's Generate is one tap.
- ~~**A chapter is not auto-refreshed on open.**~~ Reversed 2026-09-26
  on the author's objection to the banner's button ("why not just pull
  the data from the UI?"): the server checks the sources against the
  export every fifteen seconds and runs export + previews itself when
  they moved; the banner reports "re-exporting" and clears. Ruling 11
  as written. The band's Refresh chip remains for forcing it.
- **Taps are never disabled while a job runs.** The queue serialises
  them; disabling would have made a twenty-minute preview job block the
  author from queueing a render behind it.
- `tailscale serve --bg 8792` was run on the author's word from the
  phone the same evening; the port table across the repos (AuthorLM
  443, supplylm 8443, tradelm 9443 reserved) is in the SKILL.

**2026-09-26.** Two changes on the author's rulings. (1) **The stale
banner:** `generation.export_staleness` compares every manuscript file,
`audiobook.toml` and `cast.md` against the export's time on each status
poll, and the band shows "Changed since the export: <files>" with a
Refresh button; `audio status` prints STALE with the same list. Every
export touches `audiobook.json` so a re-save that changed nothing clears
on the next Refresh. (2) **The page never touches the Doc.** "Gdoc
edits are more tricky as is reconciliation. We can keep that in chat."
Refresh is export + previews; reconcile runs at session start or on
`doc pull` in chat, writes the pulled tab to disk, and the banner sees
it like any local edit. Ruling 6's gate is unchanged: what the page
renders is what is on disk, and reconcile is what puts the Doc there.

**2026-09-26, later.** The pronunciation workbench is folded into the
page as a mode ("merge this into the app we built for audiobook
creation so it's all in one place") — the "authorstation" question of
the pipeline design's §11 is answered for these two. `web/audiobook/`
carries a copy of the workbench component (given an `initialTerm`
prop and the vocingo styling), the server answers `/api/workbench`,
and a card's respelling chips open the word. The standalone
`authorlm workbench` verb and its page remain but are not the door.
The page was restyled after gamelm's vocingo card UI the same day.

## 11. Open questions

- Whether the served page should also host the pronunciation workbench
  and the Triage App ("authorstation", filed 2026-09-04). This design
  makes the three pages siblings on the same server pattern; folding
  them is a separate grilling.
- The `say` rate mapping (175 × speed) is a first guess; tune once
  after the first whole-chapter listen.
