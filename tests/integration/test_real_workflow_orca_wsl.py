"""Real WSL + ORCA workflow integration test.

Requires environment variables:
    JOBDESK_TEST_SSH_SERVER_ID=wsl
    JOBDESK_TEST_REMOTE_TMP_DIR=/tmp/jobdesk_orca_test
    JOBDESK_TEST_REAL_ORCA=1

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
from jobdesk_app.services.scheduler_helpers import resources_from_server, scheduler_from_server
from jobdesk_app.services.ssh_session import create_sftp_client, create_ssh_client
from jobdesk_app.services.workflow_service import BUILTIN_WORKFLOWS, WorkflowRunner, read_events

_SERVER_ID = os.environ.get("JOBDESK_TEST_SSH_SERVER_ID", "")
_REMOTE_TMP = os.environ.get("JOBDESK_TEST_REMOTE_TMP_DIR", "/tmp/jobdesk_orca_test")
_REAL_ORCA = os.environ.get("JOBDESK_TEST_REAL_ORCA", "")

pytestmark = pytest.mark.skipif(
    not (_SERVER_ID and _REAL_ORCA),
    reason="Set JOBDESK_TEST_SSH_SERVER_ID and JOBDESK_TEST_REAL_ORCA=1 to run",
)

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "orca"
POLL_INTERVAL = 5
TIMEOUT = 120


def _safe_remote_tmp(remote_tmp: str) -> str:
    if re.fullmatch(r"/tmp/jobdesk_[A-Za-z0-9._-]+", remote_tmp) is None:
        raise ValueError("JOBDESK_TEST_REMOTE_TMP_DIR must be under /tmp/jobdesk_*")
    return shlex.quote(remote_tmp)


@pytest.fixture(scope="module")
def server():
    return load_servers().servers[_SERVER_ID]


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
    return tmp_path_factory.mktemp("wsl_orca_wf")


@pytest.fixture(scope="module", autouse=True)
def prepare_remote(ssh_sftp):
    ssh, sftp = ssh_sftp
    try:
        remote_tmp = _safe_remote_tmp(_REMOTE_TMP)
    except ValueError as exc:
        pytest.fail(str(exc))
    ssh.run(f"rm -rf {remote_tmp}; mkdir -p {remote_tmp}", check=True)
    sftp.upload_file(EXAMPLES_DIR / "water_opt.inp", f"{_REMOTE_TMP}/water_opt.inp", overwrite=True)
    yield
    ssh.run(f"rm -rf {remote_tmp}")


def _wait_run_complete(ssh, run_record, svc, timeout=TIMEOUT):
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


class TestWaterOrcaOptFreqReal:
    def test_full_workflow(self, workspace, ssh_sftp, server):
        ssh, sftp = ssh_sftp
        spec = BUILTIN_WORKFLOWS["orca_opt_freq"]
        runner = WorkflowRunner(workspace)
        svc = RunService(workspace)

        wf_run = runner.start(spec, _SERVER_ID, _REMOTE_TMP, [f"{_REMOTE_TMP}/water_opt.inp"])
        started, uploads = runner.advance(spec, wf_run, None, None)
        assert started == ["opt"]
        assert uploads == {}

        opt_run_id = wf_run.step_run_ids["opt"]
        svc.submit_run(
            opt_run_id,
            ssh,
            sftp,
            env_init_scripts=list(server.env_init_scripts or []),
            scheduler=scheduler_from_server(server),
            resources=resources_from_server(server),
        )
        _wait_run_complete(ssh, svc.load_run(opt_run_id), svc)

        records, failures = svc.download_completed(opt_run_id, sftp, ["*.out"])
        assert not failures
        assert records

        runner.sync_status(spec, wf_run)
        assert wf_run.step_status["opt"] == "completed"
        started, uploads = runner.advance(spec, wf_run, None, None)
        assert started == ["freq"]
        assert len(uploads) == 1
        generated_path = Path(next(iter(uploads)))
        assert generated_path.suffix == ".inp"
        assert "! freq" in generated_path.read_text(encoding="utf-8")

        for local_path, remote_path in uploads.items():
            sftp.upload_file(Path(local_path), remote_path, overwrite=True)
        freq_run_id = wf_run.step_run_ids["freq"]
        svc.submit_run(
            freq_run_id,
            ssh,
            sftp,
            env_init_scripts=list(server.env_init_scripts or []),
            scheduler=scheduler_from_server(server),
            resources=resources_from_server(server),
        )
        _wait_run_complete(ssh, svc.load_run(freq_run_id), svc)

        records, failures = svc.download_completed(freq_run_id, sftp, ["*.out"])
        assert not failures
        assert records

        runner.sync_status(spec, wf_run)
        assert wf_run.step_status["freq"] == "completed"
        started, _ = runner.advance(spec, wf_run, None, None)
        assert started == []

        event_types = [event["event_type"] for event in read_events(workspace, wf_run.workflow_id)]
        assert "workflow_started" in event_types
        assert "downstream_input_generated" in event_types
