# Run requests and acceptance

Start with the [beginner explanation](GETTING-STARTED.md#what-is-an-accepted-run).
A **request** describes a bounded product run. An **acceptance gate** checks whether completed results satisfy
that request. Neither is permission to operate an instrument: the instrument owner authorizes the run, and a
protected controller such as Jenkins authenticates who starts it.

## Supported boundary

GRF supplies local request validation, local run reservation, content-aware review records and a post-run
acceptance evaluator. The product supplies runtime version semantics, observable behavior and controller integration.
The bounded deploy-and-test command below maintains plan/promotion/run/gate sequencing. There is no generic
unattended scheduler, protected profile store, distributed lease, authenticated review service or automatic
AI author/reviewer chain. A valid request does not execute anything.

`grf run --request <file>` validates authorization expiry, tier, exact selection, optional source/catalog
pins and supplied deployment identities **before** capability discovery or Robot execution. It saves the
exact request bytes as `request.toml` in a fresh run output and stamps the request ID/digest, GRF version,
available product-package version and deployment-record digests into managed Robot metadata.
The controller still protects targets, authenticates authorization and holds the site reservation.
The post-run gate cannot authenticate metadata or prevent a product adapter from addressing the wrong endpoint.

For controlled evidence, the product's controller should require immutable `source_revision` and
`catalog_sha256`, protect the exact request digest and private endpoint/target mapping, authenticate owner approval,
and hold the authoritative resource reservation throughout deployment, testing and evaluation. These policies
are not established merely by a request's `owner` or `reference` strings.

## Request schema 1

Save a TOML file in protected external storage or ignored `results`, never credentials in source.
This illustrative tests-only request uses a neutral product and an already installed application:

```toml
schema = 1
id = "counter-bench-1"
action = "test"
tier = "bench"
description = "Check the approved counter increment"
# source_revision = "<full 40-character lowercase Git commit>"
# catalog_sha256 = "<64 lowercase hex characters>"

[selection]
specs = [1]
tests = ["Increment"]
min_tests = 1

[acceptance]
allowed_skips = []
allow_known_failures = false
# max_catalog_age_days = 7

[[component]]
name = "app"
expected_version = "1.2.3.4"
runtime_identity = true

[authorization]
owner = "Instrument Owner"
reference = "OWNER-APPROVAL-REFERENCE"
valid_until = 2026-10-31
```

The authorization date is inclusive; replace it with the actual owner-approved window. The validator rejects
unknown fields, invalid types, duplicate selections/components, non-evidence tiers and unknown component names.
`schema`, `id`, `action`, `tier` and the authorization fields are required.

| Field/table | Contract |
|---|---|
| `id` | 1-80 letters, digits, `.`, `_`, `-`; first character alphanumeric |
| `action` | `test` or `deploy-and-test` |
| `tier` | Exact configured evidence tier |
| `description` | Optional text, default empty |
| `source_revision` | Optional full 40-character lowercase Git commit; gate requires matching clean tracked checkout |
| `catalog_sha256` | Optional SHA-256 of the catalog file bytes, 64 lowercase hex characters |
| `[selection] specs` | Positive specification IDs, default `[]`; each needs a non-skipped executed test |
| `[selection] tests` | Exact test names, default `[]`; each must execute, not merely skip, and must be unambiguous across executed suites |
| `[selection] min_tests` | Positive integer, default `1`; counts PASS/FAIL/SKIP entries, but all-skipped output always fails |
| `[acceptance] allowed_skips` | Exact names of optional tests allowed to skip, default `[]`; cannot satisfy required selections |
| `[acceptance] allow_known_failures` | Boolean, default `false`; opt-in allows failures carrying `known-issue:<id>` |
| `[acceptance] max_catalog_age_days` | Optional nonnegative integer; checks catalog `generatedAt` date |
| `[[component]] name` | Product-declared deployment component, unique in this request |
| `[[component]] build`, `sha256` | Exact positive Jenkins number and 64 lowercase hex digest, both required for `deploy-and-test` |
| `[[component]] expected_version` | Nonempty string; required for tests-only components with runtime identity enabled |
| `[[component]] runtime_identity` | Boolean, default `true`; disabling it means installation-only, not functional or product-version evidence |
| `[authorization] owner` | Nonempty person identifier, not `agent:...`; recorded assertion, not authentication |
| `[authorization] reference` | Nonempty approval/ticket reference |
| `[authorization] valid_until` | TOML date, not quoted text; expired requests fail |

For `deploy-and-test`, declare at least one component with an exact build and digest resolved during planning.
`expected_version` may be omitted: the gate uses the deployment's **installed** version, not a filename or
requested artifact fallback. `run --request` selects the **union** of requested specifications and exact test
names, or all configured tests when both lists are empty. It rejects missing specifications, missing/ambiguous
names and selections below `min_tests` before execution. Selection uses exact suite-qualified identities, not
Robot wildcard matching; literal `*`, `?` and brackets in names remain literal.
Unselected empty child suites are removed before their suite setup can run. Capability exclusions still apply,
and required tests excluded or skipped cannot satisfy acceptance. Forwarded Robot selectors and pre-run
modifiers are rejected with `--request` so they cannot bypass the bounded selection.
Tests-only requests pass **no deployment records** for their components; they check fresh runtime versions
against explicit expected versions. Keep read-only installation/status snapshots separately when needed.

## Commands and result

Run these from the consuming product's checkout using its installed GRF environment. Replace every placeholder.
`request check` reads local configuration and validates a file; it does not contact a product or deploy anything:

```powershell
grf request check <request-file> --json results\validated-request.json
```

Success prints `Request is valid` and writes normalized schema-1 JSON with the TOML field names, explicit
selection/acceptance/runtime-identity defaults and an ISO date string for `valid_until`.
Unset optional fields are omitted. `--json` is optional; output is written only after successful validation.
Use a fresh output path: an older JSON file is not removed when validation fails, so consumers must check the
current command's exit code and must not reuse stale normalized output.

The owner-controlled runner then performs the authorized tests, for example
`grf run --request <request-file> --outdir results\run-1`. The request supplies the tier;
an explicitly supplied `--tier` must agree. This is **not** an instruction
for an agent to run an instrument. After execution, evaluate the saved XML locally:

```powershell
grf gate --request <request-file> --output results\run-1\output.xml
```

For deploy-and-test, also supply exactly one `--deployment <record-file>` per requested component.
Supply those same records to both `run --request` and `gate`; their exact byte digests must match the run stamp.
The command also reads `GRF_DEPLOYMENT_RECORD`; clear unrelated inherited records.
`--json <file>` overrides the default `acceptance.json` next to `output.xml`.

| Command | Exit codes |
|---|---|
| `request check` | `0` valid; `1` schema/policy/expiry rejection; `2` usage/configuration/file errors |
| `gate` | `0` ACCEPTED; `1` REJECTED; `2` invalid request, missing/malformed input or integration error |

The JSON result has `schema=1`, request/action/tier, `status`, `evaluatedAt`, source revision/modified flag,
catalog digest, `requestSha256`, `outputSha256`, deployment file paths/digests, framework and product package
versions (explicitly unavailable when the product distribution cannot be identified), recorded authorization,
executed count and named `checks` with PASS/FAIL details.
The product can override `ProductPlugin.product_version()` to identify its installed distribution; the default
uses unambiguous installed package metadata for the plugin's package. If available, the run/evaluator versions
must agree. All `GRF ...` metadata and `Tier` are managed and cannot be supplied through forwarded options.
**0.4 migration:** old XML without matching request/deployment/GRF stamps is rejected. Re-run with
`run --request`; do not retrofit stamps into historical results. Even whitespace/comment changes alter the
request's exact byte digest, so keep and gate the saved request snapshot.
It checks authorization expiry, evidence tier, optional source/catalog binding/freshness, required nonempty
execution, results/skip policy, current human content reviews, specification drift, deployment provenance and
product-observed identity. Missing data never becomes a successful component check.

For deployments, the gate requires exactly one record per component, the current project, explicit
`dryRun=false`, `DEPLOYED`/`UNCHANGED`, requested artifact build/digest, and a running installed version.
It uses separate product comparison hooks for requested/installed and installed/observed versions.
Extra or ambiguous component records reject acceptance. `RESTORED` is not accepted as the requested new
deployment; the optional [Robot diagnostic assertion](DEPLOYMENT.md#checking-the-observed-product-version)
can check a restored installation separately and is not a second acceptance policy.

ACCEPTED means only that these supplied results satisfy this request and GRF's checks. Mock passes never qualify.
The evaluator is not a signed attestation: the protected runner must bind the XML, source, catalog, review ledger
and records to the same execution. Without source pins, the gate cannot prove Python/library/config identity;
it inspects the current checkout, not an authenticated execution-time snapshot.
Equal runtime versions cannot prove physical board, artifact-byte identity or correct functionality.

## Project acceptance policy

Products can tighten requests in `grf.toml`; the same policy applies to `request check`, `run --request`
and `gate`, before execution/evaluation. For example:

```toml
[acceptance.policy]
require_source_revision = true
require_catalog_sha256 = true
literal_tests = true
allow_known_failures = false
runtime_identity = { app = true }
```

Every option is optional. Required pins reject requests omitting them; `allow_known_failures = false`
prevents a request from opting into known failures. `literal_tests = true` rejects wildcard-looking
characters (`*`, `?`, brackets) in named tests/allowed skips to catch accidental pattern intent, even though
request selection itself always matches names literally. A `runtime_identity` value of `true` requires that
component's runtime check whenever it is requested; `false` imposes no additional restriction.
Policies cannot authenticate an owner, protect source/configuration or establish a site reservation.
Unknown policy fields/types fail project loading rather than being silently ignored.

## Product identity hooks

`ProductPlugin` provides:

```python
def runtime_identity(self, meta: dict[str, str]) -> dict[str, str]:
    ...

def identity_matches(self, component: str, expected: str, observed: str) -> bool:
    ...
```

For 0.4 adoption, override `installed_matches(component, expected, installed)` for requested
versus installed build identities, and `runtime_matches(component, expected, observed)` for
installed builds versus product-reported versions. Installed comparison defaults to exact text;
runtime comparison delegates to the legacy `identity_matches` during migration. Never use
a runtime protocol comparator to compare two artifact versions. The Robot deployment-binding
keyword uses the same runtime hook as the gate; the standalone helper accepts `matches=`.
Duplicate suite-qualified test identities are rejected before collection/report joins and
cannot satisfy acceptance or share one human approval.

The first extracts component versions from Robot output metadata, not a new network query during the gate.
Its default `{}` makes required identity checks fail closed. The default comparator checks stripped string
equality. Products must reject malformed/ambiguous observations and document any justified normalization.
They own fresh acquisition during the run and the protected endpoint-to-board mapping.

## Content-aware human review

`grf review` records `fingerprint = "sha256:..."` in addition to suite/name and specification hash.
Scheme `grf-content-1` covers test logic/settings (excluding pending and specification-hash tags), suite
settings/variables/keywords and transitively imported Robot resource text. Line positions, checkout locations
and other tests in the suite do not invalidate that test's fingerprint.
Python libraries, external variable files, parent suite initialization and unresolved dynamic imports are not
fully covered; pin and protect the source/configuration too. A content fingerprint is not a full execution bundle.

Old reviews remain in the ledger and visible to drift/reporting, but do not satisfy the strict gate without
a fingerprint. After actual human review, re-record with the ordinary `grf review ... --reviewer ... --verdict ...`
command; do not synthesize fingerprints as approval. Agent reviews never count as human approval.
Changing test/shared-resource content invalidates affected approvals. See [Testing](TESTING.md).

## Maintained deploy-and-test workflow

`grf workflow deploy-and-test` is for an **owner-authorized protected controller**, not an agent operation.
It requires an unexpired deploy-and-test request with numeric builds/SHA256, clean immutable source revision,
catalog digest, a protected expected request digest, and `--yes`. No floating build alias or target override
is accepted. The controller still authenticates approval and holds the authoritative instrument reservation.

Run from the pinned product checkout only after site commissioning and owner authorization:

```powershell
grf workflow deploy-and-test --yes --request <protected-request-file> --request-sha256 <approved-digest> --config <external-site-config> --outdir results\workflow-<unique-run-id>
```

Append `--trigger nightly` for a scheduled nightly invocation. The validated choices are `cli`, `manual`
and `nightly`, shared with `deploy run`; the workflow defaults to `manual` for compatibility.
The resolved value is passed to every planning and deployment stage, retained as `trigger` in both sets
of deployment records and every `workflow.jsonl` event, and checked against returned records before proceeding.
This is a recorded launch category, not authentication, scheduling or owner authorization.

Components can use different Jenkins branches. Configure `[deploy.branches]` in `grf.toml`, with the existing
`[deploy] default_branch` as fallback, or append repeatable overrides such as
`--branch app=release/app --branch controller=release/controller`. Selection is resolved once before planning
and reused unchanged for actual deployment. Unknown components, duplicate overrides and empty values are
rejected. See [the exact configuration and compatibility rules](CONFIGURATION.md#deployment-branch-selection-041).
The controller must protect these branch inputs alongside its approved request/site configuration:
the schema-1 request pins build number and artifact SHA, not branch authorization.

The command validates source/catalog/selection/preflight **before** deployment, snapshots exact request bytes
into a new directory, then plans **all** components in the product's configured deployment order. Plans must
match project/component/site target, resolved Jenkins job URL/branch, numeric build, digest and any expected installation version before
promotion. Actual deployment uses the same in-memory site configuration and pinned numeric build/digest;
re-resolving a changed Jenkins artifact fails checksum verification rather than following a moving alias.
Source/catalog/authorization are rechecked before each stage. Site credential files and host-key trust remain
site-owned, not copied into the workspace.

Successful deployment records must match the original request, planned targets and resolved job/branch before
testing begins. Both plan and deployment records retain `artifact.branch` and `artifact.job_url`; journal
stage entries also retain the resolved identity. With no branch configured, multibranch/container jobs are
rejected before parent-build lookup; regular jobs remain supported without a branch.
GRF then applies `run --request` and always attempts the pure acceptance gate after a test return/error/cancel,
preserving abnormal test errors and failing gate results. A failed deployment stops before testing.
Earlier successful component deployments are **not** automatically rolled back if a later component fails;
the owner uses retained records/recovery guidance. A failing test does not itself authorize rollback.

`run` and `gate` accept optional `--request-sha256`; the workflow always supplies it, comparing the digest
against the same bytes parsed, not a separate prior read. Changed snapshots fail closed. The fresh output
contains `request.toml`, append-only flushed `workflow.jsonl` stage/digest records, separate `plan` and `deploy`
records, `test` output/reports, and `acceptance.json`. A missing final `evaluated` event means incomplete
or interrupted orchestration, never acceptance. Existing directories are refused, never deleted or reused.
No global data pruning, cross-machine lease, authenticated signature or automatic resume is provided.

The workflow returns the gate failure code, otherwise the test return code; deployment failure codes are
preserved. Input/pin/provenance errors return 2. Success requires both a successful run and acceptance.
Mock execution and fake deployment unit tests validate orchestration only, never a real product.

**Jenkins migration:** the generated template now invokes this maintained command using protected fixed
request/digest/site inputs and an enforced Lockable Resources reservation. It no longer duplicates request
comparison logic, permits floating build parameters, clears static evidence folders or skips the gate after
failed tests. Product wrappers should retain only site guards, endpoint/approval policy and credential setup.
Commission permissions, shared reservation, approved inputs and evidence hooks before enabling the job.

## Evidence integrity and assertion coverage (0.4 migration)

An evidence tier is **eligible** for product evidence, not proof that the actual environment is valid.
Products must implement these `ProductPlugin` hooks using public types from `grf.evidence`:

| Hook | Required behavior |
|---|---|
| `evidence_preflight(tier)` | Before capability discovery or any product contact, validate pinned/clean code and resources (`provenance`) and the actual allowed configuration (`actual-target`). Return `EvidenceChecks`, or `None` when unavailable. Never create an environment, start services, deploy or operate an instrument here. |
| `evidence_checks(tier, tests, meta)` | Pure assessment of **persisted** results for every executed test: `startup`, `completeness`, `cleanup`, `provenance`, `actual-target`. No network/product contact during report or gate. Missing, malformed, generation-changed, overflowed or unconfirmed cleanup evidence must fail, not warn. |
| `assertion_coverage(passed_tests, meta)` | Map each specification ID to `AssertionCoverage(required=(...), observed=(...))`. Required clause IDs come from the product's reviewed coverage model; observed IDs come only from assertions actually executed by the supplied passing tests. A spec tag, capability label or state notification cannot substitute for an omitted hardware assertion or ordering check. |

`EvidenceCheck(name, passed, detail)` requires an exact boolean and diagnostic detail.
`EvidenceChecks(checks=(...), identities={...})` requires unique check names and nonempty validated identities
when used for evidence: for example code/resource digests and an actual configuration fingerprint.
`as_dict()` / `from_dict()` provide strict schema-1 serialization. Do not include credentials or sensitive
endpoint details. GRF stamps the validated preflight into managed `GRF Preflight` metadata; final assessment
must preserve **every** preflight identity unchanged. Product code must also compare persisted identities
with its approved pin/resource policy, not merely accept a matching pair of stale strings.

At the product facade's mandatory per-test setup boundary, call `require_checks(startup, STARTUP)` after
obtaining fresh marks/session identity; failure must prevent the test body. At teardown, retain every
mandatory obligation and use `require_checks(final, FINAL)` alongside owned-resource cleanup even if a test
assertion or diagnostic export failed. These helpers raise `EvidenceError` (a `ValueError`) on unavailable or
failed checks. Persist outcomes, including unsuccessful ones, for `evidence_checks`; aggregate **all tests**,
not just the final session. Product-specific transport schemas, service generation tracking, fault state,
resource manifests and clause IDs stay in the product.

GRF refuses an evidence run without a passing preflight **before** capability discovery. An implemented
preflight must also pass on non-evidence tiers, so adapters can reject real-target/mock contradictions.
Development/mock runs may omit the hooks, but can never become accepted/VERIFIED evidence. After execution,
`--no-report`, `--fail-on none` and known-failure policy cannot suppress missing integrity or mandatory
assertions: `run` returns 2. Gate rejects unavailable/failed assessment; report shows integrity errors and
uses PARTIAL for passing tests with undeclared/unobserved required assertions. VERIFIED additionally requires
current-content human review. A legacy hash-only review or specification tag is insufficient.

**Migration:** old adapters and XML without validated preflight/results/assertion coverage are not upgraded
into evidence. Implement and negatively test all hooks before real-tier execution. Re-run only with the
instrument owner's authorization. The generic demo remains non-evidence and needs no product validation hook.
These contracts are not signed attestation and cannot authenticate dishonest adapter claims; protected source,
controller authorization, resource reservation and owner-approved observability remain necessary.

## Product lifecycle and cleanup APIs

`GrfLibrary` exposes `begin_test(started_ms)`, `end_test()`, `set_marks()`, `add_segment(label)`,
`rig_control()`, `require_rig_control(subcommand, what)` and `ask_operator(instruction, timeout=None)`.
These are Python extension methods, not automatically exposed Robot keywords. Existing underscored
implementations remain compatibility fallbacks, so legacy mark/lifecycle overrides still execute.
New product facades should use the public methods instead of private base helpers.
`drift.current_review_problem(test, tests, reviews)` provides the strict current-content human-review decision:
missing fingerprints, pending tests and legacy hash-only approvals cannot approve newly generated content.
It never records approval or removes pending tags.

`grf.cleanup.CleanupGuard` provides a narrow cleanup contract. Product adapters register named owned-resource
actions (each returns `None` or raises) and a final-state probe (returns exactly `True` only after verifying
the approved baseline). `guard.run(actions, verify, publish=...)` attempts **every** action and the probe,
even after an earlier failure or cancellation, and publishes a `CleanupResult`.
The result includes every step, `final_state_verified` and `quarantined`. Any failure, malformed successful
return or publishing error sets quarantine and raises `CleanupError` with `.result`; cancellation is re-raised
only after remaining cleanup, with the outcome attached as an exception note.
Cleanup callbacks must implement their own bounded operations; GRF cannot stop a remote action accepted
before an HTTP/SSH timeout. Never treat process termination as proof of remote final state.

Call `guard.require_ready()` before later tests. A later successful cleanup does **not** automatically
release quarantine: authorized reconciliation must use `guard.reconcile(verify)` and explicitly verify the
approved baseline. The guard is in-process only. Product/controller integration must persist the result and
quarantine across processes, provide resource ownership, time budgets and owner-authorized reconciliation.
There is no automatic instrument operation, global quarantine registry or distributed lease in this helper.
Publish failures remain available through `guard.last_result`, not silently ignored.

## Reservation

A non-parallel tier defaults to `lock = "<tier-name>"`; parallel tiers have no automatic lock unless configured.
`grf run` holds the operating-system file lock `<configured-results>\.locks\<lock>.lock` while Robot executes.
Two runs using the same lock directory and name cannot overlap; contention returns `2`, and process exit/crash
releases the OS lock. An owner JSON file is diagnostic only, not authority to break a lock.
Different checkouts/results roots do **not** share this lock, even on one machine. Configure the same trusted
results root for shared local resources. It does not cover separate `deploy` commands or hold a whole pipeline.

For several machines or jobs using an instrument, the site must supply one authoritative reservation, such as a
Jenkins Lockable Resources lock around **deployment, tests and gate**. GRF's board deployment lock only covers
deployment, and local tier locks are not a distributed lease or authenticated controller reservation.
Do not advertise an unlocked terminal path as equivalent to the protected controller. Live commissioning,
cancellation/recovery behavior, protected approvals and instrument handover require separate owner/admin validation.
