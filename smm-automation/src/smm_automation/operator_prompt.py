"""Moved to ``grf.operator_prompt`` (General Robot Framework); this name stays for existing imports and settings."""

import sys

from grf import operator_prompt as _impl

sys.modules[__name__] = _impl
