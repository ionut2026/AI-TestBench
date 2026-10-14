---
name: grf-validate-change
description: Validates changes to GeneralRobotFramework core, templates, skills, setup or documentation with the smallest sufficient checks.
---

# Validate a GRF change

Use this skill in the GeneralRobotFramework repository after changing code, templates, skills, setup or docs.
Canonical details: `docs\TESTING.md`, `docs\SKILLS.md` and `CONTRIBUTING.md`.

## Steps

1. Work from the GRF checkout root on the requested branch; keep scratch output under `results`.
2. Run targeted tests first, for example
   `.\.venv\Scripts\python -m pytest -q tests\test_scaffold.py tests\test_skills.py --basetemp results\pytest-skills`.
3. Run `.\.venv\Scripts\ruff check src tests` and `.\.venv\Scripts\mypy`.
4. For template, agent or skill changes, verify that `examples\demo` matches `grf init` output.
5. For setup changes, run `tests\test_setup.py`.
6. Run the full suite with a new `--basetemp results\pytest-<name>` when shared behavior changed.
7. Verify README, `docs`, CONTRIBUTING, CHANGELOG and starter docs remain accurate.
8. Report commands, pass/fail counts and anything not verified.

## Safety

- Mock passes and synthetic demo results are never product evidence.
- Do not add SMM, Windchill or other product code or exports to GRF core.
- RV&S access is read-only. Product or instrument actions require the instrument owner's authorization.
- Agent review never replaces current-hash human approval. Do not commit or push unless requested.
