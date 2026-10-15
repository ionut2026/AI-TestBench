---
name: grf-coordinate-integration
description: Coordinates requested framework and consumer changes through exact contracts, user-selected Git workflow, clear repository ownership and immutable-commit validation.
---

# Coordinate a framework integration

Use this procedure with the integration-coordinator role for explicitly requested coordinated work.
This is an instruction pack, not an executable orchestrator or GRF CLI command.
Repository instructions do not spawn workers, grant permission, authenticate approval or automatically
reconnect sessions. Product mappings belong in product-owned instructions.

## Procedure

1. Discover the current client's available tools and their schemas before using them. When supported, list
   configured projects and sessions, inspect their repository, branch, dirty state and pending work, then reuse
   a session only for the same assignment. Do not invent tool names or assume app session tools are available
   in CLI, IDE or cloud clients. Without them, report the limitation and prepare manual repository handoffs.
2. Identify affected repositories from the requested behavior and dependency direction. Framework owns reusable
   contracts; consumers own adapters, product keywords, configuration and suites. Consumers depend on framework,
   never the reverse. Do not involve unrelated projects or create a worker for every configured repository.
3. Before implementation, exchange an exact contract with affected owners: artifact names/paths, API signatures,
   input/output shapes, failure semantics, configuration and version/dependency pins, compatibility/migration
   rules, validation commands and acceptance criteria. Resolve disagreement explicitly; do not implement
   conflicting guesses. Record who owns each surface and who reports to whom.
4. Honor the user-selected workflow: direct-main commits or isolated branches/PRs, only for affected repositories.
   For direct-main work, use one writer per repository; inspect branch, dirty state and ownership before writing.
   Do not switch or clean up user-owned dirty main. Discover conflicts, report them and ask before proceeding;
   never silently include, overwrite or discard unrelated edits. Coordinate any transfer from an existing
   worktree with its owner before writing main. Do not create branches/PRs against the user's choice.
   For branch/PR work, give each implementation owner an isolated branch. In either mode, give owners a bounded
   assignment with the relevant user request, agreed contract, base commit and stop conditions.
   Preserve direct main checkouts, uncommitted work and other sessions. Do not edit another owner's checkout.
   Do not spawn nested or duplicate workers.
   Starting work, pushing, merging and tagging are distinct actions: each action needs matching user
   authorization where required; a previous PR/deployment approval is not blanket permission.
5. Validate generic framework changes with isolated checks and the synthetic starter. Report the exact framework
   commit SHA. Consumer owners validate against that immutable commit, not a moving branch, mutable sibling
   editable install or package-version equality alone. Use their isolated environment, verify the imported
   framework source and installed provenance, and record both framework and consumer SHAs plus exact commands,
   outcomes and unverified gates. Run only authorized consumer checks; mock passes are not product evidence.
6. Report actionable blockers and contract changes to the coordinator promptly. The final owner handoff includes
   artifacts, commit SHAs, PR links when applicable, validation results, compatibility risks and outstanding human decisions.
   Do not label missing consumer validation as success or send repeated non-actionable status messages.
7. Sequence integration: framework immutable candidate first, consumer candidate validation second;
   human review and authorized framework integration/release before final consumer release-pin promotion.
   Use PRs only when that workflow is selected. If integration changes the validated framework commit,
   revalidate the consumer against the resulting commit.
   A release/tag is a separate maintainer decision. Synchronize dependency and configured framework version
   pins in the consumer, verify the released commit identity, and rerun affected checks before consumer integration.
   Preparation may proceed in parallel in isolated workspaces; direct-main writes remain single-owner.
   Do not merge/tag automatically or bypass branch protections; if protections prevent direct-main commits,
   report the conflict and ask rather than disabling protections or silently changing workflow.

## Safety and adoption

- Keep generic copies free of product names, local paths, credentials and instrument details.
- Adopt `.github\agents\integration-coordinator.agent.md` and
  `.github\skills\grf-coordinate-integration\SKILL.md` together from one reviewed immutable framework commit.
  Keep shared copies unchanged; document product-specific mappings separately. Package upgrades do not
  rewrite repository files. New starters receive both files; existing products review and copy deliberately.
- Do not change models, account settings, OS settings, credentials or permissions. Do not trigger Jenkins,
  start deployments or operate instruments as integration validation.
- RV&S access is read-only. Product/instrument actions require the instrument owner's authorization.
  Agent review never replaces current-hash human approval. Instructions and authorization text are not
  authenticated approval; preserve the real owner's decision and repository protections.
