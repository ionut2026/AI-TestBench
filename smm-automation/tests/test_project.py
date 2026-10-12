"""The project seam (grf.toml + plugin): the SMM project reads as before, and a project without SMM knowledge works with
the generic defaults."""

import pytest
from grf import project
from grf.capabilities import excluded_tags, tier_capabilities
from grf.doctor import Doctor, run_doctor
from grf.pipeline.drift import spec_ids
from grf.plugin import ProductPlugin

OTHER = """
[project]
name = "Widget"
product = "widgetd"
spec_prefix = "REQ"
env_prefix = "WID"

[paths]
suites = ["tests/robot"]

[tiers.sim]
capabilities = ["reset"]
evidence = false
parallel = true

[tiers.bench]
rig_control = true

[capabilities]
"needs:reset" = "reset"
"needs:power" = "power-cycle"
"needs:hands" = "operator"

[rig]
targets = { widgetd = "reset", psu = "power-cycle" }
log_capability = "widgetlog"
"""


@pytest.fixture
def other(tmp_path, monkeypatch):
    (tmp_path / "grf.toml").write_text(OTHER, encoding="utf-8")
    monkeypatch.setenv(project.PROJECT_ENV, str(tmp_path))
    project.use(None)
    return project.current()


def test_smm_project_reads_its_configuration(smm_project):
    p = smm_project
    assert (p.name, p.cli, p.product) == ("SMM", "smm-auto", "appSMM")
    assert list(p.tiers) == ["mock", "offline", "rig"]
    assert not p.is_evidence("mock") and p.is_evidence("offline") and p.is_evidence("rig")
    assert p.is_evidence("mock-parallel") is False and p.is_evidence("unknown") is True
    assert p.tier("rig").rig_control and p.tier("mock").parallel
    assert p.env("RIG_CONTROL") == "SMM_RIG_CONTROL" and p.mutation_faults_env == "SMM_MOCK_FAULTS"
    assert p.spec_tag(42) == "SDS-42" and p.rule(4) == "SMM04"
    assert p.rig_targets == {"appsmm": "restart", "broker": "broker-restart"} and p.rig_ready_ports == {"broker": 1883}
    assert type(p.plugin).__name__ == "SmmPlugin"
    assert [r.rule for r in p.lint_tag_rules] == ["SMM04", "SMM05", "SMM05", "SMM06", "SMM07", "SMM07"]


def test_unknown_tier_is_an_error(smm_project):
    with pytest.raises(ValueError, match="Unknown tier 'lab' \\(mock, offline, rig\\)"):
        smm_project.tier("lab")


def test_other_project_uses_generic_defaults(other):
    assert (other.name, other.cli, other.rule_prefix) == ("Widget", "grf", "GRF")
    assert type(other.plugin) is ProductPlugin
    assert other.plugin.known_messages() is None and other.plugin.brief_sections({"messages": []}) == []
    assert other.message_pattern is None and other.lint_tag_rules == ()
    assert other.mutation_faults_env == "WID_MOCK_FAULTS"
    assert spec_ids(["REQ-7", "SDS-8", "req-9"]) == [7, 9]


def test_other_project_capabilities(other):
    caps, notes = tier_capabilities("bench", env={})
    assert caps == set()
    assert notes[-1] == "no rig control (WID_RIG_CONTROL not set): no power-cycle, reset, widgetlog"
    assert excluded_tags({"reset"}) == ["needs:power", "needs:hands"]
    caps, _ = tier_capabilities("sim", operator="console")
    assert caps == {"reset", "operator"}


def test_generic_doctor_without_plugin_checks(other):
    checks = run_doctor("bench", env={}, deep=True)
    assert [(c.name, c.status) for c in checks] == [("rig control", "WARN"), ("operator", "WARN"), ("capabilities", "OK"),
                                                     ("deep check", "SKIP")]
    assert checks[0].detail == "WID_RIG_CONTROL is not set: reset, power-cycle and widgetlog tests will be excluded"
    assert checks[1].detail == "no operator (--operator console|dialog): operator tests will be excluded"
    assert [c.name for c in Doctor("sim", env={}).run()] == ["capabilities"]


def test_plugin_must_be_a_product_plugin(tmp_path):
    (tmp_path / "grf.toml").write_text('[project]\nplugin = "grf.project:CONFIG_FILE"\n[tiers.a]\n', encoding="utf-8")
    with pytest.raises(project.ProjectError, match="is not a ProductPlugin"):
        _ = project.Project.load(tmp_path).plugin
    (tmp_path / "grf.toml").write_text('[project]\nplugin = "no_such_module:X"\n', encoding="utf-8")
    with pytest.raises(project.ProjectError, match="cannot load the project plugin"):
        _ = project.Project.load(tmp_path).plugin


def test_missing_configuration(tmp_path):
    with pytest.raises(project.ProjectError, match="cannot read the project configuration"):
        project.Project.load(tmp_path)
    (tmp_path / "grf.toml").write_text("[project]\n", encoding="utf-8")
    with pytest.raises(project.ProjectError, match="no \\[tiers\\]"):
        _ = project.Project.load(tmp_path).tiers
    with pytest.raises(project.ProjectError, match="\\[paths\\] results is not set"):
        project.Project.load(tmp_path).path("results")
