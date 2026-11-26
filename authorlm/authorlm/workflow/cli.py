"""`authorlm workflow …`: the per-essay editing workflow from the command
line. Read-only verbs (list, status, show, turns, usage, filters without
--set, blurbs without a change) write nothing."""

from __future__ import annotations

import json
import sys

from . import context, core

READ_ONLY = ("list", "status", "show", "turns", "usage")


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


def cmd_workflow(args):
    from ..cli import _manuscript, _open_db

    db = _open_db(args)
    manuscript = _manuscript(db, args)
    verb = args.action
    try:
        if verb == "start":
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
            _print(args, rows, [
                f"{r['file']}: {r['kind']} #{r['number']} at {r['step']}"
                + (f" → {r['next']}" if r["next"] else "")
                + (f" (waits: {r['waits_for']})" if r["waits_for"]
                   and r["step"] != "done" else "")
                + f"  [{r['instance']}]" for r in rows]
                or ["no workflow instances"])
        elif verb == "show":
            inst = core.get_instance(db, manuscript, args.instance)
            _print(args, {**inst, "gate": core.gate(db, inst),
                          "decisions": core.decisions(db, inst),
                          "since_last": core.since_last(db, inst),
                          "letter_findings": core.letter_findings(db, inst)},
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
        elif verb == "skip":
            inst = core.get_instance(db, manuscript, args.instance)
            inst = core.skip(db, inst, args.reason, args.to)
            _print(args, inst, _show_lines(db, inst))
        elif verb == "advance":
            inst = core.get_instance(db, manuscript, args.instance)
            inst = core.advance(db, inst)
            _print(args, inst, _show_lines(db, inst))
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

    def leaf(name: str, text: str):
        parser = verbs.add_parser(name, parents=[common], help=text,
                                  description=text)
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

    q = leaf("skip", "move past the step's gate with one sentence of reason")
    instance(q)
    q.add_argument("--reason", required=True)
    q.add_argument("--to", choices=core.STEPS[1:],
                   help="skip to this later step (default: the next)")

    q = leaf("advance", "move to the next step when its gate is met")
    instance(q)

    q = leaf("decide", "record a principle the author stated, or a ruling the "
                       "author explicitly marked as one")
    instance(q)
    q.add_argument("--kind", required=True, choices=["principle", "ruling"])
    q.add_argument("--words", required=True, help="the author's words")
    q.add_argument("--reason")

    q = leaf("blurbs", "the manuscript's redo blurbs, most recently used first")
    q.add_argument("--add", action="append", metavar="TEXT")
    q.add_argument("--hide", action="append", metavar="TEXT")
    q.add_argument("--unhide", action="append", metavar="TEXT")
    q.add_argument("--all", action="store_true", help="include hidden ones")
