import argparse
import datetime as dt
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from deploy_fakes import component, target

from grf import cli, workflow
from grf.deploy.config import JenkinsSettings
from grf.deploy.jenkins import job_url
from grf.deploy.model import ArtifactRef, DeployError, Installed, Outcome
from grf.deploy.record import DeployRecord

SHA = "a" * 64
REVISION = "b" * 40


@pytest.fixture
def flow(widget, tmp_path, monkeypatch, synthetic_evidence):
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    suite = tmp_path / "flow.robot"
    suite.write_text("*** Test Cases ***\nIncrement\n    [Tags]    WID-1\n    No Operation\n", encoding="utf-8")
    request = tmp_path / "approved.toml"
    request.write_text(f"""
schema = 1
id = "workflow-test"
action = "deploy-and-test"
tier = "bench"
source_revision = "{REVISION}"
catalog_sha256 = "{hashlib.sha256(catalog.read_bytes()).hexdigest()}"
[selection]
specs = [1]
[[component]]
name = "app"
build = 7
sha256 = "{SHA}"
expected_version = "1.2.3"
[authorization]
owner = "Instrument Owner"
reference = "UNIT-TEST-NOT-AUTHORIZATION"
valid_until = {dt.date.today() + dt.timedelta(days=1)}
""", encoding="utf-8")
    monkeypatch.setattr(widget.plugin, "deploy_components", lambda: [component()])
    monkeypatch.setattr(workflow, "git_state", lambda root: (REVISION, False))
    monkeypatch.setattr(workflow, "load_site", lambda *args: SimpleNamespace(
        target_for=lambda name: target(), audit_log=None, jenkins=JenkinsSettings("https://jenkins.example.test")))
    args = argparse.Namespace(request=str(request), request_sha256=hashlib.sha256(request.read_bytes()).hexdigest(),
                              outdir=tmp_path / "results" / "workflow", config=None, operator="none", yes=True,
                              catalog=str(catalog), suites=[str(suite)], reviews=None, scope=None)
    calls = []

    class Deployment:
        change_plan = False
        fail_deploy = False
        order = []
        options = []
        wrong_branch = False
        wrong_job = False

        def __init__(self, project, component, site, target, **kwargs):
            self.name, self.target, self.component, self.site = component.name, target, component, site

        def deploy(self, options):
            calls.append(("plan" if options.dry_run else "deploy", options.build, options.sha256))
            self.order.append(("plan" if options.dry_run else "deploy", self.name))
            self.options.append((self.name, options))
            return DeployRecord("Widget", self.name, self.target.name, self.target.host, "manual", dry_run=options.dry_run,
                                outcome=Outcome.DRY_RUN if options.dry_run else
                                    Outcome.PREFLIGHT_FAILED if self.fail_deploy else Outcome.DEPLOYED,
                                artifact=ArtifactRef(self.name, "jenkins", "app_1.2.3.tar.gz", version="1.2.3",
                                                     build_number=8 if self.change_plan else 7, sha256=SHA,
                                                     branch="wrong" if self.wrong_branch else options.branch,
                                                     job_url="https://wrong.test" if self.wrong_job else
                                                         job_url(self.site.jenkins, self.component, options.branch)),
                                installed=Installed("app", "running", version="1.2.3"))

    def invoke(argv):
        stage = "gate" if "gate" in argv else "run"
        calls.append((stage, argv))
        return 0

    return widget, args, Deployment, invoke, calls


def execute(flow, invoke=None):
    project, args, deployment, default_invoke, _ = flow
    return workflow.deploy_and_test(args, project, invoke or default_invoke, deployment_factory=deployment)


def test_bounded_workflow_pins_every_stage_and_keeps_exact_snapshot(flow):
    project, args, _, _, calls = flow
    assert execute(flow) == 0
    assert [call[0] for call in calls] == ["plan", "deploy", "run", "gate"]
    assert calls[0][1:] == ("7", SHA) and calls[1][1:] == ("7", SHA)
    assert (args.outdir / "request.toml").read_bytes() == Path(args.request).read_bytes()
    for _, argv in calls[2:]:
        assert argv[argv.index("--request-sha256") + 1] == args.request_sha256
        assert argv[argv.index("--deployment") + 1].endswith("deployment.json")
    journal = [json.loads(line) for line in (args.outdir / "workflow.jsonl").read_text(encoding="utf-8").splitlines()]
    assert journal[-1]["stage"] == "evaluated" and journal[-1]["gateCode"] == 0
    assert next(item for item in journal if item["stage"] == "promoted")["artifacts"][0]["build"] == 7


@pytest.mark.parametrize("run_code", [1, 2, 252])
def test_failed_run_is_still_gated_and_not_masked(flow, run_code):
    called = []
    def invoke(argv):
        stage = "gate" if "gate" in argv else "run"
        called.append(stage)
        return 0 if stage == "gate" else run_code
    assert execute(flow, invoke) == run_code
    assert called == ["run", "gate"]


def test_cancelled_run_attempts_gate_then_preserves_cancellation(flow):
    called = []
    def invoke(argv):
        if "run" in argv:
            raise KeyboardInterrupt("cancelled")
        called.append("gate")
        return 2
    with pytest.raises(KeyboardInterrupt):
        execute(flow, invoke)
    assert called == ["gate"]


