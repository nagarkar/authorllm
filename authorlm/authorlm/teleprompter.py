"""The author's on-camera passages of one file, for a teleprompter.

Every `voice: author` clip of a file (or, with `whole`, the whole file read
end to end) as `_exports/<stem>.teleprompter.docx`, what teleprompter apps
import, and the same words as `.teleprompter.txt`. It sits in the audio area
because the passages come from `media`'s clip listing; `export` below it knows
nothing of clips and supplies only the export folder and the region rules.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import media
from .export import EXPORT_DIR, resolve_regions

TELEPROMPTER_SUFFIX = ".teleprompter"
TELEPROMPTER_PT = 32        # body type; teleprompter apps reflow, print does not
TELEPROMPTER_LABEL_PT = 20
TELEPROMPTER_LINE = 360     # 240ths of a line: 1.5 spacing


def _file_arg(file: str) -> str:
    file = file.strip().lstrip("./")
    return file if file.endswith(".md") else file + ".md"


def teleprompter_passages(manuscript: dict, file: str,
                          whole: bool = False) -> list[dict]:
    """The passages the author reads on camera in one file, in order:
    every clip marked `voice: author` (docs/video-clips-design.md §5),
    its text the clip's note (`_clips/notes/<key>.md`, paragraphs kept)
    or, unkeyed, its inline description. Labels are plain: `Intro` for a
    passage with nothing spoken before it, `Outro` for one with nothing
    spoken after, otherwise the Collage's title up to its first comma.
    Other clip problems (a missing `use:` file) do not matter here: the
    words are what the author needs."""
    root = Path(manuscript["path"])
    file = _file_arg(file)
    if not (root / file).is_file():
        raise LookupError(f"{file} is not in the manuscript folder")
    entries = media.list_media(root, [file])["media"]
    titles = {e["line"]: e["title"] for e in entries
              if e["kind"] == "collage"}
    passages: list[dict] = []
    for e in entries:
        if e["kind"] != "clip" or e["fields"].get("voice") != "author":
            continue
        text = e["description"]
        if e["spec_path"] and (root / e["spec_path"]).is_file():
            text = (root / e["spec_path"]).read_text(encoding="utf-8")
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text)
                      if p.strip()]
        if not e.get("before_sentence"):
            label = "Intro"
        elif not e.get("after_sentence"):
            label = "Outro"
        else:
            box = max((i for i in titles if i < e["line"]), default=None)
            label = (titles[box].split(",")[0].strip() if box is not None
                     else "") or f"Passage {len(passages) + 1}"
        passages.append({"label": label, "paragraphs": paragraphs,
                         "line": e["line"]})
    if whole:
        return _with_body(root / file, passages)
    return passages


_MEDIA_TAG = re.compile(r"^\[/?(Collage|Montage|Clip)\b", re.IGNORECASE)


def _with_body(path: Path, passages: list[dict]) -> list[dict]:
    """`--whole`: the file read on camera end to end — every word of prose
    the md export keeps (an `[Omit: audio]` intro or outro included: the
    author speaks it; tags and headings dropped, paragraphs kept) as
    `Body` passages, any `voice: author` clip in its place."""
    by_line = {p["line"]: p for p in passages}
    lines = path.read_text(encoding="utf-8").split("\n")
    for i in range(len(lines)):
        if i + 1 in by_line:
            lines[i] = media._SENTINEL.format(i + 1)
    resolved = resolve_regions("\n".join(lines), {"md"}, path.name)
    out: list[dict] = []
    body: list[str] = []
    block: list[str] = []

    def flush_block():
        text = media._spoken("\n".join(block))
        if text:
            body.append(text)
        block.clear()

    def flush_body():
        flush_block()
        if body:
            out.append({"label": "Body", "paragraphs": body[:], "line": 0})
        body.clear()

    for ln in resolved.split("\n"):
        mark = media._SENTINEL_RE.fullmatch(ln.strip())
        if mark:
            flush_body()
            out.append(by_line[int(mark.group(1))])
        elif _MEDIA_TAG.match(ln.strip()):
            flush_block()           # a Collage or Montage box, or its close
        elif ln.strip():
            block.append(ln)
        else:
            flush_block()
    flush_body()
    return out


def _xml(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def write_teleprompter_docx(passages: list[dict], out_path: Path) -> None:
    """A bare WordprocessingML package, written by hand so nothing can
    slip in: large type, 1.5 line spacing, a plain bold label above each
    passage and a page break between passages. No header, no footer, no
    page numbers, no notice or watermark — none of those parts exist."""
    import zipfile

    size, label = TELEPROMPTER_PT * 2, TELEPROMPTER_LABEL_PT * 2

    def para(text: str, style: str, page_break: bool = False) -> str:
        # Formatting sits on every paragraph and run as well as in the
        # styles: importers that skip style inheritance (QuickLook, some
        # teleprompter apps) would otherwise show 12pt type.
        pt = label if style == "Label" else size
        ppr = (f'<w:pPr><w:pStyle w:val="{style}"/>'
               + ("<w:keepNext/>" if style == "Label" else "")
               + ("<w:pageBreakBefore/>" if page_break else "")
               + f'<w:spacing w:after="360" w:line="{TELEPROMPTER_LINE}" '
               'w:lineRule="auto"/></w:pPr>')
        rpr = ('<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
               + ("<w:b/>" if style == "Label" else "")
               + f'<w:sz w:val="{pt}"/><w:szCs w:val="{pt}"/></w:rPr>')
        return (f"<w:p>{ppr}<w:r>{rpr}<w:t xml:space=\"preserve\">"
                f"{_xml(text)}</w:t></w:r></w:p>")

    body = []
    for n, passage in enumerate(passages):
        body.append(para(passage["label"], "Label", page_break=n > 0))
        body.extend(para(p, "Normal") for p in passage["paragraphs"])
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:document {w}><w:body>{''.join(body)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" '
        'w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>")
    styles = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<w:styles {w}><w:docDefaults><w:rPrDefault><w:rPr>"
        '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
        f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr></w:rPrDefault>'
        f'<w:pPrDefault><w:pPr><w:spacing w:after="360" '
        f'w:line="{TELEPROMPTER_LINE}" w:lineRule="auto"/></w:pPr>'
        "</w:pPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Label"><w:name w:val="Label"/>'
        '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        '<w:pPr><w:keepNext/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="{label}"/><w:szCs w:val="{label}"/></w:rPr>'
        "</w:style></w:styles>")
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>")
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>")
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles)


def export_teleprompter(manuscript: dict, file: str,
                        whole: bool = False) -> dict:
    """`export teleprompter --file F`: the author's on-camera passages of
    one file as `_exports/<stem>.teleprompter.docx` (what teleprompter
    apps import) and the same words as `.teleprompter.txt`. Stops with
    an error when the file marks no clip `voice: author`."""
    passages = teleprompter_passages(manuscript, file, whole=whole)
    stem = Path(_file_arg(file)).stem
    if not passages:
        raise LookupError(
            f"{stem}.md has no clip marked 'voice: author' — add "
            "'| voice: author' to the [Clip:] of each recorded intro or "
            "outro (a clip in a Collage)")
    export_dir = Path(manuscript["path"]) / EXPORT_DIR
    export_dir.mkdir(exist_ok=True)
    docx_path = export_dir / f"{stem}{TELEPROMPTER_SUFFIX}.docx"
    write_teleprompter_docx(passages, docx_path)
    txt_path = docx_path.with_suffix(".txt")
    txt_path.write_text("\n\n\n".join(
        "\n\n".join([p["label"], *p["paragraphs"]]) for p in passages) + "\n",
        encoding="utf-8")
    return {"docx": str(docx_path), "txt": str(txt_path),
            "passages": [p["label"] for p in passages], "warnings": []}
