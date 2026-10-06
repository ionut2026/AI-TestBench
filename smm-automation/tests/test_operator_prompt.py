import io
import os
import time

import pytest

from smm_automation import operator_prompt
from smm_automation.operator_prompt import OperatorUnavailable, ask_operator, operator_mode


class Console(io.StringIO):
    """stdout that 'types' the given answers into stdin when the prompt is flushed."""

    def __init__(self, write_fd: int, answers: list[str | None]):
        super().__init__()
        self.fd = write_fd
        self.answers = list(answers)

    def flush(self):
        if not self.answers:
            return
        answer = self.answers.pop(0)
        if answer is None:
            os.close(self.fd)
        else:
            os.write(self.fd, answer.encode() + b"\n")


@pytest.fixture
def pipe():
    r, w = os.pipe()
    stdin = os.fdopen(r, "r")
    yield stdin, w
    try:
        os.close(w)  # EOF ends the reader thread
    except OSError:
        pass
    operator_prompt._readers.pop(id(stdin), None)
    stdin.close()


def test_operator_mode(monkeypatch):
    monkeypatch.delenv("SMM_OPERATOR", raising=False)
    assert operator_mode() == "none"
    assert operator_mode("Console") == "console"
    monkeypatch.setenv("SMM_OPERATOR", "dialog")
    assert operator_mode() == "dialog"
    assert operator_mode("none") == "none"
    with pytest.raises(ValueError):
        operator_mode("robot")


def test_no_operator_is_unavailable():
    with pytest.raises(OperatorUnavailable, match="needs an operator"):
        ask_operator("Press E-Stop", "none", 1)


def test_console_done_after_a_wrong_answer(pipe):
    stdin, w = pipe
    out = Console(w, ["yes", "done - pressed"])
    assert ask_operator("Press E-Stop", "console", 5, stdin, out) == "done - pressed"
    assert "Press E-Stop" in out.getvalue() and "Type 'done' or 'fail <reason>'" in out.getvalue()


def test_console_fail(pipe):
    stdin, w = pipe
    with pytest.raises(AssertionError, match="reported a failure: button stuck"):
        ask_operator("Press E-Stop", "console", 5, stdin, Console(w, ["fail button stuck"]))


def test_console_timeout_then_next_prompt_still_works(pipe):
    stdin, w = pipe
    started = time.monotonic()
    with pytest.raises(AssertionError, match="did not confirm within 0.2 s"):
        ask_operator("Press E-Stop", "console", 0.2, stdin, Console(w, []))
    assert time.monotonic() - started < 2
    assert ask_operator("Release E-Stop", "console", 5, stdin, Console(w, ["done"])) == "done"


def test_answers_typed_before_the_prompt_do_not_count(pipe):
    stdin, w = pipe
    answers = operator_prompt._answers(stdin)
    os.write(w, b"done\n")
    deadline = time.monotonic() + 5
    while answers.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(AssertionError, match="reported a failure"):
        ask_operator("Press E-Stop", "console", 5, stdin, Console(w, ["fail late"]))


def test_closed_console_means_no_operator(pipe):
    stdin, w = pipe
    with pytest.raises(OperatorUnavailable, match="closed"):
        ask_operator("Press E-Stop", "console", 5, stdin, Console(w, [None]))
    with pytest.raises(OperatorUnavailable, match="closed"):
        ask_operator("Press E-Stop", "console", 5, stdin, Console(w, []))
