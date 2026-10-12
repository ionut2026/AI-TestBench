# Test authoring rules

- The specification is the oracle, not the mock or existing tests.
- Name the precondition, stimulus and observable outcome in each test.
- Carry the configured specification tag and current spechash, plus review:pending until human approval.
- Quote the specification in Documentation. Use nospec:<kind> only for explicitly unspecified behaviour.
- Do not Sleep. Wait for observable changes; use tier variables for timeout/duration arguments.
- Declare capability tags for hardware, restarts, logs and operator steps according to grf.toml.
- Do not weaken assertions to match current product defects or fabricate a review.
- Mock passes are framework checks, never product evidence.
