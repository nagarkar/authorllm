"""The four jobs only the retired MCP server could do, now on the CLI.

When the MCP server was removed (issue #364, step 1a) every tool that had
ever been called was matched to a CLI verb. Four had no full match, and
each got the smallest verb that covers it:

- `manuscript list [--file PATH]`: every registered manuscript with its
  directory, and which one holds a file (`list_manuscripts`, `resolve_file`).
- `concept list --file FILE` / `--query TEXT`: the scoped slice of the
  Concept Graph (`get_concepts` with `file=` or `query=`).
- `concept confirm <edge>` and `concept reject-edge <edge>` leave an
  `edge_triage` evidence row, as `confirm_edge` and `reject_edge` did.
- `proposal accept <id>` reaches a proposal the screen folded away and
  counts against the belief that cut it, as `resolve_proposal` did.

Run: python3 tests/test_cli_covers_retired_tools.py
"""

from __future__ import annotations

import os

# Offline suite: pin the project-config and .env lookups away from the
# real ones, and client provenance off (see tests/test_loop.py).
os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

import contextlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api, loop, proposals as prop  # noqa: E402
from authorlm.cli import main  # noqa: E402
from authorlm.db import ko_fields  # noqa: E402

PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok: {label}")


def run(workspace: Path, *argv: str) -> tuple[str, int]:
    """One CLI run: (stdout plus any exit message, exit code)."""
    buffer = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buffer):
            main(["--workspace", str(workspace), *argv])
    except SystemExit as err:
        code = 0 if err.code in (0, None) else 1
        if isinstance(err.code, str):
            buffer.write(err.code + "\n")
    return buffer.getvalue(), code


class CutsFirst:
    """A screen model that cuts proposal 1 under the law it is told."""

    enabled = True

    def __init__(self, law_id: str):
        self.law_id = law_id

    def complete_json(self, system, user):
        return {"cut": [{"n": 1, "law": self.law_id, "reason": "restates"}]}

    def complete(self, system, user):
        return json.dumps(self.complete_json(system, user))


def edge_evidence(db, manuscript_id: str) -> list[tuple[str, str]]:
    return [(r["signal"], r["target"]) for r in db.all(
        "SELECT signal, target FROM evidence WHERE manuscript_id = ? "
        "AND evidence_type = 'edge_triage' ORDER BY created_at",
        (manuscript_id,))]


