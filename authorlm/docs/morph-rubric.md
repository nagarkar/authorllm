*Reference standard for the morph combined detection call. Applied to
one paragraph at a time, given its position window and any concept
note that names it (see the harness for exact assembly).*

### Flag — Claim Before Evidence

The paragraph asserts a claim — especially a strong or universal one
("the only", "always", "no other") — with nothing in its context
window that supports it: no example, no cited source, no argument
carrying the weight of the claim.

Before flagging, decide which of these four shapes the claim actually
has — only the fourth is a finding:

1. **Supported in the window** — the claim's own sentence, or a
   neighbor, already carries an example, source, or argument for it.
   Not a finding.
2. **A stated thesis or preview** — the claim is explicitly framed as
   what the essay/section is about to argue ("this essay will show...",
   an opening or section-heading claim), not asserted as already
   established. Not a finding — a thesis doesn't need to justify itself
   at the point it's first stated.
3. **Evidence explicitly deferred** — the paragraph itself says the
   support is coming later or lives elsewhere ("as the next section
   shows...", "see Chapter 4"). Not a finding.
4. **Genuinely missing** — none of the above; the claim is asserted as
   already true, with no support anywhere in view and no signal that
   support is coming. This is the finding.

> Flag example: "Repeated action is the only path to virtue; a person
> becomes brave solely by performing brave acts over and over, and no
> other route to courage exists." — asserted flatly (shape 4), nothing
> before or after it gives a reason to believe it, and nothing frames
> it as a thesis or defers its support.

> Keep example: "Repeated action shapes virtue — Aristotle's own case
> is a person who becomes brave by first acting bravely under
> instruction, before courage is felt as a disposition." — shape 1: the
> claim is the same weight, but the sentence carries its own support.

Abstain (`present: false`) whenever the claim is shape 1, 2, or 3 —
not only when there's no claim at all.

### Flag — Discontinuity

The paragraph does not follow from the one immediately before it — no
transition, no shared thread, a reader would stop and reread the
previous paragraph to check they didn't skip something.

> Flag example: a paragraph on habit and virtue, immediately followed
> by "Meanwhile, in the 1960s, American urban planners began
> redesigning highway systems around the automobile" with nothing
> connecting the two.

> Keep example: the same jump, but opened with "This same pattern — a
> single-minded commitment producing a result no one designed — shows
> up outside ethics too" before the highway material.

Abstain when a deliberate shift is explicitly signaled ("meanwhile,"
"by contrast," "turning to a different case") AND the shift still
serves the paragraph before it — a signaled jump can still be a
finding if the signal is the only thing doing the work and the
content itself never connects.

### Flag — Specificity

Vague, generic prose that gestures at a topic without engaging it —
often recognizable because it could be dropped into an essay on almost
any subject unchanged.

> Flag example: "Highway systems are really significant and have
> changed a lot over time."

> Keep example: "The interstate system didn't just move cars faster —
> it changed which land was worth building on, and who could afford to
> live far from where they worked."

Judge this one WITH the surrounding paragraphs in view, not alone —
whether something is vague is often a comparison: does this paragraph
engage the material with the same specificity its neighbors do, or
does it visibly retreat from it.

Your `judgment`/`reason` for this dimension must name BOTH what's
generic about the paragraph AND what concrete material — already in
the window, or well-known and directly relevant — would make it
specific. "Could be more specific" with no named contrast is not a
finding — abstain (`present: false`) rather than flag something you
can't ground this concretely.

### Flag — Concept Invalidation

The paragraph asserts something that contradicts a ratified concept
note given below it — not merely touches the same topic, but takes a
position the note has already settled against.

> Flag example, given the note "suburban expansion is caused primarily
> by federal mortgage-subsidy policy, not by highway construction;
> highway building followed and reinforced sprawl but did not
> originate it": a paragraph stating "the interstate highways were the
> single cause of suburban sprawl in America."

> Keep example, same note: a paragraph stating "the interstate
> highways accelerated a suburban shift that subsidy policy had
> already set in motion" — consistent with the note, not contradicting
> it.

**If no ratified concept claims were supplied for this paragraph's
window at all, abstain (`present: false`) for this dimension —** never
judge concept-invalidation against an empty authority. No claims
supplied means nothing to contradict, not license to guess from
general knowledge of the topic.

---

## When you find something: replacement or judgment

For every dimension flagged `present: true`, decide whether you can
also supply a `replacement` — the paragraph rewritten to fix the
defect — or whether you can only supply a `judgment` — a short note
naming what's missing, with no rewrite attempted.

Default expectation, per dimension — **don't default to caution across
the board; a `judgment` with no rewrite is a real cost to the author,
not the safe option, so only pay it where invention risk is real:**

- **Specificity**: default to `replacement`. Sharpening vague prose
  using the surrounding paragraphs or well-known general knowledge
  about the thing already named is not invention — it very rarely
  needs a `judgment`. Write the rewrite.
- **Discontinuity**: default to `replacement`. A bridging clause or
  reframed opening sentence uses content already present in the
  neighboring paragraphs — write it.
- **Concept Invalidation**: default to `replacement` whenever the
  ratified note already states the correct position (it usually does
  — that's what makes it "ratified"). Rewrite the paragraph to agree
  with the note's own content. Only fall back to `judgment` if fixing
  it would need something the note itself doesn't supply.
- **Claim Before Evidence**: default to `judgment`. This is the one
  where "the fix" is usually "supply the missing argument," which
  means inventing a fact, example, or citation the author never wrote
  — name what's missing instead of manufacturing it. The single
  exception: if the concept note or a neighboring paragraph already
  states the exact support this claim needs, use it and write a
  `replacement` — don't withhold a real, already-given answer just
  because this dimension defaults to caution.

The general test, when a dimension's default doesn't obviously apply:
would the rewrite use only material already in front of you — this
paragraph, its neighbors, the concept note, or general knowledge that
sharpens something already gestured at — or would it require you to
supply the actual missing argument from nothing? The first is a
`replacement`; the second is a `judgment`. But don't reach for
`judgment` reflexively — try the rewrite first, and only back off when
you notice you're inventing the author's argument rather than
expressing it better.

## Output format

Reply with ONLY a JSON object, no markdown fences. For each paragraph
given, exactly these four keys, spelled exactly this way —
`claim_before_evidence`, `discontinuity_with_prev`, `specificity`,
`concept_invalidation` — each holding:

```
{"present": true|false, "confidence": 0.0-1.0,
 "judgment": "short gist, only if present and no safe replacement",
 "replacement": "the paragraph rewritten, only if present and safe to draft"}
```

At most one of `judgment`/`replacement` per dimension; neither when
`present` is false. Do not rename, abbreviate, or add keys.
