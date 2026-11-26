"""The proof checks: eight deterministic checks a proofreader makes by
eye, run by `book lint` beside the nine checks of `lint.py`.

Invariant: reading only, and no model. Every check takes text and returns
`lint.Finding`s that name the line, quote the words and name the rule;
none rewrites anything. Nothing here opens the database or the network:
the caller (changes.py) passes in the manuscript's words, its spelling
variant and its style laws. The one writer is `add_accepted`, the
author's own act of accepting a word, which appends to the manuscript's
accepted-words file and touches nothing else.

The rules, by code:

- `spelling`: a word that is not in the dictionary, nor among the
  manuscript's own words.
- `doubled-word`: the same word twice in a row ("the the").
- `spelling-variant`: a British spelling in an American manuscript, or
  the reverse (a word the author accepted is left alone).
- `quote-style`: straight and curly quote marks mixed (against the style
  law when one states the style), and a quote mark with no partner.
- `dash-form`: a hyphen or double hyphen typed for a dash, a spaced en
  dash used as a dash, and an em dash of the wrong form (closed or
  spaced, as the style law sets; the house default is closed).
- `footnote`: a marker with no definition, a definition with no marker,
  a label defined twice.
- `link-tracking`: a link that carries tracking tags (utm_source and
  the like).
- `banned-word`: a word or phrase a style law bans outright.

Decisions:
- The proof checks stay out of `lint.lint_text`. Why: `write propose`
  stops on a lint error, and a beat must not be stopped by a name the
  dictionary has not met. They run only where `book lint` asks. Enforced
  by:
  tests/test_lint_proof.py::test_the_nine_checks_do_not_run_the_proof_checks.
- The dictionary is three word lists shipped in the package
  (`spelling/`, built by `tools/build_spelling_wordlists.py` from the
  SCOWL Hunspell dictionaries; `spelling/PROVENANCE.md`), and a short
  hand-kept list of English words they lack (`spelling/en-extra.txt`).
  Why: the core package has no dependencies and lint runs with no
  network; a list of inflected words needs neither a spell-check library
  nor Hunspell. Enforced by:
  tests/test_lint_proof.py::test_the_word_lists_have_provenance.
- The spelling variant is the manuscript's `language` (`en-GB` and its
  kin are British; anything else, or unset, is American). Why: it is
  the setting the book already has for its language; a second setting
  could disagree with it. Enforced by:
  tests/test_book_lint_proof.py::OwnWordsTests::test_the_variant_is_the_manuscripts_language.
- The manuscript's own words are accepted from four places: concept
  names and their aliases, the pronunciation dictionary, the capitalised
  words of the current filter readings, and the accepted-words file.
  Each word of a name is accepted on its own, in any case, with a plural
  s. Enforced by: tests/test_book_lint_proof.py::OwnWordsTests.
- Early-modern English is handled by a rule, with no setting: an -eth,
  -est or -st form of a word the dictionary knows is accepted
  (differeth, knowest, lovest), with a short closed list of old words
  (didst, whoso, 'tis). Why: the Sermons' English is also quoted in the
  essays, so a setting per file would miss it there; the cost is that a
  slip which happens to make such a form passes. Enforced by:
  tests/test_lint_proof.py::test_spelling_handles_early_modern_english.
- A word English builds freely on a known word is accepted: an opening
  (un-, re-, over-…), an ending (-ness, -less, -able, -er…), a plural.
  The known word must be five letters or more (four for -ness and its
  kin). Why: on SMSTTD these were 49 words, none a slip (groundlessness,
  unshareable, theodicies); the cost is a slip that leaves such a form.
- A capitalised word the dictionary does not know is taken as a name
  and left alone, unless the file writes it once and it is one slip (one
  letter added, dropped, changed or swapped) from a name the book uses
  or one of the manuscript's own words, or, in a heading or opening a
  sentence, from any word the book uses. Why: on SMSTTD, 223 unknown
  capitalised words were all names (translators, publishers, cited
  authors), and reporting them buried the one real slip; a misspelled
  name is nearly always one slip from the right one, which the book
  itself holds. Enforced by:
  tests/test_lint_proof.py::test_spelling_leaves_unknown_names_to_the_author.
- A quoted span or a sentence that is mostly unknown words is another
  language (a German title, a Latin verse) and is not read.
- Left alone by `spelling`: code, links, file names, footnote labels,
  directive and media tags (`[Voice: …]`, `[Suggestion: …]`,
  `[Footnote: …]`), HTML, math, front matter, words set in italics
  (borrowed and transliterated terms are italic by law), words with a
  letter outside a to z, words in capitals, and words joined to a digit.
  Enforced by: tests/test_lint_proof.py::test_spelling_leaves_markup_alone.
- A banned word is read from a law's own sentence and from nowhere
  else; the grammar is `_BAN` below (never / avoid / do not use,
  followed at once by the quoted words, or a law that opens "No X:").
  Why: a law is prose, and only a sentence that bans a quoted word
  outright can be checked without judgment; "never a stand-in such as
  'this'" is left to a reader. Enforced by:
  tests/test_lint_proof.py::test_ban_terms_are_read_from_the_laws.

Terms:
- **proof check**: one of the eight deterministic rules of this module,
  named by its code (`RULES`).
- **accepted-words file**: `<manuscript>/_lint/accepted-words.txt`, the
  words the author has accepted for the spell check, one per line.
- **spelling variant**: American (`us`) or British (`uk`) spelling, read
  from the manuscript's `language`.

Never imports: api, cli, db, changes.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .lint import Finding

RULES = ("spelling", "doubled-word", "spelling-variant", "quote-style",
         "dash-form", "footnote", "link-tracking", "banned-word")

ACCEPTED_FILE = "_lint/accepted-words.txt"
_ACCEPTED_HEADER = (
    "# Words the spell check accepts in this manuscript, one per line.\n"
    "# Add one with: authorlm book lint-accept <word>\n")

_LISTS = Path(__file__).resolve().parent / "spelling"

# ------------------------------------------------------------ the words


@dataclass(frozen=True)
class Dictionary:
    """The shipped word lists for one variant. `words` keeps the lists'
    own capitals (Paris); `lower` is the same set lower-cased, for the
    near-miss test only."""
    words: frozenset[str]
    other: frozenset[str]          # the other variant's own spellings
    own: frozenset[str]            # this variant's own spellings


@dataclass(frozen=True)
class Lexicon:
    """What one manuscript's spelling is checked against."""
    variant: str = "us"                      # us | uk
    accepted: frozenset[str] = frozenset()   # casefolded; `accepted_set`
    used: frozenset[str] = frozenset()       # lower-cased; `vocabulary`
    names: frozenset[str] = frozenset()      # as written; `vocabulary`


