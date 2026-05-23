"""测试 core/batch.py - BatchMeta 与 batch.json 读写。"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from jobdesk_app.core.batch import create_batch, read_batch_json, write_batch_json
from jobdesk_app.core.models import BatchMeta


class TestBatchMeta:
    def test_create_batch_meta(self):
        batch = BatchMeta(
            batch_id="20260511_120000",
            project_name="test",
            max_parallel=4,
            remote_batch_dir="/remote/batch",
        )
        assert batch.batch_id == "20260511_120000"
        assert batch.project_name == "test"
        assert batch.max_parallel == 4
        assert batch.status == "created"
        assert batch.task_count == 0
        assert batch.manifest_path is None

    def test_auto_batch_id(self):
        batch = BatchMeta(
            project_name="test",
            max_parallel=4,
            remote_batch_dir="/remote/batch",
        )
        assert batch.batch_id
        assert len(batch.batch_id) == 22  # YYYYMMDD_HHMMSS_ffffff
        assert batch.batch_id.count("_") == 2

    def test_auto_created_at(self):
        batch = BatchMeta(
            project_name="test",
            max_parallel=4,
            remote_batch_dir="/remote/batch",
        )
        assert isinstance(batch.created_at, datetime)

    def test_create_batch_helper(self):
        batch = create_batch(
            project_name="test",
            max_parallel=8,
            remote_batch_dir="/remote/test_batch",
            task_count=10,
            status="running",
            manifest_path="manifest.tsv",
        )
        assert batch.project_name == "test"
        assert batch.max_parallel == 8
        assert batch.task_count == 10
        assert batch.status == "running"
        assert batch.manifest_path == "manifest.tsv"

    def test_write_and_read_batch_json(self):
        batch = create_batch(
            project_name="test_project",
            max_parallel=4,
            remote_batch_dir="/remote/batch_dir",
            task_count=16,
            status="running",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "batch.json"
            write_batch_json(batch, json_path)

            assert json_path.exists()

            loaded = read_batch_json(json_path)
            assert loaded.batch_id == batch.batch_id
            assert loaded.project_name == "test_project"
            assert loaded.max_parallel == 4
            assert loaded.task_count == 16
            assert loaded.status == "running"
            assert loaded.remote_batch_dir == "/remote/batch_dir"

    def test_read_batch_json_preserves_dates(self):
        batch = create_batch(
            project_name="test",
            max_parallel=2,
            remote_batch_dir="/remote/b",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "batch.json"
            write_batch_json(batch, json_path)
            loaded = read_batch_json(json_path)
            assert loaded.batch_id == batch.batch_id
            assert isinstance(loaded.created_at, datetime)

    def test_batch_json_utf8(self):
        batch = create_batch(
            project_name="测试项目",
            max_parallel=4,
            remote_batch_dir="/remote/测试",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "batch.json"
            write_batch_json(batch, json_path)

            raw = json_path.read_text(encoding="utf-8")
            assert "测试项目" in raw
            assert "测试" in raw

            loaded = read_batch_json(json_path)
            assert loaded.project_name == "测试项目"
            assert loaded.remote_batch_dir == "/remote/测试"

class TestBatchJsonAtomicWrite:
    def test_write_replace_failure_keeps_existing_batch_json(self, monkeypatch):
        batch = create_batch(
            project_name="test",
            max_parallel=4,
            remote_batch_dir="/remote/batch_dir",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "batch.json"
            json_path.write_text('{"old": true}\n', encoding="utf-8")

            def fail_replace(self, target):
                raise RuntimeError("replace failed")

            monkeypatch.setattr(Path, "replace", fail_replace)

            with pytest.raises(RuntimeError, match="replace failed"):
                write_batch_json(batch, json_path)

            assert json_path.read_text(encoding="utf-8") == '{"old": true}\n'
            assert list(Path(tmpdir).glob("*.tmp")) == []

    def test_rewrites_use_distinct_temp_files(self, tmp_path, monkeypatch):
        batch = create_batch(
            project_name="test",
            max_parallel=4,
            remote_batch_dir="/remote/batch_dir",
        )
        path = tmp_path / "batch.json"
        replaced_from = []
        original_replace = Path.replace

        def capture_replace(self, target):
            replaced_from.append(self)
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", capture_replace)
        write_batch_json(batch, path)
        write_batch_json(batch, path)

        assert replaced_from[0] != replaced_from[1]
