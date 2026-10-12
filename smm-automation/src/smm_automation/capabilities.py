"""Moved to ``grf.capabilities`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf import capabilities as _impl

sys.modules[__name__] = _impl
