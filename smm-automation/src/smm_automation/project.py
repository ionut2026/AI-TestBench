"""Moved to ``grf.project`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf import project as _impl

sys.modules[__name__] = _impl
