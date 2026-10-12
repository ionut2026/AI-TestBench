# Copilot working instructions

- Always work on the D: drive, specifically `D:\projects\AI-TestBench` (the root of this
  repository). Never read from or write to the C: drive for task output, scratch files,
  or any other work related to this project.
- Save every file generated from Windchill RV&S / MCP server data (exports, reports, lists) in
  `D:\projects\AI-TestBench\exports`. `rvs export` does this automatically.

- This checkout is now GeneralRobotFramework (remote renamed from AI-TestBench). The generic Python package is
  at `src\grf`. SMM and Windchill have moved to `ionut2026/SMMAutomationFramework` and
  `ionut2026/WindchillMcpServer`; do not add product code back into core.
- Product test runs and instrument-affecting actions require the instrument owner's authorization.
