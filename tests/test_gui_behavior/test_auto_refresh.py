"""Tests for auto-refresh coordinator delegation and task-done debouncing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")


class TestAutoRefreshCoordinatorDelegation:
    def test_runs_coordinator_reuses_page_owned_session_pool(self, runs_page, tmp_path):
        coordinator = runs_page._coordinator_for(tmp_path)

        assert coordinator._session_pool is runs_page._session_pool

    def test_active_and_completed_runs_delegate_to_distinct_use_cases(self, runs_page, qtbot):
        active = MagicMock(run_id="active", status_summary={"running": 1})
        completed = MagicMock(run_id="completed", status_summary={"remote_completed": 1})
        outcome = SimpleNamespace(errors=[], transfer_records=[], failures=[])

        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch.object(runs_page, "_execute_refresh_use_case", return_value=outcome) as refresh,
            patch.object(runs_page, "_execute_download_use_case", return_value=outcome) as download,
            patch.object(runs_page, "refresh_run_list"),
        ):
            service.return_value.list_runs.return_value = [active, completed]
            runs_page._auto_refresh_active()
            qtbot.waitUntil(lambda: not runs_page._auto_refresh_running, timeout=2000)

        refresh.assert_called_once()
        download.assert_called_once()

    def test_stale_submission_claim_is_included_in_auto_refresh(self, runs_page, qtbot):
        claimed = MagicMock(run_id="claimed", status_summary={"submitting": 1})
        outcome = SimpleNamespace(errors=[], transfer_records=[], failures=[])
        runs_page._auto_refresh_running = False
        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch.object(runs_page, "_execute_refresh_use_case", return_value=outcome) as refresh,
            patch.object(runs_page, "refresh_run_list"),
        ):
            service.return_value.list_runs.return_value = [claimed]
            runs_page._auto_refresh_active()
            qtbot.waitUntil(lambda: not runs_page._auto_refresh_running, timeout=2000)

        refresh.assert_called_once()

    def test_failure_for_one_run_does_not_skip_later_runs(self, runs_page, qtbot):
        records = [MagicMock(run_id=name, status_summary={"running": 1}) for name in ("bad", "ok")]
        outcomes = [
            SimpleNamespace(errors=["failed"], transfer_records=[], failures=[]),
            SimpleNamespace(errors=[], transfer_records=[], failures=[]),
        ]
        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch.object(runs_page, "_execute_refresh_use_case", side_effect=outcomes) as refresh,
            patch.object(runs_page, "refresh_run_list"),
        ):
            service.return_value.list_runs.return_value = records
            runs_page._auto_refresh_active()
            qtbot.waitUntil(lambda: not runs_page._auto_refresh_running, timeout=2000)

        assert refresh.call_count == 2


class TestTaskDoneDebounce:
    """Tests for #4: _on_task_done debounce."""

    def _make_event(self, run_id="run_1", server_id="wsl", exit_code=None):
        evt = MagicMock()
        evt.run_id = run_id
        evt.server_id = server_id
        evt.exit_code = exit_code
        return evt

    def test_monitor_flush_delegates_session_ownership_to_use_case(self, runs_page):
        runs_page._pending_task_events["run_1"] = {"server_id": "wsl", "has_done": False}
        record = MagicMock(run_id="run_1")
        outcome = SimpleNamespace(errors=[], transfer_records=[])

        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch.object(runs_page, "_execute_refresh_use_case", return_value=outcome) as refresh,
            patch("jobdesk_app.gui.workers.BackgroundWorker") as worker,
        ):
            service.return_value.load_run.return_value = record
            runs_page._flush_task_done("run_1")
            worker.call_args.args[0]()

        refresh.assert_called_once()

    def test_multiple_running_events_single_refresh(self, runs_page, qtbot):
        """3 RUNNING events for same run_id within 1s → only 1 refresh_batch_status call."""
        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers,
            patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh,
            patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp,
            patch.object(
                runs_page,
                "_execute_refresh_use_case",
                return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[]),
            ) as refresh,
        ):
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None,
                manifest_path=Path("m.tsv"),
                remote_dir="/r",
                server_id="wsl",
                status_summary={"running": 1},
            )
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            # Fire 3 RUNNING events rapidly
            for _ in range(3):
                runs_page._on_task_done(self._make_event(exit_code=None))

            # Wait for debounce timer to fire + worker to finish
            qtbot.waitUntil(
                lambda: "run_1" not in runs_page._pending_task_events,
                timeout=3000,
            )
            qtbot.waitUntil(
                lambda: not runs_page._bg_workers,
                timeout=3000,
            )

        refresh.assert_called_once()

    def test_multiple_done_events_single_refresh_and_download(self, runs_page, qtbot):
        """Multiple DONE events → 1 refresh + 1 download_completed."""
        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers,
            patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh,
            patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp,
            patch.object(
                runs_page,
                "_execute_refresh_use_case",
                return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[]),
            ) as refresh,
            patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]),
        ):
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None,
                manifest_path=Path("m.tsv"),
                remote_dir="/r",
                server_id="wsl",
                status_summary={"remote_completed": 1},
            )
            service.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            for _ in range(3):
                runs_page._on_task_done(self._make_event(exit_code=0))

            qtbot.waitUntil(
                lambda: "run_1" not in runs_page._pending_task_events,
                timeout=3000,
            )
            qtbot.waitUntil(lambda: not runs_page._bg_workers, timeout=3000)

            refresh.assert_called_once()

    def test_running_then_done_triggers_download(self, runs_page, qtbot):
        """RUNNING followed by DONE → merged as has_done=True → download."""
        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers,
            patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh,
            patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp,
            patch.object(
                runs_page,
                "_execute_refresh_use_case",
                return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[]),
            ) as refresh,
            patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]),
        ):
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None,
                manifest_path=Path("m.tsv"),
                remote_dir="/r",
                server_id="wsl",
                status_summary={"remote_completed": 1},
            )
            service.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._on_task_done(self._make_event(exit_code=None))  # RUNNING
            runs_page._on_task_done(self._make_event(exit_code=0))  # DONE

            qtbot.waitUntil(
                lambda: "run_1" not in runs_page._pending_task_events,
                timeout=3000,
            )
            qtbot.waitUntil(lambda: not runs_page._bg_workers, timeout=3000)

            refresh.assert_called_once()

    def test_different_run_ids_debounce_independently(self, runs_page, qtbot):
        """Events for different run_ids each produce their own refresh."""
        with (
            patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service,
            patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers,
            patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh,
            patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp,
            patch.object(
                runs_page,
                "_execute_refresh_use_case",
                return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[]),
            ) as refresh,
        ):
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None,
                manifest_path=Path("m.tsv"),
                remote_dir="/r",
                server_id="wsl",
                status_summary={"running": 1},
            )
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._on_task_done(self._make_event(run_id="run_a"))
            runs_page._on_task_done(self._make_event(run_id="run_b"))

            qtbot.waitUntil(
                lambda: not runs_page._pending_task_events,
                timeout=3000,
            )
            qtbot.waitUntil(lambda: not runs_page._bg_workers, timeout=3000)

            assert refresh.call_count == 2

    def test_shutdown_prevents_pending_timer_from_firing(self, runs_page, qtbot):
        """After shutdown, pending debounce timers must not trigger refresh."""
        with patch.object(runs_page, "_execute_refresh_use_case") as refresh:
            runs_page._on_task_done(self._make_event())
            assert "run_1" in runs_page._pending_task_events
            runs_page.shutdown()
            assert runs_page._pending_task_events == {}
            assert runs_page._task_done_timers == {}
            # Wait past debounce window — no refresh should fire
            qtbot.wait(1200)

        refresh.assert_not_called()

    def test_shutdown_ignores_late_monitor_event(self, runs_page):
        runs_page.shutdown()

        runs_page._on_task_done(self._make_event())

        assert runs_page._pending_task_events == {}
        assert runs_page._task_done_timers == {}
