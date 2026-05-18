"""M8.6B 测试: shared_files + M8.6A grouped execution hardening."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from jobdesk_app.config.schema import (
    SharedFilesUploadConfig, MissingUploadPatternPolicy, UploadConfig,
    ServerConfig,
)
from jobdesk_app.core.shared_files import select_shared_files
from jobdesk_app.core.models import SharedFileRecord
from jobdesk_app.core.transfer import TransferRecord, TransferDirection, TransferStatus
from jobdesk_app.core.template import render_command


# ==========================================================================
# SharedFilesUploadConfig schema
# ==========================================================================

class TestSharedFilesSchema:
    def test_default_values(self):
        cfg = SharedFilesUploadConfig()
        assert cfg.base_dir == "."
        assert cfg.include == []
        assert cfg.exclude == []
        assert cfg.target_subdir == "_shared"
        assert cfg.on_missing == MissingUploadPatternPolicy.error

    def test_upload_config_defaults(self):
        cfg = UploadConfig()
        assert cfg.task_files is None
        assert cfg.shared_files is None

    def test_upload_config_with_shared(self):
        cfg = UploadConfig(shared_files={"include": ["*"]})
        assert cfg.shared_files is not None
        assert cfg.shared_files.include == ["*"]


# ==========================================================================
# shared file selection
# ==========================================================================

class TestSelectSharedFiles:
    def test_no_config_returns_empty(self):
        assert select_shared_files(Path("."), None) == []

    def test_empty_include_returns_empty(self):
        cfg = SharedFilesUploadConfig(include=[])
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "a.txt").write_text("")
            assert select_shared_files(root, cfg) == []

    def test_include_selects_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "a.txt").write_text("")
            (root / "shared" / "b.txt").write_text("")

            cfg = SharedFilesUploadConfig(base_dir="shared", include=["*.txt"])
            records = select_shared_files(root, cfg)
            assert len(records) == 2

    def test_exclude_removes_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "a.txt").write_text("")
            (root / "shared" / "a.tmp").write_text("")

            cfg = SharedFilesUploadConfig(
                base_dir="shared", include=["*"], exclude=["*.tmp"])
            records = select_shared_files(root, cfg)
            assert len(records) == 1
            assert records[0].remote_name == "a.txt"

    def test_exclude_zero_match_no_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "a.txt").write_text("")
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["*"], exclude=["*.nonexistent"])
            records = select_shared_files(root, cfg)
            assert len(records) == 1

    def test_on_missing_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["*.nonexistent"])
            with pytest.raises(ValueError, match="均未匹配"):
                select_shared_files(root, cfg)

    def test_on_missing_warn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["*.nonexistent"], on_missing="warn")
            with pytest.warns(UserWarning, match="均未匹配"):
                records = select_shared_files(root, cfg)
            assert records == []

    def test_on_missing_ignore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["*.nonexistent"], on_missing="ignore")
            records = select_shared_files(root, cfg)
            assert records == []

    def test_base_dir_not_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = SharedFilesUploadConfig(base_dir="nonexistent", include=["*"])
            with pytest.raises(ValueError, match="base_dir.*不存在"):
                select_shared_files(root, cfg)

    def test_base_dir_not_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "file.txt").write_text("")
            cfg = SharedFilesUploadConfig(base_dir="file.txt", include=["*"])
            with pytest.raises(ValueError, match="不是目录"):
                select_shared_files(root, cfg)

    def test_only_selects_files_not_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "subdir").mkdir()
            (root / "shared" / "a.txt").write_text("")
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["**/*"])
            records = select_shared_files(root, cfg)
            names = {r.remote_name for r in records}
            assert "subdir" not in names  # directory not selected
            assert "a.txt" in names

    def test_stable_ordering(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "c.txt").write_text("")
            (root / "shared" / "a.txt").write_text("")
            (root / "shared" / "b.txt").write_text("")
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["*"])
            r1 = select_shared_files(root, cfg)
            r2 = select_shared_files(root, cfg)
            assert [x.remote_name for x in r1] == ["a.txt", "b.txt", "c.txt"]
            assert [x.remote_name for x in r1] == [x.remote_name for x in r2]

    def test_remote_name_posix_style(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "sub").mkdir()
            (root / "shared" / "sub" / "a.txt").write_text("")
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["**/*"])
            records = select_shared_files(root, cfg)
            assert records[0].remote_name == "sub/a.txt"
            assert "\\" not in records[0].remote_name

    def test_remote_name_no_dotdot(self):
        # base_dir prevents .. naturally; test that validation catches it
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            outside = root.parent / "outside_shared"
            outside.mkdir(exist_ok=True)
            try:
                (outside / "secret.txt").write_text("")
                cfg = SharedFilesUploadConfig(base_dir="../outside_shared", include=["*"])
                with pytest.raises(ValueError, match="project"):
                    select_shared_files(root, cfg)
            finally:
                (outside / "secret.txt").unlink(missing_ok=True)
                outside.rmdir()

    def test_relative_path_equals_remote_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "shared").mkdir()
            (root / "shared" / "a.txt").write_text("")
            cfg = SharedFilesUploadConfig(base_dir="shared", include=["*"])
            records = select_shared_files(root, cfg)
            assert records[0].relative_path == records[0].remote_name == "a.txt"


# ==========================================================================
# SharedFileRecord model
# ==========================================================================

class TestSharedFileRecord:
    def test_create_record(self):
        r = SharedFileRecord(local_path="/a/b.txt", relative_path="b.txt", remote_name="b.txt")
        assert r.local_path == "/a/b.txt"
        assert r.remote_name == "b.txt"


# ==========================================================================
# Template shared_dir / shared_dir_abs
# ==========================================================================

class TestSharedDirTemplate:
    def test_shared_dir_rendered(self):
        result = render_command(
            "ls {shared_dir}",
            {"shared_dir": "../_shared", "shared_dir_abs": "/remote/b1/_shared",
             "task_id": "t1", "job_dir": "/remote/b1/t1",
             "input_file": "in/t1.gjf", "input_name": "t1.gjf",
             "stem": "t1", "batch_id": "b1"}
        )
        assert "../_shared" in result

    def test_shared_dir_abs_rendered(self):
        result = render_command(
            "cat {shared_dir_abs}/config.yaml",
            {"shared_dir": "../_shared", "shared_dir_abs": "/remote/b1/_shared",
             "task_id": "t1", "job_dir": "/remote/b1/t1",
             "input_file": "in/t1.gjf", "input_name": "t1.gjf",
             "stem": "t1", "batch_id": "b1"}
        )
        assert "/remote/b1/_shared/config.yaml" in result

    def test_target_subdir_custom(self):
        result = render_command(
            "source {shared_dir}/confflow.yaml",
            {"shared_dir": "../_conf", "shared_dir_abs": "/remote/b1/_conf",
             "task_id": "t1", "job_dir": "/remote/b1/t1",
             "input_file": "in/t1.gjf", "input_name": "t1.gjf",
             "stem": "t1", "batch_id": "b1"}
        )
        assert "../_conf/confflow.yaml" in result


# ==========================================================================
# TransferRecord category
# ==========================================================================

class TestTransferCategory:
    def test_category_default(self):
        r = TransferRecord(direction=TransferDirection.upload, local_path="a", remote_path="b")
        assert r.category == "task"

    def test_category_shared(self):
        r = TransferRecord(direction=TransferDirection.upload, local_path="a", remote_path="b", category="shared")
        assert r.category == "shared"


# ==========================================================================
# M8.6A grouped execution test hardening
# ==========================================================================

class TestM8AGroupedExecution:
    def test_create_batch_mixed_profile_no_fail(self):
        """create_batch with mixed profiles should succeed."""
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs" / "g16").mkdir(parents=True)
            (proj_dir / "inputs" / "orca").mkdir(parents=True)
            (proj_dir / "inputs" / "g16" / "a.gjf").write_text("")
            (proj_dir / "inputs" / "orca" / "b.inp").write_text("")
            yaml_data = {
                "project_id": "tp",
                "project": {"name": "test"},
                "local_paths": {"input_dir": "./inputs"},
                "task_discoveries": [
                    {"name": "g16_jobs", "mode": "flat_single", "entry_glob": "g16/*.gjf", "execution_profile": "g16"},
                    {"name": "orca_jobs", "mode": "flat_single", "entry_glob": "orca/*.inp", "execution_profile": "orca"},
                ],
                "execution_profiles": {
                    "g16": {"label": "G16", "command": "g16 {input_name}"},
                    "orca": {"label": "ORCA", "command": "orca {input_name}"},
                },
                "submit": {"shell": "bash"},
            }
            (proj_dir / "project.yaml").write_text(yaml.safe_dump(yaml_data), encoding="utf-8")
            (base / "servers.yaml").write_text("""
