"""The deployment step machine against a simulated board (tests/deploy_fakes.py). No network, no instrument."""

import hashlib

import pytest
from deploy_fakes import PREV, WD, Clock, FakeBoard, component, image_id, inspect_of, site, tar_name

from grf.deploy.engine import Deployment, Options
from grf.deploy.model import EXIT_CODES, DeployError, Outcome
from grf.deploy.transport import CommandResult


@pytest.fixture
def board():
    b = FakeBoard(component().recipe, secret="s3cret-pw")
    b.add_companion()
    b.add_build("1.2.2")
    return b


def tarball(tmp_path, version="1.2.3", board=None):
    path = tmp_path / tar_name(version)
    path.write_bytes(version.encode())
    if board is not None:
        board.tar_images[path.name] = (f"vendor/app/arm:{version}", image_id(version))
    return path


def deploy(board, tmp_path, comp=None, config=None, **options):
    clock = Clock()
    output = tmp_path / f"out-{len(list(tmp_path.glob('out-*')))}"
    d = Deployment("Widget", comp or component(), config or site(), (config or site()).targets["board"],
                   work_dir=output, env={}, transport_factory=lambda t, env: board,
                   sleep=clock.sleep, monotonic=clock.monotonic, echo=lambda _t: None)
    record = d.deploy(Options(**options))
    return record


def step_names(record):
    return [(s.name, s.status) for s in record.steps]


def lock_free(board):
    return not any(".grf-deploy.lock" in p for p in board.files)


def test_expected_sha256_accepts_exact_bytes(board, tmp_path):
    tar = tarball(tmp_path, board=board)
    digest = hashlib.sha256(tar.read_bytes()).hexdigest()
    record = deploy(board, tmp_path, local_tar=tar, sha256=digest)
    assert record.outcome == Outcome.DEPLOYED and record.artifact.sha256 == digest


def test_filename_cannot_fabricate_installed_version(board, tmp_path):
    tar = tarball(tmp_path, board=board)
    board.tar_images[tar.name] = ("vendor/app/arm:9.9", image_id("9.9"))
    record = deploy(board, tmp_path, local_tar=tar)
    assert record.outcome == Outcome.ROLLED_BACK
    assert "contradicts loaded image" in record.message
    assert board.running_image_id() == image_id("1.2.2")


def test_locked_snapshot_is_the_rollback_point(board, tmp_path, monkeypatch):
    from grf.deploy import lock

    original = lock.acquire

    def acquire(*args):
        original(*args)
        board.add_build("1.2.4")

    monkeypatch.setattr(lock, "acquire", acquire)
    board.fail.append(["^docker load", CommandResult(1, "", "broken archive"), 1])
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome == Outcome.ROLLED_BACK
    assert result.previous.version == "1.2.4"
    assert board.running_image_id() == image_id("1.2.4")


def test_hold_is_checked_under_the_lock(board, tmp_path, monkeypatch):
    from grf.deploy import lock

    original = lock.acquire

    def acquire(*args):
        original(*args)
        board.add_file(lock.hold_path(WD), text="new hold")

    monkeypatch.setattr(lock, "acquire", acquire)
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome == Outcome.HELD
    assert not board.mutating(include_locks=False)


@pytest.mark.parametrize("answer", [
    CommandResult(1, "", "permission denied"),
    CommandResult(1, "", "Cannot connect to the Docker daemon"),
    CommandResult(0, "[]", ""),
    CommandResult(0, "[null]", ""),
    CommandResult(0, "not json", ""),
])
def test_inspect_uncertainty_is_not_absence(board, tmp_path, answer):
    board.fail.append(["^docker inspect", answer, 1])
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome not in (Outcome.DEPLOYED, Outcome.UNCHANGED)
    assert not board.mutating(include_locks=False)
    assert board.running_image_id() == image_id("1.2.2")


def test_run_conflict_never_deletes_a_foreign_container(board, tmp_path, monkeypatch):
    original = board.run
    foreign = inspect_of(board.recipe, "foreign:7", "sha256:foreign")

    def run(command, **kwargs):
        if command.startswith("docker run"):
            board.containers["app"] = foreign
        return original(command, **kwargs)

    monkeypatch.setattr(board, "run", run)
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome == Outcome.ROLLBACK_FAILED
    assert board.containers["app"] == foreign
    assert board.containers["app_grf_previous"]["Image"] == image_id("1.2.2")
    assert not any(c.startswith("docker rm -f") for c in board.commands)


