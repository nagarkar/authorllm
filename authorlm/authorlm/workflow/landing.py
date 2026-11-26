"""Landing: an instance's ruled units written into the essay file, then the
file pushed to its Doc tab through the Doc road (`gdocs.push_doc`, the
code behind `authorlm doc push`).

The rules of this module:

- **When.** The rulings land when a step that rules text ends: an advance
  or a skip past construction, the wrap-up or the filters. Also on demand:
  `authorlm workflow land <instance>` and Land on the Workflows page,
  which land every ruling so far, the current step's included. Not on
  each decision: a unit is often reopened, redone or edited several times
  before its step ends, and a landing per click would rewrite the file and
  push the Doc tab once per click (each push its own conflict check and
  Google round trip), showing half-worked text in the Doc the author reads.
  A step's end is the point at which its rulings stand as a set.
- **Matched by text, never by position.** Each ruled unit lands on its
  original text: the text the unit had when it was ruled (`unit_before`
  on the ruling turn; the walk's text for turns from before that column).
  It is found in the file as it is now, exactly, else after normalizing
  whitespace, quote marks and dashes; position only breaks a tie between
  two equal matches (the first at or after the previous unit of the same
  walk). Walks are cut from the instance's base text
  (`core.base_text`), so a label names the same text however the file
  moves.
- **When the original is gone.** A unit whose original is not in the file
  (the author edited it since, or an earlier landing changed it) is not
  landed: it is marked `needs-owner` and shown on the page and in
  `workflow status`, with both texts. `land --leave <label>` (Leave on the
  page) settles it as the file has it. Finding the unit's new text
  somewhere in the file is not taken as landed: the same words may stand
  elsewhere, and a false "landed" is worse than asking.
- **Beat by beat.** A beat walk builds text that has no original to match
  (a new paragraph, a new essay), so it lands whole: the stretch's text
  replaces the file only while the file still is the text the stretch
  started from (or the text its last landing wrote); otherwise the whole
  stretch needs the owner.
- **The Doc.** Nothing is written while the Doc road says no: a linked tab
  with edits not in the file (pull first), forms pending in it, or no
  Google sign-in. After the file is written the tab is pushed with the
  road's own guard; if the push stops, the file is put back and nothing
  counts as landed, so the file and the tab never part. A file with no
  tab lands in the file only, and says so.
- **Reversible.** Each landing run keeps the file before and after it
  (`workflow_land_runs`). A reopen of a landed unit takes it back out
  (`revert`): its new text is replaced by its original, matched by text.
- **No file is special-cased.** `register = "protected"` does not block a
  landing; the toc's `edits = "owner"` (a general attribute: no automated
  pass writes to such a file) does, with its reason.
- Idempotent: each unit's landing is keyed by its ruling turn, so landing
  again lands nothing twice.
"""

from __future__ import annotations

import contextlib
import io
import unicodedata
from pathlib import Path
from typing import Any

from .. import structure
from ..db import Database, ko_fields, now_iso
from . import core

TEXT_STEPS = ("construction", "wrapup", "filters")
WHOLE = "beats"
CHANGED = ("accepted", "edited")


# ------------------------------------------------------------- the Doc road

