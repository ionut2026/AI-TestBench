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


@pytest.fixture
def synthetic_evidence(widget, monkeypatch):
    """Explicit fake evidence for isolated contract tests; never an instrument/product result."""
    from grf.evidence import PREFLIGHT, RESULT, AssertionCoverage, EvidenceCheck, EvidenceChecks

    def checks(names):
        return EvidenceChecks(tuple(EvidenceCheck(name, True, "synthetic contract fixture") for name in names),
                              {"engine": "synthetic-fixture"})

    monkeypatch.setattr(widget.plugin, "evidence_preflight", lambda tier: checks(PREFLIGHT))
    monkeypatch.setattr(widget.plugin, "evidence_checks", lambda tier, tests, meta: checks(RESULT))
    monkeypatch.setattr(widget.plugin, "assertion_coverage", lambda tests, meta: {
        1: AssertionCoverage(("increment",), ("increment",) if any(1 in test["specs"] for test in tests) else ()),
    })
    return checks(PREFLIGHT)
