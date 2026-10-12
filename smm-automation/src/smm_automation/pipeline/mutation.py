"""Moved to ``grf.pipeline.mutation`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf.pipeline import mutation as _impl

sys.modules[__name__] = _impl
