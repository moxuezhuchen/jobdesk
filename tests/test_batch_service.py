"""M8.5C 测试: services/batch_service.py — 输入发现 + Batch 创建 (新 schema)。"""

import tempfile
from pathlib import Path

import pytest
import yaml

from jobdesk_app.services.project_service import ProjectContext, create_project_context
from jobdesk_app.services.batch_service import (
    discover_task_packages,
    create_batch,
    BatchCreateResult,
)
from jobdesk_app.services.errors import InputDiscoveryError
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.config.runtime import ResolvedExecutionContext
from jobdesk_app.config.schema import ServerConfig


def _make_ctx(base: Path, extra: dict | None = None) -> ProjectContext:
    proj_dir = base / "proj"
    (proj_dir / "inputs").mkdir(parents=True)
    yaml_data = """project_id: test-proj
project:
  name: test
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
"""
    _write(proj_dir / "project.yaml", yaml_data)
    _write(base / "servers.yaml", """
servers:
  s:
    host: h
    username: u
    auth_method: key
""")
    return create_project_context(proj_dir, base / "servers.yaml")


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_file(dir_path: Path, name: str, content: str = ""):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(content, encoding="utf-8")


def _make_resolved_contexts(remote_work_dir="/remote/work", max_parallel=4) -> dict:
    return {
        "default": ResolvedExecutionContext(
            project_id="test-proj",
            execution_profile_name="default",
            server_id="s",
            server_config=ServerConfig(server_id="s", host="h", username="u"),
            remote_work_dir=remote_work_dir,
            command_template="g16 {input_name}",
            max_parallel=max_parallel,
        )
    }


class TestInputDiscovery:
    def test_flat_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base)
            _make_file(ctx.local_input_dir, "mol_001.gjf")
            _make_file(ctx.local_input_dir, "mol_002.gjf")
            _make_file(ctx.local_input_dir, "other.txt")

            packages = discover_task_packages(ctx)
            assert len(packages) == 2
            assert {di.entry_file.stem for di in packages} == {"mol_001", "mol_002"}

    def test_flat_mode_id_is_stem(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base)
            _make_file(ctx.local_input_dir, "mol_001.gjf")

            packages = discover_task_packages(ctx)
            assert packages[0].task_id == "mol_001"

    def test_name_parser_regex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            _write(proj_dir / "project.yaml", r"""project_id: tp
project:
  name: test
local_paths:
  input_dir: ./inputs
task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*.gjf"
execution_profiles:
  default:
    label: D
    command: "cmd"
name_parser:
  regex: '^(?P<task_id>[a-z]+)_(?P<idx>\d+)\.gjf$'
submit:
  shell: bash
""")
            _write(base / "servers.yaml", """
servers:
  s:
    host: h
    username: u
    auth_method: key
""")
            ctx = create_project_context(proj_dir, base / "servers.yaml")
            _make_file(ctx.local_input_dir, "mol_001.gjf")
            _make_file(ctx.local_input_dir, "cat_007.gjf")

            packages = discover_task_packages(ctx)
            assert len(packages) == 2
            ids = {p.task_id for p in packages}
            assert ids == {"mol", "cat"}
            assert packages[0].parsed_fields.get("idx") in ("001", "007")

    def test_group_by(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            _write(proj_dir / "project.yaml", r"""project_id: tp
project:
  name: test
local_paths:
  input_dir: ./inputs
task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*.gjf"
execution_profiles:
  default:
    label: D
    command: "cmd"
name_parser:
  regex: '^(?P<ligand>[a-z]+)_(?P<idx>\d+)\.gjf$'
group_by:
  - ligand
submit:
  shell: bash
""")
            _write(base / "servers.yaml", """
servers:
  s:
    host: h
    username: u
    auth_method: key
""")
            ctx = create_project_context(proj_dir, base / "servers.yaml")
            _make_file(ctx.local_input_dir, "mol_001.gjf")
            _make_file(ctx.local_input_dir, "mol_002.gjf")
            _make_file(ctx.local_input_dir, "cat_001.gjf")

            packages = discover_task_packages(ctx)
            groups = {p.group_key for p in packages if p.group_key}
            assert "mol" in groups
            assert "cat" in groups

    def test_directory_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            _write(proj_dir / "project.yaml", """project_id: tp
project:
  name: test
local_paths:
  input_dir: ./inputs
task_discoveries:
  - name: default
    mode: directory
    entry_glob: "run.sh"
    task_id_from: directory_name
execution_profiles:
  default:
    label: D
    command: "bash run.sh"
submit:
  shell: bash
""")
            _write(base / "servers.yaml", """
servers:
  s:
    host: h
    username: u
    auth_method: key
""")
            ctx = create_project_context(proj_dir, base / "servers.yaml")
            _make_file(ctx.local_input_dir / "sys_A", "run.sh", "#!/bin/bash\necho A")
            _make_file(ctx.local_input_dir / "sys_B", "run.sh", "#!/bin/bash\necho B")

            packages = discover_task_packages(ctx)
            assert len(packages) == 2

    def test_empty_input_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir).mkdir(parents=True)
            _write(proj_dir / "project.yaml", """project_id: tp
project:
  name: test
local_paths:
  input_dir: ./inputs
task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*"
execution_profiles:
  default:
    label: D
    command: "cmd"
submit:
  shell: bash
""")
            _write(base / "servers.yaml", """
servers:
  s:
    host: h
    username: u
    auth_method: key
""")
            ctx = create_project_context(proj_dir, base / "servers.yaml")
            with pytest.raises(InputDiscoveryError, match="不存在"):
                discover_task_packages(ctx)


    def test_task_id_with_path_separator_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs" / "nested").mkdir(parents=True)
            _write(proj_dir / "inputs" / "nested" / "a.gjf", "")
            _write(proj_dir / "project.yaml", """
project_id: test
project: {name: test}
local_paths: {input_dir: ./inputs}
task_discoveries:
  - name: bad
    mode: flat_single
    entry_glob: "nested/*.gjf"
    task_id_prefix: "../"
execution_profiles:
  default: {label: D, command: "cmd"}
submit: {shell: bash}
""")
            _write(base / "servers.yaml", """
servers:
  s: {host: h, username: u, auth_method: key}
""")
            ctx = create_project_context(proj_dir, base / "servers.yaml")

            with pytest.raises(InputDiscoveryError, match="task_id"):
                discover_task_packages(ctx)


