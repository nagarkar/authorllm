"""rescan_primary_locations must count aliases as presence.

scan_realizations realizes on node_names (primary + aliases). A primary-only
rescan then falsely vanishes the same concept in the same collect — and for
extracted+unconfirmed nodes, collect silently retires the concept and its
edges while the alias text is still on disk.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTHORLM_CONFIG"] = "/nonexistent/authorlm-test/config.toml"
os.environ["AUTHORLM_ENV"] = "/nonexistent/authorlm-test/.env"
os.environ["AUTHORLM_CLIENT"] = "none"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from authorlm import api  # noqa: E402
from authorlm import concepts as cg  # noqa: E402
from authorlm.cli import main as cli_main  # noqa: E402
from authorlm.concepts import link_concepts  # noqa: E402


PASSED = 0


def check(label: str, condition: bool, context: str = "") -> None:
    global PASSED
    assert condition, f"FAIL: {label}\n{context}"
    PASSED += 1
    print(f"  ok  {label}")


def main() -> None:
    root = Path(tempfile.mkdtemp())
    ms = root / "manuscript"
    ms.mkdir()
    (ms / "01-choice.md").write_text(
        "# Choice\n\nChoice is the hinge of becoming.\n"
        "Habit resists becoming.\n",
        encoding="utf-8",
    )
    with contextlib.redirect_stdout(io.StringIO()):
        cli_main(["--workspace", str(root), "init", "--name", "book",
                  "--path", str(ms)])

    db = api.open_db(str(root))
    manuscript = api.get_manuscript(db)

    agency = api.add_concept(db, manuscript, "Agency")
    cg.add_alias(db, manuscript["id"], agency, "Choice")

    will = api.add_concept(db, manuscript, "Will")
    cg.add_alias(db, manuscript["id"], will, "becoming")
    db.update("concept_nodes", will["id"], {"metadata": json.dumps(
        {"origin": "extracted", "confirmed": False})})

    api.add_concept(db, manuscript, "Habit")
    link_concepts(db, manuscript["id"], "Will", "opposes", "Habit",
                  status="inferred")

    report = api.collect(db, manuscript, {})

    agency_row = db.one("SELECT * FROM concept_nodes WHERE id = ?",
                        (agency["id"],))
    will_row = db.one("SELECT * FROM concept_nodes WHERE id = ?",
                      (will["id"],))
    edge = db.one(
        "SELECT * FROM concept_edges WHERE manuscript_id = ? "
        "AND relation = 'opposes'",
        (manuscript["id"],),
    )
    vanished_prop = db.one(
        "SELECT id FROM knowledge_proposals WHERE kind = 'vanished' "
        "AND target = ?",
        (agency["id"],),
    )

    check("alias-only Agency realizes and stays realized",
          agency_row["status"] == "realized"
          and agency_row["introduced_in"] == "01-choice.md"
          and "Agency" not in report.get("vanished", []),
          str({"agency": dict(agency_row),
               "vanished": report.get("vanished"),
               "realized": report.get("realized")}))
    check("alias-only extracted Will is not silently retired",
          will_row["status"] == "realized"
          and "Will" not in report.get("hypotheses_dropped", []),
          str({"will": dict(will_row),
               "dropped": report.get("hypotheses_dropped")}))
    check("edges of alias-realized concepts stay live",
          edge is not None and edge["status"] != "retired",
          str(dict(edge) if edge else None))
    check("no false vanished proposal while the alias is on the page",
          vanished_prop is None,
          str(vanished_prop))

    # True vanish: remove every name, including the alias.
    (ms / "01-choice.md").write_text(
        "# Essay\n\nHabit alone remains.\n", encoding="utf-8")
    gone = api.collect(db, manuscript, {})
    will_after = db.one("SELECT status FROM concept_nodes WHERE id = ?",
                        (will["id"],))
    check("true alias disappearance still drops unconfirmed extracts",
          "Will" in gone.get("hypotheses_dropped", [])
          and will_after["status"] == "retired",
          str(gone.get("hypotheses_dropped")))
    check("true primary disappearance still proposes for author concepts",
          "Agency" in gone.get("vanished", []),
          str(gone.get("vanished")))

    print(f"\n{PASSED} checks passed.")


if __name__ == "__main__":
    main()
