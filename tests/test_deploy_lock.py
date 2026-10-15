"""Lock ownership and initialization races using only the synthetic board."""

import json

import pytest
from deploy_fakes import WD, FakeBoard, component

from grf.deploy import lock
from grf.deploy.model import DeployError


@pytest.fixture
def board():
    return FakeBoard(component().recipe)


def test_initializing_lock_is_never_stale(board):
    board.add_file(lock.lock_path(WD) + "/.dir", text="")
    board.lock_age = 999999
    with pytest.raises(DeployError, match="incomplete/legacy"):
        lock.acquire(board, WD, {"token": "new"}, 10, True, print)
    assert lock.lock_path(WD) + "/.dir" in board.files
    assert not any(c.startswith("rm -rf") for c in board.commands)


def test_cannot_release_replacement_owner(board):
    lock.acquire(board, WD, {"token": "one"}, 10, False, print)
    board.add_file(lock.lock_path(WD) + "/owner.json", text=json.dumps({"token": "two"}))
    with pytest.raises(DeployError, match="another operation"):
        lock.release(board, WD, "one")
    assert lock.lock_info(board, WD)["token"] == "two"


def test_guard_serializes_initialization_takeover_and_release(board):
    board.add_file(lock.lock_path(WD) + ".guard/.dir", text="")
    with pytest.raises(DeployError, match="guard"):
        lock.acquire(board, WD, {"token": "one"}, 10, True, print)
    with pytest.raises(DeployError, match="guard"):
        lock.release(board, WD, "one")
    assert not any(c.startswith("rm -rf") for c in board.commands)


def test_metadata_write_failure_leaves_lock_for_inspection(board):
    board.fail.append(["^printf", DeployError("lost connection"), 1])
    with pytest.raises(DeployError, match="lost connection"):
        lock.acquire(board, WD, {"token": "one"}, 10, False, print)
    assert lock.lock_path(WD) + "/.dir" in board.files
    assert lock.lock_path(WD) + ".guard/.dir" not in board.files
