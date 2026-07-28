"""Regression coverage for the server-pinned ConfFlow executable boundary."""

from __future__ import annotations

from dataclasses import replace

from jobdesk_app.config.schema import ServerConfig
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


def test_coordinator_persists_server_configured_executable_on_task(tmp_path) -> None:
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

    assert outcome.errors == []
    task = service.repository.load_tasks("run-1")[0]
    assert task.confflow_executable == "/opt/confflow/bin/confflow"
    assert "/opt/confflow/bin/confflow water.xyz" in task.rendered_command
