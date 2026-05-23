"""Real WSL + Gaussian 16 workflow integration test.

Requires environment variables:
    JOBDESK_TEST_SSH_SERVER_ID=wsl
    JOBDESK_TEST_REMOTE_TMP_DIR=/tmp/jobdesk_test
    JOBDESK_TEST_REAL_G16=1

Run with:
    $env:JOBDESK_TEST_SSH_SERVER_ID="wsl"
    $env:JOBDESK_TEST_REMOTE_TMP_DIR="/tmp/jobdesk_test"
    $env:JOBDESK_TEST_REAL_G16="1"
    pytest tests/integration/test_real_workflow_wsl.py -v --basetemp .pytest_tmp_real_wsl_workflow

Skipped automatically if env vars are not set.
"""
import os
import re
import shlex
import time
from pathlib import Path

import pytest

from jobdesk_app.config.servers import load_servers
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest
from jobdesk_app.remote.status_refresh import refresh_batch_status
from jobdesk_app.services.run_service import RunService
from jobdesk_app.services.ssh_session import create_sftp_client, create_ssh_client
from jobdesk_app.services.workflow_service import (
    BUILTIN_WORKFLOWS,
    WorkflowRunner,
    read_events,
)

_SERVER_ID = os.environ.get("JOBDESK_TEST_SSH_SERVER_ID", "")
_REMOTE_TMP = os.environ.get("JOBDESK_TEST_REMOTE_TMP_DIR", "/tmp/jobdesk_test")
_REAL_G16 = os.environ.get("JOBDESK_TEST_REAL_G16", "")

pytestmark = pytest.mark.skipif(
    not (_SERVER_ID and _REAL_G16),
    reason="Set JOBDESK_TEST_SSH_SERVER_ID and JOBDESK_TEST_REAL_G16=1 to run",
)

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "gaussian"
POLL_INTERVAL = 5
TIMEOUT = 120


def _remote_tmp_for_cleanup(remote_tmp: str) -> str:
    """Return a restricted remote temp path used by destructive cleanup."""
    if re.fullmatch(r"/tmp/jobdesk_[A-Za-z0-9._-]+", remote_tmp) is None:
        raise ValueError("JOBDESK_TEST_REMOTE_TMP_DIR must be under /tmp/jobdesk_*")
    return shlex.quote(remote_tmp)


@pytest.fixture(scope="module")
def server():
    servers = load_servers().servers
    return servers[_SERVER_ID]


@pytest.fixture(scope="module")
def ssh_sftp(server):
    ssh = create_ssh_client(server)
    ssh.connect()
    sftp = create_sftp_client(ssh)
    yield ssh, sftp
    sftp.close()
    ssh.close()


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    return tmp_path_factory.mktemp("wsl_wf")


@pytest.fixture(scope="module", autouse=True)
def prepare_remote(ssh_sftp):
    ssh, sftp = ssh_sftp
    try:
        remote_tmp = _remote_tmp_for_cleanup(_REMOTE_TMP)
    except ValueError as exc:
        pytest.fail(str(exc))
    ssh.run(f"rm -rf {remote_tmp}; mkdir -p {remote_tmp}", check=True)
    sftp.upload_file(EXAMPLES_DIR / "water_opt.gjf", f"{_REMOTE_TMP}/water_opt.gjf", overwrite=True)
    yield
    ssh.run(f"rm -rf {remote_tmp}")


def _wait_run_complete(ssh, run_record, svc, timeout=TIMEOUT):
    """Poll until all tasks reach remote_completed/downloaded/failed."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        refresh_batch_status(
            ssh=ssh,
            manifest_path=run_record.manifest_path,
            remote_batch_dir=f"{run_record.remote_dir.rstrip('/')}/.jobdesk_runs/{run_record.run_id}",
            batch_id=run_record.run_id,
            write=True,
        )
        svc.update_run_from_manifest(run_record.run_id)
        tasks = Manifest.read(run_record.manifest_path)
        if all(t.status in (TaskStatus.remote_completed, TaskStatus.downloaded, TaskStatus.failed) for t in tasks):
            return tasks
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"Timeout waiting for run {run_record.run_id}")


class TestWaterOptFreqReal:
    """Full opt -> freq workflow on WSL with real g16."""

    def test_full_workflow(self, workspace, ssh_sftp, server):
        ssh, sftp = ssh_sftp
        spec = BUILTIN_WORKFLOWS["opt_freq"]
        runner = WorkflowRunner(workspace)
        svc = RunService(workspace)

        # 1. Start workflow
        wf_run = runner.start(spec, _SERVER_ID, _REMOTE_TMP, [f"{_REMOTE_TMP}/water_opt.gjf"])
        started, uploads = runner.advance(spec, wf_run, None, None)
        assert started == ["opt"]
        assert not uploads  # first step uses original file, no generated inputs

        # 2. Submit opt
        opt_run_id = wf_run.step_run_ids["opt"]
        from jobdesk_app.services.scheduler_helpers import resources_from_server, scheduler_from_server
        svc.submit_run(
            opt_run_id, ssh, sftp,
            env_init_scripts=list(server.env_init_scripts or []),
            scheduler=scheduler_from_server(server),
            resources=resources_from_server(server),
        )

        # 3. Wait for opt to complete
        opt_record = svc.load_run(opt_run_id)
        _wait_run_complete(ssh, opt_record, svc)

        # 4. Download opt results
        records, failures = svc.download_completed(opt_run_id, sftp, ["*.log", "*.out"])
        assert not failures
        assert len(records) > 0

        # 5. Advance starts freq
        runner.sync_status(spec, wf_run)
        assert wf_run.step_status["opt"] == "completed"
        started, uploads = runner.advance(spec, wf_run, None, None)
        assert started == ["freq"]
        assert len(uploads) > 0

        # Upload generated inputs and submit freq
        for local_path, remote_path in uploads.items():
            sftp.upload_file(Path(local_path), remote_path, overwrite=True)
        freq_run_id = wf_run.step_run_ids["freq"]
        svc.submit_run(
            freq_run_id, ssh, sftp,
            env_init_scripts=list(server.env_init_scripts or []),
            scheduler=scheduler_from_server(server),
            resources=resources_from_server(server),
        )

        # 6. Wait for freq to complete
        freq_record = svc.load_run(freq_run_id)
        _wait_run_complete(ssh, freq_record, svc)

        # 7. Download freq results
        records, failures = svc.download_completed(freq_run_id, sftp, ["*.log", "*.out"])
        assert not failures

        # 8. Final advance
        runner.sync_status(spec, wf_run)
        assert wf_run.step_status["freq"] == "completed"
        started, _ = runner.advance(spec, wf_run, None, None)
        assert started == []  # nothing left to start

        # 9. Verify diagnostics
        events = read_events(workspace, wf_run.workflow_id)
        event_types = [e["event_type"] for e in events]
        assert "workflow_started" in event_types
        assert "step_started" in event_types
        assert "downstream_input_generated" in event_types
