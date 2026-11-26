"""The Workflows page: the per-essay editing workflow in the app.

Design: docs/plans/filter-lens-consolidation.md §11.9 (every decision with
the owner's words) and §12 row 5. The page is `/api/workflow` on the
audiobook server (`audiobook_server.py`); `dispatch` is its transport, as
`style_page.dispatch` is the Style laws page's.

Everything the page writes goes through the workflow package
(`authorlm.workflow.core`): instances, turns, decisions, blurbs, the step
gates. Model turns go through the engine contract
(`workflow.contract.make_engine`, filled by the engines package; tests
register a fake). The page never writes the manuscript file or the Doc:
changes to an essay happen only through forms the author accepts, and the
accepted text of each unit lives on the session record (the turns).

What the page shows for an instance, by step:

- start: the three lists (reference, voice, aware), the engine's
  recommendations with a reason each, accept or decline each (Q1, Q3).
- letter: the findings with their tiers, accept / reject / defer with a
  reason; the keep list, the passes needed or not, the paragraph notes
  (Q4, Q21d, Q21e); a reply box to discuss the letter.
- construction: the walk, one unit at a time (paragraph or section; Q8,
  Q19), or beat by beat with a plan step per beat (Q18). Each unit's form:
  before, the reason, after; accept / reject / redo / edit (Q8a, Q11);
  reopen an accepted unit.
- wrapup: removed passages, placeholders, terms to check, principles with
  their scope (Q23, Q24), each item a form.
- filters: the checklist of passes in the standard order, pre-ticked from
  the last instance and the letter (Q12), then the walk by the filter unit.
- lint: `authorlm book lint`, then advance.

Skips take one sentence of reason; engine and unit switches take a reason
mid-instance (Q6-Q9). Usage totals per instance, essay and month (Q7).
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from typing import Any

from . import api
from .db import Database, loads
from .workflow import context, contract, core

METHODS = ("overview", "instance", "start", "recommend", "rec", "list",
           "letter", "letter_reply", "finding", "accept_letter", "propose",
           "act", "reopen", "beat_done", "wrapup", "principle", "filters",
           "skip", "advance", "engine", "unit", "blurbs", "blurb_add",
           "blurb_hide", "usage")

# The read-only methods: they write nothing (the page polls none of them).
READ_ONLY = ("overview", "instance", "blurbs", "usage")

# Tags the author may put in an edit; any other [...] is an instruction
# at that spot (Q8a; `.claude/skills/authorlm/SKILL.md`).
SUPPORTED_TAGS = ("Illustration", "Footnote", "Suggestion")
LETTER_LABEL = "letter"
RECOMMEND_LABEL = "recommend"
WRAPUP_LABEL = "wrapup"
WRAPUP_KINDS = ("removed", "placeholders", "terms", "principles")
TIERS = ("must", "should", "could")


# ------------------------------------------------------------- the passes

def passes() -> list[dict]:
    """The standard order of the filter passes (library/workflow/order.toml):
    `[[pass]]` entries with name, stage, default; an older `order = [...]`
    list is read too. [] when the library has none."""
    path = context.library_dir() / "order.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out = []
    for p in data.get("pass") or []:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            out.append({"name": p["name"], "stage": p.get("stage", ""),
                        "default": p.get("default", "ticked")})
    if not out:
        out = [{"name": n, "stage": "", "default": "ticked"}
               for n in data.get("order") or [] if isinstance(n, str)]
    return out


def _has_audiobook(manuscript: dict) -> bool:
    try:
        from . import audio
        return (audio.audio_dir(manuscript) / audio.CONFIG_FILENAME).exists()
    except Exception:  # noqa: BLE001 — no audio set up means no audiobook
        return False


# ---------------------------------------------------------------- units

_HEADING = re.compile(r"^#{1,6}\s")


def _blocks(text: str) -> list[str]:
    return [b.strip("\n") for b in re.split(r"\n\s*\n", text or "") if b.strip()]


def paragraphs(text: str) -> list[str]:
    """The essay's paragraphs: blocks between blank lines, headings left
    out (a heading is not a unit of prose)."""
    out = []
    for b in _blocks(text):
        lines = [ln for ln in b.split("\n") if not _HEADING.match(ln)]
        body = "\n".join(lines).strip()
        if body:
            out.append(body)
    return out


def sections(text: str) -> list[str]:
    """The text under each subheading (§11.4). The essay's title line is
    not a section; text before the first subheading is one. An essay
    without subheadings has no sections: it goes by paragraph."""
    lines = (text or "").split("\n")
    if lines and re.match(r"^#\s", lines[0]):
        lines = lines[1:]
    if not any(re.match(r"^#{2,6}\s", ln) for ln in lines):
        return []
    out, cur = [], []
    for ln in lines:
        if re.match(r"^#{2,6}\s", ln) and cur:
            if "\n".join(cur).strip():
                out.append("\n".join(cur).strip())
            cur = []
        cur.append(ln)
    if "\n".join(cur).strip():
        out.append("\n".join(cur).strip())
    return out


def units_of(text: str, unit: str) -> tuple[str, list[str]]:
    """(the unit actually used, the units). Section falls back to
    paragraph when the essay has no subheadings (§11.4)."""
    if unit == "section":
        secs = sections(text)
        if secs:
            return "section", secs
    return "paragraph", paragraphs(text)


def _label(unit: str, n: int) -> str:
    return f"{'s' if unit == 'section' else 'p'}{n}"


# ----------------------------------------------------------- unit state

def _by_label(turns: list[dict], step: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for t in turns:
        if t["step"] == step and t["unit_label"]:
            out.setdefault(t["unit_label"], []).append(t)
    return out


def unit_state(history: list[dict], before: str = "") -> dict:
    """Where one unit stands, from its turns in order.

    - pending: the model's last turn proposes text; the form is open.
    - passed: the model's last turn proposes nothing (a unit with no
      change passes without stopping, Q8).
    - accepted / rejected / edited: the author ruled. Rejecting keeps the
      original; with no original (a new essay) it drops the unit (Q11).
    - open: no turn yet.
    """
    if not history:
        return {"state": "open", "text": before}
    last = history[-1]
    model_turns = [t for t in history if t["engine"]]
    form = model_turns[-1] if model_turns else None
    out: dict[str, Any] = {"turns": len(history)}
    if form:
        out["form"] = {"seq": form["seq"], "reason": form["reason"],
                       "proposed": form["proposed"], "text": form["reply"],
                       "engine": form["engine"], "model": form["model"]}
    if last["action"] == "accept":
        out.update(state="accepted",
                   text=last["author_text"] or (form or {}).get("proposed", ""))
    elif last["action"] == "reject":
        out.update(state="rejected" if before else "dropped", text=before)
    elif last["action"] == "edit" and not last["engine"]:
        out.update(state="edited", text=last["author_text"])
    elif last["engine"] and last["proposed"]:
        out.update(state="pending", text=before)
    elif last["engine"]:
        out.update(state="passed", text=before)
    else:
        out.update(state="open", text=before)
    return out


RESOLVED = ("accepted", "rejected", "dropped", "edited", "passed")


def _walk(db: Database, manuscript: dict, inst: dict, step: str) -> dict:
    """The units of the walk at `step` (construction or filters), each with
    its state and form, and the next label to work."""
    files = core._files(manuscript)
    essay = files.get(inst["file"], "")
    turns = core.turns(db, inst)
    hist = _by_label(turns, step)
    if step == "construction" and inst["construction_mode"] == "beats":
        return _beats_walk(essay, inst, hist)
    want = inst["filter_unit"] if step == "filters" else inst["unit"]
    used, parts = units_of(essay, want)
    units = []
    for i, text in enumerate(parts, start=1):
        label = _label(used, i)
        units.append({"label": label, "kind": used, "n": i, "before": text,
                      **unit_state(hist.get(label, []), text)})
    pending = [u["label"] for u in units if u["state"] == "pending"]
    nxt = pending[0] if pending else next(
        (u["label"] for u in units if u["state"] == "open"), None)
    return {"mode": "walk", "unit": used, "asked_unit": want, "units": units,
            "next": nxt, "end": nxt is None,
            "fallback": used != want}


_BEAT = re.compile(r"^b(\d+):(plan|section|done|p(\d+))$")


def _beats_walk(essay: str, inst: dict, hist: dict) -> dict:
    """Beat by beat (Q18): for each beat (a section), its plan, then its
    text as a whole section or paragraph by paragraph. An existing
    essay's sections are the beats' "before"; a new essay has none."""
    secs = sections(essay) or ([essay.strip()] if essay.strip() else [])
    beats: dict[int, dict] = {}
    for label, h in hist.items():
        m = _BEAT.match(label)
        if not m:
            continue
        b = int(m.group(1))
        beats.setdefault(b, {"plan": None, "text": [], "done": False})
        if m.group(2) == "plan":
            beats[b]["plan"] = (label, h)
        elif m.group(2) == "done":
            beats[b]["done"] = True
        else:
            k = int(m.group(3)) if m.group(3) else 0
            beats[b]["text"].append((k, label, h))
    units = []
    for b in sorted(beats):
        before_sec = secs[b - 1] if b - 1 < len(secs) else ""
        if beats[b]["plan"]:
            label, h = beats[b]["plan"]
            units.append({"label": label, "kind": "plan", "beat": b,
                          "before": "", **unit_state(h, "")})
        paras = paragraphs(before_sec)
        for k, label, h in sorted(beats[b]["text"]):
            before = before_sec if k == 0 else (paras[k - 1] if k - 1 < len(paras) else "")
            units.append({"label": label, "kind": "section" if k == 0 else "paragraph",
                          "beat": b, "n": k, "before": before,
                          **unit_state(h, before)})
    pending = [u["label"] for u in units if u["state"] == "pending"]
    cur = max(beats) if beats else 1
    nxt = pending[0] if pending else None
    if nxt is None:
        if cur not in beats or beats[cur]["done"]:
            cur = cur + 1 if cur in beats else cur
            nxt = f"b{cur}:plan"
        elif not beats[cur]["plan"] or unit_state(beats[cur]["plan"][1])["state"] not in RESOLVED:
            nxt = f"b{cur}:plan"
        elif inst["unit"] == "section":
            done = any(k == 0 and unit_state(h)["state"] in RESOLVED
                       for k, _l, h in beats[cur]["text"])
            nxt = None if done else f"b{cur}:section"
        else:
            k = max([k for k, _l, _h in beats[cur]["text"]] or [0]) + 1
            nxt = f"b{cur}:p{k}"
    return {"mode": "beats", "unit": inst["unit"], "asked_unit": inst["unit"],
            "units": units, "next": nxt, "beat": cur,
            "beats_before": len(secs), "end": False, "fallback": False,
            "beat_open": cur in beats and not beats[cur]["done"]}


def _unit_before(db: Database, manuscript: dict, inst: dict, step: str,
                 label: str) -> str:
    if step == "wrapup":
        item = next((i for i in _wrapup(core.turns(db, inst))["items"]
                     if i["label"] == label), None)
        if item is None:
            raise LookupError(f"no wrap-up item '{label}'")
        return item["text"]
    walk = _walk(db, manuscript, inst, step)
    hit = next((u for u in walk["units"] if u["label"] == label), None)
    if hit is not None:
        return hit["before"]
    if walk["mode"] == "beats":
        m = _BEAT.match(label)
        if not m:
            raise LookupError(f"'{label}' is not a beat label")
        essay = core._files(manuscript).get(inst["file"], "")
        secs = sections(essay) or ([essay.strip()] if essay.strip() else [])
        b = int(m.group(1))
        sec = secs[b - 1] if b - 1 < len(secs) else ""
        if m.group(2) == "plan":
            return ""
        if m.group(3):
            paras = paragraphs(sec)
            k = int(m.group(3))
            return paras[k - 1] if k - 1 < len(paras) else ""
        return sec
    raise LookupError(f"no unit '{label}' at {step}")


# --------------------------------------------------------------- engines

class _EnginesAdapter:
    """Feeds the engines package (`authorlm.engines.get_engine`, build plan
    §12 row 2) from the workflow's instance context when no engine is
    registered on the contract: standing blocks as text, the session ref
    kept on the instance's engine_state."""

    def __init__(self, engine, manuscript: dict, inst: dict):
        self._engine, self._ms, self._inst = engine, manuscript, inst
        self.name, self.model = inst["engine"], inst["model"]

    def send(self, instance_context: dict, message: str) -> dict:
        standing = [f"## {b['name']}\n\n{b['text']}"
                    for b in instance_context["standing"] if b.get("text")]
        standing.append(f"## the essay as it stands\n\n{instance_context['essay']}")
        ctx = {"standing": standing, "turns": instance_context["turns"],
               "session_ref": (self._inst.get("engine_state") or {}).get("session_ref"),
               "manuscript": self._ms["name"], "essay": self._inst["file"],
               "workspace": None}
        reply = self._engine.send(ctx, message)
        self.session_ref = getattr(self._engine, "session_ref", None)
        return reply