class GoogleDocRoad:
    """The Doc road for one workspace: the guard before a landing, and
    the guarded push after it. Google is reached only for a file whose
    tab has been pushed before."""

    def __init__(self, workspace: str | None = None):
        self.workspace = workspace
        self._services = None

    def _linked(self, db: Database, manuscript: dict, rel: str) -> bool:
        from .. import gdocs
        return gdocs.has_pushed_tab(db, manuscript, rel)

    def services(self):
        if self._services is None:
            from .. import api, gdocs
            config = api.load_config(self.workspace)
            self._services = (
                gdocs.get_service(config, self.workspace, interactive=False),
                gdocs.get_docs_service(config, self.workspace,
                                       interactive=False))
        return self._services

    def check(self, db: Database, manuscript: dict, rel: str) -> str | None:
        """Why nothing may be written now, or None."""
        from .. import gdocs
        if not self._linked(db, manuscript, rel):
            return None
        pending = gdocs.forms_pending(db, manuscript["id"], rel)
        if pending:
            return (f"{rel}'s tab carries {pending} forms; settle them by "
                    "their own resolve first")
        try:
            service, docs_service = self.services()
        except Exception as err:  # noqa: BLE001 — no sign-in, said
            return (f"{rel} has a Doc tab and there is no Google sign-in now "
                    f"({err}); run 'authorlm doc auth' in a terminal")
        state = gdocs.doc_side_state(db, manuscript, rel, service,
                                     docs_service)["state"]
        if state in ("doc_ahead", "conflict"):
            return (f"the Doc tab of {rel} has edits that are not in the file; "
                    f"pull them first ('authorlm doc pull {rel}', or 'authorlm "
                    "doc merge' when both moved), then land again")
        return None

    def push(self, db: Database, manuscript: dict, rel: str) -> dict:
        """Bring the tab up to the file; raises when the road says no."""
        from .. import gdocs
        if not self._linked(db, manuscript, rel):
            return {"pushed": False,
                    "note": f"no Doc tab for {rel} yet: 'authorlm doc push "
                            f"{rel}' makes one"}
        if gdocs.unchanged_since_push(db, manuscript, rel):
            return {"pushed": True, "note": "the tab already has it"}
        service, docs_service = self.services()
        out = gdocs.push_doc(db, manuscript, rel, service=service,
                             docs_service=docs_service, guard=True)
        held = len(out.get("held") or [])
        return {"pushed": True, "url": out.get("url", ""),
                "note": (f"{held} paragraph(s) held under open comments"
                         if held else "")}


# Tests replace this with a fake road; the CLI and the page call it.
def ROAD_FACTORY(db: Database, manuscript: dict, workspace: str | None):
    return GoogleDocRoad(workspace)


def make_road(db: Database, manuscript: dict, workspace: str | None = None):
    return ROAD_FACTORY(db, manuscript, workspace)


# ---------------------------------------------------------------- matching

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'",
                         "‛": "'", "“": '"', "”": '"',
                         "„": '"', "‟": '"', "–": "-",
                         "—": "-", " ": " "})


def _normalized(text: str) -> tuple[str, list[tuple[int, int]]]:
    """The text with whitespace runs collapsed to one space, curly quotes
    straightened and dashes made hyphens, and for each of its characters
    the span of the original it came from."""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            if out and out[-1] != " ":
                out.append(" ")
                spans.append((i, j))
            i = j
            continue
        ch = unicodedata.normalize("NFC", ch).translate(_QUOTES)
        out.append(ch)
        spans.append((i, i + 1))
        i += 1
    while out and out[-1] == " ":
        out.pop()
        spans.pop()
    return "".join(out), spans


def norm(text: str) -> str:
    return _normalized(text or "")[0]


def _all(hay: str, needle: str) -> list[int]:
    out, i = [], hay.find(needle)
    while i >= 0:
        out.append(i)
        i = hay.find(needle, i + 1)
    return out


def find(hay: str, needle: str, cursor: int = 0) -> tuple[int, int, str] | None:
    """(start, end, how) of `needle` in `hay`: exact, else normalized.
    Between equal matches, the first at or after `cursor`."""
    if not needle.strip():
        return None
    hits = _all(hay, needle)
    if hits:
        i = next((h for h in hits if h >= cursor), hits[0])
        return i, i + len(needle), "exact"
    nh, spans = _normalized(hay)
    nn = norm(needle)
    if not nn:
        return None
    hits = _all(nh, nn)
    if not hits:
        return None
    starts = [spans[h][0] for h in hits]
    k = next((j for j, s in enumerate(starts) if s >= cursor), 0)
    h = hits[k]
    return spans[h][0], spans[h + len(nn) - 1][1], "normalized"


# -------------------------------------------------------------------- plan

def _page():
    from .. import workflow_page
    return workflow_page


def _reached(inst: dict, step: str) -> bool:
    return core.STEPS.index(inst["step"]) >= core.STEPS.index(step)


