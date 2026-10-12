"""Moved to ``grf.pipeline.ingest`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf.pipeline import ingest as _impl

sys.modules[__name__] = _impl
