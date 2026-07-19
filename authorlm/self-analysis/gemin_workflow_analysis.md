# AuthorLM: Engineering and Workflow Analysis

This report provides an in-depth analysis of **AuthorLM**, an agentic editorial collaborator for philosophy and high-concept writing. It evaluates the application's core architecture, its suitability for book authoring, its object and domain model, its interactive shell environment, and identifies key deficiencies in its current authoring workflow.

> **Important Notice:** The improvements, architectural recommendations, and deficiencies described in this document are strictly analytical. They should **never** be tackled or implemented in code by any non-Claude agent (including Gemini CLI or other sub-agents). This report is an analysis of code designed and developed by Claude, and the architectural traffic is strictly one-way. This guardrail prevents independent modifications to Claude's codebase, ensuring that Claude remains the sole author of its implementations and does not become confused by out-of-band updates.

---

## Executive Summary
AuthorLM is a lightweight, highly specialized **co-editor** (not a ghostwriter) that operates as a *Common Core Decision Learning Engine* implementation of the *AuthorLM Specialization RFC (v2.1)*. Rather than generating text for the author, it establishes a bidirectional feedback loop:
1. It **observes** manuscript edits passively via a background watcher.
2. It **tracks** conceptual progression via an explicit **Concept Graph** (representing load-bearing ideas and their prerequisite structures).
3. It **suggests** structurally and contextually grounded editorial actions (bridging, prerequisite warnings, answering objections, and policy reminders) while explicitly explaining its reasoning.
4. It **learns** idiosyncratic editorial policies from how the author accepts, rejects, or modifies these suggestions, alongside an LLM-backed episodic analysis of what the author actually wrote.

For its intended domain—highly structured, academic, or philosophical non-fiction—AuthorLM's architecture is exceptionally strong. However, its rigid noun-phrase matching, lack of real-time push feedback in the shell, visual prompt contamination, and lack of database version-control rollback capabilities present substantial workflow friction.

---

## 1. Subsystem Architecture
AuthorLM is organized into five tightly coupled but modular subsystems, all operating over a single, global SQLite database located in `~/.authorlm/authorlm.db` (or overridden by a workspace argument):

```
+----------------------------------------------------------------------------------------+
|                                  MANUSCRIPT DIRECTORY                                  |
|                                                                                        |
|  +------------------+      +-------------------+      +-----------------------------+  |
|  |  01-preface.md   |      |   02-sermons.md   |      |  _concepts/ (Obsidian Stubs)|  |
|  +--------+---------+      +---------+---------+      +--------------+--------------+  |
+-----------|--------------------------|-------------------------------^-----------------+
            | (Passive watch)          | (Passive watch)               | (Export)
            v                          v                               |
+----------------------------------------------------------------------|-----------------+
|                                    AUTHORLM CORE                                       |
|                                                                      |                 |
|  +--------------------+      +--------------------+      +-----------+--------------+  |
|  | Revision Watcher   | ---> | Transition Engine  | ---> | Obsidian Export Engine   |  |
|  | (Debounced disk    |      | (Paragraph diffing |      | (Generates wiki-linked   |  |
|  |  polling)          |      |  & classification) |      |  concept stub notes)     |  |
|  +--------------------+      +---------+----------+      +--------------------------+  |
|                                        |                                               |
|                                        v                                               |
|                              +--------------------+                                    |
|                              |    Concept Graph   |                                    |
|                              | (Nodes & Relations |                                    |
|                              |  Realization Scan) |                                    |
|                              +---------+----------+                                    |
|                                        |                                               |
|                                        v                                               |
|                              +--------------------+                                    |
|                              |  Guidance Engine   |                                    |
|                              | (Deterministic &   |                                    |
|                              |  LLM suggestions)  |                                    |
|                              +---------+----------+                                    |
|                                        |                                               |
|                                        v                                               |
|                              +--------------------+                                    |
|                              | Learning Loop      |                                    |
|                              | (Policy updates,   |                                    |
|                              |  Episode analysis) |                                    |
|                              +--------------------+                                    |
+----------------------------------------------------------------------------------------+
```

1. **The Revision Watcher and Analyzer (`revisions.py`, `shell.py`):** Passes over the manuscript folder, ignoring files in folders prefixed with `.` or `_` (protecting `.obsidian/` and `_concepts/`). It automatically computes a checksummed snapshot when changes settle (defaulting to a debounced 3-second polling window). It runs a paragraph-level `SequenceMatcher` to decompose changes into semantic `editorial_transitions` (`insert`, `delete`, `rewrite`, `file_added`, `file_removed`).
2. **The Concept Graph (`concepts.py`):** Maps nodes of specific taxonomic kinds (e.g., `concept`, `definition`, `objection`, `metaphor`, `question`, `historical_reference`, `mathematical_construct`) and edges representing semantic assertions (e.g., `depends_on`, `permits`, `creates`, `defines`, `answers`, `co_occurs`). It scans versions to detect when a declared concept is "realized" in text via plural-tolerant word-boundary regexes, and counts paragraph co-occurrences of concepts to infer implicit structural links.
3. **The Guidance Generator (`guidance.py`):** Runs deterministic heuristics combined with optional LLM reasoning to output explained recommendations. It proposes introducing unrealized concepts when they serve an active intent, flags prerequisite gap violations (e.g., when concept B is introduced before concept A has been realized, despite a `depends_on` or `permits` edge), and highlights unanswered objections or reminders for active learned policies.
4. **The Policy and Episode Learning Loop (`policies.py`, `analysis.py`):** Strengthens or weakens learned editorial rules depending on the author's reviews of generated suggestions (using Laplace-smoothed support). Verbatim explanations from reviews are distilled by the LLM into candidate policies, and closed writing episodes are post-analyzed to infer the author's implicit stylistic patterns (e.g., "opened with a lived example before introducing a formal definition").
5. **The Obsidian Integration (`obsidian.py`):** Regenerates wikilinked markdown stubs inside a `_concepts/` directory inside the manuscript folder, projecting AuthorLM's internal sqlite concept graph into Obsidian's native visual graph view.

