"""Content fingerprint of a test: what a human review approves besides the specification hash.

The fingerprint is a SHA-256 over

* the test itself (name, documentation, tags except ``review:pending``, timeout, setup, teardown, template, body),
* its suite's own settings (suite setup/teardown, imports, variables and keywords), and
* the text of every Robot resource file the suite imports, followed transitively.

Line numbers and absolute paths are left out, so moving a test or checking the project out somewhere else keeps the
fingerprint. Python library code is not part of it; the source revision recorded with a run covers that. A shared
resource change therefore changes the fingerprint of every test that imports the resource: the reviewer checks the
resource diff and records the reviews again (``<cli> review <spec tag> ...`` takes several tests at once).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

FINGERPRINT_SCHEME = "grf-content-1"
_DROP = {"lineno", "source", "error"}
_PENDING = "review:pending"


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in sorted(value.items()) if k not in _DROP}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _resolve(name: str, directory: Path) -> Path | None:
    name = name.replace("${CURDIR}", str(directory)).replace("${/}", "/")
    if "${" in name:
        return None
    path = Path(name)
    path = path if path.is_absolute() else directory / path
    return path.resolve() if path.is_file() else None


def _resource_texts(imports, directory: Path, seen: dict[Path, str], unresolved: list[str]) -> None:
    from robot.running import ResourceFileBuilder

    for imp in imports:
        if imp.type != "RESOURCE":
            continue
        path = _resolve(str(imp.name), Path(imp.directory or directory))
        if path is None:
            unresolved.append(str(imp.name))
            continue
        if path in seen:
            continue
        seen[path] = path.read_bytes().decode("utf-8", "replace").replace("\r\n", "\n")
        _resource_texts(ResourceFileBuilder().build(str(path)).imports, path.parent, seen, unresolved)


def suite_context(suite) -> dict[str, Any]:
    """The part of a fingerprint that the suite contributes (shared by all its tests)."""
    directory = Path(suite.source).parent if suite.source else Path.cwd()
    seen: dict[Path, str] = {}
    unresolved: list[str] = []
    _resource_texts(suite.resource.imports, directory, seen, unresolved)
    return {
        "setup": _clean(suite.setup.to_dict()) if suite.setup else None,
        "teardown": _clean(suite.teardown.to_dict()) if suite.teardown else None,
        "resource": _clean(suite.resource.to_dict()),
        "resources": sorted(hashlib.sha256(t.encode("utf-8")).hexdigest() for t in seen.values()),
        "unresolved": sorted(unresolved),
    }


def fingerprint_of(test, context: dict[str, Any]) -> str:
    data = _clean(test.to_dict())
    data["tags"] = sorted(t for t in data.get("tags", []) if t.lower() != _PENDING and not t.lower().startswith("spechash:"))
    blob = json.dumps({"scheme": FINGERPRINT_SCHEME, "test": data, "suite": context}, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
