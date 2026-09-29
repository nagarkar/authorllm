"""Dependency-free local HTTP shell for the audiobook page.

`authorlm audio serve -m <manuscript>` serves `audiobook_dist/index.html`
on a loopback port and opens it in the browser; the page posts to
`/api/audiobook`, which is `audiobook.dispatch` (and `/api/workbench`,
which is `workbench.dispatch` — the pronunciation workbench is a mode of
the same page), and streams audio from
`/preview/<id>.mp3`, `/take/<stem>/<id>.mp3` and `/stitched/<stem>.mp3`
with HTTP ranges honoured so a phone can seek. The phone reaches the
port over Tailscale (`tailscale serve --bg <port>`); the server itself
never leaves 127.0.0.1.
"""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import audiobook

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def make_handler(workspace: str | None, manuscript: str | None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _json(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _file(self, path: Path, head: bool = False) -> None:
            """The whole file, or the byte range the client asked for."""
            size = path.stat().st_size
            start, end = 0, size - 1
            m = _RANGE.match(self.headers.get("Range", "") or "")
            partial = False
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                else:                       # bytes=-N → the last N bytes
                    start = max(size - int(m.group(2)), 0)
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                partial = True
            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-cache")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head:
                return
            with path.open("rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def _get(self, head: bool) -> None:
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html"}:
                payload = audiobook.app_html().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head:
                    self.wfile.write(payload)
                return
            if path == "/health":
                self._json(200, {"ok": True})
                return
            if path.startswith(("/preview/", "/take/", "/stitched/")):
                try:
                    s = audiobook.session(workspace, manuscript)
                    target = audiobook.audio_file_for(s, path)
                except Exception as err:  # noqa: BLE001
                    self._json(500, {"ok": False, "error": str(err)})
                    return
                if target is None or not target.exists():
                    self.send_error(404)
                    return
                self._file(target, head=head)
                return
            self.send_error(404)

        def do_GET(self) -> None:
            self._get(head=False)

        def do_HEAD(self) -> None:
            self._get(head=True)

        def do_POST(self) -> None:
            # The pronunciation workbench lives on this page too (author's
            # ruling 2026-09-26: "all in one place"); its transport is
            # unchanged, so the standalone `authorlm workbench` still works.
            if self.path not in ("/api/audiobook", "/api/workbench"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                params = body.get("params") or {}
                if not params.get("manuscript"):
                    params["manuscript"] = manuscript
                if self.path == "/api/workbench":
                    from . import workbench
                    result = workbench.dispatch(body.get("method", ""), params,
                                                workspace=workspace)
                else:
                    result = audiobook.dispatch(body.get("method", ""), params,
                                                workspace=workspace)
                self._json(200, {"ok": True, "result": result})
            except (LookupError, ValueError, RuntimeError, KeyError) as err:
                self._json(400, {"ok": False, "error": str(err)})
            except Exception as err:  # noqa: BLE001 — a local shell reports, never dies
                self._json(500, {"ok": False, "error": f"{type(err).__name__}: {err}"})

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def run(workspace: str | None, manuscript: str | None, host: str = "127.0.0.1",
        port: int = 8792, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(workspace, manuscript))
    url = f"http://{host}:{server.server_port}/"
    # Open the session (and its worker) now, so the first request is
    # not the one that pays for the database open, and refresh the
    # previews of anything the last export left without one.
    s = audiobook.session(workspace, manuscript)
    s.enqueue("preview")
    print(f"audiobook page for {s.manuscript['name']}: {url}")
    print("  phone: tailscale serve --bg "
          f"{server.server_port}   (then https://<mac>.<tailnet>.ts.net)")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
