"""`authorlm workflow …`: the per-essay editing workflow from the command
line. Read-only verbs (list, status, show, turns, usage, filters without
--set, brief without --set/--file or with --dry-run, finding without --set
or with --dry-run, keep without --file/--item or with --dry-run,
revise-letter and restart with --dry-run, blurbs without a change, walk,
job without a flag, and every --dry-run of the walk verbs) write nothing.
The verbs that drive the walks are workflow/drive.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import context, core, drive, landing

READ_ONLY = ("list", "status", "show", "turns", "usage", "walk")


def _print(args, data, lines: list[str]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print("\n".join(lines))


def _summary_line(inst: dict) -> str:
    model = f" ({inst['model']})" if inst["model"] else ""
    return (f"{inst['id']}  {inst['file']}  {inst['kind']} #{inst['number']}  "
            f"step: {inst['step']}  engine: {inst['engine']}{model}")


def _show_lines(db, inst: dict) -> list[str]:
    lines = [_summary_line(inst)]
    if inst["brief"]:
        lines.append(f"  brief: {inst['brief']}")
    for which in core.LISTS:
        lines.append(f"  {which}: {', '.join(inst[which]) or '(none)'}")
    lines.append(f"  construction: {inst['construction_mode']}, by "
                 f"{inst['unit']}; filters by {inst['filter_unit']}")
    ticked = ", ".join(inst["filters"]) or "(none)"
    lines.append(f"  filters: {ticked}"
                 + ("" if inst["filters_confirmed"]
                    else " (carried over, not yet chosen this instance)"))
    if inst["carried_from"]:
        lines.append(f"  choices carried from {inst['carried_from']}")
        since = core.since_last(db, inst)
        if since["added"] or since["removed"]:
            lines.append(f"  since then: added {', '.join(since['added']) or '-'};"
                         f" removed {', '.join(since['removed']) or '-'}")
    why = core.gate(db, inst)
    if inst["step"] != "done":
        lines.append(f"  next: {core._next_step(inst['step'])}"
                     + (f" — waits: {why}" if why else " — ready to advance"))
    for d in core.decisions(db, inst):
        if d["kind"] == "brief-change":
            # The texts are on the record (show --json); the board says
            # only that the brief changed, where, and by how much.
            lines.append(f"  decision {d['created_at'][:16]} brief-change at "
                         f"{d['detail'].get('step', '?')} "
                         f"({len(d['detail'].get('from') or '')} → "
                         f"{len(d['detail'].get('to') or '')} chars)")
            continue
        lines.append(f"  decision {d['created_at'][:16]} {d['kind']}: "
                     f"{d['words'] or d['reason'] or '-'} "
                     f"{json.dumps(d['detail'], ensure_ascii=False)}")
    return lines


def _usage_lines(totals: dict) -> list[str]:
    def fmt(t: dict) -> str:
        unknown = t.get("dollars_unknown", 0)
        plural = "s" if unknown != 1 else ""
        if t.get("dollars") is None:
            billed = "unknown" + (f" ({unknown} turn{plural} unknown)"
                                  if unknown else "")
        else:
            billed = f"${t['dollars']:.2f}" + (
                f" + {unknown} turn{plural} unknown" if unknown else "")
        return (f"{t.get('turns', 0)} turns, in {t.get('input', 0)} "
                f"(cached {t.get('cached', 0)}), out {t.get('output', 0)}, "
                f"{billed} billed, "
                f"~${t.get('est_subscription', 0):.2f} subscription")
    lines = [f"total: {fmt(totals['total'])}" if totals["total"]
             else "total: no model turns"]
    for label, key in (("month", "by_month"), ("essay", "by_essay"),
                       ("engine", "by_engine"), ("instance", "by_instance")):
        for name, t in sorted(totals[key].items()):
            lines.append(f"  {label} {name}: {fmt(t)}")
    return lines


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= 160 else text[:157] + "…"


def _land_lines(out: dict | None) -> list[str]:
    """A landing's result: what landed, what waits, what needs the author."""
    if not out:
        return []
    lines = [f"  landing: {out['note']}"]
    for u in out["units"]:
        if u["status"] in ("landed", "left"):
            continue
        word = {"land": "would land" if out["dry_run"] else "landed",
                "already": "already in the file", "waiting": "not landed",
                "needs-owner": "NEEDS YOU, not landed"}.get(u["status"], u["status"])
        lines.append(f"    {u['step']} {u['label']}: {word}"
                     + (f" — {u['reason']}" if u.get("reason") else ""))
        if u["status"] == "needs-owner" or (out["dry_run"] and u["status"] == "land"):
            lines.append(f"      from: {_clip(u['original'])}")
            lines.append(f"      to:   {_clip(u['new'])}")
    return lines