def test_successful_run_with_lost_response_removes_only_owned_id(board, tmp_path, monkeypatch):
    original = board.run

    def run(command, **kwargs):
        answer = original(command, **kwargs)
        if command.startswith("docker run"):
            raise DeployError("response lost after create")
        return answer

    monkeypatch.setattr(board, "run", run)
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome == Outcome.ROLLED_BACK
    assert any(c == "docker rm -f container-" + image_id("1.2.3") for c in board.commands)
    assert board.running_image_id() == image_id("1.2.2")


def test_unchanged_must_pass_product_health(board, tmp_path):
    tar = tarball(tmp_path, board=board)
    assert deploy(board, tmp_path, local_tar=tar).outcome == Outcome.DEPLOYED
    board.commands.clear()
    board.fail.append(["^health-probe$", CommandResult(1, "", "not healthy"), 100])
    result = deploy(board, tmp_path, comp=component(health_command="health-probe"), local_tar=tar)
    assert result.outcome == Outcome.FAILED
    assert not any(c.startswith("docker stop") for c in board.commands)


def test_mount_access_mode_is_verified(board, tmp_path):
    board.after_run = lambda c: c["Mounts"][0].update(RW=False)
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome == Outcome.ROLLED_BACK
    assert "writable" in result.message


def test_cleanup_never_prunes_unrelated_dangling_images(board, tmp_path):
    board.images.append(("<none>:<none>", "sha256:unrelated"))
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome == Outcome.DEPLOYED
    assert ("<none>:<none>", "sha256:unrelated") in board.images
    assert not any(c.startswith("docker image prune") for c in board.commands)


@pytest.mark.parametrize("digest", ["0" * 64, "bad", "A" * 64, "", 123])
def test_bad_expected_sha256_never_connects(board, tmp_path, digest):
    tar = tarball(tmp_path, board=board)
    contacted = []

    def transport(*args):
        contacted.append(True)
        return board

    d = Deployment("Widget", component(), site(), site().targets["board"], work_dir=tmp_path,
                   transport_factory=transport, echo=lambda _: None)
    result = d.deploy(Options(local_tar=tar, sha256=digest))
    assert result.outcome == Outcome.ARTIFACT_FAILED
    assert not contacted and not board.commands


def test_jenkins_changed_bytes_fail_before_board_connection(board, tmp_path):
    from grf.deploy.model import ArtifactRef

    class Jenkins:
        def resolve(self, *args):
            return ArtifactRef("app", "jenkins", "app.tar.gz", build_number=7)

        def download(self, ref, destination):
            ref.sha256 = "b" * 64
            return ref

    contacted = []
    d = Deployment("Widget", component(), site(), site().targets["board"], work_dir=tmp_path,
                   jenkins_factory=lambda *args: Jenkins(),
                   transport_factory=lambda *args: contacted.append(True),
                   echo=lambda _: None)
    result = d.deploy(Options(build="7", sha256="a" * 64))
    assert result.outcome == Outcome.ARTIFACT_FAILED and not contacted


def test_success_replaces_the_container_and_keeps_the_previous_build(board, tmp_path):
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.DEPLOYED and EXIT_CODES[record.outcome] == 0, record.message
    assert board.running_image_id() == image_id("1.2.3")
    assert "app_grf_previous" not in board.containers
    assert board.names_in(WD) == [tar_name("1.2.3")]
    assert board.names_in(PREV) == [tar_name("1.2.2")]
    assert board.marker()["sha256"] == record.artifact.sha256 and board.marker()["imageId"] == image_id("1.2.3")
    assert lock_free(board) and board.closed
    assert record.installed.version == "1.2.3" and record.previous.version == "1.2.2"
    assert all(status in ("OK",) for _, status in step_names(record)), step_names(record)
    order = [next(i for i, c in enumerate(board.commands) if c.startswith(p))
             for p in ("mkdir /home/root/.grf-deploy.lock", "PUT", "docker stop", "docker rename app app_grf_previous",
                       "docker load", "docker run", "docker rm app_grf_previous", "rm -rf -- /home/root/.grf-deploy.lock")]
    assert order == sorted(order)


