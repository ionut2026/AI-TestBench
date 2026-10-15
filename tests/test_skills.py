import re
from pathlib import Path

from grf.scaffold import SKILLS, TEMPLATES, init_project

ROOT = Path(__file__).resolve().parents[1]
SAFETY = ("product evidence", "read-only", "authorization")


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path} has no frontmatter"
    fields = dict(line.split(": ", 1) for line in match.group(1).splitlines())
    assert set(fields) == {"name", "description"}
    return fields


def test_starter_skills_are_registered():
    for name in SKILLS:
        relative = f".github/skills/grf-{name}/SKILL.md"
        assert TEMPLATES[f"skill-{name}"] == relative


def test_skills_have_valid_frontmatter_and_safety_rules():
    paths = sorted((ROOT / ".github/skills").glob("*/SKILL.md"))
    assert {path.parent.name for path in paths} == {f"grf-{name}" for name in SKILLS} | {"grf-validate-change"}
    for path in paths:
        fields = frontmatter(path)
        assert fields["name"] == path.parent.name
        assert len(fields["description"]) > 40
        text = path.read_text(encoding="utf-8").lower()
        assert all(term in text for term in SAFETY), path


def test_repository_shared_skills_match_starter_skills(tmp_path):
    generated = init_project(tmp_path / "starter", "Skills", "SPEC")
    for name in SKILLS:
        relative = f".github/skills/grf-{name}/SKILL.md"
        assert (ROOT / relative).read_text(encoding="utf-8") == (generated / relative).read_text(encoding="utf-8")


def test_agents_reference_existing_skills(tmp_path):
    generated = init_project(tmp_path / "starter", "Skills", "SPEC")
    for agent in (generated / ".github/agents").glob("*.agent.md"):
        for skill in re.findall(r"\bgrf-[a-z-]+\b", agent.read_text(encoding="utf-8")):
            assert (generated / ".github/skills" / skill / "SKILL.md").is_file(), (agent, skill)


def test_coordinator_is_shared_generic_and_uses_supported_frontmatter(tmp_path):
    generated = init_project(tmp_path / "starter", "Skills", "SPEC")
    relative = Path(".github/agents/integration-coordinator.agent.md")
    source = ROOT / relative
    assert frontmatter(source) == {
        "name": "integration-coordinator",
        "description": (
            "Coordinates explicitly requested framework and consumer changes with clear ownership, "
            "user-selected Git workflow, exact contracts and evidence-based handoffs."
        ),
    }
    text = source.read_text(encoding="utf-8")
    assert text == (ROOT / "src/grf/templates/coordinator.tpl").read_text(encoding="utf-8")
    assert text == (generated / relative).read_text(encoding="utf-8")
    assert text == (ROOT / "examples/demo" / relative).read_text(encoding="utf-8")
    assert {path.name for path in (ROOT / ".github/agents").glob("*.agent.md")} == {relative.name}
    skill = (ROOT / ".github/skills/grf-coordinate-integration/SKILL.md").read_text(encoding="utf-8")
    for content in (text, skill):
        assert len(content) < 30_000
        assert all(term not in content.lower() for term in ("smm", "d:\\", "c:\\", "ionut2026"))
        assert "grf-coordinate-integration" in content
        assert "human approval" in content
        assert "authorization" in content
        assert "read-only" in content
    assert "model:" not in text and "tools:" not in text and "mcp-servers:" not in text


def test_integration_procedure_preserves_contract_and_handoff_boundaries():
    text = (ROOT / ".github/skills/grf-coordinate-integration/SKILL.md").read_text(encoding="utf-8")
    for clause in (
        "Discover the current client's available tools and their schemas",
        "Without them, report the limitation",
        "Do not involve unrelated projects",
        "Before implementation, exchange an exact contract",
        "Consumers depend on framework",
        "Preserve direct main checkouts",
        "immutable commit",
        "verify the imported",
        "framework and consumer SHAs",
        "PR links",
        "revalidate the consumer",
        "previous PR/deployment approval is not blanket permission",
        "Do not merge/tag automatically or bypass branch protections",
        "do not spawn workers, grant permission, authenticate approval",
        "Do not change models, account settings, OS settings, credentials or permissions",
        "Honor the user-selected workflow",
        "For direct-main work, use one writer per repository",
        "Do not switch or clean up user-owned dirty main",
        "Discover conflicts, report them and ask before proceeding",
        "Do not create branches/PRs against the user's choice",
        "if protections prevent direct-main commits",
    ):
        assert clause in text


def test_repository_instructions_preserve_assigned_worktree_and_authorization():
    text = " ".join((ROOT / ".github/copilot-instructions.md").read_text(encoding="utf-8").split())
    for clause in (
        "assigned GRF checkout",
        "Preserve the direct",
        "uncommitted user work",
        "isolated",
        "direct-main commits or isolated branches/PRs, only for affected repositories",
        "one writer per repository",
        "do not switch or clean up user-owned dirty main",
        "ask on conflicts",
        "explicit authorization matching the repository, change and action",
        "previous deployment or PR is not permission",
        "never force-push, bypass review",
        "do not spawn workers, reconnect sessions, grant permission",
    ):
        assert clause in text
    assert "Use this direct checkout on `main`" not in text