def ruled_units(db: Database, manuscript: dict, inst: dict) -> list[dict]:
    """Every ruling of the instance that changes text, in landing order:
    construction's walks, the wrap-up's items, the filters' walks. Each:
    step, stretch, label, seq, kind (part | whole), original, new, and a
    `moved` reason when the text it was ruled on is not the walk's now."""
    page = _page()
    turns = core.turns(db, inst)
    decisions = core.decisions(db, inst)
    out: list[dict] = []

    def walk_units(step: str) -> None:
        views = page.phase_stretches(db, manuscript, inst, step, turns,
                                     decisions)
        for k, view in enumerate(views, start=1):
            if view["mode"] == "beats":
                ruled = [u for u in view["units"] if u.get("seq")
                         and u["state"] in page.RESOLVED]
                if not ruled or norm(view["text"]) == norm(view["base"]):
                    continue
                out.append({"step": step, "stretch": k, "label": WHOLE,
                            "seq": max(u["seq"] for u in ruled),
                            "kind": "whole", "original": view["base"],
                            "new": view["text"], "moved": ""})
                continue
            for u in view["units"]:
                if u["state"] not in CHANGED or u.get("text", "") == u["before"]:
                    continue
                ruled_on = u.get("ruled_before") or u["before"]
                moved = ("the text this unit was ruled on is not the walk's "
                         "text now" if norm(ruled_on) != norm(u["before"])
                         else "")
                out.append({"step": step, "stretch": k, "label": u["label"],
                            "seq": u["seq"], "kind": "part",
                            "original": ruled_on, "new": u.get("text", ""),
                            "moved": moved})

    if _reached(inst, "construction"):
        walk_units("construction")
    if _reached(inst, "wrapup"):
        for it in page._wrapup(turns)["items"]:
            if it["state"] not in CHANGED or not it.get("seq"):
                continue
            if it.get("text", "") == it["before"]:
                continue
            out.append({"step": "wrapup", "stretch": 1, "label": it["label"],
                        "seq": it["seq"], "kind": "item",
                        "original": it.get("ruled_before") or it["before"],
                        "new": it.get("text", ""), "moved": ""})
    if _reached(inst, "filters"):
        walk_units("filters")
    return out


def _key(u: dict) -> tuple:
    return (u["step"], u["stretch"], u["label"], u["seq"])


def _rows(db: Database, inst: dict) -> dict[tuple, dict]:
    return {(r["step"], r["stretch"], r["label"], r["seq"]): dict(r)
            for r in db.all("SELECT * FROM workflow_landings WHERE "
                            "instance_id = ?", (inst["id"],))}


def _whole_originals(rows: dict, u: dict) -> list[str]:
    """What the file may hold for a whole stretch: the text its last
    landing wrote, then the text it started from."""
    landed = sorted((r for k, r in rows.items()
                     if k[:3] == _key(u)[:3] and r["state"] == "landed"),
                    key=lambda r: r["seq"], reverse=True)
    return [r["new"] for r in landed] + [u["original"]]


def plan(db: Database, manuscript: dict, inst: dict,
         text: str | None = None) -> tuple[str, list[dict]]:
    """(the file's text with every landable unit landed, each ruled unit
    with its `status`: landed | left | land | already | needs-owner, and
    `reason`). Reads only."""
    rows = _rows(db, inst)
    text = (core.file_text(manuscript, inst["file"]) or "") if text is None else text
    units = ruled_units(db, manuscript, inst)
    cursors: dict[tuple, int] = {}
    for u in units:
        row = rows.get(_key(u))
        if row and row["state"] in ("landed", "left"):
            u.update(status=row["state"], reason=row["reason"])
            continue
        if u["moved"]:
            u.update(status="needs-owner", reason=u["moved"])
            continue
        if u["kind"] == "whole":
            if any(norm(text) == norm(o) for o in _whole_originals(rows, u)):
                text = u["new"]
                u.update(status="land", reason="")
            elif norm(text) == norm(u["new"]):
                u.update(status="already", reason="the file already has it")
            else:
                u.update(status="needs-owner", reason=(
                    "the essay file has changed since this beat walk "
                    "started, so its text cannot be landed whole"))
            continue
        cur_key = (u["step"], u["stretch"])
        cursor = cursors.get(cur_key, 0) if u["kind"] == "part" else 0
        hit = find(text, u["original"], cursor)
        if hit is not None:
            i, j, how = hit
            text = text[:i] + u["new"] + text[j:]
            cursors[cur_key] = i + len(u["new"])
            u.update(status="land", reason="" if how == "exact"
                     else "matched after normalizing spacing, quotes, dashes")
        else:
            u.update(status="needs-owner", reason=(
                "its original text is no longer in the essay (edited since "
                "it was ruled, or changed by an earlier landing)"))
    return text, units