def test_same_artifact_is_unchanged_unless_forced(board, tmp_path):
    tar = tarball(tmp_path, board=board)
    assert deploy(board, tmp_path, local_tar=tar).outcome == Outcome.DEPLOYED
    board.commands.clear()
    record = deploy(board, tmp_path, local_tar=tar)
    assert record.outcome == Outcome.UNCHANGED and EXIT_CODES[record.outcome] == 0
    assert not board.mutating(include_locks=False)
    assert deploy(board, tmp_path, local_tar=tar, force=True).outcome == Outcome.DEPLOYED


def test_dry_run_changes_nothing(board, tmp_path):
    before = board.snapshot()
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board), dry_run=True)
    assert record.outcome == Outcome.DRY_RUN and record.dry_run
    assert board.mutating() == [] and board.snapshot() == before
    assert any(line.startswith("docker run -d --name app") for line in record.planned)


def test_hold_blocks_every_deployment_unless_ignored(board, tmp_path):
    board.add_file(f"{WD}/.grf-deploy.hold", text="calibration until Friday")
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.HELD and EXIT_CODES[Outcome.HELD] == 8
    assert "calibration until Friday" in record.message and board.mutating(include_locks=False) == []
    assert deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board), ignore_hold=True).outcome == Outcome.DEPLOYED


def test_a_held_lock_stops_the_deployment(board, tmp_path):
    board.add_file(f"{WD}/.grf-deploy.lock/owner.json", text='{"user": "alice", "component": "app"}')
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.LOCKED and "alice" in record.message and "Wait" in record.message
    assert board.running_image_id() == image_id("1.2.2")
    assert f"{WD}/.grf-deploy.lock/owner.json" in board.files  # someone else's lock is never released


def test_a_stale_lock_needs_break_stale_lock(board, tmp_path):
    board.add_file(f"{WD}/.grf-deploy.lock/owner.json", text='{"user": "crashed", "token": "old"}')
    board.lock_age = 99999
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.LOCKED and "--break-stale-lock" in record.message
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board), break_stale_lock=True)
    assert record.outcome == Outcome.DEPLOYED and lock_free(board)


@pytest.mark.parametrize("setup,expected", [
    (lambda b: setattr(b, "free_mb", 100), "MB free"),
    (lambda b: setattr(b, "load", 9.5), "load"),
    (lambda b: b.containers.__setitem__("app_grf_previous", {"Image": "x", "State": {"Status": "exited"}}), "did not finish"),
    (lambda b: b.fail.append(["^docker version", CommandResult(1, "", "Cannot connect to the Docker daemon"), 1]), "Docker daemon"),
])
def test_preflight_failures_change_nothing(board, tmp_path, setup, expected):
    setup(board)
    before = board.snapshot()
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.PREFLIGHT_FAILED and EXIT_CODES[record.outcome] == 3
    assert expected in record.message
    assert board.mutating(include_locks=False) == [] and board.snapshot() == before


def test_missing_companion_is_only_a_warning(board, tmp_path):
    del board.containers["mosquitto"]
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.DEPLOYED
    assert ("preflight (read-only)", "WARN") in step_names(record)


def _crash(container):
    container["State"]["Status"] = "exited"


def _restart_loop(container):
    container["RestartCount"] = 3


@pytest.mark.parametrize("inject", [
    lambda b: b.fail.append(["^docker load", CommandResult(1, "", "invalid tar header"), 1]),
    lambda b: b.fail.append(["^docker load", CommandResult(0, "Loaded image: other/image:1\n", ""), 1]),
    lambda b: b.fail.append(["^docker image inspect", CommandResult(1, "", "No such image"), 1]),
    lambda b: b.fail.append(["^docker run", CommandResult(125, "", "invalid mount"), 1]),
    lambda b: setattr(b, "after_run", _crash),
    lambda b: setattr(b, "after_run", _restart_loop),
    lambda b: setattr(b, "after_run", lambda c: c.update(Image="sha256:wrong")),
    lambda b: setattr(b, "after_run", lambda c: c["HostConfig"].update(Privileged=True)),
    lambda b: b.fail.append(["^docker load", KeyboardInterrupt(), 1]),
    lambda b: b.fail.append(["^docker run", RuntimeError("ssh channel closed"), 1]),
], ids=["load-fails", "wrong-image", "inspect-fails", "run-fails", "crashes", "restarts", "wrong-id", "drift",
        "ctrl-c", "unexpected"])
