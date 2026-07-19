# **Decision Learning Engine — Common Core RFC (Version 2.1)**

**Editorial note.** This document is a clean synthesis of the Version 2 "Common Core RFC" draft. The original draft interleaved chapters with commentary sections containing architectural refinements. Those refinements have been applied in place, as the commentary itself directed. In particular: **Episode** has been promoted to a first-class concept and inserted into the ontology, the learning pipeline, the representation model, the Knowledge Graph, and the canonical-object catalog; **Declared vs. Inferred Intent**, the **Decision \= f(Context, Intent)** formulation, the **local/global/historical context layers**, the **Domain Semantic Graph extension point**, **retrospective vs. prospective inference**, the **Expert Evolution** principle, **descriptive vs. normative beliefs**, the **operational/learning/meta-learning loop separation**, the **memory → reasoning → knowledge relationship**, the **interpretive/generative/interrogative reasoning taxonomy**, and the **one-input/one-output capability discipline** are integrated into their proper chapters. No new concepts have been invented.

**Version 2.1.** This revision reinstates a set of Version 1 concepts that did not survive the Version 2 rewrite, following the V1-vs-V2 concept-loss analysis. Reinstated material is integrated into the existing chapter structure and marked "(reinstated from V1)" in the Concept Inventory. Everything present in Version 2 is preserved.

---

## **Change Log (v2.1)**

**Reinstated from Version 1:**

1. **Core Axioms** — a numbered, citable axiom block closes Chapter 1 (§1.12), adapted to Version 2 vocabulary.  
2. **The Marketplace of Learners** — multiple independent learners, the Hypothesis Aggregator, and learner agreement as a confidence signal (§14.6), connected to the Belief architecture (§11.5).  
3. **Chapter 17 — Experimentation** — every change to the engine is a hypothesis; two levels of learning; experiment records; replay before production; controlled operational experiments; one-variable discipline; failed experiments as knowledge; learning velocity.  
4. **Chapter 18 — Knowledge Governance** — expert designation, evidence-weight authority, execution approval, audit, rollback, conflicting organizational units, permanent expert disagreement.  
5. **Abstention** as a valid output (§16.4).  
6. **Forgetting / recency-weighted evidence** as the mechanism beneath drift (§8.8).  
7. **Configurable trust models** — evidence collection separated from evidence weighting (§11.6).  
8. **Single-writer discipline** for persistent knowledge (§15.8).

**Strengthened:**

9. The best Version 1 law phrasings merged into Appendix G ("Reasoning proposes; evidence disposes"; "Policies are hypotheses that have survived evidence").  
10. The compiler analogy added to Knowledge Evolution (§8.4).  
11. The four engineering-decision questions added to the interface-discipline discussion (§15.15).  
12. "False negatives are preferable to false positives" added to the promotion discussion (§8.5) and Execution Principles (§16.2).  
13. Reasoning-artifact versioning discipline added to the Self-Improvement Interface (§15.12).

Section renumbering: Chapters 8, 11, 14, 15, and 16 gained sections; existing sections were renumbered accordingly. No content was removed.

**v2.2 addendum (prototype feedback).** §1.12A records the working principles that emerged from the first working implementation (AuthorLM) — rules discovered in practice, not imagined in advance. The "not tied to Large Language Models" non-goal was reframed: the architecture assumes LLMs as the practical reasoning engine; what it guarantees is independence from any *particular* model, and token-efficient use rather than avoidance.

---

## **Design Goals**

The Decision Learning Engine (DLE) exists to capture and reuse expert decision-making.

Most organizations possess an enormous amount of expertise, but very little of it exists in documentation. Instead, expertise is expressed through the day-to-day evolution of the artifacts that experts manage. Every bug update, manuscript revision, code review, incident response, or design change represents knowledge being applied. The DLE is designed to learn from those observations rather than requiring experts to manually encode rules.

The architecture is guided by the following goals.

### **Learn from Evidence**

Knowledge should emerge from observed expert behavior. The system should become more capable because it has seen more evidence, not because increasingly complicated prompts have been written.

### **Separate Learning from Execution**

Learning should remain independent from operational execution. This allows the DLE to experiment, replay history, and refine its understanding without affecting production systems.

### **Preserve History**

Historical observations should never be modified. The DLE should improve its interpretation of history rather than rewriting history itself.

### **Explain Every Recommendation**

Every recommendation should be traceable back to the observations, episodes, intents, and policies that produced it. The DLE should never produce recommendations that cannot be explained.

### **Support Human Collaboration**

The DLE is intended to collaborate with experts rather than replace them. Human decisions remain authoritative observations. Human feedback remains one of the richest sources of evidence.

### **Generalize Across Domains**

The Common Core should remain independent of any particular application domain. New domains should require specialization rather than modification of the Common Core itself.

---

## **Non-Goals**

The DLE intentionally does **not** attempt to solve every problem. The following are outside the scope of the architecture.

### **It is not an autonomous agent.**

The DLE may eventually enable autonomous execution, but autonomy is not its purpose. The primary objective is learning.

### **It does not infer historical facts.**

Observations are authoritative. The DLE may infer explanations, but it never replaces observed history with inferred history.

### **It is not a replacement for human expertise.**

The DLE exists to accumulate organizational knowledge. Human experts remain the ultimate authority.

### **It does not search for universal rules.**

Different experts may legitimately behave differently. The DLE learns from evidence rather than assuming there is always a single correct answer.

### **It is not tied to Large Language Models.**

Reasoning engines may change over time. The architecture should survive improvements in AI technology.

### **It is not optimized for maximum automation.**

Automatic execution is simply one possible deployment strategy. Many domains benefit more from recommendations than from autonomous action.

---

## **Chapter 1 — Foundations**

### **1.1 Introduction**

Knowledge is created whenever an expert improves an artifact. An engineer improves software. An author improves a manuscript. A project manager improves a project plan. A teacher improves a lesson. A scientist improves a theory.

Although the artifacts differ, the process is fundamentally the same. The expert observes the current state of an artifact, forms an intent, performs one or more decisions, and produces a new version of the artifact. Most of this knowledge is never explicitly recorded. It remains embedded in the expert's decisions.

The purpose of the Decision Learning Engine is to observe those decisions, infer the intents that motivated them, accumulate evidence across many such observations, and continuously construct an explicit, explainable model of expert decision making.

The objective is not to replace expertise. The objective is to preserve, refine, and operationalize it.

### **1.2 Expert Knowledge**

Organizations often attempt to capture expertise by writing procedures, policies, or documentation. These artifacts are valuable, but they are incomplete. The richest knowledge exists in what experts actually do.

Every modification to an artifact represents an implicit answer to a question: *Why was this changed? Why now? Why in this way rather than another?* The DLE is designed to recover these implicit answers through observation rather than manual documentation.

### **1.3 Artifacts**

An artifact is any versioned object that evolves through expert decisions. Examples include software bugs, source code, design documents, books, research papers, incident reports, legal documents, product specifications, and educational material.

Every artifact possesses observable state, revision history, expert decisions, and improvement over time. The DLE is intentionally independent of the specific artifact.

### **1.4 Intent**

Every expert decision serves an intent. Intent represents the immediate objective an expert is attempting to accomplish — e.g., reduce customer impact, improve readability, introduce a new concept, resolve ambiguity, prepare for release, simplify implementation, clarify an argument.

Intent explains *why* a decision exists. Without intent, observed decisions are merely changes. With intent, they become understandable. Intent is therefore a first-class concept within the architecture.

### **1.5 Decisions**

A decision transforms one version of an artifact into another — changing ownership of a bug, rewriting a paragraph, refactoring a function, adding a diagram, splitting a chapter. The DLE learns from decisions rather than from finished artifacts alone.

### **1.6 Evidence**

Not every observed decision becomes organizational knowledge. The DLE distinguishes observation from evidence. Evidence consists of observed decisions together with sufficient context to justify learning from them.

Evidence may originate from designated experts, peer review, editorial review, operational outcomes, or explicit human feedback. Evidence is the sole source of persistent learning.

### **1.7 Knowledge**

Knowledge is not synonymous with data. Data records what happened. Knowledge explains what repeatedly proves useful.

Within the DLE, knowledge consists of hypotheses, policies, explanations, and belief records that have survived repeated evaluation against evidence. Knowledge therefore remains provisional rather than absolute.

### **1.8 Learning**

Learning is the continual refinement of organizational knowledge through accumulating evidence. The DLE becomes more capable not because its reasoning algorithms become more sophisticated, but because it continuously observes richer evidence.

**Models reason. Evidence teaches. Knowledge persists.**

### **1.9 Architecture**

The architecture intentionally separates five concerns:

- Observation  
- Reasoning  
- Knowledge  
- Execution  
- Evaluation

Each evolves independently. This separation allows new reasoning algorithms, execution engines, and storage systems to be introduced without invalidating accumulated knowledge.

### **1.10 Scope**

This document defines the conceptual architecture of the Decision Learning Engine. It intentionally avoids assumptions about any specific application domain. Subsequent RFCs specialize this architecture for particular domains while preserving the common conceptual foundation.

### **1.11 Guiding Principles**

1. Expertise is expressed through decisions.  
2. Decisions are made in pursuit of intent.  
3. Evidence, not reasoning, creates knowledge.  
4. Knowledge remains explainable.  
5. Learning is continuous.  
6. Execution is separate from learning.  
7. History is immutable; understanding evolves.

### **1.12 Core Axioms**

*(Reinstated from Version 1, adapted to the Version 2 vocabulary.)*

The guiding principles above are distilled into eight numbered axioms. The axioms are deliberately citable: later chapters reference them by number when justifying architectural decisions.

- **Axiom 1 — Expertise.** Organizational expertise is expressed through decisions made upon evolving artifacts.  
- **Axiom 2 — Observability.** Decisions become observable as transitions between successive artifact versions, organized into goal-directed episodes.  
- **Axiom 3 — Intent.** Decisions are made in pursuit of intent. Declared intent is an authoritative observation; inferred intent remains a hypothesis until supported by evidence.  
- **Axiom 4 — Evidence.** The engine does not become smarter by reasoning harder; it becomes smarter by observing more evidence.  
- **Axiom 5 — Knowledge.** Internal reasoning generates hypotheses; external evidence creates knowledge.  
- **Axiom 6 — Separation.** The DLE learns organizational policy; execution engines apply organizational policy; these responsibilities remain separate.  
- **Axiom 7 — Explainability.** Every learned policy, belief, and recommendation must be traceable to the evidence that justifies it.  
- **Axiom 8 — Continuity.** Learning is never complete; every new episode is an opportunity to refine the engine's understanding.

### **1.12A Working Principles from Implementation (v2.2 addendum)**

The axioms above were designed in advance. The principles below were *discovered* — each one earned during the first working implementation (AuthorLM), usually by observing the failure of the naive approach. They rank alongside the axioms in authority precisely because they survived contact with practice.

- **P1 — Scope by the diff; read in full.** An LLM decision is *scoped* by what the expert actually changed (only changed text may generate review work for the expert — this is what keeps model nondeterminism from taxing human attention), but the model is *fed* full local context: starved prompts judge mechanics instead of meaning. Culling bounds what may be concluded, never what may be read.
- **P2 — Every LLM call: a defined decision, a curated payload, a cache key.** Token efficiency is the goal; avoidance is not. Identical payloads replay from cache; only changed payloads spend. Wasteful invocation is the anti-goal — LLM use itself is assumed.
- **P3 — Machine output is quarantined to hypotheses.** No inference path may write to confirmed or expert-authored knowledge. The worst case of model error is wasted triage; it is never corruption.
- **P4 — Guards protect; proposals prevent fossilization.** Every hard guard (bans, settled confirmations) is paired with a proposal channel that surfaces conflicting fresh evidence for expert review. "Settled" means *the expert decides again*, not *never again*.
- **P5 — Honesty over volume.** Model output that does not fit the declared vocabulary is dropped and counted, never coerced into the nearest bucket. Coercion manufactures plausible-looking falsehoods that poison downstream reasoning.
- **P6 — Define the vocabulary in the prompt.** Any label the model must choose (a relation, a kind, a classification) gets its strict semantics stated at request time. Unstated semantics will be guessed, and guessed wrong.
- **P7 — Expert decisions are feedback fuel.** Every triage and review decision is recorded as evidence and re-enters future prompts as authoritative feedback — rejections, corrections, confirmed exemplars. The engine's judgment converges toward the expert's through ordinary use.
- **P8 — Degradation announces itself.** When the reasoning engine is unavailable, the system degrades to evidence-based heuristics with a visible notice — never a silent quality drop, and never a hard halt.
- **P9 — The deterministic layer pushes; the generative layer is pulled.** Cheap deterministic checks run on every observation and surface results immediately. Generative reasoning runs on request or at natural episode boundaries.
- **P10 — Display truncation is never data truncation.** Judgment tasks receive full data; scanning views receive previews. Stored knowledge is never shortened to fit a screen.
- **P11 — One API, thin surfaces.** Every interface (CLI, protocol servers, conversational agents) is a logic-free wrapper over one typed, tested API. Synchronization between surfaces is enforced by parity tests, not by code generation.
- **P12 — During prototyping, replace cleanly.** No compatibility shims, no legacy fallbacks; migrate live data as part of the change and delete the old path entirely.

### **1.13 Summary**

The Decision Learning Engine provides a general framework for learning expert decision making across evolving artifacts. Rather than programming expertise into software, the architecture continuously observes expert decisions, reconstructs the intents behind them, and transforms accumulated evidence into explicit organizational knowledge.

The Common Core never mentions bugs or books: it is about *expert-guided evolution of artifacts*. Specializations (such as a Bug Triage Agent or AuthorLM, and future ones like CodeLM, DesignLM, ResearchLM, or IncidentLM) need only define what those concepts mean for their domain — what the artifact is, what the intents are, and what successful execution looks like — without modifying the Common Core.

---

## **Chapter 2 — Ontology**

### **2.1 Introduction**

The Decision Learning Engine is founded upon a small set of domain-independent concepts. These concepts describe how expertise transforms evolving artifacts. The architecture intentionally minimizes the number of primitive concepts. Every specialized implementation — whether managing bugs, writing books, reviewing source code, or designing curricula — uses the same ontology.