def _finding_lines(f: dict) -> list[str]:
    lines = [f"{f['item']}" + (f" ({f['tier']})" if f["tier"] else "")
             + (f"  verdict: {f['verdict']}" if f["verdict"] else "  not ruled")
             + (f"  edited: {', '.join(f['edited'])}" if f["edited"] else "")]
    for k in core.FINDING_FIELDS:
        if k != "tier" and f[k]:
            lines.append(f"  {k}: {_clip(f[k])}")
    return lines


def _set_pairs(pairs: list[str]) -> dict:
    fields = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"--set takes FIELD=TEXT, not '{pair}' "
                             f"(fields: {', '.join(core.FINDING_FIELDS)})")
        fields[key.strip()] = value
    unknown = sorted(set(fields) - set(core.FINDING_FIELDS))
    if unknown:
        raise ValueError(f"a finding has no field {', '.join(unknown)} "
                         f"(fields: {', '.join(core.FINDING_FIELDS)})")
    return fields


def _finding_verb(db, inst: dict, args) -> None:
    """`workflow finding <i> <item> [--set FIELD=TEXT ...] [--dry-run]`."""
    rows = {r["item_id"]: r for r in core.letter_findings(db, inst)}
    if args.item not in rows:
        raise LookupError(f"no finding '{args.item}' on {inst['id']} "
                          f"(findings: {', '.join(rows) or 'none'})")
    now = core.finding_view(rows[args.item])
    fields = _set_pairs(args.set or [])
    nxt = f"authorlm workflow finding {inst['id']} {args.item} --set what=TEXT"
    if not fields:
        _print(args, {**now, "next": nxt}, _finding_lines(now))
        return
    if args.dry_run:
        changed = [k for k, v in fields.items() if v.strip() != now[k]]
        _print(args, {**now, "dry_run": True, "would_change": changed,
                      "next": nxt},
               _finding_lines(now)
               + [f"  dry run: would change {', '.join(changed) or 'nothing'}; "
                  "nothing written"])
        return
    after = core.edit_finding(db, inst, args.item, fields)
    _print(args, {**after, "next": f"authorlm workflow show {inst['id']}"},
           _finding_lines(after)
           + ["  the next model turn works from this version; the letter's "
              "own words stay on the record"])


def _keep_verb(db, inst: dict, args) -> None:
    """`workflow keep <i> [--file PATH | --item TEXT ...] [--dry-run]`."""
    from .. import workflow_page
    now = workflow_page.letter_keep(db, inst)
    items = None
    if args.file is not None:
        text = (sys.stdin.read() if args.file == "-" else
                Path(args.file).read_text(encoding="utf-8"))
        items = workflow_page._bullets(text.splitlines())
    elif args.item:
        items = list(args.item)
    which = "your list" if now["edited"] else "as the letter wrote it"
    lines = [f"keep ({which}, {len(now['keep'])} item(s)):"] \
        + [f"  - {_clip(k)}" for k in now["keep"]]
    nxt = f"authorlm workflow keep {inst['id']} --file PATH"
    if items is None or args.dry_run:
        if items is not None:
            lines.append(f"  dry run: would replace it with {len(items)} "
                         "item(s); nothing written")
        _print(args, {**now, "dry_run": items is not None,
                      "proposed": items, "next": nxt}, lines)
        return
    out = workflow_page.edit_keep(db, inst, items)
    _print(args, {**out, "next": f"authorlm workflow show {inst['id']}"},
           [f"keep (your list, {len(out['keep'])} item(s)):"]
           + [f"  - {_clip(k)}" for k in out["keep"]]
           + ["  the next model turn keeps these; the letter's own list "
              "stays on the record"])


