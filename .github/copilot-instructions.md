# Copilot working instructions

- Always work on the D: drive, specifically `D:\projects\GeneralRobotFramework` (the root of this
  repository). Never read from or write to the C: drive for task output, scratch files,
  or any other work related to this project.
- Use this direct checkout on `main`; do not create a worktree or switch branches unless the user requests it.
- The user is new to Robot Framework. Explain concepts in plain language, define unfamiliar terms on first
  use, and use small examples from this project's demo. Explain what a command does, why it is needed, where
  to run it, and what successful output looks like. Distinguish Robot Framework, GRF and product code.
  Introduce advanced topics gradually; do not assume prior test-automation knowledge or claim mock passes
  prove a real product works. Keep beginner explanations in `docs\GETTING-STARTED.md` and link from reference guides.
- After every code change, update all relevant documentation in the same change: README, applicable guides
  in `docs`, CONTRIBUTING, user-visible CHANGELOG entries, and affected starter templates/example documentation.
  Document behavior, configuration, CLI, API and workflow changes, including migration notes for compatibility
  changes. Keep each topic in its canonical guide and link to it rather than duplicating instructions.
  If a file needs no update, verify that its existing documentation remains accurate.
- Keep scratch files, dependency caches and validation output under this checkout's ignored `results` directory.
- Raw RV&S/MCP exports do not belong in core. Do not recreate the removed `exports` directory or commit
  product-specific reports. The external server's export destination is controlled separately by
  `RVS_EXPORT_DIR`; do not change the user's MCP registration.

- This checkout is now GeneralRobotFramework (remote renamed from AI-TestBench). The generic Python package is
  at `src\grf`. SMM and Windchill have moved to `ionut2026/SMMAutomationFramework` and
  `ionut2026/WindchillMcpServer`; do not add product code back into core.
- Product test runs and instrument-affecting actions require the instrument owner's authorization.
- Validate generic changes with isolated pytest, ruff, mypy and the synthetic starter. Mock results are never
  product evidence; agent review never replaces current-hash human approval.
