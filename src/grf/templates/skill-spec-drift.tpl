---
name: grf-spec-drift
description: Explains and resolves GRF specification drift with ingest, drift, accept and migrate-hashes while preserving human decisions.
---

# Handle specification drift

Use this skill when `grf drift` reports stale, orphaned, untagged or uncovered tests, changed RV&S links/states,
unrecorded reviews, or after re-ingesting specifications.

## Commands

- `grf ingest`: refreshes the local catalog from read-only RV&S data.
- `grf drift --strict`: compares suites with the catalog; exit code 1 means action is needed.
- `grf accept <id>`: accepts changed RV&S state or links as the new baseline. It does not accept changed text.
- `grf migrate-hashes --from <old-catalog.json>`: only for a hashing algorithm change with unchanged text.

## Procedure

1. Run `grf drift --strict` and group findings by specification ID.
2. Changed specification text: regenerate the brief, update the test against the new text, use the new hash,
   keep or restore `review:pending`, and request a new human review.
3. State/link changes only: show the change and get a human decision before `grf accept`.
4. Orphans or uncovered specifications: report them; never delete tests or invent coverage automatically.
5. Re-run lint and drift. Report remaining findings and human decisions needed.

## Safety

- Never auto-update stale hashes or migrate hashes when text changed.
- Agent review never replaces current-hash human approval.
- RV&S access is read-only; do not copy product exports into GRF core.
- Drift checks and mock runs are not product evidence. Real-product or instrument checks require the instrument
  owner's authorization.
