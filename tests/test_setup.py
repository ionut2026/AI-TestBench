import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELL = shutil.which("powershell") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(SHELL is None, reason="Windows PowerShell setup script")


@pytest.fixture
def setup_checkout(tmp_path):
    root = tmp_path / "checkout with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / "setup.ps1", scripts / "setup.ps1")
    (root / "pyproject.toml").write_text("[project]\nname = 'setup-test'\n", encoding="utf-8")
    return root


def run_setup(root, *args):
    return subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-File", str(root / "scripts" / "setup.ps1"), *args],
        cwd=root.parent, capture_output=True, text=True, timeout=30,
    )


def test_missing_python_fails_without_creating_environment(setup_checkout):
    result = run_setup(setup_checkout, "-Python", str(setup_checkout / "missing-python.exe"))
    assert result.returncode == 1
    assert "Python was not found" in result.stderr
    assert "Setup complete" not in result.stdout
    assert not (setup_checkout / ".venv").exists()


def test_missing_python_path_does_not_invoke_command_discovery(setup_checkout):
    script = str(setup_checkout / "scripts" / "setup.ps1").replace("'", "''")
    missing = str(setup_checkout / "missing-python.exe").replace("'", "''")
    wrapper = (
        "function Get-Command { throw 'Unexpected command discovery' };"
        f"& '{script}' -Python '{missing}'"
    )
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", wrapper],
        cwd=setup_checkout.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "Python was not found" in result.stderr
    assert "Unexpected command discovery" not in result.stderr
    assert not (setup_checkout / ".venv").exists()


def test_incomplete_environment_is_preserved(setup_checkout):
    venv = setup_checkout / ".venv"
    venv.mkdir()
    keep = venv / "user-file.txt"
    keep.write_text("preserve", encoding="utf-8")
    result = run_setup(setup_checkout)
    assert result.returncode == 1
    assert "left unchanged" in result.stderr
    assert keep.read_text(encoding="utf-8") == "preserve"
    assert not (venv / "Scripts").exists()


def test_missing_manifest_is_diagnostic(setup_checkout):
    (setup_checkout / "pyproject.toml").unlink()
    result = run_setup(setup_checkout, "-Python", sys.executable)
    assert result.returncode == 1
    assert "complete GeneralRobotFramework checkout" in " ".join(result.stderr.split())
    assert not (setup_checkout / ".venv").exists()


def test_unsupported_python_stops_before_install(setup_checkout):
    script = str(setup_checkout / "scripts" / "setup.ps1").replace("'", "''")
    python = sys.executable.replace("'", "''")
    wrapper = (
        f"function oldpython {{ & '{python}' -c 'import sys; sys.exit(''Python 3.11 or newer is required.'')' }};"
        f"& '{script}' -Python oldpython"
    )
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", wrapper],
        cwd=setup_checkout.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "Python 3.11 or newer is required" in " ".join(result.stderr.split())
    assert "Setup complete" not in result.stdout
    assert not (setup_checkout / ".venv").exists()


def test_install_command_failure_never_reports_success(setup_checkout):
    venv = setup_checkout / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=30)
    result = run_setup(setup_checkout)
    assert result.returncode == 1
    assert "Python command failed" in result.stderr
    assert "pip" in result.stderr
    assert "Setup complete" not in result.stdout
    assert (venv / "Scripts" / "python.exe").is_file()


def test_script_restores_missing_and_existing_environment_variables(setup_checkout):
    script = str(setup_checkout / "scripts" / "setup.ps1").replace("'", "''")
    wrapper = f"""
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw 'Script parse errors' }}
$outer = $ast.EndBlock.Statements | Where-Object {{ $_ -is [Management.Automation.Language.TryStatementAst] }}
$body = $outer.Finally.Extent.Text
$savedEnvironment = @{{ PIP_CACHE_DIR = 'existing-cache'; TEMP = 'existing-temp'; TMP = $null }}
$env:PIP_CACHE_DIR = 'changed-cache'
$env:TEMP = 'changed-temp'
$env:TMP = 'changed-tmp'
& ([scriptblock]::Create($body.Substring(1, $body.Length - 2)))
if ($env:PIP_CACHE_DIR -ne 'existing-cache' -or $env:TEMP -ne 'existing-temp' -or (Test-Path Env:TMP)) {{
    throw 'Environment was not restored'
}}
"""
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", wrapper],
        cwd=setup_checkout.parent, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
