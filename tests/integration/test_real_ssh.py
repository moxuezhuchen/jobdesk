# -*- coding: utf-8 -*-
"""真实 SSH 集成测试 — 默认跳过，需要环境变量触发。

环境变量:
    JOBDESK_TEST_SSH_SERVER_ID    - servers.yaml 中的 server_id
    JOBDESK_TEST_SERVERS_YAML     - servers.yaml 文件路径

示例:
    $env:JOBDESK_TEST_SERVERS_YAML = "C:\\Users\\me\\AppData\\Roaming\\JobDesk\\servers.yaml"
    $env:JOBDESK_TEST_SSH_SERVER_ID = "wcm"
    pytest tests/integration/test_real_ssh.py -v
"""

import os

import pytest

from jobdesk_app.config.servers import load_servers
from jobdesk_app.remote.ssh import SSHClientWrapper

pytestmark = pytest.mark.skipif(
    not (os.environ.get("JOBDESK_TEST_SSH_SERVER_ID") and os.environ.get("JOBDESK_TEST_SERVERS_YAML")),
    reason="需要 JOBDESK_TEST_SSH_SERVER_ID 和 JOBDESK_TEST_SERVERS_YAML 环境变量",
)


class TestRealSSH:
    """真实服务器集成测试 — 仅做只读操作。"""

    def test_connect_and_echo(self):
        servers_yaml = os.environ["JOBDESK_TEST_SERVERS_YAML"]
        server_id = os.environ["JOBDESK_TEST_SSH_SERVER_ID"]
        cfg = load_servers(servers_yaml)
        server = cfg.servers[server_id]

        with SSHClientWrapper(server, timeout=15) as ssh:
            assert ssh.test_connection() is True

            result = ssh.run("echo jobdesk-m3-test")
            assert result.exit_code == 0
            assert "jobdesk-m3-test" in result.stdout

    def test_hostname(self):
        servers_yaml = os.environ["JOBDESK_TEST_SERVERS_YAML"]
        server_id = os.environ["JOBDESK_TEST_SSH_SERVER_ID"]
        cfg = load_servers(servers_yaml)
        server = cfg.servers[server_id]

        with SSHClientWrapper(server, timeout=15) as ssh:
            result = ssh.run("hostname")
            assert result.exit_code == 0
            assert len(result.stdout) > 0
