from importlib.metadata import PackageNotFoundError

import pytest

from grf import doctor


@pytest.mark.parametrize("installed,status", [("0.1.0", "OK"), ("0.2.0", "FAIL"), ("0.1.0.dev1", "FAIL")])
def test_exact_approved_version(widget, monkeypatch, installed, status):
    widget.data["framework"] = {"version": "0.1.0"}
    monkeypatch.setattr(doctor, "version", lambda name: installed)
    checks = doctor.run_doctor("sim", env={})
    assert checks[0].name == "GRF version" and checks[0].status == status
    assert installed in checks[0].detail


def test_missing_install_is_a_failure(widget, monkeypatch):
    widget.data["framework"] = {"version": "0.1.0"}

    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(doctor, "version", missing)
    assert doctor.run_doctor("sim", env={})[0].status == "FAIL"


def test_no_pin_does_not_invent_one(widget):
    assert all(c.name != "GRF version" for c in doctor.run_doctor("sim", env={}))


@pytest.mark.parametrize("expected", ["", 1, ["0.1.0"]])
def test_invalid_pin_is_explicit(widget, expected):
    widget.data["framework"] = {"version": expected}
    check = doctor.run_doctor("sim", env={})[0]
    assert check.status == "FAIL" and "nonempty exact" in check.detail


def test_invalid_framework_section(widget):
    widget.data["framework"] = "0.1.0"
    check = doctor.run_doctor("sim", env={})[0]
    assert check.status == "FAIL" and "must be a table" in check.detail
