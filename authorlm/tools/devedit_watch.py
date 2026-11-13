"""Emit one line per author action on the dev edit page, and a COMPLETE line
when a batch is done: every decision of a stage answered, or every queued
item of a file decided. The Dev edit half of author_watch.py, alone:

    python3 -u authorlm/tools/devedit_watch.py <run folder> [workspace]

The items and verdicts are read from the workspace database."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from author_watch import DevEditWatch  # noqa: E402

watch = DevEditWatch(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None)
first = True
while True:
    for line in watch.poll(first):
        print(line, flush=True)
    first = False
    time.sleep(2)