def _engine(manuscript: dict, inst: dict):
    try:
        return contract.make_engine(inst["engine"], inst["model"])
    except LookupError:
        try:
            from .engines import get_engine  # type: ignore[attr-defined]
        except ImportError as err:
            raise LookupError(
                f"engine '{inst['engine']}' is not available in this build "
                "(the engines package is not installed)") from err
        return _EnginesAdapter(get_engine(inst["engine"], inst["model"] or None),
                               manuscript, inst)


def _send(db: Database, manuscript: dict, inst: dict, action: str,
          message: str, *, author_text: str | None = None,
          blurbs: list[str] | None = None, unit_label: str = "") -> dict:
    engine = _engine(manuscript, inst)
    turn = core.send_turn(db, manuscript, inst, action, message, engine=engine,
                          author_text=author_text, blurbs=blurbs,
                          unit_label=unit_label)
    ref = getattr(engine, "session_ref", None)
    if ref:
        state = dict(inst.get("engine_state") or {})
        state["session_ref"] = ref
        db.update("workflow_instances", inst["id"],
                  {"engine_state": json.dumps(state)})
    return turn


# ------------------------------------------------------------- the letter

_SECTION = re.compile(
    r"^\s*(?:#+\s*)?[*_]{0,2}(findings|keep|passes|paragraph notes|blurbs)"
    r"[*_]{0,2}\s*:?[*_]{0,2}\s*(.*)$", re.I)
