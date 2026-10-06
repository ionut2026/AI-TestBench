# SMM test automation framework

Automated system tests for the SMM (7251 Sample Management Module), traced to Windchill RV&S.

> **New to this framework?** Read the [SMM Automation Handbook](../docs/SMM-AUTOMATION-HANDBOOK.md): a step-by-step guide
> for installing, running, writing and maintaining the tests, written for non-specialists.

```mermaid
flowchart LR
  RVS[(Windchill RV&S)] -- windchill MCP server, read-only --> ING[smm-auto ingest]
  ING --> CAT[catalog/pilot.json<br/>specs, requirements,<br/>user stories, ETs, hashes]
  CAT --> BR[smm-auto briefs] --> AG[smm-test-author agent] --> ROB[robot/suites/*.robot<br/>review:pending]
  ROB -- human review --> ROB
  CAT --> DR[smm-auto drift]
  ROB --> DR
  ROB --> RUN[smm-auto run --tier]
  RUN -- Robot keywords SMMTestbench --> SVC[automation service<br/>HTTP API v1]
  SVC -- SMM TestBench engine<br/>pinned commit --> APP[appSMM: mock / appSMM.exe + twin / rig]
  RUN --> REP[traceability.html/json<br/>+ Robot log/report]
  CAT --> REP
```

| Part | Where | What |
|---|---|---|
| Automation service | `service/` (TypeScript) | Headless service built **from the SMM TestBench sources at the commit pinned in `testbench.lock.json`**. Acts as the SMM Bridge (MQTT, ICD v7 schemas), runs the environment of a tier (broker, appSMM, hardware twin), records a timeline of every message and offers waits/sequence matching over HTTP API v1 (`127.0.0.1:8765/api/v1`; every request except `/health` needs the API token, see the handbook section 14.3). |
| Robot keyword library | `src/smm_automation/SMMTestbench.py` | `Connect As Bridge`, `Send ICD Message`, `Wait For Message`, `Wait For Message Sequence`, `Message Should Not Arrive`, `Wait For Hardware Command`, schema checks, timeline HTML in the log... Starts the service automatically. |
| Robot resources / tiers | `robot/resources/smm.resource`, `robot/environments/<tier>.py` | Shared setup/teardown and the timeouts per tier. |
| Suites | `robot/suites/<scope>/<area>.robot` | Tests tagged `SDS-<spec id>` and `spechash:<8 hex>`. Nothing else is linked in the tests: requirements, user stories and ETs come from RV&S at report time. |
| Pipeline | `src/smm_automation/pipeline/` | `ingest` (RV&S -> catalog), `briefs` (generation input for the agent), `drift` (suites vs catalog), `report` (traceability). |
| Catalog | `catalog/<scope>.scope.toml` -> `catalog/<scope>.json` | Which specifications are in scope, which are deferred / not testable (with the reason), area keywords. |

## Tiers

| Tier | appSMM | Hardware | Use | Excluded tags |
|---|---|---|---|---|
| `mock` | scripted mock in the service | none | framework self-test, CI on every push. **Verdicts are not product evidence.** | `needs:twin`, `needs:hardware-action`, `needs:operator`, `needs:applog` |
| `offline` | real `appSMM.exe` on this PC | SMM TestBench hardware twin | developer PC, nightly | `needs:operator` |
| `rig` | real appSMM on the instrument | real | dedicated automation instrument (`SMM_RIG_BROKER=host:port`) | `needs:twin`; without `--operator`: `needs:hardware-action`, `needs:operator`; without a rig control script: `requires:restart`, `requires:broker-restart`, `needs:applog` |

A tag excludes a test when the tier lacks the capability (`smm-auto run` prints what it excludes and why). On the rig,
`SMM_RIG_CONTROL` names a site script that restarts appSMM/the broker and copies appSMM's log files
(`src/smm_automation/rigcontrol.py` describes its subcommands), and `--operator console|dialog` lets an operator do
the emergency stop and other `Operator Action` steps. `smm-auto doctor --tier <tier>` checks a tier before a long run
(service, pinned TestBench, appSMM.exe/broker port, rig broker, rig control, operator; `--deep` also asks appSMM its
state; on the rig only with the instrument's owner's agreement, it connects a Bridge). The rig's log check compares
appSMM's timestamps with this PC's clock: keep both NTP-synchronised.

`review:pending` tests run on every tier and are labelled UNREVIEWED in the report (`--exclude-pending` leaves them out).
`smm-auto run` exits with the number of **new** failures: failures of tests tagged `known-issue:FINDING-<n>` (see
Findings) are KNOWN FAIL and keep the run green (`--fail-on any` counts them too).

## Setup (Windows)

