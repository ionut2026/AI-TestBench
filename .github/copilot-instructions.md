# Copilot working instructions

- Work in the assigned GRF checkout on D:, including an app-managed isolated worktree when assigned.
  Keep task output, scratch files and caches in that checkout's ignored `results` directory, never on C:.
- Preserve the direct `D:\projects\GeneralRobotFramework` main checkout and all uncommitted user work.
  Honor the user's selected workflow: direct-main commits or isolated branches/PRs, only for affected repositories.
  For direct-main work, use one writer per repository; inspect dirty state and ownership first, do not switch
  or clean up user-owned dirty main, and ask on conflicts. Do not create branches/PRs against the user's choice
  or edit another session's checkout. Follow the shared integration procedure and coordinate a safe handoff.
- Use the `main-agent-orchestrator` agent and matching skill for requested coordination. Instructions do
  not spawn workers, reconnect sessions, grant permission or authenticate approval. Discover available client
  capabilities first; if session tools are absent, report the limitation and use explicit handoffs.
- Push, merge and tag only with explicit authorization matching the repository, change and action.
  Approval for a previous deployment or PR is not permission for a new PR or release. Preserve branch
  protections; never force-push, bypass review or treat agent review as human approval.
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
