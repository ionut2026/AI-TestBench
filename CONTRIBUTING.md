# Contributing

## Where a change belongs

Put product-independent configuration, pipeline, reporting, capability and timing/rig protocol behavior in GRF.
Keep product services, interfaces/schemas, log parsers, state models, fault implementations, test catalogs and
suites in the product repository. Use `ProductPlugin` only for actual product extension points.
Do not add a generic abstraction based on a single hypothetical consumer; demonstrate it with the neutral demo.

## Documentation is part of every code change

Update all relevant documentation in the same change: README, applicable `docs` guides, this contributor guide,
user-visible CHANGELOG entries, and affected starter/example documentation. Check unchanged guides for accuracy.
Document behavioral, CLI, configuration and API changes and explain compatibility migrations.
Keep topics in their [canonical guides](README.md#documentation) and use links instead of repeating instructions.
Write for readers new to Robot Framework: define unfamiliar terms, explain the purpose and working directory
of commands, and describe expected results. Keep beginner walkthroughs in
[Getting started](docs/GETTING-STARTED.md), with detailed policies in their reference guides.
When starter templates change, update `examples\demo` in the same change; the parity test enforces this.
For shared skills, also update the matching GRF `.github\skills` copy; see [Copilot skills](docs/SKILLS.md).
The shared integration coordinator also has identical root, template and demo copies. Preserve its supported
`name`/`description` frontmatter and generic ownership boundaries; keep product mapping in product instructions.

## Coordinated repository work

Preserve direct main checkouts, uncommitted user work and other sessions. Honor the user's selected direct-main
or isolated branch/PR workflow only for affected repositories; do not switch or edit another owner's checkout.
Direct-main work has one writer per repository. Inspect branch, dirty state and ownership, coordinate transfers
and ask on conflicts before writing. Never switch or clean up user-owned dirty main or silently include unrelated
edits. If protections block direct-main work, ask rather than bypassing them or creating unwanted branches/PRs.
Use [the shared integration procedure](docs/SKILLS.md#coordinated-integration): agree exact contracts and
dependency direction before implementation, then have consumer owners validate the immutable framework commit.
Report both source identities and unperformed gates; version equality alone is not source provenance.
Instructions do not spawn workers, reconnect sessions, grant permission or authenticate approvals.

Push, merge and tag each need explicit matching authorization; prior deployment or PR approval is not blanket
approval. Preserve branch protections and current human review. Do not change model/account/OS settings,
permissions or credentials, trigger Jenkins or operate instruments as part of this coordination procedure.

## Gates

For a Windows source checkout, `.\scripts\setup.ps1` installs the development environment before these gates.
Install `.[dev,deploy]` for the full generic checks, including the default SSH backend.
CI checks Python 3.11/3.12 on Windows/Linux and enforces the coverage floor documented in
[Testing](docs/TESTING.md#core-validation-windows); caches and coverage data belong under `results`.
It is an explicit post-clone action, not a Git hook; see [installation](docs/GETTING-STARTED.md#4-prepare-a-safe-practice-project).
Keep setup scoped to this checkout, preserve existing environments, and report install/check failures.

Run the [core validation and wheel-only demo](docs/TESTING.md#core-validation-windows).
Changes consumed by a product also need that repository's authorized regression gates.
Never regenerate goldens just to make a failure go away: explain and review an intentional output change.
Product and hardware runs need the owner's authorization; core changes do not authorize consumer operations.
Do not publish data to RV&S or introduce archive/history/publishing features excluded from the approved scope.

Deployment code (`src\grf\deploy`) must stay generic: no host names, product names or credentials in core.
Never weaken host-key, TLS, checksum, lock or rollback checks to make something work; test new behavior with the
simulated board (`tests\deploy_fakes.py`), and never run a real deployment as part of validating a core change.
See [Deploying builds](docs/DEPLOYMENT.md).

Request/gate and reservation changes must preserve [workflow boundaries](docs/WORKFLOWS.md): local locks are
not distributed leases, authorization strings are not authentication, and fingerprints do not cover every
Python/configuration input. Test rejection paths and process contention without contacting products.
Re-recording reviews requires actual human review; never migrate content fingerprints as new approval.

Keep deployment-to-test binding in `grf.deploy.binding`; product adapters own fresh version acquisition
and the endpoint-to-target mapping. Test the inherited Robot keyword as well as the helper, and reject
missing data rather than substituting the requested artifact version. Report attachment alone is not
runtime validation; see the [public contract](docs/DEPLOYMENT.md#checking-the-observed-product-version).

## Compatibility and releases

Record user-visible changes in CHANGELOG. Keep public keyword names, configuration and CLI behavior stable
within a release line. Deprecate compatibility shims deliberately, with migration instructions, not silently.
Use SemVer (`vX.Y.Z` tags): additive public APIs use a minor release, compatible fixes a patch;
before 1.0, incompatible changes also use a minor release. Release creation/push requires a maintainer decision.
Products pin an approved GRF version in both `pyproject.toml` and `[framework] version` in `grf.toml`.
`grf doctor` checks the installed distribution against the latter. During local development an explicitly selected sibling
checkout may be installed editable, but final integration checks must use the agreed immutable framework
commit in an isolated consumer environment, not a moving sibling; version equality is not a commit-identity check.
CI must also pin the approved
Git commit/release tag and use read-only authentication for private repositories.

Propose upgrades using the selected Git workflow: update both pins, install the proposed core, run the owner's authorized product gates
and review the CHANGELOG. Consumer release automation belongs to the consumer repository, not core.

## Review ownership

Core changes require a GRF maintainer review; product suites and human review ledgers require product test-owner
review. CODEOWNERS lists the current known maintainer; organization teams replace it only after their names
and access are confirmed. CODEOWNERS alone does not enforce review: branch protection must be explicitly enabled.
Agent review never constitutes human approval or permission to remove `review:pending`.

Maintainers should require both Windows/Linux CI checks and current human/code-owner approval, dismiss stale
approvals and block force pushes/deletion. Live GitHub settings are administered separately; this guide does not
assert their current state or alter repository permissions.
