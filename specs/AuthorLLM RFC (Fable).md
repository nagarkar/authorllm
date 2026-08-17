# **AuthorLM — Specialization RFC (Version 2.1)**

**Status:** Synthesized specification (v2.1 — selected Version 1 concepts reinstated) **Derived from:** Version 2 draft, "AuthorLM RFC" (specialization of the Common Core "Decision Learning Engine" RFC), updated per the V1-vs-V2 concept-loss analysis **Companion documents:** Common Core RFC (Decision Learning Engine), BTA RFC (Bug Triage Agent specialization)

---

## **Change Log (v2.1)**

This revision reinstates and strengthens Version 1 concepts that did not survive the Version 2 rewrite. Nothing was removed; every change is additive.

**Reinstated:**

- **Session-Opening Learning Briefing** — the AuthorLM analogue of V1's Daily Knowledge Report and Knowledge Review Session. New pipeline Stage 0 (§19.3) and new Interactive Authoring section (§20.3); reflected in the editorial loop (§17.11) and the prototype session workflow (§23.4).  
- **End-to-End Walkthrough** — new Appendix A tracing one editorial episode through the complete lifecycle in manuscript terms, with example object sketches and a flow diagram.  
- **KnowledgeObject base class** — a uniform base for every persistent object, giving uniform identity, versioning, provenance, and serialization (§18.3).  
- **Learning velocity** — a first-class metric of how much editorial understanding increased per session, distinct from productivity metrics (§24.4).

**Strengthened:**

- **Abstention** as a valid editorial output — the engine may abstain from suggesting entirely (§11.5; echoed in §14.3 and §19.10).  
- **Replay-based experimentation discipline** — changes to AuthorLM itself are experiments evaluated against historical writing sessions; one variable at a time; failed experiments preserved (§24.5).  
- **Prompt/reasoning-artifact versioning discipline** — versioned artifacts with declared I/O, replay-evaluated, versions never overwritten (§24.6).  
- **Four engineering-decision questions** added to the prototype philosophy (§23.1).

---

## **Overview**

This RFC specializes the Common Core Decision Learning Engine (DLE) architecture for **philosophy writing**. Throughout this document:

- The **artifact** is a **manuscript**.  
- The **execution engine** is **AuthorLM** — and, crucially, the human author collaborating with it. Unlike the BTA, there is no independent automated execution engine: *the author is the execution engine*.  
- The **objective** is to learn editorial decision making from an expert author.

All conceptual definitions — artifacts, intent, evidence, knowledge, and learning — are inherited from the Common Core RFC. This document focuses exclusively on editorial behavior and manuscript evolution.

A defining contrast with the sibling BTA specialization, integrated throughout this document:

| Dimension | BTA | AuthorLM |
| :---- | :---- | :---- |
| Driving force | **Event-driven by the external world** (bugs change → learn → generate playbook) | **Intent-driven by the author** (author declares or reveals intent → write → revise → learn) |
| Pipeline entry point | New observations | The **active writing objective**; revisions are evidence of how that objective was pursued, not the objective itself |
| Implementation emphasis | **Execution** — safely converting learned knowledge into actions via the Markdown playbook | **Collaboration** — a long-lived, intent-driven dialogue with the author |
| Execution engine | Another agent (the Bug Triage Agent) | The human author |
| Enduring asset | The **Replay Corpus** — a memory of **experience** | The **Concept Graph** — a memory of **ideas** |
| Highest-value persistent object | The **review** | The **idea** |
| What is being learned | Organizational practice (current operational judgment) | An individual's evolving intellectual practice (continuity across years of thinking) |

Everything else — the learning architecture, memory, episodes, intent, evidence, beliefs, replay — remains the same as the Common Core. That the two specializations diverge only at the execution engine is strong evidence that the Common Core has found the right level of abstraction.

---

## **Chapter 1 — Philosophy Writing Domain**

### **1.1 The Manuscript as an Artifact**

A manuscript evolves through repeated revisions. Its observable state includes:

- chapters,  
- sections,  
- paragraphs,  
- examples,  
- diagrams,  
- references,  
- concept graph,  
- narrative flow.

Every revision reflects one or more editorial decisions. The DLE observes these revisions to reconstruct the author's editorial philosophy.

### **1.2 Writing Intent**

Editorial decisions exist to accomplish writing intents. Examples:

- Introduce a new concept.  
- Clarify an argument.  
- Address an anticipated objection.  
- Improve narrative flow.  
- Increase conceptual rigor.  
- Transition between topics.  
- Prepare for mathematical formalization.  
- Strengthen intuition.  
- Increase historical context.

The DLE learns these intents alongside the edits themselves.

### **1.3 Operational Objective**

The objective of AuthorLM is **not to imitate prose**. It is to learn the author's editorial decision-making process. As organizational knowledge accumulates, AuthorLM becomes capable of:

- proposing revisions,  
- recommending topic order,  
- suggesting conceptual bridges,  
- identifying missing explanations,  
- maintaining stylistic consistency,  
- generating future drafts that align with the author's evolving philosophy.

### **1.4 Scope**

This RFC defines the specialization of the Decision Learning Engine for philosophy writing. All conceptual definitions are inherited from the Common Core RFC.

---

## **Chapter 2 — Editorial Ontology**

This chapter specializes the Common Core ontology for philosophy writing.

### **2.1 Artifact**

The artifact is a manuscript, consisting of **nested artifacts**:

flowchart TD

    B\[Book\] \--\> C\[Chapter\] \--\> S\[Section\] \--\> P\[Paragraph\] \--\> Se\[Sentence\] \--\> K\[Concept\]

Each level may evolve independently (and, per the Representation Model, each level possesses independent versions).

### **2.2 Context**

Editorial context may include:

- previous chapters,  
- concept graph,  
- unresolved objections,  
- narrative position,  
- intended audience,  
- writing goals,  
- references,  
- style guidelines.

### **2.3 Intent**

Typical editorial intents include: introduce a concept; clarify an argument; transition between ideas; address an objection; increase rigor; improve intuition; foreshadow future material; strengthen historical grounding; reduce ambiguity.

**Intent becomes one of the strongest predictors of editorial decisions.**

### **2.4 Decisions**

Editorial decisions include: rewrite, insert, delete, reorder, merge, split, annotate, illustrate, define, summarize.

### **2.5 Policy**

Editorial policies describe **reusable writing behavior**. Examples:

- Introduce intuition before formalism.  
- Define terms before abstraction.  
- Present objections before conclusions.  
- Bridge conceptual gaps with historical examples.  
- End each major section with a synthesis.

AuthorLM gradually **discovers** these policies from repeated revisions rather than requiring them to be authored manually.

---

## **Chapter 3 — Editorial Learning Architecture**

### **3.1 Observation**

AuthorLM observes every manuscript revision. Each observation records: manuscript version, affected sections, inserted text, deleted text, reordered material, comments, author notes. **Every revision becomes replayable.**

### **3.2 Context**

Editorial context extends well beyond the edited paragraph: surrounding chapters, concept graph, unresolved arguments, anticipated objections, narrative position, audience assumptions, historical revisions.

**Understanding context is often more important than understanding the local edit.**

### **3.3 Intent**

Editorial intent is central to learning. Intent may be **explicit** ("Introduce trajectories.") or **inferred** (prepare the reader, clarify terminology, strengthen intuition, transition topics, increase rigor). AuthorLM should preserve both explicit and inferred intents.

### **3.4 Decision Extraction**

Editorial decisions include rewriting, inserting, deleting, moving, splitting, merging, defining, illustrating. Each decision is linked to the intent that motivated it whenever possible.

### **3.5 Policy Learning**

AuthorLM gradually discovers reusable editorial policies. Example learned policy:

- **Intent:** Introduce New Concept  
- **Context:** Concept not previously defined  
- **Decision sequence:** Historical example → Definition → Intuition → Formal treatment

These policies become reusable editorial knowledge rather than stylistic imitation.

### **3.6 Execution Guidance**

Editorial policies are compiled into structured writing guidance consumed by AuthorLM: recommended edits, suggested section order, conceptual bridges, missing prerequisites, likely next topics.

**Unlike the BTA specialization, execution guidance for AuthorLM is collaborative rather than autonomous.**

---

## **Chapter 4 — Editorial Architecture**

Specializes the Common Core logical architecture (capabilities).

### **4.1 Observation**

The Observation Capability records every manuscript revision: textual edits, structural edits, reordered sections, inserted examples, concept graph updates, author notes. Every revision is preserved.

### **4.2 Context**

Editorial context is derived from the manuscript as a whole: concept dependencies, unresolved arguments, narrative progression, historical revisions, target audience, stylistic conventions. Context often spans multiple chapters.

### **4.3 Intent**

Intent plays a central role in editorial reasoning: prepare for a later concept, strengthen intuition, answer an objection, improve clarity, increase rigor, improve narrative flow. Intent may be supplied explicitly by the author ("Next: introduce trajectories") or inferred from editing behavior.

### **4.4 Decision**

Editorial decisions include rewrite, reorder, split, merge, expand, summarize, define, illustrate. The DLE associates each decision with its inferred or explicit intent.

### **4.5 Execution**

Execution guidance is delivered through AuthorLM: suggested revisions, missing explanations, proposed transitions, concept ordering, candidate next topics. **The author remains the final decision maker.**

### **4.6 Evaluation**

Evaluation occurs through editorial review. Signals: accepted suggestions, rejected suggestions, rewritten suggestions, author comments, subsequent revisions. These become evidence for future learning.

---

## **Chapter 5 — Editorial Representation Model**

### **5.1 Artifact**

The primary artifact is a manuscript, containing nested artifacts (Book → Chapter → Section → Paragraph → Sentence → Concept). **Each level possesses independent versions.**

### **5.2 Artifact Version**

Each revision records the observable state of the manuscript: text, structure, concept graph, references, figures, notes, annotations.

### **5.3 Context**

Editorial context includes concept dependencies, chapter sequence, unresolved objections, narrative progression, reader assumptions, stylistic conventions. Context often spans multiple artifact levels.

### **5.4 Intent**

- **Declared intents** originate from the author: "Introduce trajectories.", "Strengthen the argument.", "Bridge Sermon Three to Four."  
- **Inferred intents** include: prepare formalism, improve intuition, clarify terminology, reduce ambiguity, improve flow.

The DLE learns to associate editorial decisions with both forms of intent.

### **5.5 Decisions**

Rewrite, insert, delete, reorder, merge, split, define, summarize, illustrate, cross-reference. Each decision contributes to the evolution of the manuscript.

### **5.6 Execution Guidance**

Editorial policies become structured writing guidance. Typical outputs: recommended edits, suggested transitions, missing prerequisite concepts, conceptual inconsistencies, candidate next topics, possible objections. AuthorLM presents guidance collaboratively rather than executing changes autonomously.

