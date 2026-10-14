import pytest

from grf import project
from grf.cli import _exit_code, main


@pytest.mark.parametrize("arguments,diagnostic", [
    (["--outputdir", "elsewhere"], "managed by GRF"),
    (["-delsewhere"], "managed by GRF"),
    (["--output=NONE"], "managed by GRF"),
    (["--variablefile", "bad.py"], "managed by GRF"),
    (["--metadata", "Tier:bench"], "Tier metadata"),
    (["--metadata=Tier:bench"], "Tier metadata"),
    (["-MTier:bench"], "Tier metadata"),
    (["--met", "Tier:bench"], "Tier metadata"),
    (["--outputd", "elsewhere"], "managed by GRF"),
    (["unexpected.robot"], "suite sources"),
])
def test_run_rejects_overrides_before_execution(widget, capsys, arguments, diagnostic):
    assert main(["run", "--tier", "sim", *arguments]) == 2
    assert diagnostic in capsys.readouterr().err


def test_existing_output_is_preserved(widget, capsys):
    out = widget.root / "results" / "previous"
    out.mkdir(parents=True)
    output = out / "output.xml"
    output.write_text("user-owned output", encoding="utf-8")
    assert main(["run", "--tier", "sim", "--outdir", str(out)]) == 2
    assert "refusing to reuse" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "user-owned output"


@pytest.mark.parametrize("score", ["nan", "inf", "-1", "1.1"])
def test_mutation_score_validation(widget, capsys, score):
    assert main(["mutate", "--min-score", score]) == 2
    assert "finite number between 0 and 1" in capsys.readouterr().err


def test_mutation_requires_non_evidence_tier(widget, capsys):
    widget.tiers.clear()
    widget.tiers["bench"] = project.Tier("bench", frozenset(), evidence=True)
    assert main(["mutate"]) == 2
    assert "non-evidence tier" in capsys.readouterr().err


def test_malformed_cli_configuration_is_diagnostic(widget, capsys):
    widget.data["paths"]["scope"] = []
    assert main(["doctor"]) == 2
    assert "[paths] scope" in capsys.readouterr().err


def test_missing_catalog_is_diagnostic(widget, capsys):
    assert main(["--catalog", str(widget.root / "absent.json"), "drift", "--strict"]) == 2
    assert "absent.json" in capsys.readouterr().err


def test_invalid_operator_environment_is_explicit(widget, monkeypatch, capsys):
    monkeypatch.setenv("WIDGET_OPERATOR", "typo")
    assert main(["doctor"]) == 2
    assert "Operator mode 'typo'" in capsys.readouterr().err


def test_robot_environment_options_cannot_override_tier(widget, monkeypatch, capsys):
    monkeypatch.setenv("ROBOT_OPTIONS", "--metadata Tier:bench")
    assert main(["run", "--tier", "sim"]) == 2
    assert "Tier metadata" in capsys.readouterr().err


def test_dryrun_cannot_become_product_evidence(widget, capsys):
    assert main(["run", "--tier", "bench", "--dryrun"]) == 2
    assert "dry runs require a non-evidence tier" in capsys.readouterr().err


@pytest.mark.parametrize("fail_on", ["new", "any", "none"])
def test_missing_output_never_success(tmp_path, capsys, fail_on):
    assert _exit_code(0, tmp_path / "absent.xml", fail_on) == 2
    assert "produced no output" in capsys.readouterr().err


@pytest.mark.parametrize("rc", [-9, 252, 255])
def test_abnormal_robot_exit_never_masked(tmp_path, rc):
    assert _exit_code(rc, tmp_path / "absent.xml", "none") == rc
