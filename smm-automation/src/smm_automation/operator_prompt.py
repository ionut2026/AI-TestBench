"""Manual steps done by an operator at the instrument (plan 6.3): ``Operator Action``.

Modes (``${OPERATOR}`` or the environment variable ``SMM_OPERATOR``; ``smm-auto run --operator <mode>`` sets it):
    none      no operator: the step fails as unavailable (tests needing one are excluded by smm-auto run)
    console   the instruction is printed on the console; the operator types ``done`` (or ``fail <reason>``)
    dialog    a Robot Framework dialog (robot.libraries.Dialogs) with PASS/FAIL buttons
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
from typing import TextIO

MODES = ("none", "console", "dialog")
ENV = "SMM_OPERATOR"


class OperatorUnavailable(RuntimeError):
    pass


def operator_mode(configured: str | None = None) -> str:
    mode = (configured or os.environ.get(ENV) or "none").strip().lower()
    if mode not in MODES:
        raise ValueError(f"Operator mode '{mode}' is not one of {', '.join(MODES)}")
    return mode


def ask_operator(instruction: str, mode: str, timeout_s: float, stdin: TextIO | None = None, stdout: TextIO | None = None) -> str:
    """Shows ``instruction`` and waits for the operator. Returns what they confirmed; raises AssertionError when they
    report a failure or do not answer within ``timeout_s``, OperatorUnavailable when there is no operator."""
    if mode == "none":
        raise OperatorUnavailable(f"This step needs an operator at the instrument (run with --operator console|dialog): {instruction}")
    if mode == "dialog":
        from robot.libraries.Dialogs import execute_manual_step

        execute_manual_step(f"{instruction}\n\nPASS when done, FAIL if it could not be done.", "The operator could not do it")
        return "done"
    return _ask_console(instruction, timeout_s, stdin or sys.__stdin__, stdout or sys.__stdout__)


def _ask_console(instruction: str, timeout_s: float, stdin: TextIO | None, stdout: TextIO | None) -> str:
    if stdin is None or stdout is None:
        raise OperatorUnavailable("No console for the operator (stdin/stdout are not available)")
    answers = _answers(stdin)
    while not answers.empty():  # lines typed before this prompt do not confirm it
        if answers.get_nowait() is None:
            answers.put(None)
            raise OperatorUnavailable("The console input was closed: no operator")
    stdout.write(f"\n=== OPERATOR ACTION ===\n{instruction}\nType 'done' when done, or 'fail <reason>' "
                 f"(within {timeout_s:g} s): ")
    stdout.flush()
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            answer = answers.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            raise AssertionError(f"The operator did not confirm within {timeout_s:g} s: {instruction}") from None
        if answer is None:
            answers.put(None)
            raise OperatorUnavailable("The console input was closed: no operator")
        word, _, reason = answer.partition(" ")
        if word.lower() == "done":
            return answer
        if word.lower() == "fail":
            raise AssertionError(f"The operator reported a failure{': ' + reason.strip() if reason.strip() else ''} ({instruction})")
        stdout.write("Type 'done' or 'fail <reason>': ")
        stdout.flush()


# One reader thread per input stream: a prompt that timed out must not leave a reader that eats the next answer.
# The stream is kept with its queue so that its id cannot be reused by another stream.
_readers: dict[int, tuple[TextIO, queue.Queue[str | None]]] = {}
_readers_lock = threading.Lock()


def _answers(stdin: TextIO) -> queue.Queue[str | None]:
    with _readers_lock:
        entry = _readers.get(id(stdin))
        if entry is None:
            answers: queue.Queue[str | None] = queue.Queue()
            _readers[id(stdin)] = (stdin, answers)

            def read() -> None:
                while True:
                    try:
                        line = stdin.readline()
                    except (OSError, ValueError):
                        line = ""
                    if not line:
                        answers.put(None)
                        return
                    answers.put(line.strip())

            threading.Thread(target=read, name="operator-console", daemon=True).start()
            return answers
        return entry[1]