---

## **Chapter 6 — Editorial Knowledge Architecture**

### **6.1 Editorial Knowledge Graph**

The Editorial Knowledge Graph contains objects such as: Manuscripts, Chapters, Concepts, Editorial Decisions, Declared Intents, Inferred Intents, Editorial Policies, Concept Relationships, Author Feedback.

The graph represents the evolution of **both** the manuscript and the author's editorial philosophy.

### **6.2 Concept Graph**

One of the most important knowledge structures is the **Concept Graph**. Rather than merely storing text, AuthorLM maintains explicit relationships between concepts. For example:

flowchart TD

    Choice \-- distinguishes \--\> Distinction

    Distinction \-- creates \--\> Field

    Field \-- permits \--\> Trajectory

    Trajectory \-- defines \--\> History

The Concept Graph supports:

- prerequisite detection,  
- topic sequencing,  
- conceptual consistency,  
- future chapter planning.

### **6.3 Editorial Policies**

Editorial policies represent reusable writing behavior: define before abstraction; historical example before formalism; objections before conclusions; summarize before introducing new terminology. These policies emerge from repeated editorial decisions.

### **6.4 Explanations**

**Every editorial recommendation should explain itself.** For example:

"A bridge paragraph is recommended because the previous three chapters consistently introduced prerequisite concepts before mathematical formalization."

Explanations help the author evaluate recommendations rather than merely accept or reject them.

### **6.5 Manuscript Replay**

The Editorial Knowledge Graph supports replay of the manuscript's evolution. Replay answers questions such as:

- When was this concept introduced?  
- Why was this section reorganized?  
- Which editorial policy motivated this revision?  
- How has the author's philosophy evolved?

This allows AuthorLM to learn not only from the final manuscript but from the **complete editorial process**.

---

## **Chapter 7 — Editorial Inference**

### **7.1 Intent Inference**

Editorial intent may be inferred when not explicitly declared: prepare a future concept, answer an anticipated objection, strengthen intuition, improve conceptual flow, reduce ambiguity. **Declared editorial goals always take precedence.**

### **7.2 Concept Inference**

The DLE may infer relationships between concepts: prerequisite concepts, conceptual dependencies, recurring metaphysical structures, missing intermediate ideas. These inferred relationships gradually enrich the Concept Graph.

### **7.3 Structural Inference**

AuthorLM may infer: missing bridge sections, redundant discussions, opportunities for reordering, chapter boundaries, conceptual clustering. **These suggestions remain hypotheses until reviewed by the author.**

### **7.4 Narrative Prediction**

Given the current manuscript, AuthorLM may predict: likely next topics, likely future objections, likely supporting examples, likely missing definitions. These predictions assist the author **without constraining future creativity**.

---

## **Chapter 8 — Editorial Knowledge Evolution**

### **8.1 Editorial Evolution**

An author's editorial philosophy evolves over time: increased mathematical rigor, changing narrative style, new conceptual vocabulary, refined metaphysical framework, evolving pedagogical approach. **AuthorLM should evolve alongside the author rather than preserving outdated writing habits.**

### **8.2 Promotion**

Editorial patterns gradually become reusable policies:

Repeated Revision → Editorial Pattern → Candidate Editorial Policy → Validated Editorial Policy → Reusable Writing Guidance

### **8.3 Demotion**

Editorial policies weaken when newer revisions consistently contradict them. For example, earlier chapters may introduce formalism before intuition, while later work consistently reverses that pattern. The DLE should recognize that the author's philosophy has evolved.

### **8.4 Style Drift**

Style drift is **expected**: sentence complexity, vocabulary, rhetorical structure, explanatory depth. The DLE should distinguish intentional evolution from isolated exceptions.

### **8.5 Conceptual Drift**

Conceptual evolution is distinct from stylistic evolution: revised metaphysical assumptions, renamed concepts, refined definitions, new conceptual dependencies. **Tracking conceptual drift is one of AuthorLM's primary responsibilities.**

### **8.6 Human Review**

The author continuously shapes knowledge evolution through explicit intents, editorial revisions, accepted suggestions, rejected suggestions, and explanatory comments. These interactions provide the evidence from which AuthorLM learns.

---

## **Chapter 9 — Editorial Intent Lifecycle**

### **9.1 Editorial Intents**

Editorial intents include: introduce concepts, answer objections, improve rigor, improve intuition, strengthen transitions, prepare future discussions, summarize arguments. **Editorial intents often span multiple chapters.**

### **9.2 Declared Editorial Intent**

The author may explicitly declare goals: "Next, introduce trajectories.", "Strengthen this argument.", "Prepare for the discussion of gravity." **These declarations become authoritative observations.**

### **9.3 Editorial Refinement**

An editorial objective may become increasingly precise:

Improve Clarity → Prepare Reader → Introduce Trajectories → Contrast with Geodesics → Lead into Gravity

AuthorLM should learn this refinement process over time.

### **9.4 Multiple Editorial Intents**

A chapter frequently pursues several objectives simultaneously: teach, persuade, foreshadow, summarize, motivate. The DLE should model their **interaction** rather than reducing them to a single purpose.

### **9.5 Editorial Completion**

An editorial intent completes when the author judges the objective to have been achieved. Subsequent revisions may reactivate similar intents in later chapters.

### **9.6 Editorial Evolution**

As the author's philosophy evolves, new kinds of editorial intents may appear. AuthorLM should treat these as **opportunities for learning rather than anomalies**.

---

## **Chapter 10 — Editorial Episodes**

### **10.1 Introduction**

Writing a philosophical work is inherently **episodic**. A single editorial objective often spans many revisions, sections, and chapters. AuthorLM therefore learns from **editorial episodes rather than isolated edits**.

### **10.2 Typical Episode**

flowchart TD

    DI\["Declared Intent: Introduce Trajectories"\] \--\> A\[Insert Bridge Paragraph\]

    A \--\> B\[Rewrite Earlier Definition\]

    B \--\> C\[Move Historical Example\]

    C \--\> D\[Add Diagram\]

    D \--\> E\["Outcome: Trajectories Introduced"\]

The episode captures the author's **complete strategy** for accomplishing a conceptual objective.

### **10.3 Editorial Outcomes**

Typical outcomes: concept successfully introduced, objection resolved, narrative strengthened, chapter reorganized, future discussion prepared. These outcomes become evidence for future editorial policies.

### **10.4 Manuscript Replay**

Replay reconstructs complete editorial episodes. This enables AuthorLM to learn not only *which* edits occurred, but *how* complex conceptual objectives were achieved through coordinated editorial work.

---

## **Chapter 11 — Editorial Beliefs**

### **11.1 Editorial Beliefs**

AuthorLM maintains beliefs about: editorial policies, concept ordering, narrative structure, stylistic preferences, pedagogical techniques, conceptual dependencies. These beliefs evolve as the manuscript evolves.

### **11.2 Confidence**

Confidence **increases** when the author repeatedly makes similar editorial decisions across independent episodes. Confidence **decreases** when later revisions consistently diverge from earlier editorial patterns.

### **11.3 Maturity**

Editorial policies mature through repeated application across multiple chapters, manuscripts, or writing projects. The engine should distinguish a **temporary writing experiment** from a **durable editorial philosophy**.

### **11.4 Editorial Uncertainty**

When uncertainty is high, AuthorLM should present **alternatives rather than recommendations**: two possible topic orders, competing bridge paragraphs, multiple conceptual explanations. This encourages **collaboration rather than imitation**.

### **11.5 Abstention**

When evidence is insufficient or conflicting, AuthorLM may go one step further and **abstain from suggesting entirely**. Abstention is stronger than presenting alternatives: rather than offering competing bridge paragraphs or topic orders, the engine explicitly reports that it has no editorially justified recommendation — for example, when a chapter has no precedent in prior episodes, when two established policies point in opposite directions, or when recent revisions contradict the very policies a suggestion would rely upon.

**Abstention is a valid output, not a failure.** An unfounded suggestion costs the author time and erodes confidence in every future suggestion; a candid "I have insufficient evidence here" preserves author trust — and typically becomes an outstanding question raised in the next session-opening briefing (§20.3).

### **11.6 Explainability**

Every editorial suggestion should explain:

- which prior revisions inspired it,  
- which editorial intents it serves,  
- which writing policies support it,  
- which competing approaches remain plausible.

The author should understand not only *what* is suggested, but *why*.

---

## **Chapter 12 — Editorial Learning Loops**

### **12.1 Editorial Loop**

The primary editorial loop:

Draft → Editorial Episode → Editorial Policies → Writing Guidance → Author Review → Revised Draft

Every revision becomes additional editorial evidence.

### **12.2 Intent Loop**

Declared intentions play a particularly important role. The author may state: "Introduce trajectories.", "Strengthen this objection.", "Prepare for gravity." AuthorLM observes how these declared intents are realized through editorial decisions. This loop enables **direct learning from author objectives**.

### **12.3 Concept Loop**

Conceptual organization also evolves:

Concept Graph → Editorial Decisions → Updated Concept Graph → Improved Topic Suggestions

The DLE learns not only writing style but **conceptual structure**.

### **12.4 Editorial Review Loop**

The author evaluates recommendations by accepting, rejecting, rewriting, or explaining. Each response becomes evidence. **Explanatory feedback is particularly valuable because it often exposes previously hidden intent.**

### **12.5 AuthorLM Improvement Loop**

Replay evaluates: editorial suggestions, topic sequencing, concept graph evolution, explanation quality, narrative planning. The DLE gradually becomes a better editorial collaborator **without assuming that the author's philosophy is static**.

---

## **Chapter 13 — Editorial Memory**

### **13.1 Episodic Memory**

Editorial episodes preserve: declared intent, inferred intent, revisions, explanations, accepted edits, rejected edits. These episodes become the author's **editorial history**.

### **13.2 Semantic Memory**

Editorial Semantic Memory stores: editorial policies, concept relationships, pedagogical techniques, stylistic conventions, recurring rhetorical structures. This memory represents the author's **accumulated editorial philosophy**.

### **13.3 Working Memory**

When generating editorial guidance, Working Memory may include: the current chapter, neighboring chapters, the Concept Graph, active editorial intents, retrieved editorial episodes, relevant policies. Working Memory should **remain focused on the current editorial objective**.

### **13.4 Intent Memory**

Intent Memory stores recurring writing objectives: prepare the reader, answer objections, increase rigor, introduce a concept, foreshadow future discussion. The DLE gradually learns how these objectives are typically achieved.

### **13.5 Concept Memory (domain-specific)**

