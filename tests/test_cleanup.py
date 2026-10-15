import pytest

from grf.cleanup import CleanupError, CleanupGuard


def test_cleanup_attempts_all_actions_and_publishes_verified_final_state():
    guard, seen = CleanupGuard(), []
    result = guard.run([("restore", lambda: seen.append("restore")), ("disconnect", lambda: seen.append("disconnect"))],
                       lambda: True, publish=lambda result: seen.append(result.as_dict()))
    assert seen[:2] == ["restore", "disconnect"]
    assert result.final_state_verified and not result.quarantined
    guard.require_ready()


def test_cleanup_aggregates_failures_and_quarantines_later_tests():
    guard, seen = CleanupGuard(), []
    def fail():
        raise RuntimeError("restore unavailable")
    with pytest.raises(CleanupError) as error:
        guard.run([("restore", fail), ("disconnect", lambda: seen.append("disconnect"))], lambda: False,
                  publish=lambda result: seen.append(result))
    result = error.value.result
    assert [step.name for step in result.steps if step.status == "FAIL"] == ["restore", "final-state"]
    assert result.quarantined and not result.final_state_verified and seen[0] == "disconnect"
    with pytest.raises(AssertionError, match="quarantined"):
        guard.require_ready()
    with pytest.raises(AssertionError, match="remains"):
        guard.reconcile(lambda: False)
    with pytest.raises(CleanupError):
        guard.run([], lambda: True)  # success does not automatically release previous quarantine
    guard.reconcile(lambda: True)
    guard.require_ready()


@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_final_probe_requires_exact_true(value):
    with pytest.raises(CleanupError):
        CleanupGuard().run([], lambda: value)


def test_cleanup_preserves_cancellation_after_remaining_actions_and_probe():
    guard, seen = CleanupGuard(), []
    def cancel():
        raise KeyboardInterrupt("cancelled")
    with pytest.raises(KeyboardInterrupt):
        guard.run([("first", cancel), ("second", lambda: seen.append("second"))],
                  lambda: seen.append("probe") or True, publish=lambda result: seen.append("publish"))
    assert seen == ["second", "probe", "publish"]
    assert guard.quarantined and guard.last_result is not None


def test_outcome_publishing_failure_is_not_silenced():
    guard = CleanupGuard()
    def fail(result):
        raise OSError("record disk unavailable")
    with pytest.raises(CleanupError, match="publish.*disk unavailable"):
        guard.run([], lambda: True, publish=fail)
    assert guard.last_result.quarantined


def test_cleanup_validation_precedes_actions():
    seen = []
    with pytest.raises(ValueError, match="unique"):
        CleanupGuard().run([("same", lambda: seen.append(1)), ("same", lambda: seen.append(2))], lambda: True)
    assert seen == []
