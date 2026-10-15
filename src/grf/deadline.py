"""One monotonic time budget shared by all substeps of an operation."""

from __future__ import annotations

import time
from collections.abc import Callable

from grf.validation import number


class Deadline:
    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self.seconds = number(seconds, "deadline seconds", positive=True)
        self.clock = clock
        self.expires = clock() + self.seconds

    def remaining(self) -> float:
        remaining = self.expires - self.clock()
        if remaining <= 0:
            raise TimeoutError(f"operation exceeded its {self.seconds:g} s deadline")
        return remaining

    def sleep(self, seconds: float, *, sleep: Callable[[float], None] = time.sleep) -> None:
        number(seconds, "sleep seconds")
        budget = self.remaining()
        sleep(min(seconds, budget))
        self.remaining()
