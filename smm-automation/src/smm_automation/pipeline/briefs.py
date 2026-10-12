"""Moved to ``grf.pipeline.briefs`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf.pipeline import briefs as _impl

sys.modules[__name__] = _impl
