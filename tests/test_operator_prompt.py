import io
import os
import subprocess
import time

import pytest

from grf import operator_prompt


class Console(io.StringIO):
    """Supply the next operator answer only after its prompt is flushed."""

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
    read_fd, write_fd = os.pipe()
    stdin = os.fdopen(read_fd, "r")
    try:
        yield stdin, write_fd
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass  # The closed-input scenario already closed the writer.
        stdin.close()
        with operator_prompt._readers_lock:
            operator_prompt._readers.pop(id(stdin), None)


def test_console_done_after_a_wrong_answer(pipe):
    stdin, write_fd = pipe
    out = Console(write_fd, ["yes", "done - pressed"])
    assert operator_prompt.ask_operator("Press the demo button", "console", 5, stdin, out) == "done - pressed"
    assert "Press the demo button" in out.getvalue()
    assert "Type 'done' or 'fail <reason>'" in out.getvalue()


def test_console_fail(pipe):
    stdin, write_fd = pipe
    with pytest.raises(AssertionError, match="reported a failure: button stuck"):
        operator_prompt.ask_operator(
            "Press the demo button", "console", 5, stdin, Console(write_fd, ["fail button stuck"]),
        )


def test_console_timeout_then_next_prompt_still_works(pipe):
    stdin, write_fd = pipe
    started = time.monotonic()
    with pytest.raises(AssertionError, match="did not confirm within 0.2 s"):
        operator_prompt.ask_operator("Press the demo button", "console", 0.2, stdin, Console(write_fd, []))
    assert time.monotonic() - started < 2
    assert operator_prompt.ask_operator(
        "Release the demo button", "console", 5, stdin, Console(write_fd, ["done"]),
    ) == "done"


def test_answers_typed_before_the_prompt_do_not_count(pipe):
    stdin, write_fd = pipe
    answers = operator_prompt._answers(stdin)
    os.write(write_fd, b"done\n")
    deadline = time.monotonic() + 5
    while answers.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not answers.empty(), "Reader did not receive the stale answer before the prompt"
    with pytest.raises(AssertionError, match="reported a failure: late"):
        operator_prompt.ask_operator(
            "Press the demo button", "console", 5, stdin, Console(write_fd, ["fail late"]),
        )


def test_closed_console_means_no_operator(pipe):
    stdin, write_fd = pipe
    with pytest.raises(operator_prompt.OperatorUnavailable, match="closed"):
        operator_prompt.ask_operator("Press the demo button", "console", 5, stdin, Console(write_fd, [None]))
    with pytest.raises(operator_prompt.OperatorUnavailable, match="closed"):
        operator_prompt.ask_operator("Press the demo button", "console", 5, stdin, Console(write_fd, []))


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
