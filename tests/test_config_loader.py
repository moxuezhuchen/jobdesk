"""测试 config/schema.py - 配置数据模型校验。"""

import pytest
import tempfile
from pathlib import Path
import yaml

from jobdesk_app.config.schema import (
    ServerConfig,
    ServersConfig,
    AuthMethod,
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
        assert cfg.default_shell == "bash"
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


