---
name: grf-product-plugin
description: Guides safe implementation of GRF ProductPlugin hooks for product-specific commands, briefs and doctor checks.
---

# Write a product plugin

A **plugin** is product-owned Python code configured as `[project] plugin` in `grf.toml`.
It extends GRF without adding product code to GRF core.

## Hooks

- `known_messages()`: names the catalog may mention, or `None` to accept configured pattern matches.
- `brief_sections(spec)`: extra Markdown for briefs, such as product interface schemas.
- `doctor_class()`: a `grf.doctor.Doctor` subclass with product readiness checks.
- `add_commands(sub)`: product CLI commands returning command name to handler.

## Steps

1. Keep product interfaces, adapters and knowledge in the product repository.
2. Implement the smallest hook needed; handlers return an integer exit code.
3. Validate inputs and print explicit actionable errors.
4. Add product tests for each hook and update product documentation.
5. Run project tests, `grf lint`, `grf drift --strict` and a safe mock run.

## Safety

- Commands must clearly state whether they contact a product, instrument or RV&S.
- RV&S access is read-only.
- Real-product or instrument actions require the instrument owner's authorization.
- Plugins must not create approvals or turn mock results into product evidence.
