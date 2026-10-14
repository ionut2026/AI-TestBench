---
name: grf-review-test
description: Independently reviews GRF Robot tests for traceability, false passes and false failures without approving them.
---

# Review a GRF test

Use this skill when asked to review a drafted or changed Robot test. The project's `test-reviewer` agent defines
the role; this skill is the checklist.

## Checklist

1. Read the current specification independently of the test: the brief or read-only RV&S data.
2. Map every required outcome and constraint to an assertion, and every assertion back to a clause.
3. False passes: old evidence, broad matches, reused events, erased timelines, swallowed errors, conditional
   assertions and too-short negative observation windows.
4. False failures: invented constraints, wrong field names, short timeouts, unverified preconditions and state
   leaking between tests.
5. Tags and hashes: specification tag, current `spechash:`, capability tags and `review:pending`.
6. Isolation and readability: explicit setup/teardown, cleanup, tier timeout variables and clear documentation.
7. Run only safe non-evidence checks (`grf lint`, `grf drift --strict`, mock tier) when useful.

## Output

Give APPROVE, CHANGES REQUESTED or REJECT with file/line findings and specification evidence.
State that an agent verdict is information only.

## Safety

- Do not edit the test or review ledger during review.
- Never record human approval or remove `review:pending`.
- Mock passes are not product evidence. RV&S access is read-only.
- Do not run real-product, rig or instrument tiers without the instrument owner's authorization.
