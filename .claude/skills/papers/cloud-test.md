# Cloud session test for the papers skill

Open a cloud session of Claude Code on the authorllm repo and paste the block below as the first message. Each step says what to run, what counts as a pass, and what to record. The session writes its findings into one Google Doc it creates, named `papers-cloud-test-<date>`, so the local session can read the result. Nothing in this test touches a real paper tab; every write goes to a test tab or a test Doc, and every test object is deleted at the end unless a step says to keep it.

---

Run the papers cloud test in `.claude/skills/papers/cloud-test.md`, step by step. Report each step as pass or fail with the exact error text on a fail. Do not skip a failed step's later steps; note the dependency and go on. Write a markdown report as you go and, at the end, put it in a new Google Doc named `papers-cloud-test-<today>` in the same Drive folder as the master Doc, then give its link. End every message with the date line from `date '+%Y-%m-%d %H:%M:%S %Z'`.

**1. Rules from the repo.** Read `.claude/skills/papers/SKILL.md` and `.claude/skills/papers/house-rules.md`. Pass: both read, and your report's first line says which house rule you will apply to this very report (the no-dash rule and the date line).

**2. What the session cannot see.** Try to list `~/.claude/`, `~/.authorlm/` and `authorlm/manuscripts/`. Pass: you report each as absent or empty, without trying to create them.

**3. The CLI.** Run `cd authorlm && pip install -e ".[all]"` and then `authorlm --help`. Pass: the help prints. Record the install time and anything that failed to build (the web pages need Node; a failure there is expected and is not a fail of this step if `authorlm --help` still runs).

**4. doc auth in the cloud.** Run `authorlm doc auth`. Expected: it fails, because there is no OAuth client file and no browser can reach the localhost redirect. Record the exact message. Pass: the failure is reported verbatim and no retry is attempted. If it succeeds, record how (which file it found and where), because that changes the design.

**5. Drive connector, read.** With the Google Drive connector: search for the master Doc by name (the manuscript's name, `SMSTTD`), and read it. Pass: the Doc's text comes back. Record whether the connector returns the whole Doc as one text or lets you address a tab, and whether tab names appear anywhere in what it returns (look for `papers/0-axioms.md` or `0-axioms`).

**6. Find one tab's text.** From what step 5 returned, extract the text of the Paper 0 tab (it begins with the title `Choice Theory: an ontology of separation` and an abstract). Pass: you can quote its abstract. Record the method: did you get the tab directly, or did you have to find it inside the whole Doc's text.

**7. Write a test Doc.** With the connector, create a Doc named `papers-cloud-test-tab-<today>` in the master Doc's folder with three lines of markdown, then update it by appending a fourth line, then read it back. Pass: the read shows four lines. Record whether markdown headings survive as headings or as literal `#`.

**8. Write a tab.** Try to add a tab named `papers-cloud-test.md` to the master Doc through the connector, with one line of text. Expected: the connector has no verb for tabs. Pass: you report what the connector offers for the Doc (the list of its verbs that take a Doc id) and whether any of them can address a tab. If a tab can be created, create it, read it back, and delete it.

**9. A referee in the cloud.** Spawn one background subagent with this brief: "Read the abstract below and report, numbered, every claim that uses the word derive, show, prove or follow, with the quoted sentence. Do not edit anything." Give it the abstract from step 6. Pass: the report comes back and you relay it in one table.

**10. A probe in the cloud.** Run this and report the printed number: `python3 -c "import numpy as np; print(np.fft.fftfreq(8))"`. Pass: numpy is present. If it is not, run `pip install numpy` and say whether that worked.

**11. Flush to the Doc.** Put your full report into the Doc of step 7's folder as `papers-cloud-test-<today>` (create it now). Pass: its link is in your final message.

**12. Clean up.** Delete the test Doc of step 7 and the tab of step 8 if it was created. Keep the report Doc of step 11. Pass: you list what was deleted and what was kept.

Finish with the list of steps as pass or fail in one table, and one paragraph on what the result means for the papers skill's section 6: can a cloud session read a tab, can it write one, and what it must leave to the local session.
