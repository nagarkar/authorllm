"""`doc bind`: adopt an existing master Doc for a manuscript that has none
(a fresh workspace, a cloud session), so `doc pull` fills the folder from
its tabs instead of `doc push` creating a new Doc.

Hermetic: Google is the `_SurgicalDocFake` from test_passes.py; no
network, no model.
Run: python3 tests/test_doc_bind.py
"""
from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_doc_road_verbs import _google, _run, check  # noqa: E402
from test_passes import _SurgicalDocFake  # noqa: E402

from authorlm import api, gdocs  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402

ESSAY = "# Solo\n\nAlpha one.\n\nBeta two.\n"
URL = "https://docs.google.com/document/d/doc-fake-1234567890abcdef/edit?tab=t.0"


def _empty_manuscript(root: Path, name: str, book: str = "book"):
    """A registered manuscript with an empty folder and no Doc link."""
    ws = root / name
    ms = ws / book
    ms.mkdir(parents=True)
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(ws), "init", "--name", book,
                  "--path", str(ms), "--extraction", "off"])
    db = api.open_db(str(ws))
    return ws, db, api.get_manuscript(db), ms


def parse_ids() -> None:
    print("A. parse_doc_id:")
    check("URL with /d/ and a tab query gives the id",
          gdocs.parse_doc_id(URL) == "doc-fake-1234567890abcdef")
    check("bare id passes through",
          gdocs.parse_doc_id(" 1VDLq5605h6f9yykyMCtWnsCOP9lnMIsTdVl6IrgF2ls ")
          == "1VDLq5605h6f9yykyMCtWnsCOP9lnMIsTdVl6IrgF2ls")
    for bad in ("", "becker.md", "https://docs.google.com/document/"):
        try:
            gdocs.parse_doc_id(bad)
            check(f"rejects {bad!r}", False)
        except ValueError:
            check(f"rejects {bad!r}", True)


def bind_empty(root: Path) -> None:
    print("B. bind on a manuscript with no Doc:")
    ws, db, manuscript, ms = _empty_manuscript(root, "bind-ws")
    fake = _SurgicalDocFake([("book", ""), ("solo.md", ESSAY)])
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc", "bind",
                               "doc-fake", "-m", "book"])
    check("bind exits 0", code == 0, err)
    check("bind names the Doc URL", "docs.google.com/document/d/doc-fake" in out, out)
    check("bind counts the .md tabs", "1 .md tab(s)" in out, out)
    links = gdocs._mapping(db, manuscript).get("gdocs", {})
    check("_master_id recorded", links.get("_master_id") == "doc-fake", str(links))
    check("container tab recorded as the tab named for the manuscript",
          links.get("_container_tab") == "tab-1", str(links))
    check("doc version bumped", links.get("_doc_version") == 1, str(links))
    check("the Doc was not written", fake.bodies == [], str(fake.bodies))
    # Rebinding the same Doc is idempotent.
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc", "bind",
                               URL.replace("doc-fake-1234567890abcdef", "doc-fake"),
                               "-m", "book"])
    links = gdocs._mapping(db, manuscript).get("gdocs", {})
    check("binding the same Doc again by URL exits 0", code == 0, err)
    check("and bumps nothing", links.get("_doc_version") == 1, str(links))
    # The pull now materialises the tab as a local file.
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc", "pull",
                               "-m", "book", "--no-comments"])
    check("doc pull after bind exits 0", code == 0, err + out)
    check("solo.md materialised from its tab",
          (ms / "solo.md").exists() and "Alpha one." in (ms / "solo.md").read_text(),
          out)
    links = gdocs._mapping(db, manuscript).get("gdocs", {})
    check("solo.md linked to its tab id", links.get("solo.md", {}).get("tab_id") == "tab-2",
          str(links))


