"""grf.deploy parts: site config, Jenkins client (a local HTTP server), ssh transports, docker helpers, CLI, doctor
checks, report section and starter. Nothing here reaches a real Jenkins or board."""

import argparse
import hashlib
import http.server
import json
import re
import subprocess
import threading
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from deploy_fakes import component, inspect_of, target

from grf.deploy import cli as deploy_cli
from grf.deploy import docker
from grf.deploy.checks import deploy_checks
from grf.deploy.config import JenkinsSettings, load, parse
from grf.deploy.jenkins import Jenkins, local_artifact
from grf.deploy.model import ArtifactRef, ContainerRecipe, DeployError, Mount, Outcome
from grf.deploy.record import DeployRecord, load_records
from grf.deploy.transport import CommandResult, OpenSshTransport, ParamikoTransport, Redactor
from grf.pipeline.report import _deployments_html
from grf.scaffold import init_project

# ------------------------------------------------------------------ site config


def site_data(tmp_path, **target):
    t = {"host": "10.0.0.1", "user": "root", "known_hosts": str(tmp_path / "ssh" / "known_hosts"),
         "password_env": "BOARD_PW"}
    t.update(target)
    return {"schema": 1, "jenkins": {"base_url": "https://jenkins.example/jenkins/"},
            "targets": {"board": t}, "components": {"app": "board"}, "limits": {"retention": 3}}


def test_config_parses_a_complete_site_file(tmp_path):
    config = parse(site_data(tmp_path), tmp_path / "site.toml")
    assert config.jenkins.base_url == "https://jenkins.example/jenkins" and not config.jenkins.insecure_tls
    assert config.retention == 3 and config.min_free_mb == 1500 and config.max_load == 5.0
    assert config.target_for("app").host == "10.0.0.1"
    assert config.targets["board"].password({"BOARD_PW": "pw"}) == "pw"
    with pytest.raises(DeployError, match="BOARD_PW"):
        config.targets["board"].password({})
    with pytest.raises(DeployError, match="no target"):
        config.target_for("other")


@pytest.mark.parametrize("change,message", [
    (lambda d: d["targets"]["board"].update(password="pw"), "secrets do not belong"),
    (lambda d: d["jenkins"].update(token="t"), "secrets do not belong"),
    (lambda d: d["jenkins"].update(base_url="http://jenkins.example"), "https"),
    (lambda d: d["targets"]["board"].update(host="<board-ip>"), "placeholder"),
    (lambda d: d["targets"]["board"].update(client="telnet"), "client"),
    (lambda d: d["targets"]["board"].update(host_key_policy="ignore"), "host_key_policy"),
    (lambda d: d["targets"]["board"].update(password_file="x"), "only one"),
    (lambda d: d["targets"]["board"].update(client="openssh"), "keys only"),
    (lambda d: d["targets"]["board"].update(workdir="/home/root; rm -rf /"), "workdir"),
    (lambda d: d.update(components={"app": "missing"}), "unknown target"),
    (lambda d: d.update(limits={"retention": 0}), "retention"),
    (lambda d: d.update(schema=2), "schema"),
], ids=["password", "token", "http", "placeholder", "client", "policy", "two-auth", "openssh-password", "workdir",
        "component-target", "retention", "schema"])
def test_config_rejects(tmp_path, change, message):
    data = site_data(tmp_path)
    change(data)
    with pytest.raises(DeployError, match=message):
        parse(data, tmp_path / "site.toml")


@pytest.mark.parametrize("section,key,value", [
    ("limits", "max_load", float("nan")), ("limits", "max_load", float("inf")),
    ("limits", "min_free_mb", -1), ("limits", "retention", True), ("limits", "lock_ttl_s", 0),
    ("jenkins", "timeout_s", -1), ("jenkins", "insecure_tls", 1), ("limits", "unknown", 2),
])
def test_numeric_and_unknown_config_is_fail_closed(tmp_path, section, key, value):
    data = site_data(tmp_path)
    data[section][key] = value
    with pytest.raises(DeployError, match=key):
        parse(data, tmp_path / "site.toml")


