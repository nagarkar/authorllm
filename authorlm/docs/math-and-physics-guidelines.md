# Mathematics and physics in the manuscript

Manuscript-wide rules for writing equations: the one source form, the
subset of TeX every output road can render, the physics notation we
commit to, where math is edited, and how passages are kept out of
outputs that cannot carry them. Status: proposed 2026-09-02, measured
against the live pipeline (pandoc 3.10.1, XeLaTeX, Drive markdown
bridge). The tooling changes it depends on are listed at the end.

## 1. One source form

- Inline math is `$...$`; display math is `$$...$$`. Nothing else: no
  `\(...\)`, no `\[...\]`, no `\begin{equation}` at the top level.
  Obsidian renders only the dollar forms, and pandoc's default markdown
  reader already parses them, so this form needs no converter option.
- Inline `$` rules: no space after the opening `$`, none before the
  closing `$`, and the closing `$` is not followed by a digit. Both
  Obsidian and pandoc use these rules to tell math from a price.
- A display equation is one physical line: `$$ ... $$` with the body on
  the same line. Line breaks inside a display block do not survive the
  Google Doc road (the importer reflows the paragraph), so a
  multi-line block would churn on every pull. Use `\\` inside an
  `aligned` environment for visual line breaks, never a newline.
- Write TeX in ASCII: `\alpha`, not `α`; `\hbar`, not `ℏ`. The renderers
  agree on the command names and disagree on Unicode fallbacks.
- No equation numbers, no `\label`, no `\ref`. Pandoc's markdown does not
  number display math, and Obsidian's numbering is per-note. Refer to an
  equation by name in prose ("the continuity equation", "the second
  law"), which is how the book's readership reads anyway.
- No macros. `\newcommand` cannot be shared with Obsidian without a
  plugin, so every equation is self-contained.

## 2. The allowed subset

The subset is the intersection of the three engines that render our
math: MathJax (Obsidian), pandoc's TeX converter (which produces MathML
for EPUB and OMML for Word and the Google Doc), and LaTeX with `amsmath`
(PDF). An equation is valid when it renders in Obsidian reading view and
passes the check below without a warning.

Allowed:

- Structure: `\frac`, `\dfrac`, `\sqrt[n]`, `^` and `_` with braces,
  `\left( ... \right)`, `\langle ... \rangle`, `\lvert`, `\lVert`.
- Operators: `\sum`, `\prod`, `\int`, `\oint`, `\iint`, `\partial`,
  `\nabla`, `\cdot`, `\times`, `\pm`, `\approx`, `\propto`, `\equiv`,
  `\to`, `\infty`, `\lim`, `\sin` and the standard function names,
  `\operatorname{name}` for anything not standard.
- Letters: Greek, `\mathbf`, `\mathrm`, `\mathcal`, `\mathbb`,
  `\boldsymbol`, `\hat`, `\bar`, `\tilde`, `\dot`, `\ddot`, `\vec`.
- Spacing and text: `\,`, `\;`, `\quad`, `\qquad`, `\text{...}`.
- Environments, only inside `$$...$$`: `aligned`, `cases`, `gathered`,
  `pmatrix`, `bmatrix`, `vmatrix`.

Not allowed:

- The `physics` package (`\dv`, `\pdv`, `\bra`, `\ket`, `\abs`) and
  `siunitx` (`\SI`, `\qty`, `\unit`). They exist only in the PDF road.
  Write derivatives with `\frac{d}{dt}` and units by hand (section 3).
- `\tag`, `\label`, `\ref`, `\eqref`, `\newcommand`, `\def`.
- `equation`, `align`, `eqnarray` environments (use `aligned` inside
  `$$`). `\\` outside an environment.
- `\mbox`, `\hbox`, `\ensuremath`, `\phantom`, any TikZ. Diagrams are
  illustrations, and go through the illustration slot machinery.

The check, run from the manuscript folder on any file:

```bash
pandoc essay.md -f markdown+smart+footnotes -t html --mathml -o /dev/null
```

A line beginning `[WARNING] Could not convert TeX math` names an
equation outside the subset. Silence means every engine can render it.

## 3. Physics notation

These are choices, not laws of nature. They are fixed so the book reads
as one hand.

- Vectors are bold roman: `\mathbf{E}`, `\mathbf{p}`. Not arrows. Unit
  vectors are `\hat{\mathbf{x}}`. Magnitudes are the plain italic letter.
- Scalars, variables, and indices are italic (the default). Physical
  constants that are single letters stay italic (`c`, `G`, `\hbar`).
- Operators in quantum contexts wear a hat: `\hat{H}`, `\hat{p}`.
- Differentials are italic and preceded by a thin space: `\int f(x)\,dx`.
  Partial derivatives are `\frac{\partial \psi}{\partial t}`.
- Units are upright, separated from the number by a thin space, with
  negative exponents rather than a slash:
  `3.0 \times 10^{8}\,\mathrm{m\,s^{-1}}`, `1.6 \times 10^{-19}\,\mathrm{J}`.
  A unit alone in prose is written as a word: "metres per second".
- Named functions are upright: `\sin`, `\exp`, `\ln`. Use `e^{x}` for
  short exponents and `\exp(...)` when the exponent is a fraction.
- Sets and number systems use `\mathbb`: `\mathbb{R}`, `\mathbb{C}`.
- Inline math is for symbols and short relations a reader can take in
  without stopping: `$E$`, `$v \ll c$`, `$F = ma$`. Anything with a
  fraction, a sum, an integral, or more than one relation is display
  math on its own line.
- Every display equation is introduced by a sentence that says what is
  about to be shown and followed by a sentence that says what it means.
  The prose has to stand without the equation, because in the audiobook
  it will (section 5). The equation sharpens the prose; it never
  replaces it.
