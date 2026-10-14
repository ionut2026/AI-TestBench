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
