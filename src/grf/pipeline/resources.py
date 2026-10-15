"""Static Robot resource resolution shared by lint and review fingerprints."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_resource(name: str, directory: Path) -> Path | None:
    name = name.replace("${CURDIR}", str(directory)).replace("${/}", os.sep)
    if "${" in name or "%{" in name:
        return None
    path = Path(name)
    path = path if path.is_absolute() else directory / path
    return path.resolve() if path.is_file() else None
