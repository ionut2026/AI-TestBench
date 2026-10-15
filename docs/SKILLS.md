# GRF Copilot skills

A **Copilot skill** is a short Markdown instruction pack for one kind of task. It is stored as
`.github\skills\<skill-name>\SKILL.md`. Copilot tools that support agent skills read each skill's `name` and
`description`, then load the full instructions when your request matches that task.

A skill is not Robot Framework code, a GRF command, a test, or a background service. It does not run anything by
itself. It tells Copilot which safe procedure to follow, such as "lint, check drift, then run the mock."

## Skills, agents and plugins

| Item | What it is | Example |
|---|---|---|
| Skill | Reusable procedure for one task | `grf-author-test`: steps from brief to test |
| Agent | Selectable role and responsibility, not an automatically running process | `test-author` writes; `test-reviewer` reviews |
| Product plugin | Python code that extends GRF | SMM adds an SMM command or brief section |

Agents can use skills. For example, the starter `test-author` agent tells Copilot to use `grf-author-test`.

## Available skills

| Skill | Use it when | Where |
|---|---|---|
| `grf-getting-started` | Installing GRF/product projects, explaining or running the mock demo | GRF and new products |
| `grf-author-test` | Writing or updating a test from a brief | GRF and new products |
| `grf-review-test` | Reviewing a test for traceability, false passes and false failures | GRF and new products |
| `grf-interpret-report` | Understanding failed runs, Robot logs or traceability reports | GRF and new products |
| `grf-spec-drift` | Handling `grf drift`, `accept` and `migrate-hashes` findings | GRF and new products |
| `grf-mutation-testing` | Running or interpreting `grf mutate` on mock tiers | GRF and new products |
| `grf-doctor-tier` | Checking whether a tier is ready with `grf doctor`, or a machine with `grf doctor --deploy` | GRF and new products |
| `grf-deploy-build` | Explaining or planning a deployment, reading `deploy status` or a deployment record | GRF and new products |
| `grf-product-plugin` | Implementing `ProductPlugin` hooks | GRF and new products |
| `grf-template-upgrade` | Reviewing copied agents, skills and starter files after a GRF upgrade | GRF and new products |
| `grf-coordinate-integration` | Coordinating explicitly requested framework/consumer changes | GRF and new products |
| `grf-validate-change` | Validating GRF core, templates, setup or documentation changes | GRF repository only |

## How to use a skill

1. Open Copilot in the repository that contains the skill:
   - GRF: the assigned GRF checkout, following the user's chosen Git workflow
   - a product: that product's root folder, which contains `grf.toml`
2. Ask normally. Copilot can choose a skill whose description matches your request.
3. To be explicit, name the skill in your request.

Examples from the synthetic demo:

```text
Use the grf-getting-started skill and explain how to run the mock demo.
Use the grf-author-test skill to write a test from generated\briefs\SPEC-1.md.
Use the grf-review-test skill to review robot\suites\demo\counter.robot.
Use the grf-interpret-report skill to explain results\mock\traceability.json.
```

In Copilot CLI, `/skills` lists available skills when your version supports it. Other Copilot surfaces may load
skills automatically without a list command. If a skill is not found, confirm that Copilot opened the correct
repository root and that `.github\skills\<skill-name>\SKILL.md` exists.

## How skills reach product projects

`grf init` copies the eleven generic skills into the new project's `.github\skills` folder. The checked-in
`examples\demo` contains the same files, and tests check that they match.

Existing products, such as SMMAutomationFramework, do **not** receive skills automatically when GRF is
upgraded. Use `grf-template-upgrade` to compare the new templates with the product's copies, then adopt only
reviewed changes. Product-specific knowledge belongs in product-owned skills or agents, not in GRF core.

## Coordinated integration

Select `integration-coordinator`, stored in `.github\agents\integration-coordinator.agent.md`, and request
`grf-coordinate-integration`, stored in `.github\skills\grf-coordinate-integration\SKILL.md`.
See [the beginner example](GETTING-STARTED.md#how-do-i-ask-for-coordinated-changes).
In Copilot CLI, [`/agent` selects an agent](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/invoke-custom-agents);
other clients have their own agent picker. Supported agent
frontmatter is documented in GitHub's [custom-agent reference](https://docs.github.com/en/copilot/reference/custom-agents-configuration).
This agent uses only `name` and `description`: no model override, MCP registration or assumed session-tool list.
Omitting `tools` inherits available client tools; it does not install or grant access to new tools.
The skill uses the documented [`SKILL.md` name/description format](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills),
without scripts or pre-approved tool permissions.

The shared skill is the canonical integration procedure: discover actual project/session capabilities, delegate
only affected repositories, agree exact APIs and dependency direction before implementation, validate consumers
against an immutable framework SHA, report evidence and sequence framework release before consumer promotion.
App session tools are not guaranteed in CLI, IDE or cloud clients. When absent, use explicit manual handoffs,
not invented tool calls. Instructions do not start workers or automatically reconnect sessions.

Existing products adopt **both files together** from one reviewed immutable GRF commit; keep their contents
unchanged and put local repository mapping, product safety and gates in product-owned instructions.
New `grf init` starters already include both. Installing a newer Python package does not rewrite either file.
For example, GRF owns generic infrastructure and SMMAutomationFramework owns SMM adapters/suites;
this mapping is an example, not hard-coded into the reusable agent or skill.
Do not replace a product's specialized author/reviewer with the coordinator.

Keep direct main checkouts and uncommitted work safe. Honor the user-selected direct-main or branch/PR workflow.
Direct-main work uses one writer per repository, with branch/dirty-state/ownership discovery before writing.
Ask on conflicts; never switch or clean up user-owned dirty main, silently transfer edits or create unwanted PRs.
If protections block direct-main commits, report and ask; do not bypass them or silently switch workflows.
Push, merge and tag require explicit authorization matching the action, repository and change. Prior deployment
or PR approval does not carry forward. Preserve protections and current human review. No Jenkins trigger,
instrument operation, model/account/OS setting, credential or permission change is implied.

## Safety boundaries

Every skill follows these rules:

- Mock, synthetic, dry-run and non-evidence results are not product evidence.
- RV&S access is read-only.
- Real-product, rig or instrument actions require the instrument owner's authorization.
- Agent review never replaces current-hash human approval.
- Agents never start a deployment (`deploy run` or `deploy rollback`); they may explain, run `deploy plan` or
  `deploy status`, and read records. Starting the authorized Jenkins deployment job is the owner's decision.
  See [Deploying builds](DEPLOYMENT.md).

Skills guide Copilot but do not grant permissions. A skill cannot authorize instrument use, RV&S writes or
human approval.

## Change a skill

For a shared skill, edit `src\grf\templates\skill-<name>.tpl`, then copy the same text to:

- `.github\skills\grf-<name>\SKILL.md`
- `examples\demo\.github\skills\grf-<name>\SKILL.md`

Keep the frontmatter `name` equal to the folder name and make the `description` clearly state when to use it.
Run `tests\test_skills.py` and `tests\test_scaffold.py`; see [Testing](TESTING.md#core-validation-windows).
