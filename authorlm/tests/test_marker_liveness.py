"""Client session markers: liveness, host-id extraction, and clear.

`marker_is_live` / `_pid_alive` decide whether ambient resolution may
name a chat; `_host_id` maps a Claude messaging socket path to the host
pid Branch B looks up; `clear_marker_file` is the SessionEnd unlink.
Wrong TTL or a clear that lies about absence misattributes usage and
locks ambient resolution on a dead chat. Sweep-marker TTL (#194) is a
different clock — this file pins the live gate and the unlink door.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import clients


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def _stamp(age_hours: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=age_hours)
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def test_pid_alive_edges() -> None:
    check("missing / non-int pid assumes live (age decides)",
          clients._pid_alive(None) and clients._pid_alive("nope")
          and clients._pid_alive(-1) and clients._pid_alive(0))
    check("this process is live", clients._pid_alive(os.getpid()))
    # A pid that never existed — Linux usually raises ProcessLookupError.
    check("a dead pid is not live", not clients._pid_alive(2_147_483_646))


def test_marker_is_live() -> None:
    now = _stamp(0)
    live = {"updated_at": _stamp(1), "host_pid": os.getpid()}
    check("fresh marker with live pid is live",
          clients.marker_is_live(live, now=now))
    stale = {"updated_at": _stamp(clients.MARKER_TTL_HOURS + 1),
             "host_pid": os.getpid()}
    check("marker older than TTL is not live",
          not clients.marker_is_live(stale, now=now))
    check("unparseable stamp is not live",
          not clients.marker_is_live({"updated_at": "nope"}, now=now))
    dead = {"updated_at": _stamp(0.1), "host_pid": 2_147_483_646}
    check("dead host pid fails the live gate",
          not clients.marker_is_live(dead, now=now))
    check("check_pid=False ignores a dead host pid (Branch B)",
          clients.marker_is_live(dead, now=now, check_pid=False))


def test_host_id() -> None:
    check("first non-empty key wins",
          clients._host_id({"A": "", "B": "  42  "}, ("A", "B")) == "42")
    check("socket path → basename without .sock",
          clients._host_id(
              {"SOCK": "/tmp/cc-socks/999.sock"}, ("SOCK",)) == "999")
    check("plain basename path still yields the name",
          clients._host_id({"P": "/var/run/123"}, ("P",)) == "123")
    check("all empty → None",
          clients._host_id({"A": "  ", "B": ""}, ("A", "B")) is None)


def test_clear_marker_file() -> None:
    with tempfile.TemporaryDirectory(prefix="authorlm-marker-") as tmp:
        ws = Path(tmp)
        clients.write_marker_file(ws, {
            "engine": "claude-code", "session_id": "sess-1",
            "host_pid": os.getpid(), "updated_at": _stamp(),
            "started_at": _stamp(),
        })
        path = clients.marker_path(ws, "claude-code", "sess-1")
        check("marker file exists after write", path.is_file())
        check("clear returns True when the file existed",
              clients.clear_marker_file(ws, "claude-code", "sess-1") is True)
        check("marker is gone", not path.exists())
        check("clear of a missing file returns False, does not raise",
              clients.clear_marker_file(ws, "claude-code", "sess-1") is False)


def main() -> None:
    for t in (test_pid_alive_edges, test_marker_is_live, test_host_id,
              test_clear_marker_file):
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
