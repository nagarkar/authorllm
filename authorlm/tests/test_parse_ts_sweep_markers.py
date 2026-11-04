"""`clients._parse_ts` and `clients.sweep_markers` bound marker lifetime.

SessionStart unlinks client markers older than 24 h. Parsing must accept
the stamped formats (with/without fractional seconds, Z or offset) and
refuse junk; sweep must keep fresh markers, drop stale ones, and treat
unparseable stamps as expired. A silent change either leaves ghost
sessions forever or deletes live markers mid-chat.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import clients
from authorlm.clients import _parse_ts, sweep_markers


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_parse_accepted_formats() -> None:
    cases = (
        "2026-10-14T10:00:00.123456Z",
        "2026-10-14T10:00:00Z",
        "2026-10-14T10:00:00.123456+00:00",
        "2026-10-14T10:00:00+00:00",
    )
    for raw in cases:
        got = _parse_ts(raw)
        check(f"{raw!r} parses", got is not None, repr(got))
        check(f"{raw!r} is timezone-aware UTC",
              got is not None and got.tzinfo is not None
              and got.utcoffset() is not None
              and got.utcoffset().total_seconds() == 0,
              repr(got))


def test_parse_rejects_junk() -> None:
    for raw in (None, 123, "", "not-a-date", "2026/10/14",
                "2026-10-14 10:00:00"):
        got = _parse_ts(raw)
        check(f"{raw!r} yields None", got is None, repr(got))


def test_naive_z_gets_utc() -> None:
    got = _parse_ts("2026-10-14T10:00:00Z")
    check("Z form is treated as UTC",
          got == datetime(2026, 10, 14, 10, 0, 0, tzinfo=timezone.utc),
          repr(got))


def test_sweep_keeps_fresh_drops_stale() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        cdir = clients.clients_dir(ws)
        cdir.mkdir(parents=True, exist_ok=True)

        fresh = cdir / "fresh.json"
        stale = cdir / "stale.json"
        bad = cdir / "bad.json"
        fresh.write_text(json.dumps(
            {"updated_at": "2026-10-14T09:00:00Z"}))
        stale.write_text(json.dumps(
            {"updated_at": "2026-10-01T09:00:00Z"}))
        bad.write_text(json.dumps({"updated_at": "bogus"}))

        removed = sweep_markers(
            ws, now="2026-10-14T10:00:00Z", max_hours=24)
        check("removed count is stale + unparseable",
              removed == 2, removed)
        check("fresh marker remains", fresh.exists())
        check("stale marker removed", not stale.exists())
        check("unparseable marker removed", not bad.exists())


def test_sweep_empty_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        ws.mkdir()
        removed = sweep_markers(ws, now="2026-10-14T10:00:00Z")
        check("missing clients dir removes nothing", removed == 0, removed)


def main() -> None:
    tests = [test_parse_accepted_formats, test_parse_rejects_junk,
             test_naive_z_gets_utc, test_sweep_keeps_fresh_drops_stale,
             test_sweep_empty_dir]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
