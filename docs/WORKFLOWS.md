# Run requests and acceptance

Start with the [beginner explanation](GETTING-STARTED.md#what-is-an-accepted-run).
A **request** describes a bounded product run. An **acceptance gate** checks whether completed results satisfy
that request. Neither is permission to operate an instrument: the instrument owner authorizes the run, and a
protected controller such as Jenkins authenticates who starts it.

## Supported boundary

GRF supplies local request validation, local run reservation, content-aware review records and a post-run
acceptance evaluator. The product supplies runtime version semantics, observable behavior and controller integration.
There is no generic unattended workflow engine, protected profile store, distributed lease, authenticated
review service or automatic AI author/reviewer chain. A valid request does not execute anything.

`grf run` has **no `--request` option**. The controller validates and binds the request to its actual
source/catalog, selection, targets and builds **before** deployment or product stimuli; then it calls the ordinary
run command and evaluates the output with `grf gate`. The gate is post-run: it cannot prevent an incorrectly
configured runner from contacting the wrong product first.

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
requested artifact fallback. Request fields do not filter the Robot run: the controller must translate the
approved selection into ordinary `grf run` options and verify the resulting output.
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
`grf run --tier <authorized-tier> --outdir results\run-1` with the approved filters. This is **not** an instruction
for an agent to run an instrument. After execution, evaluate the saved XML locally:

```powershell
grf gate --request <request-file> --output results\run-1\output.xml
```

For deploy-and-test, also supply exactly one `--deployment <record-file>` per requested component.
The command also reads `GRF_DEPLOYMENT_RECORD`; clear unrelated inherited records.
`--json <file>` overrides the default `acceptance.json` next to `output.xml`.

| Command | Exit codes |
|---|---|
| `request check` | `0` valid; `1` schema/policy/expiry rejection; `2` usage/configuration/file errors |
| `gate` | `0` ACCEPTED; `1` REJECTED; `2` invalid request, missing/malformed input or integration error |

The JSON result has `schema=1`, request/action/tier, `status`, `evaluatedAt`, source revision/modified flag,
catalog digest, recorded authorization, executed count and named `checks` with PASS/FAIL details.
It checks authorization expiry, evidence tier, optional source/catalog binding/freshness, required nonempty
execution, results/skip policy, current human content reviews, specification drift, deployment provenance and
product-observed identity. Missing data never becomes a successful component check.

For deployments, the gate requires exactly one record per component, the current project, explicit
`dryRun=false`, `DEPLOYED`/`UNCHANGED`, requested artifact build/digest, and a running installed version.
It uses the product comparison hook for requested/installed/observed versions.
Extra or ambiguous component records reject acceptance. `RESTORED` is not accepted as the requested new
deployment; the optional [Robot diagnostic assertion](DEPLOYMENT.md#checking-the-observed-product-version)
can check a restored installation separately and is not a second acceptance policy.

ACCEPTED means only that these supplied results satisfy this request and GRF's checks. Mock passes never qualify.
The evaluator is not a signed attestation: the protected runner must bind the XML, source, catalog, review ledger
and records to the same execution. Without source pins, the gate cannot prove Python/library/config identity;
it inspects the current checkout, not an authenticated execution-time snapshot.
Equal runtime versions cannot prove physical board, artifact-byte identity or correct functionality.

## Product identity hooks

`ProductPlugin` provides:

```python
def runtime_identity(self, meta: dict[str, str]) -> dict[str, str]:
    ...

def identity_matches(self, component: str, expected: str, observed: str) -> bool:
    ...
```

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
