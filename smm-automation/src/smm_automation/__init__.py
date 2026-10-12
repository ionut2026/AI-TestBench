"""SMM test automation framework (Python side): service client, Robot keywords, SMM plugin.

The generic parts (project config, CLI, RV&S pipeline, rig control) are the General Robot Framework (``grf``);
this package registers the SMM project as the default so ``smm-auto`` and the Robot library find it from anywhere.
"""

from pathlib import Path

from grf import project as _project

__version__ = "0.1.0"

#: smm-automation/ (the framework root: grf.toml, service/, robot/, catalog/ live here)
FRAMEWORK_ROOT = Path(__file__).resolve().parents[2]

_project.set_default_root(FRAMEWORK_ROOT)
