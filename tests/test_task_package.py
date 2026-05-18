"""M8.5A 测试: TaskPackage 模型 + 三种 task discovery + batch 创建 + 上传。"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from jobdesk_app.services.project_service import ProjectContext, create_project_context
from jobdesk_app.services.batch_service import (
    discover_task_packages,
    create_batch,
)
from jobdesk_app.services.workflow_service import WorkflowService
from jobdesk_app.services.errors import InputDiscoveryError
from jobdesk_app.core.models import TaskPackage
from jobdesk_app.core.manifest import TaskRecord, Manifest
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.config.schema import DiscoveryMode, ServerConfig
from jobdesk_app.config.runtime import ResolvedExecutionContext
from jobdesk_app.core.transfer import TransferRecord, TransferStatus


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_file(dir_path: Path, name: str, content: str = ""):
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(content, encoding="utf-8")


def _make_ctx(base: Path, overrides: dict | None = None) -> ProjectContext:
    proj_dir = base / "proj"
    (proj_dir / "inputs").mkdir(parents=True)
    td = overrides.pop("task_discovery", None) if overrides else None
    yaml_data = {
        "project_id": "test-pkg",
        "project": {"name": "test"},
        "local_paths": {"input_dir": "./inputs"},
        "execution_profiles": {"default": {"label": "D", "command": "cmd"}},
        "submit": {"shell": "bash"},
    }
    if td is not None:
        td.setdefault("name", "default")
        yaml_data["task_discoveries"] = [td]
    else:
        yaml_data["task_discoveries"] = [{"name": "default", "mode": "flat_single", "entry_glob": "*.gjf"}]
    if overrides:
        yaml_data.update(overrides)
    _write(proj_dir / "project.yaml", yaml.safe_dump(yaml_data))
    _write(base / "servers.yaml", """
servers:
  s:
    host: h
    username: u
    auth_method: key
