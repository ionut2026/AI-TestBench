"""Moved to ``grf.doctor`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf import doctor as _impl

sys.modules[__name__] = _impl
