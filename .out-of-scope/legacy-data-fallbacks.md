# Fallbacks for legacy data shapes

AuthorLM does not carry code paths whose only job is to keep reading an
older on-disk or in-database shape after the format has moved on.

## Why this is out of scope

The project is a prototype with one author and a handful of manuscripts.
When a format changes, the new shape replaces the old one cleanly and any
live data is migrated by hand, once. A fallback that keeps the old shape
readable forever adds a second lookup path to every caller, hides
unmigrated data instead of surfacing it, and outlives the data it was
written for.

Before accepting a fix whose substance is "also accept the old shape",
check whether any live manuscript actually holds that shape. If none
does, there is nothing to fall back to. If some does, migrate it.

## Prior requests

- nagarkar/authorllm#49 — "Fix prune/Doc-pull dropping pre-key inline
  illustration art": a desc_hash lookup for illustration slots that
  predate slot keys. On 2026-09-29, SMSTTD, DON and testbench held one
  keyless slot and no keyless slot with legacy art.