@pytest.mark.parametrize("key,value", [
    ("port", 0), ("port", 65536), ("port", True), ("port", "22"),
    ("connect_timeout_s", float("nan")), ("connect_timeout_s", 0),
    ("password_file", []), ("unknown", "x"),
])
def test_invalid_target_fails_before_connection(tmp_path, key, value):
    data = site_data(tmp_path)
    data["targets"]["board"][key] = value
    with pytest.raises(DeployError, match=key):
        parse(data, tmp_path / "site.toml")


@pytest.mark.parametrize("change", [
    {"schema": True}, {"targets": []}, {"components": []}, {"limits": []}, {"unknown": 1},
])
def test_config_rejects_malformed_sections(tmp_path, change):
    data = site_data(tmp_path)
    data.update(change)
    with pytest.raises(DeployError):
        parse(data, tmp_path / "site.toml")


@pytest.mark.parametrize("change", [
    {"stable_s": float("nan")}, {"stable_s": -1}, {"ready_timeout_s": float("inf")},
    {"ready_timeout_s": 0}, {"stable_s": 31}, {"stop_timeout_s": -1}, {"health_port": 65536},
])
def test_component_rejects_invalid_budgets(change):
    with pytest.raises(ValueError):
        component(**change)


def test_config_refuses_credentials_inside_the_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data = site_data(tmp_path, known_hosts=str(repo / "known_hosts"))
    with pytest.raises(DeployError, match="outside the repository"):
        parse(data, tmp_path / "site.toml", project_root=repo)


def test_config_allows_plain_http_only_on_localhost(tmp_path):
    data = site_data(tmp_path)
    data["jenkins"]["base_url"] = "http://127.0.0.1:8080"
    assert parse(data, tmp_path / "s.toml").jenkins.base_url == "http://127.0.0.1:8080"


def test_load_reads_the_environment_variable(tmp_path):
    file = tmp_path / "site.toml"
    file.write_text('[targets.board]\nhost = "10.0.0.1"\nuser = "root"\nknown_hosts = "kh"\n', encoding="utf-8")
    assert load(None, "WIDGET", env={"WIDGET_DEPLOY_CONFIG": str(file)}).targets["board"].host == "10.0.0.1"
    with pytest.raises(DeployError, match="WIDGET_DEPLOY_CONFIG"):
        load(None, "WIDGET", env={})


# ------------------------------------------------------------------ Jenkins


class FakeJenkins(http.server.BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0]
        status, headers, body = self.routes.get(path, (404, {}, b"not found"))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        if "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def jenkins_server():
    FakeJenkins.routes = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeJenkins)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", FakeJenkins.routes
    server.shutdown()
    server.server_close()


def api(data):
    return 200, {"Content-Type": "application/json"}, json.dumps(data).encode()


def build(result="SUCCESS", names=("app_1.2.3.tar.gz", "notes.txt")):
    return {"number": 42, "result": result, "timestamp": 1_700_000_000_000, "url": "http://x/job/App/42/",
            "artifacts": [{"fileName": n, "relativePath": f"out/{n}"} for n in names],
            "actions": [{}, {"lastBuiltRevision": {"SHA1": "abc123"}}]}


def client(base):
    return Jenkins(JenkinsSettings(base_url=base, timeout_s=5), env={}, sleep=lambda _s: None, retries=2)


def test_jenkins_resolves_and_downloads_a_build(jenkins_server, tmp_path):
    base, routes = jenkins_server
    routes["/view/X/job/App/lastSuccessfulBuild/api/json"] = api(build())
    routes["/view/X/job/App/42/artifact/out/app_1.2.3.tar.gz"] = (200, {}, b"image-bytes")
    ref = client(base).resolve(component())
    assert (ref.build_number, ref.version, ref.revision, ref.branch) == (42, "1.2.3", "abc123", None)
    assert ref.build_timestamp.startswith("2023-11-14")
    client(base).download(ref, tmp_path)
    assert ref.sha256 == hashlib.sha256(b"image-bytes").hexdigest() and ref.size == 11
    assert Path(ref.path).read_bytes() == b"image-bytes" and not list(tmp_path.glob("*.partial"))


def test_jenkins_branch_job_url(jenkins_server):
    base, routes = jenkins_server
    routes["/view/X/job/App/job/feature%2Fx/7/api/json"] = api(build())
    assert client(base).resolve(component(), "7", "feature/x").branch == "feature/x"