def test_failure_after_the_stop_rolls_back(board, tmp_path, inject):
    inject(board)
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.ROLLED_BACK and EXIT_CODES[record.outcome] == 5, record.message
    assert board.running_image_id() == image_id("1.2.2")
    assert "app_grf_previous" not in board.containers
    assert board.names_in(WD) == [tar_name("1.2.2")] and board.names_in(PREV) == []
    assert lock_free(board)
    assert record.installed == record.previous
    assert ("rollback", "OK") in step_names(record)


def test_ctrl_c_is_reported_as_abort(board, tmp_path):
    board.fail.append(["^docker load", KeyboardInterrupt(), 1])
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.ROLLED_BACK and "aborted" in record.message


def test_no_previous_container_means_rollback_failed(tmp_path):
    board = FakeBoard(component().recipe)
    board.add_companion()
    board.after_run = _crash
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.ROLLBACK_FAILED and EXIT_CODES[record.outcome] == 6
    assert any("docker rename" in line for line in record.recovery) and lock_free(board)


def test_failed_rollback_lists_recovery_commands(board, tmp_path):
    board.after_run = _crash
    board.fail.append(["^docker rename app_grf_previous app", CommandResult(1, "", "rename refused"), 1])
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.ROLLBACK_FAILED
    assert "manual attention" in record.message and record.recovery and lock_free(board)


def test_upload_checksum_mismatch_restores_the_files_and_never_stops_the_container(board, tmp_path):
    board.fail.append([r"^sha256sum .*\.partial", CommandResult(0, "0" * 64 + "  x\n", ""), 1])
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert record.outcome == Outcome.FAILED and "checksum" in record.message
    assert not any(c.startswith("docker stop") for c in board.commands)
    assert board.names_in(WD) == [tar_name("1.2.2")] and board.names_in(PREV) == []
    assert board.running_image_id() == image_id("1.2.2") and lock_free(board)


def test_retention_keeps_the_configured_number_of_builds(board, tmp_path):
    for version in ("1.2.3", "1.2.4", "1.2.5", "1.2.6"):
        assert deploy(board, tmp_path, local_tar=tarball(tmp_path, version, board)).outcome == Outcome.DEPLOYED
    assert board.names_in(PREV) == [tar_name("1.2.4"), tar_name("1.2.5")]
    assert [d for _, d in board.images] == [image_id(v) for v in ("1.2.6", "1.2.5", "1.2.4")]


def test_restore_redeploys_the_newest_retained_build(board, tmp_path):
    assert deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board)).outcome == Outcome.DEPLOYED
    record = deploy(board, tmp_path, restore=True)
    assert record.outcome == Outcome.RESTORED and record.trigger == "rollback", record.message
    assert record.artifact.source == "board" and record.artifact.file_name == tar_name("1.2.2")
    assert board.running_image_id() == image_id("1.2.2")
    assert board.names_in(WD) == [tar_name("1.2.2")] and board.names_in(PREV) == [tar_name("1.2.3")]


def test_restore_without_retained_build(board, tmp_path):
    record = deploy(board, tmp_path, restore=True)
    assert record.outcome == Outcome.ARTIFACT_FAILED and "no retained" in record.message


def test_passwords_never_reach_the_record(board, tmp_path):
    board.fail.append(["^docker run", CommandResult(125, "", "auth s3cret-pw rejected"), 1])
    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    text = str(record.as_dict())
    assert record.outcome == Outcome.ROLLED_BACK and "s3cret-pw" not in text and "***" in text


