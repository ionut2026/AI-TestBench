"""One-time root-layout migration for the history-filtered SMM checkout."""

import argparse
from pathlib import Path


def rewire(root: Path) -> None:
    files = [root / "README.md", root / "docs/SMM-AUTOMATION-HANDBOOK.md",
             *sorted((root / ".github/agents").glob("*.md"))]
    for path in files:
        text = path.read_text(encoding="utf-8")
        text = text.replace("../docs/SMM-AUTOMATION-HANDBOOK.md", "docs/SMM-AUTOMATION-HANDBOOK.md")
        text = text.replace(r"D:\projects\AI-TestBench\smm-automation", r"D:\projects\SMMAutomationFramework")
        text = text.replace(r"D:\projects\AI-TestBench", r"D:\projects\SMMAutomationFramework")
        text = text.replace("smm-automation/", "").replace("smm-automation\\", "")
        text = text.replace("..\\general-robot-framework", "..\\GeneralRobotFramework")
        text = text.replace("../general-robot-framework/src/grf/",
                            "https://github.com/ionut2026/GeneralRobotFramework/tree/v0.1.0/src/grf/")
        path.write_text(text, encoding="utf-8", newline="\n")

    workflow = root / ".github/workflows/smm-automation.yml"
    text = workflow.read_text(encoding="utf-8")
    text = text.replace('    paths: ["smm-automation/**", "general-robot-framework/**", ".github/workflows/smm-automation.yml"]\n', "")
    start = text.index("      - name: Generic framework checks")
    end = text.index("      - name: Service typecheck", start)
    text = text[:start] + text[end:]
    text = text.replace("python -m pip install --no-deps -e general-robot-framework\n", "")
    # Preserve indentation of the next line after removing the old core install.
    text = text.replace("                    python -m pip", "          python -m pip")
    text = text.replace("          .venv/Scripts/pip install --no-deps -e general-robot-framework\n", "")
    text = text.replace("smm-automation/", "").replace("working-directory: smm-automation\n", "working-directory: .\n")
    text = text.replace("-e smm-automation", "-e .")
    text = text.replace("python -m pip install --no-deps -e .", "python -m pip install -e .")
    text = text.replace(".venv/Scripts/pip install --no-deps -e .", ".venv/Scripts/pip install -e .")
    text = text.replace("../.venv/Scripts/", ".venv/Scripts/")
    workflow.write_text(text, encoding="utf-8", newline="\n")

    config = root / "pyproject.toml"
    text = config.read_text(encoding="utf-8").replace(
        '"general-robot-framework==0.1.0"',
        '"general-robot-framework @ git+https://github.com/ionut2026/GeneralRobotFramework.git@v0.1.0"')
    config.write_text(text, encoding="utf-8", newline="\n")
    (root / ".github/CODEOWNERS").write_text(
        "/robot/suites/ @ionut2026\n/catalog/reviews.toml @ionut2026\n/catalog/*.scope.toml @ionut2026\n",
        encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    rewire(args.root.resolve())