@pytest.mark.parametrize("data,message", [
    (build(result="FAILURE"), "only SUCCESS"),
    (build(names=("notes.txt",)), "0 artifacts match"),
    (build(names=("app_1.2.3.tar.gz", "app_1.2.4.tar.gz")), "2 artifacts match"),
])
def test_jenkins_refuses_unusable_builds(jenkins_server, data, message):
    base, routes = jenkins_server
    routes["/view/X/job/App/lastSuccessfulBuild/api/json"] = api(data)
    with pytest.raises(DeployError, match=message) as err:
        client(base).resolve(component())
    assert err.value.outcome == Outcome.ARTIFACT_FAILED


def test_jenkins_rejects_bad_build_names_and_404(jenkins_server):
    base, _routes = jenkins_server
    with pytest.raises(DeployError, match="build must be"):
        client(base).resolve(component(), "../../x")
    with pytest.raises(DeployError, match="404"):
        client(base).resolve(component())


def test_jenkins_refuses_a_redirect_to_another_host(jenkins_server):
    base, routes = jenkins_server
    port = base.rsplit(":", 1)[1]
    routes["/view/X/job/App/lastSuccessfulBuild/api/json"] = (302, {"Location": f"http://localhost:{port}/x"}, b"")
    with pytest.raises(DeployError, match="another host"):
        client(base).resolve(component())


def test_jenkins_truncated_download_leaves_nothing(jenkins_server, tmp_path):
    base, routes = jenkins_server
    routes["/a.tar.gz"] = (200, {"Content-Length": "100"}, b"short")
    ref = ArtifactRef(component="app", source="jenkins", file_name="app_1.tar.gz", url=f"{base}/a.tar.gz")
    with pytest.raises(DeployError) as err:
        client(base).download(ref, tmp_path)
    assert err.value.outcome == Outcome.ARTIFACT_FAILED and list(tmp_path.iterdir()) == []


def test_local_artifact_checks_the_pattern(tmp_path):
    good = tmp_path / "app_2.0.tar.gz"
    good.write_bytes(b"x")
    assert local_artifact(component(), good).version == "2.0"
    bad = tmp_path / "other.tar.gz"
    bad.write_bytes(b"x")
    with pytest.raises(DeployError, match="does not match"):
        local_artifact(component(), bad)


# ------------------------------------------------------------------ transport


def test_redactor_hides_secrets():
    redact = Redactor(["s3cret", "", "ab"])
    redact.add("t0ken")
    assert redact("s3cret and t0ken and ab") == "*** and *** and ab"


def test_openssh_uses_batch_mode_and_known_hosts(tmp_path):
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    t = target(client="openssh", identity=str(tmp_path / "id"), known_hosts=str(tmp_path / "kh"))
    ssh = OpenSshTransport(t, runner=runner)
    assert ssh.run("docker ps").stdout == "ok\n"
    ssh.put(tmp_path / "f.tar.gz", "/home/root/f.tar.gz")
    assert calls[0][:2] == ["ssh", "-o"] and "BatchMode=yes" in calls[0] and "StrictHostKeyChecking=accept-new" in calls[0]
    assert f"UserKnownHostsFile={tmp_path / 'kh'}" in calls[0] and calls[0][-2:] == ["root@10.0.0.1", "docker ps"]
    assert calls[1][0] == "scp" and calls[1][-1] == "root@10.0.0.1:/home/root/f.tar.gz"


def test_openssh_pinned_needs_known_hosts_and_reports_failures(tmp_path):
    with pytest.raises(DeployError, match="does not exist"):
        OpenSshTransport(target(client="openssh", host_key_policy="pinned", known_hosts=str(tmp_path / "none")))
    ssh = OpenSshTransport(target(client="openssh", known_hosts=str(tmp_path / "kh")),
                           runner=lambda argv, timeout: subprocess.CompletedProcess(argv, 255, "", "Host key verification failed"))
    with pytest.raises(DeployError, match="Host key verification failed"):
        ssh.run("true")


class FakeSsh:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def load_host_keys(self, path):
        self.calls.append(("load", path))

    def set_missing_host_key_policy(self, policy):
        self.calls.append(("policy", type(policy).__name__))

    def connect(self, host, **kwargs):
        self.calls.append(("connect", host, kwargs))
        if self.error:
            raise self.error

    def close(self):
        pass


