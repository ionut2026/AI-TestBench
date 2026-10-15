"""Exact request selection applied before Robot starts suite setup or any test action."""

from __future__ import annotations

import base64
import json

from robot.api import SuiteVisitor

from grf.acceptance import RunRequest
from grf.pipeline.drift import TestRef, require_unique_tests, test_key


def select(request: RunRequest, tests: list[TestRef]) -> list[TestRef]:
    """Select the union of specification and exact test-name requests, or all when neither is given."""
    require_unique_tests(tests)
    for name in request.tests:
        found = [t for t in tests if t.name == name]
        if len(found) != 1:
            raise ValueError(f"request test {name!r} must match exactly one test, got {len(found)}")
    missing = set(request.specs) - {spec for test in tests for spec in test.specs}
    if missing:
        raise ValueError(f"request specification(s) without tests: {', '.join(map(str, sorted(missing)))}")
    chosen = [t for t in tests if not (request.specs or request.tests) or t.name in request.tests
              or set(t.specs).intersection(request.specs)]
    if len(chosen) < request.min_tests:
        raise ValueError(f"request selects {len(chosen)} test(s), requires at least {request.min_tests}")
    return chosen


def modifier(tests: list[TestRef]) -> str:
    payload = json.dumps([test_key(test) for test in tests]).encode("utf-8")
    return "grf.selection.RequestSelection:" + base64.urlsafe_b64encode(payload).decode("ascii")


class RequestSelection(SuiteVisitor):
    def __init__(self, encoded: str):
        raw = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
        self.chosen = {tuple(value) for value in raw}

    def start_suite(self, suite):
        parts, current = [], suite
        while current.parent is not None:
            parts.append(current.name)
            current = current.parent
        path = ".".join(reversed(parts))
        suite.tests = [test for test in suite.tests if (path, test.name) in self.chosen]

    def end_suite(self, suite):
        suite.suites = [child for child in suite.suites if child.test_count]
