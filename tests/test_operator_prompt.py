import subprocess

import pytest

from grf import operator_prompt


def test_dialog_is_bounded_without_opening_a_real_ui(monkeypatch):
    calls = []

    def run(argv, timeout):
        calls.append((argv, timeout))
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr(operator_prompt, "run_process", run)
    with pytest.raises(AssertionError, match="within 0.2 s"):
        operator_prompt.ask_operator("synthetic instruction", "dialog", 0.2)
    assert calls[0][0][-2:] == ["grf.operator_prompt", "synthetic instruction"]
    assert calls[0][1] == 0.2


@pytest.mark.parametrize("code", [0, 1])
def test_dialog_completion_and_failure_are_not_conflated(monkeypatch, code):
    monkeypatch.setattr(operator_prompt, "run_process",
                        lambda argv, timeout: subprocess.CompletedProcess(argv, code, "", "operator failed"))
    if code:
        with pytest.raises(AssertionError, match="operator failed"):
            operator_prompt.ask_operator("synthetic", "dialog", 1)
    else:
        assert operator_prompt.ask_operator("synthetic", "dialog", 1) == "done"