_FINDING = re.compile(r"^\s*(?:#{1,4}\s*)?(?:\*\*)?(?:finding\s+)?(\d+)[.):]\s*(.*)$", re.I)
_FIELD = re.compile(r"^\s*[-*]?\s*\*{0,2}(where|what is wrong|why it matters|"
                    r"direction|tier)\s*:\*{0,2}\s*(.*)$", re.I)
_FIELD_KEY = {"where": "where", "what is wrong": "what",
              "why it matters": "why", "direction": "direction", "tier": "tier"}


def _bullets(lines: list[str]) -> list[str]:
    out = []
    for ln in lines:
        s = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln).strip()
        if s:
            out.append(s)
    return out


def _tier(text: str) -> str:
    m = re.search(r"\b(must|should|could)\b", text or "", re.I)
    return m.group(1).lower() if m else ""


def parse_letter(text: str, pass_names: list[str]) -> dict:
    """The letter's parts from the engine's reply text: findings (each with
    where, what, why, direction, tier), keep, passes (needed or not, with
    the clause of reason), paragraph notes, blurbs. The reply is Markdown
    in the shape letter.md's Output asks for, or a JSON object with the
    same keys."""
    data = _json_in(text)
    if isinstance(data, dict) and "findings" in data:
        findings = []
        for i, f in enumerate(data.get("findings") or [], start=1):
            if isinstance(f, str):
                f = {"title": f}
            f = dict(f)
            f.setdefault("n", i)
            f["tier"] = _tier(str(f.get("tier", "")))
            findings.append(f)
        passes_ = {}
        for p in data.get("passes") or []:
            if isinstance(p, dict) and p.get("name"):
                passes_[p["name"]] = {"needed": bool(p.get("needed")),
                                      "reason": p.get("reason", "")}
        return {"findings": findings, "keep": list(data.get("keep") or []),
                "passes": passes_,
                "notes": list(data.get("paragraph_notes") or data.get("notes") or []),
                "blurbs": list(data.get("blurbs") or [])}
    parts: dict[str, list[str]] = {"preamble": []}
    cur = "preamble"
    for ln in (text or "").split("\n"):
        m = _SECTION.match(ln)
        if m and not _FIELD.match(ln):
            cur = m.group(1).lower()
            parts.setdefault(cur, [])
            if m.group(2).strip():
                parts[cur].append(m.group(2))
            continue
        parts.setdefault(cur, []).append(ln)
    findings: list[dict] = []
    for ln in parts.get("findings", []):
        fm = _FINDING.match(ln)
        field = _FIELD.match(ln)
        if fm and not field:
            findings.append({"n": int(fm.group(1)),
                             "title": fm.group(2).strip(" *"), "lines": [ln]})
            continue
        if not findings:
            continue
        findings[-1]["lines"].append(ln)
        if field:
            key = _FIELD_KEY[field.group(1).lower()]
            findings[-1][key] = field.group(2).strip()
    for f in findings:
        f["text"] = "\n".join(f.pop("lines")).strip()
        f["tier"] = _tier(f.get("tier", ""))
    passes_ = {}
    for ln in parts.get("passes", []):
        low = ln.lower()
        for name in pass_names:
            # The pass's name opens the line; a name inside another pass's
            # reason ("concision: needed — repetition in the middle") is not it.
            if re.match(rf"^\s*(?:[-*•]|\d+[.)])?\s*[*_`]*{re.escape(name)}[*_`]*\s*[:—–-]",
                        low) and name not in passes_:
                needed = "not needed" not in low and "needed" in low
                reason = re.split(r"not needed|needed", ln, maxsplit=1, flags=re.I)
                passes_[name] = {"needed": needed,
                                 "reason": reason[-1].strip(" *:—-–()_,;.")
                                 if len(reason) > 1 else ""}
    return {"findings": findings, "keep": _bullets(parts.get("keep", [])),
            "passes": passes_, "notes": _bullets(parts.get("paragraph notes", [])),
            "blurbs": _bullets(parts.get("blurbs", []))}


