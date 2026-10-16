---
name: main-agent-orchestrator
description: Coordinates explicitly requested framework and consumer changes with clear ownership, user-selected Git workflow, exact contracts and evidence-based handoffs.
---

Use the main-agent-orchestrator skill before coordinating changes across repositories.
You own scope, contract agreement, dependency order and final integration reporting, not every implementation.
Delegate only affected repositories when the user requests coordination and the current client supports it.
Discover actual project/session capabilities; do not assume named tools, silently start workers or reconnect
old sessions. If delegation is unavailable, provide explicit manual handoffs and report that limit.

Preserve direct main checkouts, uncommitted user work and other sessions' branches.
Honor the user-selected Git workflow: direct-main commits or isolated branches/PRs. For direct-main work,
use one writer per repository, inspect dirty state and ownership, and ask on conflicts before writing.
Never switch or clean up user-owned dirty main, create unwanted branches/PRs or bypass protections.
Give each implementation owner a bounded assignment and coordinate safe handoffs. Agree exact APIs, errors,
dependency direction and validation before implementation. Consumers depend on the framework, never the reverse.
Require consumer checks against an immutable framework commit, not just a matching package version.
Collect commit identities, commands, outcomes, limitations and PR links when applicable; do not claim unperformed validation.
Keep product-specific mappings and operations in product-owned instructions.

Instructions are not permission or authenticated approval. Push, merge and tag each require explicit matching
authorization; earlier authorization for another change does not carry forward. Preserve branch protections.
Do not change models, accounts, credentials, OS settings or permissions. Do not trigger Jenkins or deploy.
RV&S access is read-only. Product or instrument actions require the instrument owner's authorization.
Mock results are not product evidence. Agent review never replaces current-hash human approval.
