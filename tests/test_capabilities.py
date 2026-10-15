import pytest

from grf.capabilities import excluded_tags, tier_capabilities
from grf.doctor import Doctor, format_checks, run_doctor
from grf.rigcontrol import RigControl, RigControlError


class FakeControl(RigControl):
    def __init__(self, commands=(), error=None):
        super().__init__(["synthetic-rig"])
        self.commands, self.error, self.calls = commands, error, 0

    def capabilities(self):
        self.calls += 1
        if self.error:
            raise RigControlError(self.error)
        return frozenset(self.commands)

    def log_clock_offset(self):
        return 830250, 300


def test_only_declared_rig_and_operator_capabilities_are_added(widget):
    control = FakeControl(["restart-widgetd", "fetch-log", "unconfigured-command"])
    caps, notes = tier_capabilities("bench", "console", {}, control)
    assert caps == {"reset", "applog", "operator"}
    assert excluded_tags(caps) == [] and any("rig control" in note for note in notes)
    caps, _ = tier_capabilities("bench", "none", {})
    assert caps == set() and excluded_tags(caps) == ["needs:reset", "needs:operator"]
    assert tier_capabilities("sim", "none", {}, FakeControl(error="must not query static tier"))[0] == {"reset"}


def test_capability_errors_do_not_grant_tags(widget):
    with pytest.raises(RigControlError, match="unreachable"):
        tier_capabilities("bench", "none", {}, FakeControl(error="unreachable"))
    with pytest.raises(ValueError, match="Unknown tier"):
        tier_capabilities("unknown", env={})


def test_doctor_reports_rig_clock_and_operator_without_product_operations(widget, monkeypatch):
    control = FakeControl(["restart-widgetd", "fetch-log", "clock"])
    monkeypatch.setattr(RigControl, "from_env", lambda env: control)
    checks = {c.name: c for c in run_doctor("bench", "console", env={}, is_tty=lambda: False)}
    assert checks["rig control"].status == "OK"
    assert checks["rig clock"].status == "OK" and "+830.2 s (+/- 0.3 s)" in checks["rig clock"].detail
    assert checks["operator"].status == "WARN"
    assert checks["capabilities"].detail.endswith("excluded tags: none")
    assert "rig clock" in format_checks(list(checks.values()))


def test_doctor_failure_does_not_retry_broken_rig(widget, monkeypatch):
    control = FakeControl(error="unreachable")
    monkeypatch.setattr(RigControl, "from_env", lambda env: control if "WIDGET_RIG_CONTROL" in env else None)
    checks = {c.name: c for c in run_doctor("bench", env={"WIDGET_RIG_CONTROL": "synthetic"})}
    assert checks["rig control"].status == "FAIL"
    assert checks["capabilities"].status == "OK" and "needs:reset" in checks["capabilities"].detail
    assert control.calls == 1


def test_doctor_extension_hooks_are_explicit_and_ordered(widget, monkeypatch):
    class NeutralDoctor(Doctor):
        def before(self):
            self.add("adapter preflight", "OK", "synthetic only")

        def deep_check(self):
            self.add("adapter deep check", "SKIP", "owner authorization not provided")

    monkeypatch.setattr(widget.plugin, "doctor_class", lambda: NeutralDoctor)
    checks = run_doctor("sim", deep=True, env={})
    assert checks[0].name == "adapter preflight" and checks[-1].name == "adapter deep check"
    assert checks[-1].status == "SKIP"