### **2.2 Artifact**

An artifact is any persistent object that evolves through expert decisions. An artifact has three essential properties:

- It possesses observable state.  
- It changes over time.  
- Those changes are intended to improve it with respect to one or more goals.

Examples: bug, manuscript, source file, research paper, design document, incident report, lesson plan. Artifacts are the primary objects over which the DLE learns.

### **2.3 Artifact Version**

An artifact is immutable at any instant. Whenever an expert modifies an artifact, a new version is created. The DLE reasons over versions rather than mutable objects. Every version represents the complete observable state of the artifact at a particular point in time.

### **2.4 Context**

No decision exists in isolation. Every decision is made within a context, which may include artifact state, historical versions, surrounding artifacts, organizational environment, related work, external events, and user-supplied goals. The DLE explicitly distinguishes context from the artifact itself.

### **2.5 Intent**

Intent represents the immediate objective motivating one or more expert decisions. Intent answers the question: *"What is the expert trying to accomplish?"*

Intent is not inferred from the outcome alone; it is inferred from repeated evidence. Intent may evolve during the lifetime of an artifact.

### **2.6 Decision**

A decision is an intentional transformation applied to an artifact. A decision is always evaluated relative to the current artifact, the current context, and the current intent.

Decisions may consist of modifying, inserting, deleting, reordering, annotating, classifying, or assigning. Multiple decisions may contribute to a single intent.

### **2.7 Episode**

*(Promoted to a first-class ontological concept, as the Version 2 commentary directed.)*

An episode is a bounded sequence of expert activity directed toward one or more active intents. It groups together observations, intents, decisions, and outcomes that collectively pursue one or more objectives. Conceptually, Episode sits between Artifact and Intent in the canonical hierarchy: an artifact evolves through episodes; each episode is organized around intents; each intent owns decisions.

Episodes provide the primary unit from which expertise is learned. Chapter 10 defines them in full.

### **2.8 Transition**

A transition represents the observable effect of one decision:

Artifact Version N  →  Decision  →  Artifact Version N+1

The DLE learns from transitions because they provide concrete evidence of expert behavior.

### **2.9 Observation**

An observation records what occurred. It contains no interpretation. Observations are immutable. They represent the factual history of artifact evolution.

### **2.10 Evidence**

Evidence is an observation that the engine has determined is appropriate for learning. Evidence combines observation, context, provenance, and authority. Evidence remains the only mechanism by which organizational knowledge evolves.

### **2.11 Hypothesis**

A hypothesis is a candidate explanation of observed evidence. Hypotheses compete. Evidence determines which survive. Multiple hypotheses may explain the same observations simultaneously.

### **2.12 Policy**

A policy is a hypothesis that has accumulated sufficient supporting evidence to guide future decisions. Policies remain conditional. Policies remain revisable. Policies never become immutable truth.

### **2.13 Belief**

A belief records the engine's current assessment of a policy (and, more generally, of any inferred object — see Chapter 11). Belief includes confidence, maturity, supporting evidence, contradictory evidence, and outstanding questions. Beliefs evolve continuously as evidence accumulates.

### **2.14 Knowledge**

Knowledge consists of all policies, beliefs, explanations, and provenance currently maintained by the DLE. Knowledge is persistent. Reasoning is transient.

### **2.15 Strategy (Contextual Metadata, Not a First-Class Object)**

Above Intent there is one more layer that is useful across all domains:

**Strategy → Intent → Decision**

For example, in philosophy writing: *Strategy:* teach the reader gradually; *Intent:* introduce trajectories before gravity; *Decision:* insert a new bridge section after "Fields." In bug management: *Strategy:* minimize customer impact; *Intent:* escalate this customer-facing issue; *Decision:* raise priority, add a hotlist, and assign the owning team.

Strategy is deliberately **not** a first-class object in the initial architecture. Unlike Intent, strategies tend to be long-lived, relatively stable, and often belong to an organization, project, or author rather than to individual artifact revisions. Strategy is treated as contextual metadata associated with a project or artifact collection, leaving Intent as the operational objective that directly drives decisions. This keeps the ontology compact while leaving room for future expansion if explicit strategy reasoning proves valuable.

### **2.16 Summary**

The Common Core ontology deliberately separates artifacts, episodes, intent, decisions, evidence, hypotheses, and policies. These concepts form the conceptual vocabulary shared by every specialization of the Decision Learning Engine.

---

## **Chapter 2A — Artifact Domains**

### **2A.1 Introduction**

The Decision Learning Engine is intentionally independent of any particular application domain. Instead, it operates upon **artifact domains**. An artifact domain defines:

- the artifacts,  
- the experts,  
- the intents,  
- the decisions,  
- the execution mechanisms.

Every specialization of the DLE maps these concepts into its own domain.

### **2A.2 Operational Domains**

Operational artifacts coordinate work. Examples: bugs, incidents, change requests, customer tickets, infrastructure alerts. Typical intents: route work, reduce impact, restore service, prepare release.

### **2A.3 Knowledge Domains**

Knowledge artifacts communicate ideas. Examples: books, research papers, design documents, RFCs, sermons, technical documentation. Typical intents: explain, persuade, clarify, teach, synthesize.

### **2A.4 Software Domains**

Software artifacts evolve through engineering decisions. Examples: source code, unit tests, configuration, build systems. Typical intents: simplify, optimize, refactor, secure, modularize.

### **2A.5 Educational Domains**

Educational artifacts support learning. Examples: courses, lessons, exercises, assessments. Typical intents: motivate, scaffold, reinforce, assess.

### **2A.6 Creative Domains**

Creative artifacts express ideas through artistic media. Examples: stories, games, music, illustrations. Typical intents: engage, surprise, balance, immerse.

### **2A.7 Common Lifecycle**

Every artifact domain follows the same conceptual lifecycle:

flowchart TD

    A\[Artifact\] \--\> B\[Observation\]

    B \--\> C\[Intent\]

    C \--\> D\[Decision\]

    D \--\> E\[Transition\]

    E \--\> F\[Evidence\]

    F \--\> G\[Knowledge\]

    G \--\> H\[Improved Artifact\]

Only the artifact-specific details change.

### **2A.8 Summary**

Artifact domains specialize the Common Core without altering its conceptual architecture. The DLE therefore functions as a general framework for learning expert decision-making across many disciplines.

---

## **Chapter 3 — Learning Architecture**

*(Renamed from "Knowledge Architecture" in the original draft: knowledge is only one thing flowing through the system. The DLE transforms **experience** into **knowledge**; knowledge is an outcome of learning, not the architecture itself.)*

### **3.1 Introduction**

The purpose of the Decision Learning Engine is to transform observed expert experience into reusable knowledge. Experience consists of expert interactions with evolving artifacts. Knowledge consists of reusable explanations that consistently predict successful future decisions.

The architecture therefore defines a pipeline that transforms experience into increasingly abstract forms of understanding.

### **3.2 Learning Pipeline**

Every learning cycle follows the same conceptual progression. (Per the Version 2 commentary, **Episode** is inserted between Artifact and Intent.)

flowchart TD

    AV\[Artifact / Artifact Version\] \--\> OBS\[Observation\]

    OBS \--\> CTX\[Context\]

    CTX \--\> EP\[Episode\]

    EP \--\> INT\[Intent\]

    INT \--\> DEC\[Decision\]

    DEC \--\> TR\[Transition\]

    TR \--\> EV\[Evidence\]

    EV \--\> HYP\[Hypothesis\]

    HYP \--\> POL\[Policy\]

    POL \--\> EG\[Execution Guidance\]

Each stage adds semantic information. Nothing is discarded. Earlier representations remain available for replay and explanation.

### **3.3 Observation**

Learning begins with observation. Observations record the artifact version, timestamp, actor, and surrounding context. Observations intentionally avoid interpretation. Their purpose is to preserve historical truth.

### **3.4 Context Enrichment**

Observations rarely contain sufficient information to explain expert behavior. The architecture therefore enriches observations with contextual information: neighboring artifacts, organizational state, related discussions, historical behavior, environmental conditions. Context remains external to the artifact itself.

### **3.5 Intent Inference**

Intent occupies a unique position within the learning architecture. Unlike observations, intent is not always directly observable. Instead, the engine infers intent from repeated patterns of expert behavior.

Intent inference may use explicit user annotations, comments, goals, historical behavior, reasoning models, and organizational metadata. Multiple candidate intents may exist simultaneously.

### **3.6 Decision Identification**

Once context and intent are understood, the architecture identifies the expert decisions that transformed one artifact version into another. Decisions become the smallest meaningful unit of expert behavior. Multiple decisions may occur within a single revision.

### **3.7 Evidence Formation**

Not every decision contributes equally to learning. The Evidence Processor evaluates each decision using provenance, authority, consistency, context, and subsequent outcomes. Evidence becomes the permanent foundation of organizational learning.

### **3.8 Hypothesis Formation**

The architecture generates hypotheses explaining recurring evidence. A hypothesis attempts to answer: *Why do experts repeatedly make this decision under these conditions?* Hypotheses compete. Evidence determines which remain viable.

### **3.9 Policy Formation**

Policies emerge only after sufficient evidence supports a hypothesis. Policies describe reusable decision-making behavior. Policies remain conditional, explainable, revisable, and evidence-backed.

### **3.10 Execution Guidance**

Policies become useful only when translated into actionable guidance. The Common Core deliberately avoids prescribing any execution mechanism. Instead, policies are transformed into execution guidance consumed by domain-specific execution engines.

### **3.11 Continuous Learning**

The learning architecture never reaches a final state. Every new observation may strengthen existing knowledge, weaken previous assumptions, generate new hypotheses, reveal new intents, or expose missing context. Learning therefore becomes continuous rather than episodic.

### **3.12 The Central Formulation: Decision \= f(Context, Intent)**

The DLE is learning **three different things simultaneously**:

1. **What experts do** (Decisions)  
2. **Why they do it** (Intent)  
3. **When they do it** (Context)

This yields a simple but powerful formulation:

**Decision \= f(Context, Intent)**

The DLE's job is to approximate that function from evidence. The formulation generalizes across domains: in bug management, given the bug state, organizational context, and intent ("reduce customer impact"), predict the appropriate actions; in philosophy writing, given the manuscript state, conceptual context, and intent ("prepare the reader for trajectories"), predict the editorial decisions. This equation is one of the central mathematical abstractions of the Common Core — it states precisely what the DLE is trying to learn, independent of the artifact.

### **3.13 Summary**

The Learning Architecture transforms raw experience into reusable knowledge through successive stages of abstraction. Intent serves as the bridge between observed behavior and inferred understanding, enabling the DLE to learn not merely what experts do, but what they are trying to accomplish.

---

## **Chapter 4 — Logical Architecture**

*(Renamed from "System Architecture": the Common Core remains independent of software architecture for as long as possible. This chapter describes roles and responsibilities; only much later are these roles mapped onto classes and processors. This makes the Common Core more durable.)*

### **4.1 Introduction**

The Decision Learning Engine consists of a collection of logical capabilities that cooperate to transform expert experience into reusable knowledge. These capabilities are independent of programming language, deployment model, storage technology, and execution environment. Each capability performs one conceptual responsibility.

### **4.2 Architectural Principles**

- **Separation of Concerns** — observation, reasoning, knowledge management, execution, and evaluation remain distinct responsibilities.  
- **Replaceability** — any capability may be replaced without changing the conceptual architecture.  
- **Traceability** — every output shall be traceable to its supporting evidence.  
- **Incrementality** — knowledge evolves through successive observations rather than wholesale retraining.

### **4.3 Observation Capability**

Acquires artifact versions from external systems: acquiring artifacts, recording versions, preserving history, identifying changes. It performs no reasoning. Its sole responsibility is faithful observation.

### **4.4 Context Capability**

Enriches observations with information relevant to decision making: neighboring artifacts, organizational metadata, historical activity, external references, user goals, domain-specific information. Context is computed rather than observed.

### **4.5 Intent Capability**

Identifies the objective motivating expert decisions. Intent may be explicitly provided, inferred, or refined over time. Multiple candidate intents may coexist until sufficient evidence favors one interpretation. Intent is never treated as certain merely because it appears plausible.

### **4.6 Decision Capability**

Identifies the transformations performed by experts: extracting decisions, grouping related decisions, associating decisions with context, associating decisions with intent. This capability transforms artifact changes into reusable learning units.

### **4.7 Knowledge Capability**

Maintains the accumulated understanding of the engine: evidence management, hypothesis management, policy management, belief management, provenance. Knowledge persists independently of the reasoning process that created it.

### **4.8 Reasoning Capability**

Proposes explanations: generating hypotheses, synthesizing policies, explaining behavior, identifying contradictions, proposing missing context.

**Reasoning proposes. Knowledge evaluates.**

### **4.9 Execution Capability**

Translates validated knowledge into domain-specific actions. The Common Core deliberately avoids prescribing the form of execution — examples include operational automation, writing guidance, code suggestions, educational recommendations.

**Execution engines consume knowledge. They do not own it.**

### **4.10 Evaluation Capability**

Observes the consequences of execution: collecting outcomes, measuring agreement, identifying contradictions, updating evidence. Evaluation closes the learning loop.

### **4.11 Knowledge Flow**

The logical architecture forms a continuous cycle:

flowchart TD

    A\[Artifacts\] \--\> B\[Observation\]

    B \--\> C\[Context\]

    C \--\> D\[Intent\]

    D \--\> E\[Decision\]

    E \--\> K\[Knowledge\]

    K \--\> G\[Execution Guidance\]

    G \--\> X\[Execution\]

    X \--\> V\[Evaluation\]

    V \--\>|new evidence| K

    X \--\>|new observations| A

The cycle never terminates. Every completed execution produces new observations.

### **4.12 Responsibilities**

The logical architecture intentionally assigns each responsibility to exactly one capability:

- Observation **records**.  
- Reasoning **explains**.  
- Knowledge **remembers**.  
- Execution **acts**.  
- Evaluation **learns**.

This separation simplifies testing, replacement, and future evolution.

### **4.13 Summary**

The logical architecture defines the conceptual responsibilities required to transform expert behavior into reusable knowledge. It deliberately separates observation, understanding, execution, and evaluation, ensuring that knowledge remains independent of any particular implementation.

