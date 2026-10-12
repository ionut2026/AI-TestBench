import pytest

from grf import project


@pytest.fixture(autouse=True)
def isolated_project(monkeypatch):
    monkeypatch.delenv(project.PROJECT_ENV, raising=False)
    monkeypatch.setattr(project, "_current", None)
    monkeypatch.setattr(project, "_default_root", None)


@pytest.fixture
def widget(tmp_path, monkeypatch):
    (tmp_path / "grf.toml").write_text("""
[project]
name = "Widget"
spec_prefix = "WID"
env_prefix = "WIDGET"

[paths]
scope = "catalog/demo.scope.toml"
catalog_dir = "catalog"
reviews = "catalog/reviews.toml"
suites = ["robot/suites"]
environments = "robot/environments"
results = "results"
briefs = "generated/briefs"
mutants = "catalog/mutants.toml"

[tiers.sim]
capabilities = ["reset"]
evidence = false
parallel = true

[tiers.bench]
rig_control = true

[capabilities]
"needs:reset" = "reset"
"needs:operator" = "operator"

[rig]
targets = { widgetd = "reset" }

[mutation]
fault_actions = ["drop", "delay"]
""", encoding="utf-8")
    monkeypatch.setenv(project.PROJECT_ENV, str(tmp_path))
    return project.current()
