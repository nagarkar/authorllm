#!/usr/bin/env python3
"""Build a self-contained image-review page (for Artifact publishing).

Every image is embedded as a data URI, so the output needs no host and
renders anywhere — the assistant publishes it as a claude.ai artifact
for phone/remote review. Theme-aware (light/dark, including the
viewer's manual toggle).

Usage:
    python tools/gallery.py --out page.html --title "Illustration review" \
        "img1.jpg::ascending.md — the monk among the idols" \
        "img2.png::PROPOSED for basilides.md — say yes to place" \
        "img3.jpg"                       # caption optional
    python tools/gallery.py --out page.html --json spec.json

JSON spec: {"title": ..., "note": ..., "images":
    [{"path": ..., "caption": ...}, ...]}

Keep captions self-contained (file, status, next action) — the page is
often read on a phone away from the conversation.
"""

import argparse
import base64
import json
import sys
from pathlib import Path

MIME = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png",
        ".webp": "webp", ".gif": "gif", ".svg": "svg+xml"}

STYLE = """\
:root { --bg: #14120f; --ink: #e8e2d6; --dim: #9a917f; --line: #2e2a24; }
@media (prefers-color-scheme: light) {
  :root { --bg: #f5f2ea; --ink: #26221b; --dim: #6f6757; --line: #d8d2c4; }
}
:root[data-theme="light"] { --bg: #f5f2ea; --ink: #26221b; --dim: #6f6757; --line: #d8d2c4; }
:root[data-theme="dark"] { --bg: #14120f; --ink: #e8e2d6; --dim: #9a917f; --line: #2e2a24; }
html, body { background: var(--bg); color: var(--ink); }
body { font-family: Georgia, 'Times New Roman', serif; margin: 0;
       padding: 1.25rem; display: flex; flex-direction: column;
       align-items: center; gap: 2rem; box-sizing: border-box; }
figure { margin: 0; max-width: 720px; width: 100%; }
img { width: 100%; height: auto; display: block;
      border: 1px solid var(--line); }
figcaption { margin-top: .6rem; font-size: .85rem; line-height: 1.5;
             color: var(--dim); text-align: center; }
code { font-family: ui-monospace, Menlo, monospace; font-size: .95em; }
h1 { font-size: 1rem; font-weight: normal; letter-spacing: .08em;
     text-transform: uppercase; color: var(--dim); margin: .5rem 0 0;
     text-align: center; }
p.note { max-width: 60ch; color: var(--dim); font-size: .9rem;
         line-height: 1.55; text-align: center; margin: 0; }
"""


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def figure(path: Path, caption: str | None) -> str:
    mime = MIME.get(path.suffix.lower())
    if mime is None:
        sys.exit(f"error: unsupported image type '{path.suffix}' ({path})")
    b64 = base64.b64encode(path.read_bytes()).decode()
    cap = (f"\n  <figcaption>{esc(caption)}</figcaption>" if caption else "")
    alt = esc(caption or path.stem)
    return (f'<figure>\n  <img src="data:image/{mime};base64,{b64}" '
            f'alt="{alt}">{cap}\n</figure>')


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Self-contained image-review page builder")
    ap.add_argument("images", nargs="*", metavar="PATH[::CAPTION]",
                    help="image path, optionally '::'-joined with a caption")
    ap.add_argument("--out", required=True, help="output .html path")
    ap.add_argument("--title", default="Illustration review")
    ap.add_argument("--note", help="one short paragraph under the title")
    ap.add_argument("--json", dest="spec",
                    help="JSON spec file (overrides positional images)")
    args = ap.parse_args()

    title, note = args.title, args.note
    entries: list[tuple[Path, str | None]] = []
    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        title = spec.get("title", title)
        note = spec.get("note", note)
        entries = [(Path(i["path"]), i.get("caption"))
                   for i in spec["images"]]
    else:
        for item in args.images:
            path, _, caption = item.partition("::")
            entries.append((Path(path), caption or None))
    if not entries:
        sys.exit("error: no images given (positional or --json)")

    parts = [f"<title>{esc(title)}</title>", f"<style>\n{STYLE}</style>",
             f"<h1>{esc(title)}</h1>"]
    if note:
        parts.append(f'<p class="note">{esc(note)}</p>')
    parts += [figure(p, c) for p, c in entries]
    html = "\n".join(parts) + "\n"
    if len(html) > 15_000_000:
        sys.exit(f"error: page would be {len(html) // 1_000_000} MB — "
                 "the artifact limit is 16 MB; drop or downscale images")
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"{args.out}: {len(entries)} image(s), {len(html) // 1024} KB")


if __name__ == "__main__":
    main()