# -------------------------------------------------------------------- land

def _owner_only(manuscript: dict, rel: str) -> str | None:
    try:
        toc = (Path(manuscript["path"]) / structure.TOC_FILENAME).read_text(
            encoding="utf-8")
    except OSError:
        return None
    return structure.owner_edits_reason(toc, rel)


def _write(manuscript: dict, rel: str, text: str | None) -> None:
    path = Path(manuscript["path"]) / rel
    if text is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _collect(db: Database, manuscript: dict, workspace: str | None) -> None:
    """The landed file into the revision history, attached to no episode
    (a landing is not goal work of its own)."""
    from .. import api
    from ..sessions import NO_EPISODE
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            api.collect(db, manuscript, api.load_config(workspace),
                        source="workflow-land", episode=NO_EPISODE)
    except Exception:  # noqa: BLE001 — history is a convenience, not the landing
        pass


def _record(db: Database, inst: dict, units: list[dict], run: dict) -> None:
    at = now_iso()
    with db.transaction():
        db.insert("workflow_land_runs", run)
        rows = _rows(db, inst)
        for u in units:
            state = {"land": "landed", "already": "landed",
                     "needs-owner": "needs-owner"}.get(u["status"])
            if state is None:
                continue
            row = rows.get(_key(u))
            values = {"state": state, "original": u["original"],
                      "new": u["new"], "reason": u.get("reason", ""),
                      "run_id": run["id"], "updated_at": at}
            if row is None:
                db.insert("workflow_landings", {
                    **ko_fields("wl"), "instance_id": inst["id"],
                    "step": u["step"], "stretch": u["stretch"],
                    "label": u["label"], "seq": u["seq"], **values})
            elif any(row[k] != v for k, v in values.items()
                     if k not in ("run_id", "updated_at")):
                db.update("workflow_landings", row["id"], values)


def _run_row(inst: dict, trigger: str, actor: str, pre: str | None,
             post: str | None, landed: int, pushed: bool, note: str) -> dict:
    return {**ko_fields("wlr"), "instance_id": inst["id"], "file": inst["file"],
            "trigger": trigger, "pre_text": pre, "post_text": post,
            "landed": landed, "pushed": int(pushed), "note": note,
            "actor": actor}