servers:
  srv1: {host: h, username: u, auth_method: key}
""", encoding="utf-8")

            from jobdesk_app.services.project_service import create_project_context
            from jobdesk_app.services.batch_service import discover_task_packages, create_batch
            from jobdesk_app.config.runtime import ResolvedExecutionContext
            ctx = create_project_context(proj_dir, base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            assert len(pkgs) == 2
            assert len(pkgs) == 2
            profiles = {p.execution_profile for p in pkgs}
            assert profiles == {"g16", "orca"}

            rctx = {
                "g16": ResolvedExecutionContext(
                    project_id="tp", execution_profile_name="g16",
                    server_id="srv1", server_config=ServerConfig(server_id="srv1", host="h", username="u"),
                    remote_work_dir="/remote/g16", command_template="g16 {input_name}", max_parallel=4,
                ),
                "orca": ResolvedExecutionContext(
                    project_id="tp", execution_profile_name="orca",
                    server_id="srv1", server_config=ServerConfig(server_id="srv1", host="h", username="u"),
                    remote_work_dir="/remote/orca", command_template="orca {input_name}", max_parallel=2,
                ),
            }
            # should NOT raise
            result = create_batch(ctx, pkgs, rctx)
            assert result.batch_meta.task_count == 2
            assert len(result.tasks) == 2
            # check different remote_job_dirs
            dirs = {t.remote_job_dir for t in result.tasks}
            assert any("/g16/" in d for d in dirs)
            assert any("/orca/" in d for d in dirs)

    def test_create_batch_renders_custom_shared_target_subdir(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            (proj_dir / "shared").mkdir()
            (proj_dir / "inputs" / "a.gjf").write_text("")
            (proj_dir / "shared" / "conf.yaml").write_text("")
            yaml_data = {
                "project_id": "tp",
                "project": {"name": "test"},
                "local_paths": {"input_dir": "./inputs"},
                "task_discoveries": [
                    {"name": "jobs", "mode": "flat_single", "entry_glob": "*.gjf", "execution_profile": "g16"},
                ],
                "execution_profiles": {
                    "g16": {"label": "G16", "command": "cat {shared_dir_abs}/conf.yaml"},
                },
                "upload": {"shared_files": {"base_dir": "shared", "include": ["*"], "target_subdir": "_conf"}},
            }
            (proj_dir / "project.yaml").write_text(yaml.safe_dump(yaml_data), encoding="utf-8")
            (base / "servers.yaml").write_text("servers:\n  srv1: {host: h, username: u, auth_method: key}\n", encoding="utf-8")

            from jobdesk_app.services.project_service import create_project_context
            from jobdesk_app.services.batch_service import discover_task_packages, create_batch
            from jobdesk_app.config.runtime import ResolvedExecutionContext

            ctx = create_project_context(proj_dir, base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            rctx = {"g16": ResolvedExecutionContext(
                project_id="tp", execution_profile_name="g16",
                server_id="srv1", server_config=ServerConfig(server_id="srv1", host="h", username="u"),
                remote_work_dir="/remote/g16", command_template="cat {shared_dir_abs}/conf.yaml",
                max_parallel=4,
            )}

            result = create_batch(ctx, pkgs, rctx)

            assert result.batch_meta.shared_target_subdir == "_conf"
            assert "/_conf/conf.yaml" in result.tasks[0].rendered_command

    def test_submit_grouped_by_profile(self):
        """submit_batch groups by (server_id, execution_profile, remote_work_dir)."""
        from jobdesk_app.services.workflow_service import WorkflowService
        from jobdesk_app.services.project_service import ProjectContext, create_project_context

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            (proj_dir / "project.yaml").write_text("""project_id: tp
project: {name: test}
local_paths: {input_dir: ./inputs}
task_discoveries:
  - name: all
    mode: flat_single
    entry_glob: "*.gjf"
    execution_profile: default
