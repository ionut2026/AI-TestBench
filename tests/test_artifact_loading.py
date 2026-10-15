import json

import pytest

from grf.deploy.model import ArtifactRef, Installed, Outcome
from grf.deploy.record import DeployRecord, load_records
from grf.pipeline.drift import load_reviews
from grf.pipeline.ingest import load_catalog


@pytest.mark.parametrize("schema", [None, 1, 2])
def test_catalog_legacy_and_current_schemas(tmp_path, schema):
    data = {"specifications": {"1": {"id": 1, "hash": "a" * 64}}, "hashVersion": 2}
    if schema is not None:
        data["schema"] = schema
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_catalog(path) == data


@pytest.mark.parametrize("patch", [
    {"schema": True}, {"schema": 3}, {"hashVersion": "2"}, {"hashVersion": 3},
    {"specifications": []}, {"specifications": {"one": {}}},
    {"specifications": {"1": []}}, {"specifications": {"1": {"hash": 42}}},
    {"specifications": {"1": {"hash": "abcdef12", "id": True}}},
    {"specifications": {"1": {"hash": "abcdef12", "satisfies": "2"}}},
    {"specifications": {"1": {"hash": "abcdef12", "satisfies": [True]}}},
    {"specifications": {"1": {"hash": "abcdef12", "testable": "false"}}},
    {"specifications": {"1": {"hash": "abcdef12", "baseline": []}}},
    {"specifications": {"1": {"hash": "abcdef12", "baseline": {"userStories": [False]}}}},
    {"retiredStates": "Deleted"}, {"requirements": []},
])
def test_catalog_rejects_malformed_nested_data(tmp_path, patch):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"schema": 2, "hashVersion": 2, "specifications": {}, **patch}), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot load catalog"):
        load_catalog(path)


@pytest.mark.parametrize("text", [
    "review = 'not tables'", "review = [1]", "[[review]]\ntest = 1",
    '[[review]]\ntest = "A"\nreviewer = 7\nspechash = "abcdef12"',
    '[[review]]\ntest = "A"\nreviewer = "Owner"\nspechash = 7',
    '[[review]]\ntest = "A"\nreviewer = "Owner"\nspechash = "abcdef12"\nverdict = "okay"',
    '[[review]]\ntest = "A"\nreviewer = "Owner"\nspechash = "abcdef12"\nfingerprint = "bad"',
])
def test_malformed_review_entries_fail_at_boundary(tmp_path, text):
    path = tmp_path / "reviews.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="cannot load review ledger"):
        load_reviews(path)


def test_legacy_reviews_remain_available_for_drift_but_not_current_content_approval(tmp_path):
    path = tmp_path / "reviews.toml"
    path.write_text('[[review]]\ntest = "A"\nreviewer = "Owner"\nspechash = "abcdef12"\n', encoding="utf-8")
    assert load_reviews(path)[0]["reviewer"] == "Owner"
    assert "fingerprint" not in load_reviews(path)[0]
    assert load_reviews(tmp_path / "absent.toml") == []


@pytest.mark.parametrize("patch", [
    {"schema": True}, {"schema": 2}, {"dryRun": 0}, {"component": []},
    {"installed": []}, {"installed": {"restarts": True}},
    {"artifact": {"size": -1}}, {"steps": {}},
    {"steps": [{"name": "probe", "status": "OK", "seconds": float("nan")}]},
    {"steps": [{"name": "probe", "status": "success"}]},
    {"actor": []}, {"framework": {"grf": True}}, {"planned": "commands"},
])
def test_malformed_deployment_records_fail_before_report(tmp_path, patch):
    data = DeployRecord("Widget", "app", "board", "fake", "manual",
                        outcome=Outcome.DEPLOYED, installed=Installed("app", "running", version="1"),
                        artifact=ArtifactRef("app", "local", "app.tar.gz")).as_dict()
    path = tmp_path / "deployment.json"
    path.write_text(json.dumps({**data, **patch}), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read the deployment record"):
        load_records([path])
