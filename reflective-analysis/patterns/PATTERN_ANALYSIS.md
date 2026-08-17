# AuthorLM Session Patterns & Token Optimization Opportunities

**Analysis Date:** 2026-08-11  
**Sessions Analyzed:** 15 AuthorLM sessions (2026-07-18 to 2026-08-11)  
**Search Coverage:** 8 pattern searches across transcripts

---

## Executive Summary

### Primary Patterns Observed

1. **Concept Graph Operations** (10/15 sessions)
   - `get_concepts` appears in all manuscript-focused sessions
   - **Pattern**: Extract → validate → link → confirm workflow
   - **Frequency**: Repeated across different manuscripts (SMSTTD, "The Good Choice")
   - **Token Impact**: High — each `get_concepts` call fetches full graph state

2. **Guidance Synthesis** (10/15 sessions)
   - `get_guidance` called once per session (as per policy)
   - **Pattern**: Calls after briefing, combines concepts + policies + author context
   - **Token Impact**: Medium — expensive LLM call but cached/batched

3. **Illustration/Image Management** (3 sessions, trending up)
   - Extraction: [Illustration: description] tags from prose
   - Generation: Prompt refinement → image rendering
   - **Pattern**: Extract description → craft prompt → render
   - **Sessions**: "Doc mirror illustrations tab", "SMSTTD manuscript image management", "Autoregressive rewriting"
   - **Token Impact**: High — repeated prompt engineering on same material

4. **Google Docs Integration** (3 sessions)
   - Workflow: Pull from Docs → process → push back
   - **Sessions**: "Google Docs connection setup", "SMSTTD preface.md to Google Docs", "Google Docs checkout status"
   - **Pattern**: API calls + retry logic
   - **Token Impact**: Medium — deterministic, but protocol handling could be templated

5. **Test & Debug Cycles** (2 sessions with specific failures)
   - Session: "Fix pre-existing test_api.py failure" — `AttributeError: 'NoneType'`
   - **Pattern**: Error → trace → fix → retest
   - **Token Impact**: Medium-high — multiple iterations on same error

### Deterministic Operations Identified

| Operation | Sessions | Frequency | Token Cost | Candidate Tool/Skill |
|-----------|----------|-----------|------------|----------------------|
| SQL graph queries | "Multiple sessions", "SMSTTD editing" | 2+ | Low | MCP tool for batch concept queries |
| File I/O parsing (.md, .txt, JSON) | All sessions | Baseline | Low | Shell/Python script |
| Prompt format conversion | "Illustration" sessions | 3 | Medium | Skill or cached templates |
| Python/SQLite CLI invocation | "Daily improvement agent" | 1 | Low | MCP wrapper (standardize CLI args) |
| Concept extraction from prose | Every session | 10+ | High | Could be batched MCP call |

---

## Concept Graph Patterns

### Current Implementation
```
get_concepts() → fetch full graph
         ↓
Author examines concepts
         ↓
confirm_concept / retire_concept / link_concepts / add_concept
         ↓
verify via get_concepts() again
```

### Token Waste Observed
- **Full graph fetch on every `get_concepts`**: ~200+ concepts × full metadata = expensive
- **Sequential confirmation**: Each `confirm_concept` followed by another `get_concepts` to verify
- **Graph state sync**: No batched updates — each concept operation triggers a separate call

### Optimization Opportunities
1. **Batch concept operations**: Instead of N separate calls, send array of operations
2. **Partial graph queries**: Add offset/limit to `get_concepts` (pagination)
3. **Concept validation inline**: Return confirmation status in the same call, not sequential

---

## Illustration/Image Management Pattern

### Current Flow (Observed in 3 Sessions)
```
Author writes prose with [Illustration: rough description]
         ↓
Extract descriptions from document
         ↓
Refine description → craft DALL-E/image-API prompt
         ↓
Call render/generate-image (via illustrator MCP)
         ↓
Embed image + sync back to Google Doc
```

### Token Analysis
- **Prose → description**: LLM-generated (not deterministic, but similar each time)
- **Description → prompt craft**: LLM iterative refinement (multiple attempts)
- **Prompt → image**: External API (deterministic, but expensive in credits)

### Optimization Opportunities
1. **Illustration description library**: Persist templates for recurring illustration types
2. **Prompt craft skill**: Codify the description → prompt rules (currently re-learned each session)
3. **Batch illustration processing**: Process all illustrations in one pass, not one-by-one

