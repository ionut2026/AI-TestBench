---
name: grf-mutation-testing
description: Runs and interprets GRF mutation testing on mock tiers to find tests that would miss injected defects.
---

# Mutation testing

**Mutation testing** deliberately injects a defect (a **mutant**) into a mock product and checks whether tests fail.
A test that still passes has "let the mutant survive" and may have weak assertions.

## Steps

1. Confirm a non-evidence mock tier and inspect `catalog\mutants.toml` (or the configured mutants file).
2. Run `grf mutate --tier <mock-tier> --outdir results\mutation-<name>`. Use `--mutant <id>` for one mutant.
3. Explain the baseline: selected tests must pass with no fault injected before mutants are meaningful.
4. Explain the score: killed mutants divided by meaningful mutants. The default minimum is `0.9`.
5. For each survivor, identify the missing or weak assertion and propose a specification-based test change.
6. Treat setup, teardown, process errors and incomplete runs as problems to fix, not killed mutants.

## Safety

- Mutation testing is only for non-evidence mock tiers.
- Never inject faults into a real product, rig or instrument; real-product checks require the instrument owner's
  authorization. Mutation testing does not need RV&S; any RV&S access remains read-only.
- A high mutation score improves confidence in tests; it is not product evidence or approval.
- Do not weaken mutants or lower `--min-score` without a documented human decision.