def _revise_verb(db, manuscript, inst: dict, args) -> None:
    """`workflow revise-letter <i> [--note TEXT] [--dry-run]`: one model
    turn, run here (billed on a billed engine)."""
    from .. import workflow_page as page

    note = (args.note or "").strip()
    findings = [core.finding_view(f) for f in core.letter_findings(db, inst)]
    k = len(core.revisions(db, inst)) + 1
    head = (f"{inst['id']}  {inst['file']}  at {inst['step']}: revision {k} of "
            f"the letter on {inst['engine']}"
            + (f" ({inst['model']})" if inst["model"] else ""))
    nxt = f"authorlm workflow show {inst['id']}"
    if args.dry_run:
        if inst["step"] not in core.LETTER_STEPS:
            raise ValueError("the letter is revised from the letter step to the "
                             f"filters; the instance is at {inst['step']}")
        if page._letter_turn(core.turns(db, inst)) is None:
            raise ValueError("the letter is not written yet; write it first")
        ruled = sum(1 for f in findings if f["verdict"])
        _print(args, {"id": inst["id"], "dry_run": True, "revision": k,
                      "step": inst["step"], "engine": inst["engine"],
                      "findings_in_force": len(findings), "ruled": ruled,
                      "note": note, "next": f"authorlm workflow revise-letter "
                                            f"{inst['id']}"},
               [head, f"  the letter in force: {len(findings)} finding(s), "
                      f"{ruled} ruled; it would be kept as superseded",
                f"  note: {note or '(none)'}",
                "  dry run: no model call, nothing written"])
        return
    # The engines find the book by the workspace the page would name.
    page._WORKSPACE.set(getattr(args, "workspace", None))
    page.revise_letter(db, manuscript, inst, note)
    after = [core.finding_view(f) for f in core.letter_findings(db, inst)]
    lines = [head, f"  the new letter is in force: {len(after)} finding(s), "
                   "none ruled yet; the earlier one is kept as superseded"]
    for f in after:
        lines.append(f"  {f['item']}" + (f" ({f['tier']})" if f["tier"] else "")
                     + f": {_clip(f['title'])}")
    lines.append(f"  next: rule each finding on the Workflows page, or "
                 f"{nxt} --json")
    _print(args, {"id": inst["id"], "revision": k,
                  "findings": after, "next": nxt}, lines)


def _restart_verb(db, manuscript, inst: dict, args) -> None:
    """`workflow restart <i> --reason TEXT [--brief TEXT | --brief-file
    PATH] [--dry-run]`."""
    brief = None
    if args.brief_file is not None:
        brief = (sys.stdin.read() if args.brief_file == "-" else
                 Path(args.brief_file).read_text(encoding="utf-8"))
    elif args.brief is not None:
        brief = args.brief
    if args.dry_run:
        core._one_sentence(args.reason, "starting over")
        if inst["step"] == "done":
            raise ValueError("the instance is done; start a new one instead")
        kept = inst["brief"] if brief is None else brief.strip()
        _print(args, {"id": inst["id"], "dry_run": True, "brief": kept,
                      "next": f"authorlm workflow restart {inst['id']} "
                              "--reason TEXT"},
               [_summary_line(inst),
                f"  dry run: would close it as abandoned ({args.reason}) and "
                f"start {inst['kind']} #{inst['number'] + 1} of {inst['file']} "
                "with its lists, engine, units, filters and "
                + ("its brief" if brief is None else "the new brief")
                + "; nothing written"])
        return
    new = core.restart(db, manuscript, inst, args.reason, brief=brief)
    carry = new.pop("carry")
    _print(args, {**new, "carry": carry,
                  "next": f"authorlm workflow brief {new['id']} --set TEXT"},
           [f"abandoned {inst['id']}: {args.reason.strip()}"]
           + _show_lines(db, new)
           + ["  the brief is editable before the first turn: authorlm "
              f"workflow brief {new['id']} --set TEXT"])


def _road(db, manuscript, args):
    return landing.make_road(db, manuscript, getattr(args, "workspace", None))


