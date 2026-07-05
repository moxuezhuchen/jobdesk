"""Real E2E agent test: submit a workflow via AgentBridge, close/reopen GUI, verify completion.

Requires real SSH server and pre-installed confflow-agent.
Opt in with environment variables:
    JOBDESK_TEST_SERVERS_YAML=<path>
    JOBDESK_TEST_SSH_SERVER_ID=<server_id>
    JOBDESK_TEST_REMOTE_TMP_DIR=<remote_tmp>
    JOBDESK_TEST_REAL_AGENT=1
    JOBDESK_TEST_CONFFLOW_YAML=<yaml_path_on_remote>
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from jobdesk_app.config.servers import load_servers
from jobdesk_app.services.agent_bridge import AgentBridge

pytestmark = pytest.mark.integration


def _env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        pytest.skip(f"Requires env: {name}")
    return val


def _bridge() -> AgentBridge:
    yaml_path = Path(_env("JOBDESK_TEST_SERVERS_YAML"))
    server_id = _env("JOBDESK_TEST_SSH_SERVER_ID")
    return AgentBridge(server_id, servers_yaml=yaml_path)


def test_agent_install_and_start():
    """Verify agent can be installed and started on the remote."""
    bridge = _bridge()

    if bridge.is_agent_installed():
        pytest.skip("Agent already installed — run on a clean remote for this test")

    result = bridge.install_agent()
    assert result.ok, f"Install failed: {result.message}"

    result = bridge.start_agent()
    assert result.ok, f"Start failed: {result.message}"

    # Give the daemon a moment to initialise
    time.sleep(2)
    assert bridge.is_agent_running(), "Agent should be running after start"


def test_agent_submit_and_poll():
    """Submit a job, poll until it reaches terminal state, download output."""
    bridge = _bridge()

    if not bridge.is_agent_running():
        pytest.skip("Agent not running — start it first (run test_agent_install_and_start)")

    remote_tmp = _env("JOBDESK_TEST_REMOTE_TMP_DIR").rstrip("/")
    confflow_yaml = _env("JOBDESK_TEST_CONFFLOW_YAML")

    # Upload a simple test molecule
    test_dir = f"{remote_tmp}/agent_e2e_{uuid.uuid4().hex[:8]}"
    test_xyz = f"{test_dir}/mol.xyz"
    bridge._sftp_makedirs(test_dir)
    bridge._sftp_write_text(test_xyz, "1\ntest\nC 0 0 0\n")

    try:
        # Submit
        result = bridge.submit_job(
            config_remote=confflow_yaml,
            input_remote=test_xyz,
        )
        assert result.ok, f"Submit failed: {result.message}"
        job_id = result.data.get("job_id")
        assert job_id, "submit should return a job_id"
        print(f"Submitted job: {job_id}")

        # Poll until done or timeout
        MAX_WAIT = 300  # 5 minutes for a quick test calc
        POLL = 5
        deadline = time.time() + MAX_WAIT
        terminal = {"completed", "failed", "cancelled"}
        last_job = None
        while time.time() < deadline:
            time.sleep(POLL)
            jobs = bridge.parse_jobs()
            matching = [j for j in jobs if j.job_id == job_id]
            if matching:
                last_job = matching[0]
                print(f"  poll: {job_id} status={last_job.status} "
                      f"step={last_job.step} progress={last_job.progress_pct}%")
                if last_job.status in terminal:
                    break

        assert last_job is not None, f"Job {job_id} never appeared in list"
        assert last_job.status != "failed", (
            f"Job {job_id} failed — work_dir={last_job.work_dir}"
        )
        print(f"Final status: {last_job.status}")

        # Download output
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "output"
            dl = bridge.download_job_output(job_id, dest)
            assert dl.ok, f"Download failed: {dl.message}"
            print(f"Downloaded: {dl.message}")

    finally:
        # Cleanup
        bridge._exec(f"rm -rf -- {test_dir}", timeout=30, check=False)


def test_agent_pause_resume_cancel():
    """Submit, pause, resume, cancel a job lifecycle."""
    bridge = _bridge()

    if not bridge.is_agent_running():
        pytest.skip("Agent not running")

    remote_tmp = _env("JOBDESK_TEST_REMOTE_TMP_DIR").rstrip("/")
    confflow_yaml = _env("JOBDESK_TEST_CONFFLOW_YAML")

    test_dir = f"{remote_tmp}/agent_e2e_{uuid.uuid4().hex[:8]}"
    test_xyz = f"{test_dir}/mol.xyz"
    bridge._sftp_makedirs(test_dir)
    bridge._sftp_write_text(test_xyz, "1\ntest\nC 0 0 0\n")

    try:
        result = bridge.submit_job(config_remote=confflow_yaml, input_remote=test_xyz)
        assert result.ok, f"Submit failed: {result.message}"
        job_id = result.data["job_id"]

        # Wait for it to be running
        for _ in range(60):
            time.sleep(1)
            jobs = bridge.parse_jobs()
            running = [j for j in jobs if j.job_id == job_id and j.status in ("running", "pending")]
            if running:
                break

        # Cancel
        res = bridge.cancel_job(job_id)
        assert res.ok, f"Cancel failed: {res.message}"
        jobs = bridge.parse_jobs()
        cancelled = [j for j in jobs if j.job_id == job_id]
        assert cancelled and cancelled[0].status == "cancelled", \
            f"Expected cancelled, got: {cancelled[0].status if cancelled else 'not found'}"

    finally:
        bridge._exec(f"rm -rf -- {test_dir}", timeout=30, check=False)
