---
name: grf-doctor-tier
description: Checks whether a GRF tier is ready before a run and explains doctor results without unauthorized product actions.
---

# Check tier readiness

A **tier** is a configured execution target, such as a mock or real rig. `grf doctor` checks prerequisites
before a long run.

## Steps

1. Read `grf.toml` and explain the selected tier: environment file, evidence flag, capabilities and parallel setting.
2. Run `grf doctor --tier <tier>` for static checks. Exit code 1 means at least one FAIL must be fixed.
3. Explain each PASS, WARN and FAIL with the next safe diagnostic step.
4. Use `--deep` only when the product project documents what it starts or connects to and the run is safe.

## Safety

- `--deep` may start an environment or connect to a product or instrument.
- Real-product, rig, instrument or disruptive checks require the instrument owner's authorization.
- A successful doctor check means the tier appears runnable; it is not test or product evidence.
- RV&S access is read-only.