def cmd_workflow(args):
    from ..cli import _manuscript, _open_db

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    verb = args.action
    try:
        if verb in drive.VERBS:
            drive.run_verb(db, manuscript, args)
        elif verb == "start":
            inst = core.start(db, manuscript, args.essay, args.kind,
                              brief=args.brief or "", engine=args.engine,
                              model=args.model, unit=args.unit, mode=args.mode)
            carry = inst.pop("carry")
            lines = [_summary_line(inst)] + _show_lines(db, inst)[1:]
            if carry["dropped"]:
                lines.append("  dropped from the lists (no longer in the "
                             f"book): {json.dumps(carry['dropped'])}")
            if inst["engine"] == "openai-api":
                lines.append("  openai-api is billed: every turn logs its "
                             "cost ('authorlm workflow usage')")
            _print(args, {**inst, "carry": carry}, lines)
        elif verb == "list":
            rows = core.instances(db, manuscript, args.essay)
            _print(args, rows, [_summary_line(r) for r in rows]
                   or ["no workflow instances"])
        elif verb == "status":
            rows = core.status(db, manuscript, args.essay)
            lines = []
            for r in rows:
                st = landing.status(db, manuscript, core.get_instance(
                    db, manuscript, r["instance"]))
                r["landing"] = st
                lines.append(
                    f"{r['file']}: {r['kind']} #{r['number']} at {r['step']}"
                    + (f" → {r['next']}" if r["next"] else "")
                    + (f" (waits: {r['waits_for']})" if r["waits_for"]
                       and r["step"] != "done" else "")
                    + f"  [{r['instance']}]")
                if landing.status_line(st):
                    lines.append(f"  landing: {landing.status_line(st)}")
            _print(args, rows, lines or ["no workflow instances"])
        elif verb == "show":
            inst = core.get_instance(db, manuscript, args.instance)
            _print(args, {**inst, "gate": core.gate(db, inst),
                          "decisions": core.decisions(db, inst),
                          "since_last": core.since_last(db, inst),
                          "letter_findings": [core.finding_view(f) for f in
                                              core.letter_findings(db, inst)]},
                   _show_lines(db, inst))
        elif verb == "turns":
            inst = core.get_instance(db, manuscript, args.instance)
            rows = core.turns(db, inst)
            lines = []
            for t in rows:
                lines.append(f"#{t['seq']} {t['step']} {t['unit_label'] or ''} "
                             f"{t['action']}"
                             + (f" [{', '.join(t['blurbs'])}]" if t["blurbs"] else "")
                             + (f" via {t['engine']}"
                                + (f" ({t['model']})" if t["model"] else "")
                                if t["engine"] else ""))
                if t["author_text"]:
                    lines.append(f"    author: {t['author_text']}")
                if t["reason"]:
                    lines.append(f"    reason: {t['reason']}")
                if t["proposed"]:
                    lines.append(f"    proposed: {t['proposed']}")
                elif t["reply"]:
                    lines.append(f"    reply: {t['reply']}")
                for r in t["requests"]:
                    lines.append(f"    asked for {r['name']} "
                                 f"{json.dumps(r['args'], ensure_ascii=False)}"
                                 f" ({r['chars']} chars)")
            _print(args, rows, lines or ["no turns yet"])
        elif verb == "usage":
            totals = core.usage(db, manuscript, args.essay, args.month)
            _print(args, totals, _usage_lines(totals))
        elif verb == "lists":
            inst = core.get_instance(db, manuscript, args.instance)
            inst = core.change_list(db, manuscript, inst, args.list, args.essay,
                                    add=args.change == "add",
                                    words=args.words or "")
            _print(args, inst, _show_lines(db, inst))
        elif verb == "set-unit":
            inst = core.get_instance(db, manuscript, args.instance)
            inst = core.set_unit(db, inst, args.unit, phase=args.phase,
                                 mode=args.mode, reason=args.reason)
            _print(args, inst, _show_lines(db, inst))
        elif verb == "set-engine":
            inst = core.get_instance(db, manuscript, args.instance)
            inst = core.set_engine(db, inst, args.engine, args.model, args.reason)
            _print(args, inst, _show_lines(db, inst))
        elif verb == "filters":
            inst = core.get_instance(db, manuscript, args.instance)
            order = context.standard_order()
            if args.set is not None:
                inst = core.set_filters(db, inst, args.set.split(","), order)
            _print(args, {"filters": inst["filters"],
                          "confirmed": inst["filters_confirmed"],
                          "standard_order": order},
                   [f"selected: {', '.join(inst['filters']) or '(none)'}"
                    + ("" if inst["filters_confirmed"]
                       else " (pre-ticked; not yet chosen)"),
                    f"standard order: {', '.join(order) or '(not written yet)'}"])
        elif verb in ("skip", "advance"):
            before = core.get_instance(db, manuscript, args.instance)
            inst = (core.skip(db, before, args.reason, args.to)
                    if verb == "skip" else core.advance(db, before))
            out = landing.after_move(db, manuscript, before, inst,
                                     road=_road(db, manuscript, args),
                                     workspace=getattr(args, "workspace", None))
            _print(args, {**inst, "landed": out},
                   _show_lines(db, inst) + _land_lines(out))
        elif verb == "land":
            inst = core.get_instance(db, manuscript, args.instance)
            if args.leave:
                left = landing.leave(db, manuscript, inst, args.leave)
                _print(args, left, [f"left as the file has it: "
                                    f"{', '.join(left['left'])}"])
            else:
                out = landing.land(db, manuscript, inst, dry_run=args.dry_run,
                                   road=None if args.dry_run
                                   else _road(db, manuscript, args),
                                   trigger="verb", workspace=getattr(args, "workspace", None))
                _print(args, out, [f"{inst['file']} [{inst['id']}]"]
                       + _land_lines(out))
        elif verb == "brief":
            inst = core.get_instance(db, manuscript, args.instance)
            text = None
            if args.file is not None:
                text = (sys.stdin.read() if args.file == "-" else
                        Path(args.file).read_text(encoding="utf-8"))
            elif args.set is not None:
                text = args.set
            if text is None or args.dry_run:
                lines = [_summary_line(inst)]
                if text is not None:
                    new = text.strip()
                    same = new == (inst["brief"] or "").strip()
                    lines.append(
                        "  dry run: the brief is unchanged" if same else
                        f"  dry run: would change the brief from "
                        f"{len(inst['brief'] or '')} to {len(new)} chars; "
                        "nothing written")
                lines.append(f"  brief: {inst['brief'] or '(none)'}")
                _print(args, {"id": inst["id"], "brief": inst["brief"],
                              "dry_run": text is not None,
                              "next": f"authorlm workflow brief {inst['id']} "
                                      "--set TEXT | --file PATH"}, lines)
            else:
                inst = core.set_brief(db, inst, text)
                _print(args, inst, [_summary_line(inst),
                                    f"  brief: {inst['brief'] or '(none)'}",
                                    "  the next model turn reads this brief; "
                                    "earlier turns keep the one they had"])
        elif verb == "finding":
            inst = core.get_instance(db, manuscript, args.instance)
            _finding_verb(db, inst, args)
        elif verb == "keep":
            inst = core.get_instance(db, manuscript, args.instance)
            _keep_verb(db, inst, args)
        elif verb == "revise-letter":
            inst = core.get_instance(db, manuscript, args.instance)
            _revise_verb(db, manuscript, inst, args)
        elif verb == "restart":
            inst = core.get_instance(db, manuscript, args.instance)
            _restart_verb(db, manuscript, inst, args)
        elif verb == "decide":
            inst = core.get_instance(db, manuscript, args.instance)
            d = core.decide(db, inst, args.kind, args.words, args.reason or "")
            _print(args, d, [f"recorded {d['kind']}: {d['words']}"])
        elif verb == "blurbs":
            mid = manuscript["id"]
            if args.add:
                core.add_blurbs(db, mid, args.add)
            for text in args.hide or []:
                core.hide_blurb(db, mid, text, True)
            for text in args.unhide or []:
                core.hide_blurb(db, mid, text, False)
            rows = core.blurb_list(db, mid, hidden=args.all)
            _print(args, rows, [r["text"] + (" (hidden)" if r["hidden"] else "")
                                for r in rows] or ["no blurbs yet"])
    except (LookupError, ValueError) as err:
        sys.exit(f"error: {err}")


