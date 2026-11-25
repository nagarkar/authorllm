"""The Lint view of the Filters page (`lint_page.dispatch`, reached as the
`lint.*` methods of `/api/runs`): run lint on chosen tabs, apply the
mechanical fixes in bulk, rule on the rest one by one, and undo.

Invariant: lint itself writes nothing and calls no model (proof.py,
lint.py). This page writes only on the author's click, and only in four
places: the chapter file (and its Doc tab, through the guarded push), the
manuscript's accepted-words file (`changes.lint_accept`), its ignore list
(`IGNORE_FILE`), and the `lint_changes` log. A fix never becomes a form:
it goes straight into the text.

Decisions:
- Lint fixes the author accepts here go directly into the text and the
  Doc, spelling corrections included; no form is staged for them. Why:
  the owner's words (2026-11-25): "Lint output should be reviewed in the
  UI if possible (e.g. accepting spelling corrections) or just fixed
  (e.g. quote style) directly in the doc by clicking an 'apply' button."
  It extends his punctuation exception of 2026-11-11 ("a punctuation fix
  or a ratified punctuation preference ... is applied directly even after
  the first draft, then pushed"). (owner) Enforced by:
  tests/test_lint_page.py::apply_goes_into_the_text_not_a_form.
- Bulk is for mechanical standardization only, one click per rule (per
  tab or across the chosen tabs) after a count and five before/after
  pairs: straight to curly quote marks (or the reverse, as the style law
  sets), tracking tags taken out of links, and a spelling variant PER
  WORD. Spelling, unbalanced quote marks, doubled words, footnotes,
  banned words, em-dash stacks and the style checks are ruled one by
  one. Why: the owner (2026-11-25): "I don't want to review each quote
  change or other formatting standardization one at a time; it has to be
  bulk. Bulk is not possible for spellings and things like 'unbalanced
  quotes'; I have to rule on it one by one." (owner) Enforced by:
  tests/test_lint_page.py::the_actions_follow_the_rule.
- Em-dash warnings get a count and nothing else: whether a dash's aside
  is expendable is a reader's judgment, which a chat session makes. Why:
  the owner (2026-11-25): "in the UI, we don't have the chat session
  connected, so those warnings will be ignored and that's ok." (owner)
- One tab, one road: every accepted fix for a tab is applied in one
  pull, edit, guarded push. A tab linked to the Doc is read three ways
  first; changed on both sides, or holding an older text of the file, or
  a push the guard stops, and NOTHING is applied to that tab (the file is
  put back as it was). Enforced by:
  tests/test_lint_page.py::a_guard_applies_nothing_for_the_tab.
- A pending form in the tab: the push cannot go while forms are out, so
  the forms are withdrawn and restaged with the fix carried into their
  old and new texts (the skill's stale-form road: decline, merge, fix,
  push, `revise stage`). That is done only when it is safe: every form
  in the tab is an outside caller's (`revise stage`), none is ruled on
  the Merge page or edited in the Doc, and each fix inside a form's old
  text is a mechanical or word fix that lies wholly inside it. Anything
  else leaves the fix unapplied, "blocked by a pending form", with the
  form's origin. Enforced by:
  tests/test_lint_page.py::a_fix_inside_a_pending_form_is_restaged.

Terms:
- **fix**: one accepted change as the page sends it: `curl` (quote marks
  to the file's style), `untrack` (tracking tags out of the links),
  `word` (every use of one word in the file to another), or `paragraph`
  (one paragraph's text replaced, for an inline edit).
- **ignore list**: `<manuscript>/_lint/ignored-findings.tsv`, the
  findings the author marked as a legitimate use; lint on this page
  leaves them out. One per line: id, rule, file, the words quoted.
- **lint change**: one paragraph a fix changed, logged in `lint_changes`
  with its text before and after; Undo puts the before back through the
  same road.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from . import api, changes, gdocs, proof
from .db import Database, ko_fields, now_iso

IGNORE_FILE = "_lint/ignored-findings.tsv"
_IGNORE_HEADER = (
    "# Lint findings marked as a legitimate use on the Filters page's Lint\n"
    "# view; lint there leaves them out. One per line, tab-separated: id,\n"
    "# rule, file, the words quoted. Delete a line to see it again.\n")

# How each rule is acted on. The page shows the rare, one-by-one rules
# first and the bulk ones after them (owner's ask of 2026-11-25).
ONE_BY_ONE = ("spelling", "doubled-word", "unbalanced-quotes", "footnote",
              "banned-word", "em-dash-stack")
STYLE_ONE_BY_ONE = ("what-opener", "this-not-that", "term-in-definition",
                    "long-sentence", "reading-grade", "overlap",
                    "redefinition", "italic-term")
BULK = ("spelling-variant", "quote-style", "link-tracking")
COUNT_ONLY = ("em-dash",)
ORDER = ONE_BY_ONE + STYLE_ONE_BY_ONE + BULK + COUNT_ONLY
FIX_KINDS = ("curl", "untrack", "word", "paragraph")
PREVIEW_PAIRS = 5


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _page_rule(row: dict) -> str:
    """The page's rule for a lint row: quote-style splits in two, the
    mixed style (bulk) and an unbalanced mark (one by one)."""
    if row["code"] == "quote-style" and row.get("ref") == "unbalanced":
        return "unbalanced-quotes"
    return row["code"]


def finding_id(rule: str, file: str, row: dict) -> str:
    """Stable across runs while the words are: a spelling or variant
    finding is its word in its file; the rest are their words quoted."""
    if rule in ("spelling", "spelling-variant"):
        basis = f"{rule}|{file}|{row.get('ref', '')}"
    else:
        basis = f"{rule}|{file}|{_flat(row.get('quote', ''))}|{row.get('ref', '')}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------ paragraphs

_PARA = re.compile(r"(?:\A|(?<=\n))[ \t]*\S[^\n]*(?:\n[ \t]*\S[^\n]*)*")


def paragraph_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _PARA.finditer(text)]


def paragraph_at_line(text: str, line: int) -> tuple[int, int] | None:
    """The span of the paragraph that holds 1-based `line`."""
    if line < 1:
        return None
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    if line > len(starts):
        return None
    at = starts[line - 1]
    for lo, hi in paragraph_spans(text):
        if lo <= at <= hi:
            return lo, hi
    return None


# ------------------------------------------------------------ ignore list

def ignored(root: Path) -> dict[str, str]:
    """id -> rule, from the manuscript's ignore list."""
    path = root / IGNORE_FILE
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[0].strip()] = parts[1].strip()
    return out


