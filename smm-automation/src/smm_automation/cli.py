"""Moved to ``grf.cli`` (General Robot Framework); this name stays for existing imports and settings
(e.g. ``python -m smm_automation.cli`` in a site's ``SMM_RIG_CONTROL``)."""

import sys

from grf import cli as _impl

if __name__ == "__main__":
    sys.exit(_impl.main())
sys.modules[__name__] = _impl
