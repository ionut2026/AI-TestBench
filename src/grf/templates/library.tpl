from robot.api.deco import keyword, library

from grf.library import GrfLibrary


@library(scope="GLOBAL", auto_keywords=False)
class ProductLibrary(GrfLibrary):
    """Example product adapter: a synthetic counter, not a real product."""

    def __init__(self) -> None:
        super().__init__()
        self.value = 0

    @keyword
    def reset_counter(self) -> None:
        """Reset the mock counter before each test."""
        self.value = 0

    @keyword
    def request_increment(self) -> None:
        """Ask the mock counter to increment once."""
        self.value += 1

    @keyword
    def counter_should_be(self, expected: int) -> None:
        """Assert the mock counter's observable value."""
        if self.value != expected:
            raise AssertionError(f"Counter is {self.value}, expected {expected}")