def test_paramiko_policies_and_password(tmp_path):
    import paramiko
    fake = FakeSsh()
    t = target(password_env="BOARD_PW", known_hosts=str(tmp_path / "ssh" / "kh"))
    ParamikoTransport(t, {"BOARD_PW": "pw123"}, client_factory=lambda: fake)
    assert ("policy", "AutoAddPolicy") in fake.calls and (tmp_path / "ssh" / "kh").is_file()
    connect = next(c for c in fake.calls if c[0] == "connect")
    assert connect[2]["password"] == "pw123" and connect[2]["allow_agent"] is False
    fake = FakeSsh()
    ParamikoTransport(target(host_key_policy="pinned", known_hosts=str(tmp_path / "ssh" / "kh")), {}, client_factory=lambda: fake)
    assert ("policy", "RejectPolicy") in fake.calls
    with pytest.raises(DeployError, match="does not exist"):
        ParamikoTransport(target(host_key_policy="pinned", known_hosts=str(tmp_path / "none")), {}, client_factory=FakeSsh)
    changed = FakeSsh(paramiko.BadHostKeyException("10.0.0.1", paramiko.RSAKey.generate(1024), paramiko.RSAKey.generate(1024)))
    with pytest.raises(DeployError, match="CHANGED"):
        ParamikoTransport(target(known_hosts=str(tmp_path / "kh")), {}, client_factory=lambda: changed)
    refused = FakeSsh(paramiko.AuthenticationException("bad password pw123"))
    with pytest.raises(DeployError) as err:
        ParamikoTransport(t, {"BOARD_PW": "pw123"}, client_factory=lambda: refused)
    assert "pw123" not in str(err.value) and "***" in str(err.value)


def test_paramiko_drains_both_streams_before_waiting_for_exit():
    class Channel:
        def __init__(self):
            self.output = [b"x" * 65536, b"tail"]
            self.errors = [b"error" * 10000]
            self.closed = False

        def recv_ready(self):
            return bool(self.output)

        def recv_stderr_ready(self):
            return bool(self.errors)

        def recv(self, size):
            return self.output.pop(0)

        def recv_stderr(self, size):
            return self.errors.pop(0)

        def exit_status_ready(self):
            return not self.output and not self.errors  # remote blocks until both windows drain

        def recv_exit_status(self):
            assert self.exit_status_ready()
            return 0

        def close(self):
            self.closed = True

    channel = Channel()
    ssh = object.__new__(ParamikoTransport)
    ssh.paramiko = SimpleNamespace(SSHException=RuntimeError)
    ssh.redact = Redactor()
    ssh.client = SimpleNamespace(exec_command=lambda *args, **kwargs: (
        SimpleNamespace(close=lambda: None), SimpleNamespace(channel=channel), None))
    result = ssh.run("noisy-command", timeout_s=1)
    assert result.stdout == "x" * 65536 + "tail" and result.stderr == "error" * 10000
    assert channel.closed


def test_scp_total_deadline_cannot_reset_per_chunk(tmp_path, monkeypatch):
    from deploy_fakes import Clock

    from grf import deadline
    from grf.deploy import transport

    clock = Clock()
    timeouts = []

    class Channel:
        closed = False

        def settimeout(self, seconds):
            timeouts.append(seconds)

        def exec_command(self, command):
            pass

        def sendall(self, data):
            clock.sleep(0.4)

        def recv(self, count):
            clock.sleep(0.1)
            return b"\0"

        def close(self):
            self.closed = True

    channel = Channel()
    monkeypatch.setattr(transport, "Deadline", lambda seconds: deadline.Deadline(seconds, clock=clock.monotonic))
    ssh = object.__new__(ParamikoTransport)
    ssh.paramiko = SimpleNamespace(SSHException=RuntimeError)
    ssh.redact, ssh.host = Redactor(), "synthetic"
    ssh.client = SimpleNamespace(get_transport=lambda: SimpleNamespace(open_session=lambda **kwargs: channel))
    local = tmp_path / "large.tar"
    local.write_bytes(b"x" * (3 << 16))
    with pytest.raises(DeployError, match="deadline"):
        ssh.put(local, "/tmp/large.tar", timeout_s=1)
    assert channel.closed and timeouts[-1] < timeouts[0]


# ------------------------------------------------------------------ docker


