"""Moved to ``grf.pipeline.lint`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf.pipeline import lint as _impl

sys.modules[__name__] = _impl