AuthorLM introduces an additional **domain-specific memory**: **Concept Memory** preserves the evolving Concept Graph. Unlike Episodic Memory, Concept Memory represents **relationships among ideas rather than relationships among revisions**. It supports prerequisite discovery, topic sequencing, conceptual consistency, and future chapter planning.

---

## **Chapter 14 — Editorial Reasoning**

### **14.1 Editorial Inputs**

Editorial reasoning considers: current manuscript, active editorial episode, declared intents, inferred intents, Concept Graph, editorial policies, previous revisions, unresolved conceptual questions.

### **14.2 Editorial Questions**

Typical reasoning questions:

- What is the author trying to accomplish?  
- Which concepts are missing?  
- Which bridge is needed?  
- Which objection remains unanswered?  
- What topic naturally follows?  
- Which previous editorial episode resembles this one?

### **14.3 Editorial Suggestions**

Editorial reasoning produces: revision suggestions, alternative structures, bridge paragraphs, concept ordering, topic recommendations, missing prerequisites, conceptual questions. The author evaluates these suggestions before they become evidence. When evidence is insufficient or conflicting, editorial reasoning may also conclude that **no suggestion should be offered at all** — abstention is a valid result (§11.5).

### **14.4 Editorial Explanations**

Every suggestion should explain: which previous episodes inspired it, which editorial policies it follows, which intent it serves, which competing alternatives were considered.

**AuthorLM should function as an editorial collaborator rather than an autonomous author.**

---

## **Chapter 15 — Editorial Interfaces**

Specializes the Common Core capability interfaces.

| Capability | Input | Output |
| :---- | :---- | :---- |
| **Observation** | Version-controlled manuscript; author notes; declared intents | Editorial Episodes; Artifact Versions |
| **Context** | — | Retrieves surrounding chapters, Concept Graph, editorial policies, historical revisions, unresolved conceptual questions |
| **Reasoning** | — | Editorial suggestions, bridge proposals, topic recommendations, conceptual questions, alternative structures, explanations |
| **Execution** | Suggestions | Collaborative editing: the author may accept, reject, modify, or replace any suggestion; the accepted revision becomes the next observed artifact version |
| **Evaluation** | Accepted edits, rejected edits, rewritten suggestions, author explanations, subsequent revisions | New editorial evidence and updated editorial policies |

---

## **Chapter 16 — Execution Architecture**

### **16.1 Editorial Execution Levels**

AuthorLM also supports **progressive autonomy**:

- **Level 0** — Observe manuscript revisions.  
- **Level 1** — Suggest edits. The author performs all modifications.  
- **Level 2** — Prepare complete revisions. The author explicitly accepts or rejects them.  
- **Level 3** — Execute low-risk editorial operations automatically (formatting, cross-reference updates, bibliography maintenance, diagram numbering). Conceptual edits remain collaborative.  
- **Level 4** — Limited autonomous drafting may be appropriate for narrowly scoped tasks, such as generating initial examples or placeholder transitions, subject to subsequent author review.

The architecture assumes that **core philosophical content remains under the author's direction**.

### **16.2 Execution Adapter**

The Execution Adapter translates editorial guidance into concrete manuscript modifications: text insertions, paragraph restructuring, chapter reordering, concept graph updates, reference management. The adapter **isolates AuthorLM from any specific document format or editor**.

### **16.3 Editorial Safety**

Execution policies should distinguish between:

- **mechanical edits**,  
- **stylistic edits**,  
- **conceptual edits**.

Each category may operate at a different autonomy level. This allows AuthorLM to automate routine tasks while **preserving the author's authority over meaning**.

### **16.4 Outcome Collection**

The DLE observes: accepted suggestions, rejected suggestions, rewritten suggestions, explanatory comments, subsequent revisions. These outcomes continuously refine AuthorLM's editorial knowledge.

---

## **Chapter 17 — Reference Architecture**

### **17.1 Introduction**

AuthorLM specializes the Common Core architecture for long-form writing. Its objective is not to imitate prose, but to learn how an author develops ideas through successive revisions. The implementation focuses on capturing editorial episodes, declared intent, concept evolution, and author feedback. Like the BTA implementation, the initial system is designed to be built quickly using **Python, SQLite, a local LLM, and version-controlled manuscript data**.

### **17.2 Runtime Architecture**

flowchart TD

    MR\[Manuscript Repository\] \--\> RC\[Revision Collector\]

    RC \--\> RA\[Revision Analyzer\]

    RA \--\> EB\[Editorial Episode Builder\]

    EB \--\> CB\[Context Builder\]

    CB \--\> IE\[Intent Engine\]

    IE \--\> LE\[Learning Engine\]

    LE \--\> EK\[Editorial Knowledge\]

    EK \--\> GG\[Guidance Generator\]

    GG \--\> AU\[Author\]

    AU \--\> RM\[Revised Manuscript\]

    RM \--\> NC\[Next Revision Cycle\]

    NC \--\> RC

### **17.3 Revision Collector**

Acquires successive manuscript versions. Sources may include: Git history, local document snapshots, editor exports, manually saved revisions. **Every revision becomes immutable.**

### **17.4 Revision Analyzer**

Computes **editorial transitions**: inserted paragraphs, rewritten sections, reordered chapters, deleted concepts, added diagrams. These transitions become the raw observations for learning.

### **17.5 Editorial Episode Builder**

Groups individual revisions into editorial episodes. An episode may span several revisions, one declared objective, and multiple editing sessions. Episodes preserve the complete evolution of a writing objective.

### **17.6 Context Builder**

Editorial context includes: surrounding chapters, Concept Graph, unresolved objections, historical revisions, declared future topics, narrative structure. **Reasoning begins only after this context has been assembled.**

### **17.7 Intent Engine**

The Intent Engine combines:

- **Declared Intent** — "Next introduce trajectories.", "Strengthen this objection.", "Clarify nothingness."  
- **Inferred Intent** — improve intuition, prepare formalism, reduce ambiguity, bridge chapters.

**Declared intent remains authoritative.**

### **17.8 Learning Engine**

Identifies recurring editorial behavior; gradually discovers: writing policies, concept sequencing, explanation patterns, transition strategies, recurring narrative structures. The goal is to learn **editorial judgment rather than writing style alone**.

### **17.9 Editorial Knowledge**

Stores: concept relationships, editorial policies, recurring intents, accepted revisions, rejected revisions, author explanations. This becomes the author's **evolving editorial memory**.

### **17.10 Guidance Generator**

Rather than executing edits automatically, the Guidance Generator proposes: revisions, bridge paragraphs, topic ordering, missing concepts, unanswered objections, likely next topics. **Suggestions remain collaborative.**

### **17.11 Editorial Loop (daily workflow)**

1. Open the session with a learning briefing — what AuthorLM learned since the last session (§20.3).  
2. Capture new manuscript revisions.  
3. Build editorial transitions.  
4. Construct editorial episodes.  
5. Retrieve relevant editorial memory.  
6. Infer active intents.  
7. Update editorial knowledge.  
8. Generate writing guidance.  
9. Review and edit.  
10. Capture the resulting revisions.  
11. Learn from the new evidence.

### **17.12 Prototype Philosophy**

The prototype should optimize for: preserving the author's intent, explainability, rapid iteration, replay, collaborative editing. The architecture deliberately favors **learning from the author's evolving practice over generating increasingly sophisticated prose**.

### **17.13 Where the RFCs Diverge (design note)**

This is the point where the two specialization RFCs meaningfully diverge. The Common Core explains *why* the system works; the implementation chapters explain *how* each domain realizes the architecture. The BTA RFC focuses on APIs, Markdown playbooks, replay, and policy execution; the AuthorLM RFC increasingly emphasizes **concept graphs, editorial planning, and interactive writing workflows**. That divergence is a sign that the Common Core has successfully isolated the shared theory from domain-specific implementation.

---

## **Chapter 18 — Reference Data Model**

### **18.1 Introduction**

AuthorLM stores editorial knowledge using the same principles as the Common Core. Rather than representing a document editor's internal structures, the database preserves the **author's editorial process**. **SQLite** is recommended for the initial implementation because it is simple, transparent, and well suited to replay.

### **18.2 Design Principles**

The editorial data model should:

- preserve every revision,  
- preserve every declared intent,  
- preserve editorial episodes,  
- separate observations from learned knowledge,  
- remain understandable without specialized tools.

### **18.3 The KnowledgeObject Base Class**

Before defining any table, introduce a single uniform base for **every canonical, persistent object** — manuscripts, versions, transitions, episodes, declared and inferred intents, policies, reviews, guidance records, and concept-graph entries:

class KnowledgeObject(BaseModel):

    id: UUID

    version: int

    created\_at: datetime

    created\_by: str

    schema\_version: str

    metadata: dict\[str, Any\]

Every canonical object derives from this base. The payoff is uniformity: every piece of editorial knowledge shares the same **identity**, **versioning**, **provenance**, and **serialization** model. This dramatically simplifies replay, storage, logging, and future schema evolution, because every object shares the same lifecycle and metadata conventions — a small addition that directly implements the Common Core principles of stable identity and mandatory provenance.

### **18.4 Core Tables**

| Table | Purpose |
| :---- | :---- |
| `manuscripts` | One row per manuscript. |
| `manuscript_versions` | One row per saved revision: timestamp, source, checksum, raw document. The complete manuscript should always be recoverable. |
| `editorial_transitions` | One row per detected edit (insert, rewrite, delete, reorder). |
| `editorial_episodes` | Groups related revisions into coherent writing episodes. |
| `declared_intents` | Explicitly recorded writing objectives ("Introduce trajectories.", "Strengthen objection.", "Improve clarity."). These remain authoritative observations. |
| `inferred_intents` | The engine's inferred editorial objectives. Keeping declared and inferred intents separate simplifies replay and evaluation. |
| `concept_graph` | Relationships among concepts. Typical relationship types: prerequisite, elaborates, contrasts, depends\_on, references. Unlike the Common Core Knowledge Graph, this table is domain-specific. |
| `editorial_policies` | Reusable editorial patterns (define before abstraction; historical example before mathematics). |
| `editorial_reviews` | Accepted, rejected, and rewritten suggestions plus explanatory comments — the **highest-quality editorial evidence**. |
| `guidance_history` | Generated editorial guidance: suggestions, explanations, author response. Allows replay of the editorial collaboration. |

### **18.5 What Is Not Stored**

The prototype intentionally avoids storing: LLM conversations, embeddings, temporary prompts, intermediate reasoning. **Only durable editorial knowledge should persist.**

### **18.6 Schema Evolution**

As the author's workflow evolves, new tables may be added. **Historical revisions should never be rewritten.** Replay should remain possible using the original observations.

### **18.7 Prototype Philosophy**