def recipe():
    return ContainerRecipe(image="vendor/sti/arm", name="sti", privileged=True, init=True,
                           log_options=(("max-size", "10m"), ("max-file", "3")),
                           mounts=(Mount("/SMM/logs", "/logs"), Mount("/etc/cal", "/cal", readonly=True)),
                           env=(("MODE", "prod"),))


def test_run_command_is_exact():
    assert docker.run_command(recipe(), "vendor/sti/arm:1.0") == (
        "docker run -d --name sti --net=host --restart=unless-stopped --privileged --init "
        "--log-opt max-size=10m --log-opt max-file=3 -v /SMM/logs:/logs -v /etc/cal:/cal:ro -e MODE=prod vendor/sti/arm:1.0")


def test_drift_lists_every_difference():
    info = inspect_of(recipe(), "vendor/sti/arm:1.0", "sha256:1")
    assert docker.drift(recipe(), info, "vendor/sti/arm:1.0") == []
    info["HostConfig"]["Privileged"] = False
    info["Mounts"] = [{"Source": "/SMM/logs", "Destination": "/logs"}, {"Source": "/tmp", "Destination": "/x"}]
    found = docker.drift(recipe(), info, "vendor/sti/arm:2.0")
    assert any(f.startswith("image") for f in found) and any("privileged" in f for f in found)
    assert any("/etc/cal -> /cal missing" in f for f in found) and any("unexpected mount /tmp" in f for f in found)
    assert docker.tag_of("vendor/sti/arm:1.0") == "1.0" and docker.tag_of("host:5000/vendor/sti") is None


def test_recipe_and_component_validation():
    with pytest.raises(ValueError):
        ContainerRecipe(image="Vendor/App", name="app")
    with pytest.raises(ValueError):
        ContainerRecipe(image="vendor/app", name="app; rm")
    with pytest.raises(ValueError):
        component(version_from_filename=r"app_(.*)")


# ------------------------------------------------------------------ CLI


def fake_project(tmp_path, components=None):
    plugin = SimpleNamespace(deploy_components=lambda: list(components if components is not None else [component()]))
    return SimpleNamespace(name="Widget", cli="widget", env_prefix="WIDGET", root=tmp_path, plugin=plugin,
                           deploy_order=[], deploy_branch=None, path=lambda key: tmp_path / key)


def args_for(command, **values):
    defaults = dict(deploy_command=command, config=None, component="app", target=None, build="lastSuccessfulBuild",
                    branch=None, local_tar=None, outdir=None, trigger="cli", ignore_hold=False, yes=True, force=False,
                    break_stale_lock=False, json=False)
    defaults.update(values)
    return argparse.Namespace(**defaults)


class FakeDeployment:
    outcomes: list = []
    options: list = []

    def __init__(self, project, comp, config, tgt, **kwargs):
        self.comp, self.tgt = comp, tgt

    def deploy(self, options):
        FakeDeployment.options.append(options)
        record = DeployRecord("Widget", self.comp.name, self.tgt.name, self.tgt.host, "cli")
        record.outcome = FakeDeployment.outcomes.pop(0)
        return record


@pytest.fixture
def site_file(tmp_path, monkeypatch):
    file = tmp_path.parent / f"{tmp_path.name}-site.toml"
    file.write_text('[targets.board]\nhost = "10.0.0.1"\nuser = "root"\nknown_hosts = "kh"\n'
                    '[components]\napp = "board"\nother = "board"\n', encoding="utf-8")
    monkeypatch.setenv("WIDGET_DEPLOY_CONFIG", str(file))
    return file


@pytest.mark.parametrize("outcome,code", [(Outcome.DEPLOYED, 0), (Outcome.ROLLED_BACK, 5), (Outcome.HELD, 8)])
def test_cli_run_exit_codes_and_record(tmp_path, site_file, outcome, code):
    FakeDeployment.outcomes, FakeDeployment.options = [outcome], []
    out = tmp_path / "rec"
    assert deploy_cli.cmd_deploy(args_for("run", outdir=out), fake_project(tmp_path), deployment_factory=FakeDeployment) == code
    assert json.loads((out / "deployment.json").read_text(encoding="utf-8"))["outcome"] == outcome.value
    assert not FakeDeployment.options[0].dry_run


