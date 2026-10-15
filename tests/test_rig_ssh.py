"""SSH rig behavior tested without external programs or instrument access."""


import pytest

from grf.rig_ssh import RigSsh, RigSshError


@pytest.mark.parametrize("change", [
    {"connect_timeout_s": float("nan")}, {"ready_timeout_s": float("inf")},
    {"port": -1}, {"port": True}, {"ready_timeout_s": 0}, {"widgetd": {"port": 65536}},
    {"widgetd": []}, {"log": {"typo": "x"}}, {"typo": 3},
])
def test_rejects_invalid_configuration(widget, change):
    with pytest.raises(RigSshError):
        RigSsh({"host": "board", **change}, run=lambda *args: pytest.fail("no commands"))


@pytest.mark.parametrize("value", [-1, True, 1.5])
def test_invalid_downtime_never_stops(widget, value):
    rig = RigSsh({"host": "board", "widgetd": {"stop": "stop", "start": "start"}},
                 run=lambda *args: pytest.fail("no commands"))
    with pytest.raises(RigSshError, match="down_ms"):
        rig.restart("widgetd", value)
