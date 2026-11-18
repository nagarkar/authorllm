"""Post-processing for a pandoc .docx: a review watermark and manuscript
formatting (nagarkar/authorllm#469).

`export docx --watermark TEXT` adds a header part holding a VML text-path
shape (faint, diagonal, behind every page, as Word stores its own
watermarks) and a footer part with the same text beside a PAGE field.
`export docx --format "font=…; size=…; line-spacing=…"` rewrites
styles.xml: body font and size, line spacing (footnotes stay single),
chapters (Heading 1 / Title) on a new page, a first-line indent on body
paragraphs. Neither flag given: the docx is never opened, so output is
exactly pandoc's.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

FORMAT_KEYS = ("font", "size", "line-spacing")


def parse_format(spec: str) -> dict:
    """Parse "font=Times New Roman; size=12; line-spacing=2" into
    {"font": str, "size": float, "line-spacing": float}. Unknown keys and
    bad numbers raise ValueError naming the problem."""
    result: dict = {}
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"--format: '{part}' is not key=value")
        key, value = (s.strip() for s in part.split("=", 1))
        key = key.lower()
        if key not in FORMAT_KEYS:
            raise ValueError(f"--format: unknown key '{key}' — one of "
                             + ", ".join(FORMAT_KEYS))
        if not value:
            raise ValueError(f"--format: '{key}' has no value")
        if key == "font":
            result[key] = value
            continue
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"--format: {key} must be a number, "
                             f"got '{value}'") from None
        if number <= 0:
            raise ValueError(f"--format: {key} must be positive")
        result[key] = number
    if not result:
        raise ValueError("--format: no options given — keys are "
                         + ", ".join(FORMAT_KEYS))
    return result


def _ppr_insert(block: str, xml: str) -> str:
    if "<w:pPr>" in block:
        return block.replace("<w:pPr>", "<w:pPr>" + xml, 1)
    if "<w:pPr/>" in block:
        return block.replace("<w:pPr/>", f"<w:pPr>{xml}</w:pPr>", 1)
    return re.sub(r"(<w:name [^>]*/>)", lambda m: m.group(1)
                  + f"<w:pPr>{xml}</w:pPr>", block, count=1)


def _fonts(font: str) -> str:
    f = quoteattr(font)
    return f"<w:rFonts w:ascii={f} w:hAnsi={f} w:eastAsia={f} w:cs={f}/>"


def format_styles(styles: str, fmt: dict) -> str:
    font, size, spacing = (fmt.get("font"), fmt.get("size"),
                           fmt.get("line-spacing"))
    half = str(round(size * 2)) if size else None
    line = str(round(240 * spacing)) if spacing else None
    line_xml = (f'w:line="{line}" w:lineRule="auto"' if line else "")

    if font:
        styles = re.sub(r"<w:rFonts [^>]*/>", _fonts(font), styles)
    rpr = (_fonts(font) if font else "") + (
        f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>' if half else "")
    ppr = f'<w:spacing w:after="0" {line_xml}/>' if line else ""
    defaults = (f"<w:docDefaults><w:rPrDefault><w:rPr>{rpr}</w:rPr>"
                f"</w:rPrDefault><w:pPrDefault><w:pPr>{ppr}</w:pPr>"
                "</w:pPrDefault></w:docDefaults>")
    if "<w:docDefaults>" in styles:
        styles = re.sub(r"<w:docDefaults>.*?</w:docDefaults>", defaults,
                        styles, flags=re.S)
    else:
        styles = re.sub(r"(<w:styles[^>]*>)", lambda m: m.group(1)
                        + defaults, styles, count=1)

    def fix(match: re.Match) -> str:
        block = match.group(0)
        found = re.search(r'w:styleId="([^"]+)"', block)
        sid = found.group(1) if found else ""
        low = sid.lower()
        if low.startswith("footnote"):
            block = re.sub(r"<w:spacing [^>]*/>", "", block)
            return _ppr_insert(block, '<w:spacing w:after="120" '
                               'w:line="240" w:lineRule="auto"/>')
        if sid == "BodyText":
            block = re.sub(r"<w:ind [^>]*/>", "", block)
            block = _ppr_insert(block, '<w:ind w:firstLine="720"/>')
        heading = low.startswith("heading") or low == "title"
        if half and not heading:
            block = re.sub(r'<w:sz w:val="\d+"/>', f'<w:sz w:val="{half}"/>',
                           block)
            block = re.sub(r'<w:szCs w:val="\d+"/>',
                           f'<w:szCs w:val="{half}"/>', block)
        if line:
            block = re.sub(
                r"<w:spacing [^>]*/>",
                (f'<w:spacing w:before="240" w:after="0" {line_xml}/>'
                 if heading else f'<w:spacing w:after="0" {line_xml}/>'),
                block)
        if sid in ("Heading1", "Title"):
            single = 'w:line="240" w:lineRule="auto"'
            block = re.sub(r"<w:pageBreakBefore[^>]*/>|<w:jc [^>]*/>"
                           r"|<w:spacing [^>]*/>", "", block)
            block = _ppr_insert(
                block, '<w:pageBreakBefore/><w:spacing w:before="2880" '
                f'w:after="480" {line_xml or single}/>'
                '<w:jc w:val="center"/>')
        return block

    return re.sub(r"<w:style [^>]*>.*?</w:style>", fix, styles, flags=re.S)


_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
       ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
       'relationships" xmlns:v="urn:schemas-microsoft-com:vml"'
       ' xmlns:o="urn:schemas-microsoft-com:office:office"'
       ' xmlns:w10="urn:schemas-microsoft-com:office:word"')
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_SHAPETYPE = (
    '<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" '
    'adj="10800" path="m@7,l@8,m@5,21600l@6,21600e"><v:formulas>'
    '<v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/>'
    '<v:f eqn="sum 21600 0 @1"/><v:f eqn="sum 0 0 @2"/>'
    '<v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/>'
    '<v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/>'
    '<v:f eqn="if @0 @4 21600"/><v:f eqn="mid @5 @6"/>'
    '<v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/><v:f eqn="mid @6 @7"/>'
    '<v:f eqn="sum @6 0 @5"/></v:formulas><v:path textpathok="t" '
    'o:connecttype="custom" o:connectlocs="@9,0;@10,10800;@11,21600;'
    '@12,10800" o:connectangles="270,180,90,0"/><v:textpath on="t" '
    'fitshape="t"/><v:handles><v:h position="#0,bottomRight" '
    'xrange="6629,14971"/></v:handles><o:lock v:ext="edit" text="t" '
    'shapetype="t"/></v:shapetype>')


def watermark_parts(text: str, font: str = "Times New Roman") -> tuple[str, str]:
    """The header (VML watermark) and footer (text + PAGE field) XML."""
    shape = (
        '<v:shape id="AuthorLMWatermark" o:spid="_x0000_s2049" '
        'type="#_x0000_t136" style="position:absolute;margin-left:0;'
        'margin-top:0;width:468pt;height:78pt;rotation:315;'
        'z-index:-251654144;mso-position-horizontal:center;'
        'mso-position-horizontal-relative:margin;mso-position-vertical:center;'
        'mso-position-vertical-relative:margin" o:allowincell="f" '
        'fillcolor="#d9d9d9" stroked="f"><v:fill opacity=".5"/>'
        f'<v:textpath style="font-family:&quot;{escape(font)}&quot;;font-size:1pt" '
        f'string={quoteattr(text)}/>'
        '<w10:wrap anchorx="margin" anchory="margin"/></v:shape>')
    hdr = (f'{_XML}<w:hdr {_NS}><w:p><w:pPr><w:spacing w:line="240" '
           f'w:lineRule="auto"/></w:pPr><w:r><w:pict>{_SHAPETYPE}{shape}'
           '</w:pict></w:r></w:p></w:hdr>')
    rpr = '<w:rPr><w:sz w:val="18"/></w:rPr>'
    ftr = (f'{_XML}<w:ftr {_NS}><w:p><w:pPr><w:spacing w:line="240" '
           'w:lineRule="auto"/><w:jc w:val="center"/></w:pPr>'
           f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}   Page </w:t></w:r>'
           f'<w:r>{rpr}<w:fldChar w:fldCharType="begin"/></w:r>'
           f'<w:r>{rpr}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
           f'<w:r>{rpr}<w:fldChar w:fldCharType="separate"/></w:r>'
           f'<w:r>{rpr}<w:t>1</w:t></w:r>'
           f'<w:r>{rpr}<w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')
    return hdr, ftr


HEADER_PART = "word/headerAuthorLM.xml"
FOOTER_PART = "word/footerAuthorLM.xml"


def apply(path: Path, watermark: str | None = None,
          fmt: dict | None = None) -> None:
    """Rewrite the docx at `path` in place. No-op when both are empty."""
    if not watermark and not fmt:
        return
    with zipfile.ZipFile(path) as zin:
        names = zin.namelist()
        files = {n: zin.read(n) for n in names}
    doc = files["word/document.xml"].decode("utf-8")

    if fmt:
        if fmt.get("font"):
            doc = re.sub(r"<w:rFonts [^>]*/>", "", doc)
        if fmt.get("size"):
            doc = re.sub(r'<w:sz w:val="\d+"/>|<w:szCs w:val="\d+"/>', "",
                         doc)
        files["word/styles.xml"] = format_styles(
            files["word/styles.xml"].decode("utf-8"), fmt).encode("utf-8")

    if watermark:
        ref = ('<w:headerReference w:type="default" r:id="rIdAuthorLMHdr"/>'
               '<w:footerReference w:type="default" r:id="rIdAuthorLMFtr"/>')
        doc = re.sub(r'<w:(header|footer)Reference w:type="default"[^>]*/>',
                     "", doc)
        doc, n = re.subn(r"<w:sectPr([^>]*)>",
                         lambda m: f"<w:sectPr{m.group(1)}>" + ref, doc)
        if n == 0:
            doc = doc.replace("</w:body>",
                              f"<w:sectPr>{ref}</w:sectPr></w:body>")
        if "xmlns:r=" not in doc[:doc.index(">", doc.index("<w:document"))]:
            doc = doc.replace(
                "<w:document ", '<w:document xmlns:r="http://schemas.'
                'openxmlformats.org/officeDocument/2006/relationships" ', 1)
        hdr, ftr = watermark_parts(
            watermark, (fmt or {}).get("font") or "Times New Roman")
        files[HEADER_PART] = hdr.encode("utf-8")
        files[FOOTER_PART] = ftr.encode("utf-8")
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
        rels = files["word/_rels/document.xml.rels"].decode("utf-8")
        rels = rels.replace(
            "</Relationships>",
            f'<Relationship Id="rIdAuthorLMHdr" Type="{rel}header" '
            'Target="headerAuthorLM.xml"/>'
            f'<Relationship Id="rIdAuthorLMFtr" Type="{rel}footer" '
            'Target="footerAuthorLM.xml"/></Relationships>')
        files["word/_rels/document.xml.rels"] = rels.encode("utf-8")
        ct_base = "application/vnd.openxmlformats-officedocument.wordprocessingml."
        ct = files["[Content_Types].xml"].decode("utf-8")
        ct = ct.replace(
            "</Types>",
            f'<Override PartName="/{HEADER_PART}" ContentType="{ct_base}header+xml"/>'
            f'<Override PartName="/{FOOTER_PART}" ContentType="{ct_base}footer+xml"/>'
            "</Types>")
        files["[Content_Types].xml"] = ct.encode("utf-8")

    files["word/document.xml"] = doc.encode("utf-8")
    order = ["[Content_Types].xml"] + [n for n in files
                                       if n != "[Content_Types].xml"]
    tmp = path.with_suffix(".docx.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in order:
            zout.writestr(n, files[n])
    tmp.replace(path)
