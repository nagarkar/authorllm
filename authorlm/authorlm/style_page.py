"""The Style laws page: a manuscript's style laws by level, to browse, edit,
add, retire, un-retire, move and copy.

A level is the whole manuscript (`all`), one guide of its tree
(`guide:<name>`: the laws defined AT that guide), one essay (`file:<name>`:
its EFFECTIVE law, every law that reaches it, nearest first, in the order
`styles.effective_style` gives the harness, with each displaced law listed
under the law that displaces it), or the workspace defaults (`defaults`,
read-only here: they live in `library/style-defaults.toml`).

Every write is the CLI verb's own function in `api` (`add_style_law`,
`edit_style_law`, `retire_style_law`, `unretire_style_law`,
`move_style_law`, `copy_style_law`) with `actor="web"`, so a change made
here is the change `authorlm style …` would make, and `style show` /
`style history` show it with its road. The reads write nothing.

The page is `/api/style` on the audiobook server (`audiobook_server.py`);
`dispatch` is its transport, as `forms_page.dispatch` is Merge forms'.
"""

from __future__ import annotations

from typing import Any

from . import api
from .db import Database

ACTOR = "web"
METHODS = ("levels", "laws", "history", "similar", "add", "edit", "retire",
           "unretire", "move", "copy")


def _guides(db: Database, mid: str) -> list[dict]:
    """The guide tree, parents before children, siblings by name, each
    with its depth."""
    rows = [dict(g) for g in db.all(
        "SELECT * FROM style_guides WHERE manuscript_id = ? ORDER BY created_at",
        (mid,))]
    ids = {g["id"] for g in rows}
    kids: dict[str | None, list[dict]] = {}
    for g in rows:
        kids.setdefault(g["parent"] if g["parent"] in ids else None, []).append(g)
    out: list[dict] = []
    seen: set[str] = set()

    def walk(parent: str | None, depth: int) -> None:
        for g in sorted(kids.get(parent, []), key=lambda g: g["name"].lower()):
            if g["id"] in seen:
                continue
            seen.add(g["id"])
            out.append({**g, "depth": depth})
            walk(g["id"], depth + 1)
    walk(None, 0)
    return out


def _files(db: Database, manuscript: dict) -> list[str]:
    """The manuscript's files on disk, and any a law or attachment names."""
    from pathlib import Path
    from .revisions import iter_manuscript_paths

    names = set()
    try:
        names |= {n for n in iter_manuscript_paths(Path(manuscript["path"]))
                  if n.endswith(".md")}
    except OSError:
        pass
    for table in ("style_attachments", "style_laws"):
        names |= {r["file"] for r in db.all(
            f"SELECT DISTINCT file FROM {table} WHERE manuscript_id = ? "
            "AND file IS NOT NULL", (manuscript["id"],))}
    return sorted(names)


def levels(db: Database, manuscript: dict) -> dict:
    from . import styles as st

    mid = manuscript["id"]
    guides = _guides(db, mid)
    names = {g["id"]: g["name"] for g in guides}
    counts = {(r["guide_id"], r["status"]): r["n"] for r in db.all(
        "SELECT guide_id, status, COUNT(*) AS n FROM style_laws "
        "WHERE manuscript_id = ? AND guide_id IS NOT NULL GROUP BY guide_id, status",
        (mid,))}
    attached = {a["file"]: names.get(a["guide_id"]) for a in db.all(
        "SELECT file, guide_id FROM style_attachments WHERE manuscript_id = ?", (mid,))}
    root = st.root_guide(db, mid)
    return {
        "manuscript": manuscript["name"],
        "guides": [{"name": g["name"], "depth": g["depth"],
                    "parent": names.get(g["parent"]),
                    "active": counts.get((g["id"], "active"), 0),
                    "retired": counts.get((g["id"], "retired"), 0)}
                   for g in guides],
        "files": [{"file": f, "guide": attached.get(f) or (root["name"] if root else None),
                   "attached": f in attached}
                  for f in _files(db, manuscript)],
        "defaults": len(st.default_laws(mid)),
        "aspects": sorted(st.ASPECTS),
    }


def _bindings(db: Database, manuscript: dict) -> dict[str, list[str]]:
    """law id → the files whose effective law holds it (in force, not
    displaced)."""
    from . import styles as st

    out: dict[str, list[str]] = {}
    for f in _files(db, manuscript):
        for e in st.effective_style(db, manuscript["id"], f):
            out.setdefault(e["id"], []).append(f)
    return out