```powershell
# 1. SMM TestBench at the pinned commit (default D:\projects\SMM TestBench, else set SMM_TESTBENCH_DIR)
git -C "D:\projects\SMM TestBench" checkout <commit from testbench.lock.json>
npm ci --prefix "D:\projects\SMM TestBench\simulator"; npm ci --prefix "D:\projects\SMM TestBench\hwsim"

# 2. Service
cd smm-automation\service; npm ci; npm run build; npm test

# 3. Python (pinned versions from the lock file, then the package itself)
cd ..; py -3.12 -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.lock; .\.venv\Scripts\pip install --no-deps -e .
.\.venv\Scripts\python -m pytest
```

Code quality checks (all in CI): `npm run lint`, `npm run typecheck`, `npm run coverage` in `service`;
`ruff check src tests`, `mypy`, `python -m pytest --cov` (coverage floors in `pyproject.toml` and
`service\vitest.config.ts`). After changing dependencies in `pyproject.toml`, re-create the lock:
`.\.venv\Scripts\pip-compile --extra dev --strip-extras --no-emit-index-url -o requirements-dev.lock pyproject.toml`.

RV&S access (`ingest` only) uses the windchill MCP server of this repository (`windchill-mcp-server/src/server.js`,
override with `WINDCHILL_MCP_SERVER`) with the `RVS_*` settings from the environment or `~/.copilot/mcp-config.json`.

## Daily use

```powershell
.\.venv\Scripts\smm-auto ingest                 # RV&S -> catalog/pilot.json (prints what changed since the last run)
.\.venv\Scripts\smm-auto drift --strict         # stale hashes, orphans, uncovered specs, RV&S state/link changes, retired specs, lint (exit 1 on problems)
.\.venv\Scripts\smm-auto accept 2428419         # after checking the tests: accept a spec's new RV&S state/links as baseline
.\.venv\Scripts\smm-auto migrate-hashes --from results\pilot-old.json   # after a hashing change: re-tag unchanged specs
.\.venv\Scripts\smm-auto lint                   # test rules SMM01-07 (no Sleep, doc quotes spec, variable timeouts, capability tags)
.\.venv\Scripts\smm-auto briefs --spec 2528698  # generated/briefs/SDS-2528698.md for the authoring agent
.\.venv\Scripts\smm-auto doctor --tier offline   # can the tier run? (exit 1 on FAIL)
.\.venv\Scripts\smm-auto run --tier mock        # results/mock-<ts>/: log.html, report.html, traceability.html/json
.\.venv\Scripts\smm-auto run --tier mock --processes 3   # suites in parallel with pabot (mock only; own service + broker per worker)
.\.venv\Scripts\smm-auto run --tier offline --include initialization   # extra args go to robot
.\.venv\Scripts\smm-auto --suites robot\suites\pilot\recover.robot run --tier offline
.\.venv\Scripts\smm-auto run --tier rig --operator console   # operator at the instrument does the E-Stop steps
.\.venv\Scripts\smm-auto report --output results\rig-20261005-101500\output.xml
.\.venv\Scripts\smm-auto mutate                 # mutation testing: do the tests notice defects injected into the mock appSMM? (catalog/mutants.toml)
.\.venv\Scripts\smm-auto matrix                 # regenerate the state x request matrix suite from catalog/state-matrix.toml
```

## Writing tests

1. `smm-auto ingest` and `smm-auto drift`: uncovered specifications are listed per area.
2. `smm-auto briefs --spec <id>`: the brief holds the spec text, linked requirements/user stories/ETs, the ICD
   schemas of the messages it mentions, the keyword documentation, the tags to use and the target suite.
3. The `smm-test-author` agent (`.github/agents/smm-test-author.agent.md`) writes the test from the brief, tagged
   `review:pending`. Optionally the `smm-test-reviewer` agent (`.github/agents/smm-test-reviewer.agent.md`)
   gives an independent review (APPROVE / CHANGES REQUESTED / REJECT), best on a different model than the author,
   and records it in the ledger as `agent:<model>` (information only, never the review).
