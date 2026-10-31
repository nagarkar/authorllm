# Doc-road recordings

Live Google Docs/Drive calls from the fixture Doc "AuthorLM test fixture: Doc road" (id in `fixture_doc.json`), one gzipped JSON list per scenario of `tests/test_doc_road_failures.py`, scrubbed of names and emails. `tests/test_doc_fakes_vs_google.py` checks `_GoogleFake` against them.

`fake.log` holds the time of the last recording; the fixture Doc holds the same time as `Last recorded: <time>` in its own `fake-log` tab. `test_doc_fakes_vs_google.py` checks that age first (`tests/_doc_freshness.py`): under 7 days, it stays offline; 7 days or more, or no log, it re-records live (about ten minutes, normal API calls only); no log but a recent Doc line means something deleted the log, and it fails and says what to do. Without `~/.authorlm/gdocs_token.json` (a cloud run) the live step is skipped with a notice. Never delete `fake.log` by hand, and change the Doc's date line only by hand, with a human's approval.

Re-record by hand: `cd authorlm && PYTHONPATH=$PWD python3 tests/doc_road_live.py --record` (touches only the fixture Doc), then run `python3 tests/test_doc_fakes_vs_google.py` and bring `_GoogleFake` in `tests/test_doc_road_verbs.py` back in line with any difference it reports. Commit `fake.log` with the recordings.