def _replacement(law: dict, active: list[dict], history: list[dict]) -> dict | None:
    """The law that replaced a retired one: named at retire time, or
    naming it in `overrides` (known); else the most alike active law at the
    same level added after it (likely: retire + re-add before history)."""
    from . import styles as st

    by_id = {a["id"]: a for a in active}
    for h in reversed(history):
        rid = h["detail"].get("replaced_by") if h["event"] == "retire" else None
        if rid and rid in by_id:
            return {"id": rid, "statement": by_id[rid]["statement"], "known": True}
    for a in active:
        if law["id"] in st.overridden_ids(a["overrides"]):
            return {"id": a["id"], "statement": a["statement"], "known": True}
    best, score = None, api.SIMILAR_AT
    for a in active:
        if (a["guide_id"], a["file"]) != (law["guide_id"], law["file"]):
            continue
        if a["created_at"] < law["created_at"]:
            continue
        s = st.similarity(law["statement"], a["statement"])
        if s >= score:
            best, score = a, s
    return ({"id": best["id"], "statement": best["statement"], "known": False}
            if best else None)


def _law(db: Database, row: dict, level: str, bound: dict[str, list[str]],
         hist: dict[str, list[dict]], active: list[dict]) -> dict:
    from . import styles as st

    files = bound.get(row["id"], [])
    out = {
        "id": row["id"], "aspect": row["aspect"], "statement": row["statement"],
        "notes": row.get("notes"), "status": row.get("status", "active"),
        "version": row.get("version", 1), "created_at": row.get("created_at"),
        "created_by": row.get("created_by"), "level": level,
        "overrides": st.overridden_ids(row.get("overrides")),
        "displaced_by": row.get("displaced_by", []),
        "files": len(files), "bound": files,
        "history": hist.get(row["id"], []),
        "readonly": bool(row.get("default")),
        "sent": (["style_law", "checklist"] if row["aspect"] in st.PROSE_ASPECTS
                 else ["checklist"]),
        "chars": len(row["statement"]),
        "replacement": None,
    }
    if out["status"] == "retired":
        out["replacement"] = _replacement(row, active, out["history"])
    return out


def _totals(laws: list[dict]) -> dict:
    live = [x for x in laws if x["status"] == "active" and not x["displaced_by"]]
    by_level: dict[str, dict] = {}
    by_aspect: dict[str, dict] = {}
    for x in live:
        for key, table in ((x["level"], by_level), (x["aspect"], by_aspect)):
            t = table.setdefault(key, {"laws": 0, "chars": 0, "notes": 0})
            t["laws"] += 1
            t["chars"] += x["chars"]
            t["notes"] += len(x["notes"] or "")
    return {"laws": len(live), "chars": sum(x["chars"] for x in live),
            "notes": sum(len(x["notes"] or "") for x in live),
            "by_level": by_level, "by_aspect": by_aspect}


def _parse(level: str) -> tuple[str, str]:
    kind, _, name = (level or "all").partition(":")
    if kind not in ("all", "guide", "file", "defaults"):
        raise ValueError(f"unknown level '{level}'")
    if kind in ("guide", "file") and not name:
        raise ValueError(f"level '{level}' names no {kind}")
    return kind, name


