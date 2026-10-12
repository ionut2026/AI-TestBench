"""Moved to ``grf.pipeline.drift`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf.pipeline import drift as _impl

sys.modules[__name__] = _impl
