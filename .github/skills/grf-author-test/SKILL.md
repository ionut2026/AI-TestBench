---
name: grf-author-test
description: Turns a GRF generation brief into a specification-traced Robot Framework test that remains pending human review.
---

# Author a GRF test

Use this skill when writing or changing a Robot test from a GRF brief. The project's `test-author` agent defines
the role; this skill is the step-by-step procedure.

## Steps

1. Read `grf.toml`, the project README, `catalog\brief-rules.md` and the complete brief in the configured briefs
   directory (usually `generated\briefs\<PREFIX>-<id>.md`). If no brief exists, run `grf briefs --spec <id>`.
2. Treat the specification as the oracle. The mock, current implementation and existing tests are not.
3. Split the specification into preconditions, stimulus, observable outcomes, constraints and open values.
   Add negative cases for "only when", repeated stimuli for "each time" and explicit order/timing checks.
4. Write into the brief's target suite using existing project keywords, tier timeout variables and capability tags.
   Never use fixed `Sleep`, hide failures, erase evidence or make the only meaningful assertion conditional.
5. Keep the specification tag and exact `spechash:` tag. Add `review:pending`. Cite the specification in
   `[Documentation]`. If an outcome cannot be observed, report the gap instead of inventing an assertion.
6. Run `grf lint`, then `grf drift --strict`, then a mock run with a new `--outdir`.
7. Report covered clauses, limitations, commands run, outcomes and any suspected product defect.
8. Update all affected product documentation in the same change.

## Safety

- Mock passes are useful checks of the test; they are not product evidence.
- RV&S access is read-only.
- Real-product or instrument runs require the instrument owner's authorization.
- Never remove `review:pending`, run `grf review` as a human, or fabricate approval.
