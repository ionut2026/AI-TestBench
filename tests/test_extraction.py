import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/prepare_split.py"
SPEC = importlib.util.spec_from_file_location("prepare_split", PATH)
assert SPEC and SPEC.loader
extraction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extraction)


def test_product_mapping_excludes_exports_and_core():
    tree = {"smm-automation/src/library.py": "a", "smm-automation/README.md": "b",
            "docs/SMM-AUTOMATION-HANDBOOK.md": "c", "exports/pra-tested.md": "d",
            "general-robot-framework/src/grf/project.py": "e", "windchill-mcp-server/src/server.js": "f"}
    assert extraction.selected_files(tree, extraction.LAYOUTS["SMMAutomationFramework"]) == {
        "src/library.py": "a", "README.md": "b", "docs/SMM-AUTOMATION-HANDBOOK.md": "c",
    }
    assert extraction.selected_files(tree, extraction.LAYOUTS["WindchillMcpServer"]) == {"src/server.js": "f"}
    assert extraction.selected_files(tree, extraction.LAYOUTS["GeneralRobotFramework"]) == {"src/grf/project.py": "e"}


def test_collision_is_not_silently_overwritten():
    with pytest.raises(ValueError, match="collide"):
        extraction.selected_files({"a/x": "a", "b/x": "b"}, {"a/": "", "b/": ""})


def test_post_cutover_source_is_rejected_before_creating_output(tmp_path, monkeypatch):
    source = tmp_path / "core"
    source.mkdir()
    destination = source / "general-robot-framework" / "results" / "split"
    def fake_git(_source, *args):
        if args == ("rev-parse", "--show-toplevel"):
            return str(source)
        return "main" if args == ("branch", "--show-current") else "abc"

    monkeypatch.setattr(extraction, "git", fake_git)
    monkeypatch.setattr(extraction, "tree", lambda _: {"src/grf/project.py": "a"})
    with pytest.raises(ValueError, match="pre-cutover monorepo"):
        extraction.prepare(source, destination)
    assert not destination.exists()
