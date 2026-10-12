"""Moved to ``grf.rigcontrol`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf import rigcontrol as _impl

sys.modules[__name__] = _impl
