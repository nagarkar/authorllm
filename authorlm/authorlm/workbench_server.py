"""Dependency-free local HTTP shell for the compiled pronunciation workbench.

`authorlm workbench -m <manuscript>` serves `workbench_dist/index.html`
on a loopback port and opens it in the browser; the page posts to
`/api/workbench`, which is `workbench.dispatch`. This is the workbench's
one road (the MCP App road was removed 2026-09-04: it never rendered in
the client the author uses).
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from . import workbench


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
                payload = workbench.app_html().encode()
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
            if self.path != "/api/workbench":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                params = body.get("params") or {}
                if not params.get("manuscript"):
                    params["manuscript"] = manuscript
                result = workbench.dispatch(
                    body.get("method", ""), params, workspace=workspace)
                self._json(200, {"ok": True, "result": result})
            except (LookupError, ValueError, RuntimeError, KeyError) as err:
                self._json(400, {"ok": False, "error": str(err)})
            except Exception as err:  # noqa: BLE001 — a local shell reports, never dies
                self._json(500, {"ok": False, "error": f"{type(err).__name__}: {err}"})

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def run(workspace: str | None, manuscript: str | None, host: str = "127.0.0.1",
        port: int = 0, open_browser: bool = True) -> None:
    server = HTTPServer((host, port), make_handler(workspace, manuscript))
    url = f"http://{host}:{server.server_port}/"
    print(f"Pronunciation workbench: {url}", flush=True)
    print("Press Ctrl-C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
