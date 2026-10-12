# Framework split: local preparation and cutover gates

**Cutover implemented:** the user approved GitHub creation, rename and pushes on 2026-10-12.
See [REPOSITORIES.md](REPOSITORIES.md) for current locations. The instructions below document the pre-cutover
procedure; `prepare_split.py` now accepts `--source` for an archived monorepo at `3292490` and refuses the new core.
The active local folder remains AI-TestBench; old product/MCP files remain untracked and ignored locally to avoid
breaking the user's existing environment or MCP registration. They are absent from the core's current Git tree.

All implementation and commits remain in `D:\projects\AI-TestBench`, on main, until an explicit cutover decision.
Do not move or delete the active checkout. Do not edit the external SMM TestBench.

## Local candidates

`general-robot-framework\scripts\prepare_split.py` clones main with `--no-local`, then filters each **fresh clone**
with git-filter-repo. It refuses existing destinations and destinations outside this checkout's ignored
`general-robot-framework\results`. It does not stash, reset, filter or change branches in the source.
Uncommitted files (including the PRA export) are not copied.

## Verified local state (2026-10-12)

Source commit `10d6c8d` was extracted into `general-robot-framework\results\split-verified`.
All three candidate source trees are clean and have no remotes; every HEAD blob matched the mapped source blob.
Retained histories contain 7 GRF commits, 32 SMM commits and 4 MCP commits.

| Gate at extracted root | Result |
|---|---|
| GRF, without SMM installed | 41 pytest tests; ruff/mypy clean |
| SMM (goldens included) | 173 pytest tests; lint/drift/matrix/Robocop clean |
| SMM TypeScript service | Build, typecheck and lint clean; 50 Vitest tests pass |
| SMM mock, isolated service URL, 3 workers | 37 passed, 0 failed, 5 skipped; report has `productEvidence = false` |
| MCP, offline only | Dependencies installed with scripts disabled; JavaScript syntax and CLI help pass |

The new mock service and workers exited after the run. No real appSMM/instrument operation, live ingestion,
MCP registration change or external TestBench modification was performed.
MCP's Java bridge/live server verification, offline known-findings run and rig subset remain cutover gates.
GitHub CI has not run because no push is authorized.

The first fresh-checkout run exposed a test harness startup race: rig-control capability discovery shared the
test's half-second deliberate timeout. Discovery now uses a normal startup budget, then only the restart-under-test
uses the half-second limit. Production timeout behavior is unchanged.
SMM pytest requires the service bundle, so build it before pytest (as setup/CI already do).

Final publication must regenerate from final main, including subsequent documentation commits.

Install git-filter-repo in a project-local tooling venv after a missing-tool error, then run:

```powershell
python -m venv general-robot-framework\results\split-tools
.\general-robot-framework\results\split-tools\Scripts\python -m pip install git-filter-repo
.\general-robot-framework\results\split-tools\Scripts\python general-robot-framework\scripts\prepare_split.py
```

The output is three local candidate repositories with filtered history, no remotes, and `extraction.json` recording
the source SHA, filtered heads and content checks. Every extracted HEAD blob must match its original path's blob.
These are preparation copies, **not cutover-ready published repositories**: workflows/docs still contain staged
paths or references until final rewiring. Re-run into a new destination after subsequent main commits.
Do not push a stale candidate.

## Layout mappings

| Candidate | Retained paths |
|---|---|
| GeneralRobotFramework | GRF package directory moved to root; core workflows; split plan; CODEOWNERS |
| SMMAutomationFramework | SMM directory moved to root; handbook; SMM agents/workflow; CODEOWNERS |
| WindchillMcpServer | MCP directory moved to root, including its own docs and scripts |

No `exports`, credentials, venvs, node_modules, local run outputs or external TestBench sources are extracted.

## Approval boundary and remaining work

Before publishing, the owner must confirm the GitHub owner/organization, private visibility, repository creation,
pushes and the AI-TestBench rename. Organization transfer and branch protection need separate concrete decisions.
Existing `ionut2026` CODEOWNERS is a known current maintainer, not an invented organization team.

After that approval:

1. Regenerate candidates from the final committed main; prepare root-path workflows and CODEOWNERS, docs links,
   product agent paths and `.github\copilot-instructions.md` for each final checkout.
2. Create/push the product and MCP repositories, preserve the extraction commit maps, and publish the approved
   GRF release tag. Replace the local dependency bridge with a pinned Git release/commit and read-only private
   repository authentication. Do not put a token in a URL or a committed file.
3. Adapt SMM CI to fetch its approved sibling GRF separately; provide a bump-PR workflow using the confirmed
   repository and token policy. Keep both the dependency pin and `[framework] version` synchronized.
4. Verify new-location GRF Python/wheel checks; SMM Python, TS contracts, lints, drift/matrix and mock regression;
   MCP build/start without changing the user's active integration. No live ingest is needed for the split.
5. Offline/rig regression is still a separate gate: only known offline findings may fail, and an instrument subset
   needs current owner/operator permission. Do not infer instrument availability from the earlier conversation.
6. Give the user the final Windchill MCP entry and exports setting, then let the user update their Copilot config
   and rename the active checkout folder. Do not write project artifacts or configuration to C:.

## New-location setup and MCP settings

SMM's `scripts\setup.ps1` works in the staged tree or with a sibling `GeneralRobotFramework` checkout:

```powershell
.\scripts\setup.ps1 -GrfPath D:\projects\GeneralRobotFramework
$env:WINDCHILL_MCP_SERVER = "D:\projects\WindchillMcpServer\src\server.js"
$env:RVS_EXPORT_DIR = "D:\projects\WindchillMcpServer\exports"
```

Setup installs the committed third-party lock, editable GRF and SMM, checks dependencies, and builds the service.
It never starts a product environment. `-SkipService` installs/checks Python only.
Use an explicit `-GrfPath` for the local candidates.

After the final checkout exists, the relevant MCP server fields are:

```json
{
  "command": "node",
  "args": ["D:\\projects\\WindchillMcpServer\\src\\server.js"],
  "env": {
    "RVS_EXPORT_DIR": "D:\\projects\\WindchillMcpServer\\exports"
  }
}
```

Merge these fields into the user's existing Windchill entry; preserve other RVS settings and authentication.
This snippet is future cutover guidance, **not an instruction to replace the current live server now**.
