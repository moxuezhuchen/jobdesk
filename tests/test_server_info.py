"""M3 测试: remote/server_info.py — 服务器状态采集 mock 测试。"""

import pytest
from unittest.mock import MagicMock, patch
import paramiko

from jobdesk_app.config.schema import ServerConfig, AuthMethod
from jobdesk_app.remote.ssh import SSHClientWrapper, SSHResult
from jobdesk_app.remote.server_info import (
    collect_server_info,
    ServerInfo,
    DiskEntry,
    ProcessEntry,
)


def _make_ssh(mock_responses: dict[str, SSHResult], raise_on_missing: bool = False):
    """创建 mock SSH client，根据命令返回预定义结果。

    Args:
        mock_responses: 命令模式 -> SSHResult 映射。
        raise_on_missing: 若 True，未匹配命令抛出异常模拟失败。
    """
    server = ServerConfig(
        server_id="t", host="h", port=22, username="u",
        auth_method=AuthMethod.key, key_path="/k",
    )
    with patch("paramiko.SSHClient") as mock_client_class, \
         patch("jobdesk_app.remote.ssh.SSHClientWrapper._resolve_key",
               return_value=MagicMock(spec=paramiko.PKey)):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        ssh = SSHClientWrapper(server)
        ssh.connect()

        def fake_run(command: str, timeout=None, check=False):
            for pattern, result in mock_responses.items():
                if pattern in command:
                    return result
            if raise_on_missing:
                raise RuntimeError(f"mock: command failed: {command!r}")
            return SSHResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.01)

        ssh.run = fake_run
        return ssh


_HOSTNAME_RESULT = SSHResult(command="hostname", exit_code=0, stdout="compute01\n", stderr="", duration_seconds=0.01)
_UPTIME_RESULT = SSHResult(
    command="uptime", exit_code=0,
    stdout=" 10:30:00 up 30 days, 2:15, 5 users, load average: 1.50, 1.20, 1.10\n",
    stderr="", duration_seconds=0.01,
)
_WHOAMI_RESULT = SSHResult(command="whoami", exit_code=0, stdout="testuser\n", stderr="", duration_seconds=0.01)
_DF_RESULT = SSHResult(
    command="df -h", exit_code=0,
    stdout="Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1       100G   60G   40G  60% /\n/dev/sdb1       200G   80G  120G  40% /data\n",
    stderr="", duration_seconds=0.01,
)
_FREE_RESULT = SSHResult(
    command="free -m", exit_code=0,
    stdout="              total        used        free\nMem:          32000       12000       20000\n",
    stderr="", duration_seconds=0.01,
)
_PS_RESULT = SSHResult(
    command="ps", exit_code=0,
    stdout="  PID %CPU %MEM     ELAPSED CMD\n 1234 50.0  5.0    10-00:00 g16 mol.gjf\n 5678 25.0  3.0       12:34 orca inp\n",
    stderr="", duration_seconds=0.01,
)
_LSCPU_RESULT = SSHResult(
    command="lscpu", exit_code=0,
    stdout="CPU(s):              64\nModel name:          Intel Xeon Gold\n",
    stderr="", duration_seconds=0.01,
)


