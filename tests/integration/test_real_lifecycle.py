# -*- coding: utf-8 -*-
"""Real WorkflowService lifecycle integration tests.

These tests require a reachable Linux SSH server and are skipped by default.

Required environment:
    JOBDESK_TEST_SERVERS_YAML
    JOBDESK_TEST_SSH_SERVER_ID
    JOBDESK_TEST_REMOTE_TMP_DIR

Optional multi-server environment:
    JOBDESK_TEST_SERVER_ID_A
    JOBDESK_TEST_SERVER_ID_B
"""

from __future__ import annotations

import os
import shlex
import time
import uuid
from pathlib import Path

import pytest
import yaml

from jobdesk_app.config.runtime import ResolvedExecutionContext
from jobdesk_app.config.servers import load_servers
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest
from jobdesk_app.remote.sftp import SFTPClientWrapper
from jobdesk_app.remote.ssh import SSHClientWrapper
from jobdesk_app.services.project_service import create_project_context
from jobdesk_app.services.workflow_service import WorkflowService


pytestmark = pytest.mark.skipif(
    not all((
        os.environ.get("JOBDESK_TEST_SERVERS_YAML"),
        os.environ.get("JOBDESK_TEST_SSH_SERVER_ID"),
        os.environ.get("JOBDESK_TEST_REMOTE_TMP_DIR"),
    )),
    reason=(
        "requires JOBDESK_TEST_SERVERS_YAML, JOBDESK_TEST_SSH_SERVER_ID, "
        "and JOBDESK_TEST_REMOTE_TMP_DIR"
    ),
)


class _ConnectedSFTP:
    def __init__(self, server_config):
        self._ssh = SSHClientWrapper(server_config, timeout=15)
        self._ssh.connect()
        self._sftp = SFTPClientWrapper.from_ssh(self._ssh)

    def __getattr__(self, name):
        return getattr(self._sftp, name)

    def close(self):
        self._sftp.close()
        self._ssh.close()


def _server_ids() -> tuple[str, str]:
    default_id = os.environ["JOBDESK_TEST_SSH_SERVER_ID"]
    return (
        os.environ.get("JOBDESK_TEST_SERVER_ID_A", default_id),
        os.environ.get("JOBDESK_TEST_SERVER_ID_B", default_id),
    )


def _write_fake_project(project_dir: Path) -> None:
    (project_dir / "inputs" / "g16").mkdir(parents=True)
    (project_dir / "inputs" / "orca").mkdir(parents=True)
    (project_dir / "shared").mkdir(parents=True)
    (project_dir / "shared" / "basis.dat").write_text("fake basis\n", encoding="utf-8", newline="\n")
    _write_task_script(project_dir / "inputs" / "g16" / "g16_task.sh", "g16", "-1.23")
    _write_task_script(project_dir / "inputs" / "orca" / "orca_task.sh", "orca", "-2.34")

    project_yaml = {
        "project_id": "real-lifecycle",
        "project": {"name": "real_lifecycle"},
        "local_paths": {"input_dir": "./inputs", "result_dir": "./results"},
        "task_discoveries": [
            {
                "name": "g16_jobs",
                "mode": "flat_single",
                "entry_glob": "g16/*.sh",
                "task_id_prefix": "g16_",
                "execution_profile": "g16",
            },
            {
                "name": "orca_jobs",
                "mode": "flat_single",
                "entry_glob": "orca/*.sh",
                "task_id_prefix": "orca_",
                "execution_profile": "orca",
            },
        ],
        "execution_profiles": {
            "g16": {
                "label": "G16 fake",
                "command": "bash {entry_name} {shared_dir_abs}/basis.dat",
                "defaults": {"max_parallel": 2},
            },
            "orca": {
                "label": "ORCA fake",
                "command": "bash {entry_name} {shared_dir_abs}/basis.dat",
                "defaults": {"max_parallel": 2},
            },
        },
        "upload": {
            "task_files": {
                "include": ["*.sh", "g16/*.sh", "orca/*.sh"],
                "exclude": [],
                "require_entry_file": True,
                "on_missing": "ignore",
            },
            "shared_files": {
                "base_dir": "shared",
                "include": ["basis.dat"],
                "exclude": [],
                "target_subdir": "_shared",
                "on_missing": "error",
            },
        },
        "download": {"patterns": ["result.out"]},
        "extract": {
            "results": [
                {
                    "name": "energy",
                    "source_glob": "result.out",
                    "regex": r"energy=(?P<value>-?\d+(?:\.\d+)?)",
                    "strategy": "last",
                    "type": "float",
                }
            ]
        },
    }
    (project_dir / "project.yaml").write_text(yaml.safe_dump(project_yaml), encoding="utf-8")


