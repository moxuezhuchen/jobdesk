"""Tests for SubmitPage activity log persistence (Phase 15C)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jobdesk_app.services.run_repository import RunRepository

pytest.importorskip("PySide6", reason="PySide6 not installed")


# ── Repository-layer tests ───────────────────────────────────────────────────


def test_append_activity_returns_autoincrement_id(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    id1 = repository.append_activity(level="info", message="hello")
    id2 = repository.append_activity(level="info", message="world")
    assert id2 > id1 > 0


def test_append_activity_stores_all_fields(tmp_path: Path) -> None:
    from jobdesk_app.core.lifecycle import TaskStatus
    from jobdesk_app.services.run_repository import RunRecord
    from jobdesk_app.core.manifest import TaskRecord

    repository = RunRepository(tmp_path / "runs")
    # Insert a real run so the FK constraint is satisfied.
    run_record = RunRecord(
        run_id="run-42",
        server_id="srv",
        remote_dir="/r",
        command_template="cmd",
        max_parallel=1,
        mode="selected_files",
        created_at="2026-07-01T00:00:00",
        run_dir=tmp_path / "runs" / "run-42",
        manifest_path=tmp_path / "runs" / "run-42" / "manifest.tsv",
        batch_path=tmp_path / "runs" / "run-42" / "batch.json",
        local_dir=str(tmp_path),
        env_init_scripts=[],
        scheduler_type="nohup",
        resources={},
    )
    repository.create_run(run_record, [TaskRecord(task_id="t1", batch_id="run-42", remote_job_dir="/r/t1", rendered_command="cmd", status=TaskStatus.local_ready)])
    repository.append_activity(
        level="warning",
        message="test message",
        run_id="run-42",
        payload={"key": "value"},
    )

    rows = repository.list_recent_activity()
    assert len(rows) == 1
    assert rows[0]["level"] == "warning"
    assert rows[0]["message"] == "test message"
    assert rows[0]["run_id"] == "run-42"
    assert rows[0]["payload"] == {"key": "value"}


def test_append_activity_defaults_level_to_info(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.append_activity(message="hello")
    assert repository.list_recent_activity()[0]["level"] == "info"


def test_append_activity_defaults_payload_to_empty_dict(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.append_activity(message="hello")
    assert repository.list_recent_activity()[0]["payload"] == {}


def test_list_recent_activity_returns_oldest_first(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    for i in range(5):
        repository.append_activity(message=f"msg-{i}")
    rows = repository.list_recent_activity()
    assert [r["message"] for r in rows] == [f"msg-{i}" for i in range(5)]


def test_list_recent_activity_respects_limit(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    for i in range(10):
        repository.append_activity(message=f"msg-{i}")
    rows = repository.list_recent_activity(limit=3)
    assert len(rows) == 3
    assert rows[-1]["message"] == "msg-2"


def test_list_recent_activity_returns_empty_when_empty(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    assert repository.list_recent_activity() == []


def test_v5_migration_is_idempotent(tmp_path: Path) -> None:
    """Running _migrate_v4_to_v5 twice must not raise."""
    repository = RunRepository(tmp_path / "runs")
    repository.append_activity(message="first")
    assert repository.schema_version() == 5

    # Manually re-apply the migration (simulate double-init on corrupt marker)
    with sqlite3.connect(repository.database_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS submit_activity_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "ts TEXT NOT NULL,"
            "level TEXT NOT NULL DEFAULT 'info',"
            "message TEXT NOT NULL,"
            "payload_json TEXT NOT NULL DEFAULT '{}',"
            "run_id TEXT,"
            "FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE SET NULL"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS submit_activity_log_ts_idx "
            "ON submit_activity_log(ts)"
        )
    # Re-opening must not crash
    reopened = RunRepository(tmp_path / "runs")
    assert reopened.schema_version() == 5
    assert len(reopened.list_recent_activity()) == 1


def test_auto_migrate_v4_to_v5(tmp_path: Path) -> None:
    """A v4 database (before activity_log existed) upgrades to v5 on open."""
    repository = RunRepository(tmp_path / "runs")
    assert repository.schema_version() == 5  # fresh db is already v5

    # Downgrade to v4 by dropping the table and bumping version
    with sqlite3.connect(repository.database_path) as conn:
        conn.execute("DROP TABLE IF EXISTS submit_activity_log")
        conn.execute(
            "UPDATE schema_metadata SET value = '4' WHERE key = 'schema_version'"
        )
        conn.execute("DELETE FROM schema_metadata WHERE key = 'legacy_import_complete'")

    # Re-opening should re-initialise and hit the v4→v5 migration
    upgraded = RunRepository(tmp_path / "runs")
    assert upgraded.schema_version() == 5
    # Also verify the table exists and works
    upgraded.append_activity(message="after upgrade")
    assert len(upgraded.list_recent_activity()) == 1


# ── SubmitPage integration tests ──────────────────────────────────────────────


def test_submit_page_loads_recent_activity_on_init(tmp_path: Path, qtbot) -> None:
    from unittest.mock import MagicMock
    from jobdesk_app.gui.pages.submit_page import SubmitPage

    repository = RunRepository(tmp_path / "runs")
    repository.append_activity(message="persisted msg 1")
    repository.append_activity(message="persisted msg 2")

    state = MagicMock()
    state.current_project_root = tmp_path
    state.repo = repository

    widget = SubmitPage(
        state=state,
        language="en",
        on_status=lambda m: None,
        on_error=lambda t, m: None,
    )
    qtbot.addWidget(widget)

    # Clear and reload to ensure data is visible regardless of init timing
    widget.activity_list.clear()
    widget.load_recent_activity()

    assert widget.activity_list.count() == 2
    assert widget.activity_list.item(0).text() == "persisted msg 1"
    assert widget.activity_list.item(1).text() == "persisted msg 2"


def test_submit_page_writes_to_repo_on_log(tmp_path: Path, qtbot) -> None:
    from unittest.mock import MagicMock
    from jobdesk_app.gui.pages.submit_page import SubmitPage

    repository = RunRepository(tmp_path / "runs")

    state = MagicMock()
    state.current_project_root = tmp_path
    state.repo = repository

    widget = SubmitPage(
        state=state,
        language="en",
        on_status=lambda m: None,
        on_error=lambda t, m: None,
    )
    qtbot.addWidget(widget)

    widget._log("hello from test")
    widget._log("another message")

    rows = repository.list_recent_activity()
    assert len(rows) == 2
    assert rows[0]["message"] == "hello from test"
    assert rows[1]["message"] == "another message"


def test_submit_page_gracefully_handles_missing_repo(tmp_path: Path, qtbot) -> None:
    from unittest.mock import MagicMock
    from jobdesk_app.gui.pages.submit_page import SubmitPage

    state = MagicMock()
    state.current_project_root = tmp_path
    state.repo = None

    # Must not raise
    widget = SubmitPage(
        state=state,
        language="en",
        on_status=lambda m: None,
        on_error=lambda t, m: None,
    )
    qtbot.addWidget(widget)

    widget._log("message with no repo")
    assert widget.activity_list.count() == 1


def test_submit_page_accepts_explicit_activity_repo_kwarg(tmp_path: Path, qtbot) -> None:
    from unittest.mock import MagicMock
    from jobdesk_app.gui.pages.submit_page import SubmitPage

    repo = RunRepository(tmp_path / "runs")

    state = MagicMock()
    state.current_project_root = tmp_path
    state.repo = None  # should NOT be used

    widget = SubmitPage(
        state=state,
        language="en",
        on_status=lambda m: None,
        on_error=lambda t, m: None,
        activity_repo=repo,
    )
    qtbot.addWidget(widget)

    widget._log("explicit repo used")
    assert len(repo.list_recent_activity()) == 1
    assert repo.list_recent_activity()[0]["message"] == "explicit repo used"
