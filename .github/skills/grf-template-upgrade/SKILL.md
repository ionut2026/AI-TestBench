---
name: grf-template-upgrade
description: Compares a product's copied GRF agents, skills and starter files with a newer GRF release and adapts changes safely.
---

# Upgrade copied GRF templates

`grf init` copies templates into a new product project. Upgrading the GRF Python package does not update those
copies, so products must review them deliberately.

## Steps

1. Identify the product's pinned GRF version and the target GRF version.
2. In a scratch directory under the product's ignored `results` folder, run `grf init` with the new GRF.
3. Compare generated `.github\agents`, `.github\skills`, README guidance and other relevant starter files with
   the product copies.
   `grf scaffold --update-skills --check` previews managed generic updates. After review, use
   `grf scaffold --update-skills`; it refuses customized/untracked conflicts and leaves product overlays intact.
4. Classify differences: generic improvement to adopt, product customization to keep, or irrelevant demo code.
5. Apply minimal changes, preserving product-specific knowledge and stricter local rules.
6. Update product documentation, dependency pin and `[framework] version` together.
7. Run product tests, `grf lint`, `grf drift --strict` and safe mock checks.

## Safety

- Never overwrite product files wholesale or delete customizations without review.
- Do not copy demo counter code or fake fixtures into real product suites.
- Upgrades do not change review or evidence status; mock checks are not product evidence.
- Real-product or instrument validation requires the instrument owner's authorization. RV&S access is read-only.