def _write_task_script(path: Path, profile: str, energy: str) -> None:
    path.write_text(
        "\n".join([
            "#!/usr/bin/env bash",
            "set -e",
            'shared_file="$1"',
            'if [ ! -f "$shared_file" ]; then',
            '  echo "missing shared file: $shared_file" >&2',
            "  exit 2",
            "fi",
            f"echo 'profile={profile}' > result.out",
            f"echo 'energy={energy}' >> result.out",
            "echo 'shared='$(cat \"$shared_file\") >> result.out",
            "",
        ]),
        encoding="utf-8",
        newline="\n",
    )


def _resolved_contexts(ctx, server_a, server_b, remote_root: str) -> dict[str, ResolvedExecutionContext]:
    return {
        "g16": ResolvedExecutionContext(
            project_id=ctx.project_id,
            execution_profile_name="g16",
            server_id=server_a.server_id,
            server_config=server_a,
            remote_work_dir=f"{remote_root}/g16",
            command_template=ctx.project_config.execution_profiles["g16"].command,
            max_parallel=2,
        ),
        "orca": ResolvedExecutionContext(
            project_id=ctx.project_id,
            execution_profile_name="orca",
            server_id=server_b.server_id,
            server_config=server_b,
            remote_work_dir=f"{remote_root}/orca",
            command_template=ctx.project_config.execution_profiles["orca"].command,
            max_parallel=2,
        ),
    }


def _cleanup_remote(servers, server_ids: set[str], remote_root: str) -> None:
    for server_id in sorted(server_ids):
        ssh = SSHClientWrapper(servers.servers[server_id], timeout=15)
        try:
            ssh.connect()
            ssh.run(f"rm -rf {shlex.quote(remote_root)}", timeout=30)
        finally:
            ssh.close()


class TestRealWorkflowLifecycle:
    def test_mixed_profile_workflow_service_end_to_end(self, tmp_path):
        servers_yaml = Path(os.environ["JOBDESK_TEST_SERVERS_YAML"])
        remote_tmp = os.environ["JOBDESK_TEST_REMOTE_TMP_DIR"].rstrip("/")
        servers = load_servers(servers_yaml)
        server_id_a, server_id_b = _server_ids()
        if server_id_a not in servers.servers or server_id_b not in servers.servers:
            pytest.skip("configured lifecycle server id is not present in servers.yaml")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_fake_project(project_dir)
        ctx = create_project_context(project_dir, servers_yaml)
        svc = WorkflowService(ctx)
        remote_root = f"{remote_tmp}/jobdesk_lifecycle_{uuid.uuid4().hex[:8]}"

        try:
            rctx = _resolved_contexts(
                ctx,
                servers.servers[server_id_a],
                servers.servers[server_id_b],
                remote_root,
            )
            packages = svc.scan_inputs()
            batch = svc.create_batch(packages, rctx)

            records, upload_failures = svc.upload_tasks(
                batch.tasks,
                sftp_factory=lambda sid: _ConnectedSFTP(servers.servers[sid]),
                batch_dir=batch.batch_dir,
                manifest_path=batch.manifest_path,
            )
            assert records
            assert upload_failures == []

            submit_results = svc.submit_batch(
                batch.manifest_path,
                batch.batch_meta.batch_id,
                ssh_factory=lambda sc: SSHClientWrapper(sc, timeout=15).__enter__(),
                sftp_factory=lambda sc: _ConnectedSFTP(sc),
            )
            assert sum(r.submitted_task_count for r in submit_results) == 2
            assert all(not r.errors for r in submit_results)

            _wait_until_remote_completed(svc, batch.manifest_path, batch.batch_meta.batch_id)

            tasks = Manifest.read(batch.manifest_path)
            download_records, download_failures = svc.download_completed(
                tasks,
                sftp_factory=lambda sid: _ConnectedSFTP(servers.servers[sid]),
                manifest_path=batch.manifest_path,
            )
            assert len(download_records) == 2
            assert download_failures == []

            results, analyze_failures, summaries = svc.analyze_batch(
                Manifest.read(batch.manifest_path),
                batch.batch_meta.batch_id,
            )
            assert len(results) == 2
            assert analyze_failures == []
            assert summaries
            assert sorted(r.value for r in results) == [-2.34, -1.23]
        finally:
            _cleanup_remote(servers, {server_id_a, server_id_b}, remote_root)


def _wait_until_remote_completed(svc: WorkflowService, manifest_path: Path, batch_id: str) -> None:
    last_statuses: set[TaskStatus] = set()
    for _ in range(30):
        results, failures = svc.refresh_batch(
            manifest_path,
            batch_id,
            ssh_factory=lambda sc: SSHClientWrapper(sc, timeout=15).__enter__(),
            write=True,
        )
        assert failures == []
        assert results
        tasks = Manifest.read(manifest_path)
        last_statuses = {t.status for t in tasks}
        if last_statuses == {TaskStatus.remote_completed}:
            return
        time.sleep(1)
    raise AssertionError(f"tasks did not complete; last statuses={sorted(s.value for s in last_statuses)}")