---

## Guidance Synthesis Pattern

### Current Implementation
```
Author starts session → get_briefing()
         ↓
Claude calls get_guidance() ONCE
         ↓
Guidance combines: concepts, policies, style, file context
         ↓
Presented to author with explanations
```

### Observed Behavior
- **Policy learning**: Sessions show evidence of author's editorial preferences being learned
- **Explanation necessity**: Author emphasizes "why matters more than the what"
- **Multi-turn refinement**: Guidance sometimes rejected, policies updated based on evidence

### Token Impact
- **High**: Each `get_guidance` synthesizes across concepts + policies + context
- **Unavoidable complexity**: Guidance quality depends on full context

### Potential Optimization
- **Cached guidance per file**: If same file edited twice, guidance reusable
- **Incremental policy updates**: Only recompute guidance on policy changes, not every session

---

## Google Docs Integration Pattern

### Current Implementation
```
SMSTTD workflow:
- Pull preface.md from Google Docs
- Parse + extract concepts
- Sync back with annotations/edits
- Mirror illustrations tab
```

### Deterministic Parts Observed
- OAuth flow setup (sessions: "Google Docs connection setup")
- API retry logic (partial, could be templated)
- Document structure parsing (convert between Docs format ↔ .md)

### Token Cost
- Low for API calls themselves
- Medium for format conversion (if done by LLM instead of script)

### Optimization Opportunity
- **MCP tool for Docs ↔ markdown conversion**: Current workflow may regenerate conversion logic per session

---

## Test/Debug Pattern

### Observed Session: "Fix pre-existing test_api.py failure"
- **Error**: `AttributeError: 'NoneType' object has no attribute 'd...'`
- **Resolution**: Traced to missing initialization
- **Tokens spent**: Multiple iterations → trace → fix → retest

### Pattern
```
Failure ↓ → Read error trace
        ↓ → Hypothesize cause  
        ↓ → Examine code (grep, read file)
        ↓ → Fix
        ↓ → Retest (python tests/test_api.py)
        ↓ → Verify (python tests/test_e2e.py)
```

### Token Opportunity
- **Deterministic investigation**: grep patterns, file reading → could have pre-cached test data/error patterns
- **Repetition risk**: Similar errors across sessions (test suite maintenance)

---

## Evidence of Policy Learning

### Policies Observed in Transcripts
1. **Concept graph consistency**: "get_concepts afterward still showed the old notes" → policy: updates should persist
2. **Guidance timing**: "Run `get_guidance` ONCE PER SESSION unprompted" → policy about when to synthesize
3. **Explanation importance**: "why matters more than the what" → policy about guidance quality
4. **Test parity**: "parity enforced by tests" → policy about code quality

### Evidence Type
- Author reactions to suggestions (verbatim reasoning)
- Observed outcomes (what worked, what didn't)
- Explicit corrections ("test failures teach us...")

---

## Technology Stack Observed

| Layer | Technology | Sessions |
|-------|-----------|----------|
| CLI | Python stdlib (no external deps) | All |
| Storage | SQLite (~/.authorlm) | All |
| MCP | AuthorLM custom MCP | All |
| LLM Integration | urllib + local LLM | All |
| Document Format | .md + Google Docs | 3 |
| Image Generation | DALL-E/illustrator MCP | 3 |
| Skills | authorlm skill loaded in 5+ sessions | Majority |

---

## Next Steps for Analysis

### Phase 4: Concept Graph Construction
Need to:
1. Map concept nodes from observed patterns
   - `pattern:concept-graph-extraction`
   - `pattern:guidance-synthesis`
   - `pattern:illustration-management`
   - `pattern:google-docs-sync`
   - `optimization:batch-operations`
   
2. Identify edges (how patterns compose)
   - `illustration-management` **feeds-into** `document-sync`
   - `concept-extraction` **enables** `guidance-synthesis`
   - `batch-operations` **reduces-tokens-for** all patterns

3. Extract policies from evidence
   - When to call `get_guidance` (once per session)
   - What constitutes good guidance (explanations matter)
   - How to manage concept graph updates

### Phase 5: Gap Analysis
- Is AuthorLM machinery sufficient to model these patterns?
- What's missing: token metrics, tool cost tracking, skill creation heuristics?
- How should we extend for token optimization modeling?