The database should tell the story of how the manuscript evolved. **Reading the tables should feel like reading the history of the book rather than the internal state of an AI system.**

### **18.8 Persistence Principle (design note)**

This is a conscious deviation from many modern AI systems, which store everything: prompts, tool calls, embeddings, chain-of-thought-like traces, intermediate reasoning, caches, transient state. The DLE should be far more disciplined. The rule:

**Persist only what you cannot reliably reconstruct.**

Consequences: keep immutable observations, declared intents, episodes, evidence, learned policies, and human feedback — but do not persist ephemeral reasoning, retrieval results, or prompt compositions unless they become evidence in their own right. This keeps the database compact, replay-friendly, and durable, and it reinforces the central idea of the DLE: **the enduring asset is accumulated evidence and learned knowledge — not the transient thoughts that happened to produce them.**

### **18.9 Version Access and Restoration**

Because `manuscript_versions` preserves the complete manuscript at every revision (§18.4), the implementation should expose that recoverability to the author as first-class operations:

- **List** collected revisions with timestamps and checksums (`history`).  
- **View** the content of any past revision, whole or per file (`history show <version> [file]`).  
- **Restore** a past revision (or one file from it) to the working manuscript (`history restore <version> [file]`).

Two disciplines govern restoration. First, **restoration never rewinds history**: restoring writes the old content to disk, and the next collection records it as a *new* revision — the factual record only advances (Common Core §6.5). Second, restoration is a **mechanical edit** in the sense of §16.3 (Level 3): safe to execute directly, since it introduces no content the author has not already written.

These operations are deliberately independent of any external version control. The author may also keep the manuscript in Git, but the DLE's own history must suffice: the observation store is the system of record for replay, and it should be equally usable as the author's safety net.

---

## **Chapter 19 — Editorial Learning Pipeline**

### **19.1 Introduction**

Unlike bug management, writing does not naturally occur on a fixed schedule. AuthorLM learns **whenever the manuscript evolves**. The editorial pipeline therefore operates **over revisions rather than time**. Every meaningful revision provides an opportunity to improve editorial understanding.

### **19.2 Pipeline Overview**

flowchart TD

    S0\[Session-Opening Learning Briefing\] \--\> S1\[Collect Revision\]

    S1 \--\> S2\[Detect Changes\]

    S2 \--\> S3\[Construct Episode\]

    S3 \--\> S4\[Retrieve Memory\]

    S4 \--\> S5\[Resolve Intent\]

    S5 \--\> S6\[Update Editorial Knowledge\]

    S6 \--\> S7\[Generate Guidance\]

    S7 \--\> S8\[Author Review\]

    S8 \--\> S9\[Observe Next Revision\]

    S9 \--\> S0

Each revision becomes another editorial episode.

### **19.3 Stage 0 — Session-Opening Learning Briefing**

When an authoring session begins, AuthorLM opens with a **learning briefing** (§20.3): editorial policies strengthened or weakened since the last session, newly inferred intents, Concept Graph changes, contradictions between recent revisions and established policies, outstanding questions, and suggested focus areas. The briefing turns the pipeline's accumulated learning into the starting point of the session rather than an invisible side effect — and, because it is interactive, the author's answers to its questions become fresh declared evidence before the first revision is even collected. Its suggested focus areas frequently shape the declared intent that drives the remainder of the pipeline.

### **19.4 Stage 1 — Collect Revision**

Capture the latest manuscript revision from version control, local snapshots, or manual checkpoints. Every revision becomes immutable.

### **19.5 Stage 2 — Detect Changes**

Compute editorial transitions: rewritten paragraphs, reordered chapters, inserted concepts, removed arguments. **The DLE observes edits rather than attempting to infer them later.**

### **19.6 Stage 3 — Construct Episode**

Group related revisions into a coherent editorial episode. An episode often corresponds to one declared objective, e.g. "Prepare for the gravity chapter."

### **19.7 Stage 4 — Retrieve Memory**

Retrieve: similar editorial episodes, concept relationships, editorial policies, historical explanations, active writing objectives. Retrieval should focus on the **current editorial task** rather than the entire manuscript.

### **19.8 Stage 5 — Resolve Intent**

Resolve both declared and inferred intent. **Where declared intent exists, it remains authoritative.**

### **19.9 Stage 6 — Update Editorial Knowledge**

Update: concept graph, editorial policies, recurring strategies, belief records, unresolved questions. The objective is to **improve editorial judgment rather than merely record revisions**.

### **19.10 Stage 7 — Generate Guidance**

Generate collaborative editorial guidance: bridge suggestions, concept ordering, missing prerequisites, unanswered objections, possible next topics. **Guidance should explain the reasoning behind every recommendation.** When evidence is insufficient or conflicting, the correct guidance may be none at all — abstention (§11.5) preserves author trust better than an unfounded suggestion.

### **19.11 Stage 8 — Author Review**

The author evaluates every suggestion: accept, reject, rewrite, or explain. **The explanation is often more valuable than the decision itself because it exposes editorial reasoning.**

### **19.12 Stage 9 — Observe the Next Revision**

Future revisions reveal whether suggestions were adopted, new editorial strategies emerged, or writing philosophy evolved. Every revision becomes another source of editorial evidence.

### **19.13 Continuous Growth**

The editorial pipeline **never attempts to "finish" learning**. As the author's philosophy evolves, AuthorLM evolves alongside it. Its objective is to become a progressively better editorial collaborator rather than converging upon a fixed writing style.

### **19.14 Intent-Driven vs Event-Driven (design note)**

This is one of the biggest practical differences between the two systems:

- The **BTA** is fundamentally **event-driven by the external world**: bugs change → learn → generate playbook. Its pipeline should begin with **new observations**.  
- **AuthorLM** is fundamentally **intent-driven by the author**: author declares or reveals intent → write → revise → learn. Its pipeline should begin with **the active writing objective**. The manuscript revisions are evidence of how that objective was pursued, not the objective itself.

This matches the author's natural writing process ("Let's strengthen this section," "Next introduce trajectories," "Let's answer this objection"). Declared intents should drive retrieval, reasoning, and guidance generation for the session. AuthorLM is substantially more useful when organized around **active author intent** rather than simply reacting to document diffs — a genuine specialization of the Common Core, not just a different application.

---

## **Chapter 20 — Interactive Authoring**

### **20.1 Introduction**

AuthorLM is fundamentally a **collaborative system**. Unlike the BTA, there is **no independent execution engine** — the author remains the primary decision maker throughout the writing process. The role of AuthorLM is to provide timely, context-aware editorial guidance while continuously learning from the author's responses.

### **20.2 The Authoring Session**

The primary unit of interaction is an **authoring session**. A session typically begins with an objective: introduce a concept, strengthen an argument, answer an objection, improve readability, plan the next chapter. The session ends when the author considers the objective complete or shifts to a new objective.

### **20.3 The Session-Opening Learning Briefing**

Every authoring session opens with a **learning briefing** — the AuthorLM analogue of a research assistant reporting what it discovered while the author was away. Before any new guidance is generated, AuthorLM summarizes what it has learned since the last session:

- **editorial policies strengthened or weakened** by recent revisions,  
- **newly inferred intents** awaiting confirmation,  
- **Concept Graph changes** — new nodes, new relationships, weakened links,  
- **contradictions** between recent revisions and established policies,  
- **outstanding questions**, and  
- **suggested focus areas** for the current session.

For example:

"Since the last session: the *intuition-before-formalism* policy strengthened (two more supporting episodes). The Concept Graph gained a *Trajectory → History* dependency. However, two recent revisions contradict the intuition-before-formalism policy — has your approach changed? Outstanding question: the definition of *nothingness* in Chapter 3 no longer matches its use in Chapter 7."

The briefing is **interactive rather than a static report** — a Knowledge Review Session in miniature. AuthorLM may ask questions and propose experiments: "Should I treat Part I and Part II as separate style regimes?", "Two declared intents appear to conflict — which takes precedence?", "A feature I am not tracking seems to influence your ordering decisions — should I start collecting it?" The author's answers become high-quality declared evidence.

**The briefing is the primary interface between the engine and the author.** It is what turns AuthorLM from a suggestion generator into a research collaborator: the engine does not merely report *what* it learned, but what it is *uncertain* about, what *surprised* it, and what it believes should be *investigated next*. It also keeps the system's learning transparent — the author always knows what the engine currently believes and why, and no strengthening or weakening of editorial knowledge happens silently.

### **20.4 Declaring Intent**

Whenever practical, the session should begin with an explicit statement of intent: "Introduce trajectories.", "Explain nothingness.", "Prepare for gravity." **Declared intent guides retrieval, reasoning, and recommendation generation throughout the session.**

### **20.5 Guidance Generation**

AuthorLM may generate several kinds of guidance: structural suggestions, conceptual gaps, bridge paragraphs, supporting examples, anticipated objections, next-topic recommendations. The objective is to **improve editorial judgment rather than merely produce text**.

### **20.6 Review — Recommendation States**

Every recommendation enters one of several states:

- **Accepted**  
- **Rejected**  
- **Modified**  
- **Deferred**  
- **Superseded**

Each outcome contributes evidence. **Modified recommendations are particularly valuable because they reveal how the author's thinking differs from the initial proposal.**

### **20.7 Explanations**

The author should always be able to ask: Why was this suggested? Which prior revisions inspired it? Which editorial policy does it follow? Which declared intent does it support?

**The ability to explain recommendations is more important than the recommendations themselves.**

### **20.8 Long-Running Sessions**

Many writing objectives span days or weeks. AuthorLM should preserve session continuity by remembering: active intents, unresolved questions, partially completed revisions, planned future topics. The author should be able to resume work **without reconstructing prior context**.

### **20.9 Collaborative Evolution**

As the manuscript evolves, AuthorLM should gradually shift from suggesting isolated edits to assisting with **higher-level editorial planning**: chapter sequencing, concept introduction, thematic consistency, narrative pacing. This transition reflects the accumulation of editorial knowledge.

### **20.10 Session Replay**

Every authoring session should be replayable. Replay answers: Why was this section reorganized? When did this concept first appear? Which declared intent motivated this chapter? Which suggestions consistently helped? Replay also supports experimentation with improved reasoning strategies.

### **20.11 Summary**

Interactive Authoring defines the collaborative relationship between the author and AuthorLM. By organizing work around **declared objectives, explainable guidance, and continual learning**, the architecture treats writing as an **evolving partnership** rather than a sequence of isolated editing operations.

### **20.12 Execution Is Domain-Specific (design note)**

