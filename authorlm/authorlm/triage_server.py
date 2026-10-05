"""Dependency-free local HTTP shell for the compiled Triage App."""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from . import triage_transport
from .triage import TriageConflict


def app_html() -> str:
    path = Path(__file__).parent / "triage_dist" / "index.html"
    if not path.exists():
        raise RuntimeError("the Triage App frontend has not been built — "
                           "cd web/triage-app && npm install && npm run build")
    return path.read_text(encoding="utf-8")


def make_handler(workspace: str | None, manuscript: str | None):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                payload = app_html().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if self.path == "/health":
                self._json(200, {"ok": True})
                return
            self.send_error(404)

        def do_POST(self) -> None:
            if self.path != "/api/triage":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                params = body.get("params") or {}
                params.setdefault("manuscript", manuscript)
                result = triage_transport.dispatch(
                    body.get("method", ""), params, workspace=workspace)
                self._json(200, {"ok": True, "result": result})
            except TriageConflict as err:
                self._json(409, {"ok": False, "error": str(err),
                                 "conflicts": err.conflicts})
            except (LookupError, ValueError, RuntimeError, KeyError) as err:
                self._json(400, {"ok": False, "error": str(err)})

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def run(workspace: str | None, manuscript: str | None, host: str = "127.0.0.1",
        port: int = 0, open_browser: bool = True) -> None:
    server = HTTPServer((host, port), make_handler(workspace, manuscript))
    url = f"http://{host}:{server.server_port}/"
    print(f"Triage App: {url}")
    print("Press Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
