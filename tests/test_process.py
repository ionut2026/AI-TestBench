"""Real local synthetic descendants; no external service or instrument commands."""

import subprocess
import sys
import time

import pytest

from grf.process import run


def test_command_result_and_output_are_preserved():
    result = run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], 5)
    assert result.returncode == 3 and result.stdout.strip() == "out" and result.stderr.strip() == "err"


def test_timeout_kills_descendants_including_inherited_pipes(tmp_path):
    marker = tmp_path / "survived"
    started = tmp_path / "started"
    child = f"import time,pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('orphan')"
    parent = (
        f"import subprocess,sys,time,pathlib; subprocess.Popen([sys.executable, '-c', {child!r}]); "
        f"pathlib.Path({str(started)!r}).write_text('started'); time.sleep(10)"
    )
    before = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable, "-c", parent], 1.5)
    assert time.monotonic() - before < 2.5
    assert started.exists(), "probe must have spawned a descendant before timeout"
    time.sleep(2)
    assert not marker.exists(), "a killed command's descendant must not execute later"