def bind_conflicts(root: Path) -> None:
    print("C. bind when a Doc is already recorded, and bad Docs:")
    ws, db, manuscript, ms = _empty_manuscript(root, "rebind-ws")
    fake = _SurgicalDocFake([("book", ""), ("solo.md", ESSAY)])
    with _google(fake):
        _run(["--workspace", str(ws), "doc", "bind", "doc-fake", "-m", "book"])
    other = _SurgicalDocFake([("book", ""), ("other.md", ESSAY)])
    other.master_id = "doc-other"
    with _google(other):
        code, out, err = _run(["--workspace", str(ws), "doc", "bind",
                               "doc-other", "-m", "book"])
    check("a different Doc without --force exits 1", code == 1, out)
    check("and says what is bound and how to replace it",
          "already bound" in err and "--force" in err, err)
    check("mapping untouched",
          gdocs._mapping(db, manuscript)["gdocs"]["_master_id"] == "doc-fake")
    with _google(other):
        code, out, err = _run(["--workspace", str(ws), "doc", "bind",
                               "doc-other", "-m", "book", "--force"])
    links = gdocs._mapping(db, manuscript)["gdocs"]
    check("--force rebinds", code == 0 and links["_master_id"] == "doc-other", err)
    check("rebind says so and drops the old tab ids",
          "Rebound" in out and "no longer linked" in out
          and not any(isinstance(v, dict) and "tab_id" in v for v in links.values()),
          out + str(links))
    check("doc version bumped again", links["_doc_version"] == 2, str(links))
    # A Doc whose tabs do not include the manuscript's name is not adopted.
    ws2, db2, manuscript2, _ = _empty_manuscript(root, "wrong-ws", book="other")
    with _google(fake):  # its root tab is 'book', not 'other'
        code, out, err = _run(["--workspace", str(ws2), "doc", "bind",
                               "doc-fake", "-m", "other"])
    check("a Doc without the manuscript's tab exits 1", code == 1, out)
    check("and lists the tabs it found", "no tab named 'other'" in err and "book" in err, err)
    check("nothing recorded", "_master_id" not in gdocs._mapping(db2, manuscript2).get("gdocs", {}))
    with _google(fake):
        code, out, err = _run(["--workspace", str(ws), "doc", "bind", "-m", "book"])
    check("bind without an id prints usage", code == 1 and "usage: doc bind" in err, err)
    check("doc --help lists bind",
          "bind" in _run(["--workspace", str(ws), "doc", "--help"])[1])


def token_from_environment(root: Path) -> None:
    """A cloud session holds the token as AUTHORLM_GDOCS_TOKEN, never as a
    file; get_credentials must use it, refresh in memory and write nothing."""
    print("D. token from the environment:")
    import json
    import os
    from unittest import mock

    ws = root / "token-ws"
    ws.mkdir()
    token = {"token": "stale", "refresh_token": "rt",
             "client_id": "cid", "client_secret": "cs",
             "token_uri": "https://oauth2.googleapis.com/token",
             "scopes": list(gdocs.SCOPES)}

    class _Creds:
        made_from = None

        def __init__(self, expired=True):
            self.expired = expired
            self.valid = not expired
            self.refresh_token = "rt"

        def refresh(self, request):
            self.valid, self.expired = True, False

        def to_json(self):
            return json.dumps({"token": "fresh"})

    def from_info(info, scopes):
        _Creds.made_from = info
        return _Creds()

    env = {k: v for k, v in os.environ.items() if k != gdocs.TOKEN_ENV}
    env[gdocs.TOKEN_ENV] = json.dumps(token)
    with mock.patch.dict(os.environ, env, clear=True), \
            mock.patch("google.oauth2.credentials.Credentials"
                       ".from_authorized_user_info", side_effect=from_info), \
            mock.patch("google.auth.transport.requests.Request"):
        creds = gdocs.get_credentials({}, workspace=str(ws), interactive=False)
    check("credentials come from the variable", _Creds.made_from == token)
    check("and were refreshed in memory", creds.valid and not creds.expired)
    check("no token file was written",
          not (ws / ".authorlm" / "gdocs_token.json").exists())
    env[gdocs.TOKEN_ENV] = "not json"
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            gdocs.get_credentials({}, workspace=str(ws), interactive=False)
            check("a malformed variable is a clear error", False)
        except ValueError as err:
            check("a malformed variable is a clear error",
                  gdocs.TOKEN_ENV in str(err), str(err))
    env.pop(gdocs.TOKEN_ENV)
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            gdocs.get_credentials({}, workspace=str(ws), interactive=False)
            check("no file and no variable still fails non-interactively", False)
        except ValueError as err:
            check("no file and no variable still fails non-interactively",
                  gdocs.TOKEN_ENV in str(err), str(err))


def main_test() -> None:
    root = Path(tempfile.mkdtemp(prefix="authorlm-docbind-"))
    try:
        parse_ids()
        bind_empty(root)
        bind_conflicts(root)
        token_from_environment(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    from test_doc_road_verbs import PASSED
    import test_doc_road_verbs as t
    print(f"\nall checks passed ({t.PASSED})")


if __name__ == "__main__":
    main_test()