execution_profiles:
  default: {label: D, command: "cmd"}
submit: {shell: bash}
""", encoding="utf-8")
            (base / "servers.yaml").write_text("""
servers:
  srv1: {host: h, username: u, auth_method: key}
""", encoding="utf-8")
            ctx = create_project_context(proj_dir, base / "servers.yaml")

            from jobdesk_app.core.manifest import TaskRecord, Manifest
            from jobdesk_app.core.lifecycle import TaskStatus
            from jobdesk_app.config.runtime import ResolvedExecutionContext

            tasks = [
                TaskRecord(task_id="t1", batch_id="b1", execution_profile="g16", server_id="s1",
                           remote_work_dir="/r1", remote_job_dir="/r1/b1/t1",
                           task_files=["in/t1.gjf"], remote_task_files=["t1.gjf"],
                           rendered_command="cmd", status=TaskStatus.uploaded),
                TaskRecord(task_id="t2", batch_id="b1", execution_profile="orca", server_id="s2",
                           remote_work_dir="/r2", remote_job_dir="/r2/b1/t2",
                           task_files=["in/t2.gjf"], remote_task_files=["t2.gjf"],
                           rendered_command="cmd", status=TaskStatus.uploaded),
            ]
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            rctx = {
                "g16": ResolvedExecutionContext(
                    project_id="tp", execution_profile_name="g16",
                    server_id="s1", server_config=ServerConfig(server_id="s1", host="h", username="u"),
                    remote_work_dir="/r1", command_template="cmd", max_parallel=4,
                ),
                "orca": ResolvedExecutionContext(
                    project_id="tp", execution_profile_name="orca",
                    server_id="s2", server_config=ServerConfig(server_id="s2", host="h", username="u"),
                    remote_work_dir="/r2", command_template="cmd", max_parallel=2,
                ),
            }

            svc = WorkflowService(ctx)
            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=MagicMock(stdout="__NOT_FOUND__"))
            mock_sftp = MagicMock()

            results = svc.submit_batch(
                mp, "b1",
                ssh_factory=lambda sc: mock_ssh,
                sftp_factory=lambda sc: mock_sftp,
                resolved_contexts=rctx,
            )
            # should produce 2 results (one per profile)
            assert len(results) == 2

    def test_upload_grouped_by_server(self):
        """upload_tasks groups by server_id and handles shared_files."""
        import yaml
        from jobdesk_app.services.project_service import create_project_context
        from jobdesk_app.services.workflow_service import WorkflowService
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            (proj_dir / "shared").mkdir()
            (proj_dir / "shared" / "conf.yaml").write_text("")
            yaml_data = {
                "project_id": "tp",
                "project": {"name": "test"},
                "local_paths": {"input_dir": "./inputs"},
                "task_discoveries": [
                    {"name": "all", "mode": "flat_single", "entry_glob": "*.gjf", "execution_profile": "default"},
                ],
                "execution_profiles": {"default": {"label": "D", "command": "cmd"}},
                "submit": {"shell": "bash"},
                "upload": {"shared_files": {"base_dir": "shared", "include": ["*"]}},
            }
            (proj_dir / "project.yaml").write_text(yaml.safe_dump(yaml_data), encoding="utf-8")
            (base / "servers.yaml").write_text("""
