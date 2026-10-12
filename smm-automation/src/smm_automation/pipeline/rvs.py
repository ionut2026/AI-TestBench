"""Moved to ``grf.pipeline.rvs`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf.pipeline import rvs as _impl

sys.modules[__name__] = _impl