def test_artifact_problems_never_connect(tmp_path):
    def refuse(*_args):
        raise AssertionError("must not connect")

    d = Deployment("Widget", component(), site(), site().targets["board"], work_dir=tmp_path, env={},
                   transport_factory=refuse, echo=lambda _t: None)
    record = d.deploy(Options(local_tar=tmp_path / "missing.tar.gz"))
    assert record.outcome == Outcome.ARTIFACT_FAILED and EXIT_CODES[record.outcome] == 4


def test_connection_failure_is_failed(tmp_path):
    def unreachable(*_args):
        raise DeployError("cannot reach 10.0.0.1:22")

    tar = tmp_path / tar_name("1.2.3")
    tar.write_bytes(b"x")
    d = Deployment("Widget", component(), site(), site().targets["board"], work_dir=tmp_path, env={},
                   transport_factory=unreachable, echo=lambda _t: None)
    record = d.deploy(Options(local_tar=tar))
    assert record.outcome == Outcome.FAILED and "cannot reach" in record.message
    assert record.finished


def test_status_is_read_only(board, tmp_path):
    board.add_file(f"{PREV}/{tar_name('1.2.1')}")
    d = Deployment("Widget", component(), site(), site().targets["board"], work_dir=tmp_path, env={},
                   transport_factory=lambda t, env: board, echo=lambda _t: None)
    status = d.status()
    assert status["installed"]["version"] == "1.2.2" and status["installed"]["status"] == "running"
    assert status["previousBuilds"] == [tar_name("1.2.1")] and status["drift"] == []
    assert not status["unfinishedDeployment"] and status["hold"] is None and status["lock"] is None
    assert board.mutating() == []


def test_record_is_written_and_appended_to_the_audit_log(board, tmp_path):
    import json

    record = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    audit = tmp_path / "audit" / "deployments.jsonl"
    path = record.write(tmp_path / "out-0", str(audit))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == 1 and data["outcome"] == "DEPLOYED" and data["artifact"]["version"] == "1.2.3"
    assert (tmp_path / "out-0" / "deploy.log").read_text(encoding="utf-8").count("[OK]") >= 5
    assert json.loads(audit.read_text(encoding="utf-8").splitlines()[0])["component"] == "app"


def test_output_reuse_fails_before_connection(board, tmp_path):
    from grf.deploy.record import DeployRecord

    output = tmp_path / "reserved"
    previous = DeployRecord("Widget", "app", "board", "host", "cli").write(output)
    before = previous.read_bytes()
    d = Deployment("Widget", component(), site(), site().targets["board"], work_dir=output,
                   transport_factory=lambda *args: pytest.fail("must not connect"))
    with pytest.raises(FileExistsError):
        d.deploy(Options(local_tar=tarball(tmp_path, board=board)))
    assert previous.read_bytes() == before


def test_pre_action_record_survives_an_interrupted_step(board, tmp_path, monkeypatch):
    import json

    original = board.put

    def put(local, remote, **kwargs):
        data = json.loads((tmp_path / "out-0" / "deployment.json").read_text(encoding="utf-8"))
        assert data["finished"] is None and data["outcome"] is None
        assert data["previous"]["version"] == "1.2.2"
        assert data["steps"][-1]["status"] == "IN_PROGRESS"
        assert data["artifact"]["sha256"] and data["recovery"]
        return original(local, remote, **kwargs)

    monkeypatch.setattr(board, "put", put)
    assert deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board)).outcome == Outcome.DEPLOYED


def test_checkpoint_failure_after_stop_does_not_block_rollback(board, tmp_path, monkeypatch):
    from grf.deploy.record import DeployRecord

    original = DeployRecord.write
    failed = []

    def write(record, *args, **kwargs):
        if not failed and record.steps and record.steps[-1].name.startswith("stop ") and record.steps[-1].status == "OK":
            failed.append(True)
            raise OSError("disk full")
        return original(record, *args, **kwargs)

    monkeypatch.setattr(DeployRecord, "write", write)
    result = deploy(board, tmp_path, local_tar=tarball(tmp_path, board=board))
    assert result.outcome == Outcome.ROLLED_BACK
    assert "cannot persist" in result.message and "cleanup may be unjournaled" in "\n".join(result.log)
    assert board.running_image_id() == image_id("1.2.2")
    assert lock_free(board)
