"""Write reference.docx beside this script: pandoc's default reference document set to a
6 by 9 inch page with KDP margins (inside 0.75 in, outside 0.5 in, top and bottom 0.75 in,
mirrored), Garamond 11 pt body with a 0.25 in first-line indent, plain headings, 9 pt notes.
build_book.py passes it to pandoc for the Word file.

    python3 -I make_reference_docx.py
"""
import pathlib, subprocess
import docx
from docx.shared import Inches, Pt
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

here = pathlib.Path(__file__).parent
default = here / "reference-default.docx"
subprocess.run(["pandoc", "-o", str(default), "--print-default-data-file", "reference.docx"], check=True)
d = docx.Document(str(default))
for s in d.sections:
    s.page_width, s.page_height = Inches(6), Inches(9)
    s.top_margin = s.bottom_margin = Inches(0.75)
    s.left_margin, s.right_margin = Inches(0.75), Inches(0.5)
d.settings.element.append(OxmlElement("w:mirrorMargins"))

def setfont(name, size=None, font="Garamond", italic=None, bold=None, space_after=None, line=None, first_indent=None):
    try:
        st = d.styles[name]
    except KeyError:
        return
    st.font.name = font
    rpr = st.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts"); rpr.append(rf)
    for k in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rf.set(qn(k), font)
    if size: st.font.size = Pt(size)
    if italic is not None: st.font.italic = italic
    if bold is not None: st.font.bold = bold
    pf = st.paragraph_format
    if space_after is not None: pf.space_after = Pt(space_after)
    if line: pf.line_spacing = line
    if first_indent is not None: pf.first_line_indent = Inches(first_indent)

setfont("Normal", 11, space_after=0, line=1.15)
setfont("Body Text", 11, space_after=0, line=1.15, first_indent=0.25)
setfont("First Paragraph", 11, space_after=0, line=1.15, first_indent=0)
setfont("Heading 1", 20, bold=False, space_after=18)
setfont("Heading 2", 14, bold=False, italic=False, space_after=8)
setfont("Heading 3", 12, bold=False, italic=True, space_after=6)
setfont("Title", 26, bold=False)
setfont("Footnote Text", 9, space_after=2, line=1.0)
setfont("Block Text", 10.5, italic=False)
d.save(str(here / "reference.docx"))
default.unlink()
print("reference.docx written")