def land(db: Database, manuscript: dict, inst: dict, *, dry_run: bool = False,
         road=None, trigger: str = "verb", actor: str = "cli",
         workspace: str | None = None) -> dict:
    """Land every ruling of the instance not yet landed; `dry_run` says
    what would change and writes nothing. Returns {file, units, written,
    pushed, note, dry_run}."""
    rel = inst["file"]
    result: dict[str, Any] = {"file": rel, "instance": inst["id"],
                              "dry_run": dry_run, "units": [],
                              "written": False, "pushed": False, "note": ""}
    if not _reached(inst, "construction"):
        result["note"] = "nothing is ruled before construction"
        return result
    inst = core.ensure_base(db, manuscript, inst) if not dry_run else inst
    pre = core.file_text(manuscript, rel)
    text, units = plan(db, manuscript, inst, pre or "")
    result["units"] = units
    landing = [u for u in units if u["status"] == "land"]
    rows = _rows(db, inst)
    # Units whose state is new to the record (needs-owner, or found already
    # in the file): recorded even when nothing is written.
    news = [u for u in units
            if (u["status"] == "already"
                or (u["status"] == "needs-owner"
                    and (rows.get(_key(u)) or {}).get("state") != "needs-owner"))]
    if dry_run:
        result["note"] = (f"{len(landing)} unit(s) would land" if landing
                          else "nothing to land")
        return result
    if not landing and not news:
        result["note"] = "nothing to land"
        return result
    why = _owner_only(manuscript, rel) if landing else None
    road = road or make_road(db, manuscript, workspace)
    if landing and why is None:
        why = road.check(db, manuscript, rel)
    if why:
        for u in landing:
            u.update(status="waiting", reason=why)
        result["note"] = f"nothing was landed: {why}"
        with db.transaction():
            db.insert("workflow_land_runs", _run_row(
                inst, trigger, actor, pre, None, 0, False, result["note"]))
        return result
    pushed = {"pushed": False, "note": ""}
    if landing:
        _write(manuscript, rel, text)
        try:
            pushed = road.push(db, manuscript, rel)
        except Exception as err:  # noqa: BLE001 — the road's refusal, said
            _write(manuscript, rel, pre)
            for u in landing:
                u.update(status="waiting", reason="the Doc push stopped")
            result["note"] = (f"nothing was landed: the Doc push stopped, so "
                              f"the file was put back: {err}")
            with db.transaction():
                db.insert("workflow_land_runs", _run_row(
                    inst, trigger, actor, pre, None, 0, False, result["note"]))
            return result
    note = "; ".join(x for x in (
        f"{len(landing)} unit(s) landed in {rel}" if landing else "",
        pushed.get("note", "")) if x)
    _record(db, inst, units, _run_row(
        inst, trigger, actor, pre, text if landing else None, len(landing),
        bool(pushed.get("pushed")), note))
    if landing:
        _collect(db, manuscript, workspace)
    result.update(written=bool(landing), pushed=bool(pushed.get("pushed")),
                  note=note or "nothing new to land", url=pushed.get("url", ""))
    return result


def after_move(db: Database, manuscript: dict, before: dict, after: dict, *,
               road=None, actor: str = "cli",
               workspace: str | None = None) -> dict | None:
    """Land when the instance's step moved past a step that rules text
    (an advance or a skip). None when it did not."""
    a, b = core.STEPS.index(before["step"]), core.STEPS.index(after["step"])
    if b <= a or not any(s in TEXT_STEPS for s in core.STEPS[a:b]):
        return None
    return land(db, manuscript, after, road=road, trigger="step-end",
                actor=actor, workspace=workspace)


def leave(db: Database, manuscript: dict, inst: dict, label: str,
          actor: str = "cli") -> dict:
    """Settle a unit that needs the owner as the file has it: it is not
    landed, and stops being reported."""
    _text, units = plan(db, manuscript, inst)
    hits = [u for u in units if u["label"] == label
            and u["status"] == "needs-owner"]
    if not hits:
        raise LookupError(f"no unit '{label}' waiting for you to land")
    at = now_iso()
    rows = _rows(db, inst)
    with db.transaction():
        for u in hits:
            values = {"state": "left", "original": u["original"],
                      "new": u["new"], "reason": f"left as the file has it ({actor})",
                      "updated_at": at}
            row = rows.get(_key(u))
            if row is None:
                db.insert("workflow_landings", {
                    **ko_fields("wl"), "instance_id": inst["id"],
                    "step": u["step"], "stretch": u["stretch"],
                    "label": u["label"], "seq": u["seq"], "run_id": None,
                    **values})
            else:
                db.update("workflow_landings", row["id"], values)
    return {"left": [u["label"] for u in hits]}


