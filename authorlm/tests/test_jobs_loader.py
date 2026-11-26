"""`media_gen._jobs` — the job-log loader edit_plan uses to find a take's
Omni interaction.

`day_spend` walks the same directory with its own glob and never calls
`_jobs`. A regression that included `.request.json` / `.response.json`
sidecars would feed edit_plan the raw Gemini payload instead of the
job record (no `take` / `operation`), so a revise would look like the
take was never made by Omni.
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


def test_jobs_skips_sidecars_and_sorts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        jobs = root / media.CLIPS_DIR / media_gen.JOBS_SUBDIR
        jobs.mkdir(parents=True)
        (jobs / "b.json").write_text(json.dumps({"id": "b", "take": "t-b"}),
                                     encoding="utf-8")
        (jobs / "a.json").write_text(json.dumps({"id": "a", "take": "t-a"}),
                                     encoding="utf-8")
        (jobs / "a.request.json").write_text(json.dumps({"raw": 1}),
                                             encoding="utf-8")
        (jobs / "a.response.json").write_text(json.dumps({"raw": 2}),
                                              encoding="utf-8")
        (jobs / "notes.txt").write_text("not a job", encoding="utf-8")
        got = media_gen._jobs(root)
        check("only *.json job records are loaded, in name order",
              [j["id"] for j in got] == ["a", "b"], got)
        check("request/response sidecars are excluded",
              all("raw" not in j for j in got), got)
        check("missing jobs directory is an empty list",
              media_gen._jobs(root / "empty") == [])


def main() -> None:
    print("test_jobs_skips_sidecars_and_sorts:")
    test_jobs_skips_sidecars_and_sorts()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
