"""Opt-in real WSL acceptance through the public 0.8 application facade."""

from __future__ import annotations

import os
import shlex
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from jobdesk_app.bootstrap import create_application
from jobdesk_app.core.submit_payload import InputSource, SubmitPayload, WorkflowFields
from jobdesk_app.infrastructure.config.servers import load_servers
from jobdesk_app.infrastructure.remote.ssh import SSHClientWrapper
from tests.integration._remote_safety import cleanup_remote_control_state, cleanup_remote_test_dir

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not all(
            (
                os.environ.get("JOBDESK_TEST_SERVERS_YAML"),
                os.environ.get("JOBDESK_TEST_SSH_SERVER_ID"),
                os.environ.get("JOBDESK_TEST_REMOTE_TMP_DIR"),
                os.environ.get("JOBDESK_TEST_REAL_CONFFLOW") == "1",
            )
        ),
        reason="需要 WSL 配置和 JOBDESK_TEST_REAL_CONFFLOW=1",
    ),
]

WATER_XYZ = """3
water
O  0.000000  0.000000  0.000000
H  0.000000  0.757000  0.586000
H  0.000000 -0.757000  0.586000
"""

CONFFLOW_YAML = """global:
  gaussian_path: /opt/g16/g16
  cores_per_task: 1
  total_memory: 1GB
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: quick_opt
    type: calc
    params:
      iprog: g16
      itask: opt
      keyword: "opt b3lyp/6-31g(d)"
      cores_per_task: 1
      total_memory: 1GB
      max_parallel_jobs: 1
"""


def _failures(outcome) -> list[str]:
    return [failure.message for failure in outcome.failures]


def test_candidate_facade_real_wsl_submit_recover_download_without_resubmit(tmp_path):
    """Exercise admission, staging, dispatch, recovery and download once."""

    servers_path = Path(os.environ["JOBDESK_TEST_SERVERS_YAML"])
    server_id = os.environ["JOBDESK_TEST_SSH_SERVER_ID"]
    server = load_servers(servers_path).servers[server_id]
    remote_root = os.environ["JOBDESK_TEST_REMOTE_TMP_DIR"].rstrip("/")
    remote_dir = f"{remote_root}/jobdesk_v080_{uuid.uuid4().hex[:10]}"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "water.xyz"
    source.write_text(WATER_XYZ, encoding="utf-8")
    runs_dir = tmp_path / "runs"
    payload = SubmitPayload(
        kind="confflow",
        inputs=[InputSource(source, side="local", kind="xyz")],
        program="gaussian",
        calc=object(),
        workflow=WorkflowFields(
            work_dir_name="water_confflow_work",
            yaml_text=CONFFLOW_YAML,
        ),
        output_dir=workspace,
        server_id=server_id,
        remote_dir=remote_dir,
        max_parallel=1,
    )

    ssh = SSHClientWrapper(server, timeout=20)
    ssh.connect()
    executable = str(server.confflow_executable or "")
    assert executable, "real acceptance requires a pinned ConfFlow executable"
    version = ssh.run(f"{shlex.quote(executable)} --version", check=True)
    assert "2.1.6" in version.stdout
    prefix = datetime.now().strftime("%y%m%d")
    for candidate in range(1, 1000):
        candidate_run_id = f"{prefix}-{candidate:03d}"
        state_root = f"/root/.local/state/confflow/jobdesk-{candidate_run_id}"
        if ssh.run(f"test -e {shlex.quote(state_root)}").exit_code != 0:
            break
        (runs_dir / candidate_run_id).mkdir(parents=True)
    else:
        pytest.fail("could not reserve an unused real-WSL control run id")
    application = create_application(workspace, servers_path=servers_path, runs_dir=runs_dir)
    run_id = ""
    try:
        submitted = application.runs.submit(payload)
        assert submitted.ok, _failures(submitted)
        assert submitted.value is not None
        assert submitted.value.submitted_task_count == 1
        assert len(submitted.value.runs) == 1
        run_id = submitted.value.runs[0].summary.run_id

        assert run_id == candidate_run_id
        for _ in range(300):
            refreshed = application.runs.refresh(run_id)
            assert refreshed.ok, _failures(refreshed)
            assert refreshed.value is not None, _failures(refreshed)
            statuses = {task.status for task in refreshed.value.tasks}
            if statuses and statuses <= {"remote_completed", "downloaded", "analyzed"}:
                break
            time.sleep(2)
        else:
            pytest.fail("ConfFlow facade run did not finish within 600 seconds")

        downloaded = application.runs.download(run_id)
        assert downloaded.ok, _failures(downloaded)
        assert downloaded.value is not None
        downloaded_paths = tuple(Path(path) for path in downloaded.value.local_paths)
        assert downloaded_paths
        assert all(path.exists() for path in downloaded_paths)
        assert any(path.name == "output.xyz" for path in downloaded_paths)
    finally:
        application.close()

    recovered = create_application(workspace, servers_path=servers_path, runs_dir=runs_dir)
    try:
        assert [item.run_id for item in recovered.runs.list_runs()] == [run_id]
        refreshed = recovered.runs.refresh(run_id)
        assert refreshed.value is not None, _failures(refreshed)
        assert [item.run_id for item in recovered.runs.list_runs()] == [run_id]
    finally:
        recovered.close()
        cleanup_remote_test_dir(ssh, remote_dir, remote_root)
        if run_id:
            cleanup_remote_control_state(ssh, run_id)
        ssh.close()
