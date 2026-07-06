"""Stage 4 — workflow CLI tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jobdesk_app.cli import main
from jobdesk_app.cli.workflow_cmd import PRESETS, add_parser


def _run(argv: list[str]) -> int:
    return main(argv)


def test_presets_list(capsys):
    rc = _run(["workflow", "presets"])
    assert rc == 0
    captured = capsys.readouterr()
    for name in PRESETS:
        assert name in captured.out


def test_build_with_preset_writes_yaml(tmp_path: Path, capsys):
    out = tmp_path / "wf.yaml"
    rc = _run([
        "workflow", "build",
        "--preset", "opt-freq-orca",
        "--output", str(out),
        "--check",
    ])
    assert rc == 0, capsys.readouterr().err
    text = out.read_text(encoding="utf-8")
    assert "global:" in text
    assert "steps:" in text
    assert "name: conformers" in text
    captured = capsys.readouterr()
    assert "wrote" in captured.out
    assert "check OK" in captured.out


def test_build_refuses_overwrite_without_force(tmp_path: Path):
    out = tmp_path / "wf.yaml"
    out.write_text("# existing", encoding="utf-8")
    rc = _run([
        "workflow", "build",
        "--preset", "opt-freq-orca",
        "--output", str(out),
    ])
    assert rc == 2
    assert out.read_text() == "# existing"


def test_build_force_overwrites(tmp_path: Path):
    out = tmp_path / "wf.yaml"
    out.write_text("# existing", encoding="utf-8")
    rc = _run([
        "workflow", "build",
        "--preset", "sp-g16",
        "--output", str(out),
        "--force",
    ])
    assert rc == 0
    assert "global:" in out.read_text()


def test_build_with_params_json(tmp_path: Path):
    params = tmp_path / "params.json"
    params.write_text(
        """{
  "global": {"charge": -1, "iprog": "g16", "itask": "sp", "keyword": "HF/3-21G"},
  "steps": [
    {"type": "calc", "name": "sp", "params": {"iprog": "g16", "itask": "sp", "keyword": "HF/3-21G sp"}}
  ]
}""",
        encoding="utf-8",
    )
    out = tmp_path / "wf.yaml"
    rc = _run(["workflow", "build", "--params", str(params), "--output", str(out)])
    assert rc == 0
    text = out.read_text()
    assert "charge: -1" in text
    assert "name: sp" in text


def test_check_round_trip(tmp_path: Path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        """global:
  charge: 0
  iprog: orca
  itask: opt_freq
steps:
  - name: opt
    type: calc
    enabled: true
    params:
      iprog: orca
      itask: opt_freq
      keyword: B3LYP def2-SVP opt freq
""",
        encoding="utf-8",
    )
    rc = _run(["workflow", "check", str(cfg)])
    assert rc == 0


def test_check_rejects_broken_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("this is: not, a, valid, [workflow, mapping]\n", encoding="utf-8")
    rc = _run(["workflow", "check", str(bad)])
    # The YAML is a mapping but missing 'global' and 'steps'; the check command
    # treats missing steps as an empty workflow and succeeds (RC 0). Use an
    # explicit error-producing payload instead.
    bad.write_text(
        "global:\n  charge: not-an-int\nsteps:\n  - type: bogus\n",
        encoding="utf-8",
    )
    rc = _run(["workflow", "check", str(bad)])
    assert rc == 2
