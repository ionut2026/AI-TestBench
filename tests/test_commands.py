import argparse

from grf import cli, project
from grf.commands import CommandContext


def test_context_resolves_only_requested_features(widget, tmp_path):
    args = argparse.Namespace(catalog=str(tmp_path / "external.json"))
    context = CommandContext(widget)
    assert context.catalog_path(args) == tmp_path / "external.json"
    assert context.reviews_path(args) == widget.path("reviews")
    assert context.suites(args) == widget.paths("suites")
    assert widget.plugin.context.project is widget


def test_minimal_deployment_project_needs_no_unrelated_paths(tmp_path, monkeypatch, capsys):
    (tmp_path / "grf.toml").write_text('[project]\nname="Minimal"\n', encoding="utf-8")
    monkeypatch.delenv(project.PROJECT_ENV, raising=False)
    assert cli.main(["deploy", "components"], project_root=tmp_path) == 0
    assert "Minimal defines no deployable components" in capsys.readouterr().out


def test_core_command_does_not_register_unrelated_plugin_commands(widget, monkeypatch):
    def broken(sub):
        raise RuntimeError("optional product command unavailable")

    monkeypatch.setattr(widget.plugin, "add_commands", broken)
    assert cli.main(["deploy", "components"]) == 0


def test_discovery_and_scoped_selection_restore_previous_project(widget, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    (other / "grf.toml").write_text('[project]\nname="Other"\n', encoding="utf-8")
    monkeypatch.delenv(project.PROJECT_ENV, raising=False)
    selected = project.discover(other)
    assert project.current() is widget
    with project.using(selected):
        assert project.current().name == "Other"
    assert project.current() is widget
    assert cli.main(["deploy", "components"], project_root=other) == 0
    assert project.current() is widget
