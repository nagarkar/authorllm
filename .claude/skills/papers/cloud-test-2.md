# Cloud session test 2: the CLI road to the tabs

Run after `authorlm doc bind` exists and the environment carries the token (section 6 of SKILL.md). Every write goes to one test tab named `papers-cloud-test-2.md`; no real tab is edited. Paste the block below as the first message of a cloud session on nagarkar/authorllm.

---

Run the papers cloud test 2 in `.claude/skills/papers/cloud-test-2.md`, step by step. Report each step as pass or fail with the exact error text on a fail; do not skip later steps after a fail. Write a markdown report as you go and at the end create it as a Google Doc named `papers-cloud-test-2-<today>` in the master Doc's folder (through the CLI if step 7 passed, through the connector otherwise), and give its link. Apply `.claude/skills/papers/house-rules.md` to everything you write. End every message with the date line from `date '+%Y-%m-%d %H:%M:%S %Z'`.

**1. Token present.** `ls -l ~/.authorlm/gdocs_token.json` and `python3 -c "import json;d=json.load(open('$HOME/.authorlm/gdocs_token.json'));print(sorted(d))"`. Pass: the file exists with mode 600 and the keys include `refresh_token`, `client_id`, `client_secret`. Never print the values.

**1b. Tools and keys.** `pandoc --version | head -1`, `ffmpeg -version | head -1`, `which pip python3 authorlm`, and `python3 -c "import os;print({k: bool(os.environ.get(k)) for k in ['GEMINI_API_KEY','ANTHROPIC_API_KEY','OPENAI_API_KEY','AUTHORLM_SKIP_WEB_BUILD']})"`. Pass: pandoc and ffmpeg print a version, the three programs resolve under /opt/authorlm-venv, and the Gemini key is present. Print only the booleans, never a value. Then `cd authorlm && pip install -e ".[all]"` timed, and `authorlm --help`.

**2. Workspace.** `authorlm setup --workspace ~/authorlm-ws --yes` then `authorlm status`. Pass: a database is created and status runs.

**3. Manuscript and bind.** `authorlm init --name SMSTTD --path ~/authorlm-ws/SMSTTD --extraction off`, then `authorlm doc bind 1VDLq5605h6f9yykyMCtWnsCOP9lnMIsTdVl6IrgF2ls -m SMSTTD`, then `authorlm doc list -m SMSTTD`. Pass: the list names the master Doc and its tabs. Record whether the list already shows tab ids before any pull.

**4. First pull.** `authorlm doc pull -m SMSTTD`, timed. Pass: the manuscript folder fills with one `.md` per tab, `papers/0-axioms.md` among them. Record the time, the file count and the report's conflict, missing and adopted lists. Then `find ~/authorlm-ws/SMSTTD -name '*.md' | sort | xargs md5sum > /tmp/before.md5`.

**5. Headless token refresh.** Report the `expiry` field of the token file before and after step 4 (the field only, not the token). Pass: the pull did not ask for a browser. If it did, record the exact text and stop writing; later steps that push are then fail with that dependency.

**5b. One lens, one call.** `authorlm shell --help` to find the smallest verb that makes a model call, or run `python3 -c "from authorlm import llm, config; ..."` only if a one-call verb exists; otherwise skip and say so. Pass: a model answers through LiteLLM with the key from the environment, or the step is recorded as skipped with the reason. Spend no more than one call.

**6. One test tab, pushed.** Write `~/authorlm-ws/SMSTTD/papers-cloud-test-2.md` with a heading, two sentences and the date line. `authorlm doc push papers-cloud-test-2.md -m SMSTTD`. Pass: the push reports a new tab and its link.

**7. Round trip and isolation.** `authorlm doc pull papers-cloud-test-2.md -m SMSTTD`, then `diff` the pulled file against what you wrote; then `find ~/authorlm-ws/SMSTTD -name '*.md' ! -name 'papers-cloud-test-2.md' | sort | xargs md5sum > /tmp/after.md5` and `diff /tmp/before.md5 /tmp/after.md5`. Pass: the pulled test tab matches what was written (record any normalisation the Doc applied), and the second diff is empty: no other file changed.

**8. Conflict guard.** Through the Drive connector, read the master Doc and confirm the test tab is present in its text. Then append a line to the local test file, push it, and pull once more. Pass: the second push succeeds and the pull is clean. Record what `doc list` shows as checked out at each point.

**9. Referee on a real tab, read only.** Spawn one background subagent with the text of `papers/0-axioms.md` from the local cache and the brief: "Report, numbered, every sentence that uses derive, show, prove or follow, quoted. Do not edit anything." Pass: the report comes back; relay it as one table. Confirm afterwards that `papers/0-axioms.md`'s checksum is unchanged.

**10. Clean up the tab.** `authorlm doc retire papers-cloud-test-2.md -m SMSTTD` if retire removes the tab; otherwise record what retire does and leave the tab for the local session to delete, saying so. Then `rm -rf ~/authorlm-ws` and `rm ~/.authorlm/gdocs_token.json`. Pass: you list what was removed in the Doc and in the session.

Finish with the steps as pass or fail in one table, and one paragraph for section 6: can the cloud session edit one tab and leave the rest untouched, what did the pull cost, and what still has to stay local.