def add_parser(sub, common) -> None:
    """`authorlm workflow <verb>`: one leaf parser per verb."""
    p = sub.add_parser(
        "workflow",
        help="the per-essay editing workflow: start an instance, its lists, "
             "engine, unit and filters, the step gates, skips, turns, usage")
    verbs = p.add_subparsers(dest="action", required=True)

    def leaf(name: str, text: str, example: str = ""):
        parser = verbs.add_parser(name, parents=[common], help=text,
                                  description=text,
                                  epilog=f"example: {example}" if example else None)
        parser.add_argument("--json", action="store_true",
                            help="machine-readable output")
        parser.set_defaults(func=cmd_workflow)
        return parser

    def instance(parser):
        parser.add_argument("instance",
                            help="an instance id (wi-…), or an essay for its "
                                 "latest instance")

    q = leaf("start", "start a new instance of an essay's workflow; lists, "
                      "engine, units and filters carry over from the last one")
    q.add_argument("essay")
    q.add_argument("--kind", required=True, choices=core.KINDS)
    q.add_argument("--brief", help="what to build or improve this time")
    q.add_argument("--engine", choices=core.ENGINES)
    q.add_argument("--model")
    q.add_argument("--unit", choices=core.UNITS,
                   help="construction unit (default: paragraph)")
    q.add_argument("--mode", choices=core.MODES,
                   help="construction mode (new: beats; refine and "
                        "re-review: apply-findings)")

    q = leaf("list", "every instance (read-only)")
    q.add_argument("essay", nargs="?")
    q = leaf("status", "each essay's latest instance, its step and what the "
                       "next step waits for (read-only)")
    q.add_argument("essay", nargs="?")
    q = leaf("show", "one instance in full (read-only)")
    instance(q)
    q = leaf("turns", "the instance's session, turn by turn (read-only)")
    instance(q)
    q = leaf("usage", "model usage totals per month, essay, engine and "
                      "instance (read-only)")
    q.add_argument("--essay")
    q.add_argument("--month", help="YYYY-MM")

    q = leaf("lists", "add an essay to, or remove it from, the reference, "
                      "voice or aware list")
    q.add_argument("change", choices=["add", "remove"])
    instance(q)
    q.add_argument("list", choices=core.LISTS)
    q.add_argument("essay")
    q.add_argument("--words", help="the author's reason, if any")

    q = leaf("set-unit", "change the unit (paragraph|section) or the "
                         "construction mode; mid-instance takes --reason")
    instance(q)
    q.add_argument("unit", nargs="?", choices=core.UNITS)
    q.add_argument("--for", dest="phase", choices=["construction", "filters"],
                   help="which unit (default: the current step's)")
    q.add_argument("--mode", choices=core.MODES)
    q.add_argument("--reason")

    q = leaf("set-engine", "switch the engine or model; mid-instance takes "
                           "--reason")
    instance(q)
    q.add_argument("engine", choices=core.ENGINES)
    q.add_argument("--model")
    q.add_argument("--reason")

    q = leaf("filters", "the filter selection: without --set, show it "
                        "(read-only); --set a,b,c chooses it at the filter step")
    instance(q)
    q.add_argument("--set", metavar="A,B,C")

    q = leaf("skip", "move past the step's gate with one sentence of reason "
                     "(lands the rulings of the steps passed, as advance does)")
    instance(q)
    q.add_argument("--reason", required=True)
    q.add_argument("--to", choices=core.STEPS[1:],
                   help="skip to this later step (default: the next)")

    q = leaf("advance", "move to the next step when its gate is met; "
                        "leaving construction, the wrap-up or the filters "
                        "lands their rulings in the essay and its Doc tab")
    instance(q)

    q = leaf("land", "land the instance's ruled units in the essay file and "
                     "push its Doc tab (each unit matched by its original "
                     "text; one whose original is gone waits for you)")
    instance(q)
    q.add_argument("--dry-run", action="store_true",
                   help="show what would land and what needs you; write "
                        "nothing (read-only)")
    q.add_argument("--leave", metavar="LABEL",
                   help="settle a unit that needs you as the file has it: "
                        "it is not landed and no longer reported")

    q = leaf("brief", "the author's brief: without --set or --file, show it "
                      "(read-only); --set TEXT or --file PATH replaces it at "
                      "any step of an open instance (the next model turn "
                      "reads it; earlier turns keep theirs)")
    instance(q)
    src = q.add_mutually_exclusive_group()
    src.add_argument("--set", metavar="TEXT", help="the new brief, whole")
    src.add_argument("--file", metavar="PATH",
                     help="read the new brief from a file ('-' for stdin)")
    q.add_argument("--dry-run", action="store_true",
                   help="say what would change; write nothing (read-only)")

    q = leaf("finding", "a letter finding as it stands: without --set, show it "
                        "(read-only); --set FIELD=TEXT edits a field (title, "
                        "where, what, why, direction, tier) at the letter "
                        "step, before or after its verdict; the model's "
                        "words stay on the record",
             "authorlm workflow finding wi-123 F2 --set "
             "'direction=cut the second example'")
    instance(q)
    q.add_argument("item", help="the finding's id, e.g. F2")
    q.add_argument("--set", action="append", metavar="FIELD=TEXT",
                   help="a field's new text (repeat for several fields)")
    q.add_argument("--dry-run", action="store_true",
                   help="say what would change; write nothing (read-only)")

    q = leaf("keep", "the letter's Keep list: without --file or --item, show "
                     "it (read-only); --file PATH (one item a line, '-' for "
                     "stdin) or --item TEXT (repeated) replaces the whole "
                     "list at the letter step",
             "authorlm workflow keep wi-123 --item 'the plain opening' "
             "--item 'the closing image'")
    instance(q)
    src = q.add_mutually_exclusive_group()
    src.add_argument("--file", metavar="PATH",
                     help="the new list, one item a line ('-' for stdin)")
    src.add_argument("--item", action="append", metavar="TEXT",
                     help="one item of the new list (repeat for each)")
    q.add_argument("--dry-run", action="store_true",
                   help="say what would change; write nothing (read-only)")

    q = leaf("revise-letter", "revise the letter from the letter step to the "
                              "filters, even after verdicts: a new letter on "
                              "the essay as it now stands becomes the letter "
                              "in force (its findings unruled, each naming "
                              "the earlier one it continues); the earlier "
                              "letter is kept as superseded; accepted units "
                              "stay accepted. One model turn, billed on a "
                              "billed engine (openai-api)",
             "authorlm workflow revise-letter wi-123 --note 'look again at "
             "the second example'")
    instance(q)
    q.add_argument("--note", metavar="TEXT",
                   help="what to look at differently (optional)")
    q.add_argument("--dry-run", action="store_true",
                   help="say what would be revised; no model call, nothing "
                        "written (read-only)")

    q = leaf("restart", "start over from here: close the instance as "
                        "abandoned (kept on record) with one sentence of "
                        "reason, and start a new instance of the essay with "
                        "its lists, engine, units, filters and brief",
             "authorlm workflow restart wi-123 --reason 'the brief missed "
             "the point' --brief 'Tighten the argument for X'")
    instance(q)
    q.add_argument("--reason", required=True, help="one sentence: why start over")
    src = q.add_mutually_exclusive_group()
    src.add_argument("--brief", metavar="TEXT",
                     help="the new instance's brief (default: the old one)")
    src.add_argument("--brief-file", metavar="PATH",
                     help="read the new brief from a file ('-' for stdin)")
    q.add_argument("--dry-run", action="store_true",
                   help="say what would happen; write nothing (read-only)")

    q = leaf("decide", "record a principle the author stated, or a ruling the "
                       "author explicitly marked as one")
    instance(q)
    q.add_argument("--kind", required=True, choices=["principle", "ruling"])
    q.add_argument("--words", required=True, help="the author's words")
    q.add_argument("--reason")

    # Driving the walks (construction, the wrap-up, the filters) as the
    # Workflows page does, through its own calls: workflow/drive.py.
    drive.add_leaves(leaf, instance)

    q = leaf("blurbs", "the manuscript's redo blurbs, most recently used first")
    q.add_argument("--add", action="append", metavar="TEXT")
    q.add_argument("--hide", action="append", metavar="TEXT")
    q.add_argument("--unhide", action="append", metavar="TEXT")
    q.add_argument("--all", action="store_true", help="include hidden ones")
