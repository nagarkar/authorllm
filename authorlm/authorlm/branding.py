"""The AuthorLM brand files every served page links, and the routes that serve them.

`authorlm/brand/` is a verbatim copy of metalm's `brand/authorlm/`
(metalm guidelines/ui.md#brand): never redraw or restyle it here, copy it
again from metalm. Each local page server (audiobook, Triage App,
workbench) answers every brand file at the site root (`/favicon.ico`,
`/icon-192.png`, ...) through `send()`, as the Vite dev server does from
the same folder (each `vite.config.ts` names it `publicDir`). Each page's
`index.html` links `favicon.ico`, `favicon-32.png` and
`apple-touch-icon.png` in its head and shows `icon-192.png` in its header.
"""

from __future__ import annotations

from pathlib import Path

BRAND_DIR = Path(__file__).parent / "brand"

_TYPES = {".ico": "image/x-icon", ".png": "image/png"}


def asset(path: str) -> tuple[bytes, str] | None:
    """(bytes, content type) for a brand url path, or None when it is not one."""
    name = path.partition("?")[0].removeprefix("/")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    file = BRAND_DIR / name
    kind = _TYPES.get(file.suffix)
    if kind is None or not file.is_file():
        return None
    return file.read_bytes(), kind


def send(handler, path: str, head: bool = False) -> bool:
    """Answer a GET/HEAD for a brand file on `handler`; False when `path` is not one."""
    found = asset(path)
    if found is None:
        return False
    payload, kind = found
    handler.send_response(200)
    handler.send_header("Content-Type", kind)
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "max-age=86400")
    handler.end_headers()
    if not head:
        handler.wfile.write(payload)
    return True
