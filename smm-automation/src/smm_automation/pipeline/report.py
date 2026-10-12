"""Moved to ``grf.pipeline.report`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf.pipeline import report as _impl

sys.modules[__name__] = _impl