- Say what each new symbol is the first time it appears, in prose, in
  the same paragraph.

## 4. Where math is edited

Math is written and edited in Obsidian. Text is edited wherever the
author is, including the Google Doc tabs. The two roads carry the same
file, and the rule is about where an edit can be seen.

- Obsidian's live preview renders an equation as it is typed and reading
  view renders the whole essay. It is the only editor in the pipeline
  with a rendered view of the source, so it is the place to compose,
  check, and revise equations.
- In the Google Doc tabs an equation appears as its TeX text, verbatim.
  That is the honest form: the Docs API cannot create a rendered
  equation, and the tab transplant copies text runs only. Editing inside
  `$...$` in a tab is possible once the bridge fixes land, but nothing
  there shows the effect, so the rule is: prose edits in the Doc, math
  edits in Obsidian. Do not use Insert → Equation in the Doc; the next
  push would drop it and the next pull would rewrite it in Google's own
  TeX spelling.
- The push-only export Doc is different. It is built from the pandoc
  DOCX rather than from markdown, and Drive converts Word equations to
  native, rendered Docs equations. Reviewers reading the export Doc see
  typeset math. That Doc is never pulled, so its equations are display
  only.
- Rendered outputs: Obsidian (MathJax), PDF (XeLaTeX with `unicode-math`
  and STIX Two Math beside STIX Two Text), EPUB (MathML, rendered by
  Apple Books, Thorium, and Kindle Enhanced Typesetting), DOCX (Word
  equations), export Doc (Docs equations).
- LLM drafting follows the same convention: every drafting and filter
  prompt states that mathematics is MathJax in `$...$` and `$$...$$`
  within the subset above. A filter never rewrites the inside of a math
  span, and never drops a backslash.

## 5. Keeping passages out of an output

Some passages should not reach every output. The obvious case is audio:
a display equation read aloud by a synthetic voice is noise, and a
mathematical aside may be worth cutting from the audiobook altogether.
EPUB is a weaker case. With MathML in place equations render in the
mainstream readers, so exclusion there is for an individual passage that
proves badly typeset, not for mathematics as a class.

The mechanism is a pair of full-line bracket tags, in the house grammar
already used by `[Illustration: ...]`:

```
[Omit: audio, epub]
$$ \nabla \cdot \mathbf{E} = \frac{\rho}{\varepsilon_0} $$
[/Omit]

[Only: audio]
Gauss's law says the electric flux through any closed surface is
the enclosed charge divided by the permittivity of free space.
[/Only]
```

- `[Omit: a, b]` ... `[/Omit]` removes the region from the named outputs.
  `[Only: a, b]` ... `[/Only]` keeps the region for the named outputs and
  removes it everywhere else. Output names: `pdf`, `docx`, `epub`,
  `doc` (the export Doc), `md`, `audio`. Untagged text goes everywhere.
- Tags sit on their own lines, may nest one level (`Only` inside
  `Omit` is the substitution pattern above), and never split a
  paragraph.
- Why not `<exepub>`? An HTML-style tag fails on every road at once:
  Google's markdown importer silently drops unknown HTML, so it would
  not survive a push and pull; Obsidian's reading view hides raw HTML,
  so the author cannot see the boundary; pandoc treats it as raw HTML
  and passes it into the EPUB. A bracket tag is plain text on all three,
  visible everywhere, and stripped only at publish time by the same
  Python pass that resolves illustration slots.
- Audio defaults: display math is omitted from the audio export without
  a tag, because it never reads well. Inline math is spoken as text, so
  inline math is kept to symbols and relations a voice can say (`$E$`,
  `$F = ma$`). Anything heavier is display math with a spoken paraphrase
  in the surrounding prose, which section 3 already requires.
- Audiobook readers and print readers get the same argument. A passage
  wrapped in `[Only: audio]` paraphrases; it never says something the
  print reader is not told.

## 6. Tooling this depends on

Measured 2026-09-02 against a physics sample pushed through every road.
Nothing here needs a new library; pandoc, XeLaTeX, and the STIX Two
Math font are installed.

- `publication/epub.yaml`: add `html-math-method: mathml`. Today the
  EPUB writer falls back to plain HTML and emits raw TeX for any
  equation with a fraction or an environment.
- `gdocs.normalize_markdown`: protect `$...$` and `$$...$$` spans from
  the backslash-escape strip and the bullet rewrite, add `=` and `+` to
  the set of Docs-export escapes it removes (`\=` is a TeX accent and
  corrupts every equation with an equals sign on pull), and canonicalize
  display math to one line. The normalizer runs on local files before
  PDF and EPUB export too, so this fix is for every road.
- `gdocs.escape_footnotes` gets a twin for math: inside math spans,
  double the backslash before punctuation (`\\`, `\{`, `\}`, `\|`, `\,`,
  `\;`, `\!`) and escape `*`, so Google's importer hands back what was
  sent.
- `export.export_manuscript`: build the export Doc from the pandoc DOCX
  rather than the markdown, so the Doc carries native equations.
- `export` settings: a `pdf_mathfont` key mapped to pandoc's `mathfont`
  variable, default `STIX Two Math`.
- `export.publish_markdown`: resolve `[Omit:]` and `[Only:]` regions per
  output, alongside illustration slots, and add `audio` as an output
  name for the stripped variant.
- Prompts: `beat-draft.md`, `filter-run.md`, and the critique prompts
  gain the MathJax line that `guidance.py` already carries, plus the
  subset in section 2.
- A `check-math` command wrapping the pandoc check in section 2, run
  over every content file, so a bad equation is caught before export.
