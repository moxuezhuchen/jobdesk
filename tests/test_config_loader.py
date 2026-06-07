"""测试 config/schema.py - 配置数据模型校验。"""

import tempfile
from pathlib import Path

import pytest

from jobdesk_app.config.schema import (
    AuthMethod,
    ServerConfig,
)
from jobdesk_app.config.servers import load_servers


class TestServerConfig:
    """servers.yaml 解析测试。"""

    def test_valid_server_config(self):
        cfg = ServerConfig(
            server_id="wcm",
            host="example.com",
            port=22,
            username="user",
            auth_method=AuthMethod.key,
            key_path="C:/Users/user/.ssh/id_ed25519",
        )
        assert cfg.server_id == "wcm"
        assert cfg.host == "example.com"
        assert cfg.port == 22
        assert cfg.auth_method == AuthMethod.key

    def test_server_config_defaults(self):
        cfg = ServerConfig(server_id="s1", host="h", username="u")
        assert cfg.port == 22
        assert cfg.auth_method == AuthMethod.key
        assert not hasattr(cfg, "default_shell")
        assert cfg.wsl_distro is None

    def test_server_config_supports_wsl_bootstrap_distro(self):
        cfg = ServerConfig(
            server_id="wsl",
            host="127.0.0.1",
            username="root",
            wsl_distro="Ubuntu",
        )

        assert cfg.wsl_distro == "Ubuntu"

    def test_server_config_password_auth(self):
        cfg = ServerConfig(
            server_id="s2",
            host="h",
            username="u",
            auth_method=AuthMethod.password,
        )
        assert cfg.auth_method == AuthMethod.password
        assert cfg.key_path is None

    def test_server_config_invalid_port(self):
        with pytest.raises(Exception):
            ServerConfig(server_id="s", host="h", username="u", port=99999)

    def test_server_config_missing_required(self):
        with pytest.raises(Exception):
            ServerConfig(server_id="s", username="u")  # missing host

    def test_servers_config_load_yaml(self):
        yaml_content = """
servers:
  wcm:
    display_name: WCM Server
    host: example.com
    port: 22
    username: xianj
    auth_method: key
    key_path: C:/Users/xianj/.ssh/id_ed25519
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            cfg = load_servers(tmp_path)
            assert "wcm" in cfg.servers
            assert cfg.servers["wcm"].host == "example.com"
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_servers_config_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_servers(Path("/nonexistent/servers.yaml"))

    def test_servers_config_empty(self):
        yaml_content = ""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_content)
            tmp_path = f.name

        try:
            with pytest.raises(ValueError, match="为空"):
                load_servers(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)




def test_password_auth_config_loads_but_surfaces_unsupported_message():
    """Old configs with auth_method=password load but report unsupported."""
    server = ServerConfig(host="10.0.0.1", username="user", auth_method=AuthMethod.password)
    assert server.auth_unsupported_message != ""
    assert "password" in server.auth_unsupported_message

    # key auth has no warning
    server_key = ServerConfig(host="10.0.0.1", username="user", auth_method=AuthMethod.key)
    assert server_key.auth_unsupported_message == ""


def test_server_config_external_tools_defaults_to_windows_terminal():
    cfg = ServerConfig(server_id="s1", host="cluster", username="chemist")

    assert cfg.external_tools.terminal_provider == "windows_terminal"
    assert cfg.external_tools.ssh_alias == ""
    assert cfg.external_tools.putty_session == ""
    assert cfg.external_tools.terminal_path == ""


def test_server_config_external_tools_loads_explicit_values():
    cfg = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        external_tools={
            "terminal_provider": "putty",
            "ssh_alias": "cluster-a",
            "putty_session": "cluster-a-putty",
            "terminal_path": "C:/Tools/PuTTY/putty.exe",
        },
    )

    assert cfg.external_tools.terminal_provider == "putty"
    assert cfg.external_tools.ssh_alias == "cluster-a"
    assert cfg.external_tools.putty_session == "cluster-a-putty"
    assert cfg.external_tools.terminal_path == "C:/Tools/PuTTY/putty.exe"


def test_server_config_rejects_unknown_terminal_provider():
    with pytest.raises(Exception):
        ServerConfig(
            server_id="bad",
            host="cluster",
            username="chemist",
            external_tools={"terminal_provider": "unknown"},
        )


def test_server_config_ssh_access_defaults_are_empty():
    cfg = ServerConfig(server_id="s1", host="cluster", username="chemist")

    assert cfg.ssh_access.config_alias == ""
    assert cfg.ssh_access.proxy_command == ""
    assert cfg.ssh_access.proxy_jump == ""


def test_server_config_ssh_access_loads_explicit_values():
    cfg = ServerConfig(
        server_id="hpc",
        host="cluster.example.edu",
        username="chemist",
        ssh_access={
            "config_alias": "cluster-a",
            "proxy_command": "ssh -W %h:%p gateway",
            "proxy_jump": "gateway",
        },
    )

    assert cfg.ssh_access.config_alias == "cluster-a"
    assert cfg.ssh_access.proxy_command == "ssh -W %h:%p gateway"
    assert cfg.ssh_access.proxy_jump == "gateway"
