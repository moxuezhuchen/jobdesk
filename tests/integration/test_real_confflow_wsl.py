"""Real WSL ConfFlow batch validation; opt in because it executes Gaussian."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from jobdesk_app.config.servers import load_servers
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest
from jobdesk_app.remote.sftp import SFTPClientWrapper
from jobdesk_app.remote.ssh import SSHClientWrapper
from jobdesk_app.remote.status_refresh import refresh_batch_status
from jobdesk_app.services.confflow_results import load_summary
from jobdesk_app.services.program_adapters import ConfFlowAdapter
from jobdesk_app.services.run_service import RunService
from jobdesk_app.services.scheduler_helpers import (
    resources_from_server,
    scheduler_from_server,
)

pytestmark = pytest.mark.skipif(
    not all((
        os.environ.get("JOBDESK_TEST_SERVERS_YAML"),
        os.environ.get("JOBDESK_TEST_SSH_SERVER_ID"),
        os.environ.get("JOBDESK_TEST_REMOTE_TMP_DIR"),
        os.environ.get("JOBDESK_TEST_REAL_CONFFLOW") == "1",
    )),
    reason="需要 WSL 配置和 JOBDESK_TEST_REAL_CONFFLOW=1",
)


WATER_XYZ = """3
water
O  0.000000  0.000000  0.000000
H  0.000000  0.757000  0.586000
H  0.000000 -0.757000  0.586000
"""

METHANE_XYZ = """5
methane
C   0.000000   0.000000   0.000000
H   0.629118   0.629118   0.629118
H  -0.629118  -0.629118   0.629118
H  -0.629118   0.629118  -0.629118
H   0.629118  -0.629118  -0.629118
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


def test_real_confflow_batch_two_molecules(tmp_path):
    server_id = os.environ["JOBDESK_TEST_SSH_SERVER_ID"]
    server = load_servers(os.environ["JOBDESK_TEST_SERVERS_YAML"]).servers[server_id]
    remote_root = os.environ["JOBDESK_TEST_REMOTE_TMP_DIR"].rstrip("/")
    remote_dir = f"{remote_root}/confflow_batch_{uuid.uuid4().hex[:8]}"

    # Write local files
    (tmp_path / "water.xyz").write_text(WATER_XYZ, encoding="utf-8")
    (tmp_path / "methane.xyz").write_text(METHANE_XYZ, encoding="utf-8")
    (tmp_path / "confflow.yaml").write_text(CONFFLOW_YAML, encoding="utf-8")

    ssh = SSHClientWrapper(server, timeout=20)
    ssh.connect()
    sftp = SFTPClientWrapper.from_ssh(ssh)
    try:
        # Upload inputs
        for name in ("water.xyz", "methane.xyz", "confflow.yaml"):
            sftp.upload_file(tmp_path / name, f"{remote_dir}/{name}", overwrite=True)

        # Build batch spec
        service = RunService(tmp_path, runs_dir=tmp_path / "runs")
        spec = ConfFlowAdapter.build_spec(
            server_id=server_id,
            remote_dir=remote_dir,
            xyz_paths=[f"{remote_dir}/water.xyz", f"{remote_dir}/methane.xyz"],
            config_path=f"{remote_dir}/confflow.yaml",
            max_parallel=2,
        )
        assert len(spec.sources) == 2
        assert spec.max_parallel == 2

        record = service.create_run(spec, run_id="confflow-batch")
        submitted = service.submit_run(
            record.run_id,
            ssh,
            sftp,
            env_init_scripts=list(getattr(server, "env_init_scripts", []) or []),
            scheduler=scheduler_from_server(server),
            resources=resources_from_server(server),
        )
        assert not submitted.errors

        # Wait for completion
        for _ in range(120):
            refresh_batch_status(
                ssh=ssh,
                manifest_path=record.manifest_path,
                remote_batch_dir=f"{remote_dir}/.jobdesk_runs/{record.run_id}",
                batch_id=record.run_id,
                write=True,
            )
            service.update_run_from_manifest(record.run_id)
            tasks = Manifest.read(record.manifest_path)
            if all(t.status == TaskStatus.remote_completed for t in tasks):
                break
            time.sleep(2)
        else:
            pytest.fail("ConfFlow batch did not finish within 240 seconds")

        records, failures = service.download_completed(record.run_id, sftp, [])
        assert not failures

        # Verify both molecule summaries
        for mol in ("water", "methane"):
            summary_path = tmp_path / "results" / record.run_id / mol / f"{mol}_confflow_work" / "run_summary.json"
            assert summary_path.exists(), f"Missing summary for {mol}"
            summary = load_summary(summary_path)
            assert summary.final_conformers >= 1
    finally:
        ssh.run(f"rm -rf {remote_dir}")
        sftp.close()
        ssh.close()
