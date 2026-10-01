"""The audiobook export (docs/audiobook-pipeline-design.md), hermetic.

The shared fixture `tests/fixtures/audiobook/` is one complete book
folder — sources, `_audio/audiobook.toml`, `_audio/cast.md`, and the
JSON the export writes from them. This suite asserts the export
reproduces that JSON from the sources; audiostation's Rust tests read
the same folder and assert they can load it. One commit changes both.

Run: python3 tests/test_audio.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones. Without this a checkout's config.toml (llm enabled, keys in
# .env) is picked up by every test process and the suite makes live,
# billed model calls — and asserts against whatever they return.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
# And pin client provenance OFF. The suites run INSIDE a Claude Code
# Bash call, so CLAUDE_CODE_SESSION_ID is in their own environment and
# the claude-code adapter would stamp the developer's live chat into
# every fixture row — tests passing for the wrong reason. Same failure
# mode as a leaked config, so it gets the same treatment: pin it.
os.environ["AUTHORLM_CLIENT"] = "none"
os.environ.pop("ELEVENLABS_API_KEY", None)


def _assert_offline() -> None:
    import os as _os

    from authorlm import paths as _paths

    assert not _paths.config_path().exists(), (
        f"test isolation broken: reading the real config at "
        f"{_paths.config_path()}")
    assert _paths.load_env() == [], "test isolation broken: .env was loaded"
    assert not _os.environ.get("ELEVENLABS_API_KEY"), (
        "test isolation broken: an ElevenLabs key is in the environment")


import contextlib
import io
import json
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, audio, filtering, filters, passes  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.export import strip_voice_lines, VOICE_LINE  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "audiobook"
PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def run_cli(*argv: str) -> tuple[str, int]:
    out = io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            cli_main(list(argv))
        except SystemExit as err:
            if isinstance(err.code, int) or err.code is None:
                code = err.code or 0
            else:
                # `raise SystemExit("message")` prints the message at exit,
                # outside the redirect — capture it here.
                out.write(str(err.code))
                code = 1
    return out.getvalue(), code


def _png(path: Path, size: int = 2400, color_type: int = 2) -> None:
    w = h = size
    px = 3 if color_type == 2 else 1
    raw = b"".join(b"\x00" + bytes([120] * px) * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return (struct.pack(">I", len(d)) + t + d
                + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.write_bytes(png)


def _jpeg(path: Path, w: int, h: int, comps: int = 3) -> None:
    """A JPEG header with one SOF0 marker — enough for image_info."""
    sof = struct.pack(">BBHBHHB", 0xFF, 0xC0, 8 + 3 * comps, 8, h, w, comps)
    sof += b"".join(bytes([i + 1, 0x11, 0]) for i in range(comps))
    path.write_bytes(b"\xff\xd8" + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00"
                     b"\x01\x00\x01\x00\x00" + sof + b"\xff\xd9")


def _workspace(book: Path) -> tuple[Path, str]:
    ws = Path(tempfile.mkdtemp(prefix="authorlm-audio-ws-"))
    out, code = run_cli("-w", str(ws), "init", "--name", "Scratch", "--path",
                        str(book), "--author", "Chinmay Nagarkar",
                        "--copyright-owner", "Chinmay Nagarkar",
                        "--no-extract")
    assert code == 0, out
    out, code = run_cli("-w", str(ws), "manuscript", "set", "--narrator",
                        "Adam Stone", "--publisher", "Chinmay Nagarkar",
                        "--copyright-year", "2026", "--language", "en")
    assert code == 0, out
    return ws, out


def _book_copy() -> Path:
    root = Path(tempfile.mkdtemp(prefix="authorlm-audio-book-"))
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    return root


def _manuscript(ws: Path) -> dict:
    db = api.open_db(str(ws))
    return api.get_manuscript(db, "Scratch"), db


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items()
            if k not in ("generatedAt", "pronunciationDictionary")}


# --------------------------------------------------------------- units

def check_text_rules() -> None:
    cfg = audio.load_config(Path("/nonexistent"))
    text, warnings = audio.speech_text(
        "**Bold** and *italic* and __under__ with a [link](http://x), "
        "a note[^F1], `code`, and $E=mc^2$.", cfg)
    check("bold, italic, links, footnote refs, code strip to plain text",
          text == "Bold and italic and under with a link, a note, code, "
                  "and E=mc^2.", text)
    check("inline math warns once per span", warnings == [
        "inline math $E=mc^2$ read as written"], str(warnings))
    strict = json.loads(json.dumps(cfg))
    strict["text"]["inline_math"] = "refuse"
    try:
        audio.speech_text("a $x$ b", strict)
        check("inline_math = refuse refuses", False)
    except audio.AudioError as err:
        check("inline_math = refuse refuses by name", "$x$" in str(err))
    check("normalize keeps curly quotes and dashes",
          audio.normalize("“a” — b") == "“a” — b")
    check("normalize folds NBSP and collapses runs",
          audio.normalize("a b   c\n\nd") == "a b c d")
    blocks = audio.blocks_of(
        "# **Title**\n\n[Voice: x]\npara one\n\n---\n\n- a\n- b\n\n"
        "> quoted\n\n[^F1]: note\n\n[Illustration: a field]\n\n"
        "![](_illustrations/x.jpg)\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    kinds = [b["kind"] for b in blocks]
    check("blocks classify heading, voice, paragraph, rule, list, quote, "
          "footnote, illustration, image, table",
          kinds == ["heading", "voice", "paragraph", "rule", "list", "quote",
                    "footnote", "illustration", "illustration", "table"],
          str(kinds))
    check("a tag directly above its paragraph is peeled off as its own block",
          blocks[1]["text"] == "[Voice: x]" and blocks[2]["text"] == "para one"
          and blocks[2]["line"] == 4, str(blocks[1:3]))
    units = passes.paragraphs_of("p1\n\n[Voice: a]\n\np2\n\n[Voice: b]")
    check("a unit is the paragraph plus the tag line above it",
          units == ["p1", "[Voice: a]\n\np2", "[Voice: b]"], str(units))
    check("reader outputs drop voice lines",
          strip_voice_lines("a\n[Voice: x | speed=0.9]\nb") == "a\nb")
    check("VOICE_LINE is case-insensitive and full-line",
          bool(VOICE_LINE.match("[voice: herdsman]"))
          and not VOICE_LINE.match("text [Voice: x]"))


def check_voice_tags() -> None:
    tag = audio.parse_voice_tag("[Voice: herdsman | stability=0.6 | speed=0.92]")
    check("tag parses key and overrides",
          tag == {"key": "herdsman", "overrides": {"stability": 0.6,
                                                   "speed": 0.92}}, str(tag))
    check("a non-tag line is None", audio.parse_voice_tag("Harken.") is None)
    for bad, why in (("[Voice: The Dead]", "not a cast key"),
                     ("[Voice: x | pitch=2]", "override is name=value"),
                     ("[Voice: x | speed=9]", "outside"),
                     ("[Voice: x | speed=fast]", "must be a number")):
        try:
            audio.parse_voice_tag(bad)
            check(f"{bad} refused", False)
        except audio.AudioError as err:
            check(f"{bad} refused: {why}", why in str(err), str(err))


def check_cast() -> None:
    rows, warnings = audio.parse_cast(audio.SEED_CAST)
    check("the seed cast has one narrator row with no voice id",
          [r["key"] for r in rows] == ["narrator"] and rows[0]["voice_id"] == "")
    text = audio.set_cast_row(audio.SEED_CAST, {
        "key": "herdsman", "voice": "Adam", "voice_id": "v1", "model": "",
        "stability": "0.65", "similarity": "0.8", "speed": "0.97",
        "note": "x"})
    rows, _w = audio.parse_cast(text)
    check("set_cast_row appends after the last row",
          [r["key"] for r in rows] == ["narrator", "herdsman"])
    again = audio.set_cast_row(text, {"key": "herdsman", "voice": "Julian",
                                      "voice_id": "v2"})
    rows2, _w = audio.parse_cast(again)
    check("set_cast_row replaces the row with the same key in place",
          [r["key"] for r in rows2] == ["narrator", "herdsman"]
          and rows2[1]["voice_id"] == "v2" and again.count("\n") == text.count("\n"))
    check("prose before and after the table is untouched",
          again.startswith("# **Cast**") and "the rest of this file is yours"
          in again)
    try:
        audio.set_cast_row(text, {"key": "Bad Key", "voice_id": "v"})
        check("a bad key is refused", False)
    except audio.AudioError as err:
        check("a bad key is refused by name", "Bad Key" in str(err))
    shifted, warnings = audio.parse_cast(
        "| Key | Voice |\n| --- | --- |\n| a\\|b | x | y | z | 1 | 1 | 1 | n |\n")
    check("a row carrying a pipe is skipped and named",
          shifted == [] and warnings and "'|'" in warnings[0], str(warnings))


def check_config() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-audio-cfg-"))
    (root / audio.AUDIO_DIR).mkdir()
    cfg_path = root / audio.AUDIO_DIR / audio.CONFIG_FILENAME
    cfg_path.write_text(audio.DEFAULT_CONFIG_TEXT, encoding="utf-8")
    cfg = audio.load_config(root)
    check("the seeded toml parses to exactly the defaults",
          cfg == audio._CONFIG_DEFAULTS, json.dumps(cfg))
    for bad, why in (("[text]\nfootnote = \"drop\"\n", "unknown key text.footnote"),
                     ("[text]\nfootnotes = \"read\"\n", "one of drop, inline, end"),
                     ("[tts]\nquality = \"wav\"\n", "one of"),
                     ("[headings]\ngap_ms = { h7 = 1 }\n", "unknown key headings.gap_ms.h7"),
                     ("[voices]\nx = 1\n", "unknown table"),
                     ("[text]\nparagraph_gap_ms = \"700\"\n", "must be an integer")):
        cfg_path.write_text(bad, encoding="utf-8")
        try:
            audio.load_config(root)
            check(f"config refuses {bad.strip()!r}", False)
        except audio.AudioError as err:
            check(f"config refuses {bad.strip()!r} by name", why in str(err),
                  str(err))
    cfg_path.write_text("[text]\nparagraph_gap_ms = 900\n", encoding="utf-8")
    cfg = audio.load_config(root)
    check("a partial toml takes the defaults for the rest",
          cfg["text"]["paragraph_gap_ms"] == 900
          and cfg["headings"]["gap_ms"]["h1"] == 3250)
    shutil.rmtree(root, ignore_errors=True)


def check_credits_and_cover() -> None:
    meta = {"title": "T", "subtitle": "", "author": "A", "narrator": "N",
            "publisher": "P", "copyright_year": 2026, "copyright_owner": "O",
            "language": "en"}
    text = audio.render_credit(audio._CONFIG_DEFAULTS["credits"]["opening"],
                               meta, "opening")
    check("an empty subtitle vanishes with its punctuation",
          text == "T. Written by A. Narrated by N.", text)
    try:
        audio.render_credit("{title} by {author} for {publisher}",
                            {**meta, "publisher": ""}, "closing")
        check("a missing required placeholder is an error", False)
    except audio.AudioError as err:
        check("a missing required placeholder names the field and the verb",
              "publisher" in str(err) and "manuscript set --publisher" in str(err))
    try:
        audio.render_credit("{titel}", meta, "opening")
        check("an unknown placeholder is an error", False)
    except audio.AudioError as err:
        check("an unknown placeholder is named", "{titel}" in str(err))
    tmp = Path(tempfile.mkdtemp(prefix="authorlm-audio-img-"))
    _png(tmp / "sq.png", 2400)
    _png(tmp / "gray.png", 2400, color_type=0)
    _jpeg(tmp / "wide.jpg", 3000, 2000)
    _jpeg(tmp / "cmyk.jpg", 2400, 2400, comps=4)
    check("PNG header read: 2400x2400 rgb",
          audio.image_info(tmp / "sq.png") == {"width": 2400, "height": 2400,
                                               "format": "png", "color": "rgb"})
    check("JPEG SOF read: 3000x2000 rgb",
          audio.image_info(tmp / "wide.jpg") == {"width": 3000, "height": 2000,
                                                 "format": "jpg", "color": "rgb"})
    cfg = json.loads(json.dumps(audio._CONFIG_DEFAULTS))
    for name, expect in (("sq.png", []), ("gray.png", ["not RGB"]),
                         ("wide.jpg", ["not square", "at least 2400"]),
                         ("cmyk.jpg", ["cmyk, not RGB"])):
        cfg["cover"]["path"] = name
        _info, problems = audio.cover_problems(tmp, cfg)
        check(f"cover {name}: {expect or 'no problems'}",
              all(any(e in p for p in problems) for e in expect)
              and (bool(problems) == bool(expect)), str(problems))
    cfg["cover"]["path"] = ""
    check("no cover path is a named problem",
          "cover.path is empty" in audio.cover_problems(tmp, cfg)[1][0])
    shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------ fixture

def check_fixture_and_hash() -> None:
    book = _book_copy()
    ws, _out = _workspace(book)
    manuscript, db = _manuscript(ws)
    try:
        built = audio.build(manuscript)
        expected_book = json.loads((FIXTURE / "_audio" / "audiobook.json")
                                   .read_text(encoding="utf-8"))
        check("build reproduces the fixture's audiobook.json",
              _strip(built["book"]) == _strip(expected_book),
              json.dumps(_strip(built["book"]), indent=1)[:2000])
        for path in sorted((FIXTURE / "_audio" / "chapters").glob("*.json")):
            expected = json.loads(path.read_text(encoding="utf-8"))
            check(f"build reproduces {path.name}",
                  built["chapters"][path.stem] == expected,
                  json.dumps(built["chapters"][path.stem], indent=1)[:1500])
        sermons = built["chapters"]["sermons"]
        speech = [s for s in sermons["sections"] if s["type"] == "speech"]
        check("the essay default voice from toc.toml casts the headings and "
              "paragraphs", speech[0]["cast"] == "herdsman"
              and speech[0]["kind"] == "heading")
        dead = [s for s in speech if s["cast"] == "the_dead"]
        check("a [Voice:] tag casts the paragraph below it and no other",
              len(dead) == 1 and dead[0]["text"].startswith("“O Stranger"))
        over = [s for s in speech if s["speed"] == 0.92]
        check("an override applies to that paragraph only",
              len(over) == 1 and over[0]["cast"] == "herdsman")
        after = speech[speech.index(over[0]) + 1]
        check("a heading resets the cast and the override",
              after["kind"] == "heading" and after["speed"] == 0.97)
        check("footnote references and definitions are dropped",
              not any("citation" in s["text"] or "[^" in s["text"]
                      for s in speech))
        check("the applicable pronunciation rules ride each section",
              speech[2]["pronunciations"] == [{"term": "Basilides",
                                              "say": "buh-SIL-ih-deez"}])
        kindness = built["chapters"]["kindness"]
        texts = [s["text"] for s in kindness["sections"] if s["type"] == "speech"]
        check("[Omit: audio] drops and [Only: audio] keeps; display math drops",
              "This paragraph is for the ear only." in texts
              and not any("page only" in t or "int_0" in t for t in texts))
        check("about.md routes to the about-author slot, not the chapters",
              built["book"]["aboutAuthor"] == "chapters/about.json"
              and "about" not in [c["stem"] for c in built["book"]["chapters"]])
        check("title.md is not a chapter and feeds the credits",
              "title" not in built["chapters"]
              and next(s for s in built["chapters"]["_opening-credits"]["sections"]
                       if s["type"] == "speech")["text"]
              .startswith("Seven More Sermons To The Dead. Septem Plus"))
        check("the retail sample resolves to the first N paragraphs under "
              "the heading", built["book"]["retailSample"] == [
                  s["id"] for s in speech if s["kind"] == "paragraph"][1:4])
        silences = [s["durationMs"] for s in sermons["sections"]
                    if s["type"] == "silence"]
        check("silences: head gap 800 (not the H1's 3250), H2 2250 after a "
              "paragraph, 700 between paragraphs, rule 1500 (max, not sum), "
              "tail gap 2000",
              silences == [800, 2250, 2250, 700, 700, 700, 2250, 1500, 700, 2000],
              str(silences))

        # --- the five consequences of the hash (design §6) ---
        ids = {s["text"]: s["id"] for s in speech}
        (book / "sermons.md").write_text(
            (book / "sermons.md").read_text(encoding="utf-8")
            .replace("A quoted line.", "A quoted line, changed."),
            encoding="utf-8")
        rebuilt = audio.build(manuscript)
        new = {s["text"]: s["id"] for s in rebuilt["chapters"]["sermons"]["sections"]
               if s["type"] == "speech"}
        moved = {t for t in ids if t in new and new[t] != ids[t]}
        check("editing one paragraph moves one id and no other",
              moved == set() and "A quoted line, changed." in new
              and "A quoted line." not in new, str(moved))
        cast_path = book / "_audio" / "cast.md"
        cast_text = cast_path.read_text(encoding="utf-8")
        cast_path.write_text(cast_text.replace("| 0.65 | 0.8 | 0.97 |",
                                               "| 0.7 | 0.8 | 0.97 |"),
                             encoding="utf-8")
        rebuilt = audio.build(manuscript)
        new = {s["text"]: s["id"] for s in rebuilt["chapters"]["sermons"]["sections"]
               if s["type"] == "speech"}
        moved = {t for t in ids if t in new and new[t] != ids[t]}
        herdsman = {s["text"] for s in speech if s["cast"] == "herdsman"
                    and s["text"] in new}
        check("changing a cast row moves every id that role speaks and only "
              "those", moved == herdsman, f"{len(moved)} vs {len(herdsman)}")
        cast_path.write_text(cast_text, encoding="utf-8")
        pron_path = book / "pronunciations.md"
        pron_text = pron_path.read_text(encoding="utf-8")
        pron_path.write_text(pron_text.replace("| NOSS-tik |", "| NOS-tick |"),
                             encoding="utf-8")
        rebuilt = audio.build(manuscript)
        new = {s["text"]: s["id"] for s in rebuilt["chapters"]["sermons"]["sections"]
               if s["type"] == "speech"}
        moved = {t for t in ids if t in new and new[t] != ids[t]}
        check("changing one pronunciation moves only the paragraphs that "
              "contain the term",
              moved == {t for t in ids if "Gnostic" in t} and len(moved) == 1,
              str(moved))
        pron_path.write_text(pron_text, encoding="utf-8")
        (book / "sermons.md").write_text(
            (book / "sermons.md").read_text(encoding="utf-8")
            .replace("A quoted line, changed.", "A quoted line."),
            encoding="utf-8")
        again = audio.build(manuscript)
        check("the same sources give the same ids",
              {s["id"] for c in again["chapters"].values() for s in c["sections"]}
              == {s["id"] for c in built["chapters"].values() for s in c["sections"]})

        # --- export writes only what changed ---
        shutil.rmtree(book / "_audio" / "chapters")
        (book / "_audio" / "audiobook.json").unlink()
        first = audio.export(db, manuscript, resolve_dictionary=False)
        check("a fresh export writes every file",
              len(first["written"]) == 6 and not first["unchanged"],
              str(first))
        second = audio.export(db, manuscript, resolve_dictionary=False)
        check("a repeated export writes nothing", second["written"] == []
              and len(second["unchanged"]) == 6, str(second))
        (book / "kindness.md").write_text(
            (book / "kindness.md").read_text(encoding="utf-8")
            + "\nOne more paragraph.\n", encoding="utf-8")
        third = audio.export(db, manuscript, resolve_dictionary=False)
        check("editing one essay rewrites its chapter file alone",
              third["written"] == ["_audio/chapters/kindness.json"],
              str(third["written"]))
        check("a dictionary name without a key is a warning, not a refusal",
              any("no ELEVENLABS_API_KEY" in w for w in
                  audio.export(db, manuscript)["warnings"]))

        # --- the readiness report ---
        report = audio.check(db, manuscript)
        check("check reads audiostation's state read-only",
              report["state"]["sermons"] == {"generated": 0, "total": 12,
                                             "stitched": False}
              or report["state"]["sermons"]["total"] == 12,
              str(report["state"]))
        check("check names the short retail sample and nothing else blocks",
              [p for p in report["problems"] if "retail sample" not in p] == []
              and any("retail sample" in p for p in report["problems"]),
              str(report["problems"]))
        (book / "about.md").unlink()
        (book / "toc.toml").write_text(
            (book / "toc.toml").read_text(encoding="utf-8")
            .split("[[chapter]]\nfile = \"about.md\"")[0], encoding="utf-8")
        report = audio.check(db, manuscript)
        check("check blocks on a missing about.md",
              any("about.md" in p for p in report["problems"]))
        api.update_manuscript_metadata(db, manuscript, narrator="")
        report = audio.check(db, manuscript)
        check("check blocks on missing metadata by name",
              any("narrator is not set" in p for p in report["problems"]))
        try:
            audio.build(manuscript)
            check("a missing narrator refuses the credits", False)
        except audio.AudioError as err:
            check("a missing narrator refuses the credits by name",
                  "narrator" in str(err))
    finally:
        shutil.rmtree(book, ignore_errors=True)
        shutil.rmtree(ws, ignore_errors=True)


def check_cli_and_refusals() -> None:
    book = _book_copy()
    ws, _out = _workspace(book)
    try:
        out, code = run_cli("-w", str(ws), "audio", "init")
        check("audio init refuses when the files exist", code == 1
              and "already exist" in out, out)
        out, code = run_cli("-w", str(ws), "audio", "cast", "set", "the_dead",
                            "--voice-id", "3MTvEr8xCMCC2mL9ujrI",
                            "--stability", "0.55")
        rows, _w = audio.parse_cast((book / "_audio" / "cast.md")
                                    .read_text(encoding="utf-8"))
        row = next(r for r in rows if r["key"] == "the_dead")
        check("audio cast set updates one field and keeps the rest",
              code == 0 and row["stability"] == "0.55"
              and row["note"] == "Weary; many as one", str(row))
        out, code = run_cli("-w", str(ws), "audio", "export", "--offline")
        check("audio export --offline writes and reports",
              code == 0 and "Wrote _audio/chapters/sermons.json" in out
              and "speech section(s)" in out, out)
        out, code = run_cli("-w", str(ws), "audio", "check")
        check("audio check exits 1 while a problem stands and shows state",
              code == 1 and "retail sample" in out and "sermons:" in out, out)
        (book / "sermons.md").write_text(
            (book / "sermons.md").read_text(encoding="utf-8")
            .replace("[Voice: the_dead]", "[Voice: ghost]"), encoding="utf-8")
        out, code = run_cli("-w", str(ws), "audio", "export", "--offline")
        check("an unknown cast key refuses the export by file and key",
              code == 1 and "sermons.md" in out and "'ghost'" in out, out)
        out, code = run_cli("-w", str(ws), "audio", "voices")
        check("a voice verb without a key names the variable",
              code == 1 and "ELEVENLABS_API_KEY" in out, out)
        out, code = run_cli("-w", str(ws), "manuscript", "set",
                            "--copyright-year", "26")
        check("copyright year must be four digits", code == 1
              and "four digits" in out, out)
        out, code = run_cli("-w", str(ws), "manuscript", "set",
                            "--language", "EN-us")
        check("language is normalized", code == 0 and "language: en-us" in out,
              out)
    finally:
        shutil.rmtree(book, ignore_errors=True)
        shutil.rmtree(ws, ignore_errors=True)


# ----------------------------------------------------------- ElevenLabs

class _Resp:
    def __init__(self, payload: bytes, headers: dict | None = None) -> None:
        self._payload = payload
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(log: list, routes: dict):
    def open_(req, timeout=0):
        body = json.loads(req.data.decode()) if req.data else None
        log.append((req.get_method(), req.full_url, body))
        for key, (payload, headers) in routes.items():
            if key in req.full_url and (not isinstance(payload, tuple)
                                        or payload[0] == req.get_method()):
                if isinstance(payload, tuple):
                    payload = payload[1]
                return _Resp(payload if isinstance(payload, bytes)
                             else json.dumps(payload).encode(), headers)
        raise AssertionError(f"unexpected {req.get_method()} {req.full_url}")
    return open_


def check_elevenlabs() -> None:
    log: list = []
    voice = {"voice_id": "v1", "model": "eleven_multilingual_v2",
             "stability": 0.6, "similarity": 0.75, "speed": 1.0}
    client = audio.ElevenLabs("k", opener=_opener(log, {
        "/text-to-speech/v1": (b"MP3", {"request-id": "req-1"}),
    }))
    audio_bytes, request_id = client.tts("hello", voice, "mp3_44100_64",
                                         previous_text="before",
                                         next_text="after",
                                         locators=[{"pronunciation_dictionary_id": "d",
                                                    "version_id": "v"}])
    method, url, body = log[-1]
    check("tts posts the voice settings, the neighbours and the locator",
          method == "POST" and url.endswith("output_format=mp3_44100_64")
          and body["previous_text"] == "before" and body["next_text"] == "after"
          and body["voice_settings"] == {"stability": 0.6,
                                         "similarity_boost": 0.75, "speed": 1.0}
          and body["pronunciation_dictionary_locators"][0]["version_id"] == "v",
          str(body))
    check("tts returns the bytes and the request-id header",
          audio_bytes == b"MP3" and request_id == "req-1")
    pls = ('<?xml version="1.0"?><lexicon xmlns="http://www.w3.org/2005/01/'
           'pronunciation-lexicon"><lexeme><grapheme>Gnostic</grapheme>'
           '<alias>noss tik</alias></lexeme><lexeme><grapheme>Abraxas'
           '</grapheme><phoneme>/əˈbræksəs/</phoneme></lexeme></lexicon>')
    rules = audio.parse_pls(pls)
    check("PLS parses alias and phoneme lexemes",
          rules == [{"string_to_replace": "Gnostic", "type": "alias",
                     "alias": "noss tik"},
                    {"string_to_replace": "Abraxas", "type": "phoneme",
                     "phoneme": "/əˈbræksəs/"}], str(rules))
    check("the alias is the respelling as written, whitespace-normalized",
          audio.alias_for(" uh-BRAK-suss ") == "uh-BRAK-suss"
          and audio.alias_for("Chitta  Darshana") == "Chitta Darshana")

    book = _book_copy()
    ws, _out = _workspace(book)
    manuscript, _db = _manuscript(ws)
    try:
        wanted = audio.desired_rules(manuscript, audio.load_config(book))
        check("every term is pushed as written and lowercased",
              "Gnostic" in wanted and "gnostic" in wanted
              and wanted["Gnostic"]["alias"] == "NOSS-tik", str(list(wanted)))
        log.clear()
        client = audio.ElevenLabs("k", opener=_opener(log, {
            "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": [
                {"id": "D1", "name": "Scratch pronunciations",
                 "latest_version_id": "V1"}]}, {}),
            "/download": (pls.replace("noss tik", "old").encode(), {}),   # Gnostic stale, Abraxas a phoneme
        }))
        plan = audio.dictionary_plan(manuscript, client)
        # Remote: Gnostic → "old" (a stale alias), Abraxas as a PHONEME
        # rule where the table wants an alias — both are changes; every
        # lowercase variant and the other terms are adds; nothing is
        # remote-only because both remote terms are still in the table.
        check("the plan classifies add and change",
              not plan["create"] and "gnostic" in
              [r["string_to_replace"] for r in plan["add"]]
              and [r["string_to_replace"] for r in plan["change"]]
              == ["Abraxas", "Gnostic"] and plan["remove"] == []
              and plan["unchanged"] == 0,
              json.dumps({k: v for k, v in plan.items() if k != "remote"})[:800])
        client2 = audio.ElevenLabs("k", opener=_opener(log, {
            "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": [
                {"id": "D1", "name": "Scratch pronunciations",
                 "latest_version_id": "V1"}]}, {}),
            "/download": (pls.replace("<phoneme>/əˈbræksəs/</phoneme>",
                                      "<alias>x</alias>")
                          .replace("Abraxas", "Zeus")
                          .replace("noss tik", "NOSS-tik").encode(), {}),
        }))
        plan2 = audio.dictionary_plan(manuscript, client2)
        check("a current remote rule is unchanged and a remote term missing "
              "from the table is remote-only",
              plan2["unchanged"] == 1 and plan2["remove"] == ["Zeus"]
              and plan2["change"] == [],
              json.dumps({k: v for k, v in plan2.items() if k != "remote"})[:600])
        lines = audio.describe_plan(plan)
        check("the plan reads as prose with one line per rule",
              lines[0].startswith("Dictionary 'Scratch pronunciations'")
              and any(l.startswith("  change  Gnostic") for l in lines), "\n".join(lines))
        check("a rule the table does not carry is listed for removal",
              any(l.startswith("  remove  Zeus") for l in audio.describe_plan(plan2)))
        log.clear()
        client = audio.ElevenLabs("k", opener=_opener(log, {
            "/remove-rules": ({"version_id": "V2"}, {}),
            "/add-rules": ({"version_id": "V3"}, {}),
        }))
        result = audio.dictionary_apply(plan, client)
        removed = [b for m, u, b in log if u.endswith("/remove-rules")]
        added = [b for m, u, b in log if u.endswith("/add-rules")]
        check("apply removes the changed readings before re-adding them",
              removed and removed[0]["rule_strings"] == ["Abraxas", "Gnostic"]
              and log[0][1].endswith("/remove-rules")
              and result["version_id"] == "V3" and result["changed"] == 2,
              str(log))
        removed2: list = []
        # Clear keeps the word listed without a reading and drops every
        # remote variant of its rule now; Remove drops the row too.
        clog: list = []
        cclient = audio.ElevenLabs("k", opener=_opener(clog, {
            "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": [
                {"id": "D1", "name": "Scratch pronunciations",
                 "latest_version_id": "V1"}]}, {}),
            "/download": (pls.replace("noss tik", "NOSS-tik").encode(), {}),
            "/remove-rules": ({"version_id": "V7"}, {}),
        }))
        cleared = audio.clear_say(manuscript, "Gnostic", cclient)
        rows_after, _w = audio.pron.parse((book / audio.pron.FILENAME).read_text(encoding="utf-8"))
        gone = [b["rule_strings"] for _m, u, b in clog if u.endswith("/remove-rules")]
        check("clear keeps the row, blanks the reading, and removes the remote rules",
              cleared["cleared"] and cleared["removed_rule"]
              and audio.pron.lookup(rows_after)["gnostic"]["say"] == ""
              and gone and set(gone[0]) >= {"Gnostic"}, str((cleared, gone)))
        try:
            audio.clear_say(manuscript, "Nobody", cclient)
            check("clearing a word not in the table is refused", False)
        except audio.AudioError as err:
            check("clearing a word not in the table is refused by name", "Nobody" in str(err))
        # Put the reading back: the checks below count every rule.
        audio.settle_say(manuscript, "Gnostic", "NOSS-tik")
        client3 = audio.ElevenLabs("k", opener=_opener(removed2, {
            "/remove-rules": ({"version_id": "V4"}, {}),
            "/add-rules": ({"version_id": "V5"}, {}),
        }))
        done2 = audio.dictionary_apply(plan2, client3)
        gone = [b["rule_strings"] for _m, u, b in removed2 if u.endswith("/remove-rules")]
        check("push mirrors the table: a rule the table does not carry is removed "
              "(the append-only rule of 2026-09-03 reversed 2026-09-26)",
              gone == [["Zeus"]] and done2["removed"] == 1, str(removed2))
        check("apply adds every missing rule in one call",
              added and {r["string_to_replace"] for r in added[0]["rules"]}
              >= {"gnostic"} and "Gnostic" in
              {r["string_to_replace"] for r in added[0]["rules"]}, str(added))
        log.clear()
        client = audio.ElevenLabs("k", opener=_opener(log, {
            "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": []}, {}),
            "/add-from-rules": ({"id": "D9", "version_id": "V9"}, {}),
        }))
        plan = audio.dictionary_plan(manuscript, client)
        result = audio.dictionary_apply(plan, client)
        check("a missing dictionary is created from every rule",
              plan["create"] and result["created"] and result["id"] == "D9"
              and log[-1][2]["name"] == "Scratch pronunciations"
              and len(log[-1][2]["rules"]) == len(wanted))
        log.clear()
        client = audio.ElevenLabs("k", opener=_opener(log, {
            "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": [
                {"id": "D1", "name": "Scratch pronunciations",
                 "latest_version_id": "V7"}]}, {}),
        }))
        built = audio.build(manuscript, dictionary={
            "name": "Scratch pronunciations", "id": "D1", "versionId": "V7"})
        check("the export carries the dictionary locator by name",
              built["book"]["pronunciationDictionary"]["versionId"] == "V7")
    finally:
        shutil.rmtree(book, ignore_errors=True)
        shutil.rmtree(ws, ignore_errors=True)


def check_say() -> None:
    book = _book_copy()
    ws, _out = _workspace(book)
    manuscript, _db = _manuscript(ws)
    try:
        check("sentence_around picks the sentence carrying the term",
              audio.sentence_around("Harken. Do ye recall Basilides? Mark well.",
                                    "basilides") == "Do ye recall Basilides?")
        check("substitute_alias replaces every whole-word occurrence, any case",
              audio.substitute_alias("Basilides and basilides, not Basilidesx",
                                     "Basilides", "buh sil ih deez")
              == "buh sil ih deez and buh sil ih deez, not Basilidesx")
        ctx = audio.say_contexts(manuscript, "Basilides")
        check("say_contexts finds the term in reading order with its voice",
              ctx and ctx[0]["file"] == "sermons.md" and ctx[0]["cast"] == "herdsman"
              and ctx[0]["voice"]["voice_id"] == "pNInz6obpgDQGcFmaJgB"
              and "Basilides" in ctx[0]["sentence"], str(ctx[:1]))
        ctx = audio.say_contexts(manuscript, "Gnostic")
        check("the [Voice:] override reaches the say voice",
              ctx and ctx[0]["voice"]["speed"] == 0.92, str(ctx[:1]))
        log: list = []
        client = audio.ElevenLabs("k", opener=_opener(log, {
            "/text-to-speech/": (b"MP3", {"request-id": "r"}),
        }))
        result = audio.say(manuscript, client, "Basilides",
                           says=["buh-SIL-ih-deez", "ba-SIL-i-deez"])
        bodies = [b for _m, _u, b in log]
        check("say renders one clip per respelling with the alias substituted "
              "inline, as written, and no locator",
              len(result["clips"]) == 2 and "buh-SIL-ih-deez" in bodies[0]["text"]
              and "ba-SIL-i-deez" in bodies[1]["text"]
              and "pronunciation_dictionary_locators" not in bodies[0]
              and "Basilides" not in bodies[0]["text"], str(bodies))
        check("say uses the section's own voice by default",
              log[0][1].startswith("https://api.elevenlabs.io/v1/text-to-speech/pNInz6obpgDQGcFmaJgB"),
              log[0][1])
        log.clear()
        audio.say(manuscript, client, "Basilides", cast_key="the_dead")
        check("--cast overrides the voice and the table's reading is the default",
              "3MTvEr8xCMCC2mL9ujrI" in log[0][1]
              and "buh-SIL-ih-deez" in log[0][2]["text"], str(log))
        log.clear()
        client2 = audio.ElevenLabs("k", opener=_opener(log, {
            "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": [
                {"id": "D1", "name": "Scratch pronunciations",
                 "latest_version_id": "V1"}]}, {}),
            "/text-to-speech/": (b"MP3", {}),
        }))
        result = audio.say(manuscript, client2, "Basilides", use_dictionary=True)
        tts = [b for _m, u, b in log if "/text-to-speech/" in u][0]
        check("--dictionary renders the untouched sentence with the locator",
              "Basilides" in tts["text"]
              and tts["pronunciation_dictionary_locators"][0]["version_id"] == "V1",
              str(tts))
        try:
            audio.say(manuscript, client, "Zeus", says=["zoos"])
            check("an unspoken term refuses", False)
        except audio.AudioError as err:
            check("an unspoken term refuses by name", "'Zeus' is not spoken" in str(err))
        done = audio.settle_say(manuscript, "Basilides", "ba-SIL-i-deez")
        text = (book / "pronunciations.md").read_text(encoding="utf-8")
        rows, _w = audio.pron.parse(text)
        row = audio.pron.lookup(rows)["basilides"]
        check("settle rewrites only the term's row, keeping its note",
              not done["added"] and row["say"] == "ba-SIL-i-deez"
              and row["note"].startswith("Greek proper name") and len(rows) == 7,
              str(row))
        done = audio.settle_say(manuscript, "Zeus", "zoos", note="a god")
        rows, _w = audio.pron.parse((book / "pronunciations.md").read_text(encoding="utf-8"))
        check("settle adds a row for a new term", done["added"] and len(rows) == 8)
    finally:
        shutil.rmtree(book, ignore_errors=True)
        shutil.rmtree(ws, ignore_errors=True)


def check_workbench() -> None:
    from authorlm import workbench

    book = _book_copy()
    ws, _out = _workspace(book)
    log: list = []
    workbench.CLIENT_FACTORY = lambda: audio.ElevenLabs("k", opener=_opener(log, {
        "/text-to-speech/": (b"MP3BYTES", {"request-id": "r"}),
        "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": [
            {"id": "D1", "name": "Scratch pronunciations", "latest_version_id": "V1"}]}, {}),
        "/download": (b"<lexicon></lexicon>", {}),
        "/add-rules": ({"version_id": "V2"}, {}),
    }))
    workbench._SESSIONS.clear()

    def call(method, **params):
        return workbench.dispatch(method, {"manuscript": "Scratch", **params},
                                  workspace=str(ws))
    try:
        state = call("state")
        check("state lists the table's rows, the dictionary name and the cast",
              [t["term"] for t in state["terms"]][:2] == ["Abraxas", "Basilides"]
              and state["dictionary"] == "Scratch pronunciations"
              and set(state["cast"]) == {"narrator", "herdsman", "the_dead"}, str(state)[:400])
        ctx = call("contexts", term="Basilides")
        check("contexts give one sentence per voice that says the word",
              [c["cast"] for c in ctx["contexts"]] == ["herdsman"]
              and ctx["say"] == "buh-SIL-ih-deez", str(ctx)[:300])
        clip = call("render", term="Basilides", say="ba-SIL-i-deez",
                    context=ctx["contexts"][0], mode="respelling")
        check("render substitutes the respelling as written in that voice and returns the audio",
              "ba-SIL-i-deez" in clip["spoken"] and clip["alias"] == "ba-SIL-i-deez"
              and clip["mode"] == "respelling"
              and "pNInz6obpgDQGcFmaJgB" in log[-1][1]
              and clip["audio_base64"] == "TVAzQllURVM=", str(clip)[:300])
        try:
            call("render", term="Basilides", say="", context=ctx["contexts"][0])
            check("render without a respelling refuses", False)
        except ValueError as err:
            check("render without a respelling refuses by name", "respelling" in str(err))
        try:
            call("render", term="Basilides", say="x", context=ctx["contexts"][0], mode="loud")
            check("an unknown render mode refuses", False)
        except ValueError as err:
            check("an unknown render mode refuses by name", "loud" in str(err))
        plain = call("render", term="Basilides", say="ignored", context=ctx["contexts"][0], mode="default")
        tts = [b for _m, u, b in log if "/text-to-speech/" in u][-1]
        check("render in default mode sends the untouched sentence with no locator and no alias",
              plain["spoken"] == ctx["contexts"][0]["sentence"] and plain["alias"] == ""
              and plain["mode"] == "default"
              and not tts.get("pronunciation_dictionary_locators"), str(tts)[:300])
        call("render", term="Basilides", say="", context=ctx["contexts"][0], mode="dictionary")
        tts = [b for _m, u, b in log if "/text-to-speech/" in u][-1]
        check("render via the pushed dictionary with an empty box sends the untouched "
              "sentence with the locator",
              "Basilides" in tts["text"]
              and tts["pronunciation_dictionary_locators"][0]["version_id"] == "V1", str(tts)[:300])
        both = call("render", term="Basilides", say="ba-SIL-i-deez",
                    context=ctx["contexts"][0], mode="dictionary")
        tts = [b for _m, u, b in log if "/text-to-speech/" in u][-1]
        check("with a respelling in the box, dictionary mode substitutes the word AND "
              "attaches the dictionary for the rest (2026-09-26)",
              "ba-SIL-i-deez" in tts["text"] and "Basilides" not in tts["text"]
              and tts["pronunciation_dictionary_locators"][0]["version_id"] == "V1"
              and both["say"] == "ba-SIL-i-deez" and both["mode"] == "dictionary", str(tts)[:300])
        short = call("render", term="Basilides", say="ba-SIL-i-deez",
                     context={**ctx["contexts"][0], "sentence": "  Basilides   spoke. "}, mode="respelling")
        check("render reads the edited sentence, whitespace-normalized, with the alias substituted",
              short["spoken"] == "ba-SIL-i-deez spoke." and short["sentence"] == "Basilides spoke."
              and short["chars"] == len("ba-SIL-i-deez spoke."), str(short)[:200])
        try:
            call("render", term="Basilides", say="x", context={**ctx["contexts"][0], "sentence": "Nobody spoke."})
            check("render with a sentence that lost the word refuses", False)
        except ValueError as err:
            check("render with a sentence that lost the word refuses by name", "Basilides" in str(err))
        again = call("contexts", term="Basilides")
        recs = again["clips"]
        keys = {(r["mode"], r["say"], r["sentence"]): r["key"] for r in recs}
        check("contexts lists every clip rendered for the word, keyed by what produced it, without audio",
              len(recs) == 5 and len(set(r["key"] for r in recs)) == 5
              and all("audio_base64" not in r for r in recs)
              and ("respelling", "ba-SIL-i-deez", "Basilides spoke.") in keys
              and ("default", "", ctx["contexts"][0]["sentence"]) in keys
              and ("dictionary", "", ctx["contexts"][0]["sentence"]) in keys
              and ("dictionary", "ba-SIL-i-deez", ctx["contexts"][0]["sentence"]) in keys, str(keys))
        k = keys[("respelling", "ba-SIL-i-deez", "Basilides spoke.")]
        one = call("clip", key=k)
        check("clip fetches one take by key with its audio",
              one["key"] == k and one["audio_base64"] == "TVAzQllURVM=" and one["cast"] == "herdsman", str(one)[:200])
        retake = call("render", term="Basilides", say=" ba-SIL-i-deez ",
                      context={**ctx["contexts"][0], "sentence": "Basilides spoke."}, mode="respelling")
        check("the same alias and sentence is the same key — a re-take replaces the take, never a sixth record",
              retake["key"] == k and len(call("contexts", term="Basilides")["clips"]) == 5, retake["key"])
        other = call("render", term="Basilides", say="BASS-il-ides",
                     context={**ctx["contexts"][0], "sentence": "Basilides spoke."}, mode="respelling")
        check("a different alias is a different key and a sixth record",
              other["key"] != k and len(call("contexts", term="Basilides")["clips"]) == 6)
        try:
            call("clip", key="nope")
            check("an unknown clip key refuses", False)
        except LookupError as err:
            check("an unknown clip key refuses by name", "nope" in str(err))
        check("the clip index lives beside the clips",
              (book / "_audio" / "auditions" / "say" / "workbench-clips.json").exists())
        saved = call("save", rows=[{"term": "Basilides", "say": "ba-SIL-i-deez", "note": ""},
                                   {"term": "Zeus", "say": "zoos", "note": "a god"}])
        rows, _w = audio.pron.parse((book / "pronunciations.md").read_text(encoding="utf-8"))
        by = audio.pron.lookup(rows)
        check("save writes the changed row and the new row into pronunciations.md",
              saved["count"] == 2 and by["basilides"]["say"] == "ba-SIL-i-deez"
              and by["zeus"]["say"] == "zoos" and len(rows) == 8, str(saved))
        check("state reflects the saved rows", "Zeus" in [t["term"] for t in call("state")["terms"]])
        by_term = {t["term"]: t for t in call("state")["terms"]}
        check("a row is tested only when a clip exists for its CURRENT reading",
              by_term["Basilides"]["tested"] is True        # ba-SIL-i-deez was rendered above
              and by_term["Zeus"]["tested"] is False        # saved, never rendered
              and by_term["Abraxas"]["tested"] is False, str({k: v["tested"] for k, v in by_term.items()}))
        blank = call("save", rows=[{"term": "Sophia", "say": "", "note": ""},
                                   {"term": "Basilides", "say": "", "note": ""}])
        rows, _w = audio.pron.parse((book / "pronunciations.md").read_text(encoding="utf-8"))
        by = audio.pron.lookup(rows)
        check("save lands a new word with no reading yet and never blanks an existing row",
              blank["count"] == 1 and blank["skipped"] == ["Basilides"]
              and by["sophia"]["say"] == "" and by["basilides"]["say"] == "ba-SIL-i-deez"
              and len(rows) == 9, str(blank))
        pushed = call("push")
        check("push applies the plan and reports the version",
              pushed["applied"] and pushed["version_id"] == "V2"
              and any("Zeus" in l or "zeus" in l for l in pushed["plan"]), str(pushed)[:400])
        s = call("suggest", term="Zeus")
        check("suggest without an LLM says so instead of guessing",
              s["say"] is None and "LLM" in s["reason"], str(s))
        check("an unspoken word has no contexts", call("contexts", term="Nobody")["contexts"] == [])
        try:
            call("dance")
            check("an unknown method refuses", False)
        except ValueError as err:
            check("an unknown method refuses by name", "dance" in str(err))
        # Remove: the author's verdict that the voice's default is better.
        # A fresh client whose dictionary carries Zeus — the row goes, and
        # so does exactly that one remote rule; a row with no remote rule
        # goes alone; a term that is not a row is refused by name.
        log2: list = []
        workbench.CLIENT_FACTORY = lambda: audio.ElevenLabs("k", opener=_opener(log2, {
            "/pronunciation-dictionaries?": ({"pronunciation_dictionaries": [
                {"id": "D1", "name": "Scratch pronunciations", "latest_version_id": "V2"}]}, {}),
            "/download": (b"<lexicon><lexeme><grapheme>Zeus</grapheme><alias>zoos</alias></lexeme></lexicon>", {}),
            "/remove-rules": ({"version_id": "V3"}, {}),
        }))
        workbench._SESSIONS.clear()
        gone = call("remove", term="Zeus")
        rows, _w = audio.pron.parse((book / "pronunciations.md").read_text(encoding="utf-8"))
        removal = [b for _m, u, b in log2 if "/remove-rules" in u]
        check("remove drops the row from pronunciations.md and that one rule from ElevenLabs",
              gone["removed_row"] and gone["removed_rule"] and gone["version_id"] == "V3"
              and "zeus" not in audio.pron.lookup(rows) and len(rows) == 8
              and removal and removal[-1]["rule_strings"] == ["Zeus"], str(gone) + str(removal))
        gone = call("remove", term="Abraxas")
        rows, _w = audio.pron.parse((book / "pronunciations.md").read_text(encoding="utf-8"))
        check("remove of a row with no remote rule drops the row alone and says so",
              gone["removed_row"] and not gone["removed_rule"] and gone["version_id"] is None
              and "abraxas" not in audio.pron.lookup(rows) and len(rows) == 7
              and len([b for _m, u, b in log2 if "/remove-rules" in u]) == 1, str(gone))
        check("state no longer lists the removed rows",
              not {"Zeus", "Abraxas"} & {t["term"] for t in call("state")["terms"]})
        try:
            call("remove", term="Nobody")
            check("remove of a term that is not a row refuses", False)
        except LookupError as err:
            check("remove of a term that is not a row refuses by name", "Nobody" in str(err))
        text = (book / "pronunciations.md").read_text(encoding="utf-8")
        check("remove rewrote no other line",
              "Basilides" in text and text.count("\n") >= 6 and "| Zeus" not in text)
        check("the served page is the built page",
              "Pronunciation Workbench" in workbench.app_html()[:2000]
              or "workbench" in workbench.app_html().lower())
        # The one road: `authorlm workbench -m Scratch` → GET / is the
        # page, POST /api/workbench is dispatch with the server's
        # manuscript filled in, and a refusal is a 400 with the reason.
        import http.server, threading, urllib.request, urllib.error, json as _json
        from authorlm import workbench_server
        server = http.server.HTTPServer(
            ("127.0.0.1", 0), workbench_server.make_handler(str(ws), "Scratch"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"

        def post(method, **params):
            req = urllib.request.Request(
                f"{base}/api/workbench",
                data=_json.dumps({"method": method, "params": params}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    return resp.status, _json.loads(resp.read())
            except urllib.error.HTTPError as err:
                return err.code, _json.loads(err.read())
        try:
            with urllib.request.urlopen(f"{base}/") as resp:
                page = resp.read().decode("utf-8")
            check("the local server serves the built page at /",
                  resp.status == 200 and "/api/workbench" in page)
            status, body = post("state")
            check("POST /api/workbench answers with the envelope and the server's manuscript",
                  status == 200 and body["ok"] and body["result"]["manuscript"] == "Scratch"
                  and "Basilides" in [t["term"] for t in body["result"]["terms"]], str(body)[:300])
            status, body = post("dance")
            check("a refused method is a 400 that names the reason",
                  status == 400 and body["ok"] is False and "dance" in body["error"], str(body))
        finally:
            server.shutdown()
            server.server_close()
    finally:
        workbench.CLIENT_FACTORY = None
        workbench._SESSIONS.clear()
        shutil.rmtree(book, ignore_errors=True)
        shutil.rmtree(ws, ignore_errors=True)


def check_audiostation_launch() -> None:
    book = _book_copy()
    ws, _out = _workspace(book)
    manuscript, _db = _manuscript(ws)
    fake = Path(tempfile.mkdtemp(prefix="authorlm-audiostation-"))
    try:
        try:
            audio.audiostation_launch(manuscript, env={}, root=fake)
            check("no app and no package.json refuses by name", False)
        except audio.AudioError as err:
            check("no app and no package.json refuses by name",
                  "no audiostation found" in str(err), str(err))
        (fake / "package.json").write_text("{}", encoding="utf-8")
        launch = audio.audiostation_launch(manuscript, env={}, root=fake)
        check("with only the source, the dev server is the fallback",
              launch["mode"] == "dev" and launch["argv"][:3] == ["npx", "tauri", "dev"]
              and launch["argv"][-1].endswith("_audio") and launch["cwd"] == str(fake),
              str(launch))
        binary = fake / "src-tauri" / "target" / "debug" / "audiostation"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"#!/bin/sh\n")
        launch = audio.audiostation_launch(manuscript, env={}, root=fake)
        check("a debug binary beats the dev server",
              launch["mode"] == "binary" and launch["argv"] == [str(binary), launch["dir"]],
              str(launch))
        app = fake / "src-tauri" / "target" / "debug" / "bundle" / "macos" / "audiostation.app"
        app.mkdir(parents=True)
        launch = audio.audiostation_launch(manuscript, env={}, root=fake)
        check("a .app bundle beats the binary and opens with --args",
              launch["mode"] == "bundle" and launch["argv"][:2] == ["open", "-a"]
              and launch["argv"][2] == str(app) and launch["argv"][3:] == ["--args", launch["dir"]],
              str(launch))
        override = fake / "elsewhere.app"
        override.mkdir()
        launch = audio.audiostation_launch(manuscript, env={audio.APP_ENV: str(override)},
                                           root=fake)
        check("AUDIOSTATION_APP overrides everything",
              launch["argv"][2] == str(override), str(launch))
        (book / "_audio" / "audiobook.json").unlink()
        try:
            audio.audiostation_launch(manuscript, env={}, root=fake)
            check("an unexported manuscript refuses", False)
        except audio.AudioError as err:
            check("an unexported manuscript refuses and names the verb",
                  "audio export" in str(err), str(err))
        out, code = run_cli("-w", str(ws), "audiostation", "--dry-run")
        check("the CLI's dry run names the missing export", code == 1
              and "audio export" in out, out)
    finally:
        shutil.rmtree(fake, ignore_errors=True)
        shutil.rmtree(book, ignore_errors=True)
        shutil.rmtree(ws, ignore_errors=True)


def check_casting_filter_block() -> None:
    meta, body = filters.parse_front_matter(
        "---\nclass = \"sequential\"\ncast = true\n---\n# Casting\nwho speaks")
    check("a filter may declare cast = true", meta["cast"] is True)
    try:
        filters.parse_front_matter("---\nclass = \"sequential\"\ncast = \"yes\"\n---\nx")
        check("cast must be a boolean", False)
    except filters.FilterError as err:
        check("cast must be a boolean, refused by name", "`cast` is a boolean"
              in str(err))
    block = filtering.cast_block(audio.SEED_CAST)
    check("the CAST block lists keys and notes, never voice ids",
          block == "- narrator — The default for every essay and heading"
          and "pNIn" not in block, block)
    check("an empty cast renders as an absence, not an error",
          "no cast rows yet" in filtering.cast_block(""))


def _mp3(path: Path, secs: float = 0.2) -> None:
    """A real, tiny mp3 — ffmpeg concat refuses fake bytes."""
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=44100", "-t",
                    str(secs), "-c:a", "libmp3lame", "-q:a", "9", str(path)],
                   check=True)


def check_generation_helpers() -> None:
    """Pure helpers that name moved takes, stitch files, and status cards.

    Exercised only indirectly through `check_generation`'s fixture board —
    these pins cover the edge cases that board never hits (empty diffs,
    missing ids, filename hygiene, truncation)."""
    from authorlm import generation as gen

    check("sanitise_filename keeps safe characters and collapses the rest",
          gen.sanitise_filename("Kindness Ch.1!") == "Kindness_Ch.1"
          and gen.sanitise_filename("a/b:c") == "a_b_c"
          and gen.sanitise_filename("___") == "untitled"
          and gen.sanitise_filename("") == "untitled"
          and gen.sanitise_filename("ok-name_1.mp3") == "ok-name_1.mp3")

    chapter = {"sections": [
        {"id": "h1", "type": "heading", "text": "Title"},
        {"id": "a" * 32, "type": "speech", "text": "First."},
        {"id": "silence", "type": "silence"},
        {"id": "b" * 32, "type": "speech", "text": "Second."},
        {"id": "c" * 32, "type": "speech", "text": "Third."},
    ]}
    check("ordinal_of counts speech sections only, 1-based",
          gen.ordinal_of(chapter, "a" * 32) == 1
          and gen.ordinal_of(chapter, "b" * 32) == 2
          and gen.ordinal_of(chapter, "c" * 32) == 3
          and gen.ordinal_of(chapter, "h1") is None
          and gen.ordinal_of(chapter, "missing") is None)

    check("first_words truncates with an ellipsis and keeps short text whole",
          gen.first_words("one two three four five six seven eight nine", 8)
          == "one two three four five six seven eight…"
          and gen.first_words("short", 8) == "short"
          and gen.first_words("", 8) == "")

    old_p = [{"term": "Dharmic", "say": "DAR-mik"},
             {"term": "Epictetus", "say": "ep-ih-TEE-tus"}]
    new_p = [{"term": "Dharmic", "say": "DHAR-mik"},
             {"term": "Basilides", "say": "ba-SIL-ih-deez"}]
    check("pronunciation_diff names adds, changes, and removals",
          gen.pronunciation_diff(old_p, new_p)
          == ["Dharmic: DAR-mik → DHAR-mik",
              "added Basilides → ba-SIL-ih-deez",
              "removed Epictetus"]
          and gen.pronunciation_diff([], []) == []
          and gen.pronunciation_diff(old_p, old_p) == [])

    base = {"voiceId": "v1", "cast": "Narrator", "model": "eleven_turbo_v2_5",
            "stability": 0.5, "similarity": 0.75, "speed": 1.0,
            "pronunciations": []}
    check("param_diff names voice, model, and numeric drifts",
          gen.param_diff(base, {**base, "voiceId": "v2", "cast": "Other"})
          == ["voice"]
          and gen.param_diff(base, {**base, "model": "eleven_multilingual_v2"})
          == ["model"]
          and gen.param_diff(base, {**base, "stability": 0.6,
                                    "similarity": 0.8, "speed": 1.1})
          == ["stability", "similarity", "speed"]
          and gen.param_diff(
              {**base, "pronunciations": old_p},
              {**base, "pronunciations": new_p})
          == ["Dharmic: DAR-mik → DHAR-mik",
              "added Basilides → ba-SIL-ih-deez",
              "removed Epictetus"])
    check("param_diff falls back to 'parameters' when nothing named differs",
          gen.param_diff(base, dict(base)) == ["parameters"]
          and gen.param_diff({}, {"pronunciations": []}) == ["parameters"])


def check_generation() -> None:
    """generate / retake / stitch / status on a copy of the fixture, with
    a fake ElevenLabs (docs/audiobook-review-design.md §4–§5)."""
    from authorlm import generation as gen

    book = _book_copy()
    ws, _out = _workspace(book)
    manuscript, db = _manuscript(ws)
    root = book / "_audio"
    try:
        # The state schema, read and written the way types.rs does.
        state = gen.load_state(root, "sermons")
        check("state loads with the Rust schema's keys",
              state["schema"] == 1 and "sections" in state
              and "stitched" in state and "stitchKeys" in state)
        check("stitch key matches the Rust literal for the fixture chapter",
              gen.stitch_key(gen.load_chapter(root, gen.load_book(root), "sermons"))
              == "bf49100e19686707")
        check("fold pairs curly and straight quotes",
              gen.fold("“O Stranger” — said he") == gen.fold('"o stranger" - said he'))

        # The cold reason line: kindness's last paragraph pairs to the
        # fixture's old take by text and names what moved since it.
        report = gen.status(manuscript, "kindness")
        ch = report["chapters"][0]
        check("status counts the chapter", ch["total"] == 4 and ch["generated"] == 0
              and ch["remaining"] == 4, str(ch)[:300])
        moved = {m["ordinal"]: m for m in ch["moved"]}
        check("a paragraph without a take pairs to its last take by text and "
              "names each change since", 4 in moved
              and moved[4]["detail"] == ["speed", "removed Dharmic"]
              and moved[4]["since"] == "2026-09-01T10:00:00Z", str(ch["moved"]))
        lines = gen.describe_status(report)
        check("the board prints one line per fact",
              lines[0].startswith("kindness  4 paragraphs · 0 previewed · 0 generated")
              and any("¶ 4 speed, removed Dharmic" in ln for ln in lines), "\n".join(lines))
        found = gen.status(manuscript, find="ear only")["find"]
        check("--find resolves words to a paragraph with its ordinal",
              len(found) == 1 and found[0]["stem"] == "kindness"
              and found[0]["ordinal"] == 3, str(found))
        check("a find prints only its matches",
              gen.describe_status(gen.status(manuscript, find="ear only"))
              == ["kindness ¶ 3 ○ 6f869a3f… «This paragraph is for the ear only.»"])

        # Addressing.
        chapter = gen.load_chapter(root, gen.load_book(root), "kindness")
        ids = [s["id"] for s in gen.speech_of(chapter)]
        check("paragraph numbers and ranges resolve in chapter order",
              gen.resolve_paragraphs(chapter, "4,2-3") == ids[1:4])
        check("an id prefix resolves too",
              gen.resolve_paragraphs(chapter, ids[0][:8]) == ids[:1])
        for bad, why in (("9", "the chapter has 4"), ("x", "neither"),
                         ("0f0f0f0f", "no paragraphs match")):
            try:
                gen.resolve_paragraphs(chapter, bad)
                check(f"'{bad}' is refused", False)
            except audio.AudioError as err:
                check(f"'{bad}' is refused by name", why in str(err), str(err))

        # Generate with a fake ElevenLabs. Each render re-reads the state.
        log: list = []
        client = audio.ElevenLabs("k", opener=_opener(log, {
            "/text-to-speech/": (b"MP3BYTES", {"request-id": "req-new"}),
            "/user/subscription": ({"character_count": 100, "character_limit": 1000}, {}),
        }))
        dry = gen.generate(manuscript, "kindness", remaining=True, dry_run=True,
                           client=client)
        check("a dry run plans every paragraph without a take and spends nothing",
              len(dry["planned"]) == 4 and dry["characters"] == sum(
                  len(s["text"]) for s in gen.speech_of(chapter))
              and not log and dry["rendered"] == [])
        seen: list[str] = []
        result = gen.generate(manuscript, "kindness", spec="2-3", client=client,
                              progress=seen.append)
        check("generate renders the named paragraphs at the book's quality",
              [r["ordinal"] for r in result["rendered"]] == [2, 3]
              and result["quality"] == "mp3_44100_128"
              and all((root / r["rel"]).read_bytes() == b"MP3BYTES"
                      for r in result["rendered"]), str(result)[:400])
        check("progress names the paragraph", seen == ["kindness ¶ 2 (1/2)",
                                                       "kindness ¶ 3 (2/2)"], str(seen))
        state = gen.load_state(root, "kindness")
        entry = state["sections"][ids[1]]
        check("the take is recorded in the Rust schema, with what it was made from",
              entry["audioFiles"] == {"mp3_44100_128": f"audio/{ids[1]}.mp3_44100_128.mp3"}
              and entry["requestId"] == "req-new"
              and entry["generatedAt"].endswith("Z")
              and entry["madeFrom"]["text"].startswith("The Dharmic")
              and entry["madeFrom"]["speed"] == 1.0
              and entry["madeFrom"]["pronunciations"] == [], json.dumps(entry)[:400])
        check("the fixture's old entry survives the save (unknown entries are kept)",
              "0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f0f" in state["sections"])
        tts_calls = [c for c in log if "/text-to-speech/" in c[1]]
        second = tts_calls[1][2]
        check("continuity: the neighbours' text rides along, ids only when recent",
              second["previous_text"].startswith("The Dharmic")
              and second["next_text"] == "A last paragraph."
              and second["previous_request_ids"] == ["req-new"]
              and "next_request_ids" not in second, json.dumps(second)[:500])
        check("the account is read after spending",
              result["account"] == {"used": 100, "limit": 1000, "remaining": 900})

        # Guard 2: a done id is never re-rendered by generate.
        before = len(log)
        again = gen.generate(manuscript, "kindness", spec="2", client=client)
        check("generate skips a done paragraph and says so",
              again["rendered"] == [] and [p["ordinal"] for p in again["skipped"]] == [2]
              and len([c for c in log[before:] if "/text-to-speech/" in c[1]]) == 0)
        rest = gen.generate(manuscript, "kindness", remaining=True, client=client)
        check("--remaining renders only what lacks a take",
              [r["ordinal"] for r in rest["rendered"]] == [1, 4])
        check("the moved flag clears once the paragraph has its take",
              gen.status(manuscript, "kindness")["chapters"][0]["moved"] == [])

        # Retake re-renders a done id, and only on that word.
        stamp_before = gen.load_state(root, "kindness")["sections"][ids[1]]["generatedAt"]
        (root / f"audio/{ids[1]}.mp3_44100_128.mp3").write_bytes(b"OLD")
        took = gen.generate(manuscript, "kindness", spec="2", retake=True, client=client)
        check("retake re-renders and replaces the take",
              [r["ordinal"] for r in took["rendered"]] == [2]
              and (root / f"audio/{ids[1]}.mp3_44100_128.mp3").read_bytes() == b"MP3BYTES"
              and gen.load_state(root, "kindness")["sections"][ids[1]]["generatedAt"]
              >= stamp_before)

        # Stitch, with real tiny mp3s standing in for the takes.
        if gen.find_tool("ffmpeg"):
            for sid in ids:
                _mp3(root / f"audio/{sid}.mp3_44100_128.mp3")
            said: list[str] = []
            st = gen.stitch(manuscript, "kindness", progress=said.append)
            state = gen.load_state(root, "kindness")
            check("stitch writes one file and records it with its key",
                  st["rel"] == "audio/kindness.mp3_44100_128.mp3"
                  and (root / st["rel"]).stat().st_size > 0
                  and state["stitched"]["mp3_44100_128"] == st["rel"]
                  and state["stitchKeys"]["mp3_44100_128"] == gen.stitch_key(chapter)
                  and state["loudnessLufs"] == -20.0 and st["segments"] == len(chapter["sections"])
                  and len(said) == 2, str(st))
            check("silence clips are shared under silence/",
                  any(p.name.startswith("silence_") for p in (root / "silence").iterdir()))
            check("status shows the stitch as fresh",
                  gen.status(manuscript, "kindness")["chapters"][0]["stitchStale"] is False)
            gen.generate(manuscript, "kindness", spec="1", retake=True, client=client)
            check("a retake drops the stitch, so the chapter reads as not stitched",
                  gen.status(manuscript, "kindness")["chapters"][0]["stitched"] is None)
        else:
            print("  (ffmpeg not on PATH — stitch not exercised)")

        # Previews (review design §6): text with respellings substituted,
        # keyed by id, missing ones rendered, gone ones swept.
        sermons = gen.load_chapter(root, gen.load_book(root), "sermons")
        with_rule = next(s for s in gen.speech_of(sermons) if s["pronunciations"])
        check("the preview text carries the respelling inline",
              with_rule["pronunciations"][0]["term"] not in gen.preview_text(with_rule)
              and audio.alias_for(with_rule["pronunciations"][0]["say"])
              in gen.preview_text(with_rule), gen.preview_text(with_rule)[:200])
        calls: list[tuple[str, str, int]] = []

        def stub(r: Path, sp: dict, v: str, rt: int) -> Path:
            calls.append((sp["id"], v, rt))
            out = gen.preview_path(r, sp["id"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"PREVIEW")
            return out
        (root / "preview").mkdir(exist_ok=True)
        (root / "preview" / ("ab" * 16 + ".mp3")).write_bytes(b"STALE")
        pv = gen.preview(manuscript, "kindness", renderer=stub, workers=2)
        check("preview renders every paragraph of the chapter that lacks one and "
              "sweeps a preview whose id is gone",
              len(pv["rendered"]) == 4 and pv["kept"] == 0 and pv["swept"] == 1
              and pv["voice"] == "Daniel" and pv["rate"] == 175
              and all((root / "preview" / f"{sid}.mp3").exists() for sid in ids)
              and not (root / "preview" / ("ab" * 16 + ".mp3")).exists(), str(pv)[:300])
        check("the rate follows the cast row's speed", all(c[2] == 175 for c in calls))
        again = gen.preview(manuscript, "kindness", renderer=stub)
        check("a second run keeps what exists", again["rendered"] == [] and again["kept"] == 4)
        forced = gen.preview(manuscript, "kindness", spec="2", renderer=stub, force=True)
        check("--force re-renders the named paragraph only",
              [r["id"] for r in forced["rendered"]] == [ids[1]])
        whole = gen.preview(manuscript, None, renderer=stub)
        check("the whole book previews every chapter, credits included",
              set(whole["chapters"]) >= {"_opening-credits", "sermons", "about"}
              and len(whole["rendered"]) > 4)
        st = gen.status(manuscript, "kindness")["chapters"][0]
        check("status counts previews and names them per paragraph",
              st["previewed"] == 4 and st["sections"][0]["preview"] == f"preview/{ids[0]}.mp3")
        if sys.platform == "darwin" and gen.find_tool("ffmpeg"):
            real = gen.render_preview(root, {"id": "c" * 32, "text": "A short test.",
                                             "speed": 1.0, "pronunciations": []},
                                      "Daniel", 175)
            check("a real say render produces an mp3", real.stat().st_size > 1000)
            real.unlink()
        out, code = run_cli("-w", str(ws), "audio", "preview", "-m", "Scratch")
        check("audio preview needs a chapter or --all", code != 0 and "--all" in out, out)

        # The stale banner: a source saved after the export.
        fresh = gen.export_staleness(manuscript)
        check("a fresh export is not stale", fresh["stale"] is False and fresh["changed"] == [],
              str(fresh))
        import os as _os, time as _time
        future = _time.time() + 5
        _os.utime(book / "kindness.md", (future, future))
        _os.utime(root / "cast.md", (future, future))
        st_ = gen.export_staleness(manuscript)
        check("a source saved after the export names itself",
              st_["stale"] is True and st_["changed"] == ["_audio/cast.md", "kindness.md"], str(st_))
        board = gen.describe_status(gen.status(manuscript, "kindness"))
        check("the board says STALE and names the files",
              any(ln.startswith("STALE") and "kindness.md" in ln for ln in board), "\n".join(board))
        _os.utime(book / "kindness.md", None)
        _os.utime(root / "cast.md", None)
        report_ = audio.export(db, manuscript, resolve_dictionary=False)
        check("an export that rewrites nothing still clears it",
              report_["written"] == [] and gen.export_staleness(manuscript)["stale"] is False,
              str(gen.export_staleness(manuscript)))

        # The CLI road.
        out, code = run_cli("-w", str(ws), "audio", "status", "kindness", "-m", "Scratch")
        check("audio status prints the board",
              code == 0 and "kindness  4 paragraphs · 4 previewed" in out, out)
        out, code = run_cli("-w", str(ws), "audio", "generate", "kindness", "-p", "2",
                            "--dry-run", "-m", "Scratch")
        check("audio generate refuses a done paragraph without spending",
              code == 0 and "already has a take" in out and "retake kindness -p 2" in out, out)
        out, code = run_cli("-w", str(ws), "audio", "retake", "kindness", "-m", "Scratch")
        check("audio retake needs -p", code != 0 and "-p" in out, out)
        out, code = run_cli("-w", str(ws), "audio", "generate", "nope", "--remaining",
                            "-m", "Scratch")
        check("an unknown chapter is refused by name", code != 0 and "no chapter 'nope'" in out, out)
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(book, ignore_errors=True)


def check_audiobook_page() -> None:
    """The review page's backend (docs/audiobook-review-design.md §3):
    status, jobs on one queue, the audio routes with ranges."""
    from authorlm import audiobook, audiobook_server, generation as gen

    book = _book_copy()
    ws, _out = _workspace(book)
    root = book / "_audio"
    log: list = []
    audiobook.CLIENT_FACTORY = lambda: audio.ElevenLabs("k", opener=_opener(log, {
        "/text-to-speech/": (b"MP3BYTES", {"request-id": "req-page"}),
        "/user/subscription": ({"character_count": 5, "character_limit": 105}, {}),
    }))

    def stub(r: Path, sp: dict, v: str, rt: int) -> Path:
        out = gen.preview_path(r, sp["id"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"PREVIEW-" + sp["id"].encode())
        return out
    audiobook.PREVIEW_RENDERER = stub
    audiobook._SESSIONS.clear()
    try:
        s = audiobook.session(str(ws), "Scratch", inline=True)
        st = s.status()
        check("status lists every chapter with counts and opens none",
              [c["stem"] for c in st["book"]["chapters"]][:2] == ["_opening-credits", "sermons"]
              and st["chapter"] is None and st["doc"]["state"] == "reconciled in chat"
              and st["export"]["stale"] is False, str(st)[:300])
        st = s.status("kindness")
        ch = st["chapter"]
        check("a chapter comes with its paragraphs, text, and audio urls",
              ch["total"] == 4 and ch["sections"][1]["text"].startswith("The Dharmic")
              and ch["sections"][1]["previewUrl"] is None and ch["sections"][1]["takeUrl"] is None
              and ch["sections"][3]["moved"]["detail"] == ["speed", "removed Dharmic"], str(ch)[:400])
        job = s.enqueue("preview", stem="kindness")
        check("a preview job runs inline in tests and reports",
              job["state"] == "done" and "4 preview(s) rendered" in job["message"], str(job))
        st = s.status("kindness")
        check("previews show up as urls", st["chapter"]["sections"][0]["previewUrl"]
              == f"/preview/{st['chapter']['sections'][0]['id']}.mp3")
        job = s.enqueue("generate", stem="kindness", ids=[ch["sections"][1]["id"]])
        check("a generate job carries its cost and ordinal and renders",
              job["characters"] == ch["sections"][1]["characters"] and job["ordinals"] == [2]
              and job["state"] == "done" and "1 paragraph(s) rendered" in job["message"], str(job))
        check("the account is read after a paid job", s.account and s.account["remaining"] == 100)
        try:
            s.enqueue("generate", stem="kindness", ids=[ch["sections"][1]["id"]])
            check("a second tap on a done paragraph is refused at enqueue", False)
        except ValueError as err:
            check("a second tap on a done paragraph is refused at enqueue",
                  "already has a take" in str(err), str(err))
        job = s.enqueue("retake", stem="kindness", ids=[ch["sections"][1]["id"]])
        check("retake re-renders", job["state"] == "done" and "re-rendered" in job["message"])
        job = s.enqueue("generate", stem="kindness", remaining=True)
        check("generate remaining renders the rest", job["ordinals"] == [1, 3, 4]
              and job["state"] == "done", str(job))
        try:
            s.enqueue("stitch", stem="nope")
            check("an unknown chapter is refused", False)
        except audio.AudioError as err:
            check("an unknown chapter is refused by name", "no chapter 'nope'" in str(err))
        check("a fresh export wants no refresh", s.wants_refresh() is False)
        import os as _os2, time as _t2
        # Age the export instead of post-dating the source: the refresh
        # touches the book file with the real clock, and a source from the
        # future would keep the want alive.
        for p_ in [root / "audiobook.json", *(root / "chapters").glob("*.json")]:
            _os2.utime(p_, (_t2.time() - 60, _t2.time() - 60))
        _os2.utime(book / "kindness.md", None)
        check("a source newer than the export wants a refresh", s.wants_refresh() is True)
        s.enqueue("refresh", stem="kindness")
        check("the refresh re-exports and clears the want", s.wants_refresh() is False)
        jobs = s.jobs()
        check("jobs lists the recent ones newest last",
              jobs["running"] is None and jobs["queued"] == []
              and [j["kind"] for j in jobs["recent"]] == ["preview", "generate", "retake", "generate", "refresh"])
        out = audiobook.dispatch("status", {"manuscript": "Scratch", "stem": "kindness"},
                                 workspace=str(ws), inline=True)
        check("dispatch routes to the same session", out["chapter"]["generated"] == 4)
        try:
            audiobook.dispatch("dance", {"manuscript": "Scratch"}, workspace=str(ws))
            check("an unknown method is refused", False)
        except ValueError as err:
            check("an unknown method is refused by name", "dance" in str(err))

        # The audio routes and the range handling, over a real socket.
        import http.server, threading, urllib.request, urllib.error
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), audiobook_server.make_handler(str(ws), "Scratch"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(f"{base}/") as resp:
                page = resp.read().decode("utf-8")
            check("the server serves the built page at /", resp.status == 200
                  and "/api/audiobook" in page)
            sid = ch["sections"][0]["id"]
            with urllib.request.urlopen(f"{base}/preview/{sid}.mp3") as resp:
                body = resp.read()
            check("a preview streams whole with Accept-Ranges",
                  resp.status == 200 and body == b"PREVIEW-" + sid.encode()
                  and resp.headers["Accept-Ranges"] == "bytes")
            req = urllib.request.Request(f"{base}/preview/{sid}.mp3", headers={"Range": "bytes=8-11"})
            with urllib.request.urlopen(req) as resp:
                part = resp.read()
            check("a byte range answers 206 with the slice",
                  resp.status == 206 and part == sid.encode()[:4]
                  and resp.headers["Content-Range"] == f"bytes 8-11/{8 + 32}")
            with urllib.request.urlopen(f"{base}/take/kindness/{ch['sections'][1]['id']}.mp3") as resp:
                check("a take streams from the book's quality", resp.read() == b"MP3BYTES")
            try:
                urllib.request.urlopen(f"{base}/take/kindness/{'0' * 32}.mp3")
                check("a missing take is a 404", False)
            except urllib.error.HTTPError as err:
                check("a missing take is a 404", err.code == 404)
            try:
                urllib.request.urlopen(f"{base}/preview/../../audiobook.json")
                check("a path outside the audio routes is refused", False)
            except urllib.error.HTTPError as err:
                check("a path outside the audio routes is refused", err.code == 404)
            req = urllib.request.Request(f"{base}/api/audiobook", method="POST",
                                         data=json.dumps({"method": "status", "params": {"stem": "kindness"}}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
            check("POST /api/audiobook answers with the envelope and the server's manuscript",
                  body["ok"] and body["result"]["book"]["manuscript"] == "Scratch"
                  and body["result"]["chapter"]["stem"] == "kindness")
            req = urllib.request.Request(f"{base}/api/workbench", method="POST",
                                         data=json.dumps({"method": "state", "params": {}}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
            check("the same server answers the pronunciation workbench's road",
                  body["ok"] and body["result"]["manuscript"] == "Scratch"
                  and "Basilides" in [t["term"] for t in body["result"]["terms"]], str(body)[:200])
        finally:
            server.shutdown()
            server.server_close()
    finally:
        audiobook.CLIENT_FACTORY = None
        audiobook.PREVIEW_RENDERER = None
        audiobook._SESSIONS.clear()
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(book, ignore_errors=True)


def main_test() -> None:
    check_generation_helpers()
    check_text_rules()
    check_voice_tags()
    check_cast()
    check_config()
    check_credits_and_cover()
    check_fixture_and_hash()
    check_cli_and_refusals()
    check_elevenlabs()
    check_say()
    check_workbench()
    check_audiostation_launch()
    check_casting_filter_block()
    check_generation()
    check_audiobook_page()
    print(f"\nAll {PASSED} checks passed.")


if __name__ == "__main__":
    _assert_offline()
    main_test()
