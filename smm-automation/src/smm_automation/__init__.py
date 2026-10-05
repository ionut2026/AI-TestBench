"""SMM test automation framework (Python side): service client, Robot keywords, RV&S pipeline."""

from pathlib import Path

__version__ = "0.1.0"

#: smm-automation/ (the framework root: service/, robot/, catalog/ live here)
FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]
