"""Moved to ``grf.plugin`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf import plugin as _impl

sys.modules[__name__] = _impl
