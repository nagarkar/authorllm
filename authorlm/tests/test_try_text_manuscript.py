"""#486: /api/try-text and /api/voices honour the page's open manuscript.

Before the fix, Listen used the server's `-m` default (and its preview
voice) even when the page had another manuscript open. An unknown name
must be a 400, not a 500; a non-string manuscript field is refused.
Hermetic: two synthetic books, stubbed Mac say, no spend.
Run: python3 tests/test_try_text_manuscript.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-try-text-ms/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-try-text-ms/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import audio, audiobook, audiobook_server  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
import contextlib
import io


FAILS = 0
SRC = Path(__file__).resolve().parents[1] / "web" / "audiobook" / "src"


def check(name: str, ok: bool, detail: object = "") -> None:
    global FAILS
    print(("ok   " if ok else "FAIL ") + name
          + ("" if ok or not detail else f" — {detail}"))
    FAILS += 0 if ok else 1


def _cli(*argv: str) -> None:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            cli_main(list(argv))
            code = 0
        except SystemExit as err:
            code = err.code or 0 if isinstance(err.code, int) or err.code is None else 1
            if not isinstance(err.code, int) and err.code is not None:
                out.write(str(err.code))
    assert code == 0, out.getvalue()


def _book(root: Path, name: str, voice: str) -> None:
    (root / "one.md").write_text("# One\n\nHello.\n", encoding="utf-8")
    (root / "toc.toml").write_text('[[chapter]]\nfile = "one.md"\n',
                                   encoding="utf-8")
    ms = {"path": str(root), "name": name}
    audio.init(ms)
    cfg = (root / "_audio" / "audiobook.toml").read_text(encoding="utf-8")
    (root / "_audio" / "audiobook.toml").write_text(
        cfg.replace('voice = "Daniel"', f'voice = "{voice}"', 1),
        encoding="utf-8")


def _post(base: str, body: dict):
    return urllib.request.urlopen(urllib.request.Request(
        f"{base}/api/try-text", method="POST",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-AuthorLM": "1",
                 "Origin": base}))


def main() -> int:
    ws = Path(tempfile.mkdtemp(prefix="authorlm-try-ms-ws-"))
    default_book = Path(tempfile.mkdtemp(prefix="authorlm-try-ms-default-"))
    other_book = Path(tempfile.mkdtemp(prefix="authorlm-try-ms-other-"))
    rendered: list[str] = []
    server = None
    try:
        _book(default_book, "Default", "Jamie (Premium)")
        _book(other_book, "Other", "Alex")
        _cli("-w", str(ws), "init", "--name", "Default", "--path",
             str(default_book), "--author", "A", "--copyright-owner", "A")
        _cli("-w", str(ws), "init", "--name", "Other", "--path",
             str(other_book), "--author", "A", "--copyright-owner", "A")

        def renderer(root, speech, voice, rate):
            rendered.append(voice)
            path = Path(root) / "x.mp3"
            path.write_bytes(b"MP3-" + voice.encode())
            return path

        audiobook.PREVIEW_RENDERER = renderer
        audiobook.INSTALLED_VOICES = lambda: {
            "Jamie (Premium)", "Alex", "Daniel"}
        audiobook._SESSIONS.clear()

        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            audiobook_server.make_handler(str(ws), "Default"))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        with _post(base, {"markdown": "Hello there."}) as resp:
            check("no manuscript uses the server default's preview voice",
                  resp.status == 200 and rendered[-1] == "Jamie (Premium)"
                  and resp.read().startswith(b"MP3-Jamie"),
                  rendered)

        with _post(base, {"markdown": "Hello there.",
                          "manuscript": "Other"}) as resp:
            check("a named manuscript uses that manuscript's preview voice",
                  resp.status == 200 and rendered[-1] == "Alex"
                  and resp.read().startswith(b"MP3-Alex"),
                  rendered)

        try:
            _post(base, {"markdown": "Hello.", "manuscript": "Nowhere"})
            check("an unknown manuscript is a 400", False)
        except urllib.error.HTTPError as err:
            body = json.loads(err.read())
            check("an unknown manuscript is a 400",
                  err.code == 400 and "no manuscript" in body.get("error", ""),
                  body)

        try:
            _post(base, {"markdown": "Hello.", "manuscript": 3})
            check("a non-string manuscript is a 400", False)
        except urllib.error.HTTPError as err:
            body = json.loads(err.read())
            check("a non-string manuscript is a 400",
                  err.code == 400 and "manuscript is a name" in body.get(
                      "error", ""), body)

        with urllib.request.urlopen(f"{base}/api/voices?m=Other") as resp:
            voices = json.loads(resp.read())
        check("/api/voices?m= uses that manuscript's preview voice",
              voices.get("current") == "Alex" and "Alex" in voices.get(
                  "choices", []), voices)

        listen = (SRC / "ListenVoice.tsx").read_text(encoding="utf-8")
        check("pageManuscript takes the first hash segment",
              'location.hash.replace(/^#/, "").split("/")[0]' in listen)
        devedit = (SRC / "DevEdit.tsx").read_text(encoding="utf-8")
        check("DevEdit Listen posts manuscript: pageManuscript()",
              "manuscript: pageManuscript()" in devedit
              and "voice: listenVoice()" in devedit)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        audiobook.PREVIEW_RENDERER = None
        audiobook.INSTALLED_VOICES = audiobook.installed_say_voices
        audiobook._SESSIONS.clear()
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(default_book, ignore_errors=True)
        shutil.rmtree(other_book, ignore_errors=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
