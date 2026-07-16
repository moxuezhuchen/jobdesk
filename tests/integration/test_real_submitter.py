# -*- coding: utf-8 -*-
"""真实提交集成测试 — 默认跳过，需要环境变量触发。

环境变量:
    JOBDESK_TEST_SSH_SERVER_ID
    JOBDESK_TEST_SERVERS_YAML
    JOBDESK_TEST_REMOTE_TMP_DIR

只运行 echo 类安全命令，不运行真实计算程序。
"""

import os
import time
import uuid

import pytest

from jobdesk_app.config.servers import load_servers
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.core.submit import SubmitMode
from jobdesk_app.remote.sftp import SFTPClientWrapper
from jobdesk_app.remote.ssh import SSHClientWrapper
from jobdesk_app.remote.submitter import JobSubmitter
from tests.integration._remote_safety import cleanup_remote_test_dir

pytestmark = pytest.mark.skipif(
    not all((
        os.environ.get("JOBDESK_TEST_SSH_SERVER_ID"),
        os.environ.get("JOBDESK_TEST_SERVERS_YAML"),
        os.environ.get("JOBDESK_TEST_REMOTE_TMP_DIR"),
    )),
    reason="需要 JOBDESK_TEST_SSH_SERVER_ID / JOBDESK_TEST_SERVERS_YAML / JOBDESK_TEST_REMOTE_TMP_DIR",
)


class TestRealSubmitter:
    """真实服务器提交集成测试 — 仅 echo 命令。"""

    def _setup(self):
        servers_yaml = os.environ["JOBDESK_TEST_SERVERS_YAML"]
        server_id = os.environ["JOBDESK_TEST_SSH_SERVER_ID"]
        remote_tmp = os.environ["JOBDESK_TEST_REMOTE_TMP_DIR"]
        cfg = load_servers(servers_yaml)
        server = cfg.servers[server_id]
        ssh = SSHClientWrapper(server, timeout=15)
        ssh.connect()
        sftp = SFTPClientWrapper.from_ssh(ssh)
        return ssh, sftp, remote_tmp

    def test_submit_echo_task(self):
        ssh, sftp, remote_tmp = self._setup()
        test_id = f"jobdesk_m5_{uuid.uuid4().hex[:8]}"
        remote_base = f"{remote_tmp}/{test_id}"
        batch_id = test_id

        try:
            # 创建 manifest with one uploaded task
            task = TaskRecord(
                task_id="echo_test",
                batch_id=batch_id,
                task_files=["inputs/test.txt"],
                remote_job_dir=f"{remote_base}/echo_test",
                remote_task_files=["test.txt"],
                rendered_command="echo 'JobDesk M5 integration test ok'",
                status=TaskStatus.uploaded,
            )
            submitter = JobSubmitter(
                tasks=[task],
                ssh=ssh,
                sftp=sftp,
                max_parallel=1,
                remote_batch_dir=remote_base,
                batch_id=batch_id,
            )
            result = submitter.submit_batch(SubmitMode.all)

            assert not result.errors, f"submitter errors: {result.errors}"
            assert result.submitted_task_count == 1
            assert result.updated_task_ids == ["echo_test"]

            exit_code_path = f"{remote_base}/echo_test/.jobdesk_exit_code"
            for _ in range(50):
                status = ssh.run(f"test -f {exit_code_path!r} && cat {exit_code_path!r}")
                if status.exit_code == 0:
                    assert status.stdout.strip() == "0"
                    break
                time.sleep(0.2)
            else:
                pytest.fail("submitted echo task did not complete within 10 seconds")
        finally:
            cleanup_remote_test_dir(ssh, remote_base, remote_tmp)
            sftp.close()
            ssh.close()
