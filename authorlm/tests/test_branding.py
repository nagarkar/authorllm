"""Every served page wears the AuthorLM icon (metalm guidelines/ui.md#brand).

Pins that each page's `index.html` head links `favicon.ico`,
`favicon-32.png` and `apple-touch-icon.png`, that its header shows the
icon, that each local server (audiobook, Triage App, workbench) serves
those files from `authorlm/brand/`, and that the folder ships with the
package. No Node, no network: the page sources are read as text and the
servers run on a loopback port.

Run: python3 tests/test_branding.py
"""

from __future__ import annotations

import http.server
import os
import sys
import threading
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# Offline suite: the same pins as the other suites, so no real config,
# .env or client provenance is picked up by the imported servers.
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from authorlm import audiobook_server, branding, triage_server, workbench_server  # noqa: E402

PAGES = {"audiobook": "audiobook_dist", "triage-app": "triage_dist",
         "workbench": "workbench_dist"}
HEAD_LINKS = ['<link rel="icon" href="/favicon.ico"',
              'href="/favicon-32.png"',
              '<link rel="apple-touch-icon" href="/apple-touch-icon.png"']
SERVED = {"/favicon.ico": "image/x-icon", "/favicon-32.png": "image/png",
          "/apple-touch-icon.png": "image/png", "/icon-192.png": "image/png"}


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_brand_folder_is_complete() -> None:
    names = {p.name for p in branding.BRAND_DIR.iterdir()}
    want = {"favicon.ico", "favicon-32.png", "apple-touch-icon.png",
            "icon-192.png", "icon-512.png", "logo.png",
            "logo-wordmark-light.png", "logo-wordmark-dark.png"}
    check("authorlm/brand/ holds the metalm brand set", want <= names,
          sorted(want - names))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check("the brand folder ships as package data", '"brand/*"' in pyproject)


def test_every_page_head_links_the_favicons() -> None:
    for src, out in PAGES.items():
        html = (ROOT / "web" / src / "index.html").read_text(encoding="utf-8")
        head = html.split("</head>")[0]
        check(f"web/{src}/index.html head links the favicons",
              all(link in head for link in HEAD_LINKS), head)
        cfg = (ROOT / "web" / src / "vite.config.ts").read_text(encoding="utf-8")
        check(f"web/{src} dev server serves authorlm/brand/, build does not copy it",
              'publicDir: "../../authorlm/brand"' in cfg
              and "copyPublicDir: false" in cfg)
        app = "\n".join(p.read_text(encoding="utf-8")
                        for p in (ROOT / "web" / src / "src").glob("*.tsx"))
        check(f"web/{src} shows the icon in its header",
              'className="brand-icon" src="/icon-192.png"' in app)
        dist = ROOT / "authorlm" / out / "index.html"
        if dist.is_file():  # build output, present only after an install
            built = dist.read_text(encoding="utf-8").split("</head>")[0]
            check(f"the built {out} head links favicon.ico",
                  'favicon.ico"' in built and 'apple-touch-icon.png"' in built)


def _get(url: str) -> tuple[int, str | None, bytes]:
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, resp.headers.get("Content-Type"), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, None, b""


def test_every_server_serves_the_favicons() -> None:
    ws = tempfile.mkdtemp()
    servers = {
        "Triage App": (http.server.HTTPServer, triage_server.make_handler(ws, None)),
        "workbench": (http.server.HTTPServer, workbench_server.make_handler(ws, None)),
        "audiobook": (http.server.ThreadingHTTPServer,
                      audiobook_server.make_handler(ws, None)),
    }
    for name, (cls, handler) in servers.items():
        server = cls(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for path, kind in SERVED.items():
                status, ctype, body = _get(base + path)
                want = (branding.BRAND_DIR / path.lstrip("/")).read_bytes()
                check(f"{name} serves {path} as {kind}",
                      status == 200 and ctype == kind and body == want,
                      (status, ctype, len(body)))
            status, _, _ = _get(base + "/favicon.ico?v=2")
            check(f"{name} ignores a cache-busting query", status == 200, status)
            for bad in ("/nothing.png", "/%2e%2e/branding.py", "/.hidden.png"):
                status, _, _ = _get(base + bad)
                check(f"{name} answers {bad} with 404", status == 404, status)
        finally:
            server.shutdown()
            server.server_close()


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for test in tests:
        print(test.__name__)
        test()
    print(f"\n{len(tests)} test groups passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
