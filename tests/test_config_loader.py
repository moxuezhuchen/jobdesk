"""测试 config/schema.py - 配置数据模型校验。"""

import pytest
import tempfile
from pathlib import Path
import yaml

from jobdesk_app.config.schema import (
    ServerConfig,
    ServersConfig,
    ProjectConfig,
    ProjectMeta,
    LocalPaths,
    TaskDiscoveryRule,
    ExecutionProfile,
    SubmitConfig,
    AuthMethod,
    DiscoveryMode,
    ExtractStrategy,
    ExtractType,
)
from jobdesk_app.config.servers import load_servers
from jobdesk_app.config.loader import load_project


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


class TestProjectConfig:
    """project.yaml 解析测试。"""

    def _minimal_config(self) -> dict:
        return {
            "project_id": "test-proj",
            "project": {"name": "test_project"},
            "local_paths": {"input_dir": "./inputs"},
            "task_discoveries": [
                {"name": "default", "mode": "flat_single", "entry_glob": "*.gjf"}
            ],
            "execution_profiles": {
                "default": {"label": "Default", "command": "g16 {input_name}"}
            },
            "submit": {"shell": "bash"},
        }

    def test_valid_minimal_config(self):
        cfg = ProjectConfig(**self._minimal_config())
        assert cfg.project.name == "test_project"
        assert cfg.project_id == "test-proj"
        assert cfg.submit.shell == "bash"

    def test_all_defaults(self):
        cfg = ProjectConfig(**self._minimal_config())
        assert cfg.local_paths.result_dir == "./results"
        assert cfg.upload.task_files is None
        assert cfg.upload.skip_if_same_size is True
        assert cfg.download.completed_only is True

    def test_full_config(self):
        data = {
            "project_id": "full-project",
            "project": {
                "name": "full_project",
                "description": "A test project",
            },
            "local_paths": {"input_dir": "./in", "result_dir": "./out"},
            "task_discoveries": [
                {
                    "name": "default",
                    "mode": "flat_single",
                    "entry_glob": "*.inp",
                    "execution_profile": "g16",
                }
            ],
            "execution_profiles": {
                "g16": {
                    "label": "Gaussian 16",
                    "command": "g16 < {entry_name} > {entry_stem}.log",
                    "requirements": {"tags": ["cpu"]},
                    "defaults": {"max_parallel": 8},
                },
                "orca": {
                    "label": "ORCA",
                    "command": "orca {input_name} > {stem}.out",
                    "requirements": {"tags": ["cpu"]},
                },
            },
            "name_parser": {"regex": "^(?P<task_id>.+)\\.inp$"},
            "group_by": ["ligand", "face"],
            "submit": {"shell": "bash"},
            "upload": {
                "task_files": ["{input_file}"],
                "shared_files": {"include": ["basis.gbs"]},
                "skip_if_same_size": True,
            },
            "download": {
                "patterns": ["*.out", "*.log"],
                "completed_only": True,
                "overwrite_policy": "deny_cross_batch",
            },
            "status": {
                "success_patterns": ["Normal termination"],
                "failure_patterns": ["Error"],
                "check_globs": ["*.out"],
            },
            "extract": {
                "results": [
                    {
                        "name": "energy",
                        "source_glob": "*.out",
                        "regex": "Energy:\\s+(?P<value>-?[\\d.]+)",
                        "strategy": "last",
                        "type": "float",
                        "unit": "hartree",
                    }
                ]
            },
            "output": {
                "relative_energy_unit": "kcal_mol",
                "hartree_to_kcal_mol": 627.509474,
            },
        }
        cfg = ProjectConfig(**data)
        assert cfg.project.description == "A test project"
        assert cfg.group_by == ["ligand", "face"]
        assert cfg.extract.results[0].name == "energy"
        assert cfg.output.hartree_to_kcal_mol == 627.509474
        assert "g16" in cfg.execution_profiles
        assert cfg.execution_profiles["g16"].command == "g16 < {entry_name} > {entry_stem}.log"
        assert cfg.execution_profiles["g16"].max_parallel == 8

    def test_missing_project_name(self):
        data = self._minimal_config()
        data["project"] = {"name": ""}
        with pytest.raises(Exception):
            ProjectConfig(**data)

    def test_missing_project_id(self):
        data = self._minimal_config()
        del data["project_id"]
        with pytest.raises(Exception):
            ProjectConfig(**data)

    def test_empty_entry_glob(self):
        data = self._minimal_config()
        data["task_discoveries"][0]["entry_glob"] = ""
        with pytest.raises(Exception):
            ProjectConfig(**data)

    def test_directory_mode(self):
        data = self._minimal_config()
        data["task_discoveries"][0]["mode"] = "directory"
        data["task_discoveries"][0]["entry_glob"] = "run.sh"
        cfg = ProjectConfig(**data)
        assert cfg.task_discoveries[0].mode == DiscoveryMode.directory
        assert cfg.task_discoveries[0].entry_glob == "run.sh"

    def test_no_program_specific_fields_in_schema(self):
        """确保 schema 字段名中没有计算程序专用字段。"""
        data = self._minimal_config()
        cfg = ProjectConfig(**data)
        cfg_dict = cfg.model_dump()
        field_names = set()

        def collect_keys(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full_key = f"{prefix}.{k}" if prefix else k
                    field_names.add(k.lower())
                    collect_keys(v, full_key)

        collect_keys(cfg_dict)
        for keyword in ["gaussian", "orca", "xtb", "g16", "g09"]:
            assert keyword not in field_names, (
                f"Schema field names contain program-specific keyword: '{keyword}'. "
                f"All field names: {sorted(field_names)}"
            )

    def test_load_project_from_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj_dir = Path(tmpdir) / "proj"
            proj_dir.mkdir()
            (proj_dir / "project.yaml").write_text("""
project_id: yaml-test
project:
  name: yaml_test

local_paths:
  input_dir: ./inputs

task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*.gjf"

execution_profiles:
  default:
    label: Default
    command: "g16 {input_name}"

submit:
  shell: bash
""", encoding="utf-8")
            cfg = load_project(proj_dir)
            assert cfg.project_id == "yaml-test"
            assert cfg.project.name == "yaml_test"

    def test_get_execution_profile(self):
        data = self._minimal_config()
        cfg = ProjectConfig(**data)
        ep = cfg.get_execution_profile("default")
        assert ep.label == "Default"
        assert ep.command == "g16 {input_name}"

    def test_get_execution_profile_missing_raises(self):
        data = self._minimal_config()
        cfg = ProjectConfig(**data)
        with pytest.raises(ValueError, match="nonexistent"):
            cfg.get_execution_profile("nonexistent")

    def test_execution_profile_must_have_command(self):
        data = self._minimal_config()
        data["execution_profiles"]["default"]["command"] = ""
        with pytest.raises(Exception):
            ProjectConfig(**data)