def laws(db: Database, manuscript: dict, level: str) -> dict:
    from . import critic
    from . import styles as st

    mid = manuscript["id"]
    kind, name = _parse(level)
    names = {g["id"]: g["name"] for g in db.all(
        "SELECT id, name FROM style_guides WHERE manuscript_id = ?", (mid,))}
    rows = [dict(r) for r in db.all(
        "SELECT * FROM style_laws WHERE manuscript_id = ? "
        "AND status IN ('active', 'retired') ORDER BY created_at", (mid,))]
    active = [r for r in rows if r["status"] == "active"]
    bound = _bindings(db, manuscript)
    hist = st.histories(db, mid)

    def where(r: dict) -> str:
        if r.get("default"):
            return "workspace default"
        return f"{r['file']} (file-local)" if r["file"] else names.get(r["guide_id"], "?")

    out: dict[str, Any] = {"level": f"{kind}:{name}" if name else kind}
    if kind == "file":
        layers = st.effective_layers(db, mid, name)
        live = [e for e in layers if not e["displaced_by"]]
        # A displaced law sits right under the (first) law displacing it.
        under: dict[str, list[dict]] = {}
        for e in layers:
            if e["displaced_by"]:
                under.setdefault(e["displaced_by"][0], []).append(e)
        ordered: list[dict] = []
        for e in live:
            ordered.append(e)
            ordered.extend(under.pop(e["id"], []))
        for rest in under.values():          # displaced by a law not here
            ordered.extend(rest)
        chain = {g["id"] for g in st.guide_chain(db, mid, name)}
        retired = [r for r in rows if r["status"] == "retired"
                   and (r["file"] == name or r["guide_id"] in chain)]
        out["laws"] = [_law(db, e, where(e), bound, hist, active)
                       for e in ordered + retired]
        out["guide"] = next((g["name"] for g in st.guide_chain(db, mid, name)), None)
        out["rendered"] = {"style_law": st.render(db, mid, name),
                           "checklist": critic.checklist(db, mid, name, [])}
    elif kind == "defaults":
        out["laws"] = [_law(db, {**d, "displaced_by": []}, where(d), bound, hist, active)
                       for d in st.default_laws(mid)]
    elif kind == "guide":
        guide = st.get_guide(db, mid, name)
        if not guide:
            raise LookupError(f"no style guide named '{name}'")
        out["laws"] = [_law(db, r, where(r), bound, hist, active)
                       for r in rows if r["guide_id"] == guide["id"]]
    else:
        out["laws"] = [_law(db, r, where(r), bound, hist, active) for r in rows]
    totals = _totals(out["laws"])
    if kind == "file":
        totals["style_law_chars"] = len(out["rendered"]["style_law"])
        totals["checklist_chars"] = len(out["rendered"]["checklist"])
    out["totals"] = totals
    return out


def _target(to: str) -> dict:
    kind, name = _parse(to)
    if kind == "guide":
        return {"guide_name": name}
    if kind == "file":
        return {"file": name}
    raise ValueError("a law goes to a guide or to one essay")


def _text(params: dict, key: str) -> str | None:
    value = params.get(key)
    return None if value is None else str(value)


def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None) -> Any:
    """The page's API (`/api/style`). Every method names the manuscript."""
    if method not in METHODS:
        raise ValueError(f"unknown method '{method}'")
    db = api.open_db(workspace)
    manuscript = api.get_manuscript(db, params.get("manuscript"))
    if method == "levels":
        return levels(db, manuscript)
    if method == "laws":
        return laws(db, manuscript, str(params.get("level") or "all"))
    if method == "history":
        return api.style_history(db, manuscript, str(params["id"]))
    if method == "similar":
        return api.similar_style_laws(db, manuscript, str(params.get("statement") or ""),
                                      exclude=params.get("exclude"),
                                      **_target(str(params.get("level") or "")))
    if method == "add":
        target = _target(str(params.get("level") or ""))
        statement = str(params.get("statement") or "").strip()
        if not statement:
            raise ValueError("a style law needs a statement")
        similar = api.similar_style_laws(db, manuscript, statement, **target)
        law = api.add_style_law(db, manuscript, str(params.get("aspect") or ""),
                                statement, notes=(_text(params, "notes") or "").strip() or None,
                                actor=ACTOR, **target)
        return {"law": law, "similar": similar}
    law_id = str(params.get("id") or "")
    if method == "edit":
        return api.edit_style_law(db, manuscript, law_id,
                                  statement=_text(params, "statement"),
                                  notes=_text(params, "notes"),
                                  aspect=_text(params, "aspect"), actor=ACTOR)
    if method == "retire":
        reason = str(params.get("reason") or "").strip()
        if not reason:
            raise ValueError("say why the law is retired")
        return api.retire_style_law(db, manuscript, law_id, reason=reason,
                                    actor=ACTOR,
                                    replaced_by=params.get("replaced_by") or None)
    if method == "unretire":
        return api.unretire_style_law(db, manuscript, law_id, actor=ACTOR,
                                      reason=_text(params, "reason"))
    to = _target(str(params.get("to") or ""))
    if method == "move":
        return api.move_style_law(db, manuscript, law_id, actor=ACTOR, **to)
    return api.copy_style_law(db, manuscript, law_id, actor=ACTOR, **to)
