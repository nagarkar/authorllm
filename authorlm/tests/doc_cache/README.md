# Doc-road recordings

Live Google Docs/Drive calls from the fixture Doc "AuthorLM test fixture: Doc road" (id in `fixture_doc.json`), one gzipped JSON list per scenario of `tests/test_doc_road_failures.py`, scrubbed of names and emails. `tests/test_doc_fakes_vs_google.py` checks `_GoogleFake` against them.

Re-record when Google changes: `cd authorlm && PYTHONPATH=$PWD python3 tests/doc_road_live.py --record` (needs `~/.authorlm/gdocs_token.json`; touches only the fixture Doc), then run `python3 tests/test_doc_fakes_vs_google.py` and bring `_GoogleFake` in `tests/test_doc_road_verbs.py` back in line with any difference it reports.
