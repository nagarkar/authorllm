# The Amazon edition road

The scripts here assembled the first Amazon edition of SMSTTD (the Preface, the Sermons, the
Recapitulation, Ecce Homo, the Ascending Essays and the Connections) from a cloud session that
could reach the master Google Doc only through the Drive connector, with no AuthorLM workspace.
They carry no manuscript text. The edition itself lives in the gitignored manuscripts tree
(`authorlm/manuscripts/SMSTTD-amazon/`) and in the author's Drive folder.

- `split_master.py`: one file per Doc tab from the connector's text of the master Doc, with the
  export's backslash escapes undone.
- `resolve_forms.py`: collapses the Doc's pending-change forms (`<<old>>{{new}}`, `{{new}}`,
  `[Omit: …]`, `[Explain: …]`) for a copy that is edited outside AuthorLM, logging every form.
- `check_book.py`: the mechanical checks (markers, footnotes, stale Sermon quotations, Appendix
  pointers, barred words, headings, dashes, lists).
- `build_book.py`, `print.css`, `make_pdf.js`, `make_reference_docx.py`, `order-final.txt`: the
  assembly in reading order and the three outputs: a 6 by 9 print PDF with endnotes by essay
  (pandoc fragments rendered by Chromium through Playwright), a Word file with real footnotes,
  and an EPUB. `[Voice: …]` and `[Illustration: …]` markers are dropped from the text-only
  outputs; the plates are placed by `authorlm export --profile book`.

Run from the manuscript folder:

    python3 -I check_book.py book
    python3 -I make_reference_docx.py
    python3 -I build_book.py book build order-final.txt --front front
