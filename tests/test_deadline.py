import pytest
from deploy_fakes import Clock

from grf.deadline import Deadline


def test_one_budget_for_all_substeps():
    clock = Clock()
    deadline = Deadline(3, clock=clock.monotonic)
    deadline.sleep(2, sleep=clock.sleep)
    assert deadline.remaining() == 1
    with pytest.raises(TimeoutError):
        deadline.sleep(2, sleep=clock.sleep)
    assert clock.now == 3


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), -1, 0, True])
def test_invalid_deadline(seconds):
    with pytest.raises(ValueError):
        Deadline(seconds)