4. A test engineer reviews it against the specification (and runs it on `offline`/`rig`), records the review with
   `smm-auto review <test | SDS-id> --reviewer "<name>" --verdict approved --ref "PR #n"` (it appends to
   `catalog/reviews.toml` at the test's current spechash) and removes `review:pending` in the same change.
   `drift --strict` fails on a test without `review:pending` and without an approving human review entry.
5. When RV&S changes the specification text, `drift` reports the test as **stale**: re-review, then update `spechash:`.
   When only its state or its requirement/user story links change, `drift` reports **SPEC-STATE-CHANGED** /
   **LINKS-CHANGED** until the change is checked and accepted with `smm-auto accept <id>`; a covered spec that is
   rejected or deleted in RV&S is **RETIRED**. A test covering several specifications carries one
   `spechash:<id>:<hash>` per specification. Handbook sections 12.4–12.5.1.

Rules: one behaviour per test, assert what the specification states (message, fields, order, topic, timing), wait for
events (never `Sleep`), only use keywords from the library/resource, add `needs:twin` if hardware-twin state (racks, a COP
fault) is required, `needs:hardware-action` if the only hardware step is the emergency stop, `needs:operator` for
`Operator Action`, `requires:restart` / `requires:broker-restart` if appSMM / the broker must be restarted and
`needs:applog` if appSMM's log files are read. Timeouts are variables (time limits stated
by a specification as `${SDS_<id>_LIMIT}` in the suite). `smm-auto lint` and `robocop check robot` enforce these rules
in CI (handbook Section 11.3). Each wait consumes the message it matched; order is asserted with
`Wait For Message Sequence` (or `since=last`), not by consecutive waits (handbook Section 10.2).

## Reports

`traceability.html` lists every in-scope specification with its verdict (FAIL, NOT RUN, UNCOVERED, SKIP, PARTIAL,
DEFERRED, PASS, NOT TESTABLE), its review status (VERIFIED = passed and every test human-reviewed, UNREVIEWED), the
tests with their outcome (PASS, NEW FAIL, KNOWN FAIL, FIXED?) and messages, stale/suspect flags, and rolls the verdicts
up to requirements and user stories (worst verdict wins). PARTIAL means some tests of the specification passed and
others did not run on this tier. Robot's `log.html` holds the full message timeline of every test. On the `mock`
tier the report carries a banner: those verdicts test the framework, not appSMM.

## Pilot status

Scope `catalog/pilot.scope.toml`: Initialization, Recover, Shutdown, System Status and Bridge connection,
26 specifications (21 automated, 4 deferred, 1 not testable).

> **The pilot suites were written by the authoring agent and still need a human review against the
> specifications before their verdicts are used as evidence.** Open points are listed in the test documentation
> (e.g. the real appSMM's answer to InitializationRequest outside NotInitialized is only logged).

Around the specified behaviour: `state_matrix.robot` is **generated** by `smm-auto matrix` from
`catalog/state-matrix.toml` (every request in NotInitialized, Idle and E-Stop; cells the specifications leave open
are skipped with appSMM's observed answer, their questions for the specification owner are in
`catalog/state-matrix.questions.md`; do not edit the generated files, CI checks them with `smm-auto matrix --check`).
`robustness.robot` checks that malformed or misrouted input does not disturb appSMM in Idle, plus a second
InitializationRequest during Initializing and a burst of 50 SystemStatusRequests. Tests of behaviour no
specification states are tagged `nospec:<kind>` instead of `SDS-<id>`; drift and the report list them apart and they
count for no specification (handbook Section 9.13).

Deferred: 2528708/2528710 (appSMM logs are SmartInspect `.sil` files; no reader yet), 2532404/2532508 (need RTC
fault injection in the hardware twin).

### Findings on appSMM 0.7.2305.25001 (offline tier)

The offline tier runs the real appSMM against the hardware twin. With the pilot suites it shows these differences
between the specifications and that build. They are **candidate findings, not confirmed defects**: the build is
from 2023 and the specifications are newer, so a human has to confirm each one (or correct the test) first.

| ID | Specification | Observed | Consequence |
|---|---|---|---|
| FINDING-1 | SDS-2854109 (Bridge crash) | After the SMMBridge last will "SMMBridge Disconnected" appSMM stays `Idle`; a broker outage does put it into `E-Stop` | SDS-2854281 (Operator warning on reconnection) fails on its precondition |
| FINDING-2 | SDS-2653094, SDS-2532510 (Recover) | Recover goes `E-Stop` -> `NotInitialized` (DeInitializeCmd); no automatic initialization, no InitializeCmd | |
| FINDING-3 | SDS-2525388 (PowerOn) | No SystemStatusNotification with `CurrentState=PowerOn` after a start (SystemStatusResponse does report `PowerOn`), only `PowerOn` -> `NotInitialized` | |

The failing tests are tagged `known-issue:FINDING-<n>`, so an offline run with only these failures is green and any
other failure is reported as NEW FAIL. When a finding is fixed or rejected, remove the tag (the report shows FIXED?
when a tagged test passes).

Open interpretation for the reviewer: SDS-2854281 is tested as "warning after reconnection and RecoverRequest"; the
test accepts the warning from the moment of reconnection on.

Known appSMM behaviour the suites take into account: after InitializationRequest the empty instrument goes
`Initializing` -> `Idle` -> `Clearing` -> `Idle`; with a rack on the instrument it goes to `NormalOperation`, and appSMM
keeps its rack bookkeeping across Shutdown/Recover, so tests that load racks empty the twin and restart appSMM in
their teardown.
