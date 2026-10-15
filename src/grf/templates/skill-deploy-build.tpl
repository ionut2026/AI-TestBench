---
name: grf-deploy-build
description: Explains, plans and diagnoses GRF deployments of Jenkins builds to instrument boards without ever starting a deployment itself.
---

# Deploy a build to a board

**Deploying** means putting one Jenkins build of a product component (for example an application packed as a Docker
image tarball) on the instrument board that runs it. `grf deploy` does this safely: it checks the board first,
keeps the previous build and rolls back automatically when the new one does not come up. Only products whose plugin
defines `deploy_components()` can deploy. The full guide is `docs/DEPLOYMENT.md` in GeneralRobotFramework.

The commands below use `grf`; a product with its own CLI name uses that name instead (for example `smm deploy`).

## What an agent may do

1. Explain the components and their targets: `grf deploy components`.
2. List Jenkins builds or branches (read-only): `grf deploy builds --component <name>`.
3. Read what runs on a board, only with the instrument owner's authorization for that board:
   `grf deploy status --component <name>` (read-only, but it logs in to the board).
4. Check readiness without the board: `grf doctor --deploy`. `--deploy --deep` logs in to the board; the same
   authorization rule applies.
5. Read a deployment record (`deployment.json` and `deploy.log` in `results/deploy/...`) and explain the outcome
   and exit code (DEPLOYED 0, UNCHANGED 0, DRY_RUN 0, RESTORED 0, PREFLIGHT_FAILED/FAILED 3, ARTIFACT_FAILED 4,
   ROLLED_BACK 5, ROLLBACK_FAILED 6, LOCKED 7, HELD 8).
6. Suggest a dry run for a human to start: `grf deploy plan --component <name>` downloads and checks the build and
   prints the board commands, but changes nothing.

## What an agent never does

- Never start `grf deploy run` or `grf deploy rollback`, and never start the Jenkins deployment job. Starting the
  authorized Jenkins job (or typing the target name at the CLI prompt) is the instrument owner's authorization;
  an agent cannot give it.
- Never pass `--yes`, `--force`, `--ignore-hold` or `--break-stale-lock` on someone's behalf.
- Never write a password, token or board address into the repository, a skill, a prompt or a report. Passwords
  come only from an environment variable or a file outside the checkout.
- Never weaken host-key checking, never suggest `insecure_tls = true` as a fix, never run
  `docker image prune -a` or delete containers, images or `previous_builds` on a board.
- Never edit the site deployment config of a rig; describe the change and let its owner make it.

## Diagnosing a failed deployment

- `PREFLIGHT_FAILED` or `ARTIFACT_FAILED`: nothing on the board changed. Read the failing step in the record.
- `ROLLED_BACK`: the new build failed its checks and the previous build runs again. Report the failing step and
  the build; the product team investigates the build.
- `ROLLBACK_FAILED`: the board may not run anything. Show the `recovery` commands from the record to the
  instrument owner; do not run them.
- `LOCKED` or `HELD`: someone else is deploying, or the owner reserved the board. Ask the owner.

## Evidence

A successful deployment proves only that the build was installed and started. It is not product evidence: the
tests that run afterwards, with human-reviewed results, are. A mock or fake-transport deployment proves nothing
about the instrument. RV&S access stays read-only.