---

## **Chapter 5 — Representation Model**

*(Renamed from "Knowledge Representation": the DLE doesn't just represent knowledge; it represents **experience** at multiple levels of abstraction. This chapter is the ontology of everything the engine stores — not just knowledge.)*

### **5.1 Introduction**

The Decision Learning Engine operates by transforming raw experience into progressively richer representations. Each representation captures a different aspect of expert behavior. Lower-level representations preserve facts. Higher-level representations capture increasingly abstract understanding.

The architecture intentionally preserves every level rather than collapsing them into a single representation. This allows future reasoning algorithms to reinterpret historical experience without loss of information.

### **5.2 Representation Hierarchy**

The DLE maintains the following conceptual hierarchy (with **Episode** as a canonical representation, per the Version 2 commentary):

Artifact

  ↓

Artifact Version

  ↓

Observation

  ↓

Context

  ↓

Episode

  ↓

Intent

  ↓

Decision

  ↓

Transition

  ↓

Evidence

  ↓

Hypothesis

  ↓

Policy

  ↓

Execution Guidance

Each layer adds semantic meaning. Earlier layers remain immutable. Later layers evolve.

### **5.3 Artifact**

The artifact represents the object being improved. Artifacts possess identity independent of their versions. Artifacts are long-lived; their versions capture evolution.

### **5.4 Artifact Version**

Artifact versions represent immutable snapshots of an artifact. Each version captures observable state, metadata, timestamp, and provenance. Versions form the factual history of an artifact.

### **5.5 Observation**

Observations record what occurred and intentionally avoid explanation. Examples: a paragraph was moved; a priority changed; a new section appeared. Observations remain objective.

### **5.6 Context**

Context augments observations with information necessary to understand expert decisions. Unlike observations, context may be computed or retrieved: neighboring artifacts, organizational structure, concept graph, historical revisions, external references.

Context is dynamic. Historical replay reconstructs context as it existed at the time of the decision.

**Context layers.** Context has three distinct layers, defined here as categories (not yet first-class objects):

1. **Local Context** — the immediate artifact and nearby content.  
2. **Global Context** — the broader project, organization, or manuscript.  
3. **Historical Context** — the sequence of prior versions and decisions.

For a writing system: local \= the current paragraph and neighboring sections; global \= the entire manuscript and concept graph; historical \= previous drafts and revision history. For a bug system: local \= the current bug; global \= release state, ownership, related bugs; historical \= prior modifications and routing history.

Different processors may require different mixes of local, global, and historical context; making this distinction explicit improves both reasoning quality and implementation clarity, and guides retrieval strategies, without complicating the core ontology.

### **5.7 Intent**

Intent represents the objective motivating one or more decisions. The architecture distinguishes two forms of intent — Intent is a **shared object** between the human and the DLE:

#### *Declared Intent*

Provided explicitly by a human or external system. Examples: "Prepare this bug for release." / "Next, introduce trajectories."

#### *Inferred Intent*

Generated by the DLE through observation and reasoning. Examples: improve readability; reduce customer impact; introduce prerequisite concepts.

**Declared intents become observations. Inferred intents become hypotheses until sufficiently supported by evidence.** The Belief Record attaches confidence to inferred intents, while declared intents remain authoritative observations. By treating declared and inferred intents separately, the DLE can learn from explicit goals without confusing them with its own hypotheses — making the architecture both more rigorous and more collaborative.

### **5.8 Episode**

An episode is a canonical representation of a bounded, goal-directed unit of expert work. It binds together artifacts, versions, observations, context, declared and inferred intents, decisions, transitions, evidence, and outcomes (see Chapter 10). Episodes are the temporal and semantic boundary within which learning occurs.

### **5.9 Decision**

A decision represents an intentional transformation of an artifact. Every decision references an artifact version, context, intent, actor, and timestamp. Decisions become the primary units from which expertise is learned.

### **5.10 Transition**

Transitions describe the observable effect of decisions. A transition relates two artifact versions. Transitions contain no explanation; they merely record the transformation.

### **5.11 Evidence**

Evidence enriches observations with organizational meaning. Evidence records provenance, authority, supporting context, trust, and relationships. Evidence becomes permanent. Unlike hypotheses, evidence is never regenerated.

### **5.12 Hypothesis**

Hypotheses attempt to explain recurring evidence. They explicitly record supporting evidence, contradictory evidence, confidence, and outstanding questions. Hypotheses are intentionally provisional.

### **5.13 Policy**

Policies represent reusable expert behavior. Policies contain conditions, actions, applicability, explanation, and a belief record. Policies summarize understanding rather than observations.

### **5.14 Belief Record**

The Belief Record records the engine's current understanding of a policy (or other inferred object). It includes confidence, maturity, drift indicators, evidence references, and unresolved questions. Beliefs evolve continuously. Policies evolve through their associated beliefs.

### **5.15 Execution Guidance**

Policies are transformed into execution guidance suitable for a domain-specific execution engine. Execution guidance is intentionally separate from policy: different domains may operationalize identical policies differently.

### **5.16 Representation Independence**

Every representation is independent of storage, programming language, reasoning engine, and execution engine. The Common Core therefore specifies conceptual objects rather than implementation details.

### **5.17 Summary**

The Representation Model defines the conceptual language of the DLE. By preserving progressively richer representations of expert behavior, the architecture enables continual reinterpretation, explanation, replay, and learning without losing historical fidelity.

---

## **Chapter 6 — Knowledge Architecture**

### **6.1 Introduction**

The purpose of the Knowledge Architecture is to organize accumulated understanding independently of any particular reasoning algorithm or execution engine. Knowledge is not merely stored. It is organized into an interconnected structure that preserves evidence, provenance, explanations, competing hypotheses, beliefs, and policies. The architecture therefore treats knowledge as a graph rather than a collection of independent records.

**Relationship to memory.** Conceptually, the Knowledge Architecture is built on top of the Memory Architecture (Chapter 13), even though it is introduced earlier in this document for explanatory purposes. Memory stores experience and learned structures; reasoning operates over memory; knowledge is the subset of memory that has become generalized and reusable:

Experience → Memory → Reasoning → Knowledge

Knowledge is not a separate storage system; it is a particular kind of memory that has achieved a higher level of abstraction. This aligns the architecture with how both cognitive science and modern AI systems think about memory and learning.

### **6.2 The Knowledge Graph**

Every persistent object maintained by the DLE is a node within a conceptual Knowledge Graph: Artifact, Artifact Version, Observation, Context, **Episode**, Intent, Decision, Evidence, Hypothesis, Policy, Belief Record, Execution Guidance.

Relationships between these objects are explicit. For example:

Artifact Version

      │

      ▼

   Decision ── motivated by ──► Intent

                                   │

                          supported by

                                   ▼

                               Evidence ── supports ──► Hypothesis ── becomes ──► Policy

The graph records not only *what* is known, but *why* it is known.

### **6.3 Identity**

Every knowledge object possesses a stable identity, independent of storage technology or implementation. Stable identities enable provenance, replay, versioning, cross-references, and explanation. Objects are never identified solely by their position within another object.

### **6.4 Provenance**

Every knowledge object records its origin: which observations contributed, which evidence supports it, which expert performed the decision, which reasoning process proposed it, which policy references it.

**Provenance is mandatory. Knowledge without provenance cannot be trusted.**

### **6.5 Immutability**

Historical objects remain immutable: artifact versions, observations, decisions, evidence. Knowledge evolves by adding new objects rather than modifying historical ones. Only beliefs and policies evolve through versioning. History is never rewritten.

### **6.6 Versioning**

Knowledge evolves continuously. Rather than overwriting objects, the architecture records successive versions. Versioning enables replay, auditing, comparison, rollback, and historical explanation. The DLE reasons over the latest version while preserving all previous understanding.

### **6.7 Explanations**

Explanations are first-class knowledge objects. Every policy should reference one or more explanations. Explanations themselves possess provenance, evidence, and revision history. This avoids regenerating different explanations for identical knowledge.

### **6.8 Competing Knowledge**

The architecture intentionally permits competing hypotheses. Knowledge need not converge immediately. Example: *Hypothesis A: customer impact explains routing. Hypothesis B: component ownership explains routing.* Both may coexist until evidence sufficiently distinguishes them.

### **6.9 Belief Evolution**

Beliefs evolve independently from policies. A policy may remain unchanged while confidence increases, contradictory evidence appears, or outstanding questions are resolved. Separating policies from beliefs simplifies continual learning.

### **6.10 Retrieval**

Reasoning should retrieve knowledge rather than raw observations whenever possible. Typical retrieval operations: similar evidence, related policies, supporting explanations, contradictory evidence, unresolved questions. Retrieval should return semantically meaningful objects rather than documents.

### **6.11 Knowledge Independence**

Knowledge remains independent of reasoning models, language models, storage engines, and execution engines. Future reasoning algorithms should operate over existing knowledge without reconstruction.

### **6.12 Domain Semantic Graphs (Extension Point)**

Sophisticated domains often require rich semantic relationships beyond the universal ontology. A writing domain's **Concept Graph** is a domain-specific instance of a more general idea: an **Artifact Semantic Graph**. For a manuscript, the nodes are concepts and arguments; for a bug system, they might be services, components, teams, releases, and customers; for source code, classes, functions, modules, and APIs.

Because the semantics differ significantly across domains, these graphs are **not** part of the Common Core itself. Instead, the Common Core defines an extension point: **each specialization may define one or more Domain Semantic Graphs that augment the generic Knowledge Graph.** This keeps the Common Core clean while acknowledging that for some specializations (e.g., AuthorLM's Concept Graph) the semantic graph may become as important as the Knowledge Graph itself, because it represents the structure of the ideas being communicated, not merely the history of decisions.

### **6.13 Summary**

The Knowledge Architecture preserves accumulated organizational understanding as an interconnected, versioned, explainable graph. This graph becomes the enduring asset of the Decision Learning Engine.

---

## **Chapter 7 — Inference Architecture**

### **7.1 Introduction**

Observation alone is insufficient to explain expert behavior. The Decision Learning Engine therefore performs inference: the process of proposing information that was not directly observed but is supported by available evidence.

The architecture deliberately distinguishes between observation, inference, and knowledge. Only observations are facts. Inferences remain provisional until supported by sufficient evidence.

### **7.2 What May Be Inferred**

The DLE may infer:

- intent,  
- hypotheses,  
- explanations,  
- missing context,  
- policy applicability,  
- future decisions,  
- likely outcomes.

These become candidates for learning.

### **7.3 What May Never Be Inferred**

Certain information must remain observational: artifact versions, timestamps, identities, recorded decisions, declared intent, human reviews. The DLE may reason about these objects. It may never replace them.

### **7.4 Layers of Inference**

- **Context Inference** — recover missing contextual information.  
- **Intent Inference** — infer why experts acted.  
- **Hypothesis Inference** — infer explanations.  
- **Policy Inference** — generalize hypotheses into reusable behavior.  
- **Prediction** — infer likely future decisions.

Each level builds upon previous levels.

### **7.5 Retrospective and Prospective Inference**

The DLE performs **two kinds of inference**:

1. **Retrospective inference** — explaining what experts did. ("The author inserted this section because they were preparing for trajectories." / "The engineer raised the priority to reduce customer impact.")  
2. **Prospective inference** — predicting what experts are likely to do next. ("The author will probably introduce histories next." / "This bug will likely be routed to the Payments team.")

This distinction belongs in the Common Core because it applies equally across domains. The architecture primarily exists to improve **retrospective inference**, since that is how knowledge is acquired. **Prospective inference** is a consequence of that understanding rather than the primary objective. The ordering matters: **the DLE predicts well because it first learns to explain well.** That is a stronger philosophical foundation than treating prediction as the central goal.

### **7.6 Competing Inferences**

Multiple inferences may coexist. For example, a single decision may plausibly serve *Intent A: reduce customer impact* or *Intent B: prepare release*. Both remain valid until evidence favors one interpretation. The architecture deliberately preserves competing explanations.

### **7.7 Declared vs. Inferred Objects**

Whenever possible, **declared information takes precedence over inferred information**. A declared intent (author: "Introduce trajectories.") supersedes inferred editorial intent; explicit bug-management goals supersede inferred operational goals.

**Inference supplements observation. It never replaces it.**

### **7.8 Inference Provenance**

Every inference records originating evidence, reasoning method, confidence, timestamp, and version. Inferences are therefore replayable and auditable.

### **7.9 Inference Evolution**

As additional evidence accumulates, inferences may strengthen, weaken, split, merge, or disappear. The DLE continuously refines inferred understanding.

### **7.10 Inference Is Not Knowledge**

One of the most important principles of the architecture:

**Inference proposes. Evidence validates. Knowledge remembers.**

This separation prevents speculative reasoning from becoming persistent organizational knowledge.

### **7.11 Summary**

Inference enables the DLE to recover hidden structure from observed expert behavior. The architecture deliberately constrains inference, preserving a clear boundary between observed facts, inferred explanations, and accumulated knowledge.

---

## **Chapter 8 — Knowledge Evolution**

*(Replaces the original "Organizational Knowledge Base" chapter, which was too implementation-oriented. This chapter answers: **how does knowledge change over time?** — distinct from how it is represented (Chapter 5\) and how it is organized (Chapter 6). This chapter describes how the DLE actually learns.)*

### **8.1 Introduction**

Knowledge is not static. Organizations change. Experts change. Artifacts change. Consequently, the knowledge maintained by the DLE must also evolve.

The purpose of the Knowledge Evolution architecture is to define how understanding changes while preserving historical truth. **The DLE never rewrites history. It revises its interpretation of history.**

### **8.2 Immutable History**

Historical observations remain immutable: artifact versions, observations, declared intents, decisions, evidence. These objects never change. They constitute the factual record upon which learning is built.

### **8.3 Evolving Understanding**

Understanding evolves: inferred intents, hypotheses, explanations, policies, beliefs. These objects may strengthen, weaken, split, merge, or disappear as evidence accumulates.

### **8.4 The Knowledge Lifecycle**

Every inferred object follows the same conceptual lifecycle:

flowchart TD

    O\[Observation\] \--\> E\[Evidence\]

    E \--\> H\[Hypothesis\]

    H \--\> CP\[Candidate Policy\]

    CP \--\> VP\[Validated Policy\]

    VP \--\> SP\[Stable Policy\]

    SP \--\> RP\[Retired Policy\]

Movement through this lifecycle is governed by evidence rather than time.

**The compiler analogy.** *(Reinstated from Version 1.)* Knowledge evolution resembles compilation more than configuration editing. Evidence is the source code; policies are the intermediate representation; execution guidance is the compiled artifact. Policies are never edited directly — they are continuously reconstructed from accumulated evidence, and execution guidance is regenerated from current policy rather than manually maintained. Whenever evidence changes, the downstream representations are recompiled. This framing explains why history must remain immutable: it is the source from which all understanding is rebuilt.

### **8.5 Promotion**

Knowledge becomes more authoritative through promotion, which occurs when accumulated evidence consistently supports an inference. Typical promotions: inferred intent → accepted intent; hypothesis → candidate policy; candidate policy → validated policy. Promotion should always remain explainable.

Promotion is deliberately conservative: **false negatives are preferable to false positives.** *(Reinstated from Version 1.)* A pattern the engine fails to promote costs only a missed recommendation; a pattern promoted without substantial empirical support erodes trust and contaminates execution. Any policy that reaches execution guidance must have survived substantial evidence.

### **8.6 Demotion**

Knowledge may weaken. Contradictory evidence may cause reduced confidence, policy demotion, policy retirement, or hypothesis splitting.

**Demotion is not failure. It is learning.**

### **8.7 Drift**

Organizations evolve. Policies that were once correct may gradually lose explanatory power. The DLE therefore monitors knowledge drift. Sources include organizational restructuring, changing objectives, new expertise, changing writing style, changing engineering practices.

Drift does not imply incorrectness. It indicates changing evidence.

### **8.8 Forgetting and Recency-Weighted Evidence**

*(Reinstated from Version 1.)*

Learning requires forgetting. The DLE distinguishes **historical knowledge** from **current organizational practice**: unless a policy explicitly dictates otherwise, older evidence gradually contributes less to belief than recent evidence. History remains immutable; its **influence** decays.

Recency weighting is the mechanism beneath drift (§8.7). Without it, a policy supported by hundreds of old observations could never be displaced by a genuine change in expert behavior; with it, adaptation follows naturally from the ordinary accumulation of evidence. Decayed evidence is never deleted — it remains available for replay, historical explanation, and the reconstruction of past understanding. The objective is adaptation, not historical preservation (Axiom 8).

### **8.9 Contradictions**

Contradictions are valuable. Rather than eliminating conflicting evidence, the DLE preserves it. Contradictions often indicate missing context, competing intents, organizational evolution, or incomplete hypotheses. Resolving contradictions frequently produces deeper understanding.

### **8.10 Retirement**

Knowledge should not persist indefinitely. Policies may be retired when evidence disappears, organizations change, better explanations emerge, or explicit human guidance supersedes them. Retired knowledge remains historically accessible.

### **8.11 Replay**

Because observations remain immutable, the DLE can reconstruct knowledge using newer reasoning algorithms. Replay therefore becomes a mechanism for evolving understanding without altering history.

### **8.12 Human Participation**

Humans contribute to knowledge evolution by declaring intent, reviewing recommendations, providing explicit feedback, and making expert decisions. **Humans contribute evidence. The DLE updates knowledge.**

### **8.13 Three Kinds of Evolution and the Expert Evolution Principle**

There are **three independent kinds of evolution**:

1. **Artifact evolution** — the manuscript or bug changes.  
2. **Knowledge evolution** — the DLE's understanding changes.  
3. **Expert evolution** — the human's own practices and preferences change.

An author may genuinely become a different philosopher over the course of writing a book; an organization may adopt a new triage philosophy after a reorganization. The DLE should therefore avoid assuming there is a single timeless "correct" policy. It models knowledge as time-dependent and recognizes that expert behavior itself evolves.

**Expert Evolution** is an explicit principle of the Common Core: the DLE is learning from a moving target, and **adaptation — not convergence — is the long-term objective.** This fits naturally with the evidence-driven philosophy of the architecture. (No separate ontology is required for this principle.)

### **8.14 Summary**

Knowledge evolution separates immutable historical experience from evolving understanding. This distinction allows the DLE to continuously improve while preserving complete historical fidelity.

---

## **Chapter 9 — Intent Lifecycle**

*(Renamed from "Decision Lifecycle": decisions are instantaneous; intents persist. A bug may undergo dozens of decisions in service of one intent; a chapter may undergo hundreds of edits in service of one intent. The thing that actually has a lifecycle is **Intent** — a much stronger abstraction.)*

### **9.1 Introduction**

Intent is the operational objective that motivates one or more expert decisions. Unlike individual decisions, intents often persist across multiple revisions of an artifact. Understanding intent is therefore central to understanding expertise. This chapter defines how intents are created, inferred, refined, completed, and retired.

### **9.2 Declared Intent**

Some intents are explicitly declared: "Prepare this bug for release." / "Introduce trajectories." / "Address the strongest objection."

Declared intents become observations. They are authoritative records of expert objectives. **The DLE does not infer declared intents. It records them.**

### **9.3 Inferred Intent**

Many intents are never explicitly stated. They are reconstructed from repeated expert behavior: improve readability, reduce customer impact, simplify implementation, strengthen intuition. Inferred intents remain hypotheses until supported by sufficient evidence.

### **9.4 Intent Activation**

An intent becomes active when it begins influencing decisions. An artifact may possess multiple active intents simultaneously — a manuscript chapter may simultaneously pursue introducing a concept, improving readability, and preparing a future chapter; a bug may simultaneously pursue customer impact reduction, release readiness, and ownership clarification. The architecture does not require a single active intent.

### **9.5 Intent Refinement**

As additional evidence accumulates, an intent may become more precise. "Improve the manuscript" may refine into "Prepare the reader for trajectories." "Escalate issue" may refine into "Escalate customer-facing payments bug before release." Learning therefore increases specificity over time.

### **9.6 Intent Completion**

An intent completes when sufficient decisions have achieved its objective. Completion does not imply permanence: future revisions may reactivate similar intents. Completed intents remain part of historical knowledge.

### **9.7 Intent Splitting**

A single inferred intent may actually represent multiple objectives. "Improve clarity" may separate into: define terminology, simplify prose, restructure argument. "Prepare release" may split into: routing, milestone updates, risk assessment. The DLE should permit intent decomposition as understanding improves.

### **9.8 Intent Merging**

Conversely, multiple inferred intents may later prove equivalent and merge into a single, more general intent. This prevents unnecessary fragmentation of organizational knowledge.

### **9.9 Intent Drift**

Intent itself may evolve. Organizations change priorities. Authors change writing objectives. Engineers change design philosophy. The DLE therefore continuously evaluates whether historical intents still explain current decisions.

### **9.10 Intent Hierarchies**

Intents frequently form hierarchies. The architecture should preserve these relationships whenever supported by evidence.

flowchart TD

    A\[Improve Reader Understanding\] \--\> A1\[Introduce Trajectories\]

    A \--\> A2\[Clarify Fields\]

    A \--\> A3\[Prepare Mathematics\]

    B\[Reduce Customer Impact\] \--\> B1\[Escalate\]

    B \--\> B2\[Route\]

    B \--\> B3\[Prioritize\]

### **9.11 Intent Provenance**

Every inferred intent records supporting evidence, contradictory evidence, originating observations, confidence, and version history. Intent therefore becomes a replayable knowledge object.

### **9.12 Intent Owns Decisions**

One of the deepest realizations of the architecture: intent is not something attached to decisions — **intent owns decisions**. The hierarchy is:

Intent

  ├── Decision 1

  ├── Decision 2

  ├── Decision 3

  └── Decision 4

rather than Decision → Intent. Instead of asking "What was the intent of this decision?", the engine asks "**What sequence of decisions appears to be serving the same intent?**" This reframes learning from isolated edits to **goal-directed episodes**.

In reinforcement-learning terms, an intent is analogous to an episode objective, while decisions are the actions taken within that episode. This is a richer and more faithful model of expert behavior, particularly for long-running activities like writing a book or managing a complex bug over several weeks. It also implies that the DLE should maintain **active intent instances** during an artifact's evolution, not merely infer intents retrospectively after all decisions have been made.

### **9.13 Summary**

Intent provides the bridge between observable decisions and reusable knowledge. By explicitly modeling the lifecycle of intent, the DLE learns not only what experts change, but the evolving objectives that motivate those changes.

---

## **Chapter 10 — Episodes**

*(A new first-class Common Core chapter, inserted before belief and confidence. Once "Intent owns decisions" is established, the DLE is not learning isolated rules — it is learning **episodes of expert behavior**. Episodes must be defined before confidence can be defined rigorously.)*

### **10.1 Introduction**

Expert behavior is rarely composed of isolated decisions. Instead, experts pursue objectives over time through sequences of related decisions. The DLE represents these sequences as **episodes**.

An episode groups together observations, intents, decisions, and outcomes that collectively pursue one or more objectives. Episodes provide the primary unit from which expertise is learned.

### **10.2 Definition**

An episode is a bounded sequence of expert activity directed toward one or more active intents. Conceptually:

Episode → Intent(s) → Decision(s) → Artifact Evolution → Outcome

An episode represents a coherent piece of expert work.

### **10.3 Why Episodes Matter**

Without episodes, the DLE observes isolated edits. With episodes, it observes workflows. For example: raising a bug's priority, changing its owner, adding a hotlist, and requesting additional information may appear unrelated when viewed independently. Within an episode, they become coordinated actions serving the same operational objective.

### **10.4 Episode Boundaries**

Episodes begin when one or more intents become active. Episodes end when:

- the intent is completed,  
- the artifact reaches a stable state,  
- another intent supersedes the current one,  
- a long period of inactivity occurs,  
- a human explicitly closes the objective.

Episode boundaries may be explicit or inferred.

### **10.5 Episode Composition**

Every episode contains: one or more artifacts; one or more artifact versions; observations; context; declared intents; inferred intents; decisions; transitions; evidence; outcomes. Not every episode contains every object type.

### **10.6 Multiple Intents**

Episodes may contain several simultaneous intents. A manuscript revision may simultaneously improve clarity, prepare a future chapter, and answer an objection; a bug episode may simultaneously reduce customer impact, prepare release, and coordinate ownership. The architecture does not require a one-to-one relationship between episodes and intents.

### **10.7 Outcomes**

Every episode produces one or more outcomes: artifact improvement, recommendation acceptance, recommendation rejection, operational success, editorial revision. Outcomes become evidence for future learning.

### **10.8 Episode Replay**

Replay occurs naturally at the episode level. Rather than replaying isolated observations, the DLE reconstructs complete episodes, allowing newer learning algorithms to reinterpret expert workflows rather than isolated actions.

### **10.9 Episode Provenance**

Every episode records participating artifacts, participating experts, active intents, supporting evidence, and resulting policies. Episodes therefore become navigable knowledge objects.

### **10.10 Episode as a First-Class Object**

Episode is as fundamental to the architecture as Intent itself. The canonical object sequence becomes:

flowchart TD

    A\[Artifact\] \--\> EP\[Episode\]

    EP \--\> I\[Intent\]

    I \--\> D\[Decision\]

    D \--\> T\[Transition\]

    T \--\> EV\[Evidence\]

    EV \--\> H\[Hypothesis\]

    H \--\> P\[Policy\]

An Episode isn't merely a container; it is the temporal and semantic boundary within which learning occurs. It gives context to sequences of decisions, allows multiple intents to coexist, and provides a natural unit for replay and evaluation.

Accordingly, Episode appears throughout this document as a first-class concept: in the Ontology (Chapter 2), the Learning Pipeline (Chapter 3), the Representation Model (Chapter 5), the Knowledge Graph (Chapter 6), and the canonical objects (Appendix H). This changes the DLE from learning individual decisions to learning coherent, goal-directed units of expert work — a much closer model of how expertise actually operates.

### **10.11 Summary**

Episodes provide the temporal structure within which expert behavior unfolds. By learning from complete episodes rather than isolated decisions, the DLE captures workflows, objectives, and coordinated behavior that would otherwise remain invisible.

---

## **Chapter 11 — Belief and Confidence**

*(In the original draft, belief was attached almost entirely to policies. That is insufficient: the DLE forms beliefs about many kinds of objects — Intent, Context, Hypothesis, Policy, future outcomes. Confidence is therefore not a property of policies; it is a property of **inference**.)*

### **11.1 Introduction**

The Decision Learning Engine distinguishes between facts and beliefs. Facts are directly observed. Beliefs are inferred from evidence. The architecture intentionally prevents inferred understanding from being mistaken for observed reality. Belief represents the engine's current assessment of an inferred object rather than an assertion of truth.

### **11.2 Facts versus Beliefs**

**Observational objects** (facts):

- Artifact Versions  
- Observations  
- Declared Intent  
- Recorded Decisions  
- Human Reviews

**Inferential objects** (possess beliefs):

- Inferred Context  
- Inferred Intent  
- Hypotheses  
- Policies  
- Predictions

Only inferential objects possess beliefs.

### **11.3 Belief Objects**

Every inferential object references an associated Belief Record. A Belief Record captures the engine's current understanding rather than the inferred object itself. For example, the policy "Introduce intuition before formalism." remains unchanged, while the belief about that policy evolves as additional evidence accumulates.

### **11.4 Components of Belief**

A Belief Record contains:

- confidence,  
- maturity,  
- supporting evidence,  
- contradictory evidence,  
- uncertainty,  
- provenance,  
- last evaluation,  
- outstanding questions.

These quantities evolve independently of the underlying policy.

### **11.5 Confidence**

Confidence measures how strongly available evidence supports an inference. **Confidence should not be interpreted as probability.** It reflects the engine's present level of justification. Confidence increases through consistent supporting evidence; it decreases through contradiction or missing context.

Confidence has a second principled source beyond evidence volume: **agreement between independent learners.** When several learners with different inductive biases independently propose the same hypothesis from the same evidence (§14.6), the belief in that hypothesis is strengthened; when they disagree, the disagreement is recorded as uncertainty and identifies where additional evidence should be collected. Learner agreement is therefore not a heuristic — it is a structural input to the Belief Record.

### **11.6 Configurable Trust Models**

*(Reinstated from Version 1.)*

Evidence does not weigh equally, and how it weighs is an organizational choice, not an architectural constant. The architecture therefore **separates evidence collection from evidence weighting**. Collection records facts, provenance, and authority; weighting determines how strongly each piece of evidence influences belief.

The weighting strategy is configurable. Factors may include organizational role, designated expertise, historical consistency, reviewer authority, operational outcomes, recency (§8.8), and the number of supporting observations. One organization may treat all practitioners equally; another may weight designated experts more heavily; a third may privilege reviewed recommendations and operational outcomes above all individual behavior. **Organizations differ; trust models are never hard-coded.** Because collection and weighting are separate, an organization can evolve its trust model without redesigning the learning engine and without invalidating collected evidence. Who is authorized to change the trust model is a governance question (Chapter 18).

### **11.7 Maturity**

Confidence and maturity are distinct. A recently inferred hypothesis may possess high confidence but low maturity because it has been observed only a few times. Conversely, an older policy may possess moderate confidence but high maturity because it has remained stable across many episodes. The architecture evaluates both dimensions separately.

### **11.8 Uncertainty**

Uncertainty is valuable. Rather than forcing premature conclusions, the DLE explicitly records uncertainty: multiple plausible intents, competing hypotheses, insufficient evidence, conflicting expert behavior. **Uncertainty directs future observation. It is not treated as failure.**

### **11.9 Contradictions**

Contradictory evidence weakens belief without deleting knowledge. Contradictions may indicate organizational evolution, incomplete context, multiple valid strategies, exceptions, or previously unknown intents. The DLE preserves contradictions rather than resolving them immediately.

### **11.10 Belief Revision**

Beliefs evolve continuously. New evidence may strengthen, weaken, split, merge, or retire existing beliefs. Historical belief states remain replayable.

### **11.11 Explainability**

Every belief must explain itself. A user should always be able to answer:

- Why does the engine believe this?  
- What evidence supports it?  
- What contradicts it?  
- **What would change its mind?**

Explainability is a required property of every belief.

### **11.12 Descriptive vs. Normative Beliefs**

There are two kinds of beliefs:

1. **Descriptive beliefs** — "I believe this is how the expert behaves."  
2. **Normative beliefs** — "I believe this is the preferred way to behave."

Example: a bug-triage system may observe that two teams handle priorities differently (descriptive); the organization may later decide one approach is the standard (normative). Similarly, a writing system may learn that an author *used* to introduce formalism before intuition, while the author later explicitly declares a new writing philosophy.

Normative beliefs are **not** part of the Common Core. They are a specialization layered on top of descriptive learning. The Common Core focuses on accurately modeling observed expertise; domain-specific systems may overlay organizational standards, editorial guidelines, or other normative goals when generating execution guidance. This keeps the Common Core empirical while allowing specialized RFCs to express prescriptive behavior when appropriate.

### **11.13 Summary**

Belief separates inference from observation. By treating confidence as a property of inferred understanding rather than factual history, the DLE remains evidence-driven, explainable, and continuously self-correcting.

---

## **Chapter 12 — Learning Loops**

*(A concept used throughout the document, now formally defined. The DLE is fundamentally a collection of interacting feedback loops operating at different time scales: some improve individual artifacts, some improve policies, some improve the DLE itself. This chapter explains why the architecture continuously improves.)*

### **12.1 Introduction**

The Decision Learning Engine does not learn through a single mechanism. It consists of multiple interacting learning loops operating at different temporal and conceptual scales. Each loop transforms new experience into refined understanding. Together, these loops enable continual adaptation without requiring complete retraining or manual rule maintenance.

### **12.2 The Principle of Incremental Learning**

Learning occurs incrementally. Each new episode contributes additional evidence. The DLE updates its understanding by incorporating new evidence into existing beliefs rather than rebuilding its knowledge from scratch. This preserves continuity while allowing continual refinement.

### **12.3 Artifact Loop**

The first learning loop concerns the artifact itself:

Artifact → Episode → Improved Artifact

Experts improve artifacts. The DLE observes these improvements. This loop exists even without the DLE.

### **12.4 Knowledge Loop**

The second loop transforms observed experience into knowledge:

Episodes → Evidence → Hypotheses → Policies → Belief Revision

This loop explains how expertise becomes reusable.

### **12.5 Execution Loop**

Knowledge influences future work through execution guidance:

Policies → Execution Guidance → Expert Actions → New Episodes

Execution produces new evidence. Learning therefore becomes self-reinforcing.

### **12.6 Human Collaboration Loop**

Humans continuously shape learning: declared intents, recommendation reviews, corrections, explanations, expert decisions. Human participation supplies evidence rather than directly modifying knowledge.

### **12.7 Replay Loop**

Replay evaluates alternative reasoning over identical historical experience:

Historical Episodes → Replay → Comparison → Improved Learner

Replay allows the DLE to improve without risking operational behavior.

### **12.8 Self-Improvement Loop**

The DLE also learns about itself. It observes prompt quality, retrieval quality, policy quality, explanation quality, and prediction quality. These observations become evidence for improving the learning system itself. This is distinct from learning about the artifact domain.

### **12.9 Multiple Time Scales**

Different loops evolve at different rates:

| Cadence | Activity |
| :---- | :---- |
| Immediate | recommendation review |
| Daily | policy updates |
| Weekly | replay experiments |
| Monthly | prompt revisions |
| Quarterly | architectural improvements |

The DLE intentionally separates these cadences.

### **12.10 Stability**

Not every loop should react equally quickly. Historical observations remain stable. Beliefs adapt more rapidly. Execution policies change cautiously. Architectural changes occur even more conservatively. Separating time scales reduces instability.

### **12.11 The Learning Hierarchy and Nested Loops**

The loops naturally form a hierarchy of nested learning loops:

flowchart TD

    subgraph OPERATIONAL\["Operational loop — doing work"\]

        A\[Artifacts\] \--\> EP\[Episodes\]

        EP \--\> IA\[Improved Artifacts\]

        IA \--\> A

    end

    subgraph LEARNING\["Learning loop — learning from work"\]

        EP \--\> EV\[Evidence\]

        EV \--\> HYP\[Hypotheses\]

        HYP \--\> POL\[Policies\]

        POL \--\> BR\[Belief Revision\]

        POL \--\> EG\[Execution Guidance\]

        EG \--\> XA\[Expert / Executed Actions\]

        XA \--\> EP

    end

    subgraph META\["Meta-learning loop — improving the DLE itself"\]

        HE\[Historical Episodes\] \--\> RP\[Replay\]

        RP \--\> CMP\[Comparison\]

        CMP \--\> IL\[Improved Learner\]

        XA \--\> EVAL\[Evaluation\]

        EVAL \--\> ILS\[Improved Learning System\]

        ILS \--\> RP

    end

Every level contributes to continual improvement. The loops are not all of the same kind — they alternate between **doing work** and **learning from work**:

Work → Observe → Learn → Guide Work → Observe → Learn → …

That alternation is fundamental. The DLE never learns in a vacuum, and it never acts without the possibility of future learning.

### **12.12 Operational, Learning, and Meta-Learning Loops**

For implementation, three loop families should be kept architecturally separate:

- **Operational loops** — bugs being triaged, manuscripts being edited.  
- **Learning loops** — knowledge and policy updates.  
- **Meta-learning loops** — improving prompts, retrieval, and the DLE itself.

Keeping these loops separate makes the system easier to reason about, test, and evolve. It also reinforces a core principle of the architecture: **the DLE does not become better because it reasons more; it becomes better because each loop gives it better evidence to reason with.**

### **12.13 Summary**

The Decision Learning Engine continuously improves through interacting learning loops. Some loops improve artifacts. Others improve organizational knowledge. The highest-level loop improves the DLE itself. This layered architecture allows continual adaptation while preserving historical evidence.

---

## **Chapter 13 — Memory Architecture**

*(Retrieving context, similar episodes, supporting evidence, policies, and concept graphs are all forms of **memory**. The DLE doesn't have one memory; it has multiple kinds.)*

### **13.1 Introduction**

Learning requires memory. Without memory, observations cannot accumulate into evidence, evidence cannot mature into knowledge, and knowledge cannot guide future decisions. The DLE therefore maintains several distinct forms of memory, each serving a different purpose. These memories differ in persistence, abstraction, and rate of change.

### **13.2 Principles**

- Memory serves learning.  
- Different kinds of knowledge require different memories.  
- Historical truth is immutable.  
- Learned understanding evolves.  
- Retrieval should return the most useful representation rather than the largest amount of data.

### **13.3 Episodic Memory**

Episodic Memory stores complete episodes, preserving artifact evolution, active intents, decisions, outcomes, and participating experts. Replay primarily operates over Episodic Memory.

### **13.4 Semantic Memory**

Semantic Memory stores generalized understanding: policies, concept relationships, reusable explanations, organizational knowledge. Semantic Memory evolves slowly. It summarizes repeated experience.

### **13.5 Working Memory**

Working Memory contains information relevant to the current reasoning task: retrieved episodes, active policies, current artifact, current intent, retrieved context. Working Memory is transient. It disappears after reasoning completes.

### **13.6 Intent Memory**

Intent Memory records recurring objectives: reduce customer impact, introduce trajectories, improve clarity, prepare release. Intent Memory allows the DLE to recognize recurring goals across unrelated episodes.

### **13.7 Belief Memory**

Belief Memory stores the current state of inferred understanding: confidence, maturity, uncertainty, contradictions. Unlike Semantic Memory, Belief Memory changes frequently.

### **13.8 Declarative vs. Procedural Memory**

The architecture distinguishes between:

- **Declarative Memory** — facts and knowledge (observations, evidence, policies).  
- **Procedural Memory** — how expertise is exercised (workflows, episode patterns, execution guidance).

The DLE gradually acquires procedural knowledge from repeated expert behavior.

### **13.9 Retrieval**

Reasoning begins by retrieving relevant memories. Retrieval should consider the current artifact, active episode, active intents, semantic similarity, temporal proximity, and historical relevance.

**The objective is not maximum recall. The objective is maximum usefulness.**

### **13.10 Memory Evolution**

Each memory evolves differently: Episodic Memory grows continuously; Semantic Memory grows gradually; Belief Memory changes frequently; Working Memory is ephemeral. Separating these memories prevents instability while preserving historical fidelity.

### **13.11 Memory Independence**

The conceptual memory architecture is independent of implementation. Semantic Memory may eventually use graphs; episodes may reside in relational storage; Working Memory may exist only during reasoning. The Common Core deliberately avoids prescribing implementation technologies.

### **13.12 Memory, Reasoning, and Knowledge**

The cleanest statement of the relationship:

- **Memory** stores experience and learned structures.  
- **Reasoning** operates over memory.  
- **Knowledge** is the subset of memory that has become generalized and reusable.

Experience → Memory → Reasoning → Knowledge

Knowledge is not a separate storage system; it is memory that has achieved a higher level of abstraction. The Knowledge Architecture (Chapter 6\) is conceptually built on top of the Memory Architecture.

### **13.13 Summary**

The Memory Architecture provides the long-term and short-term representations required for continual learning. By separating episodic, semantic, intent, belief, and working memories, the DLE supports efficient reasoning while preserving complete historical experience.

---

## **Chapter 14 — Reasoning Architecture**

*(The original RFC treated LLMs as "the reasoner." That is incorrect: the DLE has a **Reasoning Architecture**, of which LLMs are only one possible implementation. This distinction future-proofs the Common Core.)*

### **14.1 Introduction**

The purpose of the Reasoning Architecture is to transform retrieved memory into candidate understanding. Reasoning does not create knowledge. Reasoning proposes explanations, interpretations, predictions, and recommendations. Knowledge evolves only after those proposals are evaluated against evidence. The DLE therefore separates reasoning from learning.

### **14.2 Responsibilities**

The Reasoning Architecture is responsible for: interpreting observations, inferring intent, generating hypotheses, comparing alternatives, predicting future decisions, generating explanations, recommending actions.

It is **not** responsible for deciding what becomes organizational knowledge.

### **14.3 Inputs**

Reasoning operates over retrieved memory: current artifact, current episode, active intents, contextual information, retrieved episodes, semantic knowledge, belief records, domain-specific semantic graphs.

The quality of reasoning depends heavily upon the quality of retrieval.

### **14.4 Outputs**

Reasoning produces candidate objects: inferred intent, hypothesis, explanation, recommendation, prediction, missing-context suggestion, outstanding question. These objects remain provisional until evaluated.

### **14.5 Multiple Reasoners**

The Common Core intentionally permits multiple reasoning engines: large language models, symbolic reasoning, probabilistic models, rule-based systems, search algorithms, domain-specific planners. The DLE reasons over canonical objects rather than depending upon any particular implementation.

### **14.6 The Marketplace of Learners**

*(Reinstated from Version 1.)*

Permitting multiple reasoners is not sufficient; the architecture organizes them. Hypothesis generation is not a single component but a **marketplace of learners**: multiple independent learners, each embodying a different learning paradigm, consume the same evidence in parallel and each propose hypotheses:

- **Case-Based Learner** — retrieves similar historical episodes and infers likely decisions.  
- **Rule Miner** — discovers deterministic condition → decision relationships.  
- **Bayesian Learner** — updates confidence as evidence accumulates.  
- **Online Learner** — continuously updates predictive models per observation.  
- **Statistical Learner** — identifies recurring patterns.  
- **LLM Learner** — generates candidate explanations and identifies latent organizational concepts.

A **Hypothesis Aggregator** combines their proposals before policy evaluation:

flowchart TD

    EV\[Evidence\] \--\> L1\[Case-Based Learner\]

    EV \--\> L2\[Rule Miner\]

    EV \--\> L3\[Bayesian Learner\]

    EV \--\> L4\[Online Learner\]

    EV \--\> L5\[Statistical Learner\]

    EV \--\> L6\[LLM Learner\]

    L1 \--\> HA\[Hypothesis Aggregator\]

    L2 \--\> HA

    L3 \--\> HA

    L4 \--\> HA

    L5 \--\> HA

    L6 \--\> HA

    HA \--\> PE\[Policy Evaluation\]

Each learner independently proposes; the aggregator combines; policy evaluation compares the combined proposals against accumulated evidence and decides what enters knowledge. **No learner possesses authority** (Axioms 4 and 5 apply to every learner equally).

The marketplace gives the Belief architecture a principled source of confidence: **agreement between independent learners increases confidence; disagreement identifies where to collect more evidence** (§11.5). It also ties the DLE to no single learning paradigm — large language models become one learner among many, and symbolic, statistical, retrieval-based, and future techniques combine without system redesign. This is one of the defining characteristics of the architecture.

### **14.7 Retrieval Before Reasoning**

Reasoning should rarely begin from raw artifacts. The DLE first retrieves the most relevant memory. Reasoning therefore becomes a process of interpreting prior experience rather than rediscovering it. This significantly improves explainability and consistency.

### **14.8 Competing Explanations**

The Reasoning Architecture should actively generate competing explanations whenever uncertainty exists. Instead of asking *"Why did the expert make this decision?"*, the engine asks *"What are the plausible explanations, and what evidence supports each?"* The DLE reasons comparatively rather than dogmatically.

### **14.9 Asking Questions**

Reasoning is not limited to generating answers. It should also identify missing information: missing context, conflicting evidence, ambiguous intent, insufficient observations. Questions become valuable outputs of reasoning.

### **14.10 Reasoning Is Ephemeral**

Reasoning itself is transient. Only its evaluated outputs become persistent knowledge. This separation allows reasoning algorithms to evolve without invalidating accumulated organizational understanding.

### **14.11 Three Kinds of Reasoning**

The DLE performs **three kinds of reasoning**:

1. **Interpretive reasoning** — explaining what happened.  
2. **Generative reasoning** — proposing what could happen.  
3. **Interrogative reasoning** — determining what it still needs to know.

Most AI systems focus almost entirely on the second category. The DLE emphasizes the first and third. Prioritized:

Observe → Interpret → Question → Generate → Evaluate

Good generation depends on good interpretation, and good interpretation depends on asking the right questions when evidence is incomplete. The DLE doesn't become more capable by producing more answers — it becomes more capable by building a more accurate understanding of expert behavior and by recognizing the limits of that understanding. **Reasoning is fundamentally an act of understanding**, with generation emerging as one consequence of that understanding rather than its primary purpose.

### **14.12 Summary**

The Reasoning Architecture transforms memory into candidate understanding. It remains independent of implementation technology and deliberately separates inference from persistent organizational knowledge.

---

## **Chapter 15 — Architectural Interfaces**

*(Everything before this answered "What exists?" This chapter answers "How do these pieces interact?" This is not yet software architecture — it is the contract between the conceptual pieces of the DLE. Specializations later map these interfaces to classes, APIs, databases, and agents.)*

### **15.1 Introduction**

The Decision Learning Engine is composed of independent capabilities that cooperate through well-defined interfaces. The Common Core specifies the information exchanged between capabilities rather than their implementation. Implementations may vary; interfaces remain stable. This separation allows reasoning engines, storage systems, execution engines, and learning algorithms to evolve independently.

### **15.2 Interface Principles**

Every interface should:

- Operate on canonical objects.  
- Be deterministic whenever practical.  
- Preserve provenance.  
- Never mutate historical objects.  
- Be replayable.  
- Be independently testable.

Capabilities communicate through shared representations rather than implementation-specific data structures.

### **15.3 Observation Interface**

- **Consumes:** external artifact sources.  
- **Produces:** Artifact Version, Observation, Metadata.  
- **Responsibilities:** acquire, validate, version, timestamp.

### **15.4 Context Interface**

- **Consumes:** Observation, Artifact Version, Episodic Memory, Semantic Memory.  
- **Produces:** Context (combining local, global, historical, and domain-specific information).

### **15.5 Intent Interface**

- **Consumes:** Context, Declared Intent, Historical Episodes.  
- **Produces:** Active Intent Set (declared intents, inferred intents, competing intents).

### **15.6 Decision Interface**

- **Consumes:** Artifact Versions, Active Intents.  
- **Produces:** Decisions, Transitions. Each decision references the intent(s) it appears to serve.

### **15.7 Evidence Interface**

- **Consumes:** Decisions, Outcomes, Human Reviews.  
- **Produces:** Evidence. Evidence becomes immutable once created.

### **15.8 Learning Interface**

- **Consumes:** Evidence, Existing Knowledge.  
- **Produces:** Hypotheses, Policies, Belief Updates. Learning is incremental; historical evidence is never modified.

**Single-writer discipline.** *(Reinstated from Version 1.)* The Learning capability — the policy-synthesis pathway defined by this interface — is the **only** capability permitted to modify persistent knowledge. Every other capability, including reasoning, execution, and evaluation, reads knowledge but never writes it; they influence knowledge only by producing evidence that flows through this interface. A single writer makes every knowledge change auditable, replayable, and attributable to the evidence that caused it.

### **15.9 Reasoning Interface**

- **Consumes:** Working Memory, Semantic Memory, Episodic Memory, Active Episode.  
- **Produces:** Recommendations, Predictions, Questions, Explanations. Reasoning does not modify knowledge directly.

### **15.10 Execution Interface**

- **Consumes:** Execution Guidance.  
- **Produces:** Artifact Changes, Operational Outcomes. Execution is domain-specific; the Common Core defines only the conceptual interface.

### **15.11 Evaluation Interface**

- **Consumes:** Execution Outcomes, Human Reviews, Subsequent Episodes.  
- **Produces:** New Evidence, Belief Updates, Outstanding Questions. Evaluation closes the learning loop.

### **15.12 Self-Improvement Interface**

- **Consumes:** Replay Results, Evaluation Metrics, Learning Metrics.  
- **Produces:** candidate prompt revisions, retrieval improvements, reasoning improvements, policy synthesis improvements. Changes remain experimental until validated through replay.

**Reasoning-artifact versioning discipline.** *(Reinstated from Version 1.)* Prompts, retrieval configurations, and other reasoning artifacts are software artifacts, not ad hoc instructions embedded in application code. Every reasoning artifact carries a stable identifier, a version, and declared input and output schemas. Every revision is evaluated through replay — against correctness, stability, cost, and agreement with historical evidence — before deployment; **revisions are never deployed because they "sound better."** Versions are never overwritten: replay must be able to reproduce historical reasoning using the artifact version that was active at the time. The no-future-information replay rule depends on this discipline. (The Common Core prescribes the discipline only; catalogs of specific prompts belong to specializations, if anywhere.)

### **15.13 Interface Independence**

No capability should depend directly upon another capability's internal implementation. Capabilities exchange canonical objects only. This allows independent evolution of storage, reasoning, execution, retrieval, and learning.

### **15.14 The One-Input/One-Output Discipline**

A design rule for every implementation:

**Every capability should expose exactly one conceptual input and one conceptual output.**

| Capability | Transformation |
| :---- | :---- |
| Observation | Artifacts → Observations |
| Context | Observations → Context |
| Intent | Context → Active Intents |
| Learning | Evidence → Knowledge |
| Reasoning | Memory → Candidate Understanding |
| Execution | Guidance → Outcomes |
| Evaluation | Outcomes → Evidence |

Internally, a capability may be arbitrarily sophisticated — it may invoke multiple agents, databases, search engines, or machine-learning models. Externally, it should behave like a simple transformation between canonical representations.

This discipline has two major benefits. First, replay and testing become straightforward, because every capability can be exercised independently with canonical inputs and outputs. Second, the DLE can evolve one capability at a time without creating hidden coupling across the architecture. This interface discipline is one of the most important engineering principles of the entire system.

### **15.15 The Four Engineering Questions**

*(Reinstated from Version 1.)*

The interface disciplines above are complemented by a decision test for every proposed implementation feature. Every implementation decision must satisfy at least one of four questions:

1. Does this improve **evidence**?  
2. Does this improve **explainability**?  
3. Does this improve **replay**?  
4. Does this improve **organizational understanding**?

If all four answers are no, the feature should probably not be built. This test keeps implementations aligned with the purpose of the architecture — learning — rather than with infrastructure for its own sake.

### **15.16 Summary**

The Architectural Interfaces define the contracts between the conceptual capabilities of the Decision Learning Engine. Stable interfaces allow implementations to evolve without disrupting the conceptual architecture or accumulated organizational knowledge.

---

## **Chapter 16 — Execution Architecture**

*(The Execution Architecture answers the final question about the operational loop: **How does the DLE safely influence the outside world while continuing to learn?** The original RFC's remaining chapters — Reference Implementation, Experimental Methodology, Minimum Learning Loop, Conclusions — were originally deferred to specializations. Version 2.1 revisits that judgment: reference implementations and the Minimum Learning Loop remain specialization material, but experimental methodology and knowledge governance proved to be domain-agnostic and are reinstated as Chapters 17 and 18.)*

### **16.1 Introduction**

The purpose of the Execution Architecture is to transform learned knowledge into actions that improve artifacts. Execution is deliberately separated from learning. The DLE learns from evidence; execution applies learned knowledge. This separation ensures that experimentation, learning, and operational behavior remain independently evolvable.

### **16.2 Execution Principles**

- **Evidence Before Automation** — knowledge must be supported by sufficient evidence before influencing execution.  
- **Explainability** — every execution should be explainable in terms of supporting evidence, inferred intent, applicable policies, and expected outcome.  
- **Progressive Autonomy** — execution should become increasingly autonomous only as evidence accumulates.  
- **Human Collaboration** — humans remain authoritative participants in execution; human review constitutes evidence.  
- **Replayability** — every execution should be replayable for future learning and evaluation.  
- **Conservatism** — when knowledge reaches execution, false negatives are preferable to false positives: a missed action costs an opportunity; a wrong action costs trust (§8.5).

### **16.3 Execution Levels**

The Common Core recognizes multiple levels of execution autonomy:

- **Level 0 — Observation.** The DLE performs no execution. It only observes.  
- **Level 1 — Recommendations.** The DLE produces recommendations for human review. Humans perform all execution.  
- **Level 2 — Assisted Execution.** The DLE prepares executable actions. Humans explicitly approve execution.  
- **Level 3 — Conditional Automation.** The DLE executes only when predefined execution policies permit. Human overrides remain available.  
- **Level 4 — Autonomous Execution.** The DLE executes without prior approval. Execution continues to produce evidence and remains subject to evaluation and rollback.

The appropriate execution level is domain-specific and may vary across policy categories.

### **16.4 Abstention**

*(Reinstated from Version 1.)*

At every execution level, one of the most important behaviors is knowing when **not** to act. The DLE explicitly supports abstention: given insufficient evidence, conflicting evidence, low confidence, a novel situation, or an unrecognized artifact state, the engine may decline to execute — and may decline even to recommend.

**Abstention is a valid output, not a failure mode.** An abstention is often preferable to an unreliable recommendation, because an unreliable recommendation consumes expert attention, erodes trust, and pollutes the evidence stream with reviews of proposals that should never have been made. Abstentions are recorded with their reasons; recurring abstention over the same conditions is itself a signal directing future evidence collection (§11.8).

### **16.5 Execution Guidance**

Execution Guidance is the operational form of learned knowledge. It is derived from policies but distinct from them. It may include recommendations, executable actions, execution constraints, confidence requirements, required approvals, and safety conditions. Execution Guidance is tailored to the needs of the execution engine.

### **16.6 Execution Adapters**

An **Execution Adapter** translates generic Execution Guidance into domain-specific operations: API requests, Markdown playbooks, document edits, workflow updates, command sequences. The DLE itself remains independent of the external systems it influences.

### **16.7 Execution Policies**

Execution Policies govern whether guidance may be executed. They may consider confidence, maturity, required authority, organizational rules, risk, artifact state, and active intent.

Execution Policies are distinct from learned policies:

**A learned policy recommends behavior. An execution policy authorizes behavior.**

This distinction prevents operational authority from becoming entangled with learned expertise.

### **16.8 Outcomes**

Every execution produces one or more outcomes: artifact modifications, human approval, human rejection, operational success, operational failure, partial completion. **Outcomes are observations.** They become inputs to future learning.

### **16.9 Rollback**

Execution should never be assumed correct. The architecture therefore supports rollback. Rollback does not erase history: it becomes another observed episode contributing evidence to future learning.

### **16.10 Progressive Autonomy**

Autonomy should increase gradually:

Observation → Recommendation → Human Approval → Conditional Automation → Autonomous Execution

Progression is driven by evidence rather than elapsed time. Different policy families may progress independently.

### **16.11 Continuous Learning**

Execution is not the end of the learning process:

Execution → Outcome → Observation → Evidence → Learning → Improved Execution

Execution completes the learning loop established throughout the Common Core.

### **16.12 Separation of Concerns**

| Concern | Responsibility |
| :---- | :---- |
| Learning | Discover reusable expertise |
| Reasoning | Generate candidate understanding |
| Execution | Apply approved guidance |
| Evaluation | Measure outcomes |
| Memory | Preserve experience |
| Knowledge | Preserve generalized understanding |

Each capability evolves independently.

### **16.13 The Final Principle: Improved Judgment**

One final architectural principle unifies the Common Core and all specializations:

**Execution is not the goal; improved judgment is the goal.**

The DLE exists to improve the quality of future decisions. Execution is simply one mechanism by which those decisions affect the world and generate new evidence. A successful DLE is not one that executes everything automatically; it is one that knows **when** to execute, **when** to ask for help, and **when** to admit uncertainty.

In an operational specialization, this explains why recommendation mode is a valuable operational state rather than merely a stepping stone to automation. In a writing specialization, it explains why collaboration with the author is often preferable to autonomous editing. More broadly, autonomy should be viewed as an optimization variable — not as the objective of the architecture itself.

### **16.14 Summary**

The Execution Architecture defines how learned knowledge safely influences evolving artifacts. By separating learning from execution and introducing progressive autonomy, execution adapters, execution policies, and continual evaluation, the DLE remains both adaptable and trustworthy.

---

## **Chapter 17 — Experimentation**

*(Reinstated from Version 1's Experimental Methodology chapter. The meta-learning loop of Chapter 12 states that the DLE learns about itself; this chapter defines the discipline under which that learning occurs. It is domain-agnostic and therefore belongs in the Common Core.)*

### **17.1 Introduction**

The engine's own evolution must follow the same principle as its learning: knowledge from evidence, not assumption (Axiom 4). Every architectural decision, learning strategy, reasoning artifact, weighting model, and policy-synthesis algorithm is a **hypothesis**, not a permanent truth. The DLE therefore evolves through controlled experimentation rather than uncontrolled modification.

### **17.2 Two Levels of Learning**

The DLE learns at two levels simultaneously, and the architecture keeps them separate:

1. **Learning about the domain** — how experts route work, revise manuscripts, refactor code; which intents recur; which policies hold.  
2. **Learning about itself** — whether evidence should be weighted differently; whether retrieval is improving explanations; whether one reasoning artifact outperforms another; whether two hypotheses should be merged.

These correspond to the learning and meta-learning loops of Chapter 12\. Confusing the two levels leads to unstable behavior: a change to the engine can masquerade as a change in the organization, and vice versa.

### **17.3 Every Change Is an Experiment**

A new reasoning artifact, a different learner in the marketplace, a revised weighting model, a new evidence source, a new feature of context enrichment — each introduces uncertainty. Rather than immediately replacing behavior, the DLE compares old and new against identical historical experience. Changes to the engine follow the same scientific cycle the engine applies to its domain: observe, hypothesize, design an experiment, collect evidence, evaluate, revise.

### **17.4 Experiment Records**

Every experiment is a persistent knowledge object. An **Experiment Record** contains:

- the hypothesis being tested,  
- the configuration under test,  
- the replay dataset (or operational scope) used,  
- the metrics collected,  
- the success criteria, declared in advance,  
- the rollback conditions, declared in advance,  
- the conclusion.

Because experiment records persist, the engine's own evolution acquires the same properties as its domain knowledge: provenance, explainability, and replayability.

### **17.5 Replay Before Production**

Replay is the primary validation mechanism. For any candidate change the first question is retrospective: *if this change had been active during a representative historical window, what would it have produced? Would it have agreed more often with experts? Would it have produced fewer contradictions?* Replay is the first stage of validation; production is the second. No change reaches operational behavior on the strength of plausibility alone.

### **17.6 Controlled Operational Experiments**

Some questions cannot be answered by replay — the effect of a recommendation on expert behavior, for example, only exists in production. Operational experiments therefore begin conservatively: a single policy family, a single organizational unit, a small fraction of eligible artifacts, with a control group where feasible. Every operational experiment must possess a hypothesis, success criteria, rollback conditions, and evaluation metrics. **Execution without measurement provides little organizational learning.** The specific rollout mechanics are domain-specific and are defined in the specializations.

### **17.7 One Variable at a Time**

Experimental discipline discourages simultaneous changes. Modifying reasoning artifacts, retrieval, evidence weighting, and policy synthesis in one deployment makes interpretation impossible. **Experiments should isolate one variable.** Where multiple changes are unavoidable, they are recorded as distinct experiments with declared interactions.

### **17.8 Failed Experiments Are Knowledge**

Negative results remain valuable: an artifact revision that performed worse, a retriever that introduced noise, a weighting scheme that reduced reviewer agreement. **Unsuccessful experiments are preserved alongside successful ones.** A failed experiment prevents the same hypothesis from being silently retried, and often identifies the missing context that made it fail.

### **17.9 Learning Velocity**

The key meta-metric of the DLE is **learning velocity**: not "how many recommendations did the engine make?" but "**how much did organizational understanding increase?**" — indicated by new evidence incorporated, policies strengthened or correctly weakened, contradictions resolved, uncertainty reduced, and explanations improved. Execution metrics measure the execution system; learning velocity measures the learning system. A DLE whose learning velocity approaches zero is failing at its purpose regardless of how well it executes.

### **17.10 Summary**

The Experimentation chapter closes the meta-learning loop with discipline: every change to the engine is a hypothesis, validated first by replay and then by controlled operational experiments, recorded permanently whether it succeeds or fails, and measured by how much it increases the organization's understanding.

---

## **Chapter 18 — Knowledge Governance**

*(Reinstated from Version 1's proposed Security and Governance chapter. This is **knowledge governance**, not cybersecurity: it concerns authority over what the organization is allowed to learn and execute, not network or data security. Every production deployment will need these answers.)*

### **18.1 Introduction**

The DLE accumulates knowledge that influences real decisions. Authority over that knowledge is therefore an architectural concern. The Common Core does not prescribe a governance policy — organizations differ — but it defines the questions every deployment must answer and the properties any answer must satisfy: every governance decision must be recorded, attributable, auditable, and reversible.

### **18.2 Who May Designate Experts**

Evidence weighting depends on designated expertise (§11.6). Designating an expert is therefore an act of governance, not administration: it changes what the organization learns. Deployments must define who may designate experts, for which artifact domains, and for how long; designations are recorded as observations, and changes to them are themselves evidence.

### **18.3 Who May Change Evidence Weights**

The trust model (§11.6) is configurable — which means someone can configure it. Because a weighting change silently alters every downstream belief, weight changes require explicit authority, take effect as versioned configuration, and are announced through the engine's reporting. Historical replays use the weighting configuration active at the time.

### **18.4 Who Approves Policies for Execution**

A learned policy recommends; an execution policy authorizes (§16.7). The authority to move a policy family up the execution levels (§16.3) — from recommendation to assisted execution to automation — is a human governance decision, distinct from the evidence thresholds that make a policy eligible. Eligibility is earned by evidence; authorization is granted by people.

### **18.5 Auditing Knowledge Changes**

Because only the Learning capability writes persistent knowledge (§15.8) and every knowledge object carries provenance (§6.4), every rule change is auditable in principle. Governance makes it auditable in practice: deployments must be able to answer, for any policy, *who and what caused it to exist, change, or reach execution* — including the evidence, the experiments (Chapter 17), and the human approvals involved.

### **18.6 Rolling Back a Bad Policy**

When a policy misbehaves in execution, rollback must be immediate and must not require retraining or manual knowledge surgery. Rollback demotes the policy's execution authorization and, where necessary, reverts to a prior knowledge version (§6.6). The rollback itself becomes an observed episode (§16.9): a bad policy is evidence about the promotion process that produced it.

### **18.7 Conflicting Organizational Units**

Different organizational units may exhibit — and legitimately maintain — different practices. Governance determines whether the DLE learns unit-scoped policies, a shared policy with unit-specific conditions, or escalates the conflict for an organizational decision. The Common Core's position is descriptive (§11.12): the engine models the difference; the organization decides whether the difference should persist.

### **18.8 Permanent Expert Disagreement**

Two designated experts may disagree permanently. The engine preserves both behaviors as competing hypotheses with their evidence (§6.8); it does not adjudicate between experts. Governance provides the resolution paths: scope the policies to each expert's authority, escalate to a normative decision layered above the Common Core, or accept the coexistence explicitly. Permanent disagreement recorded honestly is better knowledge than a false consensus.

### **18.9 Summary**

Knowledge governance defines who may shape what the DLE learns and executes: expert designation, trust-model changes, execution approval, audit, rollback, and the handling of conflicting units and disagreeing experts. The architecture supplies the mechanisms — provenance, versioning, single-writer knowledge, execution policies; each organization supplies the authority structure.

---

## **Appendix G — Architectural Principles**

The following principles appear repeatedly throughout the architecture. They provide a useful mental model when extending or implementing the DLE.

### **History is immutable.**

Observations are never rewritten. Only understanding changes.

### **Evidence teaches.**

Reasoning alone never creates knowledge. Knowledge grows through evidence.

### **Reasoning proposes; evidence disposes. *(reinstated from V1)***

Reasoning components generate candidate explanations; only accumulated evidence determines whether they become organizational knowledge. This boundary prevents the system from confusing plausible reasoning with organizational truth.

### **Policies are hypotheses that have survived evidence. *(reinstated from V1)***

No policy is a permanent truth. Every policy remains a provisional explanation, continuously justified by evidence and open to revision as the organization evolves.

### **Intent owns decisions.**

Individual decisions rarely make sense in isolation. They should be understood as serving one or more active intents.

### **Episodes are the unit of learning.**

Experts rarely solve problems through isolated actions. The DLE learns from complete episodes rather than individual decisions whenever possible.

### **Memory and reasoning are separate.**

Memory preserves experience. Reasoning interprets experience. The two should evolve independently.

### **Learning never ends.**

Every recommendation, review, correction, and execution produces additional evidence. The DLE continuously refines its understanding.

### **Uncertainty is valuable.**

The DLE should preserve uncertainty rather than hiding it. Recognizing uncertainty often leads to better future observations.

### **Execution is another observation.**

Execution does not conclude the learning process. It begins the next learning cycle.

### **Human expertise is evidence.**

Expert behavior is the richest source of learning available to the DLE.

### **Domain knowledge belongs in specializations.**

The Common Core intentionally avoids introducing concepts specific to bugs, books, source code, or any other application domain.

---

## **Appendix H — Canonical Objects**

Throughout the Common Core, a small number of concepts appear repeatedly. These concepts form the vocabulary of the architecture.

| Object | Purpose |
| :---- | :---- |
| Artifact | The object being improved. |
| Artifact Version | An immutable snapshot of an artifact. |
| Episode | A sequence of work performed in pursuit of one or more intents. |
| Observation | A factual record of something that occurred. |
| Context | Information surrounding an observation that may influence decisions. |
| Intent | The objective motivating one or more decisions. |
| Decision | A transformation applied to an artifact. |
| Transition | The observable difference between two artifact versions. |
| Evidence | An observation deemed suitable for learning. |
| Hypothesis | A proposed explanation for recurring evidence. |
| Policy | A reusable pattern of expert behavior supported by evidence. |
| Belief | The DLE's current assessment of an inferred object. |
| Execution Guidance | Domain-specific instructions derived from one or more policies. |

---

## **Appendix I — Common Learning Patterns**

Certain workflows appear repeatedly across different domains. These patterns are useful when implementing new specializations.

### **Observation → Learning**

Observe expert behavior. Convert observations into evidence. Refine hypotheses and policies. Repeat.

### **Recommendation → Review**

Generate a recommendation. Allow a human to review it. Observe the outcome. Use that outcome as evidence.

### **Progressive Autonomy**

Begin in observation mode. Progress to recommendation mode. Introduce assisted execution. Expand automation only where evidence supports it.

### **Replay**

Record complete episodes. Replay historical episodes using improved reasoning. Compare the results. Incorporate the improvements into future learning.

### **Declared Intent**

Whenever an expert explicitly states an objective, record it. Treat declared intent as authoritative. Learn how experts typically accomplish that objective.

### **Continuous Improvement**

Every completed episode produces new evidence. Every new piece of evidence has the potential to refine organizational knowledge. Learning is therefore continuous rather than periodic.

---

## **Closing Remarks**

The Decision Learning Engine is built around a simple observation: organizations express their expertise through the way their artifacts evolve.

Rather than asking experts to explain everything they know, the DLE observes what they do, reconstructs the intents behind their decisions, and gradually develops reusable organizational knowledge from accumulated evidence.

The architecture deliberately separates observation, memory, reasoning, learning, execution, and evaluation so that each can evolve independently. This allows the DLE to adapt continuously while preserving the complete history from which it learns.

Although this document defines the Common Core, it does not prescribe how the architecture should be applied to any particular domain. The remaining RFCs demonstrate how these principles are specialized for concrete applications such as bug management and long-form writing.

---

## **Concept Inventory**

An exhaustive flat list of every distinct named concept, idea, and principle in the Version 2.1 Common Core (concepts reinstated from Version 1 are marked):

- Decision Learning Engine (DLE)  
- Common Core / specialization split (domain-agnostic core; BTA, AuthorLM, and future CodeLM, DesignLM, ResearchLM, IncidentLM specializations)  
- Design goal: Learn from Evidence (capability from evidence, not prompt complexity)  
- Design goal: Separate Learning from Execution  
- Design goal: Preserve History  
- Design goal: Explain Every Recommendation (traceability of recommendations)  
- Design goal: Support Human Collaboration  
- Design goal: Generalize Across Domains  
- Non-goal: not an autonomous agent (learning is the primary objective)  
- Non-goal: does not infer historical facts (observations are authoritative)  
- Non-goal: not a replacement for human expertise (humans are ultimate authority)  
- Non-goal: does not search for universal rules (experts may legitimately differ)  
- Non-goal: not tied to Large Language Models (survives AI technology change)  
- Non-goal: not optimized for maximum automation (recommendations often beat autonomy)  
- Expertise embedded in artifact evolution (implicit answers: why changed, why now, why this way)  
- Artifact (versioned object evolving through expert decisions; observable state, revision history, improvement over time)  
- Artifact Version (immutable snapshot; DLE reasons over versions, not mutable objects)  
- Context (distinct from artifact; computed rather than observed)  
- Local / Global / Historical context layers (categories guiding retrieval, not first-class objects)  
- Intent (first-class concept; the "why" of decisions; immediate objective)  
- Declared Intent vs. Inferred Intent (declared \= authoritative observation; inferred \= hypothesis until evidenced)  
- Intent as a shared object between human and DLE  
- Strategy → Intent → Decision layering (Strategy as contextual metadata, deliberately not first-class)  
- Decision (intentional transformation; evaluated relative to artifact, context, intent; smallest meaningful unit of expert behavior)  
- Transition (observable effect of one decision; Version N → Decision → Version N+1)  
- Observation (factual, immutable, interpretation-free record)  
- Evidence (observation \+ context \+ provenance \+ authority; sole source of persistent learning; never regenerated)  
- Evidence Processor (evaluates decisions via provenance, authority, consistency, context, outcomes)  
- Hypothesis (candidate explanation; hypotheses compete; evidence decides survival)  
- Policy (evidence-backed reusable behavior; conditional, revisable, never immutable truth)  
- Belief / Belief Record (confidence, maturity, supporting/contradictory evidence, uncertainty, provenance, outstanding questions, drift indicators)  
- Knowledge (persistent policies, beliefs, explanations, provenance; provisional, not absolute; "data records, knowledge explains what proves useful")  
- Episode (first-class object; bounded, goal-directed sequence of expert activity; inserted between Artifact and Intent in ontology, pipeline, representation model, Knowledge Graph, and canonical objects)  
- Episode boundaries (intent completion, stable state, superseding intent, inactivity, explicit closure; explicit or inferred)  
- Episode composition and outcomes (outcomes become evidence)  
- Episodes as the unit of learning (workflows, not isolated edits)  
- Intent owns decisions (inverted hierarchy; "what sequence of decisions serves the same intent?")  
- Goal-directed episodes / reinforcement-learning analogy (intent ≈ episode objective, decisions ≈ actions)  
- Active intent instances (maintained during evolution, not only retrospective)  
- Artifact Domains (operational, knowledge, software, educational, creative) with domain-typical intents  
- Common artifact lifecycle (Artifact → Observation → Intent → Decision → Transition → Evidence → Knowledge → Improved Artifact)  
- Learning Architecture (renamed from Knowledge Architecture; transforms experience into knowledge)  
- Learning Pipeline (Artifact → Observation → Context → Episode → Intent → Decision → Transition → Evidence → Hypothesis → Policy → Execution Guidance; each stage adds semantics; nothing discarded)  
- Decision \= f(Context, Intent) (central mathematical abstraction; DLE approximates this function from evidence)  
- Learning three things simultaneously: what (decisions), why (intent), when (context)  
- "Models reason. Evidence teaches. Knowledge persists."  
- Continuous (non-episodic) learning — never a final state  
- Logical Architecture (roles/responsibilities, deliberately pre-software; renamed from System Architecture)  
- Architectural principles: Separation of Concerns, Replaceability, Traceability, Incrementality  
- Capabilities: Observation, Context, Intent, Decision, Knowledge, Reasoning, Execution, Evaluation  
- "Reasoning proposes. Knowledge evaluates."  
- "Execution engines consume knowledge; they do not own it."  
- Knowledge-flow cycle (Artifacts → Observation → Context → Intent → Decision → Knowledge → Execution Guidance → Execution → Evaluation → Knowledge; never terminates)  
- One responsibility per capability ("Observation records. Reasoning explains. Knowledge remembers. Execution acts. Evaluation learns.")  
- Representation Model (renamed from Knowledge Representation; represents experience at multiple abstraction levels)  
- Representation hierarchy (immutable lower layers, evolving upper layers; all levels preserved for reinterpretation)  
- Representation independence (of storage, language, reasoning engine, execution engine)  
- Knowledge Architecture / Knowledge Graph (all persistent objects as nodes; explicit relationships; records what and why)  
- Stable identity for knowledge objects  
- Mandatory provenance ("knowledge without provenance cannot be trusted")  
- Immutability of historical objects; evolution by addition, not mutation  
- Versioning of knowledge (replay, auditing, comparison, rollback, historical explanation)  
- Explanations as first-class knowledge objects  
- Competing knowledge / coexisting hypotheses (no forced convergence)  
- Belief evolution independent of policies  
- Retrieval of knowledge over raw observations (semantically meaningful objects, not documents)  
- Knowledge independence (from reasoning models, LLMs, storage, execution)  
- Domain Semantic Graph extension point (Artifact Semantic Graph; Concept Graph as domain instance; augments generic Knowledge Graph)  
- Inference Architecture (observation vs. inference vs. knowledge boundary)  
- What may be inferred (intent, hypotheses, explanations, missing context, policy applicability, future decisions, likely outcomes)  
- What may never be inferred (versions, timestamps, identities, recorded decisions, declared intent, human reviews)  
- Layers of inference (context, intent, hypothesis, policy, prediction)  
- Retrospective vs. prospective inference ("predicts well because it first learns to explain well")  
- Competing inferences preserved until evidence discriminates  
- Declared-over-inferred precedence ("inference supplements observation; it never replaces it")  
- Inference provenance (replayable, auditable)  
- Inference evolution (strengthen, weaken, split, merge, disappear)  
- "Inference proposes. Evidence validates. Knowledge remembers."  
- Knowledge Evolution ("never rewrites history; revises its interpretation of history")  
- Knowledge lifecycle (Observation → Evidence → Hypothesis → Candidate Policy → Validated Policy → Stable Policy → Retired Policy; governed by evidence, not time)  
- Promotion (inferred intent → accepted intent; hypothesis → candidate policy; candidate → validated)  
- Demotion ("demotion is not failure; it is learning")  
- Knowledge drift monitoring (drift indicates changing evidence, not incorrectness)  
- Contradictions as valuable, preserved signals  
- Policy retirement (retired knowledge remains historically accessible)  
- Replay (reinterpreting immutable history with newer reasoning)  
- Human participation in knowledge evolution (humans contribute evidence; DLE updates knowledge)  
- Three kinds of evolution: artifact, knowledge, expert  
- Expert Evolution principle (learning from a moving target; adaptation, not convergence)  
- Intent Lifecycle (created, inferred, refined, completed, retired; replaces Decision Lifecycle — decisions are instantaneous, intents persist)  
- Intent activation (multiple simultaneous active intents allowed)  
- Intent refinement (increasing specificity over time)  
- Intent completion (non-permanent; reactivation possible)  
- Intent splitting and merging (decomposition; anti-fragmentation)  
- Intent drift  
- Intent hierarchies (e.g., Improve Reader Understanding → Introduce Trajectories / Clarify Fields / Prepare Mathematics; Reduce Customer Impact → Escalate / Route / Prioritize)  
- Intent provenance (intent as replayable knowledge object)  
- Belief and Confidence architecture (facts vs. beliefs; only inferential objects possess beliefs)  
- Confidence as a property of inference, not only of policies (beliefs about intent, context, hypotheses, policies, future outcomes)  
- Confidence ≠ probability (present level of justification)  
- Maturity as a dimension distinct from confidence  
- Uncertainty as valuable (directs future observation; not failure)  
- Belief revision (strengthen, weaken, split, merge, retire; historical belief states replayable)  
- Belief explainability ("What would change its mind?")  
- Descriptive vs. normative beliefs (Common Core stays empirical/descriptive; normative overlays belong to specializations)  
- Learning Loops (multiple interacting feedback loops at different scales)  
- Principle of Incremental Learning (incorporate evidence into existing beliefs; no rebuild from scratch)  
- Artifact Loop (exists even without the DLE)  
- Knowledge Loop (episodes → evidence → hypotheses → policies → belief revision)  
- Execution Loop (policies → guidance → actions → new episodes; self-reinforcing)  
- Human Collaboration Loop (participation supplies evidence, not direct knowledge edits)  
- Replay Loop (improvement without operational risk)  
- Self-Improvement Loop (prompt/retrieval/policy/explanation/prediction quality as evidence about the DLE itself)  
- Multiple time scales (immediate/daily/weekly/monthly/quarterly cadences kept separate)  
- Loop stability (different reaction speeds by layer)  
- Learning hierarchy (Artifacts → Episodes → Knowledge → Execution → Evaluation → Improved Learning System)  
- Nested loops alternating work and learning (Work → Observe → Learn → Guide Work)  
- Operational / Learning / Meta-learning loop separation  
- "Better because each loop gives it better evidence to reason with, not because it reasons more"  
- Memory Architecture (multiple kinds of memory differing in persistence, abstraction, rate of change)  
- Episodic Memory (complete episodes; replay substrate)  
- Semantic Memory (generalized understanding; evolves slowly)  
- Working Memory (transient, task-scoped)  
- Intent Memory (recurring objectives across unrelated episodes)  
- Belief Memory (frequently changing inferred-understanding state)  
- Declarative vs. Procedural Memory (facts/knowledge vs. workflows/episode patterns/guidance)  
- Retrieval principle: maximum usefulness, not maximum recall  
- Memory evolution rates and memory independence from implementation  
- Experience → Memory → Reasoning → Knowledge (knowledge as memory at higher abstraction; Knowledge Architecture built on Memory Architecture)  
- Reasoning Architecture (LLMs only one possible implementation; future-proofing)  
- Reasoning responsibilities vs. non-responsibility (does not decide what becomes knowledge)  
- Retrieval before reasoning (interpreting prior experience, not rediscovering it)  
- Comparative rather than dogmatic reasoning (actively generate competing explanations)  
- Questions as valuable reasoning outputs (missing context, conflicting evidence, ambiguous intent)  
- Reasoning is ephemeral (only evaluated outputs persist)  
- Interpretive / Generative / Interrogative reasoning taxonomy  
- Observe → Interpret → Question → Generate → Evaluate ordering (reasoning as an act of understanding)  
- Architectural Interfaces (conceptual contracts, not software architecture)  
- Interface principles (canonical objects, determinism, provenance, no mutation of history, replayable, independently testable)  
- Named interfaces: Observation, Context, Intent, Decision, Evidence, Learning, Reasoning, Execution, Evaluation, Self-Improvement  
- Active Intent Set (output of the Intent Interface)  
- One-input/one-output capability discipline (simple external transformation, arbitrary internal sophistication; enables independent testing and uncoupled evolution)  
- Execution Architecture (how the DLE safely influences the world while learning)  
- Execution principles: Evidence Before Automation, Explainability, Progressive Autonomy, Human Collaboration, Replayability  
- Execution autonomy Levels 0–4 (Observation, Recommendations, Assisted Execution, Conditional Automation, Autonomous Execution; level is domain- and policy-category-specific)  
- Execution Guidance (operational form of knowledge; distinct from policy; constraints, confidence requirements, approvals, safety conditions)  
- Execution Adapter (translates generic guidance to domain operations: API requests, Markdown playbooks, document edits, workflow updates, command sequences)  
- Execution Policies vs. learned policies ("a learned policy recommends; an execution policy authorizes")  
- Outcomes as observations (inputs to future learning)  
- Rollback as an observed episode (history never erased)  
- Progressive autonomy driven by evidence, not elapsed time; independent progression per policy family  
- Execution completes the learning loop (Execution → Outcome → Observation → Evidence → Learning → Improved Execution)  
- Separation-of-concerns table (Learning / Reasoning / Execution / Evaluation / Memory / Knowledge)  
- "Execution is not the goal; improved judgment is the goal" (knowing when to execute, ask for help, admit uncertainty; autonomy as optimization variable)  
- Appendix G principles: History is immutable; Evidence teaches; Intent owns decisions; Episodes are the unit of learning; Memory and reasoning are separate; Learning never ends; Uncertainty is valuable; Execution is another observation; Human expertise is evidence; Domain knowledge belongs in specializations  
- Canonical Objects catalog (Artifact, Artifact Version, Episode, Observation, Context, Intent, Decision, Transition, Evidence, Hypothesis, Policy, Belief, Execution Guidance)  
- Common Learning Patterns: Observation → Learning; Recommendation → Review; Progressive Autonomy; Replay; Declared Intent; Continuous Improvement  
- Guiding principles (Ch. 1.11): expertise expressed through decisions; decisions pursue intent; evidence creates knowledge; knowledge remains explainable; learning is continuous; execution separate from learning; history immutable, understanding evolves  
- Closing thesis: organizations express expertise through how their artifacts evolve  
- Core Axioms 1–8 (§1.12) — numbered, citable axiom block: Expertise, Observability, Intent, Evidence, Knowledge, Separation, Explainability, Continuity (reinstated from V1)  
- Marketplace of Learners (§14.6) — multiple independent learners (case-based, rule mining, Bayesian, online, statistical, LLM) proposing hypotheses in parallel (reinstated from V1)  
- Hypothesis Aggregator — combines learner proposals before policy evaluation; no learner possesses authority (reinstated from V1)  
- Learner agreement as a confidence signal; learner disagreement identifies where to collect more evidence (reinstated from V1)  
- Chapter 17 — Experimentation: every change to the engine is a hypothesis (reinstated from V1)  
- Two levels of learning — learning about the domain vs. learning about itself, mapped to the learning and meta-learning loops (reinstated from V1)  
- Experiment Record — hypothesis, configuration, replay dataset, metrics, success criteria, rollback conditions, conclusion; persistent knowledge object (reinstated from V1)  
- Replay before production — retrospective validation precedes operational deployment (reinstated from V1)  
- Controlled operational experiments — single policy family / unit / small fraction with control group; hypothesis, success criteria, rollback conditions, metrics required (reinstated from V1)  
- One-variable-at-a-time experimental discipline (reinstated from V1)  
- Failed experiments preserved as knowledge (reinstated from V1)  
- Learning velocity — the key meta-metric: how much organizational understanding increased, distinct from execution metrics (reinstated from V1)  
- Chapter 18 — Knowledge Governance (not cybersecurity): authority over what the organization learns and executes (reinstated from V1)  
- Governance questions: who designates experts; who changes evidence weights; who approves execution; audit of knowledge changes; rollback of a bad policy; conflicting organizational units; permanent expert disagreement (reinstated from V1)  
- "Eligibility is earned by evidence; authorization is granted by people" (reinstated from V1)  
- Rollback as evidence about the promotion process that produced the bad policy (reinstated from V1)  
- Abstention as a valid output (§16.4) — insufficient/conflicting evidence, novel situations, unrecognized state; preferable to an unreliable recommendation (reinstated from V1)  
- Forgetting / recency-weighted evidence (§8.8) — history immutable, influence decays; the mechanism beneath drift; historical knowledge vs. current organizational practice (reinstated from V1)  
- Configurable trust models (§11.6) — evidence collection separated from evidence weighting; organizations differ; trust models never hard-coded (reinstated from V1)  
- Single-writer discipline (§15.8) — only the Learning capability modifies persistent knowledge (reinstated from V1)  
- The compiler analogy (§8.4) — evidence \= source code; policies \= intermediate representation; execution guidance \= compiled artifact (reinstated from V1)  
- "False negatives are preferable to false positives" in promotion and execution (§8.5, §16.2) (reinstated from V1)  
- The four engineering-decision questions (§15.15) — does this improve evidence / explainability / replay / understanding; if all no, do not build it (reinstated from V1)  
- Reasoning-artifact versioning discipline (§15.12) — stable identifiers, versions, declared I/O schemas, replay evaluation before deployment, versions never overwritten (reinstated from V1)  
- Appendix G additions: "Reasoning proposes; evidence disposes"; "Policies are hypotheses that have survived evidence" (reinstated from V1)

	