def _json_in(text: str) -> Any:
    """The first JSON object in a reply's text, or None."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except json.JSONDecodeError:
        return None


def _letter_turn(turns: list[dict]) -> dict | None:
    """The letter itself: the first model turn labelled letter at the
    letter step (later ones are the discussion)."""
    return next((t for t in turns if t["step"] == "letter"
                 and t["unit_label"] == LETTER_LABEL and t["engine"]
                 and t["action"] == "start"), None)


def _letter_view(db: Database, inst: dict, turns: list[dict]) -> dict:
    lt = _letter_turn(turns)
    names = [p["name"] for p in passes()]
    parsed = parse_letter(lt["reply"], names) if lt else None
    findings = []
    for f in core.letter_findings(db, inst):
        body = loads(f["body"], {}) if isinstance(f["body"], str) else (f["body"] or {})
        findings.append({"item": f["item_id"], "verdict": f["verdict"],
                         "reason": f["reason_given"], "decided": f["decided"],
                         **{k: body.get(k, "") for k in
                            ("n", "title", "where", "what", "why",
                             "direction", "tier", "text")}})
    discussion = [{"seq": t["seq"], "action": t["action"],
                   "author": t["author_text"], "reply": t["reply"],
                   "reason": t["reason"], "at": t["created_at"]}
                  for t in turns if t["step"] == "letter"
                  and t["unit_label"] == LETTER_LABEL and t is not lt]
    return {"drafted": lt is not None,
            "text": lt["reply"] if lt else "",
            "findings": findings,
            "keep": parsed["keep"] if parsed else [],
            "passes": parsed["passes"] if parsed else {},
            "notes": parsed["notes"] if parsed else [],
            "discussion": discussion,
            "accepted_without_findings": any(
                t["step"] == "letter" and t["action"] == "accept"
                and t["unit_label"] == LETTER_LABEL for t in turns)}


def _letter_passes(db: Database, inst: dict) -> dict:
    """The passes the letter marked, from the latest letter of the essay's
    instances (this one first)."""
    names = [p["name"] for p in passes()]
    for cand in [inst] + list(reversed(core.instances(db, {"id": inst["manuscript_id"]},
                                                       inst["file"]))):
        lt = _letter_turn(core.turns(db, cand))
        if lt:
            return parse_letter(lt["reply"], names)["passes"]
    return {}


# --------------------------------------------------------------- wrap-up

def _wrapup(turns: list[dict]) -> dict:
    """The wrap-up's items (Q23, Q24) from the latest wrap-up turn: each
    removed passage, placeholder and term to check is a form item; the
    principles are ticked, not ruled."""
    lt = next((t for t in reversed(turns) if t["step"] == "wrapup"
               and t["unit_label"] == WRAPUP_LABEL and t["engine"]), None)
    if lt is None:
        return {"drafted": False, "items": [], "principles": [], "text": ""}
    data = _json_in(lt["reply"]) or {}
    hist = _by_label(turns, "wrapup")
    items = []
    for kind in WRAPUP_KINDS[:3]:
        for i, raw in enumerate(data.get(kind) or [], start=1):
            raw = raw if isinstance(raw, dict) else {"text": str(raw)}
            label = f"w:{kind}:{i}"
            before = str(raw.get("text") or "")
            h = hist.get(label, [])
            st = unit_state(h, before)
            if "form" not in st:
                # The wrap-up's own proposal is the first form of the item.
                st["form"] = {"seq": lt["seq"], "reason": raw.get("reason", ""),
                              "proposed": raw.get("proposal", ""), "text": "",
                              "engine": lt["engine"], "model": lt["model"]}
                if not h:
                    st["state"] = "pending" if raw.get("proposal") else "open"
            items.append({"label": label, "kind": kind, "before": before, **st})
    principles = [p if isinstance(p, dict) else {"words": str(p), "scope": ""}
                  for p in data.get("principles") or []]
    return {"drafted": True, "items": items, "principles": principles,
            "text": lt["reply"] if not data else ""}


# --------------------------------------------------------------- views

def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _usage_for(db: Database, manuscript: dict, inst: dict) -> dict:
    u = core.usage(db, manuscript)
    month = _month()
    return {"instance": u["by_instance"].get(inst["id"], {}),
            "essay": u["by_essay"].get(inst["file"], {}),
            "month": u["by_month"].get(month, {}), "month_name": month,
            "total": u["total"]}


def overview(db: Database, manuscript: dict) -> dict:
    order = core.reading_order(manuscript)
    rows = core.instances(db, manuscript)
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(
            {"id": r["id"], "kind": r["kind"], "number": r["number"],
             "step": r["step"], "engine": r["engine"], "model": r["model"],
             "created": r["created_at"], "finished": r["finished_at"]})
    essays = [{"file": f, "instances": by_file.pop(f, [])} for f in order]
    # A new essay's workflow names a file not yet in the reading order.
    essays += [{"file": f, "instances": v, "new": True} for f, v in by_file.items()]
    for e in essays:
        open_ = [i for i in e["instances"] if i["step"] != "done"]
        e["open"] = open_[-1]["id"] if open_ else None
    u = core.usage(db, manuscript)
    return {"manuscript": manuscript["name"], "essays": essays,
            "kinds": list(core.KINDS), "steps": list(core.STEPS),
            "engines": [e for e in core.ENGINES if e not in core.PLANNED_ENGINES],
            "planned_engines": list(core.PLANNED_ENGINES),
            "units": list(core.UNITS), "modes": list(core.MODES),
            "usage": {"month": u["by_month"].get(_month(), {}),
                      "month_name": _month(), "total": u["total"]}}


def _filter_checklist(db: Database, manuscript: dict, inst: dict) -> dict:
    """Every pass in the standard order with its tick (Q12): this
    instance's choice once made; else the last instance's ticks (carried
    on the instance), else the pass's default; a pass the letter marks not
    needed starts unticked, and the page says why."""
    order = passes()
    letter = _letter_passes(db, inst)
    audiobook = _has_audiobook(manuscript)
    carried = list(inst["filters"])
    had_last = bool(inst.get("carried_from"))
    out = []
    for p in order:
        name = p["name"]
        why = ""
        if inst["filters_confirmed"]:
            ticked = name in carried
            why = "chosen for this instance"
        elif had_last:
            ticked = name in carried
            why = "ticked last instance" if ticked else "not ticked last instance"
        else:
            d = p["default"]
            ticked = d == "ticked" or (d == "audiobook" and audiobook)
            why = ("the standard default" if d != "audiobook" else
                   "ticked for a manuscript with an audiobook" if audiobook
                   else "unticked: no audiobook")
        mark = letter.get(name)
        if mark and not inst["filters_confirmed"]:
            if not mark["needed"]:
                ticked = False
                why = f"the letter: not needed — {mark['reason']}".rstrip(" —")
            elif mark["needed"] and not ticked:
                ticked = True
                why = f"the letter: needed — {mark['reason']}".rstrip(" —")
        out.append({"name": name, "stage": p["stage"], "ticked": ticked,
                    "why": why, "letter": mark})
    return {"passes": out, "confirmed": inst["filters_confirmed"],
            "audiobook": audiobook}


def instance_view(db: Database, manuscript: dict, ref: str) -> dict:
    inst = core.get_instance(db, manuscript, ref)
    turns = core.turns(db, inst)
    files = core._files(manuscript)
    out: dict[str, Any] = {
        "instance": inst, "gate": core.gate(db, inst),
        "next_step": None if inst["step"] == "done" else core._next_step(inst["step"]),
        "decisions": core.decisions(db, inst),
        "since_last": core.since_last(db, inst),
        "order": core.reading_order(manuscript, files),
        "protected": context.is_protected(files, inst["file"]),
        "usage": _usage_for(db, manuscript, inst),
        "blurbs": core.blurb_list(db, manuscript["id"]),
        "supported_tags": list(SUPPORTED_TAGS),
        "turns": [{k: t[k] for k in ("seq", "step", "unit_label", "action",
                                     "author_text", "blurbs", "reply",
                                     "reason", "proposed", "engine", "model",
                                     "usage", "requests", "created_at")}
                  for t in turns],
        "letter": _letter_view(db, inst, turns),
    }
    out["recommendations"] = _recommendations(turns, inst)
    if inst["step"] in ("construction", "filters"):
        out["walk"] = _walk(db, manuscript, inst, inst["step"])
    if inst["step"] == "wrapup":
        out["wrapup"] = _wrapup(turns)
    if inst["step"] in ("filters", "lint"):
        out["checklist"] = _filter_checklist(db, manuscript, inst)
    out["principles"] = [d for d in out["decisions"] if d["kind"] == "principle"]
    out["skipped"] = [d for d in out["decisions"] if d["kind"] == "skip"]
    return out


def _recommendations(turns: list[dict], inst: dict) -> dict:
    """The start step's recommendations (Q3), each with the author's
    answer: accepted, declined or open."""
    lt = next((t for t in reversed(turns) if t["unit_label"] == RECOMMEND_LABEL
               and t["engine"]), None)
    if lt is None:
        return {"asked": False, "items": [], "text": ""}
    data = _json_in(lt["reply"]) or {}
    answered = {t["unit_label"]: t["action"] for t in turns
                if t["unit_label"].startswith("rec:") and t["seq"] > lt["seq"]}
    items = []
    for r in data.get("recommendations") or []:
        if not isinstance(r, dict) or r.get("list") not in core.LISTS:
            continue
        change = "remove" if r.get("change") == "remove" else "add"
        label = f"rec:{r['list']}:{change}:{r.get('essay', '')}"
        action = answered.get(label)
        items.append({"list": r["list"], "essay": r.get("essay", ""),
                      "change": change, "reason": r.get("reason", ""),
                      "label": label,
                      "answer": {"accept": "accepted", "reject": "declined"}.get(action, "")})
    return {"asked": True, "items": items,
            "text": "" if data else lt["reply"], "seq": lt["seq"]}


# ------------------------------------------------------------- messages

def _accepted_findings(db: Database, inst: dict) -> str:
    rows = [f for f in core.letter_findings(db, inst) if f["verdict"] == "accept"]
    lines = []
    for f in rows:
        body = loads(f["body"], {}) if isinstance(f["body"], str) else (f["body"] or {})
        lines.append(f"- {f['item_id']}"
                     + (f" ({body.get('tier')})" if body.get("tier") else "")
                     + f": {body.get('title') or body.get('what') or ''}")
    return "\n".join(lines)


def _unit_message(db: Database, manuscript: dict, inst: dict, step: str,
                  label: str, before: str) -> str:
    if step == "filters":
        names = ", ".join(inst["filters"]) or "(none chosen)"
        return (f"Unit {label} ({inst['filter_unit']}): apply {names}, in that "
                "order. Mark each change in your reason with the filter that "
                "made it. Return the whole unit in `proposed`, or an empty "
                f"`proposed` when nothing needs changing.\n\n{before}")
    m = _BEAT.match(label)
    if m and inst["construction_mode"] == "beats":
        b = int(m.group(1))
        if m.group(2) == "plan":
            return (f"Beat {b}: propose its plan — what the section is for, the "
                    "point it makes and how it follows from the one before — "
                    "in `proposed`, with your reason."
                    + (f"\n\nThe section as it stands:\n\n{before}" if before else ""))
        hist = _by_label(core.turns(db, inst), "construction")
        plan = unit_state(hist.get(f"b{b}:plan", []))
        plan_text = plan.get("text") or (plan.get("form") or {}).get("proposed", "")
        what = ("the whole section" if m.group(2) == "section"
                else f"paragraph {m.group(3)} of the section")
        return (f"Beat {b}: write {what}, following the accepted plan:\n\n"
                f"{plan_text}\n\nReturn it in `proposed`; an empty `proposed` "
                "means the section is complete."
                + (f"\n\nAs it stands now:\n\n{before}" if before else ""))
    findings = _accepted_findings(db, inst)
    return (f"Unit {label} ({inst['unit']}): propose the text this unit needs, "
            "working the accepted findings and any paragraph note that names "
            "it. Reason first; an empty `proposed` when nothing needs "
            "changing here."
            + (f"\n\nAccepted findings:\n{findings}" if findings else "")
            + f"\n\nThe unit as it stands:\n\n{before}")


def _redo_message(label: str, blurbs: list[str], free: str) -> str:
    parts = [f"Redo {label}."]
    if blurbs:
        parts.append("What is wrong: " + "; ".join(blurbs) + ".")
    if free.strip():
        parts.append(free.strip())
    return " ".join(parts)


def _edit_message(label: str, text: str) -> str:
    return (f"The author edited {label} to the text below. Work in each "
            "[...] note at its place: the supported tags ("
            + ", ".join(f"[{t}: ...]" for t in SUPPORTED_TAGS)
            + ") keep their meaning, and any other bracket is an instruction "
            "to you at that spot. Propose the revision, and say in your "
            f"reason what you did with each note.\n\n{text}")


# ------------------------------------------------------------- actions

def _step_for(inst: dict, label: str) -> str:
    if label.startswith("w:"):
        return "wrapup"
    return inst["step"]


def _check_label_step(inst: dict, step: str) -> None:
    if step not in ("construction", "filters", "wrapup"):
        raise ValueError(f"no units are worked at {inst['step']}")
    if step != inst["step"]:
        raise ValueError(f"the instance is at {inst['step']}, not {step}")


def propose(db: Database, manuscript: dict, inst: dict, label: str | None) -> dict:
    """Ask the engine for the form of a unit: the walk's next unit when no
    label is given."""
    step = inst["step"]
    _check_label_step(inst, step)
    if step == "filters" and not inst["filters_confirmed"]:
        raise ValueError("choose the filters first (the checklist)")
    if label is None:
        walk = _walk(db, manuscript, inst, step)
        label = walk["next"]
        if label is None:
            raise ValueError("the walk has reached the end of the essay; "
                             "advance when you are ready")
    before = _unit_before(db, manuscript, inst, step, label)
    return _send(db, manuscript, inst, "start",
                 _unit_message(db, manuscript, inst, step, label, before),
                 author_text="", unit_label=label)


def act(db: Database, manuscript: dict, inst: dict, label: str, action: str,
        text: str = "", blurbs: list[str] | None = None, free: str = "") -> dict:
    """The four actions on a unit (Q8a, Q11). Accept and reject, and an
    edit with no bracket, are recorded without a model call; a redo, and
    an edit with a bracket in it, ask the engine for the next revision."""
    step = _step_for(inst, label)
    _check_label_step(inst, step)
    blurbs = [b for b in (blurbs or []) if b.strip()]
    if action == "accept":
        hist = _by_label(core.turns(db, inst), step).get(label, [])
        st = unit_state(hist)
        if step == "wrapup" and "form" not in st:
            st = next((i for i in _wrapup(core.turns(db, inst))["items"]
                       if i["label"] == label), st)
        proposed = (st.get("form") or {}).get("proposed", "")
        if not proposed:
            raise ValueError(f"{label} has no proposal to accept")
        return core.record_turn(db, inst, "accept", author_text=proposed,
                                unit_label=label)
    if action == "reject":
        return core.record_turn(db, inst, "reject", unit_label=label)
    if action == "redo":
        if not blurbs and not free.strip():
            raise ValueError("a redo takes a blurb or a few words of what is "
                             "wrong")
        return _send(db, manuscript, inst, "redo",
                     _redo_message(label, blurbs, free),
                     author_text=free.strip(), blurbs=blurbs, unit_label=label)
    if action == "edit":
        if not text.strip():
            raise ValueError("an edit carries the author's text")
        if core.needs_model("edit", text):
            return _send(db, manuscript, inst, "edit", _edit_message(label, text),
                         author_text=text, unit_label=label)
        return core.record_turn(db, inst, "edit", author_text=text,
                                unit_label=label)
    raise ValueError("action is accept, reject, redo or edit")


def reopen(db: Database, manuscript: dict, inst: dict, label: str) -> dict:
    """Go back to a unit already ruled (Q11, confirmed 2026-11-25): the
    engine proposes it again; units after it are kept, and the engine is
    told so."""
    step = _step_for(inst, label)
    _check_label_step(inst, step)
    hist = _by_label(core.turns(db, inst), step).get(label, [])
    if unit_state(hist)["state"] not in RESOLVED:
        raise ValueError(f"{label} is not ruled yet; nothing to reopen")
    before = _unit_before(db, manuscript, inst, step, label)
    kept = unit_state(hist, before).get("text", "")
    return _send(db, manuscript, inst, "reply",
                 f"Reopen {label}: the author goes back to this unit. The units "
                 "after it are kept as they are. Propose it again in the light "
                 f"of everything since.\n\nAs it was kept:\n\n{kept}",
                 author_text=f"(reopened {label})", unit_label=label)


def draft_letter(db: Database, manuscript: dict, inst: dict) -> dict:
    if inst["step"] != "letter":
        raise ValueError(f"the letter is written at the letter step; the "
                         f"instance is at {inst['step']}")
    turns = core.turns(db, inst)
    if _letter_turn(turns):
        raise ValueError("the letter is drafted; discuss it with a reply")
    names = [p["name"] for p in passes()]
    turn = _send(db, manuscript, inst, "start",
                 "Write the editorial letter on this essay now, in `text`, in "
                 "the shape your brief's Output asks for (Findings, Keep, "
                 "Passes, Paragraph notes, Blurbs). The passes, in their fixed "
                 f"order: {', '.join(names) or '(none)'}.",
                 author_text="", unit_label=LETTER_LABEL)
    parsed = parse_letter(turn["reply"], names)
    for i, f in enumerate(parsed["findings"], start=1):
        core.add_finding(db, inst, f"F{f.get('n') or i}",
                         {k: f.get(k, "") for k in ("n", "title", "where", "what",
                                                    "why", "direction", "tier",
                                                    "text")})
    if parsed["blurbs"]:
        core.add_blurbs(db, manuscript["id"], parsed["blurbs"])
    return turn


def start_instance(db: Database, manuscript: dict, p: dict) -> dict:
    inst = core.start(db, manuscript, str(p.get("essay") or ""),
                      str(p.get("kind") or ""),
                      brief=str(p.get("brief") or ""),
                      engine=p.get("engine") or None,
                      model=p.get("model") or None,
                      unit=p.get("unit") or None, mode=p.get("mode") or None)
    return {"id": inst["id"], "carry": inst.get("carry")}


def recommend(db: Database, manuscript: dict, inst: dict) -> dict:
    """Ask the engine for list recommendations (Q3). On a later instance
    it looks only at what changed since the last one."""
    if inst["step"] == "done":
        raise ValueError("the instance is done")
    since = core.since_last(db, inst)
    order = core.reading_order(manuscript)
    lists = "\n".join(f"- {k}: {', '.join(inst[k]) or '(empty)'}" for k in core.LISTS)
    scope = (("This is a later instance: look only at what changed since the "
              f"last one (added: {', '.join(since['added']) or 'none'}; removed: "
              f"{', '.join(since['removed']) or 'none'}), and recommend "
              "additions or removals.")
             if inst.get("carried_from") else
             "Read every summary (essay, section, paragraph); open an essay's "
             "text only when its summary says it matters.")
    return _send(db, manuscript, inst, "start",
                 "Recommend essays for this essay's three lists: reference "
                 "(content binds; never imitated for style), voice (style), "
                 "aware (summaries; keep in mind). " + scope
                 + f"\n\nThe lists now:\n{lists}\n\nThe essays of the book: "
                 + ", ".join(e for e in order if e != inst["file"])
                 + '\n\nPut in `text` only a JSON object: {"recommendations": '
                 '[{"list": "reference|voice|aware", "essay": "<file>", '
                 '"change": "add|remove", "reason": "<one sentence>"}]}.',
                 author_text="", unit_label=RECOMMEND_LABEL)


def answer_rec(db: Database, manuscript: dict, inst: dict, p: dict) -> dict:
    which, essay = str(p.get("list") or ""), str(p.get("essay") or "")
    change = "remove" if p.get("change") == "remove" else "add"
    verdict = p.get("verdict")
    label = f"rec:{which}:{change}:{essay}"
    if verdict == "accept":
        inst = core.change_list(db, manuscript, inst, which, essay,
                                add=change == "add",
                                words=str(p.get("reason") or "recommended"))
        return core.record_turn(db, inst, "accept", unit_label=label)
    if verdict == "decline":
        return core.record_turn(db, inst, "reject", unit_label=label)
    raise ValueError("verdict is accept or decline")


def wrapup(db: Database, manuscript: dict, inst: dict) -> dict:
    if inst["step"] != "wrapup":
        raise ValueError(f"the instance is at {inst['step']}, not wrapup")
    return _send(db, manuscript, inst, "start",
                 "The walk has reached the end. Give the wrap-up (your brief's "
                 "Wrap-up): put in `text` only a JSON object: "
                 '{"removed": [{"text": "<the passage>", "proposal": "<placed '
                 'text or empty to drop>", "reason": "<where, and why>"}], '
                 '"placeholders": [{"text", "proposal", "reason"}], '
                 '"terms": [{"text", "proposal", "reason"}], '
                 '"principles": [{"words": "<the author\'s principle>", '
                 '"scope": "this paragraph | this essay | beyond"}]}.',
                 author_text="", unit_label=WRAPUP_LABEL)


# ------------------------------------------------------------- dispatch

def _blurb_list(p: dict) -> list[str]:
    raw = p.get("blurbs") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(b) for b in raw]


def dispatch(method: str, params: dict[str, Any],
             workspace: str | None = None) -> Any:
    """The page's API (`/api/workflow`). Every method names the
    manuscript; instance methods name the instance as `id`."""
    if method not in METHODS:
        raise ValueError(f"unknown method '{method}'")
    db = api.open_db(workspace)
    manuscript = api.get_manuscript(db, params.get("manuscript"))
    p = params
    if method == "overview":
        return overview(db, manuscript)
    if method == "start":
        return start_instance(db, manuscript, p)
    if method == "blurbs":
        return core.blurb_list(db, manuscript["id"], hidden=bool(p.get("all")))
    if method == "blurb_add":
        return {"added": core.add_blurbs(db, manuscript["id"], _blurb_list(p))}
    if method == "blurb_hide":
        core.hide_blurb(db, manuscript["id"], str(p.get("text") or ""),
                        bool(p.get("hidden", True)))
        return core.blurb_list(db, manuscript["id"], hidden=True)
    if method == "usage":
        return core.usage(db, manuscript, p.get("essay") or None,
                          p.get("month") or None)
    inst = core.get_instance(db, manuscript, str(p.get("id") or ""))
    if method == "instance":
        return instance_view(db, manuscript, inst["id"])
    if method == "recommend":
        recommend(db, manuscript, inst)
    elif method == "rec":
        answer_rec(db, manuscript, inst, p)
    elif method == "list":
        core.change_list(db, manuscript, inst, str(p.get("list") or ""),
                         str(p.get("essay") or ""),
                         add=p.get("change") != "remove",
                         words=str(p.get("words") or ""))
    elif method == "letter":
        draft_letter(db, manuscript, inst)
    elif method == "letter_reply":
        text = str(p.get("text") or "").strip()
        if not text:
            raise ValueError("say something to discuss the letter")
        _send(db, manuscript, inst, "reply", text, unit_label=LETTER_LABEL)
    elif method == "finding":
        core.rule_finding(db, inst, str(p.get("item") or ""),
                          str(p.get("verdict") or ""),
                          str(p.get("reason") or ""), actor="web")
    elif method == "accept_letter":
        if core.letter_findings(db, inst):
            raise ValueError("the letter has findings: rule each of them")
        core.record_turn(db, inst, "accept", unit_label=LETTER_LABEL)
    elif method == "propose":
        propose(db, manuscript, inst, p.get("label") or None)
    elif method == "act":
        act(db, manuscript, inst, str(p.get("label") or ""),
            str(p.get("action") or ""), text=str(p.get("text") or ""),
            blurbs=_blurb_list(p), free=str(p.get("free") or ""))
    elif method == "reopen":
        reopen(db, manuscript, inst, str(p.get("label") or ""))
    elif method == "beat_done":
        walk = _walk(db, manuscript, inst, "construction")
        if walk["mode"] != "beats":
            raise ValueError("beat by beat only")
        if any(u["state"] == "pending" for u in walk["units"]):
            raise ValueError("rule the open form first")
        core.record_turn(db, inst, "accept", unit_label=f"b{walk['beat']}:done")
    elif method == "wrapup":
        wrapup(db, manuscript, inst)
    elif method == "principle":
        scope = str(p.get("scope") or "").strip()
        core.decide(db, inst, "principle", str(p.get("words") or ""),
                    reason=("candidate style rule, ticked at the wrap-up"
                            + (f"; scope: {scope}" if scope else "")))
    elif method == "filters":
        names = p.get("names") or []
        if isinstance(names, str):
            names = names.split(",")
        core.set_filters(db, inst, [str(n) for n in names],
                         [x["name"] for x in passes()])
    elif method == "skip":
        core.skip(db, inst, str(p.get("reason") or ""), p.get("to") or None)
    elif method == "advance":
        core.advance(db, inst)
    elif method == "engine":
        core.set_engine(db, inst, str(p.get("engine") or ""),
                        p.get("model") if p.get("model") is not None else None,
                        p.get("reason") or None)
    elif method == "unit":
        core.set_unit(db, inst, p.get("unit") or None, phase=p.get("phase") or None,
                      mode=p.get("mode") or None, reason=p.get("reason") or None)
    return instance_view(db, manuscript, inst["id"])
