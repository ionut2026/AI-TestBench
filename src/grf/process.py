"""Bounded local commands: timeout/cancellation owns the entire descendant process tree."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO


def run(argv: Sequence[str], timeout_s: float, *, env: Mapping[str, str] | None = None,
        stdout: int | TextIO = subprocess.PIPE, stderr: int | TextIO = subprocess.PIPE,
        cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    from grf.validation import number

    number(timeout_s, "command timeout", positive=True)
    if os.name == "nt":
        # The helper joins a kill-on-close job before it can spawn the command. Killing the helper
        # closes that job even when the command already exited but its descendants inherited pipes.
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), *argv], timeout=timeout_s,
                              stdout=stdout, stderr=stderr, env=env, cwd=cwd, text=True, check=False)
    with subprocess.Popen(argv, stdout=stdout, stderr=stderr, env=env, cwd=cwd, text=True,
                          start_new_session=True) as process:
        try:
            output, errors = process.communicate(timeout=timeout_s)
        except BaseException as err:
            _kill_group(process.pid)
            output, errors = process.communicate()
            if isinstance(err, subprocess.TimeoutExpired):
                raise subprocess.TimeoutExpired(argv, timeout_s, output=output, stderr=errors) from None
            raise
        else:
            _kill_group(process.pid)
        return subprocess.CompletedProcess(argv, process.returncode, output, errors)


def _kill_group(pid: int) -> None:
    if sys.platform != "win32":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # no process in the operation's session remains


def _windows_job() -> None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64), ("Flags", wintypes.DWORD),
                ("MinimumWorkingSet", ctypes.c_size_t), ("MaximumWorkingSet", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("ReadOperations", "WriteOperations", "OtherOperations", "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("Basic", BasicLimits), ("Io", IoCounters), ("ProcessMemory", ctypes.c_size_t),
                ("JobMemory", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t),
                ("PeakJobMemory", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway permission
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
        # Keep the non-inheritable handle open until this helper exits, including abnormal termination.


if __name__ == "__main__":
    try:
        _windows_job()
        result = subprocess.run(sys.argv[1:], check=False)
    except OSError as error:
        print(f"GRF process ownership/launch failed: {error}", file=sys.stderr, flush=True)
        os._exit(127)
    os._exit(result.returncode)
