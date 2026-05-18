"""M8.5D 测试: RuntimeBinding 解析 + max_parallel override。"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from jobdesk_app.config.schema import (
    ProjectConfig,
    RuntimeBinding,
    ServerConfig,
)
from jobdesk_app.config.runtime import (
    RuntimeBindingStore,
    resolve_execution_context,
    ResolvedExecutionContext,
)

_PROJECT_CONFIG = ProjectConfig(
    project_id="test-proj",
    project={"name": "test"},
    local_paths={"input_dir": "./inputs"},
    task_discoveries=[{"name": "default", "mode": "flat_single", "entry_glob": "*.gjf", "execution_profile": "g16"}],
    execution_profiles={
        "g16": {
            "label": "Gaussian 16",
            "command": "g16 {input_name}",
            "defaults": {"max_parallel": 4},
        },
        "orca": {
            "label": "ORCA",
            "command": "orca {input_name}",
            "defaults": {"max_parallel": 2},
        },
    },
    submit={"shell": "bash"},
)


_SRV_YAML = """
servers:
  srv1:
    host: 10.0.0.1
    port: 22
    username: root
    auth_method: key
"""


def _write_servers(base: Path):
    (base / "servers.yaml").write_text(_SRV_YAML, encoding="utf-8")


def _write_bindings(base: Path, content: dict):
    p = base / "runtime_bindings.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")


class TestRuntimeBindingStore:
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store = RuntimeBindingStore(base / "test_bindings.yaml")

            binding = RuntimeBinding(
                server_id="srv1",
                remote_work_dir="/home/user/proj/g16",
                max_parallel=8,
            )
            store.save_binding("my-proj", "g16", binding)

            loaded = store.get_binding("my-proj", "g16")
            assert loaded is not None
            assert loaded.server_id == "srv1"
            assert loaded.remote_work_dir == "/home/user/proj/g16"
            assert loaded.max_parallel == 8

    def test_get_binding_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store = RuntimeBindingStore(base / "nonexistent.yaml")
            assert store.get_binding("no", "no") is None

    def test_save_without_max_parallel(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            store = RuntimeBindingStore(base / "test_bindings.yaml")

            binding = RuntimeBinding(
                server_id="srv1",
                remote_work_dir="/tmp/proj",
            )
            store.save_binding("my-proj", "orca", binding)

            loaded = store.get_binding("my-proj", "orca")
            assert loaded is not None
            assert loaded.max_parallel is None


class TestResolveExecutionContext:
    def test_resolve_with_max_parallel_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _write_servers(base)
            _write_bindings(base, {
                "bindings": {
                    "test-proj": {
                        "g16": {
                            "server_id": "srv1",
                            "remote_work_dir": "/home/user/g16",
                            "max_parallel": 8,
                        }
                    }
                }
            })

            ctx = resolve_execution_context(
                _PROJECT_CONFIG, "g16",
                binding_store=RuntimeBindingStore(base / "runtime_bindings.yaml"),
                servers_path=base / "servers.yaml",
            )
            assert ctx.max_parallel == 8
            assert ctx.server_id == "srv1"
            assert ctx.remote_work_dir == "/home/user/g16"
            assert ctx.command_template == "g16 {input_name}"
            assert ctx.server_config.host == "10.0.0.1"

    def test_resolve_without_max_parallel_uses_profile_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _write_servers(base)
            _write_bindings(base, {
                "bindings": {
                    "test-proj": {
                        "g16": {
                            "server_id": "srv1",
                            "remote_work_dir": "/home/user/g16",
                        }
                    }
                }
            })

            ctx = resolve_execution_context(
                _PROJECT_CONFIG, "g16",
                binding_store=RuntimeBindingStore(base / "runtime_bindings.yaml"),
                servers_path=base / "servers.yaml",
            )
            assert ctx.max_parallel == 4  # from profile defaults

    def test_resolve_missing_binding_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _write_servers(base)
            _write_bindings(base, {"bindings": {}})

            with pytest.raises(ValueError, match="未绑定运行时"):
                resolve_execution_context(
                    _PROJECT_CONFIG, "g16",
                    binding_store=RuntimeBindingStore(base / "runtime_bindings.yaml"),
                    servers_path=base / "servers.yaml",
                )

    def test_resolve_invalid_server_id_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _write_servers(base)
            _write_bindings(base, {
                "bindings": {
                    "test-proj": {
                        "g16": {
                            "server_id": "nonexistent",
                            "remote_work_dir": "/tmp/x",
                        }
                    }
                }
            })

            with pytest.raises(ValueError, match="nonexistent"):
                resolve_execution_context(
                    _PROJECT_CONFIG, "g16",
                    binding_store=RuntimeBindingStore(base / "runtime_bindings.yaml"),
                    servers_path=base / "servers.yaml",
                )

    def test_resolve_missing_profile_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _write_servers(base)
            _write_bindings(base, {
                "bindings": {
                    "test-proj": {
                        "unknown": {
                            "server_id": "srv1",
                            "remote_work_dir": "/tmp/x",
                        }
                    }
                }
            })

            with pytest.raises(ValueError, match="nonexistent"):
                resolve_execution_context(
                    _PROJECT_CONFIG, "nonexistent",
                    binding_store=RuntimeBindingStore(base / "runtime_bindings.yaml"),
                    servers_path=base / "servers.yaml",
                )