---

## 2. Suitability for Book Authoring: Engineering Assessment

### Where It Shines (Target Use Case)
AuthorLM is exceptionally suited for **high-concept, logical, or highly structured non-fiction**, specifically:
- **Philosophy Manuscripts:** Tracks rigorous arguments, conceptual mappings, refutations, and historical citations. It ensures that prerequisite terminology is established before dependent theories are deployed.
- **Scientific or Technical Works:** Tracks mathematical constructs, definitions, and logical dependencies. It acts as a semantic linter for technical explanations.
- **Structured Explanations and Systems Design Books:** Ensures that structural prerequisites are consistently respected.
- **Obsidian-Centric Thinkers:** The visual mapping of concepts into Obsidian's visual graph means that an author's research vault and manuscript outline are always in physical, visual alignment.

### Where It Fails / Deficiencies in Non-Philosophy Domains
- **Narrative Fiction and Memoirs:** In fiction, concepts are fluid, metaphorical, and highly context-dependent. They are rarely bound to single, repetitive noun-phrases (like "Trajectory" or "Archaic Tongue"). A novelist writing about "betrayal," "growing up," or "grief" cannot rely on regex word-boundary matching.
- **Semantic Synonym Blinds:** Because the engine matches using precise string-based regex stems (e.g. `trajectory` -> `trajectories`), it cannot recognize that a concept has been discussed when an author uses a synonym or a descriptive circumlocution (e.g., using "the course of becoming" instead of "trajectory"). This leads to false-positive prerequisite warnings and forces artificial, repetitive phrasing.
- **Payload and Context Windows:** The LLM concept extraction is artificially capped at a low token size (default `extraction_max_chars` of 24,000) to keep LLM calls cheap and reliable. For an author writing a 300-page book, this requires heavily chunked, incremental processing, which degrades the LLM's capacity to recognize global dependencies or themes across distant chapters.
- **Lack of Natural Language CLI or Agentic Integration:** As designed, AuthorLM requires interacting with a specialized custom terminal shell, rather than natively supporting natural language prompts or conversational agent integrations (e.g., Claude Code or Gemini CLI). There is no direct "Claude prompt -> skill or MCP tool invocation" flow. The current model is not exposed as a unified agentic toolset. Ideally, the system should allow a flow where a user interacts with a conversational agent to draft or discuss content, and the agent silently calls underlying AuthorLM tools (e.g., to query or update the Concept Graph, retrieve past writing precedents, or validate prerequisite gaps) behind the scenes.
- **Absence of a Model Context Protocol (MCP) Interface:** AuthorLM lacks an MCP server interface. Because the data and heuristics are trapped inside a local Python executable and a custom SQLite schema, external agentic tools cannot easily query, read, or write to the AuthorLM database as resources or tools. Exposing the manuscript versions, concept graph, and active intents as standard MCP tools would allow any modern coding or authoring agent to dynamically inspect the state of the book without manual command execution.

---

## 3. Review of the Object Model and Domain Concepts

The object model of AuthorLM is exceptionally well-defined, clean, and architecturally robust. 

### Strengths of the Domain Model
- **Immutable Historical Logs vs. Evolving Belief State:** 
  - The split is modeled in the database schema.
  - Historical artifacts (revisions, transitions, reviews, evidence, intents) are strictly immutable, append-only records.
  - Conversely, the "belief" state (concept node status, concept edge status, editorial policies, knowledge proposals) evolves over time, with each state modification incrementing an internal `version` field (complying with RFC Common Core §6.5).
  - This ensures that the entire writing history is fully replayable.
- **Traceable Provenance:** Every generated suggestion in `guidance_history` carries an explicit `explanation` string that points back to specific, observable items of evidence in the DB (like concept co-occurrences or active intents).
- **Unwarned Degradation via Silent LLM Failback:** If the LLM client is unconfigured or unreachable, the system silently degrades to deterministic, evidence-only heuristics. While this keeps the system running, it represents a substantial architectural weakness: the author suddenly receives a lower-quality briefing and guidance analysis without an explicit, hard failure or a clear alert. It would be architecturally superior to employ a "fail-fast" protocol that notifies the author of LLM failure immediately.
- **Negative Feedback Capture:** Rejects and modifications are captured as `evidence` with high weights. Rather than allowing the LLM to hallucinate rules, the author must explicitly seed or validate policy proposals.

---

## 4. Evaluation of the Shell Environment

The interactive shell (`authorlm shell`) provides an immersive, command-line companion experience.

