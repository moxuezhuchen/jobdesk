# -*- coding: utf-8 -*-
"""真实 SFTP 集成测试 — 默认跳过，需要环境变量触发。

环境变量:
    JOBDESK_TEST_SSH_SERVER_ID    - servers.yaml 中的 server_id
    JOBDESK_TEST_SERVERS_YAML     - servers.yaml 文件路径
    JOBDESK_TEST_REMOTE_TMP_DIR   - 远程可写的临时目录（如 /tmp/jobdesk_test）

示例:
    $env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\\JobDesk\\servers.yaml"
    $env:JOBDESK_TEST_SSH_SERVER_ID = "wcm"
    $env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
    pytest tests/integration/test_real_sftp.py -v
"""

import os
import uuid
import pytest
from pathlib import Path

from jobdesk_app.config.servers import load_servers
from jobdesk_app.remote.ssh import SSHClientWrapper
from jobdesk_app.remote.sftp import SFTPClientWrapper

pytestmark = pytest.mark.skipif(
    not all((
        os.environ.get("JOBDESK_TEST_SSH_SERVER_ID"),
        os.environ.get("JOBDESK_TEST_SERVERS_YAML"),
        os.environ.get("JOBDESK_TEST_REMOTE_TMP_DIR"),
    )),
    reason="需要 JOBDESK_TEST_SSH_SERVER_ID / JOBDESK_TEST_SERVERS_YAML / JOBDESK_TEST_REMOTE_TMP_DIR",
)


class TestRealSFTP:
    """真实服务器 SFTP 集成测试 — 仅做只读和临时文件操作。"""

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

    def test_upload_and_download_roundtrip(self):
        ssh, sftp, remote_tmp = self._setup()
        test_id = f"jobdesk_m4_{uuid.uuid4().hex[:8]}"
        remote_dir = f"{remote_tmp}/{test_id}"

        try:
            # 创建临时本地文件
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8",
                prefix="jobdesk_test_"
            ) as f:
                f.write("Hello from JobDesk M4 integration test!\n")
                local_file = f.name

            try:
                # 上传
                rec = sftp.upload_file(Path(local_file), f"{remote_dir}/test.txt")
                assert rec.status.value in ("transferred", "planned"), f"upload failed: {rec.reason}"

                # 下载到新位置
                with tempfile.TemporaryDirectory() as dl_dir:
                    dl_path = Path(dl_dir) / "downloaded.txt"
                    rec2 = sftp.download_file(f"{remote_dir}/test.txt", dl_path)
                    assert rec2.status.value in ("transferred", "planned"), f"download failed: {rec2.reason}"
                    content = dl_path.read_text(encoding="utf-8")
                    assert "JobDesk M4" in content
            finally:
                Path(local_file).unlink(missing_ok=True)
        finally:
            # 清理远程测试目录
            ssh.run(f"rm -rf {remote_dir}")
            sftp.close()
            ssh.close()
