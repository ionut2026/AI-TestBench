import pytest
from deploy_fakes import component

from grf.deploy.branches import resolve_branches
from grf.deploy.model import DeployError
from grf.project import ProjectError


def test_branch_precedence_and_legacy_override():
    components = [component(), component(name="other")]
    assert resolve_branches(components) == {"app": None, "other": None}
    assert resolve_branches(components, default_branch="main", configured={"app": "release/app"}) == {
        "app": "release/app", "other": "main",
    }
    assert resolve_branches(components, default_branch="main", configured={"app": "release/app"},
                            overrides=["app=feature/app", "other=release/other"]) == {
        "app": "feature/app", "other": "release/other",
    }
    assert resolve_branches(components, configured={"app": "release/app"}, overrides=["legacy"]) == {
        "app": "legacy", "other": "legacy",
    }
    assert resolve_branches(components, overrides="legacy") == {"app": "legacy", "other": "legacy"}


@pytest.mark.parametrize("overrides", [
    ["unknown=main"], ["app=main", "app=other"], ["app="], ["=main"], [""],
    ["app=   "], ["legacy", "app=main"], ["legacy", "other"], ["app=main", "unknown=other"],
])
def test_invalid_branch_overrides_fail_closed(overrides):
    with pytest.raises(DeployError):
        resolve_branches([component()], overrides=overrides)


def test_unknown_configured_component_is_rejected():
    with pytest.raises(DeployError, match="unknown component"):
        resolve_branches([component()], configured={"typo": "main"})


def test_project_exposes_validated_branch_map(widget):
    widget.data["deploy"] = {"default_branch": "main", "branches": {"app": "release/app"}}
    widget.validate()
    assert widget.deploy_branches == {"app": "release/app"}
    assert widget.deploy_branch == "main"


@pytest.mark.parametrize("branches", [False, [], {"app": ""}, {"app": 7}, {"": "main"}])
def test_project_rejects_malformed_branch_map(widget, branches):
    widget.data["deploy"] = {"branches": branches}
    with pytest.raises(ProjectError):
        widget.validate()
