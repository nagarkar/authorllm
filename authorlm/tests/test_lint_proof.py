"""The proof checks of `proof.py`, one rule at a time: spelling against
the shipped word lists, doubled words, the spelling variant, quote marks,
footnotes, tracking tags on links, and the style law's banned words.

Each expectation is written from the rule as the module docstring states
it, on text made up for the case. The shipped word lists are read as they
are (no network, no model); nothing is written anywhere.

Run: python3 tests/test_lint_proof.py
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import authorlm  # noqa: E402

assert Path(authorlm.__file__).resolve().parent == HERE.parent / "authorlm", (
    f"testing the wrong package: {authorlm.__file__}")

from authorlm import lint, proof  # noqa: E402


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _words(findings) -> list[str]:
    return [f.ref for f in findings]


def _spell(text: str, accepted=(), variant: str = "us"):
    return proof.check_spelling(text, proof.Lexicon(
        variant=variant, accepted=proof.accepted_set(accepted)))


# ------------------------------------------------------------- spelling

def test_spelling_finds_a_typo_and_says_where() -> None:
    text = ("# A Heading\n\nThe river ran the long way round.\n\n"
            "Nobody in the village recieved the letter.\n")
    hits = _spell(text)
    check("one finding, the misspelled word", _words(hits) == ["recieved"],
          _words(hits))
    hit = hits[0]
    check("it is a spelling warning", (hit.code, hit.severity)
          == ("spelling", "warning"), (hit.code, hit.severity))
    check("it names line 5 and paragraph 3", (hit.line, hit.unit) == (5, 3),
          (hit.line, hit.unit))
    check("it quotes the words around it", "recieved the letter" in hit.quote,
          hit.quote)
    check("it offers the right spelling", "received" in hit.message,
          hit.message)


def test_spelling_counts_a_repeated_word_once_per_file() -> None:
    hits = _spell("He was definately late.\n\nShe was definately early.\n")
    check("one finding for two uses", _words(hits) == ["definately"],
          _words(hits))
    check("the message counts both and names both lines",
          "2 times" in hits[0].message and "lines 1, 3" in hits[0].message,
          hits[0].message)


def test_spelling_accepts_ordinary_english() -> None:
    text = ("The Herdsman's fire didn't burn; Paris wasn't far. She "
            "re-read the well-known co-author's twenty-first chapter, "
            "the 3rd of its kind, about NATO and the USSR.\n")
    check("inflections, possessives, contractions, names, hyphens, "
          "ordinals and acronyms pass", _words(_spell(text)) == [],
          _words(_spell(text)))


def test_spelling_takes_the_manuscripts_own_words() -> None:
    text = "He holds to upaya and to anatta, as the darshanas do.\n"
    check("without them, the terms are reported",
          _words(_spell(text)) == ["upaya", "anatta", "darshanas"],
          _words(_spell(text)))
    hits = _spell(text, accepted=["upaya", "Anatta", "Chitta Darshana"])
    check("protected terms, concept names and dictionary entries are "
          "accepted in any case, each word of a name on its own, with "
          "its plural", hits == [], _words(hits))


def test_spelling_leaves_markup_alone() -> None:
    text = "\n".join([
        "---", "titel: frontmatter", "---", "",
        "See `teh_code` and https://exampel.com/pagee and "
        "[the site](https://exampel.org/wrongg).[^Zq1]", "",
        "```", "blokc of code", "```", "",
        "[Voice: narratorr]", "",
        "[Suggestion: tihs is a note to the author]", "",
        "[Footnote: Soem source, p. 3]", "",
        "He called it *sunyataa* and _dukkhaa_, in the old tongue.", "",
        "<span class=\"wrongg\">plain</span> text with $x_{abcz}$ math.", "",
        "[^Zq1]: A note with a mispelled word.", ""])
    hits = _spell(text)
    check("front matter, code, URLs, link targets, footnote markers, "
          "directive tags, italics, HTML and math are not read; a "
          "footnote's own text is", _words(hits) == ["mispelled"],
          _words(hits))


def test_spelling_handles_early_modern_english() -> None:
    text = ("He that hath ears, let him hear: the root differeth not, and "
            "whoso knoweth it loveth it. Thou sayest well; thou didst "
            "come. Whither goest thou? 'Tis true, and ye cometh o'er.\n")
    check("-eth and -est forms of known verbs and the old pronouns pass",
          _words(_spell(text)) == [], _words(_spell(text)))
    hits = _spell("He recieveth the letter and wanteth nothing.\n")
    check("an -eth form of a word the dictionary does not know is still "
          "reported", _words(hits) == ["recieveth"], _words(hits))


def test_spelling_leaves_unknown_names_to_the_author() -> None:
    hits = _spell("Then Zorblatt spoke of Nietzsche, and Nietzche answered "
                  "him. Gaye and R. K. Hardie translated it.\n")
    check("a capitalised unknown word is taken as a name, unless it is "
          "one slip away from a word the book uses",
          _words(hits) == ["Nietzche"], _words(hits))
    check("and the near word is offered", "Nietzsche" in hits[0].message,
          hits[0].message)


def test_spelling_skips_the_other_variant() -> None:
    hits = _spell("The colour of the centre was grey.\n")
    check("a British spelling is the variant rule's finding, not a "
          "misspelling", hits == [], _words(hits))


# --------------------------------------------------------- doubled words

def test_doubled_words() -> None:
    text = ("He walked to the the mill.\n\nShe had had enough, and knew "
            "that that was so.\n\nThe end\nend came. Very, very late. "
            "It is 2 2 go.\n")
    hits = proof.check_doubled_words(text)
    check("'the the' and a pair across a line break are found; 'had "
          "had', 'that that', a pair with a comma and digits are not",
          [(f.line, f.ref) for f in hits] == [(1, "the the"),
                                               (5, "end end")],
          [(f.line, f.ref) for f in hits])
    check("the rule and severity", {(f.code, f.severity) for f in hits}
          == {("doubled-word", "warning")})
    hits = proof.check_doubled_words("Only *neti neti* is said.\n")
    check("a doubled word in italics is a borrowed phrase", hits == [],
          [f.ref for f in hits])
    hits = proof.check_doubled_words("# The Fields\n\nFields move not.\n")
    check("a heading and the paragraph under it are not a pair",
          hits == [], [f.ref for f in hits])


# ------------------------------------------------------ spelling variant

def test_spelling_variant() -> None:
    text = "The colour of the center was gray, and he realized it.\n"
    us = proof.check_spelling_variant(text, "us")
    check("under American spelling the British form is reported",
          _words(us) == ["colour"], _words(us))
    check("with the rule name and the other spelling",
          us[0].code == "spelling-variant" and "color" in us[0].message,
          us[0].message)
    uk = proof.check_spelling_variant(text, "uk")
    check("under British spelling the American forms are; -ize is "
          "British too", _words(uk) == ["center", "gray"], _words(uk))


def test_spelling_variant_boundaries() -> None:
    text = ("Caesar wept among the worshippers.\n\n> The colour of a "
            "quoted source stays.\n")
    hits = proof.check_spelling_variant(text, "us")
    check("a name is not a variant, a block quotation keeps its "
          "spelling, and the American form is offered",
          _words(hits) == ["worshippers"] and "worshipers" in hits[0].message,
          [(f.ref, f.message) for f in hits])
    hits = proof.check_spelling_variant(
        text, "us", proof.accepted_set(["worshippers"]))
    check("a word the author accepted is left alone", hits == [],
          _words(hits))
    for british, american in (("grey", "gray"), ("fulfil", "fulfill"),
                              ("spoilt", "spoiled"), ("centre", "center"),
                              ("sceptical", "skeptical")):
        check(f"{british} is offered as {american}",
              proof.other_spelling(british, "us") == american,
              proof.other_spelling(british, "us"))
    check("and back", proof.other_spelling("color", "uk") == "colour")


def test_variant_of_language() -> None:
    check("en-GB is British", proof.variant_of("en-GB") == "uk")
    check("en-US, en and unset are American",
          [proof.variant_of(x) for x in ("en-US", "en", "")] == ["us"] * 3)


# ----------------------------------------------------------- quote style

def test_quote_style_against_the_law() -> None:
    law = proof.quote_law([
        "Use epigraphs to set the tone.",
        "Curly quotation marks, American style: “double” outside."])
    check("the law that states a quote style is found", law == "curly", law)
    check("no law, no style", proof.quote_law(["Use epigraphs."]) is None)
    text = ("He said \"go\" and didn't wait.\n\n“Stay,” she said. "
            "It’s late.\n\nA `\"code\"` span and <a href=\"x\">a link</a>.\n")
    hits = proof.check_quote_style(text, "curly")
    check("one finding for the paragraph with straight marks, counting "
          "them; code and HTML attributes are left alone",
          [(f.line, f.code) for f in hits] == [(1, "quote-style")]
          and "3 straight" in hits[0].message,
          [(f.line, f.message) for f in hits])


def test_quote_style_mixed_without_a_law() -> None:
    text = ("“One,” he said.\n\n“Two,” she said.\n\n\"Three,\" it said.\n")
    hits = proof.check_quote_style(text, None)
    check("with no law the minority style is reported",
          [f.line for f in hits] == [5] and "straight" in hits[0].message,
          [(f.line, f.message) for f in hits])
    check("one style alone is clean",
          proof.check_quote_style("“One,” he said.\n", None) == [])


def test_quote_balance() -> None:
    text = ("“An opening with no close.\n\nA fine “pair” here.\n\n"
            "“A speech that runs on.\n\n“And closes in the next "
            "paragraph.”\n\nA stray \" mark.\n")
    hits = [f for f in proof.check_quote_style(text, None)
            if "unbalanced" in f.message]
    check("an unclosed curly quote and an odd straight quote are "
          "unbalanced; a speech carried over paragraphs is not",
          [f.line for f in hits] == [1, 9], [(f.line, f.message)
                                             for f in hits])


# ------------------------------------------------------------- footnotes

def test_footnotes() -> None:
    text = ("A claim.[^A1] Another.[^A2] A third.[^A1]\n\n"
            "[^A1]: The first source.\n\n[^A3]: Never cited.\n\n"
            "[^A1]: The first source again.\n\n`[^code]` is not one.\n")
    hits = proof.check_footnotes(text)
    got = sorted((f.ref, f.severity, f.line) for f in hits)
    check("a marker with no definition, a definition with no marker and "
          "a second definition, each at its line",
          got == [("[^A1]", "error", 7), ("[^A2]", "error", 1),
                  ("[^A3]", "warning", 5)], got)
    check("all under one rule", {f.code for f in hits} == {"footnote"})
    check("a file with every marker defined once is clean",
          proof.check_footnotes("One.[^a]\n\n[^a]: Note.\n") == [])


# ---------------------------------------------------------- link tracking

def test_link_tracking() -> None:
    text = ("See [the talk](https://example.com/talk?id=7&utm_source=news"
            "&utm_medium=email) and https://youtu.be/abc?si=XyZ and "
            "https://example.com/watch?v=abc&t=30s for more.\n")
    hits = proof.check_link_tracking(text)
    check("two links carry tracking tags; an ordinary query does not",
          len(hits) == 2 and {f.code for f in hits} == {"link-tracking"},
          [(f.quote, f.message) for f in hits])
    check("the tags are named",
          "utm_source, utm_medium" in hits[0].message
          and "si" in hits[1].message, [f.message for f in hits])


# ----------------------------------------------------------- banned words

def test_ban_terms_are_read_from_the_laws() -> None:
    bans = proof.ban_terms([
        "'Ground' is reserved for The Dharma (never 'ground of "
        "existence'). Nothing is the groundless ground; never plain "
        "'the ground'.",
        "Avoid the word \"Hindu\"; prefer geographic phrasings.",
        "Never write the Dead in the possessive: write 'the plea of the "
        "Dead', never 'the Dead's plea'.",
        "No Marx: Marx is not cited, quoted or used as a comparison.",
        "Don't use the words ‘utilize’, ‘leverage’ or ‘impactful’.",
        "Name the thing itself, never a stand-in such as 'this' or "
        "'things'.",
        "Don't start sentences with the word 'what'.",
        "Use epigraphs to set the tone.",
    ])
    got = [b.term for b in bans]
    check("only the words a law bans outright, in the law's order",
          got == ["ground of existence", "the ground", "Hindu",
                  "the Dead's plea", "Marx", "utilize", "leverage",
                  "impactful"], got)


def test_banned_words() -> None:
    bans = proof.ban_terms([
        "Never plain 'the ground'.", "Avoid the word \"Hindu\"."])
    text = ("He stood on the ground. The ground held.\n\nThe Ground of "
            "Creation is not the groundless ground.\n\nA Hindu temple, "
            "and a hindu rite.\n")
    hits = proof.check_banned_words(text, bans)
    got = [(f.line, f.ref) for f in hits]
    check("each use is found with its line; a lower-case ban also "
          "matches at a sentence's start; a capitalised ban matches "
          "that capital only; a longer word is not a match",
          got == [(1, "the ground"), (1, "the ground"), (5, "Hindu")], got)
    check("the finding names the law", "Never plain 'the ground'."
          in hits[0].message and hits[0].code == "banned-word",
          hits[0].message)


# ------------------------------------------------------------- the entry

def test_rules_are_selectable() -> None:
    text = "He walked to the the mill and recieved a colour.[^x]\n"
    lexicon = proof.Lexicon(variant="us", accepted=frozenset())
    everything = proof.proof_text(text, lexicon=lexicon)
    check("every rule runs by default",
          sorted({f.code for f in everything}) == [
              "doubled-word", "footnote", "spelling", "spelling-variant"],
          sorted({f.code for f in everything}))
    one = proof.proof_text(text, lexicon=lexicon, rules=["footnote"])
    check("a rule runs alone when named",
          {f.code for f in one} == {"footnote"})
    check("the rule list is the eight proof checks", proof.RULES == (
        "spelling", "doubled-word", "spelling-variant", "quote-style",
        "dash-form", "footnote", "link-tracking", "banned-word"),
        proof.RULES)
    check("findings are lint findings",
          all(isinstance(f, lint.Finding) for f in everything))


def test_the_nine_checks_do_not_run_the_proof_checks() -> None:
    report = lint.lint_text("He walked to the the mill and recieved it.")
    check("lint_text is unchanged: no proof rule in its report",
          not set(report.codes()) & set(proof.RULES), report.codes())


def test_accepted_words_file() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        check("no file, no words", proof.read_accepted(root) == [])
        added = proof.add_accepted(root, ["Zorblatt", "upaya", "Zorblatt"])
        check("new words are added once", added == ["Zorblatt", "upaya"],
              added)
        check("a word already there is not added again",
              proof.add_accepted(root, ["upaya", "anatta"]) == ["anatta"])
        path = root / proof.ACCEPTED_FILE
        check("the file sits in the manuscript's _lint folder, one word "
              "per line", path.read_text(encoding="utf-8").splitlines()[-3:]
              == ["Zorblatt", "upaya", "anatta"], path.read_text())
        path.write_text(path.read_text() + "# a comment\n\nkoan\n")
        check("comments and blank lines are skipped",
              proof.read_accepted(root) == ["Zorblatt", "upaya", "anatta",
                                            "koan"],
              proof.read_accepted(root))
        try:
            proof.add_accepted(root, ["two words"])
            ok = False
        except ValueError:
            ok = True
        check("a phrase is not a word", ok)


def test_the_word_lists_have_provenance() -> None:
    folder = HERE.parent / "authorlm" / "spelling"
    lists = sorted(p.name for p in folder.glob("*.txt.gz"))
    check("three lists are shipped", lists == [
        "en-common.txt.gz", "en-gb-only.txt.gz", "en-us-only.txt.gz"], lists)
    note = (folder / "PROVENANCE.md").read_text(encoding="utf-8")
    import hashlib

    for name in lists:
        digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        check(f"{name} is the file PROVENANCE.md describes",
              digest in note)


def main() -> None:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