""")
    return create_project_context(proj_dir, base / "servers.yaml")


def _make_resolved_contexts(remote_work_dir="/remote/work", max_parallel=4) -> dict:
    return {
        "default": ResolvedExecutionContext(
            project_id="test-pkg",
            execution_profile_name="default",
            server_id="s",
            server_config=ServerConfig(server_id="s", host="h", username="u"),
            remote_work_dir=remote_work_dir,
            command_template="cmd",
            max_parallel=max_parallel,
        )
    }


# ===========================================================================
# TaskPackage 模型
# ===========================================================================


class TestTaskPackageModel:
    def test_create_task_package(self):
        pkg = TaskPackage(
            task_id="mol_001",
            entry_file=Path("inputs/mol_001.gjf"),
            files=[Path("inputs/mol_001.gjf")],
            parsed_fields={"group": "mol"},
            group_key="mol",
        )
        assert pkg.task_id == "mol_001"
        assert pkg.task_dir is None
        assert pkg.entry_file == Path("inputs/mol_001.gjf")
        assert len(pkg.files) == 1

    def test_sorted_files_stable(self):
        pkg = TaskPackage(
            task_id="t1",
            files=[
                Path("c_file.txt"),
                Path("a_file.txt"),
                Path("b_file.txt"),
            ],
        )
        assert [p.name for p in pkg.sorted_files()] == ["a_file.txt", "b_file.txt", "c_file.txt"]


# ===========================================================================
# flat_single 模式
# ===========================================================================


class TestFlatSingle:
    def test_discover_flat_single_packages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {"name": "default", "mode": "flat_single", "entry_glob": "*.gjf"}
            })
            _make_file(ctx.local_input_dir, "001.gjf")
            _make_file(ctx.local_input_dir, "002.gjf")
            _make_file(ctx.local_input_dir, "other.txt")

            packages = discover_task_packages(ctx)
            assert len(packages) == 2
            ids = {p.task_id for p in packages}
            assert ids == {"001", "002"}
            for pkg in packages:
                assert len(pkg.files) == 1
                assert pkg.entry_file in pkg.files

    def test_flat_single_discover_returns_correct_task_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {"name": "default", "mode": "flat_single", "entry_glob": "*.inp", "task_id_from": "stem"}
            })
            _make_file(ctx.local_input_dir, "water.inp")
            _make_file(ctx.local_input_dir, "methane.inp")
            packages = discover_task_packages(ctx)
            assert {p.task_id for p in packages} == {"water", "methane"}

    def test_flat_single_creates_batch_with_new_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {"name": "default", "mode": "flat_single", "entry_glob": "*.gjf"},
                "submit": {"shell": "bash"},
            })
            _make_file(ctx.local_input_dir, "001.gjf")
            _make_file(ctx.local_input_dir, "002.gjf")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            tasks = result.tasks
            assert len(tasks) == 2
            for t in tasks:
                assert len(t.task_files) == 1
                assert len(t.remote_task_files) == 1
                assert t.task_files[0] == str(t.entry_file)
                assert t.status == TaskStatus.local_ready

            # verify manifest can be re-read
            loaded = Manifest.read(result.manifest_path)
            assert len(loaded) == 2
            for t in loaded:
                assert t.task_files


# ===========================================================================
# grouped_by_stem 模式
# ===========================================================================


class TestGroupedByStem:
    def test_discover_grouped_by_stem(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "grouped_by_stem",
                    "entry_glob": "*.inp",
                    "associated_globs": ["{stem}.xyz", "{stem}.constraint"],
                }
            })
            _make_file(ctx.local_input_dir, "001.inp")
            _make_file(ctx.local_input_dir, "001.xyz")
            _make_file(ctx.local_input_dir, "001.constraint")
            _make_file(ctx.local_input_dir, "002.inp")
            _make_file(ctx.local_input_dir, "002.xyz")
            _make_file(ctx.local_input_dir, "002.constraint")

            packages = discover_task_packages(ctx)
            assert len(packages) == 2
            for pkg in packages:
                assert pkg.entry_file.suffix == ".inp"
                assert len(pkg.files) == 3
                names = {f.name for f in pkg.files}
                assert len(names) == 3
                assert f"{pkg.task_id}.inp" in names
                assert f"{pkg.task_id}.xyz" in names
                assert f"{pkg.task_id}.constraint" in names

    def test_grouped_by_stem_missing_associated_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "grouped_by_stem",
                    "entry_glob": "*.inp",
                    "associated_globs": ["{stem}.xyz", "{stem}.constraint"],
                }
            })
            _make_file(ctx.local_input_dir, "001.inp")
            _make_file(ctx.local_input_dir, "001.xyz")
            # missing 001.constraint
            with pytest.raises(InputDiscoveryError, match="001"):
                discover_task_packages(ctx)

    def test_grouped_by_stem_associated_multi_match_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "grouped_by_stem",
                    "entry_glob": "*.inp",
                    "associated_globs": ["{stem}*.xyz"],
                }
            })
            _make_file(ctx.local_input_dir, "001.inp")
            _make_file(ctx.local_input_dir, "001_a.xyz")
            _make_file(ctx.local_input_dir, "001_b.xyz")
            with pytest.raises(InputDiscoveryError, match="匹配到多个文件"):
                discover_task_packages(ctx)

    def test_grouped_by_stem_files_order_stable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "grouped_by_stem",
                    "entry_glob": "*.inp",
                    "associated_globs": ["{stem}.xyz"],
                }
            })
            _make_file(ctx.local_input_dir, "001.inp")
            _make_file(ctx.local_input_dir, "001.xyz")

            # call twice - same result
            p1 = discover_task_packages(ctx)
            p2 = discover_task_packages(ctx)
            assert [f.name for f in p1[0].files] == [f.name for f in p2[0].files]

    def test_grouped_by_stem_creates_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "grouped_by_stem",
                    "entry_glob": "*.inp",
                    "associated_globs": ["{stem}.xyz"],
                },
                "submit": {"shell": "bash"},
            })
            _make_file(ctx.local_input_dir, "001.inp")
            _make_file(ctx.local_input_dir, "001.xyz")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            t = result.tasks[0]
            assert len(t.task_files) == 2
            assert "001.inp" in str(t.task_files)
            assert "001.xyz" in str(t.task_files)


# ===========================================================================
# directory 模式
# ===========================================================================


class TestDirectoryMode:
    def test_discover_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "directory",
                    "directory_glob": "*",
                    "entry_glob": "run.sh",
                    "task_id_from": "directory_name",
                }
            })
            _make_file(ctx.local_input_dir / "sys_A", "run.sh", "#!/bin/bash\necho A")
            _make_file(ctx.local_input_dir / "sys_A", "input.inp")
            _make_file(ctx.local_input_dir / "sys_A", "coord.xyz")
            _make_file(ctx.local_input_dir / "sys_B", "run.sh", "#!/bin/bash\necho B")
            _make_file(ctx.local_input_dir / "sys_B", "input.inp")

            packages = discover_task_packages(ctx)
            assert len(packages) == 2
            ids = {p.task_id for p in packages}
            assert ids == {"sys_A", "sys_B"}
            for pkg in packages:
                assert pkg.task_dir is not None
                assert pkg.entry_file is not None
                assert pkg.entry_file.name == "run.sh"
                assert len(pkg.files) >= 2

    def test_directory_missing_entry_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "directory",
                    "entry_glob": "run.sh",
                }
            })
            _make_file(ctx.local_input_dir / "sys_A", "input.inp")
            with pytest.raises(InputDiscoveryError, match="sys_A"):
                discover_task_packages(ctx)

    def test_directory_entry_multi_match_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "directory",
                    "entry_glob": "*.sh",
                }
            })
            _make_file(ctx.local_input_dir / "sys_A", "run.sh")
            _make_file(ctx.local_input_dir / "sys_A", "setup.sh")
            with pytest.raises(InputDiscoveryError, match="sys_A"):
                discover_task_packages(ctx)

    def test_directory_files_order_stable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "directory",
                    "directory_glob": "*",
                    "entry_glob": "run.sh",
                    "task_id_from": "directory_name",
                }
            })
            _make_file(ctx.local_input_dir / "sys_A", "run.sh")
            _make_file(ctx.local_input_dir / "sys_A", "b_file.txt")
            _make_file(ctx.local_input_dir / "sys_A", "a_file.txt")
            _make_file(ctx.local_input_dir / "sys_A", "c_file.txt")

            p1 = discover_task_packages(ctx)
            p2 = discover_task_packages(ctx)
            assert [f.name for f in p1[0].files] == [f.name for f in p2[0].files]
            # verify it's sorted
            names = [f.name for f in p1[0].files]
            assert names == sorted(names)

    def test_directory_with_include_glob(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "directory",
                    "directory_glob": "*",
                    "entry_glob": "run.sh",
                    "task_id_from": "directory_name",
                    "include": ["*.inp", "*.xyz"],
                }
            })
            _make_file(ctx.local_input_dir / "sys_A", "run.sh")
            _make_file(ctx.local_input_dir / "sys_A", "input.inp")
            _make_file(ctx.local_input_dir / "sys_A", "coord.xyz")
            _make_file(ctx.local_input_dir / "sys_A", "extra.log")  # should be excluded

            packages = discover_task_packages(ctx)
            assert len(packages) == 1
            names = {f.name for f in packages[0].files}
            assert "extra.log" not in names
            assert names == {"run.sh", "input.inp", "coord.xyz"}

    def test_directory_creates_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "directory",
                    "directory_glob": "*",
                    "entry_glob": "run.sh",
                    "task_id_from": "directory_name",
                },
                "submit": {"shell": "bash"},
            })
            _make_file(ctx.local_input_dir / "sys_A", "run.sh")
            _make_file(ctx.local_input_dir / "sys_A", "input.inp")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            t = result.tasks[0]
            assert len(t.task_files) == 2
            assert t.task_dir is not None
            assert "sys_A" in t.task_dir


# ===========================================================================
# Manifest 多文件列
# ===========================================================================


class TestManifestMultiFile:
    def test_manifest_write_read_multi_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            task = TaskRecord(
                task_id="t1",
                batch_id="b1",
                remote_job_dir="/r/b/t1",
                task_files=["inputs/t1.inp", "inputs/t1.xyz"],
                remote_task_files=["t1.inp", "t1.xyz"],
                task_dir="inputs/t1",
                entry_file="inputs/t1.inp",
                rendered_command="run t1.inp",
                status=TaskStatus.local_ready,
            )
            Manifest.write(mp, [task])
            loaded = Manifest.read(mp)
            assert len(loaded) == 1
            t = loaded[0]
            assert t.task_files == ["inputs/t1.inp", "inputs/t1.xyz"]
            assert t.remote_task_files == ["t1.inp", "t1.xyz"]
            assert t.task_dir == "inputs/t1"
            assert t.entry_file == "inputs/t1.inp"

    def test_manifest_empty_task_files_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            task = TaskRecord(
                task_id="t1",
                batch_id="b1",
                remote_job_dir="/r/b/t1",
                rendered_command="cmd",
            )
            Manifest.write(mp, [task])
            loaded = Manifest.read(mp)
            assert loaded[0].task_files == []
            assert loaded[0].remote_task_files == []


# ===========================================================================
# 上传多文件
# ===========================================================================


class TestUploadMultiFile:
    def test_upload_tasks_uploads_all_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {
                    "mode": "grouped_by_stem",
                    "entry_glob": "*.inp",
                    "associated_globs": ["{stem}.xyz"],
                },
                "submit": {"shell": "bash"},
            })
            _make_file(ctx.local_input_dir, "001.inp", "input data")
            _make_file(ctx.local_input_dir, "001.xyz", "coords")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            tasks = result.tasks

            # mock sftp factory
            sftp = MagicMock()
            rec = TransferRecord(
                local_path=Path("dummy"),
                remote_path="/dummy",
                direction="upload",
                status=TransferStatus.transferred,
            )
            sftp.upload_file.return_value = rec
            sftp_factory = lambda sc: sftp

            svc = WorkflowService(ctx)
            records = svc.upload_tasks(tasks, sftp_factory)
            # should have called upload_file twice (2 files)
            assert sftp.upload_file.call_count == 2
            assert len(records) == 2

    def test_upload_tasks_single_file_still_works(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx = _make_ctx(base, {
                "task_discovery": {"name": "default", "mode": "flat_single", "entry_glob": "*.gjf"},
                "submit": {"shell": "bash"},
            })
            _make_file(ctx.local_input_dir, "001.gjf", "data")
            packages = discover_task_packages(ctx)
            result = create_batch(ctx, packages, _make_resolved_contexts())
            tasks = result.tasks

            sftp = MagicMock()
            rec = TransferRecord(
                local_path=Path("dummy"),
                remote_path="/dummy",
                direction="upload",
                status=TransferStatus.transferred,
            )
            sftp.upload_file.return_value = rec
            sftp_factory = lambda sc: sftp

            svc = WorkflowService(ctx)
            records = svc.upload_tasks(tasks, sftp_factory)
            assert sftp.upload_file.call_count == 1
            assert len(records) == 1