The two specializations diverge in exactly the right way. The BTA is increasingly about **execution** (safely converting learned knowledge into actions through the Markdown playbook); AuthorLM is increasingly about **collaboration** (a long-lived, intent-driven dialogue with the author). This highlights a key Common Core insight: **the execution engine is domain-specific**. For BTA, the execution engine is another agent (the Bug Triage Agent). For AuthorLM, **the execution engine is the human author**. Everything else — learning architecture, memory, episodes, intent, evidence, beliefs, replay — remains the same.

---

## **Chapter 21 — The Concept Graph**

### **21.1 Introduction**

The Concept Graph is the **central knowledge structure of AuthorLM**. Where the manuscript records *what has been written*, the Concept Graph records *how ideas relate to one another*. It captures the conceptual structure underlying the work rather than its textual presentation. As the manuscript evolves, the Concept Graph evolves alongside it.

### **21.2 Purpose**

The Concept Graph exists to answer questions such as:

- Which concepts must be understood first?  
- Which ideas depend upon one another?  
- Which objections remain unanswered?  
- Which concepts have not yet been introduced?  
- Which future topics are already being prepared?

The graph allows AuthorLM to **reason about ideas rather than paragraphs**.

### **21.3 Nodes**

Node kinds include: concepts, objections, examples, metaphors, mathematical constructs, historical references, open questions, and syllogisms. A concept's definition is stored in its notes.

**A node represents a unit of thought rather than a unit of text.**

### **21.4 Relationships**

Relationships describe how ideas interact: depends on, motivates, contrasts with, elaborates, generalizes, specializes, answers, foreshadows, illustrates.

The graph should remain **semantically rich** rather than reducing everything to simple references.

