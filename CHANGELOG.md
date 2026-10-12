# Changelog

GRF uses Semantic Versioning. Before 1.0, incompatible API changes require a minor release; compatible fixes use
a patch release. Release tags are `vMAJOR.MINOR.PATCH`. Tags and publication are explicit maintainer actions.

## 0.1.0 - 2026-10-12

- Extracted generic project configuration, plugins, CLI and read-only RV&S traceability pipeline from SMM.
- Added reusable Robot timing/operator/rig helpers, preserving SMM compatibility shims.
- Added packaged `grf init`, a synthetic demo, independent tests and reusable Windows/Linux CI.
- Added exact approved-version checks in doctor via `[framework] version`.

First extracted core release. Product dependencies pin the `v0.1.0` Git tag.