class TestServerInfo:
    def test_full_collection(self):
        ssh = _make_ssh({
            "hostname": _HOSTNAME_RESULT,
            "uptime": _UPTIME_RESULT,
            "whoami": _WHOAMI_RESULT,
            "lscpu": _LSCPU_RESULT,
            "free -m": _FREE_RESULT,
            "df -h": _DF_RESULT,
            "ps -u": _PS_RESULT,
        })
        info = collect_server_info(ssh)
        assert info.hostname == "compute01"
        assert "load average" in info.uptime_text
        assert info.load_average == "1.50, 1.20, 1.10"
        assert info.current_user == "testuser"
        assert info.cpu_summary is not None
        assert "Intel" in info.cpu_summary
        assert info.memory_total_mb == "32000"
        assert info.memory_used_mb == "12000"
        assert info.memory_free_mb == "20000"
        assert len(info.disk_entries) == 2
        assert info.disk_entries[0].filesystem == "/dev/sda1"
        assert info.disk_entries[0].percent == "60%"
        assert info.disk_entries[1].mountpoint == "/data"
        assert len(info.user_processes) == 2
        assert info.user_processes[0].cmd == "g16 mol.gjf"
        assert info.warnings == []

    def test_partial_failure_no_crash(self):
        """某些命令失败不应崩溃，应返回 partial result + warnings。"""
        ssh = _make_ssh({
            "hostname": SSHResult(command="hostname", exit_code=0, stdout="srv\n", stderr="", duration_seconds=0.01),
            "whoami": SSHResult(command="whoami", exit_code=0, stdout="u\n", stderr="", duration_seconds=0.01),
            # uptime, df, free, ps, lscpu 全都失败
        }, raise_on_missing=True)
        info = collect_server_info(ssh)
        assert info.hostname == "srv"
        assert info.current_user == "u"
        assert len(info.warnings) >= 3  # uptime, free, df, ps, lscpu 至少 3 个失败

    def test_df_parsing(self):
        ssh = _make_ssh({"df -h": _DF_RESULT, "hostname": _HOSTNAME_RESULT,
                          "uptime": _UPTIME_RESULT, "whoami": _WHOAMI_RESULT,
                          "free -m": _FREE_RESULT, "ps -u": _PS_RESULT,
                          "lscpu": _LSCPU_RESULT})
        info = collect_server_info(ssh)
        assert len(info.disk_entries) == 2

    def test_free_parsing(self):
        ssh = _make_ssh({"free -m": _FREE_RESULT, "hostname": _HOSTNAME_RESULT,
                          "uptime": _UPTIME_RESULT, "whoami": _WHOAMI_RESULT,
                          "df -h": _DF_RESULT, "ps -u": _PS_RESULT,
                          "lscpu": _LSCPU_RESULT})
        info = collect_server_info(ssh)
        assert info.memory_total_mb == "32000"

    def test_ps_parsing(self):
        ssh = _make_ssh({"ps -u": _PS_RESULT, "hostname": _HOSTNAME_RESULT,
                          "uptime": _UPTIME_RESULT, "whoami": _WHOAMI_RESULT,
                          "df -h": _DF_RESULT, "free -m": _FREE_RESULT,
                          "lscpu": _LSCPU_RESULT})
        info = collect_server_info(ssh)
        assert len(info.user_processes) == 2
        assert info.user_processes[0].pid == "1234"

    def test_ps_max_20_processes(self):
        """ps 输出超过 20 行时只取前 20。"""
        lines = "  PID %CPU %MEM     ELAPSED CMD\n"
        for i in range(30):
            lines += f" {i:5d}  0.0  0.0       00:00 proc{i}\n"
        ps_result = SSHResult(command="ps", exit_code=0, stdout=lines, stderr="", duration_seconds=0.01)
        ssh = _make_ssh({"ps -u": ps_result, "hostname": _HOSTNAME_RESULT,
                          "uptime": _UPTIME_RESULT, "whoami": _WHOAMI_RESULT,
                          "df -h": _DF_RESULT, "free -m": _FREE_RESULT,
                          "lscpu": _LSCPU_RESULT})
        info = collect_server_info(ssh)
        assert len(info.user_processes) <= 20

    def test_chinese_utf8(self):
        hostname_r = SSHResult(command="hostname", exit_code=0, stdout="计算节点01\n", stderr="", duration_seconds=0.01)
        ssh = _make_ssh({"hostname": hostname_r, "uptime": _UPTIME_RESULT, "whoami": _WHOAMI_RESULT,
                          "df -h": _DF_RESULT, "free -m": _FREE_RESULT, "ps -u": _PS_RESULT,
                          "lscpu": _LSCPU_RESULT})
        info = collect_server_info(ssh)
        assert info.hostname == "计算节点01"