Illustrative example (from the author's metaphysical framework):

flowchart LR

    Choice \-- distinguishes \--\> Distinction

    Distinction \-- creates \--\> Field

    Field \-- permits \--\> Trajectory

    Trajectory \-- defines \--\> History

### **21.5 Evolution**

The Concept Graph is expected to evolve. Relationships may **strengthen, weaken, split, or disappear** as the author's thinking develops. Earlier conceptual structures should remain **historically accessible through replay**.

### **21.6 Declared Concepts**

The author may explicitly introduce future concepts: "Next I want to introduce trajectories.", "Later we will discuss gravity." These declarations become authoritative observations. **The graph should preserve them even before the corresponding manuscript text exists.**

### **21.7 Inferred Relationships**

AuthorLM may infer relationships the author has not explicitly stated — e.g., that one concept consistently precedes another, or that two ideas are frequently explained together. **Inferred relationships remain hypotheses until reinforced by repeated editorial evidence.**

### **21.8 Graph-Guided Retrieval**

The Concept Graph becomes a **primary retrieval mechanism**. Rather than retrieving text solely through semantic similarity, AuthorLM can retrieve **conceptually adjacent** material. For example, while working on "trajectories," the graph may suggest revisiting "fields," "histories," or "configuration space" because of their explicit relationships. This makes retrieval **intentional rather than purely lexical**.

### **21.9 Beyond a Single Book**

The Concept Graph is not limited to one manuscript. Concepts may span multiple books, essays, or projects. Over time, the graph becomes a representation of the **author's evolving philosophical framework** rather than any single document.

### **21.10 Summary**

The Concept Graph enables AuthorLM to collaborate **at the level of ideas instead of text**. By preserving conceptual structure independently of the manuscript, the DLE can assist with planning, sequencing, explanation, and long-term intellectual development.

### **21.11 The Enduring Asset (design note)**

There is an instructive symmetry between the specializations:

- For the **BTA**, the enduring asset is the **Replay Corpus** — as bug episodes accumulate, the organization gains a richer testbed for evaluating future learners. It answers: *"Would this new learner have made better decisions?"*  
- For **AuthorLM**, the enduring asset is the **Concept Graph** — as manuscripts and revisions accumulate, the author gains a richer representation of their intellectual landscape. It answers: *"How do these ideas fit together, and what should come next?"*

One is a memory of **experience**; the other is a memory of **ideas**. Both are long-lived assets that grow more valuable over time, and they are the defining capabilities that distinguish the two specializations from a generic agent framework.

---

## **Chapter 22 — Long-Term Editorial Memory**

### **22.1 Introduction**

Writing a book is rarely an isolated activity. Ideas develop over months or years, crossing chapter boundaries, manuscript boundaries, and entirely different projects. AuthorLM maintains a **long-term editorial memory that extends beyond any single document**. Its purpose is to preserve the **evolution of the author's thinking** rather than merely the history of individual edits.

### **22.2 Beyond the Manuscript**

The manuscript is only one expression of the author's work. Long-term memory should also preserve: recurring concepts, unfinished ideas, recurring objections, future projects, abandoned approaches, historical definitions. This allows AuthorLM to assist with **intellectual continuity across projects**.

### **22.3 Persistent Editorial Intent**

Some editorial intents remain active for long periods: "Develop a unified metaphysical framework.", "Clarify the relationship between choice and history.", "Strengthen the mathematical foundations." These objectives may span multiple books. AuthorLM should preserve them **independently of any individual manuscript**.

### **22.4 Recurring Questions**

Many authors revisit the same questions repeatedly. Long-term memory should record: unresolved questions, partially answered questions, recurring criticisms, recurring themes. These become valuable retrieval targets during future writing sessions.

### **22.5 Evolving Definitions**

Definitions mature over time. AuthorLM should preserve the **history of important concepts**, allowing the author to see how a definition has evolved across revisions and projects. This is particularly valuable in philosophical writing, where conceptual precision develops gradually.

### **22.6 Editorial Style**

Long-term memory should distinguish between: enduring editorial preferences, temporary experiments, project-specific conventions. This prevents short-lived stylistic changes from being mistaken for permanent editorial philosophy.

### **22.7 Cross-Project Retrieval**

When beginning a new manuscript, AuthorLM should retrieve relevant knowledge from prior work: related concepts, previous explanations, earlier objections, useful analogies, mathematical developments. **The objective is not to repeat earlier work but to build upon it.**

### **22.8 Remembering Future Work**

Authors frequently record ideas intended for later exploration: "Expand this into a chapter.", "Connect this with gravity.", "Return to this objection." These notes represent **future intent rather than current content**. AuthorLM should preserve and retrieve them at appropriate times.

### **22.9 Intellectual Evolution**

One of AuthorLM's responsibilities is helping the author understand the **evolution of their own thinking**: concepts that became central, ideas that were abandoned, definitions that stabilized, recurring intellectual themes. This historical perspective is difficult to reconstruct manually but **emerges naturally from accumulated editorial evidence**.

### **22.10 Summary**

Long-term editorial memory transforms AuthorLM from a manuscript assistant into a **long-term intellectual partner**, enabling collaboration at the scale of an author's **body of work** rather than a single document.

### **22.11 The Idea as the Highest-Value Object (design note)**

The BTA primarily learns **organizational practice**; human review is valuable because it reflects the organization's current operational judgment. AuthorLM learns **an individual's evolving intellectual practice**; long-term memory is valuable because it preserves continuity across years of thinking. Implementation consequence:

- In the BTA, the highest-value persistent object is often the **review**.  
- In AuthorLM, the highest-value persistent object is often the **idea**.

Everything else — episodes, policies, explanations, retrieval — exists to support those two enduring assets. Recognizing this distinction keeps each implementation focused on what it is fundamentally trying to preserve.

---

## **Chapter 23 — Prototype Implementation**

### **23.1 Philosophy**

The prototype should **help the author write better today**. It should not attempt to become a complete writing platform. Every feature should strengthen the editorial feedback loop. If a capability does not improve collaboration or learning, it should probably wait for a later version.

A practical test inherited from Version 1 applies to every implementation decision. Ask four questions: **Does this improve evidence? Does this improve explainability? Does this improve replay? Does this improve understanding of the author?** If all four answers are no, the feature should probably not be built.

### **23.2 Technology Stack**

- **Language:** Python 3  
- **Database:** SQLite  
- **Reasoning:** Local LLM using an OpenAI-compatible SDK  
- **Storage:** Markdown, Git, JSON configuration

No specialized infrastructure is required.

### **23.3 Suggested Directory Layout**

authorlm/

├── revisions/

├── episodes/

├── concepts/

├── context/

├── intent/

├── learning/

├── guidance/

├── replay/

├── storage/

├── prompts/

├── manuscripts/

├── config/

├── logs/

├── concept\_graph.db

└── main.py

The organization mirrors the conceptual architecture rather than any particular editor.

### **23.4 Editorial Session**

A typical session:

1. Receive the session-opening learning briefing (§20.3) and answer its questions.  
2. Declare the current objective.  
3. Load the current manuscript.  
4. Retrieve relevant memory.  
5. Generate editorial guidance.  
6. Revise the manuscript.  
7. Record accepted and modified suggestions.  
8. Update editorial knowledge.

The workflow should feel natural to the author.

### **23.5 Configuration**

Examples: manuscript location, replay corpus, retrieval limits, concept graph settings, guidance verbosity, explanation depth. Editorial preferences should remain configurable.

### **23.6 Logging**

Logs should summarize the editorial process, e.g.:

Collected 7 manuscript revisions.

Detected 52 editorial transitions.

Constructed 3 editorial episodes.

Updated 11 concept relationships.

Generated 8 editorial suggestions.

The emphasis is on understanding the evolution of the manuscript.

### **23.7 Growing the Prototype**

Suggested implementation order:

1. Revision collection  
2. Transition detection  
3. Episode construction  
4. Declared intent capture  
5. Concept Graph  
6. Editorial guidance  
7. Replay  
8. Long-term memory

**Every stage should remain independently useful.**

### **23.8 Success Criteria**

The prototype succeeds if:

- it remembers previous editorial decisions,  
- suggestions improve over time,  
- explanations become increasingly relevant,  
- the author spends less effort reconstructing previous thinking.

The ultimate objective is to **preserve and strengthen the author's editorial judgment**.

### **23.9 Single-Orchestrator Design for Version 1 (design note)**

A deliberate revision of an earlier idea: although many independent agents were originally proposed, **Version 1 should not be multi-agent**. It should be a **single orchestrator** that invokes well-defined capabilities in sequence:

collect() → build\_transitions() → build\_episodes() → retrieve() → infer\_intent() → learn() → generate\_guidance()

Each capability can internally use an LLM or other tools, but overall execution remains **linear and easy to debug**. Only after the system is stable should capabilities be distributed across multiple agents or services. A further advantage: **every stage produces an inspectable artifact** (snapshots, transitions, episodes, intents, policies, playbooks), which makes debugging dramatically easier and aligns with the DLE philosophy of preserving evidence at every step. A simple orchestrator with **pure functions between stages** is the right architecture for Version 1; multi-agent coordination can come later if there is a demonstrated need, not as an upfront design decision.

---

## **Chapter 24 — Validation and Evolution**

### **24.1 Introduction**

AuthorLM should be evaluated by the **quality of its collaboration with the author rather than the quantity of text it produces**. Validation determines whether the system becomes a progressively more valuable editorial partner.

### **24.2 What Should Improve?**

Over time, AuthorLM should demonstrate improvement in: editorial guidance, concept retrieval, explanation quality, understanding of the author's intent, preservation of long-term context. The objective is to **deepen collaboration rather than increase automation**.

### **24.3 Editorial Metrics**

Useful indicators: accepted suggestions, modified suggestions, rejected suggestions, retrieval usefulness, concept graph growth, reuse of editorial knowledge. The emphasis remains on **learning rather than productivity**.

### **24.4 Learning Velocity**

The most important metric measures the *learning* system rather than the author's output: **learning velocity** — how much editorial understanding increased in a session. The question is not "How many suggestions were generated?" but "**How much better does AuthorLM understand the author than it did last session?**" Indicators:

- new editorial evidence captured,  
- policies strengthened (or correctly weakened),  
- contradictions resolved,  
- uncertainty reduced — outstanding questions answered,  
- Concept Graph enrichment — new nodes and relationships confirmed,  
- improved explanations.

Learning velocity is deliberately distinct from productivity metrics such as words written or suggestions accepted. A session in which the author rejects every suggestion but explains *why* may exhibit very high learning velocity. The session-opening briefing (§20.3) is, in effect, learning velocity made visible to the author.

### **24.5 Replay**

Replay allows the author to evaluate improved editorial reasoning against historical writing sessions: improved topic planning, bridge suggestions, objection handling, concept sequencing. **Replay provides a safe environment for experimentation.**

That experimentation should be disciplined. **Every change to AuthorLM itself — retrieval strategies, prompts, learners, evidence weighting — is a hypothesis, not an upgrade.** Before deployment, each change is evaluated as an experiment against historical writing sessions: would the modified engine have produced better guidance, better explanations, fewer contradictions? Experiments should isolate **one variable at a time** — changing retrieval, prompts, and policy synthesis in a single deployment makes the results uninterpretable. And **failed experiments are preserved**: a prompt that performed worse, or a retriever that introduced noise, is durable knowledge about the system itself, not something to discard.

### **24.6 Prompt and Reasoning-Artifact Versioning**

The replay discipline quietly depends on a versioning discipline for the engine's reasoning artifacts. Prompts and similar reasoning artifacts are **versioned software artifacts**: each carries a stable identifier, a version, and declared input and output schemas; each revision is evaluated through replay before deployment — never because it "sounds better"; and **versions are never overwritten**, because replay must reproduce historical reasoning using the artifact version active at the time. This requires no heavyweight prompt catalog — only that the artifacts which produce editorial reasoning are treated with the same discipline as the knowledge they produce.

### **24.7 Incremental Evolution**

AuthorLM should evolve gradually: refining retrieval, improving concept relationships, improving editorial policies, expanding long-term memory. Each improvement should remain understandable to the author.

### **24.8 Author Trust**

The strongest indicator of success is whether the author **naturally begins relying upon AuthorLM during the writing process**. Trust grows when suggestions are: relevant, well explained, respectful of intent, intellectually useful.

### **24.9 Expanding Responsibility**

As AuthorLM develops deeper editorial understanding, its responsibilities may expand from local editing toward higher-level assistance: chapter planning, book organization, concept evolution, future project planning. **This progression should occur naturally through accumulated evidence.**

### **24.10 Long-Term Evolution**

The long-term objective is **not to automate writing**. It is to preserve and strengthen the author's **intellectual continuity across years of work**. The more accurately AuthorLM understands the evolution of the author's ideas, the more valuable it becomes.

### **24.11 Summary**

A successful AuthorLM implementation becomes an increasingly capable editorial collaborator whose understanding of the author's intellectual framework grows alongside the author's own work.

### **24.12 Closing by Philosophy, Not Future Work (design note)**

Rather than ending with "future work" sections — which tend to become vague, quickly outdated wish lists — each specialization RFC ends with a **statement of philosophy**. For the BTA: the system's greatest asset is not its automation but the operational knowledge it accumulates. For AuthorLM: the enduring asset is not generated prose but the **preservation and evolution of the author's conceptual framework**. These endings mirror the Common Core's emphasis on evidence, learning, and long-term knowledge rather than any particular implementation or AI model.

---

## **Closing Remarks**

AuthorLM applies the Decision Learning Engine to a different kind of artifact: **ideas**.

A manuscript is more than a collection of paragraphs. It is the **visible trace of an author's evolving understanding**. Every revision, deleted paragraph, reordered chapter, and rewritten definition reflects an attempt to express an idea more clearly than before.

The purpose of AuthorLM is not to write on the author's behalf. **Its purpose is to learn alongside the author.** By observing revisions, reconstructing editorial intent, preserving conceptual relationships, and remembering the evolution of ideas across years of work, AuthorLM becomes an increasingly capable **collaborator rather than a replacement**.

This distinction is essential. A writing assistant that merely generates text may save time, but an editorial partner that preserves the continuity of an author's thinking may preserve something far more valuable.

The enduring asset created by AuthorLM is therefore **not the manuscript itself**. It is the accumulated understanding of how the author's ideas evolve, how concepts connect, and how intellectual objectives are gradually realized through successive revisions. Over time, that accumulated understanding becomes a **companion to the author's body of work**, helping preserve not only *what* was written, but *how* and *why* it came to be written.

### **A Final Observation**

Having finished all three RFCs, the project has become something larger than a better bug triage agent. What emerged is a general architecture for **learning from the evolution of artifacts under expert guidance**.

That reframing explains why the same Common Core fits both bug management and philosophy writing so naturally. The artifacts are different. The intents are different. The execution engines are different. But the learning process — **observe, infer intent, accumulate evidence, form policies, evaluate outcomes, and continually refine understanding** — is fundamentally the same.

The strongest validation of the architecture is not that it *can* be generalized to many domains; it is that, once generalized, **each specialization still feels natural rather than forced**. That is usually a good sign that the abstraction has been chosen at the right level.

---

## **Appendix A — End-to-End Walkthrough: One Editorial Episode**

Rather than a tiny artificial example, this appendix follows **one complete editorial episode** — from the moment the author declares an intent until the resulting learning appears in the next session's briefing. Someone who understands this appendix should understand the entire AuthorLM specialization; ideally, someone should be able to build the system from this appendix alone.

flowchart TD

    DI\["Declared Intent: Introduce Trajectories"\] \--\> BR\[Session-Opening Learning Briefing\]

    BR \--\> RET\["Retrieval: Episodic Memory \+ Concept Graph"\]

    RET \--\> GG\["Guidance Generated, with Explanations"\]

    GG \--\> AR\["Author Review: accept / modify / reject \+ explain"\]

    AR \--\> RV\[Revisions Observed\]

    RV \--\> TR\[Editorial Transitions\]

    TR \--\> EP\[Episode Assembled\]

    EP \--\> EV\[Evidence\]

    EV \--\> PS\[Editorial Policy Strengthened\]

    PS \--\> CG\["Concept Graph Updated: Trajectory linked to Field"\]

    CG \--\> BL\[Belief Record Updated\]

    BL \--\> NB\["Next Session's Briefing Reflects the Learning"\]

**A.1 Session Zero.** Editorial Knowledge already contains a validated policy: *introduce intuition before formalism* — high confidence, moderate maturity, 23 supporting episodes, 2 contradictory. The Concept Graph contains `Field` (introduced in Chapter 4\) but no realized `Trajectory` node — although the author previously declared "Later we will discuss trajectories," so a declared placeholder exists (§21.6).

**A.2 The Author Declares Intent.** A new authoring session begins. The author states: "Introduce trajectories." This becomes an authoritative observation:

// declared\_intents

{

  "intent\_id": "di-2101",

  "manuscript\_id": "sermons-vol2",

  "statement": "Introduce trajectories.",

  "session\_id": "s-0147",

  "declared\_at": "2026-07-14T09:02:00Z",

  "status": "active"

}

**A.3 Session-Opening Briefing.** Before any guidance is generated, AuthorLM briefs the author (§20.3). Since the last session: the *intuition-before-formalism* policy strengthened (one more supporting episode); a new inferred intent — *contrast trajectories with geodesics* — awaits confirmation; and one contradiction is raised: "Two recent revisions in Chapter 6 placed formal definitions before the motivating example, contradicting the intuition-before-formalism policy — has your approach changed?" The author answers: "No — Chapter 6 is a deliberate exception; it revisits material already motivated in Chapter 4." The answer becomes declared evidence, and the engine proposes an experiment: "Should I treat *revisited concepts* as a separate context in which the policy does not apply?" The author agrees. The session's focus is confirmed: the trajectory introduction.

**A.4 Retrieval.** Working Memory is assembled. Episodic Memory retrieves the episode in which `Field` was introduced (historical example → definition → intuition → formal treatment). The Concept Graph reports that `Field` —permits→ `Trajectory` was declared but never realized in text, and that the future `History` concept will depend on `Trajectory`. Intent Memory retrieves how "introduce a concept" intents have typically been achieved. Retrieval is graph-guided rather than merely lexical (§21.8): "configuration space" is retrieved because of its explicit relationship to `Trajectory`, not because of textual similarity.

**A.5 Guidance Generated, with Explanations.** The Guidance Generator proposes three suggestions, each explaining itself:

1. **Insert a bridge paragraph** at the end of Chapter 5 connecting fields to motion. *Explanation: the previous three concept introductions began with a bridge from the preceding concept; serves the declared intent; follows the intuition-before-formalism policy.*  
2. **Open the new section with a historical example** (Maupertuis and least action) before defining trajectories. *Explanation: mirrors the episode that introduced `Field`; the historical-example-before-definition pattern has 17 supporting episodes.*  
3. **Add the formal variational definition** immediately after the informal one. *Offered with lower confidence: two competing patterns exist in prior episodes, and both are presented.*

**A.6 Author Review.** The author **accepts** suggestion 1 unchanged; **modifies** suggestion 2 — keeping the historical opening but replacing Maupertuis with the sailing metaphor already used in Chapter 2; and **rejects** suggestion 3 with an explanation: "Too formal too early — I want the contrast with geodesics before any variational machinery." The rejection explanation exposes previously hidden intent, confirming the inferred *contrast with geodesics* intent from the briefing.

**A.7 Revisions Observed.** The author writes. The Revision Collector captures the new manuscript version — immutable, checksummed, replayable.

**A.8 Transitions.** The Revision Analyzer computes editorial transitions:

// editorial\_transitions

{

  "transition\_id": "tr-88412",

  "version\_before": "v312",

  "version\_after": "v313",

  "kind": "insert",

  "location": "ch5/sec4/end",

  "summary": "Bridge paragraph: from fields to motion",

  "timestamp": "2026-07-14T10:31:12Z"

}

Additional transitions record the metaphorical opening and the new trajectory section itself. No policy is inferred yet — the engine has merely observed change.

**A.9 Episode Assembled.** The Episode Builder groups the declared intent, the briefing exchange, the guidance, the review outcomes, and the transitions into one editorial episode — *Introduce Trajectories* — spanning one session, three suggestions, and four transitions, with outcome: **concept successfully introduced**.

**A.10 Evidence.** The Evaluation Capability converts the review outcomes into evidence:

// evidence (one of several records)

{

  "evidence\_id": "ev-5521",

  "episode\_id": "ep-0147-trajectories",

  "evidence\_type": "author\_review",

  "signal": "accepted",

  "target": "bridge-paragraph suggestion",

  "supports\_policy": "pol-017",

  "weight": "high",

  "timestamp": "2026-07-14T10:32:40Z"

}

The rejection of suggestion 3 — with its explanation — becomes the highest-quality evidence of the session. Note the subtlety: **the author did not edit any policy; the author contributed evidence.**

**A.11 Editorial Policy Strengthened.** The Learning Engine finds the *intuition-before-formalism* policy already exists. Supporting episodes: 23 → 24; contradictory unchanged; confidence increases slightly. No new policy is created — **existing knowledge becomes stronger**. Simultaneously, a candidate pattern gains its first support: *contrast a new concept with its neighbor before formalizing it*.

// editorial\_policies \+ belief record

{

  "policy\_id": "pol-017",

  "statement": "Introduce intuition before formalism.",

  "status": "validated",

  "belief": {

    "confidence": 0.91,

    "maturity": "established",

    "supporting\_episodes": 24,

    "contradicting\_episodes": 2,

    "outstanding\_questions": \[

      "Does the policy apply to revisited concepts? (experiment agreed in session s-0147)"

    \]

  }

}

**A.12 Concept Graph Updated.** The declared placeholder becomes a realized node, and the edge is confirmed:

// concept\_graph

{

  "edge\_id": "cg-0932",

  "from": "Field",

  "relation": "permits",

  "to": "Trajectory",

  "status": "realized",

  "introduced\_in": "ch5/sec5",

  "evidence": \["ep-0147-trajectories"\]

}

A new inferred edge — `Trajectory` —contrasts with→ `Geodesic` — is recorded as a hypothesis, pending reinforcement by future episodes (§21.7).

**A.13 Belief Record Updated.** Confidence in *intuition-before-formalism* rises; the new outstanding question (revisited concepts) is recorded; the *contrast with geodesics* intent is promoted from inferred to author-confirmed.

**A.14 The Next Session's Briefing.** When the author returns, the briefing reflects the learning: "The *intuition-before-formalism* policy strengthened (24 supporting episodes). `Trajectory` is now realized in the Concept Graph and linked to `Field`; `History` remains unrealized and depends on it — a candidate focus area. New hypothesis: you prefer contrasting a new concept with its neighbor before formalizing it (one supporting episode so far). Open experiment: revisited concepts as a separate policy context." The loop is closed: the author sees exactly what the system learned from the previous session.

**A.15 What Actually Changed?** During the entire walkthrough, only five things permanently entered editorial knowledge: the **observations** (manuscript versions and their transitions), the **declared intent** (with its confirming answers from the briefing), the **evidence**, the **author's feedback** (acceptances, modifications, and the explained rejection), and the **outcomes**. Everything else — retrieved context, generated suggestions, prompt compositions, intermediate reasoning — could be regenerated from those durable records.

**The enduring asset is accumulated evidence and learned knowledge — not the transient reasoning that happened to produce them.**

**A.16 Lessons.** Declared intent drives the session. The briefing makes learning visible and interactive. Retrieval reasons over ideas, not just text. Guidance explains itself. The author's review — especially the explained rejection — contributes evidence rather than editing policy. The episode, the policy, the belief record, and the Concept Graph all evolve from the same durable observations. And the next briefing closes the loop. Learning never terminates.

---

## **Concept Inventory**

Exhaustive flat list of distinct named concepts, ideas, principles, and artifacts in the AuthorLM portion of Version 2, updated for Version 2.1 (reinstated concepts marked):

- AuthorLM as a specialization of the Common Core Decision Learning Engine (DLE) for philosophy writing  
- The manuscript as the artifact; manuscript observable state (chapters, sections, paragraphs, examples, diagrams, references, concept graph, narrative flow)  
- Nested artifact hierarchy: Book → Chapter → Section → Paragraph → Sentence → Concept, each level versioned and evolving independently  
- Writing intent as the driver of editorial decisions (introduce concept, clarify argument, address objection, improve flow, increase rigor, prepare formalization, strengthen intuition, historical context)  
- Operational objective: learn the author's editorial decision-making process, not imitate prose  
- Editorial ontology (artifact, context, intent, decision, policy specialized for writing)  
- Editorial decisions vocabulary (rewrite, insert, delete, reorder, merge, split, annotate, illustrate, define, summarize, expand, cross-reference)  
- Editorial policies as reusable writing behavior (e.g., intuition before formalism; define before abstraction; objections before conclusions; bridge gaps with historical examples; end sections with synthesis)  
- Policies are discovered from repeated revisions, not authored manually  
- Intent as one of the strongest predictors of editorial decisions  
- Every revision becomes replayable / immutable observation of revisions  
- Editorial context extends beyond the local edit (surrounding chapters, concept graph, unresolved objections, narrative position, audience assumptions, historical revisions); context more important than the local edit  
- Declared intent vs inferred intent; declared intent is authoritative and takes precedence  
- Decision extraction linked to motivating intent  
- Policy learning example: Intent (Introduce New Concept) \+ Context (concept undefined) → Historical example → Definition → Intuition → Formal treatment  
- Execution guidance is collaborative rather than autonomous (unlike BTA)  
- The author remains the final decision maker  
- Editorial Knowledge Graph (manuscripts, chapters, concepts, decisions, intents, policies, relationships, author feedback)  
- The Concept Graph as the central domain-specific knowledge structure (memory of ideas, not text)  
- Concept Graph example chain: Choice —distinguishes→ Distinction —creates→ Field —permits→ Trajectory —defines→ History  
- Concept Graph uses: prerequisite detection, topic sequencing, conceptual consistency, future chapter planning  
- Concept Graph node types (concepts, objections, examples, metaphors, mathematical constructs, historical references, open questions, syllogisms); definitions are concept notes; node \= unit of thought, not unit of text
- Concept Graph relationship types (depends on, motivates, contrasts with, elaborates, generalizes, specializes, answers, foreshadows, illustrates; also prerequisite, depends\_on, references in the data model)  
- Concept Graph evolution (relationships strengthen, weaken, split, disappear; historical structures remain accessible through replay)  
- Declared concepts — future concepts preserved in the graph before manuscript text exists  
- Inferred relationships remain hypotheses until reinforced by repeated editorial evidence  
- Graph-guided retrieval — retrieval of conceptually adjacent material; intentional rather than purely lexical/semantic-similarity retrieval  
- Concept Graph beyond a single book — spanning books, essays, projects; representation of the author's evolving philosophical framework  
- Explanations required for every recommendation (which revisions inspired it, which intent it serves, which policies support it, which alternatives remain plausible); explaining is more important than the recommendation itself  
- Manuscript replay (when was a concept introduced, why was a section reorganized, which policy motivated a revision, how the philosophy evolved)  
- Editorial inference: intent inference, concept inference, structural inference (missing bridges, redundancy, reordering, chapter boundaries, conceptual clustering), narrative prediction (next topics, future objections, supporting examples, missing definitions)  
- Structural inferences remain hypotheses until author review; predictions must not constrain creativity  
- Editorial knowledge evolution: promotion ladder (Repeated Revision → Editorial Pattern → Candidate Editorial Policy → Validated Editorial Policy → Reusable Writing Guidance)  
- Demotion — policies weaken when newer revisions consistently contradict them (recognizing evolved philosophy)  
- Style drift (expected; distinguish intentional evolution from isolated exceptions)  
- Conceptual drift (distinct from stylistic drift; tracking it is a primary AuthorLM responsibility)  
- Editorial intent lifecycle: declared intent as authoritative observation; intent refinement chains (Improve Clarity → Prepare Reader → Introduce Trajectories → Contrast with Geodesics → Lead into Gravity); multiple simultaneous intents modeled interactively; editorial completion judged by the author; new intent kinds treated as learning opportunities  
- Editorial episodes — learning from episodes rather than isolated edits; episodes capture the complete strategy for a conceptual objective  
- Typical episode example: Declared Intent (Introduce Trajectories) → Insert Bridge Paragraph → Rewrite Earlier Definition → Move Historical Example → Add Diagram → outcome  
- Editorial outcomes as evidence (concept introduced, objection resolved, narrative strengthened, chapter reorganized, future discussion prepared)  
- Editorial beliefs (about policies, ordering, structure, style, pedagogy, dependencies) with confidence and maturity  
- Confidence dynamics (rises with repeated similar decisions across independent episodes; falls with consistent divergence)  
- Maturity — distinguishing temporary writing experiments from durable editorial philosophy  
- Editorial uncertainty handling — present alternatives rather than recommendations (collaboration over imitation)  
- Editorial learning loops: Editorial Loop (Draft → Episode → Policies → Guidance → Review → Revised Draft), Intent Loop, Concept Loop (Concept Graph → Decisions → Updated Graph → Improved Suggestions), Editorial Review Loop (accept/reject/rewrite/explain), AuthorLM Improvement Loop (replay-based)  
- Explanatory feedback exposes previously hidden intent (most valuable review signal)  
- Editorial memory: Episodic Memory (editorial history), Semantic Memory (accumulated editorial philosophy), Working Memory (focused on current objective), Intent Memory (recurring objectives and how they're achieved), and domain-specific Concept Memory (relationships among ideas rather than among revisions)  
- Editorial reasoning inputs, questions, suggestions, and explanations; AuthorLM as editorial collaborator, not autonomous author  
- Editorial interfaces (Observation, Context, Reasoning, Execution, Evaluation) with collaborative editing as execution; accepted revision becomes the next observed artifact version  
- Editorial execution levels 0–4 (observe; suggest; prepare complete revisions; auto-execute low-risk mechanical operations; limited autonomous drafting for narrowly scoped tasks under author review)  
- Core philosophical content remains under the author's direction  
- Execution Adapter — translates guidance into concrete manuscript modifications; isolates AuthorLM from document format/editor  
- Editorial safety taxonomy: mechanical vs stylistic vs conceptual edits, each at its own autonomy level; author's authority over meaning preserved  
- Outcome collection (accepted/rejected/rewritten suggestions, explanatory comments, subsequent revisions)  
- Reference runtime architecture: Manuscript Repository → Revision Collector → Revision Analyzer → Editorial Episode Builder → Context Builder → Intent Engine → Learning Engine → Editorial Knowledge → Guidance Generator → Author → Revised Manuscript → Next Revision Cycle  
- Revision Collector (Git history, snapshots, editor exports, manual saves; immutable revisions)  
- Revision Analyzer computing editorial transitions (raw observations for learning)  
- Editorial Episode Builder (episodes spanning revisions, one declared objective, multiple sessions)  
- Context Builder — reasoning begins only after context assembly  
- Intent Engine combining declared and inferred intent  
- Learning Engine — learns editorial judgment rather than writing style alone (policies, sequencing, explanation patterns, transition strategies, narrative structures)  
- Guidance Generator — proposes rather than executes; suggestions remain collaborative  
- Editorial loop / daily workflow (11 steps from session-opening briefing to learning)  
- Prototype philosophy: preserve author intent, explainability, rapid iteration, replay, collaborative editing; learning from evolving practice over generating sophisticated prose  
- RFC divergence insight: Common Core isolates shared theory; BTA → playbooks/execution, AuthorLM → concept graphs/editorial planning/interactive workflows  
- Reference data model in SQLite (simple, transparent, replay-friendly); tables: manuscripts, manuscript\_versions, editorial\_transitions, editorial\_episodes, declared\_intents, inferred\_intents, concept\_graph, editorial\_policies, editorial\_reviews, guidance\_history  
- Data-model design principles (preserve every revision and declared intent; preserve episodes; separate observations from learned knowledge; remain understandable without specialized tools)  
- Separation of declared and inferred intents simplifies replay and evaluation  
- editorial\_reviews as the highest-quality editorial evidence  
- guidance\_history enabling replay of the editorial collaboration  
- What is not stored: LLM conversations, embeddings, temporary prompts, intermediate reasoning — only durable editorial knowledge persists  
- Persistence principle: "Persist only what you cannot reliably reconstruct"  
- Version access and restoration — author-facing list/view/restore over the complete stored revisions (history / history show / history restore); restoration writes forward as a new revision (history never rewinds) and is a mechanical (Level 3) operation; the DLE's own history is the system of record independent of external version control  
- The enduring asset is accumulated evidence and learned knowledge, not the transient thoughts that produced them  
- Schema evolution without rewriting historical revisions; replay always possible from original observations  
- Database-as-history: reading the tables should feel like reading the history of the book, not the internal state of an AI system  
- Revision-driven (not time-driven) editorial learning pipeline; ten stages: Session-Opening Learning Briefing → Collect Revision → Detect Changes → Construct Episode → Retrieve Memory → Resolve Intent → Update Editorial Knowledge → Generate Guidance → Author Review → Observe Next Revision  
- Observing edits directly rather than inferring them later  
- Retrieval focused on the current editorial task, not the entire manuscript  
- Continuous growth — the pipeline never "finishes" learning; no convergence to a fixed style  
- BTA is event-driven by the external world; AuthorLM is intent-driven by the author  
- AuthorLM's pipeline begins with the active writing objective; revisions are evidence of pursuit of the objective, not the objective itself  
- Interactive Authoring — AuthorLM as fundamentally collaborative; no independent execution engine; the author IS the execution engine  
- The execution engine is domain-specific (BTA: another agent; AuthorLM: the human author); everything else in the Common Core stays the same  
- The authoring session as the primary unit of interaction (begins with an objective, ends on completion or objective shift)  
- Declaring intent at session start guides retrieval, reasoning, and recommendation generation  
- Recommendation states: Accepted, Rejected, Modified, Deferred, Superseded  
- Modified recommendations reveal how the author's thinking differs from the proposal (especially valuable evidence)  
- Long-running sessions — continuity via remembered active intents, unresolved questions, partial revisions, planned topics; resume without reconstructing context  
- Collaborative evolution — shift from isolated edits to higher-level editorial planning (chapter sequencing, concept introduction, thematic consistency, narrative pacing)  
- Session replay for authoring sessions  
- Writing as an evolving partnership rather than isolated editing operations  
- BTA is about execution; AuthorLM is about collaboration  
- Enduring-asset symmetry: BTA's Replay Corpus (memory of experience) vs AuthorLM's Concept Graph (memory of ideas)  
- Replay answers "Would this new learner have made better decisions?"; the Concept Graph answers "How do these ideas fit together, and what should come next?"  
- Long-Term Editorial Memory extending beyond any single document (preserving evolution of thinking, not just edit history)  
- Memory beyond the manuscript: recurring concepts, unfinished ideas, recurring objections, future projects, abandoned approaches, historical definitions  
- Persistent editorial intent spanning multiple books (e.g., unified metaphysical framework; choice–history relationship; mathematical foundations)  
- Recurring questions (unresolved, partially answered, recurring criticisms and themes) as retrieval targets  
- Evolving definitions — preserving the history of concept definitions across revisions and projects  
- Editorial style memory distinguishing enduring preferences, temporary experiments, project-specific conventions  
- Cross-project retrieval when beginning a new manuscript (build upon, not repeat, earlier work)  
- Remembering future work — notes as future intent rather than current content, retrieved at appropriate times  
- Intellectual evolution — showing the author how their thinking evolved (central concepts, abandoned ideas, stabilized definitions, recurring themes)  
- BTA learns organizational practice; AuthorLM learns an individual's evolving intellectual practice  
- Highest-value persistent object: the review (BTA) vs the idea (AuthorLM); everything else supports these enduring assets  
- Prototype implementation philosophy: help the author write better today; not a complete writing platform; every feature strengthens the editorial feedback loop  
- Technology stack: Python 3, SQLite, local LLM via OpenAI-compatible SDK, Markdown \+ Git \+ JSON; no specialized infrastructure  
- Suggested directory layout mirroring the conceptual architecture (revisions/, episodes/, concepts/, context/, intent/, learning/, guidance/, replay/, storage/, prompts/, manuscripts/, config/, logs/, concept\_graph.db, main.py)  
- Typical editorial session workflow (receive learning briefing → declare objective → load manuscript → retrieve memory → generate guidance → revise → record responses → update knowledge)  
- Configuration surface (manuscript location, replay corpus, retrieval limits, concept graph settings, guidance verbosity, explanation depth)  
- Narrative logging of the editorial process (revisions collected, transitions detected, episodes constructed, relationships updated, suggestions generated)  
- Incremental prototype growth order (revision collection → transitions → episodes → declared intent → Concept Graph → guidance → replay → long-term memory); every stage independently useful  
- Prototype success criteria (remembers decisions, suggestions improve, explanations increasingly relevant, less effort reconstructing prior thinking)  
- Single-orchestrator design for Version 1 (not multi-agent); linear capability sequence collect → build\_transitions → build\_episodes → retrieve → infer\_intent → learn → generate\_guidance  
- Pure functions between stages; every stage produces an inspectable artifact; multi-agent only upon demonstrated need  
- Validation by collaboration quality, not text quantity  
- What should improve: editorial guidance, concept retrieval, explanation quality, intent understanding, long-term context preservation; deepen collaboration rather than increase automation  
- Editorial metrics (accepted/modified/rejected suggestions, retrieval usefulness, concept graph growth, reuse of editorial knowledge); learning over productivity  
- Replay as a safe environment for experimentation against historical writing sessions  
- Incremental evolution — small, author-understandable improvements  
- Author trust as the strongest success indicator (relevant, well explained, respectful of intent, intellectually useful suggestions)  
- Expanding responsibility from local editing to chapter planning, book organization, concept evolution, future project planning — naturally through accumulated evidence  
- Long-term objective: preserve and strengthen the author's intellectual continuity across years of work, not automate writing  
- Ending RFCs with a statement of philosophy rather than "future work" wish lists  
- Closing philosophy: AuthorLM's artifact is ideas; the manuscript is the visible trace of evolving understanding; learn alongside the author; collaborator not replacement; the enduring asset is accumulated understanding of idea evolution, a companion to the author's body of work  
- Final reframing: a general architecture for learning from the evolution of artifacts under expert guidance; same learning process (observe, infer intent, accumulate evidence, form policies, evaluate outcomes, refine understanding) across domains  
- Abstraction-level validation: each specialization feels natural rather than forced once generalized  
- Session-Opening Learning Briefing — interactive session-start report of policies strengthened/weakened, newly inferred intents, Concept Graph changes, contradictions between recent revisions and established policies, outstanding questions, and suggested focus areas; the primary interface between the engine and the author, turning a suggestion generator into a research collaborator (reinstated from V1: Daily Knowledge Report / Knowledge Review Session)  
- Briefing interactivity — the engine asks questions and proposes experiments ("Should I treat Part I and Part II as separate style regimes?"); the author's answers become high-quality declared evidence (reinstated from V1)  
- Learning transparency — no strengthening or weakening of editorial knowledge happens silently; the author always knows what the engine currently believes and why (reinstated from V1)  
- Pipeline Stage 0 — the learning briefing as a standing stage of the editorial learning pipeline (reinstated from V1)  
- End-to-End Walkthrough (Appendix A) — one editorial episode traced through the complete lifecycle, with example object sketches for declared intent, transition, evidence, policy \+ belief record, and concept-graph edge (reinstated from V1)  
- Walkthrough closing observation — only observations, declared intent, evidence, author feedback, and outcomes permanently enter knowledge; everything else is regenerable (reinstated from V1)  
- KnowledgeObject base class (id, version, created\_at, created\_by, schema\_version, metadata) — uniform identity, versioning, provenance, and serialization for every canonical persistent object (reinstated from V1)  
- Learning velocity — how much editorial understanding increased per session (new evidence, strengthened policies, resolved contradictions, reduced uncertainty, Concept Graph enrichment, improved explanations); deliberately distinct from productivity metrics (reinstated from V1)  
- Abstention as a valid editorial output — when evidence is insufficient or conflicting, AuthorLM may abstain from suggesting entirely; abstention preserves author trust (reinstated from V1)  
- Replay-based experimentation discipline — every change to AuthorLM itself (retrieval, prompts, learners, weighting) is a hypothesis evaluated against historical writing sessions before deployment; one variable at a time; failed experiments preserved (reinstated from V1)  
- Four engineering-decision questions — does this improve evidence, explainability, replay, or understanding of the author; if all four answers are no, do not build it (reinstated from V1)  
- Prompt/reasoning-artifact versioning discipline — stable identifiers, versions, declared input/output schemas, replay evaluation before deployment, versions never overwritten (reinstated from V1)
