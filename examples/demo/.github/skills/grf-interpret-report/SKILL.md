---
name: grf-interpret-report
description: Explains GRF and Robot Framework run results and classifies failures before anyone changes tests or product code.
---

# Interpret a GRF report

Use this skill when a `grf run`, `grf report`, CI job or Robot report fails or is unclear.

## Read the outputs

- `report.html`: Robot's overview of suites and tests.
- `log.html`: each keyword step, argument, message and failure.
- `traceability.html` / `traceability.json`: GRF's specification links, tier, review status and verdicts.
- `output.xml`: the raw machine-readable result used to build the other reports.

## Classify the problem

1. **Command or setup:** invalid options, missing dependencies, wrong project, existing `output.xml`.
2. **Environment:** unavailable tier prerequisites; run `grf doctor --tier <tier>` when safe.
3. **Test defect:** wrong keyword, invented expectation, poor timing or missing cleanup.
4. **Mock gap:** the fake product does not model the specified behavior.
5. **Specification drift or review state:** stale hash, changed links, missing current human approval.
6. **Suspected product defect:** only from an authorized evidence tier and confirmed against the specification.

Quote the first meaningful error, name the evidence used, and propose the smallest next diagnostic step.

## Safety

- Mock, dry-run and non-evidence results are never product evidence.
- VERIFIED needs an evidence tier, current hashes, recorded human approval and no relevant drift.
- Do not weaken assertions or mark failures known just to make a report pass.
- Real-product or instrument reruns require the instrument owner's authorization. RV&S access is read-only.