### Strengths of the Shell Design
- **Prefix-Free Execution:** Typing `intent declare` instead of `authorlm intent declare` inside the shell minimizes keystroke overhead.
- **Introspective Tab-Completion:** The completion dictionary (`completion.py`) dynamically reads from the active argument parser Choice actions, as well as live SQLite values (concept names, policy prefixes, manuscript names). Completion of multi-word names is automatically injected with surrounding quotes, preventing shell parser crashes.
- **Shorthand Translations:** The translation logic (`translate_line`) intercepts intuitive user shortcuts (such as `review 1 accept` converting into `review 1 --accept`), reducing syntax-related friction.

### Weaknesses in the Shell Design
- **Lack of True TUI Multiplexing:** The shell relies on basic readline inputs on stdout. It lacks a true Text User Interface (TUI) framework (such as `prompt_toolkit` or `curses`). This forces all outputs (learning briefings, transitions, guidance) to print into the same vertical scrollback buffer, which quickly becomes cluttered.
- **Blocklist Incompleteness:** The shell blocks recursive `shell` and `watch` commands, but it does not block destructive commands like `unregister`. Running `unregister` deletes the manuscript and its tables out from under the active shell session, resulting in instant SQL operational errors.

---

## 5. Main Deficiencies in the Shell-Based Authoring Workflow

While the code quality is superb, the interactive user experience in the shell suffers from several key workflow deficiencies:

### A. Terminal Prompt Pollution (Visual Contamination)
- **Problem:** Saving a file in an external editor causes the background watcher to trigger `collect` and print output directly to stdout while the user is typing, visually scrambling the input line.
- **How Other Tools Solve It:** Classic shell tools (like `bash` or `zsh`) rewrite or repaint the prompt line when background job status updates arrive. Sophisticated multiplexing tools or TUI frameworks (such as `prompt_toolkit`, `tmux`, or `curses`) split the display into distinct visual panes, buffering raw logs into a scrollback pane while keeping the interactive command prompt isolated in a separate window or bottom-pinned buffer.
- **Tradeoffs and Recommendations:** Building a full split-pane TUI adds dependencies (like `urwid` or `prompt_toolkit`), increases the complexity of terminal resize handling, and makes output redirection more difficult. However, it completely eliminates visual pollution. We recommend transitioning from a pure readline shell to a dual-pane `prompt_toolkit` terminal where background watcher operations are directed to an inline, non-disruptive status bar or a separate scrollable logging window.

### B. Pull-Based Guidance Generation (No Real-Time Push)
- **Problem:** When the watcher collects a revision, it merely prints that a revision was collected without recalculating or displaying updated guidance, forcing the user to type `guide` or `briefing` to verify their progress.
- **How Other Tools Solve It:** Modern software linters and compilers (such as those integrated into VS Code or running as file-watch daemons like `cargo watch` or `tsc --watch`) operate on a continuous push model. When files are saved, they automatically re-verify the AST or graph, pushing live diagnostics, squiggly lines, or real-time status banners directly to the editor or a split console screen.
- **Tradeoffs and Recommendations:** Generating suggestions through LLMs on every single file save is prohibitively expensive in terms of token cost and latency. However, running the system's deterministic, local graph-prerequisite rules and co-occurrence counts is extremely cheap (typically taking less than 15ms). We recommend a hybrid push-pull model: when files settle and are collected, run the local deterministic graph rules immediately to update a visual, real-time "prerequisite gap" list in the TUI, while keeping the full LLM bridge-drafting and policy-reminder generations bound to a manual or debounced command invocation.

### C. Locking and Blocked Observations During Interactive Loops
- **Problem:** The main command execution in the shell is serialized via a global `lock` thread mutex. While in long interactive sub-processes like `concept triage`, the watcher is blocked from collecting edits, leading to missed snapshots or lumped transitions.
- **How Other Tools Solve It:** Databases and concurrent software systems use multi-version concurrency control (MVCC) or reader-writer lock separation. In SQLite, this is achieved by enabling Write-Ahead Logging (WAL) mode, allowing concurrent background writes to proceed in parallel with active user read transactions. Interactive CLI systems also run user triage loops on separate, isolated transaction frames rather than locking the entire global dispatcher.
- **Tradeoffs and Recommendations:** Allowing concurrent writes while the user is actively reviewing a concept list can lead to dirty reads or conflicting updates if the file watcher changes node statuses out from under the active triage menu. However, blocking all file watch collections is highly disruptive to continuous observation. We recommend enabling SQLite WAL mode, removing the global mutex lock from the watch loop, and isolating interactive command menus to read-only snapshots that prompt for confirmation if underlying data is modified in the background.

### D. Coarse, Fragile Session State Boundaries
- **Problem:** Exiting the shell does not end a session unless `session end` is typed. Days later, background edits get grouped under the old session's episode, distorting writing and learning velocity metrics.
- **How Other Tools Solve It:** Web servers, SSH sessions, and developer tracking tools (such as WakaTime) utilize activity heartbeats and auto-expiration timers. If no filesystem edits or keyboard inputs are received within a fixed inactive window (e.g., 30 minutes), the session is automatically closed or marked as idle, suspending metric aggregation.
- **Tradeoffs and Recommendations:** Aggressive timeouts can split a single, slow-paced creative session (where an author spends an hour reading or thinking) into multiple fragmented sessions. However, leaving sessions open indefinitely corrupts the accuracy of learning episodes. We recommend implementing an automatic 30-minute idle-timeout daemon in the shell watcher that safely auto-completes and closes the session, and automatically prompts the author to resume or start a new session upon their next interaction.