def ignore(root: Path, fid: str, rule: str, file: str, quote: str) -> dict:
    """Mark one finding a legitimate use, once."""
    if not re.fullmatch(r"[0-9a-f]{12}", fid or ""):
        raise ValueError("a finding id is twelve hex characters")
    path = root / IGNORE_FILE
    if fid in ignored(root):
        return {"path": str(path), "added": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.is_file() else _IGNORE_HEADER
    with path.open("a", encoding="utf-8") as out:
        out.write(head + "\t".join([fid, rule, file, _flat(quote)
                                    .replace("\t", " ")]) + "\n")
    return {"path": str(path), "added": True}


# ------------------------------------------------------------------ run

def _quote_target(db: Database, manuscript: dict, rel: str,
                  text: str) -> tuple[str | None, dict | None]:
    """The style a quote-style fix moves the file to, and the law that
    sets it (id and words), or None for both when the file is not mixed
    and no law speaks. With no law the file moves to the style it uses
    more, the same choice `proof.check_quote_style` makes."""
    from . import styles

    laws = styles.effective_style(db, manuscript["id"], rel)
    law = None
    for row in laws:
        if proof.quote_law([row["statement"]]):
            law = {"id": row.get("id"), "statement": row["statement"]}
            break
    style = proof.quote_law([r["statement"] for r in laws])
    if style:
        return style, law
    prose = proof.masked(text)
    straight = len(proof._STRAIGHT.findall(prose))
    curly = len(proof._CURLY.findall(prose))
    if straight and curly:
        return ("straight" if straight > curly else "curly"), None
    return None, None


def run(db: Database, manuscript: dict, files: list[str],
        rules: list[str] | None = None) -> dict:
    """Lint the chosen tabs. Per rule: its count, how it is acted on, and
    its findings (em-dash warnings: the count only). Nothing written."""
    from . import critic

    if not files:
        raise ValueError("choose at least one tab")
    wanted = changes.lint_rules(rules or None, None,
                                default=proof.RULES + changes.LINT_CODES)
    root = Path(manuscript["path"])
    known = critic.book(manuscript)[0]
    lexicon = changes.proof_lexicon(db, manuscript)
    skip = ignored(root)
    by_rule: dict[str, list[dict]] = {}
    hidden: dict[str, int] = {}
    quote_laws: dict[str, dict] = {}
    for query in files:
        rel = changes.docs._resolve_relpath(manuscript, query)
        text = known.get(rel)
        if text is None:
            text = (root / rel).read_text(encoding="utf-8")
        found = []
        if any(r in changes.LINT_CODES for r in wanted):
            found += critic.lint_draft(db, manuscript, rel, text,
                                       include_self=False).findings
        found += changes._proof_findings(db, manuscript, rel, text, wanted,
                                         lexicon)
        style, law = _quote_target(db, manuscript, rel, text)
        if law:
            quote_laws[rel] = law
        for row in changes._rows(text, found, wanted):
            rule = _page_rule(row)
            fid = finding_id(rule, rel, row)
            if fid in skip:
                hidden[rule] = hidden.get(rule, 0) + 1
                continue
            item = {"id": fid, "rule": rule, "file": rel, **row}
            if rule in COUNT_ONLY:
                by_rule.setdefault(rule, []).append({"id": fid})
                continue
            item.update(_actions(rule, row, text, lexicon, style))
            by_rule.setdefault(rule, []).append(item)
    groups = []
    for rule in sorted(by_rule, key=lambda r: ORDER.index(r)
                       if r in ORDER else len(ORDER)):
        items = by_rule[rule]
        group = {"rule": rule, "count": len(items),
                 "act": ("bulk" if rule in BULK else "count" if rule in
                         COUNT_ONLY else "one"),
                 "ignored": hidden.get(rule, 0),
                 "findings": [] if rule in COUNT_ONLY else items}
        if rule == "em-dash":
            from .lint import EM_DASH_LAW
            group["note"] = (
                "Each of these needs a reader's judgment (does the dash "
                "explain something inline, and could it be cut?). A chat "
                "session handles them; this page takes no action. The law: "
                + EM_DASH_LAW)
        groups.append(group)
    return {"manuscript": manuscript["name"],
            "files": [changes.docs._resolve_relpath(manuscript, f)
                      for f in files],
            "rules": wanted, "groups": groups,
            "counts": {g["rule"]: g["count"] for g in groups},
            "ignored": hidden, "quote_laws": quote_laws,
            "ignore_file": str(root / IGNORE_FILE),
            "accepted_file": str(root / proof.ACCEPTED_FILE)}


def _actions(rule: str, row: dict, text: str, lexicon,
             style: str | None) -> dict:
    """What the page offers for one finding: the paragraph to edit, and
    the ready-made fixes."""
    out: dict[str, Any] = {}
    span = paragraph_at_line(text, row.get("line") or 0)
    if span:
        out["paragraph"] = text[span[0]:span[1]]
    if rule == "spelling":
        word = row.get("ref") or ""
        out["word"] = word
        out["suggestions"] = proof.suggestions(word, lexicon)
        out["uses"] = len(word_edits(text, word, word, fold=False))
    elif rule == "spelling-variant":
        word = row.get("ref") or ""
        out["word"] = word
        out["to"] = proof.other_spelling(word, lexicon.variant)
        out["uses"] = len(word_edits(text, word, word, fold=True))
    elif rule == "doubled-word" and span:
        pair = (row.get("ref") or "").split()
        if pair:
            para = text[span[0]:span[1]]
            m = re.search(rf"\b({re.escape(pair[0])})(\s+)\1\b", para,
                          re.IGNORECASE)
            if m:
                out["suggested"] = para[:m.start(2)] + para[m.end():]
    elif rule == "quote-style":
        out["style"] = style
    return out


# ----------------------------------------------------------- the fixes

def _curl_one(text: str, i: int) -> str:
    """The curly mark for the straight mark at `i`."""
    ch = text[i]
    # Emphasis marks are looked through: *"Quote."* opens after the
    # asterisk as it would after the space before it.
    j = i - 1
    while j >= 0 and text[j] in "*_":
        j -= 1
    prev = text[j] if j >= 0 else ""
    nxt = text[i + 1] if i + 1 < len(text) else ""
    opening_after = "([{—–-/“‘>"
    if ch == '"':
        return "“" if (not prev or prev.isspace() or prev in opening_after) \
            else "”"
    if prev and (prev.isalnum() or prev in ".,;:!?)]’”%"):
        return "’"
    if nxt.isdigit() or re.match(r"(?:tis|twas|em|til|cause|n)\b",
                                 text[i + 1:i + 7], re.IGNORECASE):
        return "’"                         # 'tis, '90s, rock 'n' roll
    return "‘" if nxt and not nxt.isspace() else "’"


def curl_edits(text: str, style: str | None) -> list[tuple[int, int, str]]:
    """Every quote mark of the wrong style turned to `style`, in prose
    only (code, links, math, front matter and tags left alone)."""
    if style not in ("curly", "straight"):
        return []
    prose = proof.masked(text)
    out = []
    for i, ch in enumerate(text):
        if prose[i] != ch:
            continue
        if style == "curly" and ch in "\"'":
            out.append((i, i + 1, _curl_one(text, i)))
        elif style == "straight" and ch in "“”‘’":
            out.append((i, i + 1, '"' if ch in "“”" else "'"))
    return out


def untrack_edits(text: str) -> list[tuple[int, int, str]]:
    """Each link with tracking tags, without them (the rest of its query,
    its order and its fragment kept)."""
    out = []
    for m in proof._LINK.finditer(text):
        url = m.group(0).rstrip(".,;:!?)'’”")
        if url.count("(") < url.count(")"):
            url = url.rstrip(")")
        base, hash_, fragment = url.partition("#")
        head, q, query = base.partition("?")
        if not q:
            continue
        kept = []
        for part in query.split("&"):
            name = parse_qsl(part, keep_blank_values=True)
            key = (name[0][0] if name else part.split("=", 1)[0]).lower()
            if key in proof.TRACKING_PARAMS or key.startswith(
                    proof._TRACKING_PREFIXES):
                continue
            kept.append(part)
        if len(kept) == len(query.split("&")):
            continue
        new = head + ("?" + "&".join(kept) if kept else "") + hash_ + fragment
        out.append((m.start(), m.start() + len(url), new))
    return out


def _cased(match: str, to: str) -> str:
    if match.isupper() and len(match) > 1:
        return to.upper()
    if match[:1].isupper():
        return to[:1].upper() + to[1:]
    return to


def word_edits(text: str, word: str, to: str,
               fold: bool) -> list[tuple[int, int, str]]:
    """Every use of `word` in prose (italics left alone, as the checks
    leave them) as `to`. `fold` matches any case and keeps the match's
    capitals (a variant: Colour -> Color); else the exact word."""
    if not word:
        return []
    prose = proof.masked(text, italics=True)
    pattern = re.compile(rf"(?<![\w'’-]){re.escape(word)}(?![\w-])",
                         re.IGNORECASE if fold else 0)
    return [(m.start(), m.end(), _cased(m.group(0), to) if fold else to)
            for m in pattern.finditer(prose)]


def paragraph_edits(text: str, old: str, new: str,
                    line: int = 0) -> list[tuple[int, int, str]]:
    """One paragraph's text replaced: the use of `old` nearest `line`."""
    if not old.strip():
        raise ValueError("the paragraph to change is empty")
    if not new.strip():
        raise ValueError("the new text is empty; to delete a paragraph, "
                         "do it in the Doc")
    if re.search(r"\n\s*\n", new.strip()):
        raise ValueError("the new text is one paragraph (no blank line)")
    hits = [m.start() for m in re.finditer(re.escape(old), text)]
    if not hits:
        return []
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    want = starts[min(max(line, 1), len(starts)) - 1]
    at = min(hits, key=lambda h: abs(h - want))
    return [(at, at + len(old), new.strip())]


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    out, at = [], 0
    for lo, hi, new in sorted(edits):
        out.append(text[at:lo])
        out.append(new)
        at = hi
    out.append(text[at:])
    return "".join(out)


def _map(edits: list[tuple[int, int, str]], at: int) -> int:
    """Offset `at` of the old text in the new one (edits before it)."""
    delta = 0
    for lo, hi, new in edits:
        if hi <= at:
            delta += len(new) - (hi - lo)
    return at + delta


def edits_for(fix: dict, text: str, db: Database, manuscript: dict,
              rel: str) -> list[tuple[int, int, str]]:
    kind = fix.get("kind")
    if kind == "curl":
        style, _law = _quote_target(db, manuscript, rel, text)
        return curl_edits(text, style)
    if kind == "untrack":
        return untrack_edits(text)
    if kind == "word":
        to = str(fix.get("to") or "").strip()
        if not to or re.search(r"\s", to) and fix.get("rule") != "spelling":
            raise ValueError("a word fix needs the new word")
        if "\n" in to:
            raise ValueError("the new word is on one line")
        return word_edits(text, str(fix.get("word") or ""), to,
                          fold=fix.get("rule") == "spelling-variant")
    if kind == "paragraph":
        return paragraph_edits(text, str(fix.get("old") or ""),
                               str(fix.get("new") or ""),
                               int(fix.get("line") or 0))
    raise ValueError(f"a fix is one of {', '.join(FIX_KINDS)}, not {kind!r}")


def string_transform(fix: dict, db: Database, manuscript: dict, rel: str,
                     file_text: str):
    """The fix as a function on any text (a form's new words), or None
    when it has none (an inline paragraph edit)."""
    kind = fix.get("kind")
    if kind == "curl":
        style, _law = _quote_target(db, manuscript, rel, file_text)
        return lambda t: apply_edits(t, curl_edits(t, style))
    if kind == "untrack":
        return lambda t: apply_edits(t, untrack_edits(t))
    if kind == "word":
        word, to = str(fix.get("word") or ""), str(fix.get("to") or "")
        fold = fix.get("rule") == "spelling-variant"
        return lambda t: apply_edits(t, word_edits(t, word, to, fold))
    return None


def changed_paragraphs(text: str, edits: list[tuple[int, int, str]]
                       ) -> list[dict]:
    """{line, before, after} for each paragraph the edits touch."""
    edits = sorted(edits)
    new_text = apply_edits(text, edits)
    out = []
    for lo, hi in paragraph_spans(text):
        if not any(lo <= e[0] < hi or (e[0] == e[1] == hi) or
                   (e[0] < lo < e[1]) for e in edits):
            continue
        # An edit that ends at the paragraph's end belongs to it, so the
        # end maps past it.
        a, b = _map(edits, lo), _map(edits, hi)
        out.append({"line": text.count("\n", 0, lo) + 1,
                    "before": text[lo:hi], "after": new_text[a:b]})
    return out


def preview(db: Database, manuscript: dict, fixes: list[dict]) -> dict:
    """What a set of fixes would change, nothing written: the count of
    changes per tab, and the first `PREVIEW_PAIRS` changed paragraphs as
    word segments (what goes, what comes)."""
    root = Path(manuscript["path"])
    total, pairs, per_file = 0, [], {}
    for fix in fixes:
        rel = changes.docs._resolve_relpath(manuscript, str(fix["file"]))
        text = (root / rel).read_text(encoding="utf-8")
        edits = edits_for(fix, text, db, manuscript, rel)
        total += len(edits)
        per_file[rel] = per_file.get(rel, 0) + len(edits)
        if len(pairs) < PREVIEW_PAIRS:
            for row in changed_paragraphs(text, edits):
                if len(pairs) >= PREVIEW_PAIRS:
                    break
                pairs.append({"file": rel, "line": row["line"],
                              "segments": word_diff(row["before"],
                                                    row["after"])})
    return {"count": total, "files": per_file, "pairs": pairs}


def word_diff(before: str, after: str) -> list[dict]:
    """The paragraph as word segments (equal, delete, insert), the Merge
    forms page's own diff, so a changed apostrophe shows where it is."""
    from .forms_page import word_diff as diff
    return diff(before, after)


# ---------------------------------------------------------- the road

def _forms(db: Database, manuscript: dict, rel: str) -> list[dict]:
    return [dict(r) for r in db.all(
        "SELECT * FROM doc_threads WHERE manuscript_id = ? AND file = ? "
        "AND state = 'written' ORDER BY created_at, id",
        (manuscript["id"], rel))]


def _origin_name(thread: dict) -> str:
    meta = json.loads(thread.get("metadata") or "{}")
    if thread["origin_type"] == api.EXTERNAL_ORIGIN:
        return meta.get("origin") or "an outside caller"
    if thread["origin_type"] == api.LENS_ORIGIN:
        return "lens " + (thread.get("origin_id") or "").split(":", 1)[0] \
            .split("@", 1)[0]
    if thread["origin_type"] == api.FILTER_ORIGIN:
        return "a filter run"
    return thread["origin_type"]


def _form_span(text: str, thread: dict) -> tuple[int, int] | None:
    """Where a replacement form's old words sit in the file."""
    old = thread.get("proposed_old") or ""
    if not old:
        return None
    hits = [m.start() for m in re.finditer(re.escape(old), text)]
    if len(hits) == 1:
        return hits[0], hits[0] + len(old)
    meta = json.loads(thread.get("metadata") or "{}")
    anchor = meta.get("anchor_paragraph")
    spans = [s for s in paragraph_spans(text)]
    if isinstance(anchor, int) and 1 <= anchor <= len(spans):
        lo, hi = spans[anchor - 1]
        for h in hits:
            if lo <= h < hi:
                return h, h + len(old)
    return (hits[0], hits[0] + len(old)) if hits else (-1, -1)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.lint")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _collect(db: Database, manuscript: dict, config: dict) -> None:
    from .sessions import NO_EPISODE

    with contextlib.redirect_stdout(io.StringIO()):
        api.collect(db, manuscript, config or {}, source="lint-page",
                    episode=NO_EPISODE)


def apply_tab(db: Database, manuscript: dict, rel: str, fixes: list[dict],
              service, docs_service, config: dict | None = None,
              batch: str | None = None, label: str = "",
              collect: bool = True) -> dict:
    """Every accepted fix for one tab in one pull, edit, guarded push.
    Returns {file, applied: [fix ids], blocked: [{id, reason}], changes,
    pushed, restaged, warnings, message}. When the tab cannot be pulled,
    pushed, or its forms cannot be carried safely, nothing is applied to
    it and every fix comes back blocked with the reason."""
    from . import forms_page

    config = config or {}
    path = Path(manuscript["path"]) / rel
    links = gdocs.doc_status(db, manuscript)
    entry = links.get(rel) if isinstance(links.get(rel), dict) else {}
    linked = bool(links.get("_master_id") and entry.get("tab_id"))
    result: dict[str, Any] = {"file": rel, "applied": [], "blocked": [],
                              "changes": [], "pushed": False,
                              "restaged": 0, "warnings": [], "message": ""}
    ids = [str(f.get("id") or k) for k, f in enumerate(fixes)]

    def block_all(reason: str) -> dict:
        result["blocked"] = [{"id": i, "reason": reason}
                             for i in ids if i not in
                             {b["id"] for b in result["blocked"]}] \
            + result["blocked"]
        result["applied"] = []
        result["message"] = f"{rel}: nothing applied. {reason}"
        return result

    if linked and (service is None or docs_service is None):
        return block_all(
            "this tab is in the Google Doc and the server has no Google "
            "sign-in now ('authorlm doc auth' in a terminal), so it cannot "
            "be pulled and pushed")
    if linked:
        try:
            view = gdocs.doc_three_way(db, manuscript, rel, service,
                                       docs_service)
        except Exception as err:  # noqa: BLE001 — Google's errors, said
            return block_all(f"the tab could not be read: {err}")
        if view["state"] == "conflict":
            return block_all(
                "the file and its tab both changed since the last push; "
                "settle that under Conflicts on Merge forms first")
        if view["state"] == "doc_ahead" and not _forms(db, manuscript, rel):
            version = forms_page.older_version_of(db, manuscript, rel,
                                                  view["doc"], view["local"])
            if version is not None:
                return block_all(
                    f"the tab holds an older text of the file (version "
                    f"{version}); pulling it would undo what the file "
                    f"gained since. Run 'authorlm doc diff {rel}'")
            # A tab that lost most of the file's words is never pulled
            # from here: an emptied or half-pasted tab would empty the
            # file (seen in the sandbox check of 2026-11-25).
            doc_words = len((view.get("doc") or "").split())
            local_words = len((view.get("local") or "").split())
            if local_words and doc_words < local_words // 2:
                return block_all(
                    f"the tab holds far less than the file ({doc_words} "
                    f"words against {local_words}); nothing was pulled. "
                    f"Look at it with 'authorlm doc diff {rel}'")
            report = gdocs.pull_doc(db, manuscript, rel, service=service,
                                    docs_service=docs_service)
            if rel in report.get("conflicts", []) or rel in report.get(
                    "missing", []):
                return block_all("the pull did not come down cleanly")
            _collect(db, manuscript, config)
            result["warnings"].append(
                f"{rel} was edited in the Doc; that edit was pulled first")
    before_all = path.read_text(encoding="utf-8")

    taken, why, _ = _plan_again(fixes, before_all, db, manuscript, rel)
    threads = _forms(db, manuscript, rel)
    restage: list[dict] | None = None
    if threads and taken:
        kinds = {t["origin_type"] for t in threads}
        origins = ", ".join(sorted({_origin_name(t) for t in threads}))
        spans = {t["id"]: _form_span(before_all, t) for t in threads}
        inside: dict[str, list[int]] = {}
        for lo, hi, _n, k in taken:
            for t in threads:
                span = spans[t["id"]]
                if not span:
                    continue
                s, e = span
                if lo < e and s < hi or (lo == hi and s <= lo <= e):
                    if not (s <= lo and hi <= e) or \
                            fixes[k].get("kind") == "paragraph":
                        why[k] = (f"blocked by a pending form from "
                                  f"{_origin_name(t)} («{_flat(t.get('proposed_old'))[:60]}»): "
                                  "the fix crosses the form's words; rule on "
                                  "the form on Merge forms first")
                    else:
                        inside.setdefault(t["id"], []).append(k)
        reason = None
        if kinds != {api.EXTERNAL_ORIGIN}:
            reason = (f"blocked by a pending form: the tab holds forms from "
                      f"{origins}, which only their own resolve can settle; "
                      "settle them on Merge forms, then apply")
        elif any(s == (-1, -1) for s in spans.values()):
            reason = (f"blocked by a pending form from {origins}: its words "
                      "are no longer in the file as staged")
        if reason is None:
            try:
                listing = forms_page.listing(db, manuscript, service,
                                             docs_service)
            except Exception as err:  # noqa: BLE001 — Google's errors, said
                listing, reason = None, f"the forms could not be read: {err}"
            if listing is not None:
                group = next((g for g in listing["files"]
                              if g["file"] == rel), None)
                rows = {r["id"]: r for r in (group or {}).get("forms", [])}
                for t in threads:
                    r = rows.get(t["id"])
                    if r is None or not r.get("found"):
                        reason = (f"blocked by a pending form from "
                                  f"{_origin_name(t)}: it is not in the tab "
                                  "as staged")
                        break
                    if r.get("ruling"):
                        reason = (f"blocked by a pending form from "
                                  f"{_origin_name(t)}: you have ruled on it "
                                  "on Merge forms; merge it first")
                        break
                    if (r.get("current") or "") != (t.get("proposed_new")
                                                    or ""):
                        reason = (f"blocked by a pending form from "
                                  f"{_origin_name(t)}: its new words were "
                                  "edited in the Doc; merge it first")
                        break
                if reason is None:
                    restage = [{"thread": t, "seen": rows[t["id"]]["current"],
                                "fixes": inside.get(t["id"], [])}
                               for t in threads]
        if reason:
            for k in range(len(fixes)):
                why.setdefault(k, reason)
            taken = []
    # Every fix that is still in is applied; recompute on the file as it
    # stands at write time.
    kept = [k for k in range(len(fixes)) if k not in why]
    result["blocked"] = [{"id": ids[k], "reason": r} for k, r in why.items()]
    if not kept:
        result["message"] = (f"{rel}: nothing applied" + (
            f". {result['blocked'][0]['reason']}" if result["blocked"] else ""))
        return result

    if restage is not None:
        for item in restage:
            got = forms_page.decide(db, manuscript, item["thread"]["id"],
                                    "decline", item["seen"])
            if "ruling" not in got:
                return block_all("the forms could not be withdrawn")
        merged = forms_page.merge(db, manuscript, rel, service, docs_service,
                                  config)
        if merged.get("stopped"):
            for item in restage:
                with contextlib.suppress(Exception):
                    forms_page.decide(db, manuscript, item["thread"]["id"],
                                      "clear", None)
            return block_all("the pending forms could not be withdrawn to "
                             "carry the fix: " + merged.get("message", ""))
    text = path.read_text(encoding="utf-8")
    sub = [fixes[k] for k in kept]
    sub_taken, sub_why, _ = _plan_again(sub, text, db, manuscript, rel)
    for j, reason in sub_why.items():
        k = kept[j]
        result["blocked"].append({"id": ids[k], "reason": reason})
    edits = [(lo, hi, n) for lo, hi, n, _j in sub_taken]
    applied_ks = sorted({kept[j] for *_x, j in sub_taken})
    new_text = apply_edits(text, edits)
    revisions: list[tuple[str, dict]] = []
    originals: list[tuple[str, dict]] = []
    if restage is not None:
        for item in restage:
            t = item["thread"]
            meta = json.loads(t.get("metadata") or "{}")
            old, new = t.get("proposed_old") or "", t.get("proposed_new") or ""
            originals.append((meta.get("origin") or "lint", {
                "old": old, "new": new, "note": t.get("note") or "",
                "anchor_paragraph": meta.get("anchor_paragraph"),
                "_index": meta.get("index", 0)}))
            if old:
                span = _form_span(text, t)
                if span and span != (-1, -1):
                    s, e = span
                    mine = [x for x in edits if s <= x[0] and x[1] <= e]
                    old = apply_edits(text[s:e], [(lo - s, hi - s, n)
                                                  for lo, hi, n in mine])
                    if mine:
                        for k in item["fixes"]:
                            fn = string_transform(fixes[k], db, manuscript,
                                                  rel, text)
                            if fn:
                                new = fn(new)
            revisions.append((meta.get("origin") or "lint", {
                "old": old, "new": new, "note": t.get("note") or "",
                "anchor_paragraph": meta.get("anchor_paragraph"),
                "_index": meta.get("index", 0)}))
    if new_text != text:
        _write(path, new_text)
    rows = changed_paragraphs(text, edits)
    pushed = True
    if linked and new_text != text:
        try:
            if not gdocs.unchanged_since_push(db, manuscript, rel):
                gdocs.push_doc(db, manuscript, rel, service=service,
                               docs_service=docs_service, guard=True)
        except Exception as err:  # noqa: BLE001 — the guard and Google's errors
            pushed = False
            _write(path, text)
            result["applied"] = []
            reason = f"the push was stopped, so the file was put back: {err}"
            result["blocked"] = [{"id": i, "reason": reason} for i in ids]
            result["message"] = f"{rel}: nothing applied. {reason}"
    if pushed:
        result["pushed"] = linked
        result["applied"] = [ids[k] for k in applied_ks]
        if new_text != text and collect:
            _collect(db, manuscript, config)
    if restage is not None:
        # The push stopped and the file was put back: the forms go back
        # as they were, not with the fix.
        staged = _restage(db, manuscript, rel,
                          revisions if pushed else originals, service,
                          docs_service, config)
        result["restaged"] = staged["written"]
        result["warnings"] += staged["warnings"]
    if pushed and rows:
        batch = batch or ko_fields("lb")["id"]
        for row in rows:
            fields = ko_fields("lc")
            fields.update(manuscript_id=manuscript["id"], batch_id=batch,
                          file=rel, rule=label or ",".join(sorted({
                              str(fixes[k].get("rule") or fixes[k]["kind"])
                              for k in applied_ks})),
                          line=row["line"], before_text=row["before"],
                          after_text=row["after"], pushed=1 if linked else 0,
                          undone_at=None,
                          restaged=json.dumps([r for _o, r in revisions])
                          if revisions else None)
            db.insert("lint_changes", fields)
        result["changes"] = rows
        result["batch"] = batch
        result["message"] = (
            f"{rel}: {len(result['applied'])} fix(es), {len(rows)} "
            f"paragraph(s) changed" + ("; pushed to the tab" if linked else
                                       "; the file has no tab"))
    return result


def _plan_again(fixes, text, db, manuscript, rel):
    taken: list[tuple[int, int, str, int]] = []
    why: dict[int, str] = {}
    for k, fix in enumerate(fixes):
        try:
            edits = edits_for(fix, text, db, manuscript, rel)
        except (ValueError, KeyError) as err:
            why[k] = str(err)
            continue
        if not edits:
            why[k] = ("nothing to change: the words are no longer in the "
                      "file as lint found them; run lint again")
            continue
        if any(lo < h and l < hi for lo, hi, _n in edits
               for l, h, _m, _k in taken):
            why[k] = "overlaps another fix in this batch"
            continue
        taken += [(lo, hi, n, k) for lo, hi, n in edits]
    return taken, why, None


def _restage(db, manuscript, rel, revisions, service, docs_service,
             config) -> dict:
    """The withdrawn forms back in the tab, each origin's in its order."""
    by_origin: dict[str, list[dict]] = {}
    for origin, rev in sorted(revisions, key=lambda r: r[1]["_index"]):
        by_origin.setdefault(origin, []).append(
            {k: v for k, v in rev.items() if k != "_index"})
    written, warnings = 0, []
    for origin, revs in by_origin.items():
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                out = api.stage_revisions(db, manuscript, rel, revs, origin,
                                          service, docs_service, config)
        except Exception as err:  # noqa: BLE001 — said, with the way back
            warnings.append(f"the {origin} forms could not be restaged "
                            f"({err}); their texts are kept in the lint log "
                            "(lint_changes.restaged)")
            continue
        written += len(out["written"])
        for rev, reason in out["failed"]:
            warnings.append(f"a {origin} form was not restaged ({reason}): "
                            f"«{_flat(rev.get('old') or rev.get('new'))[:60]}»")
    return {"written": written, "warnings": warnings}


def apply(db: Database, manuscript: dict, fixes: list[dict], service,
          docs_service, config: dict | None = None,
          label: str = "") -> dict:
    """Every fix, grouped by tab, one road per tab (`apply_tab`)."""
    by_file: dict[str, list[dict]] = {}
    for k, fix in enumerate(fixes or []):
        if fix.get("kind") not in FIX_KINDS:
            raise ValueError(f"a fix is one of {', '.join(FIX_KINDS)}")
        rel = changes.docs._resolve_relpath(manuscript, str(fix["file"]))
        by_file.setdefault(rel, []).append({**fix, "id": str(
            fix.get("id") or k)})
    if not by_file:
        raise ValueError("no fix to apply")
    batch = ko_fields("lb")["id"]
    # One snapshot for the whole click, after every tab (a collect reads
    # the whole book, so one per tab would multiply the wait).
    tabs = [apply_tab(db, manuscript, rel, group, service, docs_service,
                      config, batch=batch, label=label, collect=False)
            for rel, group in by_file.items()]
    if any(t["changes"] for t in tabs):
        _collect(db, manuscript, config or {})
    return {"batch": batch, "tabs": tabs,
            "applied": sum(len(t["applied"]) for t in tabs),
            "blocked": sum(len(t["blocked"]) for t in tabs)}


# ------------------------------------------------------------ the log

def log(db: Database, manuscript: dict, limit: int = 200) -> list[dict]:
    """The changes this page made, newest batch first, each paragraph's
    text before and after."""
    rows = [dict(r) for r in db.all(
        "SELECT id, batch_id, file, rule, line, before_text, after_text, "
        "pushed, undone_at, created_at FROM lint_changes WHERE "
        "manuscript_id = ? ORDER BY created_at DESC, id LIMIT ?",
        (manuscript["id"], limit))]
    batches: dict[str, dict] = {}
    for r in rows:
        r["segments"] = word_diff(r["before_text"], r["after_text"])
        b = batches.setdefault(r["batch_id"], {
            "batch": r["batch_id"], "at": r["created_at"], "changes": []})
        b["changes"].append(r)
    return list(batches.values())


def undo(db: Database, manuscript: dict, ids: list[str], service,
         docs_service, config: dict | None = None) -> dict:
    """Put the logged paragraphs back as they were, through the same road;
    the undone rows are marked, and the undo is itself logged."""
    if not ids:
        raise ValueError("name the changes to undo")
    marks = ",".join("?" for _ in ids)
    rows = [dict(r) for r in db.all(
        f"SELECT * FROM lint_changes WHERE manuscript_id = ? AND "
        f"undone_at IS NULL AND (id IN ({marks}) OR batch_id IN ({marks}))",
        (manuscript["id"], *ids, *ids))]
    if not rows:
        raise LookupError("nothing to undo: no such change, or already undone")
    fixes = [{"id": r["id"], "kind": "paragraph", "file": r["file"],
              "old": r["after_text"], "new": r["before_text"],
              "line": r["line"], "rule": "undo"} for r in rows]
    out = apply(db, manuscript, fixes, service, docs_service, config,
                label="undo")
    done = {i for t in out["tabs"] for i in t["applied"]}
    for r in rows:
        if r["id"] in done:
            db.update("lint_changes", r["id"], {"undone_at": now_iso()})
    return out


# ----------------------------------------------------------- dispatch

def dispatch(method: str, params: dict[str, Any], session) -> dict:
    """`lint.run`, `lint.preview`, `lint.apply`, `lint.accept`,
    `lint.ignore`, `lint.log`, `lint.undo`. Only apply and undo touch
    Google (the tab's pull and push)."""
    manuscript = session.manuscript
    db = session._thread_db()

    def services():
        try:
            return session._doc_services()
        except Exception:  # noqa: BLE001 — no sign-in: said per tab
            return {}, None, None

    if method == "run":
        return run(db, manuscript, [str(f) for f in params.get("files") or []],
                   [str(r) for r in params.get("rules") or []] or None)
    if method == "preview":
        return preview(db, manuscript, list(params.get("fixes") or []))
    if method == "apply":
        config, service, docs_service = services()
        return apply(db, manuscript, list(params.get("fixes") or []),
                     service, docs_service, config)
    if method == "accept":
        return changes.lint_accept(db, manuscript,
                                   [str(w) for w in params.get("words") or []])
    if method == "ignore":
        return ignore(Path(manuscript["path"]), str(params.get("id") or ""),
                      str(params.get("rule") or ""),
                      str(params.get("file") or ""),
                      str(params.get("quote") or ""))
    if method == "log":
        return {"batches": log(db, manuscript)}
    if method == "undo":
        config, service, docs_service = services()
        return undo(db, manuscript, [str(i) for i in params.get("ids") or []],
                    service, docs_service, config)
    return {"error": f"unknown method 'lint.{method}'"}