def test_changed_plan_cannot_be_promoted(flow):
    flow[2].change_plan = True
    with pytest.raises(ValueError, match="numeric build"):
        execute(flow)
    assert [call[0] for call in flow[4]] == ["plan"]


def test_all_components_are_planned_before_deployment_in_product_order(flow, monkeypatch):
    project, args, deployment, _, _ = flow
    monkeypatch.setattr(project.plugin, "deploy_components", lambda: [component(), replace(component(), name="app2")])
    project.data["deploy"] = {"order": ["app2", "app"], "default_branch": "main", "branches": {"app": "release/app"}}
    args.branch = ["app2=feature/other"]
    request = Path(args.request)
    request.write_text(request.read_text(encoding="utf-8") + f"""
[[component]]
name = "app2"
build = 7
sha256 = "{SHA}"
expected_version = "1.2.3"
""", encoding="utf-8")
    args.request_sha256 = hashlib.sha256(request.read_bytes()).hexdigest()
    assert execute(flow) == 0
    assert deployment.order == [("plan", "app2"), ("plan", "app"), ("deploy", "app2"), ("deploy", "app")]
    assert [(name, options.branch) for name, options in deployment.options] == [
        ("app2", "feature/other"), ("app", "release/app"), ("app2", "feature/other"), ("app", "release/app"),
    ]
    for name, branch in (("app", "release/app"), ("app2", "feature/other")):
        for stage in ("plan", "deploy"):
            record = json.loads((args.outdir / stage / name / "deployment.json").read_text(encoding="utf-8"))
            assert record["artifact"]["branch"] == branch
            assert record["artifact"]["job_url"].endswith("/job/" + branch.replace("/", "%2F"))


@pytest.mark.parametrize("attribute", ["wrong_branch", "wrong_job"])
@pytest.mark.parametrize("stage", ["plan", "deploy"])
def test_workflow_rejects_wrong_jenkins_identity_before_tests(flow, attribute, stage):
    deployment = flow[2]
    original = deployment.deploy

    def deploy(self, options):
        setattr(self, attribute, options.dry_run == (stage == "plan"))
        return original(self, options)

    deployment.deploy = deploy
    with pytest.raises(ValueError, match="Jenkins job/branch differs"):
        execute(flow)
    assert all(call[0] not in ("run", "gate") for call in flow[4])


@pytest.mark.parametrize("branches", [["unknown=main"], ["app=one", "app=two"], ["app="]])
def test_workflow_invalid_branch_fails_before_side_effects(flow, branches):
    flow[1].branch = branches
    with pytest.raises(DeployError):
        execute(flow)
    assert not flow[4] and not flow[1].outdir.exists()


def test_workflow_parser_accepts_repeatable_branches():
    parser = argparse.ArgumentParser()
    workflow.add_parser(parser.add_subparsers())
    args = parser.parse_args(["workflow", "deploy-and-test", "--request", "request.toml",
                              "--request-sha256", SHA, "--outdir", "new",
                              "--branch", "app=release/app", "--branch", "other=main"])
    assert args.branch == ["app=release/app", "other=main"]


def test_source_change_after_planning_stops_before_real_deployment(flow, monkeypatch):
    states = iter([(REVISION, False), (REVISION, False), (REVISION, True)])
    monkeypatch.setattr(workflow, "git_state", lambda root: next(states))
    with pytest.raises(ValueError, match="changed before"):
        execute(flow)
    assert [call[0] for call in flow[4]] == ["plan"]


def test_failed_deployment_does_not_start_tests(flow):
    flow[2].fail_deploy = True
    assert execute(flow) == 3
    assert [call[0] for call in flow[4]] == ["plan", "deploy"]


@pytest.mark.parametrize("change", [{"yes": False}, {"request_sha256": "0" * 64}])
def test_authorization_and_digest_fail_before_any_deployment(flow, change):
    for key, value in change.items():
        setattr(flow[1], key, value)
    with pytest.raises(ValueError):
        execute(flow)
    assert not flow[4] and not flow[1].outdir.exists()


def test_existing_evidence_directory_is_never_cleaned_or_reused(flow):
    flow[1].outdir.mkdir(parents=True)
    marker = flow[1].outdir / "keep.txt"
    marker.write_text("prior evidence", encoding="utf-8")
    with pytest.raises(FileExistsError):
        execute(flow)
    assert marker.read_text(encoding="utf-8") == "prior evidence" and not flow[4]


def test_request_digest_option_is_checked_before_capability_discovery(widget, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "tier_capabilities", lambda *args: pytest.fail("no contact"))
    assert cli.main(["run", "--tier", "sim", "--request-sha256", SHA]) == 2


@pytest.mark.parametrize("command", ["run", "gate"])
def test_expected_digest_compares_the_actual_parsed_request_bytes(flow, monkeypatch, capsys, command):
    monkeypatch.setattr(cli, "tier_capabilities", lambda *args: pytest.fail("no product contact"))
    args = flow[1]
    extra = ["--output", str(args.outdir / "missing.xml")] if command == "gate" else []
    assert cli.main([command, "--request", args.request, "--request-sha256", "0" * 64, *extra]) == 2
    assert "protected request digest does not match" in capsys.readouterr().err