### E. Lack of Database Version-Control Rollback
- **Problem:** Because the database schema is strictly append-only, if the author accidentally saves a corrupted file, deletes a chapter, or makes a major spelling error, that version is permanently recorded, polluting history.
- **How Other Tools Solve It:** Git solves this via the index and reflog, allowing developers to reset or branch (`git reset --hard HEAD~1`). Database migration frameworks track down/rollback scripts, and traditional editors support localized undo/redo buffers.
- **Tradeoffs and Recommendations:** Allowing arbitrary DB rollbacks in an append-only knowledge ledger can break relational integrity (e.g., if a rolled-back version introduced a concept node that has since been linked to other nodes). However, lacking any rollback capability makes the database fragile to spelling mistakes and accidental edits. We recommend implementing a restricted `revision rollback` command that lets the user remove the latest version and its transitions from SQLite *only* if no subsequent concepts have been declared or links established.
- **Major Deletions and Safety Checks:**
  - *How other systems solve it:* To prevent destructive edits (like accidental deletion of entire chapters or catastrophic spelling errors), version control and document editing systems utilize interactive confirmation prompts, staging areas, or trash/recycle bin folders.
  - *Evaluation and Recommendation:* Rather than automatically committing massive changes (such as deleting half a manuscript), the file watcher should analyze the relative magnitude of the diff. If a deletion or rewrite exceeds a high-water mark (e.g., >30% of a file's content is removed), the system should prompt the user with a confirmation request (`Detected massive deletion in chapter X. Is this intentional? [y/N]`) before committing the snapshot to the database, or temporarily stage it in a draft version state.

### F. No Inline Context or Visual Diffing
- **Problem:** Sneak previews of transitions are minimal, providing no visual diffing of what actually changed inside the shell.
- **How Other Tools Solve It:** Git and specialized CLI diff engines (like `git diff` or `delta`) render unified diff format with green (added) and red (removed) ANSI line colors. Many modern editors show gutter annotations indicating additions, removals, and modifications.
- **Tradeoffs and Recommendations:** Full terminal-based interactive side-by-side diffing is complex and can clutter the scrollback. However, basic unified diffs are incredibly high-signal. We recommend adding an inline `diff` command (and integrating a lightweight colored unified diff using Python's standard `difflib.unified_diff`) that allows authors to instantly see what text changes drove a specific collected transition.

### G. Delayed Intent Validation
- **Problem:** Typo-ridden or unmatched intents are accepted without warning, wasting author intent cycles until `guide` is run manually.
- **How Other Tools Solve It:** Compilers and software CLI arguments parse and validate types or references immediately at command entry time. If a reference does not exist, they warn the user instantly with auto-suggestions or fuzzy matches.
- **Tradeoffs and Recommendations:** Forcing strict validation right at declaration time would prevent authors from declaring an intent before they've created its concepts in the graph, which disrupts the natural "top-down" creative flow (declaring a high-level goal, then filling in details). We recommend a non-blocking fuzzy lookup immediately upon `intent declare`. The system should issue an immediate warning (e.g., `"Warning: 'trajectories' matches no current concepts. Did you mean 'Trajectory'?"`) without blocking the declaration, keeping the author's creative flow unhindered while eliminating silent typo waste.
- **Automatic Guidance Execution on Intent Declaration:**
  - *Pros:* Provides instant, highly encouraging feedback, showing the direct logical consequences of declaring a goal without requiring a separate manual command.
  - *Cons:* Generates significant shell execution latency (due to LLM calls) right after a command entry, interrupting the author's flow. It also causes unnecessary API token consumption if the author already knows their next steps.
  - *Recommendation:* Do not automatically trigger full LLM-based guidance on intent declaration. Instead, run the local, deterministic graph-prerequisite rules instantly and display a quick conceptual preview (e.g., matched concepts, active gaps) on the terminal, keeping the detailed, generative guidance manual.

---

## 6. Strategic Recommendations for Workflow Optimization

To transition AuthorLM from a robust prototype to an optimal, high-productivity tool for book authors, we recommend the following engineering improvements:

1. **Adopt a Split-Pane TUI (such as `prompt_toolkit` or `urwid`):**
   - Replace the standard `readline` prompt with a full-terminal interface containing two panels:
     - **Left Pane:** Interleaved interactive command input/output.
     - **Right Pane:** A real-time, push-based dashboard displaying active intents, realized concept counts, outstanding policy questions, and a "live linter" showing structural prerequisite gaps.
   - This eliminates visual prompt contamination entirely, as logs/background collections can print to a dedicated status bar or log pane without disrupting the input buffer.
2. **Implement Push-Based Reactive Guidance:**
   - When a revision settles and is collected, run the guidance heuristics in the background and instantly update the TUI dashboard pane. This provides the author with immediate, satisfying visual feedback when they successfully resolve a prerequisite gap or introduce a concept.
3. **Refine Thread Synchronization and Interactive Commands:**
   - Break up long-running interactive prompts (like `concept triage`) into localized sub-states within the main TUI loop rather than blocking the main dispatch lock.
   - Ensure the database connection uses a WAL (Write-Ahead Logging) mode so reading can occur in parallel with background watcher writes, removing the need to block the watch collector during manual user triage.
4. **Introduce Automatic Session Expirations:**
   - If no manuscript edits are observed for more than 30 minutes, or if the terminal shell is closed, automatically close the active writing session.
   - On shell launch, if the last session was closed due to an idle timeout, offer the option to split intervening revisions into a distinct background-activity episode or associate them with a new session.
5. **Add a Revision Rollback Command (`collect undo` or `revision rollback`):**
   - Allow authors to discard the most recent revision and its transitions from SQLite, effectively saying "ignore my last save." This maintains historical data integrity.
6. **Support Fuzzy/Semantic Concept Recognition:**
   - Introduce an optional semantic embedding layer (or a custom LLM pass) to check for synonyms of declared concepts in newly written paragraphs. This mitigates the brittleness of word-boundary regexes and handles conceptual synonyms gracefully.

---

## 7. Long-Term Vision: Claude Code and Model Context Protocol (MCP) Integration

Taking a long-term view, the primary goal should be to allow the user to remain mostly unaware of AuthorLM's mechanical inner workings, interacting instead with a conversational AI assistant (like Claude Code or Gemini CLI) while AuthorLM runs as an intelligent, silent substrate in the background.

### Transition to Claude Code and MCP Agent Workflows
Exposing AuthorLM as an MCP (Model Context Protocol) server would allow a user to use Claude Code with AuthorLM configured as a registered skill or MCP tool. In this paradigm:
- The custom terminal shell is replaced by a standard chat interface.
- Conversational prompts (e.g., *"Let's draft a section introducing trajectory"*) automatically trigger background MCP calls that check the Concept Graph, retrieve past writing precedents, or validate prerequisite structures.
- This creates a unified experience where the author discusses, brainstorms, and drafts text in natural language, while the agent uses AuthorLM to ensure strict logical and conceptual compliance with the established manuscript architecture.

### Role of the Shell as an Observability Substrate
Improving the core AuthorLM terminal shell and maintaining direct shell interaction is not a wasted effort; it serves as a critical observability and debugging substrate. 
- Just as Git is supported by a robust command-line interface that underpins visual IDE wrappers, AuthorLM requires a high-fidelity local shell. 
- A stable, well-defined CLI allows authors to inspect exact database tables, run manual triages, and inspect transitions when automated agents behave unexpectedly.
- By refining the shell's logic and APIs, we create a stable, deterministic substrate that conversational agents like Claude can inspect, query, and depend upon with extreme reliability.

### Conversational Evolution and Token Optimization
To maintain a fluid conversational co-authoring experience while keeping LLM token usage to an absolute minimum, the system should evolve along several architectural axes:
- **API Payloads and Summarization:** Rather than feeding the entire 100,000-word manuscript into Claude's context window on every turn, the agent should query local AuthorLM APIs. Since `collect` and `detect_transitions` run locally, they can summarize the latest changes into highly condensed semantic payloads (e.g., `"v5 transitions: 1 paragraph inserted in 03-trajectories.md introducing Trajectory"`). Claude can read this micro-summary rather than parsing raw text, saving thousands of tokens.
- **Immediate Conversational Feedback:** When an author declares an intent (e.g., *"I want to write about Choice now"*), they expect the system to provide immediate, context-aware feedback without having to explicitly ask for it. By leveraging local, deterministic graph-lookup APIs, the agent can instantly verify dependencies and reply (*"Excellent. Choice depends on Distinction, which is already realized. However, make sure to address the pending objection 'Error of Specialness' raised in Chapter 1."*) with zero LLM token overhead before any drafting begins.
- **Implicit Triage and Hidden Workflows:** The conversational interface can completely hide mechanical workflows like `concept triage`, `edge triage`, or `policy answering`. When an author chats with the agent (*"Yes, I think becoming depends on choice"*), the agent can implicitly mark that relationship as confirmed or answer an outstanding policy question behind the scenes, recording the conversation itself as high-weight evidence and updating SQLite without ever forcing the author into a CLI triage menu.
- **Lazy and On-Demand Generative Analysis:** Heavily cognitive operations—like full policy distillation, large-scale LLM concept extraction, and episode post-analysis—should run lazily or in the background, utilizing SQLite buffers and local response caches (like `tests/llm_cache/`) to prevent redundant LLM invocations.

---

## 8. Phased Implementation Roadmap

To systematically execute these improvements, we propose a four-phase roadmap that transitions AuthorLM from a robust prototype into a seamless, high-productivity agentic collaborator.

### Phase 1: Terminal TUI Isolation and Concurrency Hardening (Infrastructure Baseline)
- **Activations:** Enable SQLite Write-Ahead Logging (WAL) in `db.py` to allow non-blocking concurrent reads and background collections.
- **Terminal Redesign:** Port the standard Python `readline` shell over to `prompt_toolkit`. Utilize an asynchronous terminal painter to isolate the prompt line from incoming background watcher logs.
- **Metric Protection:** Implement an inactivity heartbeat (30-minute threshold) to automatically end long-running idle sessions and preserve the chronological integrity of writing episodes.

### Phase 2: Passive Push-Based Linting and Input Safety (UX Optimization)
- **Continuous Validation:** Execute the deterministic graph prerequisite checks locally on every file collection (<20ms execution time). Push newly discovered gaps or resolutions directly to a TUI status bar.
- **Fuzzy Intent Matching:** Implement non-blocking string-distance lookups upon running `intent declare`. Warn authors of potential typos immediately at entry time without breaking their flow.
- **Destructive Action Protections:** Introduce watch-loop safeguards that detect massive text removals (>30% paragraph deletions) and halt automated snapshotting, prompting the user for verification.

### Phase 3: Version Control and Visual Debugging (Data Integrity)
- **History Undo:** Create a `revision rollback` command that allows authors to prune the most recent, accidental snapshot version and its semantic transitions from the SQLite history frame.
- **Inline Diffs:** Create an inline `diff` command that parses Python’s standard `difflib.unified_diff` with basic red/green ANSI terminal coloring, allowing the author to inspect what specific content modifications drove a transitional summary block.

### Phase 4: Model Context Protocol (MCP) Server (Agentic Conversational Layer)
- **Protocol Server:** Expose AuthorLM's database, graph heuristics, and learning engine as an MCP server.
- **Conversational Integration:** Standardize natural language chats to automatically drive background tool and resource invocations under the hood, replacing manual terminal commands with natural conversational flow.

---

## 9. Architectural Evaluation: Model Context Protocol (MCP) Server vs. Skill

In Phase 4, exposing AuthorLM's capabilities is best handled via a programmatic interface. We evaluate whether a standardized Model Context Protocol (MCP) server or a localized Agentic Skill (e.g., a `.agents/skills/` specification file) is the superior architectural model.

### Overview of the Technologies
- **Model Context Protocol (MCP):** An open, platform-agnostic standard (developed by Anthropic) that defines a formal JSON-RPC protocol over `stdio` or `SSE`. It allows an external LLM agent (like Claude Code, Cursor, or Gemini CLI) to dynamically query custom tools, resources, and pre-formatted prompt templates.
- **Agentic Skill:** A localized, instruction-based Markdown specification (such as a custom `SKILL.md` template) bundled within a specific client framework. It instructs the LLM on how to run shell CLI binaries directly in the host terminal workspace.

### Detailed Tradeoffs Comparison

| Feature Axis                  | Model Context Protocol (MCP) Server                                                                                    | Agentic Workspace Skill                                                                                                    |
| :---------------------------- | :--------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------- |
| **Interoperability**          | **Extremely High:** Compatible with Claude Code, Cursor, Zed, Claude Desktop, and any conforming MCP client natively.  | **Low:** Coupled tightly to a single agent executor framework (e.g., Gemini CLI skills engine).                            |
| **Calling Latency**           | **Extremely Low:** Structured JSON-RPC messages are directly invoked over persistent `stdio` pipes.                    | **High:** Requires spawning sub-processes, invoking shell commands, and parsing text outputs.                              |
| **Token Efficiency**          | **High:** Returns highly structured, minimal JSON payloads containing exact data values.                               | **Low:** The LLM must carry the entire skill instruction set in its context window and parse verbal terminal help outputs. |
| **Reliability**               | **Extremely High:** Schema-validated input types are enforced at the interface boundary before the LLM calls the tool. | **Moderate:** Brittle; vulnerable to shell parsing failures, escaping errors, and command-line signature drifts.           |
| **Implementation Complexity** | **Moderate:** Requires writing a protocol standard server file (such as in Python via the `mcp` SDK).                  | **Extremely Low:** Requires writing a single descriptive `SKILL.md` markdown guide.                                        |

### Decisive Recommendation: The Hybrid Architecture
For Phase 4, **an MCP Server is the vastly superior core architectural interface.** Trapping AuthorLM's intelligent co-editing features inside terminal-exclusive CLI execution creates massive friction for conversational agents. An MCP server decouples the editorial logic from the console, turning AuthorLM into a highly portable local background daemon.

However, we recommend a **Hybrid Architecture**:
1. **The Core Engine as an MCP Server:** Build a programmatic Python MCP server wrapper (`authorlm_mcp.py`) that exposes the SQLite database, Concept Graph operations, and active policies as formal JSON tools and resource paths.
   * *Zero-Redundancy Code Reuse:* To prevent any surface drift between the CLI and the MCP API, the Python MCP server will dynamically generate its entire tool schema at runtime by introspecting the existing `argparse` parser definitions.
   * *Build-Time Enforcement:* A CI unit test will programmatically assert that every registered CLI subcommand maps to an identical, schema-validated MCP tool handler, guaranteeing that the CLI and MCP interfaces remain strictly synchronized with zero manual duplication of code.
2. **The Gemini CLI Skill as a Bootstrap Wrapper:** Maintain a lightweight workspace `SKILL.md` configuration. This skill does not try to manually parse CLI outputs; instead, its sole programmatic purpose is to instruct the client agent on how to launch, configure, and register the AuthorLM MCP server inside the user's workspace.

---

## 10. Conversational Workflow Mapping and Background API Mechanics

To understand how AuthorLM functions behind a conversational chat interface (such as Claude Code or Gemini CLI), we map out the typical creative workflows, the corresponding user dialogues, and the invisible background MCP/API operations.

### Workflow A: Intent Declaration and Immediate Feedback
*An author wants to begin working on a specific concept. They announce this goal conversationally in chat using standard, natural phrasing. Behind the scenes, the workspace auto-detection system triggers the AuthorLM skill based on file header metadata to interpret the intent.*

```
[User Chat Prompt]
"Introduce gravity in preface.md"
                                   │
                                   ▼ (Auto-Detection & Skill Invocation)
                  - Client (e.g. Claude Code) reads 'preface.md' header.
                  - Detects "<!-- authorlm: manuscript -->" metadata comment.
                  - Instantly activates and binds the 'AuthorLM Skill'.
                                   │
                                   ▼ (Natural Language Parsing to Tool Translation)
                  [Agent Calls MCP Tool: declare_intent]
                  - statement: "Introduce gravity"
                  - location: "preface.md"
                                   │
                                   ▼ (AuthorLM SQLite Execution)
                  - Inserts active intent row into 'declared_intents' table.
                  - Local graph validator runs: scans dependencies of 'Gravity'.
                  - Identifies: 'Gravity' depends on 'Field' and 'Distinction'.
                  - Checks status: 'Field' is realized; 'Distinction' is realized.
                                   │
                                   ▼ (MCP JSON Response Payload)
                  {"status": "declared", "dependencies_satisfied": true, "gaps": []}
                                   │
                                   ▼ (Natural Language Synthesis)
[Agent Chat Response]
"Done! I've declared your intent to introduce 'Gravity' in preface.md. I checked your 
Concept Graph: all prerequisites for 'Gravity' are already satisfied ('Field' in Chapter 2 
and 'Distinction' in Chapter 1 are both realized). You're structurally clear to proceed."
```

### Workflow B: Passive Observation and Real-Time Resolution
*The author writes a paragraph in their external editor (e.g., Obsidian) and saves the file. The background watcher detects the change, publishes an MCP resource-updated notification, and the agent proactively translates this JSON event into a conversational update.*

```
[Author Saves File '01-preface.md' with: "Gravity is the curvature of fields..."]
                                   │
                                   ▼ (File Watcher Polls Disk)
               [Watcher Runs Local Command: authorlm collect]
               - Computes checksum of file directory.
               - Creates manuscript version v5.
               - Runs scan_realizations(): matches regex stem for 'Gravity'.
               - Updates node 'Gravity' status to 'realized'.
                                   │
                                   ▼ (MCP Event Published to Client Client)
               - MCP server broadcasts standard SSE/stdio JSON-RPC notification:
                 {
                   "method": "notifications/resources/updated",
                   "params": {
                     "uri": "authorlm://manuscript/revisions",
                     "data": {
                       "version": 5,
                       "newly_realized": ["Gravity"],
                       "gaps_resolved": ["Introduce Gravity"]
                     }
                   }
                 }
                                   │
                                   ▼ (Notification Interception & Chat Translation)
               - Client client (Claude Code / IDE) intercepts the resource-update notification.
               - Invokes the background handler of the 'AuthorLM Skill'.
               - Dynamically translates raw JSON values into natural, cohesive prose.
                                   │
                                   ▼ (Proactive Agent Message)
[Agent Chat Response (Next Turn)]
"I noticed you saved your edits! A new revision (v5) was passively collected. 
The concept 'Gravity' has been successfully realized in preface.md, resolving your 
outstanding prerequisite gap. Nicely done."
```

### Workflow C: Natural Language Conversational Triage
*Rather than forcing the author into a tedious terminal menu (`concept triage`), the author refines their concept graph conversationally.*

```
[User Chat Prompt]
"Actually, gravity also contrasts with the concept of Field of Nothingness. Let's record that."
                                   │
                                   ▼
                   [Agent Calls MCP Tool: link_concepts]
                   - from_node: "Gravity"
                   - relation: "contrasts_with"
                   - to_node: "Field of Nothingness"
                   - status: "declared"
                                   │
                                   ▼ (AuthorLM SQLite Execution)
                   - Inserts new row in 'concept_edges' table.
                                   │
                                   ▼
[Agent Chat Response]
"Perfect. I've linked 'Gravity' as contrasting with 'Field of Nothingness' in your Concept 
Graph. Obsidian's graph view has also been automatically regenerated to display this edge."
```

<!-- Add some question type prompts as examples and map out the processing:
1. I don't understand why you are suggesting a connection between Gravity and The Field of Choice in your latest suggestion. What is causing you to think that there is a connection?"
2. Write a mathematical treatment of  Gravity based on the idea that geometric differences in small loci of choice bouncing around become persistent creating curvature
3. Remove all paragraph that discuss gravity and move to a new self-standing document.
4. Remove the references to gravity and any edgest or policy related to the concept.
-->
### Workflow D: Episode Conclusion and Policy Distillation
*The author completes their writing segment and closes the session. The agent wraps up metrics and extracts new stylistic guidelines.*

```
[User Chat Prompt]
"I think I'm done with this chapter. Let's close the active session."
                                   │
                                   ▼
                   [Agent Calls MCP Tool: close_session]
                   - intent_outcome: "Gravity introduced successfully"
                                   │
                                   ▼ (AuthorLM Execution)
                   - Updates active intent status to 'completed'.
                   - Closes active 'editorial_episode'.
                   - Compiles transitions of the closed episode.
                   - Runs LLM episode analysis locally (analysis.py).
                   - Analyzes transition: "Opened with a metaphor before formalizing."
                   - Seeds new candidate policy in 'editorial_policies'.
                                   │
                                   ▼ (MCP JSON Response Payload)
                   {
                     "session_id": "ses-98fd",
                     "duration_minutes": 45,
                     "concepts_realized": 1,
                     "evidence_weight_high": 1,
                     "seeded_policy": "Introduce physical phenomena via metaphor before math models"
                   }
                                   │
                                   ▼
[Agent Chat Response]
"Active session closed successfully! Your learning velocity for this 45-minute episode:
- 1 concept realized ('Gravity')
- 1 high-weight evidence point recorded

Based on an analysis of your edits, I noticed you opened with a tide metaphor before 
presenting formal equations. I've seeded a new candidate policy: 'Introduce physical 
phenomena via metaphor before introducing mathematical models'. I'll remind you of this 
the next time we start writing!"
```

<!-- Is there a way to automatically detect when a new claude chat window is opened, and open a new session? or can we close all sessions that are inactive for more than an three hours? I don't want to need to close sessions or remember to do so. -->

---

### Explicit Directives and Metadata-Based Manuscript Auto-Identification
Conversational, conversational-style prompts like *"Introduce gravity in preface.md"* are highly natural, but the system must know which files are manuscript files and which are unrelated workspace assets without requiring a hardcoded sqlite registry database as the sole source of truth.

1. **Metadata-Based File Identification (HTML Comments / Frontmatter):**
   - Every file that is a tracked manuscript carries a simple, unobtrusive metadata header, e.g.:
     ```html
     <!-- authorlm: manuscript -->
     ```
     or
     ```yaml
     ---
     authorlm: manuscript
     ---
     ```
   - When the user prompts the chat with *"Introduce gravity in preface.md"*, the workspace Agent Skill reads the directory, detects the `authorlm: manuscript` comment inside `preface.md`, and instantly maps the natural command to an AuthorLM active-intent declaration. Unmarked files are ignored, avoiding false-positive background tracking.

2. **Explicit Skill Invocations via `/authorlm` Prefix:**
   - For administrative actions that feel more explicit, the user can use a `/authorlm` command prefix. This provides a clear, predictable interface to the underlying skill, e.g.:
     ```
     /authorlm add doc chapter-4.md
     ```
   - The Agent Skill intercepts this command, scaffolds the file `chapter-4.md` locally, automatically injects the `<!-- authorlm: manuscript -->` identification header at the top, and registers it in SQLite via the `doc add` API.
   - This keeps administrative tasks precise and accessible without requiring the user to switch to the terminal or use complex multi-step prompts.

---

## 11. LaTeX Mathematical Formatting and Cross-Platform Rendering

When AuthorLM's drafting LLM generates mathematical formulas (such as during a mathematical treatment of Gravity), the formatting of equations represents a crucial crossover point between the terminal shell, conversational chat interfaces (like Claude Code), and external markdown editors (specifically Obsidian).

### Current Status and Limitations
Currently, AuthorLM does not process or parse LaTeX symbols returned in drafted bridge paragraphs. Raw LaTeX tokens (such as `\nabla \times \mathbf{E}` or `\sigma(x)`) are printed directly to the terminal stdout. While this is unrendered and can look complex or cluttered in the console, it is readable, and the priority is to ensure absolute correctness and visual rendering in the final publishing medium.

### Optimal Strategy for Obsidian and Web Integrations
To ensure that mathematical sections are visually perfect in the author's primary writing canvas and the agent interface, the drafting system must enforce strict formatting constraints:
1. **MathJax Compatibility (Obsidian Native):** Obsidian natively parses and renders MathJax formulas out of the box in its reading and editing live-preview views. To trigger this, the drafting LLM must be explicitly instructed to output formal math blocks using standard MathJax wrapper notation:
   - **Inline Equations:** Wrapped in single dollar signs, e.g., `$ f(x) = \sigma(x) $`.
   - **Display Block Equations:** Wrapped in double dollar signs, e.g., `$$ \nabla \times \mathbf{E} = -\frac{\partial \mathbf{B}}{\partial t} $$`.
2. **Claude Chat Compatibility:** Modern agentic chat interfaces (like Claude Code or Gemini CLI) natively compile KaTeX/MathJax tokens, so standard math wrappers are rendered as beautiful textbook-quality equations inline within the conversation block.

### Tradeoffs and Terminal Coexistence
While complex LaTeX equations remain unrendered plain text on the raw terminal stdout, this is an acceptable tradeoff for the current phase of development:
- **Pros of Raw LaTeX on CLI:** Preserves 100% semantic fidelity. When the author copies the draft from the terminal and pastes it into their Obsidian editor, it renders instantly without needing any regex translation or formatting cleanup.
- **Cons of Raw LaTeX on CLI:** High visual clutter inside standard monospace terminal screens.
- **Resolution:** We prioritize downstream publishing fidelity (Obsidian/PDF/HTML) over upstream CLI formatting. By structuring the LLM's system prompt in `guidance.py` to always return clean, formal MathJax syntax, we guarantee that the final manuscript files are immediately render-ready upon collection.
