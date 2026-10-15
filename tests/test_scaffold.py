import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from grf import project
from grf.cli import main
from grf.pipeline import ingest
from grf.scaffold import DEPLOY_TEMPLATES, TEMPLATES, init_project


def test_reusable_source_and_templates_use_product_neutral_examples():
    root = Path(__file__).resolve().parents[1] / "src" / "grf"
    for pattern in ("*.py", "*.tpl"):
        for path in root.rglob(pattern):
            assert not re.search(r"\b(?:SMM|appSMM|smm-auto)\b", path.read_text(encoding="utf-8"), re.I), path


def command(root: Path, *args: str, expected: int = 0) -> subprocess.CompletedProcess:
    env = {**os.environ, project.PROJECT_ENV: str(root), "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(root / "src")}
    result = subprocess.run([sys.executable, "-m", "grf", *args], cwd=root, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == expected, result.stdout + result.stderr
    return result


def test_init_works_without_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "widget"
    assert main(["init", str(target), "--name", "Widget", "--spec-prefix", "WID"]) == 0
    assert "Created product starter" in capsys.readouterr().out
    p = project.Project.load(target)
    assert p.name == "Widget" and p.spec_prefix == "WID"
    assert (target / ".github/agents/test-reviewer.agent.md").is_file()
    assert "__SPEC_HASH__" not in (target / "robot/suites/demo/counter.robot").read_text(encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0 and "init" in capsys.readouterr().out


@pytest.mark.parametrize("name,prefix", [("", "SPEC"), ("Widget\nBad", "SPEC"), ("Widget", "42"),
                                      ("Widget", "../BAD"), ("Widget", "bad-tag"), ("!!!", "SPEC")])
def test_invalid_names_create_nothing(tmp_path, name, prefix):
    target = tmp_path / "new"
    with pytest.raises(ValueError):
        init_project(target, name, prefix)
    assert not target.exists()


def test_init_refuses_existing_files(tmp_path, capsys):
    keep = tmp_path / "keep.txt"
    keep.write_text("user-owned", encoding="utf-8")
    assert main(["init", str(tmp_path)]) == 2
    assert "new or empty directory" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == [keep]
    assert keep.read_text(encoding="utf-8") == "user-owned"


def test_checked_in_demo_matches_starter(tmp_path):
    generated = init_project(tmp_path / "demo", "Demo Counter", "SPEC", with_deploy=True)
    example = Path(__file__).resolve().parents[1] / "examples/demo"
    for relative in {**TEMPLATES, **DEPLOY_TEMPLATES}.values():
        assert (example / relative).read_text(encoding="utf-8") == (generated / relative).read_text(encoding="utf-8")


def test_generated_project_end_to_end(tmp_path):
    root = init_project(tmp_path / "widget", 'Widget "Test" 2.0', "WID")
    command(root, "demo-ingest")
    catalog = json.loads((root / "catalog/demo.json").read_text(encoding="utf-8"))
    assert catalog["counts"] == {"specifications": 1, "requirements": 1, "userStories": 1, "explorativeTests": 1}
    assert catalog["specifications"]["1"]["satisfies"] == [2]
    assert catalog["specifications"]["1"]["userStories"] == [3]
    assert catalog["specifications"]["1"]["explorativeTests"] == [4]
    command(root, "briefs")
    brief = (root / "generated/briefs/WID-1.md").read_text(encoding="utf-8")
    assert "REQ-2" in brief and "US-3" in brief and "ET-4" in brief and "Counter Should Be" in brief
    command(root, "lint")
    command(root, "drift", "--strict")
    command(root, "doctor", "--tier", "mock")
    command(root, "run", "--tier", "mock", "--outdir", "results/mock")
    report = json.loads((root / "results/mock/traceability.json").read_text(encoding="utf-8"))
    assert report["totals"] == {"pass": 1, "fail": 0, "skip": 0}
    assert not report["productEvidence"] and report["tier"] == "mock"
    for key, sid in (("specifications", 1), ("requirements", 2), ("userStories", 3)):
        assert report[key][0]["id"] == sid and report[key][0]["verdict"] == "PASS"
    assert not report["specifications"][0]["verified"]
    assert report["review"]["unreviewed"] == 1
    assert (root / "results/mock/traceability.html").is_file()
    command(root, "run", "--tier", "mock", "--processes", "2", "--outdir", "results/parallel",
            "--metadata", "Owner:synthetic", "--include", "WID-1")
    parallel = json.loads((root / "results/parallel/traceability.json").read_text(encoding="utf-8"))
    assert parallel["totals"] == {"pass": 1, "fail": 0, "skip": 0}
    assert not parallel["productEvidence"] and parallel["coverage"]["verified"] == 0
    assert parallel["metadata"]["Owner"] == "synthetic"
    assert not (root / ".pabotsuitenames").exists()

    # Text drift and broken tests must fail the real CLI gates, not just the helper functions.
    catalog["specifications"]["1"]["hash"] = ingest.spec_hash("Changed specification")
    ingest.write_catalog(root / "catalog/demo.json", catalog)
    assert "STALE" in command(root, "drift", "--strict", expected=1).stdout
    suite = root / "robot/suites/demo/counter.robot"
    suite.write_text(suite.read_text(encoding="utf-8").replace("Counter Should Be    1", "Counter Should Be    2"),
                     encoding="utf-8")
    command(root, "run", "--tier", "mock", "--outdir", "results/broken", expected=1)


def test_real_ingest_path_with_fake_rvs_client(tmp_path, monkeypatch):
    root = init_project(tmp_path / "widget", "Widget", "WID")
    monkeypatch.setenv(project.PROJECT_ENV, str(root))
    monkeypatch.syspath_prepend(str(root / "src"))
    fixture = json.loads((root / "fixtures/rvs.json").read_text(encoding="utf-8"))
    items = {i["ID"]: i for values in fixture.values() for i in values}
    requested = []

    class FakeRvsClient:
        def __init__(self, server_js):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get_items(self, ids, **opts):
            requested.append(list(ids))
            return [items[int(i)] for i in ids]

    monkeypatch.setattr(ingest, "RvsClient", FakeRvsClient)
    assert main(["ingest"]) == 0
    assert requested == [[1], [2], [3], [4]]
    catalog = ingest.load_catalog(root / "catalog/demo.json")
    assert catalog["specifications"]["1"]["hash"] == ingest.spec_hash(items[1]["Text"])
    assert main(["drift", "--strict"]) == 0
