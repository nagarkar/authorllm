"""Pure helpers behind Veo clip generation: duration buckets, day spend,
prompt composition, and take staleness.

`plan`/`start` in `test_media.py` cover one happy path. These pin the
edges that gate spend (4/6/8-second buckets, forced 8s for refs/1080p,
per-day USD sum) and the stale-law / stale-desc flags that keep a
regenerate from silently reusing a take whose law or spec moved.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from authorlm import media, media_gen


def check(label: str, cond: bool, detail: object = "") -> None:
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {label}"
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(label)


def test_duration_buckets() -> None:
    d = media_gen._duration
    check("under 4s rounds up to 4", d({"estimated_seconds": 3}, False) == 4)
    check("exactly 4 stays 4", d({"estimated_seconds": 4}, False) == 4)
    check("between 4 and 6 → 6", d({"estimated_seconds": 5}, False) == 6)
    check("between 6 and 8 → 8", d({"estimated_seconds": 7}, False) == 8)
    check("over 8 caps at 8", d({"estimated_seconds": 99}, False) == 8)
    check("missing estimate defaults toward 8",
          d({}, False) == 8)
    check("forced_eight wins over a short estimate",
          d({"estimated_seconds": 3}, True) == 8)


def test_compose_prompt_text_and_law() -> None:
    spec = ("subject: a tired man\nmotion: he sits\nlook: dusk\n"
            "camera: still\nsound: none\ntext: none\n")
    bare = media_gen.compose_prompt(spec, "", {})
    check("without text: field, prompt forbids on-screen text",
          "No on-screen text" in bare, bare)
    check("spec sections are uppercased labels",
          "SUBJECT: a tired man" in bare, bare)
    with_text = media_gen.compose_prompt(spec, "CLIP STYLE:\n- mute",
                                         {"text": "EXIT"})
    check("text: field quotes the exact on-screen words",
          'ON-SCREEN TEXT, exactly: "EXIT"' in with_text, with_text)
    check("law is appended when present",
          "CLIP STYLE:\n- mute" in with_text, with_text)
    check("no-on-screen line is absent when text: is set",
          "No on-screen text" not in with_text)


def test_day_spend_filters_local_date() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        jobs = root / media.CLIPS_DIR / media_gen.JOBS_SUBDIR
        jobs.mkdir(parents=True)
        (jobs / "a.json").write_text(json.dumps({
            "started_local": "2026-10-18T09:00:00", "est_usd": 1.5,
        }), encoding="utf-8")
        (jobs / "b.json").write_text(json.dumps({
            "started_local": "2026-10-17T23:00:00", "est_usd": 9.0,
        }), encoding="utf-8")
        (jobs / "c.json").write_text(json.dumps({
            "started_local": "2026-10-18T12:00:00", "est_usd": 0.4,
        }), encoding="utf-8")
        got = media_gen.day_spend(root, today="2026-10-18")
        check("only today's jobs sum", got == 1.9, got)
        check("missing jobs dir is zero",
              media_gen.day_spend(root / "empty", today="2026-10-18") == 0.0)


def test_takes_stale_flags_and_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        clips = root / media.CLIPS_DIR
        clips.mkdir(parents=True)
        # key may itself contain hyphens; TAKE is greedy on the key group.
        (clips / "neo-choice-aabbccdd-1122-01.mp4").write_bytes(b"a")
        (clips / "neo-choice-aabbccdd-99aa-i02.mp4").write_bytes(b"b")
        (clips / "other-aabbccdd-1122-01.mp4").write_bytes(b"c")
        rows = media_gen.takes(root, "neo-choice",
                               want_desc="aabbccdd", want_law="1122")
        check("only this key's takes are listed",
              [r["n"] for r in rows] == ["01", "i02"], [r["n"] for r in rows])
        check("matching hashes are not stale",
              rows[0]["stale"] == [], rows[0])
        check("law mismatch is stale-law",
              rows[1]["stale"] == ["stale-law"], rows[1])
        desc_rows = media_gen.takes(root, "neo-choice",
                                    want_desc="deadbeef", want_law="1122")
        check("desc mismatch is stale-desc",
              "stale-desc" in desc_rows[0]["stale"], desc_rows[0])


def main() -> None:
    tests = [
        test_duration_buckets,
        test_compose_prompt_text_and_law,
        test_day_spend_filters_local_date,
        test_takes_stale_flags_and_order,
    ]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