def test_cli_plan_is_a_dry_run(tmp_path, site_file):
    FakeDeployment.outcomes, FakeDeployment.options = [Outcome.DRY_RUN], []
    assert deploy_cli.cmd_deploy(args_for("plan", yes=False), fake_project(tmp_path), deployment_factory=FakeDeployment) == 0
    assert FakeDeployment.options[0].dry_run


def test_cli_sha256_is_validated_and_forwarded(tmp_path, site_file, capsys):
    FakeDeployment.outcomes, FakeDeployment.options = [Outcome.DEPLOYED], []
    args = args_for("run", outdir=tmp_path / "rec", sha256="a" * 64)
    assert deploy_cli.cmd_deploy(args, fake_project(tmp_path), deployment_factory=FakeDeployment) == 0
    assert FakeDeployment.options[0].sha256 == "a" * 64
    args.sha256 = "not-a-digest"
    assert deploy_cli.cmd_deploy(args, fake_project(tmp_path), deployment_factory=FakeDeployment) == 2
    assert "64 lowercase" in capsys.readouterr().err
    args.sha256, args.component = "a" * 64, "app,other"
    product = fake_project(tmp_path, [component(), component(name="other")])
    assert deploy_cli.cmd_deploy(args, product, deployment_factory=FakeDeployment) == 2
    assert "single --component" in capsys.readouterr().err


def test_cli_stops_after_the_first_failing_component(tmp_path, site_file):
    other = component(name="other", recipe=ContainerRecipe(image="vendor/other", name="other"))
    project = fake_project(tmp_path, [component(), other])
    project.deploy_order = ["other", "app"]
    FakeDeployment.outcomes, FakeDeployment.options = [Outcome.PREFLIGHT_FAILED, Outcome.DEPLOYED], []
    code = deploy_cli.cmd_deploy(args_for("run", component="app,other", outdir=tmp_path / "rec"), project,
                                 deployment_factory=FakeDeployment)
    assert code == 3 and FakeDeployment.outcomes == [Outcome.DEPLOYED]
    assert (tmp_path / "rec" / "other" / "deployment.json").is_file()