def _read_list(name: str) -> frozenset[str]:
    with gzip.open(_LISTS / name, "rt", encoding="utf-8") as handle:
        return frozenset(line.rstrip("\n") for line in handle if line.strip())


@lru_cache(maxsize=2)
def dictionary(variant: str = "us") -> Dictionary:
    common = _read_list("en-common.txt.gz") | frozenset(
        line.strip() for line in (_LISTS / "en-extra.txt").read_text(
            encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#"))
    us, gb = _read_list("en-us-only.txt.gz"), _read_list("en-gb-only.txt.gz")
    own, other = (gb, us) if variant == "uk" else (us, gb)
    return Dictionary(words=common | own, other=other, own=own)


_BRITISH = ("gb", "uk", "au", "nz", "ie", "za", "in")


def variant_of(language: str) -> str:
    """`us` or `uk` for a manuscript's language tag: `en-GB` and the
    other Commonwealth tags are British spelling; `en`, `en-US` and an
    unset language are American."""
    parts = (language or "").lower().replace("_", "-").split("-")
    return "uk" if len(parts) > 1 and parts[1] in _BRITISH else "us"


_LETTERS = re.compile(r"[^\W\d_]+")


def accepted_set(terms) -> frozenset[str]:
    """The casefolded words of `terms`, each word of a name on its own
    ('Chitta Darshana' gives chitta and darshana; 're-ligare' gives re
    and ligare, and the whole)."""
    out: set[str] = set()
    for term in terms:
        term = unicodedata.normalize("NFC", term or "")
        for word in _LETTERS.findall(term):
            out.add(word.casefold())
    return frozenset(out)


def capitalised_words(text: str) -> list[str]:
    """The capitalised words of a text (a registry, a reading): its
    proper names, and the first word of each sentence, which costs
    nothing because that word is English anyway."""
    return [w for w in _LETTERS.findall(text or "") if w[:1].isupper()]


def read_accepted(root: Path) -> list[str]:
    """The words of the manuscript's accepted-words file, in file order;
    [] when there is none."""
    path = Path(root) / ACCEPTED_FILE
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.split("#")[0].strip()
        if word:
            out.append(word)
    return out


def check_word(word: str) -> str:
    """`word` trimmed and NFC, or a ValueError when it is not one word
    (letters, with hyphens or apostrophes inside)."""
    word = unicodedata.normalize("NFC", (word or "").strip())
    if not re.fullmatch(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", word):
        raise ValueError(
            f"'{word}' is not a single word (letters, with a hyphen "
            f"or an apostrophe inside)")
    return word


def new_accepted(root: Path, words: list[str]) -> list[str]:
    """The words of `words` the accepted-words file does not hold yet,
    each once, in order. Writes nothing."""
    have = set(read_accepted(Path(root)))
    new: list[str] = []
    for word in words:
        word = check_word(word)
        if word not in have:
            have.add(word)
            new.append(word)
    return new


def add_accepted(root: Path, words: list[str]) -> list[str]:
    """Append the words not yet in the accepted-words file; returns the
    ones added."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"the manuscript folder is not there: {root}")
    new = new_accepted(root, words)
    if new:
        path = root / ACCEPTED_FILE
        path.parent.mkdir(exist_ok=True)
        old = (path.read_text(encoding="utf-8") if path.is_file()
               else _ACCEPTED_HEADER)
        if old and not old.endswith("\n"):
            old += "\n"
        path.write_text(old + "".join(f"{w}\n" for w in new),
                        encoding="utf-8")
    return new


# -------------------------------------------------------------- masking
#
# Every check reads a MASKED copy of the file: the spans it must not
# read are blanked to spaces, newlines kept, so an offset in the masked
# text is the same offset, and the same line, in the file.

_FRONT_MATTER = re.compile(r"\A---\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL)
_FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.DOTALL | re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>\n]*>")
_MATH = re.compile(r"\$\$.+?\$\$|(?<![\\$\w])\$(?=\S)[^$\n]+?(?<=\S)\$(?!\w)",
                   re.DOTALL)
_LINK_TARGET = re.compile(r"(?<=\])\([^()\s]*(?:\([^()\s]*\)[^()\s]*)*"
                          r"(?:\s+\"[^\"\n]*\")?\)")
_URL = re.compile(
    r"(?:https?://|www\.)[^\s<>\[\]\"“”]+"
    r"|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"
    # a DOI, a bare domain with its path, a file name
    r"|\b10\.\d{4,9}/[^\s<>\[\]\"“”]+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|edu|gov|io|ai|co|uk|de|fr"
    r"|md|markdown|html?|pdf|toml|json|txt|png|jpe?g|svg|py)\b"
    r"(?:/[^\s<>\[\]\"“”)]*)?")
_FOOTNOTE_REF = re.compile(r"\[\^[^\]\n]*\]:?")
# `[Voice: narrator]`, `[Illustration: … | caption: …]`, `[/Clip]`: a
# capitalised keyword and a colon, or a closing tag. The body may hold
# one level of brackets.
_TAG = re.compile(r"\[/[A-Z][A-Za-z]*\]"
                  r"|\[[A-Z][A-Za-z]*:(?:[^\[\]\n]|\[[^\[\]\n]*\])*\]")
_ITALIC = re.compile(
    r"(?<![*\w\\])\*(?![*\s])([^*\n]+?)(?<![*\s\\])\*(?![*\w])"
    r"|(?<![_\w\\])_(?![_\s])([^_\n]+?)(?<![_\s\\])_(?![_\w])")

_ALWAYS = (_FRONT_MATTER, _FENCE, _INLINE_CODE, _COMMENT, _MATH,
           _LINK_TARGET, _URL, _HTML_TAG)


def _blank(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(
        lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def masked(text: str, *, tags: bool = True, footnotes: bool = True,
           italics: bool = False) -> str:
    """`text` with code, links, HTML, math and front matter blanked, and
    on request directive tags, footnote labels and italic spans too.
    Same length, same line breaks."""
    out = text
    for pattern in _ALWAYS:
        out = _blank(out, pattern)
    if tags:
        out = _blank(out, _TAG)
    if footnotes:
        out = _blank(out, _FOOTNOTE_REF)
    if italics:
        out = _blank(out.replace("**", "  "), _ITALIC)
    return out


class _Places:
    """Line and paragraph numbers for offsets into one text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        # Paragraph k (1-based, as `lint.paragraphs` counts them) starts
        # at these offsets.
        self.units = [m.start() for m in re.finditer(
            r"(?:\A|(?<=\n))[ \t]*\S[^\n]*(?:\n[ \t]*\S[^\n]*)*", text)]

    def line(self, at: int) -> int:
        import bisect

        return bisect.bisect_right(self.line_starts, at)

    def unit(self, at: int) -> int:
        import bisect

        return max(1, bisect.bisect_right(self.units, at))

    def context(self, start: int, end: int, width: int = 36) -> str:
        """The words around [start, end) on its own line."""
        lo = self.text.rfind("\n", 0, start) + 1
        hi = self.text.find("\n", end)
        hi = len(self.text) if hi == -1 else hi
        a, b = max(lo, start - width), min(hi, end + width)
        if a > lo:
            a = self.text.find(" ", a, start) + 1 or a
        if b < hi:
            b = self.text.rfind(" ", end, b) if " " in self.text[end:b] else b
        return (("…" if a > lo else "") + self.text[a:b].strip()
                + ("…" if b < hi else ""))


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(
        r"(?:\A|(?<=\n))[ \t]*\S[^\n]*(?:\n[ \t]*\S[^\n]*)*", text)]


# ------------------------------------------------------------- spelling

_TOKEN = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_ASCII_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*\Z")
_CONTRACTION_TAILS = ("ve", "ll", "d", "re", "t", "s", "m", "em")
# Prefixes written with a hyphen (re-read, non-being, co-author).
_PREFIXES = frozenset(
    "anti co counter de ex extra inter intra meta mid multi neo non over "
    "post pre pro proto pseudo quasi re self semi sub super trans ultra "
    "un under".split())
# Old words no -eth/-est rule reaches.
_ARCHAIC = frozenset(
    "didst wouldst couldst shouldst canst mayst mightst hadst saidst "
    "durst wast wert hast dost doth hath shalt wilt art thou thee thy "
    "thine ye whoso whosoever whatsoever wheresoever whither thither "
    "hither whence thence hence wherefore wherein whereof whereby "
    "thereof therein thereby hereof herein unto betwixt nay yea aught "
    "naught ere oft tis twas twere twill o'er e'er ne'er e'en th "
    "harken hearken perchance mayhap methinks forsooth prithee verily "
    "begat spake".split())
_LETTERS_AZ = "abcdefghijklmnopqrstuvwxyz"


def _archaic_stems(low: str) -> list[str]:
    """The modern words an -eth / -est / -st form may be built on."""
    stems: list[str] = []
    for suffix in ("eth", "est"):
        if low.endswith(suffix) and len(low) > len(suffix) + 1:
            base = low[:-3]
            stems += [base, base + "e"]
            if len(base) > 2 and base[-1] == base[-2]:
                stems.append(base[:-1])            # runneth: run
            if base.endswith("i"):
                stems.append(base[:-1] + "y")      # crieth: cry
    if low.endswith("st") and low[:-2].endswith("e"):
        stems.append(low[:-2])                     # lovest: love
    return stems


def _in_dictionary(word: str, low: str, words: frozenset[str]) -> bool:
    if word in words or low in words:
        return True
    # A word in capitals or with a leading capital may be a name the
    # list holds capitalised (PARIS, Paris).
    return word[:1].isupper() and low.capitalize() in words


# Endings and openings English adds freely; the lists hold the common
# results (kindness, unhappy) and not the rest (groundlessness,
# unshareable). A slip rarely leaves a known word of five letters behind.
_OPENINGS = ("un", "re", "non", "over", "under", "mis", "pre", "super",
             "counter", "inter", "anti", "self", "para", "co")
_ENDINGS = ("lessness", "ness", "less", "hood", "like", "ship", "able",
            "ers", "er")


def _built_on(low: str) -> list[str]:
    """The known words `low` may be built on by an opening, an ending,
    or a plural s."""
    stems: list[str] = []
    for opening in _OPENINGS:
        if low.startswith(opening) and len(low) - len(opening) >= 5:
            stems.append(low[len(opening):])
    for ending in _ENDINGS:
        if low.endswith(ending) and len(low) - len(ending) >= (
                5 if ending in ("able", "ers", "er") else 4):
            stem = low[:-len(ending)]
            stems.append(stem)
            if stem.endswith("i"):
                stems.append(stem[:-1] + "y")      # creatureliness
    if low.endswith("ies") and len(low) >= 8:
        stems.append(low[:-3] + "y")
    elif low.endswith("s") and not low.endswith("ss") and len(low) >= 7:
        stems.append(low[:-1])
        if low.endswith("es"):
            stems.append(low[:-2])
    return stems


def is_known(word: str, lexicon: Lexicon) -> bool:
    """Whether the spell check accepts `word` (one token, apostrophes
    straightened)."""
    book = dictionary(lexicon.variant)
    for form in _forms(word):
        low = form.lower()
        if (_in_dictionary(form, low, book.words)
                or low in book.other          # the variant rule's finding
                or form.casefold() in lexicon.accepted
                or low in _ARCHAIC):
            return True
        if low.endswith("s") and (low[:-1] in lexicon.accepted
                                  or (low.endswith("es")
                                      and low[:-2] in lexicon.accepted)):
            return True
        if any(stem in book.words for stem in _archaic_stems(low)):
            return True
        for stem in _built_on(low):
            if stem in book.words or any(
                    deeper in book.words for deeper in _built_on(stem)):
                return True
    return False


def _forms(word: str) -> list[str]:
    """`word`, then without its possessive or contraction ending."""
    forms = [word]
    if "'" in word:
        head, _, tail = word.rpartition("'")
        if tail.lower() in _CONTRACTION_TAILS and head:
            forms.append(head)
    return forms


def _edits(low: str):
    """Every string one slip from `low`: a swap of neighbours, a letter
    dropped, changed or added, in that order (the commonest slips
    first)."""
    splits = [(low[:i], low[i:]) for i in range(len(low) + 1)]
    for a, b in splits:
        if len(b) > 1:
            yield a + b[1] + b[0] + b[2:]
    for a, b in splits:
        if b:
            yield a + b[1:]
    for a, b in splits:
        if b:
            for c in _LETTERS_AZ:
                if c != b[0]:
                    yield a + c + b[1:]
    for a, b in splits:
        for c in _LETTERS_AZ:
            yield a + c + b


@lru_cache(maxsize=4)
def _lower_index(variant: str) -> dict[str, str]:
    """{lower-cased word: the word as the list writes it}."""
    return {w.lower(): w for w in dictionary(variant).words}


def suggestions(word: str, lexicon: Lexicon, limit: int = 3,
                pool: frozenset[str] | None = None) -> list[str]:
    """Known words one slip from `word`, commonest kind of slip first.
    With `pool`, only words of that set."""
    low = word.lower()
    index = _lower_index(lexicon.variant)
    seen: dict[str, None] = {}
    for candidate in _edits(low):
        if candidate == low or candidate in seen:
            continue
        if pool is not None:
            if candidate in pool:
                seen[candidate] = None
        elif candidate in index:
            seen[index[candidate]] = None
        elif candidate in lexicon.accepted:
            seen[candidate] = None
        if len(seen) >= limit:
            break
    return list(seen)


def _sentence_initial(text: str, at: int) -> bool:
    i = at - 1
    while i >= 0 and text[i] in " \t*_#>\"'“‘([-—":
        i -= 1
    if i < 0 or text[i] in "!?\n|":
        return True
    if text[i] != ".":
        return False
    # "R. K. Gaye": a full stop after a single capital is an initial.
    return not (i >= 1 and text[i - 1].isupper()
                and (i < 2 or not text[i - 2].isalpha()))


_SEGMENTS = (re.compile(r"“[^“”\n]*”|\"[^\"\n]*\""),
             re.compile(r"[^\n.!?;:]+"))


def _foreign_spans(prose: str, tokens: list[tuple]) -> list[tuple[int, int]]:
    """The quoted spans and sentences that are mostly words the lint
    does not know: another language (a German title, a Latin verse),
    not a run of slips."""
    import bisect

    starts = [t[0] for t in tokens]
    out = []
    for m in (m for pattern in _SEGMENTS for m in pattern.finditer(prose)):
        inside = tokens[bisect.bisect_left(starts, m.start()):
                        bisect.bisect_left(starts, m.end())]
        total = len(inside)
        unknown = sum(1 for t in inside if not t[3])
        if total and ((unknown >= 3 and unknown * 2 >= total)
                      or (total <= 4 and unknown >= 2
                          and unknown * 5 >= total * 3)):
            out.append((m.start(), m.end()))
    return out


def _spelling_tokens(prose: str, lexicon: Lexicon) -> list[tuple]:
    """(start, end, word, known) for every word the spell check reads;
    `word` is None for one it never reports (a word with a letter
    outside a to z, capitals, a hyphenated prefix), which still counts
    toward telling a foreign passage."""
    out = []
    for m in _TOKEN.finditer(prose):
        start, end = m.start(), m.end()
        if (start and prose[start - 1].isdigit()) or (
                end < len(prose) and prose[end].isdigit()):
            continue
        word = unicodedata.normalize("NFC", m.group(0)).replace("’", "'")
        if len(word) < 2:
            continue
        if not _ASCII_WORD.match(word):
            out.append((start, end, None, False))
            continue
        if re.match(r"[A-Za-z]'[A-Z]", word):
            out.append((start, end, None, True))   # d'Orbigny, O'Brien
            continue
        if word.isupper() or (not word.islower() and not word.istitle()
                              and "'" not in word):
            out.append((start, end, None, True))   # NATO, iPhone
            continue
        if (end < len(prose) and prose[end] == "-"
                and word.lower() in _PREFIXES):
            out.append((start, end, None, True))
            continue
        out.append((start, end, word, is_known(word, lexicon)))
    return out


def vocabulary(texts) -> tuple[frozenset[str], frozenset[str]]:
    """(used, names) over the book's texts. `used`: the lower-cased words
    the dictionary knows that the book uses, which a misspelled name is
    compared with ('Nietzche' against the book's own 'Nietzsche').
    `names`: the capitalised words the dictionary does not know that the
    book writes more than once; a word spelled the same way twice is a
    name, not a slip."""
    lexicon = Lexicon()
    used: set[str] = set()
    counts: dict[str, int] = {}
    for text in texts:
        for _s, _e, word, known in _spelling_tokens(
                masked(text, italics=True), lexicon):
            if not word:
                continue
            base = _forms(word)[-1]
            if known:
                used.add(base.lower())
            elif base[:1].isupper():
                counts[base] = counts.get(base, 0) + 1
    return frozenset(used), frozenset(w for w, n in counts.items() if n > 1)


def _name_slips(word: str, near: list[str], lexicon: Lexicon,
                here: set[str], inside: bool, once: bool) -> list[str]:
    """Which of the words one slip from a capitalised unknown `word`
    make it a likely slip and not a name. Inside a sentence: only a
    name the book uses or one of the manuscript's own words, and only
    for a word of six letters or more ('Nietzche' beside 'Nietzsche';
    short names have too many neighbours). Opening a sentence, where
    any word has a capital: a word the book uses. Never a word the
    book writes more than once: the same spelling twice is a name."""
    words = dictionary(lexicon.variant).words
    if not once or word in lexicon.names:
        return []
    if inside:
        if len(word) < 6:
            return []
        return [n for n in near if n.lower() not in words and (
            n.casefold() in lexicon.accepted
            or (n[:1].isupper() and (n.lower() in here
                                     or n.lower() in lexicon.used)))]
    return [n for n in near if n.lower() in words
            and (n.lower() in here or n.lower() in lexicon.used)]


def check_spelling(text: str, lexicon: Lexicon) -> list[Finding]:
    """One finding per unknown word per file, at its first use, counting
    the rest."""
    prose = masked(text, italics=True)
    places = _Places(text)
    tokens = _spelling_tokens(prose, lexicon)
    foreign = _foreign_spans(prose, tokens)
    here = {_forms(t[2])[-1].lower() for t in tokens if t[2] and t[3]}
    seen: dict[str, list[int]] = {}
    first: dict[str, tuple[int, int]] = {}
    capital_inside: set[str] = set()
    for start, end, word, known in tokens:
        if word is None or known:
            continue
        if any(lo <= start < hi for lo, hi in foreign):
            continue
        base = _forms(word)[-1]
        seen.setdefault(base, []).append(places.line(start))
        first.setdefault(base, (start, end))
        if base[:1].isupper() and not _sentence_initial(prose, start):
            capital_inside.add(base)
    heading_line = {places.line(m.start()) for m in re.finditer(
        r"^[ \t]*#", prose, re.MULTILINE)}
    out = []
    for word, lines in seen.items():
        near = suggestions(word, lexicon)
        if word[:1].isupper() and lines[0] not in heading_line:
            near = _name_slips(word, near, lexicon, here,
                               inside=word in capital_inside,
                               once=len(lines) == 1)
            if not near:
                continue          # a name; the author's to spell
        elif word[:1].isupper() and not near:
            continue
        start, end = first[word]
        message = (f"'{word}' is not in the dictionary or the "
                   f"manuscript's own words")
        if near:
            message += "; did you mean " + ", ".join(near) + "?"
        if len(lines) > 1:
            shown = ", ".join(str(n) for n in lines[:8])
            message += (f" ({len(lines)} times in this file, lines {shown}"
                        + ("…" if len(lines) > 8 else "") + ")")
        out.append(Finding("spelling", "warning", places.unit(start),
                           places.context(start, end), message, ref=word,
                           line=lines[0]))
    return out


# --------------------------------------------------------- doubled words

_DOUBLED = re.compile(r"\b([^\W\d_]+(?:['’][^\W\d_]+)*)([ \t]+|[ \t]*\n[ \t]*)"
                      r"\1\b(?![-'’])", re.IGNORECASE)
# Pairs English writes on purpose.
DOUBLED_ALLOWED = frozenset(("had", "that", "is", "do", "so", "very", "no",
                             "ha", "her", "more", "there"))


def check_doubled_words(text: str) -> list[Finding]:
    """The same word twice in a row, within a paragraph. Left alone:
    the pairs of `DOUBLED_ALLOWED`, a pair with punctuation between, and
    words in italics (*neti neti*)."""
    prose = masked(text, italics=True)
    # A heading is its own unit: its last word and the next line's first
    # are not a pair.
    prose = re.sub(r"^[ \t]*#[^\n]*$", lambda m: " " * len(m.group(0)),
                   prose, flags=re.MULTILINE)
    places = _Places(text)
    out = []
    for m in _DOUBLED.finditer(prose):
        word = m.group(1)
        if word.lower() in DOUBLED_ALLOWED or len(word) < 2:
            continue
        if m.start() and prose[m.start() - 1] in "-'’":
            continue
        out.append(Finding(
            "doubled-word", "warning", places.unit(m.start()),
            places.context(m.start(), m.end()),
            f"'{word}' twice in a row", ref=f"{word} {word}".lower(),
            line=places.line(m.start())))
    return out


# ------------------------------------------------------ spelling variant

_VARIANT_NAME = {"us": "American", "uk": "British"}


def variant_counts(text: str) -> dict[str, int]:
    """How many words of each variant's own spelling the text holds:
    `{"us": n, "uk": n}`. The larger one is the variant the text is
    mostly written in."""
    book = dictionary("us")
    counts = {"us": 0, "uk": 0}
    for m in _TOKEN.finditer(masked(text, italics=True)):
        word = _forms(m.group(0).replace("’", "'"))[-1]
        low = word.lower()
        if low in book.own:
            counts["us"] += 1
        elif low in book.other and not _in_dictionary(word, low,
                                                      book.words):
            counts["uk"] += 1
    return counts


# How the two variants differ, British first. Used only to offer the
# other spelling; which words ARE British or American is the word lists'.
_VARIANT_PAIRS = (
    ("isation", "ization"), ("ise", "ize"), ("ising", "izing"),
    ("yse", "yze"), ("ysing", "yzing"), ("our", "or"), ("tre", "ter"),
    ("bre", "ber"), ("ogue", "og"), ("ence", "ense"), ("lled", "led"),
    ("lling", "ling"), ("ller", "ler"), ("pped", "ped"), ("pping", "ping"),
    ("pper", "per"), ("lment", "llment"), ("fil", "fill"), ("ement", "ment"),
    ("ae", "e"), ("oe", "e"), ("grey", "gray"), ("mme", "m"), ("que", "ck"),
    ("ilt", "iled"), ("lt", "led"), ("nt", "ned"), ("axe", "ax"),
    ("sc", "sk"), ("ou", "o"), ("ngst", "ng"), ("ilst", "ile"))


def other_spelling(low: str, variant: str) -> str | None:
    """`low`, a word of the other variant, as `variant` spells it, or
    None when no known difference between the two gives a word."""
    book = dictionary(variant)
    for british, american in _VARIANT_PAIRS:
        old, new = ((british, american) if variant == "us"
                    else (american, british))
        at = low.find(old)
        while at != -1:
            candidate = low[:at] + new + low[at + len(old):]
            if candidate in book.words and candidate not in book.other:
                return candidate
            at = low.find(old, at + 1)
    return None


def check_spelling_variant(text: str, variant: str = "us",
                           accepted: frozenset[str] = frozenset()
                           ) -> list[Finding]:
    """One finding per word of the other variant per file. A block
    quotation keeps its source's spelling and is not read; a word the
    manuscript accepts (`accepted`, casefolded) is the author's choice."""
    book = dictionary(variant)
    prose = masked(text, italics=True)
    prose = re.sub(r"^[ \t]*>[^\n]*$", lambda m: " " * len(m.group(0)),
                   prose, flags=re.MULTILINE)
    places = _Places(text)
    seen: dict[str, list[int]] = {}
    first: dict[str, tuple[int, int]] = {}
    for m in _TOKEN.finditer(prose):
        word = _forms(m.group(0).replace("’", "'"))[-1]
        low = word.lower()
        if (low in book.other and low not in accepted
                and not _in_dictionary(word, low, book.words)):
            seen.setdefault(low, []).append(places.line(m.start()))
            first.setdefault(low, (m.start(), m.end()))
    other = "British" if variant == "us" else "American"
    out = []
    for low, lines in seen.items():
        start, end = first[low]
        mine = other_spelling(low, variant)
        message = (f"'{low}' is a {other} spelling in a manuscript set to "
                   f"{_VARIANT_NAME[variant]}")
        if mine:
            message += f"; {_VARIANT_NAME[variant]}: {mine}"
        if len(lines) > 1:
            message += (f" ({len(lines)} times in this file, lines "
                        + ", ".join(str(n) for n in lines[:8])
                        + ("…" if len(lines) > 8 else "") + ")")
        out.append(Finding("spelling-variant", "warning",
                           places.unit(start), places.context(start, end),
                           message, ref=low, line=lines[0]))
    return out


# ----------------------------------------------------------- quote style

_QUOTE_LAW = re.compile(r"\bquot(?:ation marks?|es?)\b", re.IGNORECASE)


def quote_law(statements: list[str]) -> str | None:
    """`curly` or `straight` when a style law states the book's quote
    marks, else None. The nearest law that speaks wins (the caller
    passes them nearest first)."""
    for statement in statements:
        if not _QUOTE_LAW.search(statement or ""):
            continue
        curly = re.search(r"\bcurly\b|\btypographic\b|\bsmart\b",
                          statement, re.IGNORECASE)
        straight = re.search(r"\bstraight\b", statement, re.IGNORECASE)
        if curly and not straight:
            return "curly"
        if straight and not curly:
            return "straight"
        if curly and straight:
            return "curly" if curly.start() < straight.start() else "straight"
    return None


_STRAIGHT = re.compile(r"[\"']")
_CURLY = re.compile(r"[“”‘’]")


def check_quote_style(text: str, law: str | None = None) -> list[Finding]:
    """Per paragraph: the quote marks of the wrong style (the law's, or
    with no law the style the file uses less), and a double quote mark
    with no partner. A speech carried over several paragraphs opens each
    one and closes only the last; that is not unbalanced."""
    prose = masked(text)
    places = _Places(text)
    spans = _paragraph_spans(prose)
    straight_n = len(_STRAIGHT.findall(prose))
    curly_n = len(_CURLY.findall(prose))
    if law == "curly":
        wrong, name = _STRAIGHT, "straight"
    elif law == "straight":
        wrong, name = _CURLY, "curly"
    elif straight_n and curly_n:
        wrong, name = ((_STRAIGHT, "straight") if straight_n <= curly_n
                       else (_CURLY, "curly"))
    else:
        wrong, name = None, ""
    why = (f"the style law sets {law} quote marks" if law else
           "the file mixes straight and curly quote marks; this is the "
           "style it uses less")
    out = []
    for k, (lo, hi) in enumerate(spans):
        para = prose[lo:hi]
        if wrong is not None:
            hits = list(wrong.finditer(para))
            if hits:
                at = lo + hits[0].start()
                out.append(Finding(
                    "quote-style", "warning", places.unit(lo),
                    places.context(at, at + 1),
                    f"{len(hits)} {name} quote mark"
                    f"{'s' if len(hits) != 1 else ''} in this paragraph "
                    f"({why})", ref=name, line=places.line(at)))
        opens, closes = para.count("“"), para.count("”")
        carried = (opens == closes + 1 and k + 1 < len(spans)
                   and prose[spans[k + 1][0]:spans[k + 1][1]]
                   .lstrip(" \t>*_").startswith("“"))
        if opens != closes and not carried:
            mark = "“" if opens > closes else "”"
            at = lo + (para.rfind(mark) if opens > closes
                       else para.find(mark))
            out.append(Finding(
                "quote-style", "warning", places.unit(lo),
                places.context(at, at + 1),
                f"unbalanced quote marks: {opens} opening “ and {closes} "
                f"closing ” in this paragraph", ref="unbalanced",
                line=places.line(at)))
        if para.count('"') % 2:
            at = lo + para.rfind('"')
            out.append(Finding(
                "quote-style", "warning", places.unit(lo),
                places.context(at, at + 1),
                f"unbalanced quote marks: {para.count(chr(34))} straight "
                f"double quote marks in this paragraph", ref="unbalanced",
                line=places.line(at)))
    out.sort(key=lambda f: f.line)
    return out


# ------------------------------------------------------------- footnotes

_FN_DEF = re.compile(r"^[ \t]{0,3}\[\^([^\]\s]+)\]:", re.MULTILINE)
_FN_MARK = re.compile(r"\[\^([^\]\s]+)\](?!:)")


def check_footnotes(text: str) -> list[Finding]:
    """Within one file: a marker whose label is never defined (error), a
    label defined twice (error, at the second definition), a definition
    no marker points at (warning)."""
    prose = masked(text, tags=False, footnotes=False)
    places = _Places(text)
    defs: dict[str, list[int]] = {}
    for m in _FN_DEF.finditer(prose):
        defs.setdefault(m.group(1), []).append(m.start())
    def_starts = {at for ats in defs.values() for at in ats}
    marks: dict[str, list[int]] = {}
    for m in _FN_MARK.finditer(prose):
        line_start = prose.rfind("\n", 0, m.start()) + 1
        if line_start + len(prose[line_start:m.start()]) - len(
                prose[line_start:m.start()].lstrip()) in def_starts \
                and not prose[line_start:m.start()].strip():
            continue                     # a definition's own label
        marks.setdefault(m.group(1), []).append(m.start())
    out = []

    def add(severity: str, at: int, label: str, message: str) -> None:
        out.append(Finding("footnote", severity, places.unit(at),
                           places.context(at, at + len(label) + 3, 30),
                           message, ref=f"[^{label}]", line=places.line(at)))

    for label, ats in marks.items():
        if label not in defs:
            add("error", ats[0], label,
                f"footnote marker [^{label}] has no definition in this "
                f"file" + (f" ({len(ats)} markers)" if len(ats) > 1 else ""))
    for label, ats in defs.items():
        for at in ats[1:]:
            add("error", at, label,
                f"footnote [^{label}] is defined more than once (first at "
                f"line {places.line(ats[0])})")
        if label not in marks:
            add("warning", ats[0], label,
                f"footnote [^{label}] is defined and no marker points at "
                f"it")
    out.sort(key=lambda f: f.line)
    return out


# ------------------------------------------------------------ dash form

_DASH_LAW = re.compile(r"\bdash(?:es)?\b", re.IGNORECASE)
# A run of dash marks with any spaces or tabs around it, between two
# characters on the same line. Each match is judged by `_dash_fault`.
_DASH_RUN = re.compile(r"(?<=\S)[ \t]*(?:-{1,3}|–|—)[ \t]*(?=\S)")
_NOT_PROSE = set("|-:<>!=*_#+`~")


_DASH_FORM = re.compile(
    r"\b(?P<neg>(?:not|never|no)\s+(?:an?\s+)?)?(?P<form>closed|unspaced|"
    r"spaced)\s+(?:\(?[—–-]\)?\s+)?(?:em[\s-]+)?dash", re.IGNORECASE)
_DASH_NOT = re.compile(r"\b(?:not|never)\s+(?P<form>closed|unspaced|spaced)\b",
                       re.IGNORECASE)


def _dash_form_of(statement: str) -> str | None:
    """The em dash form one statement sets, or None when it sets none or
    says both. The form named on the dash itself ('a spaced em dash',
    'closed em dash') decides; a negated one ('not closed', 'never a
    spaced dash') counts for the other form; loose words ('no spaces',
    'spaced') decide only when they all point one way."""
    def norm(form: str) -> str:
        return "closed" if form.lower() in ("closed", "unspaced") else "spaced"

    def flip(form: str) -> str:
        return "spaced" if form == "closed" else "closed"

    named = set()
    for m in _DASH_FORM.finditer(statement):
        form = norm(m.group("form"))
        named.add(flip(form) if m.group("neg") else form)
    for m in _DASH_NOT.finditer(statement):
        named.add(flip(norm(m.group("form"))))
    if len(named) == 1:
        return named.pop()
    if named:
        return None                        # says both: not read
    closed = re.search(r"\bclosed\b|\bno spaces?\b|\bunspaced\b|"
                       r"\bwithout spaces?\b", statement, re.IGNORECASE)
    spaced = re.search(r"\bspaced\b|(?<!\bno )(?<!\bwithout )\bspaces? "
                       r"(?:on|either|around|both)\b",
                       statement, re.IGNORECASE)
    if closed and spaced:
        return None
    return "closed" if closed else "spaced" if spaced else None


def dash_law(statements: list[str]) -> str | None:
    """`closed` or `spaced` when a style law states the em dash's form,
    else None. The nearest law that speaks wins (the caller passes them
    nearest first); a law that names both forms without saying which is
    the em dash's is not read. The house default
    (library/style-defaults.toml): "a closed em dash (—), no spaces"."""
    for statement in statements:
        if not _DASH_LAW.search(statement or ""):
            continue
        form = _dash_form_of(statement)
        if form:
            return form
    return None


def _dash_fault(text: str, m: re.Match, style: str) -> tuple[str, str] | None:
    """(what is wrong, the dash it should be) for one dash run, or None
    when it is not a dash fault (a hyphen in a word, a range, a table)."""
    run = m.group(0)
    mark = run.strip(" \t")
    spaced = run != mark
    left, right = text[m.start() - 1], text[m.end()]
    if left in _NOT_PROSE or right in _NOT_PROSE:
        return None
    want = "—" if style == "closed" else " — "
    if mark == "-":
        if not (run.startswith((" ", "\t")) and run.endswith((" ", "\t"))):
            return None                    # a hyphen: well-known, 3-5
        if left.isdigit() and right.isdigit():
            return None                    # arithmetic, left to a reader
        return "a spaced hyphen typed for a dash", want
    if mark in ("--", "---"):
        return "a double hyphen typed for a dash", want
    if mark == "–":
        if not spaced or (left.isdigit() and right.isdigit()):
            return None                    # a range, or a joined pair
        return "a spaced en dash used for a dash", want
    if style == "closed" and spaced:
        return "a spaced em dash (the form is closed: a—b)", want
    if style == "spaced" and not spaced:
        return "a closed em dash (the form is spaced: a — b)", want
    return None


def dash_style(text: str, law: str | None = None) -> str:
    """The em dash form a file is held to: the law's, else closed (the
    house form). Never the file's own majority: a file that drifted to
    spaced dashes is not held to its drift. `text` is kept for callers."""
    del text
    return law if law in ("closed", "spaced") else "closed"


def dash_faults(text: str, law: str | None = None
                ) -> list[tuple[int, int, str, str]]:
    """Every dash of the wrong form, in prose only (code, links, math,
    tags and front matter left alone): (start, end, the dash it should be,
    what is wrong). A hyphen inside a word, a range (1914–1918) and a
    joined en dash pair (Smith–Jones) are not faults."""
    style = dash_style(text, law)
    prose = masked(text)
    out = []
    for m in _DASH_RUN.finditer(prose):
        fault = _dash_fault(prose, m, style)
        if fault:
            out.append((m.start(), m.end(), fault[1], fault[0]))
    return out


def check_dash_form(text: str, law: str | None = None) -> list[Finding]:
    """Per paragraph: the dashes of the wrong form (`dash_faults`), one
    finding naming the first and the count. A mechanical fix: `book lint
    --fix` and the Lint view apply it in bulk."""
    style = dash_style(text, law)
    places = _Places(text)
    by_unit: dict[int, list] = {}
    for fault in dash_faults(text, law):
        by_unit.setdefault(places.unit(fault[0]), []).append(fault)
    why = (f"the style law sets a {style} em dash" if law else
           f"the house form is a {style} em dash")
    out = []
    for unit, faults in sorted(by_unit.items()):
        lo, hi, _new, what = faults[0]
        n = len(faults)
        out.append(Finding(
            "dash-form", "warning", unit, places.context(lo, hi),
            what + (f", and {n - 1} more dash"
                    f"{'es' if n > 2 else ''} of the wrong form"
                    if n > 1 else "")
            + f" in this paragraph ({why})", ref=style,
            line=places.line(lo)))
    return out


# ---------------------------------------------------------- link tracking

_LINK = re.compile(r"https?://[^\s<>\[\]\"“”]+")
_TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "oly_")
TRACKING_PARAMS = frozenset(
    "fbclid gclid gclsrc dclid gbraid wbraid msclkid mc_cid mc_eid igshid "
    "igsh yclid twclid ttclid li_fat_id _hsenc _hsmi hsctatracking "
    "mkt_tok ref_src ref_url s_cid vero_id wt_mc spm si srsltid _ga _gl "
    "_openstat".split())


def check_link_tracking(text: str) -> list[Finding]:
    places = _Places(text)
    out = []
    for m in _LINK.finditer(text):
        url = m.group(0).rstrip(".,;:!?)'’”")
        if url.count("(") < url.count(")"):
            url = url.rstrip(")")
        query = urlsplit(url).query
        names = [name for name, _ in parse_qsl(query, keep_blank_values=True)
                 if name.lower() in TRACKING_PARAMS
                 or name.lower().startswith(_TRACKING_PREFIXES)]
        if names:
            out.append(Finding(
                "link-tracking", "warning", places.unit(m.start()), url,
                "the link carries tracking tags: "
                + ", ".join(dict.fromkeys(names)), ref=url,
                line=places.line(m.start())))
    return out


# ----------------------------------------------------------- banned words


@dataclass(frozen=True)
class Ban:
    term: str       # the words banned, as the law writes them
    law: str        # the law's statement


_QUOTED_TERM = (r"(?:'((?:[^'\n]|'(?=\w))+)'|‘((?:[^‘’\n]|’(?=\w))+)’"
                r"|\"([^\"\n]+)\"|“([^“”\n]+)”)")
_BAN = re.compile(
    r"\b(?:never|avoid|banned|do not (?:use|write|say)"
    r"|don[’']t (?:use|write|say))"
    r"(?:\s+(?:plain|bare|use|write|say|using|writing|saying"
    r"|the\s+words?|the\s+phrases?|the\s+terms?))*\s*:?\s*"
    r"(?=['‘\"“])", re.IGNORECASE)
_BAN_MORE = re.compile(r"\s*(?:,\s*(?:or|and|nor)?|or|and|nor)\s*(?=['‘\"“])")
_BAN_HEADLINE = re.compile(r"\ANo ([A-Z][\w’'-]*(?: [A-Z][\w’'-]*){0,2}):")
_QUOTED_AT = re.compile(_QUOTED_TERM)


def ban_terms(statements: list[str]) -> list[Ban]:
    """The words the laws ban outright, in the laws' order. See the
    module docstring for the grammar and why it is this narrow."""
    out: list[Ban] = []
    seen: set[str] = set()

    def add(term: str, law: str) -> None:
        term = " ".join(term.replace("’", "'").split()).strip(" .,;:")
        if term and term not in seen:
            seen.add(term)
            out.append(Ban(term, law))

    for statement in statements:
        statement = statement or ""
        headline = _BAN_HEADLINE.match(statement)
        if headline:
            add(headline.group(1), statement)
        for m in _BAN.finditer(statement):
            at = m.end()
            while True:
                quoted = _QUOTED_AT.match(statement, at)
                if not quoted:
                    break
                add(next(g for g in quoted.groups() if g), statement)
                more = _BAN_MORE.match(statement, quoted.end())
                if not more:
                    break
                at = more.end()
    return out


def check_banned_words(text: str, bans: list[Ban]) -> list[Finding]:
    """Every use of a banned word. A ban written in lower case matches
    those letters and the same words opening a sentence; a ban with a
    capital matches that capital only ('Hindu' is not 'hindu')."""
    if not bans:
        return []
    prose = masked(text).replace("’", "'")
    places = _Places(text)
    out = []
    for ban in bans:
        body = r"\s+".join(re.escape(w) for w in ban.term.split())
        if ban.term == ban.term.lower() and ban.term[:1].isalpha():
            first = ban.term[0]
            body = f"[{first}{first.upper()}]" + body[len(re.escape(first)):]
        pattern = re.compile(rf"(?<![\w'-]){body}(?![\w-])")
        law = ban.law if len(ban.law) <= 140 else ban.law[:137] + "…"
        for m in pattern.finditer(prose):
            if (m.group(0)[:1] != ban.term[:1]
                    and not _sentence_initial(prose, m.start())):
                continue
            out.append(Finding(
                "banned-word", "warning", places.unit(m.start()),
                places.context(m.start(), m.end()),
                f"'{ban.term}' is banned by the style law: {law}",
                ref=ban.term, line=places.line(m.start())))
    out.sort(key=lambda f: f.line)
    return out


# ------------------------------------------------------------- the entry

def check_rules(rules) -> list[str]:
    """`rules` as a list, each one a proof rule."""
    out = list(rules)
    unknown = [r for r in out if r not in RULES]
    if unknown:
        raise ValueError(f"unknown proof rule: {', '.join(unknown)} "
                         f"(one of: {', '.join(RULES)})")
    return out


def proof_text(text: str, *, lexicon: Lexicon, rules=RULES,
               law: str | None = None,
               bans: list[Ban] | None = None,
               dash: str | None = None) -> list[Finding]:
    """The findings of the named proof rules over one file's text, in
    line order. `law` is the quote style a law sets, `dash` the em dash
    form one sets (`dash_law`)."""
    rules = check_rules(rules)
    out: list[Finding] = []
    if "spelling" in rules:
        out += check_spelling(text, lexicon)
    if "doubled-word" in rules:
        out += check_doubled_words(text)
    if "spelling-variant" in rules:
        out += check_spelling_variant(text, lexicon.variant,
                                      lexicon.accepted)
    if "quote-style" in rules:
        out += check_quote_style(text, law)
    if "dash-form" in rules:
        out += check_dash_form(text, dash)
    if "footnote" in rules:
        out += check_footnotes(text)
    if "link-tracking" in rules:
        out += check_link_tracking(text)
    if "banned-word" in rules:
        out += check_banned_words(text, bans or [])
    out.sort(key=lambda f: (f.line, RULES.index(f.code)))
    return out
