import pytest

from grf import library
from grf.library import GrfLibrary, epoch_ms, match_from, timeline_html
from grf.operator_prompt import OperatorUnavailable


@pytest.mark.parametrize("later,passes", [(1999, True), (2000, False), (2001, False), (999, False)])
def test_strict_upper_bound(later, passes):
    lib = GrfLibrary()
    if passes:
        assert lib.time_between_should_be_less_than(1000, later, "1s") == 0.999
    else:
        with pytest.raises(AssertionError):
            lib.time_between_should_be_less_than(1000, later, "1s")


@pytest.mark.parametrize("later,passes", [(1999, False), (2000, True), (2001, True)])
def test_inclusive_lower_bound(later, passes):
    lib = GrfLibrary()
    if passes:
        assert lib.time_between_should_be_at_least(1000, later, "1s") == (later - 1000) / 1000
    else:
        with pytest.raises(AssertionError):
            lib.time_between_should_be_at_least(1000, later, "1s")


def test_time_and_nested_fields():
    assert epoch_ms({"time": "1970-01-01T00:00:01Z"}) == 1000
    assert epoch_ms("1000") == 1000
    with pytest.raises(ValueError, match="Entry has no time"):
        epoch_ms({"id": 1})
    assert match_from({"nested.value": "42", "flag": "false", "name": "Widget"}) == {
        "nested": {"value": 42}, "flag": False, "name": "Widget",
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "nan", "inf", True, False])
def test_nonfinite_times_cannot_pass_timing_assertions(value):
    with pytest.raises(ValueError, match="finite"):
        GrfLibrary().time_between_should_be_less_than(0, value, "1s")
    with pytest.raises(ValueError, match="finite"):
        GrfLibrary().time_between_should_be_at_least(0, value, "1s")


def test_robot_helpers_only_suppress_missing_execution_context(monkeypatch):
    def broken():
        raise RuntimeError("unexpected keyword failure")

    monkeypatch.setattr(library, "BuiltIn", broken)
    with pytest.raises(RuntimeError, match="unexpected"):
        library.robot_var("${VALUE}", "default")
    with pytest.raises(RuntimeError, match="unexpected"):
        library.metadata("Tier", "sim")


def test_lifecycle_and_operator_marks(widget, monkeypatch):
    lib = GrfLibrary()
    lib._begin_test(1000)
    lib._mark = 7
    monkeypatch.setattr(library, "robot_var", lambda name, default: default)
    calls = []
    monkeypatch.setattr(library.operator_prompt, "ask_operator",
                        lambda instruction, mode, seconds: calls.append((instruction, mode, seconds)) or "done")
    assert lib.operator_action("Load widget", "2s") == "done"
    assert calls == [("Load widget", "none", 2)]
    assert lib._segments == [(7, "Operator: Load widget")]
    lib._end_test()
    lib._segment("outside test")
    assert len(lib._segments) == 1
    lib._begin_test(2000)
    assert lib._segments == [] and lib._test_started_ms == 2000


def test_operator_failure_is_explicit(widget, monkeypatch):
    monkeypatch.setattr(library, "robot_var", lambda name, default: default)

    def unavailable(*args):
        raise OperatorUnavailable("no operator")

    monkeypatch.setattr(library.operator_prompt, "ask_operator", unavailable)
    with pytest.raises(AssertionError, match="no operator"):
        GrfLibrary().operator_action("Load widget")


def test_missing_rig_control_names_project(widget, monkeypatch):
    lib = GrfLibrary()
    monkeypatch.setattr(lib, "_control", lambda: None)
    with pytest.raises(AssertionError, match="WIDGET_RIG_CONTROL"):
        lib._require_control("restart-widget", "restart widget")


def test_timeline_escapes_product_data():
    text = timeline_html([{"id": 2, "time": "", "way": "tx", "name": "<msg>", "topic": "<topic>",
                           "body": "<body>", "valid": False, "errors": ["<error>"]}],
                         "<title>", [(1, "<segment>")], peer="<widget>")
    for value in ("msg", "topic", "body", "error", "title", "segment", "widget"):
        assert f"<{value}>" not in text
        assert f"&lt;{value}&gt;" in text
    assert text.index("&lt;segment&gt;") < text.index("&lt;msg&gt;")