def test_cli_refuses_without_a_terminal_or_yes(tmp_path, site_file, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    FakeDeployment.outcomes = [Outcome.DEPLOYED]
    assert deploy_cli.cmd_deploy(args_for("run", yes=False), fake_project(tmp_path), deployment_factory=FakeDeployment) == 2
    assert "not an interactive terminal" in capsys.readouterr().err and FakeDeployment.outcomes == [Outcome.DEPLOYED]


def test_cli_errors(tmp_path, site_file, capsys):
    assert deploy_cli.cmd_deploy(args_for("run", component="nope"), fake_project(tmp_path)) == 2
    assert "unknown component" in capsys.readouterr().err
    assert deploy_cli.cmd_deploy(args_for("run"), fake_project(tmp_path, [])) == 2
    assert deploy_cli.cmd_deploy(args_for("components"), fake_project(tmp_path, [])) == 0
    assert "defines no deployable components" in capsys.readouterr().out
    assert deploy_cli.cmd_deploy(args_for("run", component="app,other", build="12"),
                                 fake_project(tmp_path, [component(), component(name="other")])) == 2
    assert "single --component" in capsys.readouterr().err


def test_cli_components_lists_targets(tmp_path, site_file, capsys):
    assert deploy_cli.cmd_deploy(args_for("components"), fake_project(tmp_path)) == 0
    out = capsys.readouterr().out
    assert "view/X/job/App" in out and "target board" in out


def test_cli_parser_registers_every_subcommand():
    parser = argparse.ArgumentParser()
    deploy_cli.add_parser(parser.add_subparsers(dest="command"), "WIDGET")
    args = parser.parse_args(["deploy", "run", "--component", "app", "--build", "12", "--yes", "--trigger", "nightly"])
    assert (args.deploy_command, args.build, args.yes, args.trigger) == ("run", "12", True, "nightly")
    for sub in ("components", "status", "builds --component a", "plan --component a", "rollback --component a"):
        assert parser.parse_args(["deploy", *sub.split()]).deploy_command == sub.split()[0]


def test_record_paths_and_loading(tmp_path):
    record = DeployRecord("Widget", "app", "board", "10.0.0.1", "nightly")
    record.outcome = Outcome.DEPLOYED
    record.write(tmp_path / "deploy" / "a")
    paths = deploy_cli.record_paths([str(tmp_path / "deploy")], {"GRF_DEPLOYMENT_RECORD": str(tmp_path / "deploy")})
    assert paths == [tmp_path / "deploy"]
    assert [r["component"] for r in load_records(paths)] == ["app"]
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not a GRF deployment record"):
        load_records([bad])


# ------------------------------------------------------------------ doctor --deploy


def test_deploy_checks_report_missing_credentials_without_printing_them(tmp_path):
    file = tmp_path / "site.toml"
    file.write_text('[jenkins]\nbase_url = "https://jenkins.example"\n[targets.board]\nhost = "10.0.0.1"\nuser = "root"\n'
                    f'known_hosts = "{(tmp_path / "kh").as_posix()}"\npassword_env = "BOARD_PW"\n'
                    '[components]\napp = "board"\n', encoding="utf-8")

    class Reachable:
        def __init__(self, *args, **kwargs):
            pass

        def api(self, url):
            return {}

    checks = {c.name: c for c in deploy_checks([component()], str(file), "WIDGET", tmp_path / "repo",
                                               env={"BOARD_PW": ""}, jenkins_factory=Reachable)}
    assert checks["deploy: site config"].status == "OK" and checks["deploy: jenkins"].status == "OK"
    assert checks["deploy: board credential"].status == "FAIL" and "is not set" in checks["deploy: board credential"].detail
    assert checks["deploy: board host key"].status == "WARN"
    checks = {c.name: c for c in deploy_checks([component()], str(file), "WIDGET", tmp_path / "repo", deep=True,
                                               env={"BOARD_PW": "s3cret"}, jenkins_factory=Reachable,
                                               transport_factory=lambda t, e: SimpleNamespace(
                                                   run=lambda c: CommandResult(0, "24.0\n", ""), close=lambda: None))}
    assert checks["deploy: board credential"].status == "OK" and "s3cret" not in str(checks)
    assert checks["deploy: board board"].detail.endswith("docker 24.0")


def test_deploy_checks_without_config(tmp_path):
    checks = deploy_checks([], None, "WIDGET", tmp_path, env={})
    assert [c.status for c in checks] == ["WARN", "FAIL"]


# ------------------------------------------------------------------ report and starter


def test_report_section_warns_about_bad_outcomes():
    good = {"component": "app", "target": "board", "outcome": "DEPLOYED",
            "artifact": {"version": "1.2.3", "branch": "main", "build_number": 42, "file_name": "app.tar.gz", "sha256": "ab" * 32}}
    html = _deployments_html([good])
    assert "Deployed builds" in html and "#42" in html and "did not finish" not in html
    assert "did not finish cleanly" in _deployments_html([good, {**good, "component": "sti", "outcome": "ROLLED_BACK"}])
    assert _deployments_html([]) == ""


def test_starter_deploy_files_only_with_the_flag(tmp_path):
    plain = init_project(tmp_path / "plain", "Widget", "WID")
    assert not (plain / "jenkins" / "Jenkinsfile.deploy").exists()
    root = init_project(tmp_path / "deploy", "Widget", "WID", with_deploy=True)
    jenkinsfile = (root / "jenkins" / "Jenkinsfile.deploy").read_text(encoding="utf-8")
    assert "withCredentials" in jenkinsfile and "'run --yes'" in jenkinsfile and "grf deploy ${action}" in jenkinsfile
    assert "grf request check" in jenkinsfile and "grf gate --request" in jenkinsfile
    assert "--sha256" in jenkinsfile and "acceptance.json" in jenkinsfile
    assert jenkinsfile.index("grf request check") < jenkinsfile.index("grf deploy ${action}")
    assert jenkinsfile.index("grf run --tier") < jenkinsfile.index("grf gate --request")
    example = (root / "robot" / "environments" / "deploy.example.toml").read_text(encoding="utf-8")
    with pytest.raises(DeployError, match="placeholder"):
        parse(tomllib.loads(example), Path("deploy.example.toml"))
    filled = example
    for placeholder in sorted(set(re.findall(r"<[^<>\n]+>", example))):
        filled = filled.replace(placeholder, "10.0.0.9" if "ip" in placeholder.lower() else "x")
    config = parse(tomllib.loads(filled), Path("deploy.toml"))
    assert config.targets and not any(k in filled.lower() for k in ("password =", "token ="))