servers:
  srv1: {host: h, username: u, auth_method: key}
""", encoding="utf-8")
            ctx = create_project_context(proj_dir, base / "servers.yaml")

            from jobdesk_app.core.manifest import TaskRecord, Manifest
            from jobdesk_app.core.lifecycle import TaskStatus

            bid = "20260101_000000"
            tasks = [
                TaskRecord(task_id="t1", batch_id=bid, execution_profile="default", server_id="srv1",
                           remote_work_dir="/r1", remote_job_dir="/r1/b1/t1",
                           task_files=["in/t1.gjf"], remote_task_files=["t1.gjf"],
                           rendered_command="cmd", status=TaskStatus.local_ready),
            ]
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            # write batch.json with shared files
            batch_dir = ctx.batches_dir / bid
            batch_dir.mkdir(parents=True)
            from jobdesk_app.core.batch import write_batch_json
            from jobdesk_app.core.models import BatchMeta
            bm = BatchMeta(project_name="test", max_parallel=4, remote_batch_dir="", task_count=1)
            bm.batch_id = bid
            bm.shared_files = [SharedFileRecord(local_path=str(base / "shared" / "conf.yaml"),
                                                  relative_path="conf.yaml", remote_name="conf.yaml")]
            write_batch_json(bm, batch_dir / "batch.json")

            svc = WorkflowService(ctx)
            mock_sftp = MagicMock()
            def _make_rec(local_path, remote_path, **kw):
                r = TransferRecord(direction=TransferDirection.upload, local_path=str(local_path), remote_path=remote_path,
                                  status=TransferStatus.transferred)
                return r
            mock_sftp.upload_file = MagicMock(side_effect=_make_rec)

            records = svc.upload_tasks(tasks, sftp_factory=lambda sid: mock_sftp,
                                        dry_run=False, batch_dir=batch_dir)
            # task file (1) + shared file (1) = 2 records
            assert len(records) == 2
            categories = {r.category for r in records}
            assert "task" in categories
            assert "shared" in categories
