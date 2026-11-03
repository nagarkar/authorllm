"""How old the Doc-road recordings are, and what to do about it.

`tests/doc_cache/fake.log` holds the time of the last live recording
(`tests/doc_road_live.py --record`), and the fixture Doc holds the same
time on one line, `Last recorded: <time>`, in a tab of its own titled
`fake-log` (no scenario reads it). The log travels with the recordings;
the Doc line is the witness that survives a deleted log.

    log under 7 days old             -> nothing to do, no Google call
    log missing or old, Doc line old
      or absent                      -> re-record live
    log missing, Doc line fresh      -> FAIL: something deleted the log
    no Google token (a cloud run)    -> skip the live step, say so

`decide` is pure (clock, log and Doc are handed in) so the rule is
tested hermetically (test_doc_freshness.py); `ensure_fresh` wires it to
the real clock, file, token and Doc.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "doc_cache"
LOG = CACHE / "fake.log"
FIXTURE = CACHE / "fixture_doc.json"
TOKEN = Path.home() / ".authorlm" / "gdocs_token.json"
MAX_AGE = dt.timedelta(days=7)
DOC_TAB = "fake-log"
DOC_PREFIX = "Last recorded: "


class LogDeleted(AssertionError):
    pass


def deleted_message(doc_time: dt.datetime) -> str:
    return (
        f"tests/doc_cache/fake.log was deleted: it is missing, although the "
        f"fixture Doc shows a recording on {doc_time.isoformat()}. Agent: "
        f"find what deleted tests/doc_cache/fake.log and fix that. Then, "
        f"only after a human approves, edit the 'Last recorded:' line in "
        f"the fixture Doc's '{DOC_TAB}' tab by hand to a date 7 or more "
        f"days old, so the next run re-records. Never rewrite that date "
        f"automatically.")


def parse(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith(DOC_PREFIX):
        text = text[len(DOC_PREFIX):].strip()
    try:
        when = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return when if when.tzinfo else None


def decide(now: dt.datetime, log_time: dt.datetime | None,
           log_exists: bool, has_token: bool,
           read_doc_time) -> str:
    """'fresh' | 'record' | 'skip'; raises LogDeleted. `read_doc_time`
    is called only when the log does not settle it, and only with a
    token: a fresh log never costs a Google call."""
    if log_exists and log_time is not None and now - log_time < MAX_AGE:
        return "fresh"
    if not has_token:
        return "skip"
    doc_time = read_doc_time()
    if doc_time is None or now - doc_time >= MAX_AGE:
        return "record"
    if not log_exists:
        raise LogDeleted(deleted_message(doc_time))
    return "record"      # the log is old (or unreadable) and the Doc newer


# ------------------------------------------------------------ the real one


def read_doc_time() -> dt.datetime | None:
    """The fixture Doc's 'Last recorded:' line, or None."""
    sys.path.insert(0, str(HERE.parent))
    from authorlm import gdocs, paths

    paths.load_env()
    docs = gdocs.get_docs_service({}, None, interactive=False)
    doc_id = json.loads(FIXTURE.read_text())["doc_id"]
    got = docs.documents().get(
        documentId=doc_id, includeTabsContent=True,
        fields="tabs(tabProperties(title),documentTab(body(content("
               "paragraph(elements(textRun(content)))))))").execute()
    for tab in got.get("tabs", []):
        if tab.get("tabProperties", {}).get("title") != DOC_TAB:
            continue
        for item in tab.get("documentTab", {}).get("body", {}).get(
                "content", []):
            text = "".join(e.get("textRun", {}).get("content", "")
                           for e in (item.get("paragraph") or {}).get(
                               "elements", []))
            if text.strip().startswith(DOC_PREFIX):
                return parse(text)
    return None


def log_time() -> dt.datetime | None:
    return parse(LOG.read_text()) if LOG.exists() else None


def ensure_fresh() -> str:
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    action = decide(now, log_time(), LOG.exists(), TOKEN.exists(),
                    read_doc_time)
    if action == "skip":
        print("  notice: no Google token (~/.authorlm/gdocs_token.json); "
              "the recordings' age is not checked live, using them as "
              "they are")
    elif action == "record":
        print("  recordings are 7 days old or more: re-recording live "
              "(tests/doc_road_live.py --record)", flush=True)
        before = log_time()
        subprocess.run([sys.executable, str(HERE / "doc_road_live.py"),
                        "--record"], cwd=str(HERE.parent), check=False)
        after = log_time()
        if after is None or after == before:
            raise AssertionError("the live re-record did not write "
                                 "tests/doc_cache/fake.log; see its "
                                 "output above")
    return action