def main_test() -> None:
    # Resolved: `init` records the real path (/private/var on macOS).
    root = Path(tempfile.mkdtemp(prefix="authorlm-retired-tools-")).resolve()
    try:
        ws = root / "ws"
        first, second = root / "first", root / "second"
        for folder in (first, second):
            folder.mkdir()
        (first / "01-choice.md").write_text(
            "# Choice\n\nThe Field of Choice precedes every distinction.\n",
            encoding="utf-8")
        (first / "02-regret.md").write_text(
            "# Regret\n\nRegret follows an ineffective act.\n",
            encoding="utf-8")
        (second / "poem.md").write_text("# Poem\n\nNothing here.\n",
                                        encoding="utf-8")
        for name, folder in (("first", first), ("second", second)):
            out, code = run(ws, "init", "--name", name, "--path", str(folder))
            check(f"fixture: init {name}", code == 0, out)

        print("manuscript list")
        out, code = run(ws, "manuscript", "list")
        check("lists every manuscript with its directory, no -m needed",
              code == 0 and f"first  {first}" in out
              and f"second  {second}" in out, out)
        out, code = run(ws, "manuscript", "list", "--file", "poem.md")
        check("--file names the manuscript that holds a bare filename",
              code == 0 and out.split()[0] == "second" and "first" not in out,
              out)
        out, code = run(ws, "manuscript", "list", "--file",
                        str(first / "02-regret.md"))
        check("--file names the manuscript that holds a full path",
              code == 0 and out.split()[0] == "first", out)
        out, code = run(ws, "manuscript", "list", "--file", "/tmp/nowhere.md")
        check("--file on a foreign path exits 1 and names the fix",
              code == 1 and "not inside any registered manuscript" in out
              and "authorlm manuscript list" in out, out)

        print("concept list --file / --query")
        m = ("-m", "first")
        notes = "The ground of every distinction. " * 12
        for argv in (("Field of Choice", "--notes", notes),
                     ("Regret", "--notes", "What follows an ineffective act."),
                     ("Gravity", "--notes", "Not in any essay yet.")):
            out, code = run(ws, *m, "concept", "add", *argv)
            check(f"fixture: concept add {argv[0]}", code == 0, out)
        out, code = run(ws, *m, "concept", "alias", "Regret", "remorse")
        check("fixture: concept alias", code == 0, out)
        out, code = run(ws, *m, "concept", "link", "Regret", "depends_on",
                        "Field of Choice")
        check("fixture: concept link", code == 0, out)

        out, code = run(ws, *m, "concept", "list", "--file", "02-regret.md")
        check("--file keeps the concepts named in that essay only",
              code == 0 and "Regret (" in out and "Gravity" not in out
              and "Field of Choice" not in out and "(1)" in out, out)
        out, code = run(ws, *m, "concept", "list", "--file", "01-choice.md")
        check("--file shortens a long note to a preview",
              code == 0 and "Field of Choice (" in out and "…" in out
              and notes.strip() not in out, out)
        out, code = run(ws, *m, "concept", "list", "--query", "REMORSE")
        check("--query matches an alias, whatever the case",
              code == 0 and "Regret (" in out and "also: remorse" in out
              and "Gravity" not in out, out)
        out, code = run(ws, *m, "concept", "list", "--query", "ineffective")
        check("--query matches the notes", code == 0 and "Regret (" in out,
              out)
        out, code = run(ws, *m, "concept", "list", "--query", "o")
        check("relationships are kept among the selected concepts",
              code == 0 and "Regret —depends_on→ Field of Choice" in out, out)
        out, code = run(ws, *m, "concept", "list", "--query", "zzz")
        check("--query with no match says so",
              code == 0 and "No concepts matching 'zzz'." in out, out)
        out, code = run(ws, *m, "concept", "list", "--file", "missing.md")
        check("--file on an unknown essay exits 1",
              code == 1 and "no manuscript file matching" in out, out)
        out, code = run(ws, *m, "concept", "show", "Regret", "--query", "x")
        check("--query on another sub-verb is a usage error",
              code == 1 and "concept list" in out, out)

        print("edge rulings leave evidence")
        db = api.open_db(str(ws))
        manuscript = api.get_manuscript(db, "first")
        mid = manuscript["id"]
        run(ws, *m, "concept", "link", "Gravity", "leads_to", "Regret")
        edges = {r["relation"]: r["id"] for r in db.all(
            "SELECT id, relation FROM concept_edges WHERE manuscript_id = ?",
            (mid,))}
        check("fixture: no edge ruling recorded yet",
              edge_evidence(db, mid) == [], str(edge_evidence(db, mid)))
        out, code = run(ws, *m, "concept", "confirm", edges["depends_on"])
        check("concept confirm <edge> prints the confirmed relationship",
              code == 0
              and "Confirmed: Regret —depends_on→ Field of Choice" in out, out)
        out, code = run(ws, *m, "concept", "reject-edge", edges["leads_to"])
        check("concept reject-edge rejects", code == 0 and "rejected" in out,
              out)
        db = api.open_db(str(ws))
        check("each ruling is one edge_triage evidence row",
              edge_evidence(db, mid)
              == [("confirmed", "Regret —depends_on→ Field of Choice"),
                  ("rejected", "Gravity —leads_to→ Regret")],
              str(edge_evidence(db, mid)))
        check("and the edges carry the ruling",
              {r["id"]: r["status"] for r in db.all(
                  "SELECT id, status FROM concept_edges WHERE "
                  "manuscript_id = ?", (mid,))}
              == {edges["depends_on"]: "declared",
                  edges["leads_to"]: "rejected"})
        out, code = run(ws, *m, "concept", "reject-edge", "no-such-edge")
        check("an unknown edge exits 1", code == 1 and "no edge matching" in out,
              out)

        print("a folded proposal is reachable by id")
        spec = loop.spec_for("proposals/note_update")
        belief = ko_fields("pol")
        belief.update(manuscript_id=mid, statement="A note says what a thing "
                      "is, not what it does.", status="validated",
                      confidence=0.8, supporting=5, contradicting=0,
                      source=spec.source)
        db.insert("editorial_beliefs", belief)
        target = db.one("SELECT id FROM concept_nodes WHERE manuscript_id = ? "
                        "AND name = 'Gravity'", (mid,))["id"]
        proposal = prop.create(db, mid, "note_update", target, {
            "name": "Gravity", "current_note": "Not in any essay yet.",
            "proposed_note": "The pull that every choice exerts on the next.",
            "current_kind": "concept", "proposed_kind": "concept"})
        rows = [r for r in prop.open_proposals(db, mid)
                if r["kind"] == "note_update"]
        cuts = loop.screen(db, mid, spec, rows, CutsFirst(belief["id"]))
        check("fixture: the screen folds the proposal under the belief",
              len(cuts) == 1 and proposal["id"] not in
              {r["id"] for r in prop.open_proposals(db, mid)}, str(cuts))
        out, code = run(ws, *m, "proposal", "list", "--belief", belief["id"])
        check("proposal list --belief shows it and names 'proposal accept'",
              code == 0 and proposal["id"][:8] in out
              and "proposal accept" in out, out)
        out, code = run(ws, *m, "proposal", "accept", proposal["id"])
        check("proposal accept adopts the folded proposal",
              code == 0 and "counted against the belief that folded it" in out,
              out)
        db = api.open_db(str(ws))
        after = db.one("SELECT * FROM editorial_beliefs WHERE id = ?",
                       (belief["id"],))
        check("and that counts against the belief that cut it",
              after["contradicting"] == 1 and after["confidence"] < 0.8,
              str(dict(after)))
        check("and the concept's note is the adopted one",
              db.one("SELECT notes FROM concept_nodes WHERE id = ?",
                     (target,))["notes"]
              == "The pull that every choice exerts on the next.")
        out, code = run(ws, *m, "proposal", "accept", proposal["id"])
        check("a proposal already settled exits 1 and names the fix",
              code == 1 and "no open proposal matching" in out
              and "authorlm proposal list" in out, out)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\nall checks passed ({PASSED})")


if __name__ == "__main__":
    main_test()
