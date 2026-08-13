"""SSH/SFTP adapter for ConfFlow's one-shot control protocol."""

from __future__ import annotations

import json
import posixpath
import shlex
import tempfile
from collections.abc import Iterable
from pathlib import Path

from jobdesk_app.remote.confflow_probe import build_confflow_preflight_shell, quote_confflow_executable
from jobdesk_app.remote.scheduler import PBSAdapter, ResourceSpec, SlurmAdapter
from jobdesk_app.services.confflow_control import (
    ControlArtifactManifest,
    ControlEventPage,
    ControlProtocolError,
    ControlSnapshot,
    ControlTransport,
    ControlUnsupported,
    parse_artifacts_response,
    parse_capabilities,
    parse_events_response,
    parse_snapshot_response,
)


class SSHControlTransport(ControlTransport):
    """Run one isolated control command per operation over an existing lease."""

    def __init__(
        self,
        ssh,
        sftp,
        *,
        executable: str | None,
        state_root: str,
        env_init_scripts: Iterable[str] = (),
    ) -> None:
        self._ssh = ssh
        self._sftp = sftp
        self._executable = executable or "confflow"
        self.state_root = _absolute_remote_path(state_root, "state_root")
        self._env_init_scripts = tuple(env_init_scripts)

    def capabilities(self) -> bool:
        result = self._run("capabilities")
        if result.exit_code != 0 and _looks_like_unsupported_capability(result.stderr, result.stdout):
            raise ControlUnsupported()
        return parse_capabilities(result.stdout, exit_code=result.exit_code, stderr=result.stderr)

    def prepare(self, request: dict[str, object]) -> ControlSnapshot:
        run_id = _safe_request_component(_required_string(request, "run_id"), "run_id")
        key = _safe_request_component(_required_string(request, "idempotency_key"), "idempotency_key")
        request_path = posixpath.join(self.state_root, "jobdesk-requests", f"{run_id}-{key}.json")
        self._sftp.mkdir_p(posixpath.dirname(request_path))
        with tempfile.TemporaryDirectory(prefix="jobdesk-control-") as temp_dir:
            local_path = Path(temp_dir) / "request.json"
            local_path.write_text(json.dumps(request, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self._sftp.upload_file(local_path, request_path, overwrite=True)
            try:
                result = self._run(
                    "prepare",
                    f"--state-root {shlex.quote(self.state_root)} --request {shlex.quote(request_path)}",
                )
            finally:
                try:
                    self._sftp.remove_file(request_path)
                except Exception:
                    pass
        return parse_snapshot_response(
            "prepare",
            result.stdout,
            exit_code=result.exit_code,
            stderr=result.stderr,
            expected_run_id=run_id,
        )

    def execute(self, run_id: str) -> ControlSnapshot:
        return self._snapshot("execute", run_id)

    def status(self, run_id: str) -> ControlSnapshot:
        return self._snapshot("status", run_id)

    def events(self, run_id: str, *, after: str | None) -> ControlEventPage:
        suffix = "" if after is None else f" --after {shlex.quote(after)}"
        result = self._run(
            "events",
            f"--state-root {shlex.quote(self.state_root)} --run-id {shlex.quote(run_id)}{suffix}",
        )
        return parse_events_response(
            "events",
            result.stdout,
            exit_code=result.exit_code,
            stderr=result.stderr,
            expected_run_id=run_id,
        )

    def cancel(self, run_id: str) -> ControlSnapshot:
        return self._snapshot("cancel", run_id)

    def resume(self, run_id: str, *, checkpoint: str | None) -> ControlSnapshot:
        suffix = "" if checkpoint is None else f" --checkpoint {shlex.quote(checkpoint)}"
        result = self._run(
            "resume",
            f"--state-root {shlex.quote(self.state_root)} --run-id {shlex.quote(run_id)}{suffix}",
        )
        return parse_snapshot_response(
            "resume",
            result.stdout,
            exit_code=result.exit_code,
            stderr=result.stderr,
            expected_run_id=run_id,
        )

    def artifacts(self, run_id: str) -> ControlArtifactManifest:
        result = self._run(
            "artifacts",
            f"--state-root {shlex.quote(self.state_root)} --run-id {shlex.quote(run_id)}",
        )
        return parse_artifacts_response(
            "artifacts",
            result.stdout,
            exit_code=result.exit_code,
            stderr=result.stderr,
            expected_run_id=run_id,
        )

    def _snapshot(self, operation: str, run_id: str) -> ControlSnapshot:
        result = self._run(
            operation,
            f"--state-root {shlex.quote(self.state_root)} --run-id {shlex.quote(run_id)}",
        )
        return parse_snapshot_response(
            operation,
            result.stdout,
            exit_code=result.exit_code,
            stderr=result.stderr,
            expected_run_id=run_id,
        )

    def _run(self, operation: str, options: str = ""):
        command = f"{quote_confflow_executable(self._executable)} control {operation} {options} --json".strip()
        return self._ssh.run(build_confflow_preflight_shell(command, self._env_init_scripts), timeout=120)


def resolve_control_state_root(ssh, *, env_init_scripts: Iterable[str] = ()) -> str:
    """Resolve and validate the producer's default control state locator."""
    command = build_confflow_preflight_shell("printf '%s\\n' \"$HOME\"", env_init_scripts)
    result = ssh.run(command, timeout=30)
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.exit_code}"
        raise ControlProtocolError("capabilities", "internal", f"cannot resolve producer HOME: {detail}", retryable=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith("/"):
        raise ControlProtocolError("capabilities", "invalid_request", "producer HOME is not an absolute path")
    return posixpath.join(lines[0], ".local", "state", "confflow", "control")


def build_control_execute_command(executable: str | None, state_root: str, run_id: str) -> str:
    """Build the one-shot producer command used by a JobDesk launcher."""
    state_root = _absolute_remote_path(state_root, "state_root")
    run_id = _safe_launcher_run_id(run_id)
    return (
        f"{quote_confflow_executable(executable)} control execute"
        f" --state-root {shlex.quote(state_root)} --run-id {shlex.quote(run_id)} --json"
    )


def build_control_worker_command(
    worker_executable: str | None,
    state_root: str,
    run_id: str,
    handoff_path: str,
) -> str:
    """Build the producer-owned external worker handoff command."""
    state_root = _absolute_remote_path(state_root, "state_root")
    handoff_path = _absolute_remote_path(handoff_path, "handoff_path")
    run_id = _safe_launcher_run_id(run_id)
    return (
        f"{quote_confflow_executable(worker_executable)}"
        f" --state-root {shlex.quote(state_root)}"
        f" --run-id {shlex.quote(run_id)}"
        f" --handoff {shlex.quote(handoff_path)} --json"
    )


def build_control_launcher_script(
    *,
    executable: str | None,
    worker_executable: str | None,
    handoff_path: str,
    state_root: str,
    run_id: str,
    metadata_path: str,
    scheduler_type: str,
    resources: ResourceSpec,
    env_init_scripts: Iterable[str] = (),
    worker_only: bool = False,
) -> str:
    """Build a scheduler script for execute followed by the producer worker.

    The marker is written before the producer command so a lost submit response
    can be reconciled without submitting the same producer request twice. The
    producer worker is launched through ``setsid`` so its lease records a
    dedicated process session, which is required for crash recovery.
    """
    state_root = _absolute_remote_path(state_root, "state_root")
    handoff_path = _absolute_remote_path(handoff_path, "handoff_path")
    metadata_path = _absolute_remote_path(metadata_path, "metadata_path")
    run_id = _safe_launcher_run_id(run_id)
    scheduler = (scheduler_type or "nohup").lower()
    execute_command = build_control_execute_command(executable, state_root, run_id)
    worker_command = build_control_worker_command(worker_executable, state_root, run_id, handoff_path)
    command = (
        f"setsid --wait {worker_command}"
        if worker_only
        else f"{execute_command} && setsid --wait {worker_command}"
    )
    marker = json.dumps(
        {
            "content_schema": "jobdesk.confflow.launcher.v1",
            "run_id": run_id,
            "scheduler_type": scheduler,
            "scheduler_job_id": "__JOBDESK_SCHEDULER_JOB_ID__",
            "pid": "__JOBDESK_PID__",
            "command": command,
            "state_root": state_root,
            "execution_state": "started",
            "execute_rc": 0 if worker_only else None,
            "worker_rc": None,
            "worker_started": worker_only,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    marker_q = shlex.quote(marker)
    metadata_q = shlex.quote(metadata_path)
    tmp_q = f"{shlex.quote(metadata_path + '.tmp.')}$$"
    lines: list[str]
    if scheduler in {"slurm", "sbatch"}:
        lines = SlurmAdapter.build_header(resources, job_name=f"jd_control_{run_id}")
    elif scheduler in {"pbs", "torque", "qsub"}:
        lines = PBSAdapter.build_header(resources, job_name=f"jd_control_{run_id}")
    elif scheduler == "nohup":
        lines = ["#!/usr/bin/env bash"]
    else:
        raise ValueError(f"Unknown scheduler type: {scheduler_type}")
    lines.extend(
        [
            "set -e",
            f"marker={marker_q}",
            'job_id="${SLURM_JOB_ID:-${PBS_JOBID:-}}"',
            "marker=${marker//__JOBDESK_SCHEDULER_JOB_ID__/$job_id}",
            'marker=${marker//__JOBDESK_PID__/$$}',
            f"printf '%s\\n' \"$marker\" > {tmp_q}",
            f"mv -f {tmp_q} {metadata_q}",
        ]
    )
    if worker_only:
        lines.extend(
            [
                "set +e",
                build_confflow_preflight_shell(f"setsid --wait {worker_command}", env_init_scripts),
                "worker_rc=$?",
                "set -e",
                "old_fragment='\"execution_state\":\"started\"'",
                "new_fragment='\"execution_state\":\"completed\"'",
                'completed_marker="${marker//$old_fragment/$new_fragment}"',
                "old_worker_rc='\"worker_rc\":null'",
                "new_worker_rc='\"worker_rc\":'\"$worker_rc\"''",
                'completed_marker="${completed_marker//$old_worker_rc/$new_worker_rc}"',
                f"printf '%s\\n' \"$completed_marker\" > {tmp_q}",
                f"mv -f {tmp_q} {metadata_q}",
                "",
            ]
        )
        return "\n".join(lines)
    lines.extend(
        [
            "set +e",
            build_confflow_preflight_shell(execute_command, env_init_scripts),
            "execute_rc=$?",
            "set -e",
            'worker_rc=""',
            'if [ "$execute_rc" -eq 0 ]; then',
            '  old_worker_fragment=\'"worker_rc":null,"worker_started":false\'',
            '  new_worker_fragment=\'"worker_rc":null,"worker_started":true\'',
            '  started_marker="${marker//$old_worker_fragment/$new_worker_fragment}"',
            f"  printf '%s\\n' \"$started_marker\" > {tmp_q}",
            f"  mv -f {tmp_q} {metadata_q}",
            '  marker="$started_marker"',
            "  set +e",
            build_confflow_preflight_shell(f"setsid --wait {worker_command}", env_init_scripts),
            "  worker_rc=$?",
            "  set -e",
            '  execution_state="completed"',
            'else',
            '  worker_rc=""',
            '  execution_state="failed"',
            'fi',
            "old_fragment='\"execute_rc\":null,\"execution_state\":\"started\"'",
            "new_fragment='\"execute_rc\":'\"$execute_rc\"',\"execution_state\":\"'\"$execution_state\"'\"'",
            'completed_marker="${marker//$old_fragment/$new_fragment}"',
            'if [ "$execute_rc" -eq 0 ]; then',
            "  old_worker_rc='\"worker_rc\":null'",
            "  new_worker_rc='\"worker_rc\":'\"$worker_rc\"''",
            '  completed_marker="${completed_marker//$old_worker_rc/$new_worker_rc}"',
            'fi',
            f"printf '%s\\n' \"$completed_marker\" > {tmp_q}",
            f"mv -f {tmp_q} {metadata_q}",
            "",
        ]
    )
    return "\n".join(lines)


def _absolute_remote_path(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} must be an absolute POSIX path")
    return posixpath.normpath(value)


def _required_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _safe_request_component(value: str, label: str) -> str:
    """Keep producer request identifiers inside the isolated request directory."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not value[0].isalnum()
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in value)
    ):
        raise ValueError(f"{label} must match the producer request identifier pattern")
    return value


def _looks_like_unsupported_capability(stderr: str, stdout: str) -> bool:
    diagnostic = f"{stderr}\n{stdout}".lower()
    return any(
        marker in diagnostic
        for marker in (
            "command not found",
            "no such file or directory",
            "invalid choice",
            "unrecognized arguments",
            "unknown command",
            "--json requires --capabilities",
        )
    )


def _safe_launcher_run_id(run_id: str) -> str:
    return _safe_request_component(run_id, "run_id")


__all__ = [
    "SSHControlTransport",
    "build_control_execute_command",
    "build_control_worker_command",
    "build_control_launcher_script",
    "resolve_control_state_root",
]
