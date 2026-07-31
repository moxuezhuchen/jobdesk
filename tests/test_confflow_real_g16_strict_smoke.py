"""Pure strict-decision tests for the real-g16 smoke harnesses."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import confflow_real_g16_smoke_common as smoke

from jobdesk_app.core.confflow_contract import (
    EXPECTED_ARTIFACTS,
    REQUIRED_COMMANDS,
    RUN_SUMMARY_SCHEMA,
    WORKFLOW_STATE_SCHEMA,
    WORKFLOW_STATS_SCHEMA,
)


def _capability() -> dict[str, object]:
    build = {"commit": smoke.REFERENCE_BUILD_COMMIT, "dirty": False}
    return {
        "schema_version": smoke.CAPABILITY_SCHEMA_VERSION,
        "version": smoke.REFERENCE_VERSION,
        "capabilities": {"workflow_state": True, "resume": True, "dag": True},
        "artifacts": {
            "run_summary": EXPECTED_ARTIFACTS.run_summary,
            "workflow_stats": EXPECTED_ARTIFACTS.workflow_stats,
            "workflow_state": EXPECTED_ARTIFACTS.workflow_state,
            "output_manifest": EXPECTED_ARTIFACTS.output_manifest,
            "run_report": EXPECTED_ARTIFACTS.run_report,
            "min_xyz": EXPECTED_ARTIFACTS.min_xyz,
        },
        "commands": {name: True for name in REQUIRED_COMMANDS},
        "build": build,
        "producer": {
            "package": "confflow",
            "version": smoke.REFERENCE_VERSION,
            "build": dict(build),
            "wheel": {
                "filename": smoke.REFERENCE_WHEEL_FILENAME,
                "sha256": smoke.REFERENCE_WHEEL_SHA256,
            },
            "install_provenance": {"status": "verified"},
        },
        "install_provenance": {"status": "verified"},
        "executable": {
            "path": smoke.CONFFLOW_EXE,
            "sha256": "a" * 64,
            "python": smoke.CONFFLOW_PYTHON,
        },
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _identity() -> dict[str, dict[str, object]]:
    return {
        smoke.G16_PATH: {
            "path": smoke.G16_PATH,
            "realpath": smoke.G16_PATH,
            "size": 10,
            "mtime_ns": 20,
            "device": 30,
            "inode": 40,
            "sha256": "b" * 64,
        },
        smoke.L1_PATH: {
            "path": smoke.L1_PATH,
            "realpath": smoke.L1_PATH,
            "size": 11,
            "mtime_ns": 21,
            "device": 31,
            "inode": 41,
            "sha256": "c" * 64,
        },
    }


def _write_step(work_dir: Path, name: str) -> None:
    step_dir = work_dir / name
    step_dir.mkdir(parents=True, exist_ok=True)
    (step_dir / "output.xyz").write_text("C 0 0 0\n", encoding="utf-8")
    _write_json(
        step_dir / "manifest.json",
        {
            "schema_version": 1,
            "step_name": name,
            "step_type": "calc",
            "status": "completed",
            "config_digest": "sha256:config",
            "input_digest": "sha256:input",
            "output": "output.xyz",
            "failed": None,
            "total_tasks": 1,
            "succeeded": 1,
            "failed_count": 0,
            "error": None,
        },
    )


def _write_workflow(result_root: Path, *, checkpoint: bool = False) -> None:
    names = (
        ("step_06_g16_opt", "step_07_g16_sp_readchk")
        if checkpoint
        else ("g16_opt",)
    )
    work_dir = result_root / "methane_confflow_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        _write_step(work_dir, name)

    state_steps = {
        name: {"name": name, "type": "calc", "status": "completed", "error": None}
        for name in names
    }
    rows = [{"name": name, "type": "calc", "status": "completed"} for name in names]
    _write_json(
        work_dir / "run_summary.json",
        {
            "content_schema": RUN_SUMMARY_SCHEMA,
            "step_status_counts": {"completed": len(names)},
            "steps": rows,
        },
    )
    _write_json(
        work_dir / "workflow_stats.json",
        {"content_schema": WORKFLOW_STATS_SCHEMA, "steps": rows},
    )
    _write_json(
        work_dir / ".workflow_state.json",
        {
            "content_schema": WORKFLOW_STATE_SCHEMA,
            "final_status": "completed",
            "steps": state_steps,
        },
    )
    _write_json(
        work_dir / "output_manifest.json",
        {
            "content_schema": "confflow.output_manifest.v1",
            "terminals": {name: [f"{name}/output.xyz"] for name in names},
        },
    )
    _write_json(result_root / "capability.json", _capability())
    (result_root / "preflight_4line.txt").write_text("2\n0\n0\n/opt/g16/bsd/g16.profile\n", encoding="utf-8")
    _write_json(result_root / "g16_identity_before.json", _identity())
    _write_json(result_root / "g16_identity_after.json", _identity())

    if checkpoint:
        backup06 = work_dir / "step_06_g16_opt" / "backups"
        backup07 = work_dir / "step_07_g16_sp_readchk" / "backups"
        backup06.mkdir(parents=True, exist_ok=True)
        backup07.mkdir(parents=True, exist_ok=True)
        (backup06 / "A000001.chk").write_bytes(b"checkpoint")
        (backup07 / "A000001.old.chk").write_bytes(b"checkpoint")
        (backup07 / "A000001.gjf").write_text(
            "%Chk=A000001.chk\n%OldChk=A000001.old.chk\n", encoding="utf-8"
        )
        (backup06 / "A000001.log").write_text(
            "Optimization completed\nNormal termination of Gaussian 16\n", encoding="utf-8"
        )
        (backup07 / "A000001.log").write_text(
            "%OldChk=A000001.old.chk\n"
            'Copying data from "A000001.old.chk" to current chk file "A000001.chk"\n'
            'Structure from the checkpoint file: "A000001.chk"\n'
            'Initial guess from the checkpoint file: "A000001.chk"\n'
            "Normal termination of Gaussian 16\n",
            encoding="utf-8",
        )
    else:
        backup = work_dir / "g16_opt" / "backups"
        backup.mkdir(parents=True, exist_ok=True)
        (backup / "A000001.log").write_text(
            "Normal termination of Gaussian 16\n", encoding="utf-8"
        )


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        ("dirty build", lambda payload: payload["build"].update(dirty=True)),
        (
            "unverified install",
            lambda payload: payload["install_provenance"].update(status="unverified"),
        ),
        (
            "wrong executable",
            lambda payload: payload["executable"].update(path="/opt/confflow-1.4.4-prod-venv/bin/confflow"),
        ),
        (
            "wrong wheel digest",
            lambda payload: payload["producer"]["wheel"].update(sha256="d" * 64),
        ),
    ],
)
def test_capability_rejects_unapproved_identity(label: str, mutate) -> None:
    payload = _capability()
    mutate(payload)
    with pytest.raises(smoke.SmokeValidationError, match="capability"):
        smoke.validate_capability_payload(json.dumps(payload))


def test_preflight_rejects_wrong_fourth_line() -> None:
    with pytest.raises(smoke.SmokeValidationError, match="preflight"):
        smoke.validate_preflight_lines("2\n0\n0\n/opt/g16/bsd/other.profile\n")


def _run_capability_probe_script(tmp_path: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    capability_path = tmp_path / "capability.json"
    _write_json(capability_path, payload)
    return subprocess.run(
        [sys.executable, "-c", smoke.capability_probe_script(), str(capability_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_capability_probe_accepts_live_shape_without_realpath(tmp_path: Path) -> None:
    result = _run_capability_probe_script(tmp_path, _capability())
    assert result.returncode == 0, result.stderr


def test_capability_probe_accepts_correct_optional_realpath(tmp_path: Path) -> None:
    payload = _capability()
    payload["executable"]["realpath"] = smoke.CONFFLOW_EXE  # type: ignore[index]
    result = _run_capability_probe_script(tmp_path, payload)
    assert result.returncode == 0, result.stderr


def test_capability_probe_rejects_wrong_optional_realpath(tmp_path: Path) -> None:
    payload = _capability()
    payload["executable"]["realpath"] = "/opt/confflow-wrong/bin/confflow"  # type: ignore[index]
    result = _run_capability_probe_script(tmp_path, payload)
    assert result.returncode != 0


def test_harness_label_with_spaces_is_shell_literal_under_nounset() -> None:
    """A human label must not become a shell variable during template expansion."""

    harness = smoke.build_inner_harness(workflow_yaml="steps: []", label="methane opt")
    label_line = next(line for line in harness.splitlines() if "starting ConfFlow" in line)
    assert "$methane" not in label_line
    assert label_line == "printf '[smoke] starting ConfFlow (%s)\\n' 'methane opt'"

    syntax = subprocess.run(
        [smoke.WIN_WSL, "-d", smoke.WSL_DISTRO, "--", "bash", "-n"],
        input=harness.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    wsl_output = (syntax.stdout + syntax.stderr).decode("utf-8", errors="replace").replace("\x00", "")
    if "E_ACCESSDENIED" in wsl_output:
        pytest.skip("WSL Bash is unavailable in this test environment")
    assert syntax.returncode == 0, wsl_output

    result = subprocess.run(
        [smoke.WIN_WSL, "-d", smoke.WSL_DISTRO, "--", "bash", "-u", "-s"],
        input=label_line.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert result.stdout.decode("utf-8", errors="replace") == "[smoke] starting ConfFlow (methane opt)\n"


def test_nonzero_rc_two_is_always_rejected() -> None:
    with pytest.raises(smoke.SmokeValidationError, match="2"):
        smoke.validate_process_returncode(2, stage="ConfFlow")


def test_cleanup_rc_two_with_empty_output_reports_full_diagnostics(monkeypatch) -> None:
    target = "/tmp/jobdesk-confflow-g16.ABC123"
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 2, "", ""),
    )

    with pytest.raises(smoke.SmokeValidationError) as error:
        smoke.cleanup_remote(target)

    message = str(error.value)
    assert "rc=2" in message
    assert f"target={target!r}" in message
    assert "stdout=''" in message
    assert "stderr=''" in message


def test_cleanup_rm_failure_reports_stderr(monkeypatch) -> None:
    target = "/tmp/jobdesk-confflow-g16.ABC123"
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, "", "rm: cannot remove target"
        ),
    )

    with pytest.raises(smoke.SmokeValidationError, match="cannot remove target") as error:
        smoke.cleanup_remote(target)

    assert "rc=1" in str(error.value)
    assert f"target={target!r}" in str(error.value)


def test_cleanup_success_script_verifies_delete_postcondition(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    def run(args, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(smoke.subprocess, "run", run)
    smoke.cleanup_remote("/tmp/jobdesk-confflow-g16.ABC123")

    assert len(calls) == 1
    script = calls[0][-1]
    assert isinstance(script, str)
    assert 'test ! -e "$p"' in script


def test_cleanup_rejects_unsafe_path_without_subprocess(monkeypatch) -> None:
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unsafe cleanup must not spawn a subprocess")

    monkeypatch.setattr(smoke.subprocess, "run", run)
    with pytest.raises(smoke.SmokeValidationError, match="unsafe remote cleanup"):
        smoke.cleanup_remote("/tmp/not-harness-owned")
    assert not called


def test_failed_workflow_state_is_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path)
    state_path = tmp_path / "methane_confflow_work" / ".workflow_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["final_status"] = "failed"
    _write_json(state_path, state)
    with pytest.raises(smoke.SmokeValidationError, match="final_status"):
        smoke.validate_smoke_bundle(tmp_path, step_names=("g16_opt",))


def test_missing_output_manifest_is_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path)
    (tmp_path / "methane_confflow_work" / "output_manifest.json").unlink()
    with pytest.raises(smoke.SmokeValidationError, match="output manifest"):
        smoke.validate_smoke_bundle(tmp_path, step_names=("g16_opt",))


def test_only_one_checkpoint_log_normal_termination_is_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path, checkpoint=True)
    log07 = tmp_path / "methane_confflow_work" / "step_07_g16_sp_readchk" / "backups" / "A000001.log"
    log07.write_text("%OldChk=A000001.old.chk\n", encoding="utf-8")
    with pytest.raises(smoke.SmokeValidationError, match="normal termination"):
        smoke.validate_smoke_bundle(
            tmp_path,
            step_names=("step_06_g16_opt", "step_07_g16_sp_readchk"),
            checkpoint=True,
        )


def test_missing_checkpoint_is_rejected(tmp_path: Path) -> None:
    _write_workflow(tmp_path, checkpoint=True)
    (tmp_path / "methane_confflow_work" / "step_06_g16_opt" / "backups" / "A000001.chk").unlink()
    with pytest.raises(smoke.SmokeValidationError, match="checkpoint"):
        smoke.validate_smoke_bundle(
            tmp_path,
            step_names=("step_06_g16_opt", "step_07_g16_sp_readchk"),
            checkpoint=True,
        )


def test_two_step_complete_fake_output_passes(tmp_path: Path) -> None:
    _write_workflow(tmp_path, checkpoint=True)
    smoke.validate_smoke_bundle(
        tmp_path,
        step_names=("step_06_g16_opt", "step_07_g16_sp_readchk"),
        checkpoint=True,
    )


def test_opt_script_main_rejects_rc_two_without_starting_g16(monkeypatch, capsys) -> None:
    from scripts import smoke_confflow_real_g16_wsl as script

    monkeypatch.setattr(script, "deploy_harness", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        script,
        "run_inner",
        lambda _destination: subprocess.CompletedProcess(
            args=["fake-wsl"],
            returncode=2,
            stdout="[smoke] RESULT_DIR=/tmp/jobdesk-confflow-g16.ABC123\n",
            stderr="fake output parse failure",
        ),
    )
    monkeypatch.setattr(script, "pull_artifacts", lambda *args, **kwargs: None)
    assert script.main() != 0
    assert "non-zero rc=2" in capsys.readouterr().err


def test_checkpoint_main_never_accepts_one_combined_normal_marker(monkeypatch, capsys) -> None:
    from scripts import smoke_confflow_real_g16_chk_wsl as script

    monkeypatch.setattr(script, "deploy_harness", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        script,
        "run_inner",
        lambda _destination: subprocess.CompletedProcess(
            args=["fake-wsl"],
            returncode=2,
            stdout=(
                "[smoke] RESULT_DIR=/tmp/jobdesk-confflow-g16.ABC123\n"
                "Normal termination of Gaussian 16\n"
            ),
            stderr="",
        ),
    )
    monkeypatch.setattr(script, "pull_artifacts", lambda *args, **kwargs: None)
    assert script.main() != 0
    assert "non-zero rc=2" in capsys.readouterr().err