def revert(db: Database, manuscript: dict, inst: dict, step: str, label: str,
           *, road=None, actor: str = "page",
           workspace: str | None = None) -> dict | None:
    """Take a landed unit back out of the essay (a reopen): its new text is
    replaced by its original, matched by text. None when it never landed.
    When its landed text is gone from the file it is marked needs-owner
    and the file is left alone."""
    rows = [r for r in _rows(db, inst).values() if r["step"] == step
            and r["label"] == label and r["state"] == "landed"]
    if not rows:
        return None
    row = max(rows, key=lambda r: (r["stretch"], r["seq"]))
    rel = inst["file"]
    pre = core.file_text(manuscript, rel) or ""
    hit = find(pre, row["new"]) if row["new"] else None
    at = now_iso()
    if hit is None:
        db.update("workflow_landings", row["id"], {
            "state": "needs-owner", "updated_at": at,
            "reason": "reopened, but its landed text is no longer in the "
                      "essay; the file was left as it is"})
        return {"reverted": False, "note": "landed text not found"}
    road = road or make_road(db, manuscript, workspace)
    why = road.check(db, manuscript, rel)
    if why:
        with db.transaction():
            db.insert("workflow_land_runs", _run_row(
                inst, "reopen", actor, pre, None, 0, False,
                f"{label} was not taken back out: {why}"))
        return {"reverted": False, "note": why}
    i, j, _how = hit
    text = pre[:i] + row["original"] + pre[j:]
    _write(manuscript, rel, text)
    try:
        pushed = road.push(db, manuscript, rel)
    except Exception as err:  # noqa: BLE001 — the road's refusal, said
        _write(manuscript, rel, pre)
        with db.transaction():
            db.insert("workflow_land_runs", _run_row(
                inst, "reopen", actor, pre, None, 0, False,
                f"{label} was not taken back out: the Doc push stopped: {err}"))
        return {"reverted": False, "note": str(err)}
    run = _run_row(inst, "reopen", actor, pre, text, 0,
                   bool(pushed.get("pushed")),
                   f"{label} taken back out for a reopen")
    with db.transaction():
        db.insert("workflow_land_runs", run)
        db.update("workflow_landings", row["id"], {
            "state": "reverted", "run_id": run["id"], "updated_at": at,
            "reason": "reopened: the original is back in the essay"})
    _collect(db, manuscript, workspace)
    return {"reverted": True, "pushed": bool(pushed.get("pushed"))}


# ------------------------------------------------------------------ status

def status(db: Database, manuscript: dict, inst: dict) -> dict:
    """Where the instance's landing stands (reads only): units landed,
    waiting to land, needing the owner (with both texts), and the last
    run's note."""
    if not _reached(inst, "construction"):
        return {"landed": 0, "waiting": 0, "needs_owner": [], "last": None,
                "file_changed": False}
    _text, units = plan(db, manuscript, inst)
    last = db.one("SELECT created_at, trigger, landed, pushed, note FROM "
                  "workflow_land_runs WHERE instance_id = ? ORDER BY "
                  "created_at DESC, rowid DESC LIMIT 1", (inst["id"],))
    base = inst.get("base_text")
    now = core.file_text(manuscript, inst["file"])
    return {
        "landed": sum(u["status"] in ("landed", "already") for u in units),
        "waiting": sum(u["status"] == "land" for u in units),
        "left": sum(u["status"] == "left" for u in units),
        "needs_owner": [{k: u[k] for k in ("step", "stretch", "label",
                                           "original", "new", "reason")}
                        for u in units if u["status"] == "needs-owner"],
        "last": dict(last) if last else None,
        # The file moved since construction began (the author's edits or
        # a landing): units are still matched by their text.
        "file_changed": base is not None and norm(now or "") != norm(base),
    }


def status_line(st: dict) -> str:
    parts = []
    if st["needs_owner"]:
        parts.append(f"{len(st['needs_owner'])} unit(s) need you, not landed: "
                     + ", ".join(f"{u['step']} {u['label']}"
                                 for u in st["needs_owner"][:5]))
    if st["waiting"]:
        parts.append(f"{st['waiting']} ruled unit(s) land when the step ends "
                     "(or 'workflow land')")
    if st["last"] and st["last"]["note"].startswith("nothing was landed"):
        parts.append(f"last landing: {st['last']['note']}")
    return "; ".join(parts)

