"""M8.5C 测试: services/project_service.py — ProjectContext (新 schema)。"""

import tempfile
from pathlib import Path

import pytest
import yaml

from jobdesk_app.services.project_service import ProjectContext, create_project_context


def _write_yaml(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestProjectContext:
    def test_create_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "myproject"
            (proj_dir / "inputs").mkdir(parents=True)

            _write_yaml(proj_dir / "project.yaml", """
project_id: test-proj
project:
  name: test_project
local_paths:
  input_dir: ./inputs
  result_dir: ./results
task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*.gjf"
execution_profiles:
  default:
    label: Default
    command: "echo {input_name}"
submit:
  shell: bash
""")
            ctx = create_project_context(proj_dir)
            assert ctx.project_name == "test_project"
            assert ctx.project_id == "test-proj"
            assert ctx.local_input_dir.name == "inputs"

    def test_remote_path_not_converted_to_pathlib(self):
        # remote_work_dir no longer on ProjectContext
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "p"
            (proj_dir / "inputs").mkdir(parents=True)

            _write_yaml(proj_dir / "project.yaml", """
project_id: p
project:
  name: p
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
            ctx = create_project_context(proj_dir)
            assert ctx.project_id == "p"

    def test_jobdesk_meta_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "p"
            (proj_dir / "inputs").mkdir(parents=True)
            _write_yaml(proj_dir / "project.yaml", """
project_id: p
project:
  name: p
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
            ctx = create_project_context(proj_dir)
            assert ctx.jobdesk_meta_dir.name == ".jobdesk"
            assert ctx.batches_dir.name == "batches"
