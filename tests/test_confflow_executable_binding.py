"""Regression coverage for the server-pinned ConfFlow executable boundary."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jobdesk_app.config.schema import ServerConfig
from jobdesk_app.core.confflow_executable import (
    build_executable_identity_guard,
    build_executable_identity_probe,
    parse_executable_identity_probe,
)
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind, build_run_plan
from jobdesk_app.remote.confflow_probe import build_confflow_capability_command
from jobdesk_app.services.run_coordinator import RunCoordinator
from jobdesk_app.services.run_service import RunService


def _workflow_spec() -> RunSpec:
    return RunSpec(
        server_id="wsl",
        remote_dir="/remote/project",
        command_template="cd {dir} && confflow {name} -c workflow.yaml",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/water.xyz")],
        workflow_kind=WorkflowKind.confflow,
    )


def test_capability_command_quotes_explicit_executable() -> None:
    assert build_confflow_capability_command("/opt/Conf Flow/bin/confflow") == (
        "'/opt/Conf Flow/bin/confflow' --capabilities --json"
    )
    assert build_confflow_capability_command() == "confflow --capabilities --json"


def test_run_plan_binds_configured_executable_to_every_workflow_task() -> None:
    spec = replace(_workflow_spec(), confflow_executable="/opt/Conf Flow/bin/confflow")

    plan = build_run_plan(spec, run_id="run-1")

    assert plan.tasks[0].confflow_executable == "/opt/Conf Flow/bin/confflow"
    assert "'/opt/Conf Flow/bin/confflow' water.xyz" in plan.tasks[0].command
    assert "confflow water.xyz" not in plan.tasks[0].command


def test_coordinator_rejects_unadmitted_workflow_creation(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    server = ServerConfig(
        server_id="wsl",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda _server_id: server,
        ssh_factory=lambda _config: None,
        sftp_factory=lambda _ssh: None,
    )

    outcome = coordinator.create_run(_workflow_spec(), run_id="run-1", local_dir=str(tmp_path))

    assert outcome.errors[0].code == "configuration_admission_required"
    with pytest.raises(KeyError):
        service.load_run("run-1")


def test_identity_probe_and_runner_guard_bind_path_digest_and_stat_snapshot() -> None:
    path = "/opt/confflow-1.4.6-prod-venv/bin/confflow"
    python_executable = "/opt/confflow-1.4.6-prod-venv/bin/python3.12"
    identity = parse_executable_identity_probe(
        f"{path}\n123|456|7|8\n" + "a" * 64 + "\n",
        path=path,
        python_executable=python_executable,
    )

    probe = build_executable_identity_probe(path, python_executable)
    guard = "\n".join(build_executable_identity_guard(identity, "water"))

    assert "readlink -f" in probe
    assert "sha256sum" in probe
    assert python_executable in probe
    assert "123|456|7|8" in guard
    assert "a" * 64 in guard
    assert "exit 126" in guard


@pytest.mark.parametrize("stdout", ["", "/opt/confflow\n1|2|3\n" + "a" * 64, "/opt/confflow\n1|2|3|4\nnot-a-digest\n"])
def test_identity_probe_rejects_malformed_remote_data(stdout: str) -> None:
    with pytest.raises(ValueError, match="identity"):
        parse_executable_identity_probe(
            stdout,
            path="/opt/confflow",
            python_executable="/opt/confflow-python",
        )
