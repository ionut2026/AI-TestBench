"""Read-only assertions linking a deployment record to a product-owned version observation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grf.deploy.model import Outcome
from grf.deploy.record import load_records


def assert_product_version(
    record: str | Path,
    component: str,
    actual_version: str,
    *,
    target: str,
    project: str | None = None,
) -> dict[str, Any]:
    """Return the matching record, or raise ``AssertionError``.

    ``record`` is one explicit JSON file, not a folder or an inferred latest record.
    Versions are exact strings, with no normalization or artifact-version fallback.
    The product must obtain a fresh observation and establish the endpoint-to-target
    mapping; matching versions and target labels cannot prove physical board identity.
    ``project=None`` omits only the expected-project comparison.
    """
    expected = {"component": component, "target": target, "actual_version": actual_version}
    if project is not None:
        expected["project"] = project
    for name, expected_value in expected.items():
        if not isinstance(expected_value, str) or not expected_value.strip():
            raise AssertionError(f"Deployment binding requires a nonempty string for {name}")
    if not isinstance(record, (str, Path)) or not str(record).strip():
        raise AssertionError("Deployment binding requires an explicit record file")
    path = Path(record)
    if not path.is_file():
        raise AssertionError(f"Deployment binding requires one existing record file: {path}")
    try:
        data = load_records([path])[0]
    except ValueError as err:
        raise AssertionError(str(err)) from err

    if type(data["schema"]) is not int:
        raise AssertionError(f"{path}: deployment schema must be an integer")
    for name in ("project", "component", "target"):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            raise AssertionError(f"{path}: deployment record requires a nonempty {name}")
    for name, value in (("component", component), ("target", target), ("project", project)):
        if value is not None and data[name] != value:
            raise AssertionError(f"{path}: deployment {name} is {data[name]!r}, expected {value!r}")
    if data.get("dryRun") is not False:
        raise AssertionError(f"{path}: deployment must explicitly have dryRun=false")
    if data.get("outcome") not in (Outcome.DEPLOYED, Outcome.UNCHANGED, Outcome.RESTORED):
        raise AssertionError(f"{path}: deployment outcome {data.get('outcome')!r} does not confirm an installed build")
    installed = data.get("installed")
    if not isinstance(installed, dict) or installed.get("status") != "running":
        raise AssertionError(f"{path}: deployment requires an installed running component")
    version = installed.get("version")
    if not isinstance(version, str) or not version.strip():
        raise AssertionError(f"{path}: deployment requires a nonempty installed version")
    if version != actual_version:
        raise AssertionError(f"{path}: observed {component} version {actual_version!r}, expected installed version {version!r}")
    return data