class TestBatchCreation:
    def test_create_batch_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base)
            _make_file(ctx.local_input_dir, "mol_001.gjf")
            _make_file(ctx.local_input_dir, "mol_002.gjf")
            packages = discover_task_packages(ctx)

            result = create_batch(ctx, packages, _make_resolved_contexts())
            assert result.batch_meta.task_count == 2
            assert result.batch_meta.max_parallel == 4
            assert result.manifest_path.exists()

            tasks = result.tasks
            assert len(tasks) == 2
            for t in tasks:
                assert t.status == TaskStatus.local_ready
                assert t.batch_id == result.batch_meta.batch_id
                assert t.rendered_command
                assert t.execution_profile == "default"

    def test_rendered_command_correct(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base)
            _make_file(ctx.local_input_dir, "mol_001.gjf")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            t = result.tasks[0]
            assert "g16" in t.rendered_command
            assert "mol_001.gjf" in t.rendered_command

    def test_remote_job_dir_is_posix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base)
            _make_file(ctx.local_input_dir, "mol_001.gjf")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            for t in result.tasks:
                assert t.remote_job_dir.startswith("/")
                assert "\\" not in t.remote_job_dir

    def test_batch_json_written(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base)
            _make_file(ctx.local_input_dir, "mol_001.gjf")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            batch_json = result.batch_dir / "batch.json"
            assert batch_json.exists()

    def test_task_id_stable_ordering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base)
            for i in range(20, 0, -1):
                _make_file(ctx.local_input_dir, f"mol_{i:03d}.gjf")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            ids = [t.task_id for t in result.tasks]
            assert ids == sorted(ids)
