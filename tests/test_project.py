import pytest

from grf import project
from grf.capabilities import excluded_tags, tier_capabilities
from grf.cli import main
from grf.doctor import run_doctor
from grf.pipeline.drift import spec_ids
from grf.plugin import ProductPlugin


def test_neutral_configuration(widget):
    assert (widget.name, widget.cli, widget.rule_prefix) == ("Widget", "grf", "GRF")
    assert type(widget.plugin) is ProductPlugin
    assert widget.plugin.known_messages() is None
    assert widget.env("RIG_CONTROL") == "WIDGET_RIG_CONTROL"
    assert widget.spec_tag(9) == "WID-9"
    assert spec_ids(["WID-9", "wid-10", "SDS-11"]) == [9, 10]
    assert widget.mutation_faults_env == "WIDGET_MOCK_FAULTS"
    assert widget.mutation_fault_actions == {"drop", "delay"}
    assert widget.paths("suites") == [widget.root / "robot/suites"]
    assert not widget.is_evidence("sim-parallel")
    assert widget.is_evidence("bench") and not widget.is_evidence("unknown")
    assert not widget.is_evidence("?") and not widget.is_evidence("benchwork")
    with pytest.raises(ValueError, match="Unknown tier"):
        widget.tier("unknown")


def test_discovery_from_parent(tmp_path, monkeypatch):
    (tmp_path / "grf.toml").write_text("[project]\nname='Parent'\n", encoding="utf-8")
    nested = tmp_path / "child"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert project.current().root == tmp_path
    assert project.current().name == "Parent"


def test_resolution_priority_and_cache(widget, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    config = other / "grf.toml"
    config.write_text("[project]\nname='Registered'\n", encoding="utf-8")
    project.set_default_root(other)
    project.use(None)
    assert project.current().name == "Widget"  # environment wins over registered root
    monkeypatch.delenv(project.PROJECT_ENV)
    assert project.current().name == "Widget"  # one project per process until reset
    project.use(None)
    assert project.current().name == "Registered"
    monkeypatch.setenv(project.PROJECT_ENV, str(config))
    project.use(None)
    assert project.current().root == other  # file and directory overrides both work


def test_capabilities_and_doctor(widget):
    caps, _ = tier_capabilities("sim", operator="console", env={})
    assert caps == {"reset", "operator"}
    assert excluded_tags({"reset"}) == ["needs:operator"]
    checks = run_doctor("bench", env={}, deep=True)
    assert [(c.name, c.status) for c in checks] == [
        ("rig control", "WARN"), ("operator", "WARN"), ("capabilities", "OK"), ("deep check", "SKIP"),
    ]
    assert "WIDGET_RIG_CONTROL" in checks[0].detail


def test_cli_help_without_product_commands(widget, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    usage = help_text.split("\n\n", 1)[0]
    assert "doctor" in usage and "matrix" not in usage and "smm-auto" not in usage
    assert main(["doctor", "--tier", "sim"]) == 0


def test_cli_outside_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["doctor"]) == 2
    assert "no grf.toml found" in capsys.readouterr().err


def test_invalid_configuration(tmp_path):
    config = tmp_path / "grf.toml"
    config.write_text("[project", encoding="utf-8")
    with pytest.raises(project.ProjectError, match="cannot read"):
        project.Project.load(config)
    config.write_text("[project]\nplugin='grf.project:CONFIG_FILE'\n", encoding="utf-8")
    p = project.Project.load(config)
    with pytest.raises(project.ProjectError, match="not a ProductPlugin"):
        _ = p.plugin
    with pytest.raises(project.ProjectError, match="no \\[tiers\\]"):
        _ = p.tiers
    with pytest.raises(project.ProjectError, match="results is not set"):
        p.path("results")


@pytest.mark.parametrize("text,diagnostic", [
    ("project = false", r"\[project\].*table"),
    ("paths = []", r"\[paths\].*table"),
    ("[project]\nname = 42", "name.*nonempty string"),
    ("[project]\nspec_prefix = '../BAD'", "spec_prefix"),
    ("[paths]\nsuites = [1]", "suites.*nonempty strings"),
    ("[paths]\nscope = ''", "scope.*nonempty strings"),
    ("[tiers]\nsim = []", r"\[tiers.sim\].*table"),
    ("[tiers.sim]\nevidence = 'false'", "evidence.*boolean"),
    ("[tiers.sim]\nparallel = 1", "parallel.*boolean"),
    ("[tiers.sim]\nrig_control = 'no'", "rig_control.*boolean"),
    ("[tiers.sim]\ncapabilities = 'reset'", "capabilities.*nonempty strings"),
    ("[capabilities]\n'needs:reset' = false", "capability names"),
    ("[operator]\ncapabilities = 'operator'", "capabilities.*nonempty strings"),
    ("[rig]\ntargets = []", "targets.*table"),
    ("[rig.targets]\nwidgetd = false", "widgetd.*nonempty"),
    ("[rig.ready_ports]\nwidgetd = true", "widgetd.*integer"),
    ("[rig.ready_ports]\nwidgetd = 65536", "widgetd.*integer"),
    ("[rig]\nlog_files = 1", "log_files.*nonempty"),
    ("[[lint.tag_rules]]\ntag = 'needs:reset'", "rule.*integer"),
    ("[[lint.tag_rules]]\nrule = true\ntag = 'x'", "rule.*integer"),
    ("[[lint.tag_rules]]\nrule = 1", "tag.*nonempty"),
    ("[[lint.tag_rules]]\nrule=1\ntag='x'\nkeywords=2", "keywords.*nonempty strings"),
    ("[lint]\ntag_rules = false", "tag_rules.*list"),
    ("[catalog]\nmessage_pattern = '['", "message_pattern.*invalid"),
    ("[mutation]\nfault_actions = 'drop'", "fault_actions.*nonempty strings"),
    ("[tiers.bench]\nevidnce=false", "unknown.*evidnce"),
    ("[texts]\nnon_evidence = []", "values.*strings"),
    ("[framework]\nversion = 42", "version.*nonempty"),
])
def test_malformed_values_rejected_at_load(tmp_path, text, diagnostic):
    config = tmp_path / "grf.toml"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(project.ProjectError, match=diagnostic):
        project.Project.load(config)


def test_registered_relative_root_survives_directory_change(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(tmp_path)
    project.set_default_root("child")
    monkeypatch.chdir(child)
    assert project.default_root() == child


def test_longest_evidence_tier_match(widget):
    widget.tiers["sim-real"] = project.Tier("sim-real", frozenset(), evidence=True)
    assert widget.is_evidence("sim-real-run")
    assert widget.is_evidence("bench legacy")
    assert not widget.is_evidence("simulator")
