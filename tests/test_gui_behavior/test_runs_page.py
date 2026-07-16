"""GUI behavior tests for the Runs Results page."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from tests.test_gui_behavior.conftest import _FakeWorker

pytest.importorskip("PySide6", reason="PySide6 not installed")


class TestRunsPage:
    def test_page_creates_without_crash(self, runs_page):
        assert runs_page is not None

    def test_refresh_use_case_delegates_to_coordinator(self, runs_page, tmp_path):
        coordinator = MagicMock()
        expected = SimpleNamespace(errors=[])
        coordinator.refresh_and_download.return_value = expected
        runs_page._coordinator_factory = MagicMock(return_value=coordinator)
        record = SimpleNamespace(run_id="run-1", local_dir=str(tmp_path))

        outcome = runs_page._execute_refresh_use_case(record, ["*.out"], download=True)

        assert outcome is expected
        coordinator.refresh_and_download.assert_called_once_with("run-1", ["*.out"])

    def test_table_has_correct_columns(self, runs_page):
        table = runs_page.table
        assert table.columnCount() == 6

    def test_buttons_exist(self, runs_page):
        assert runs_page.retry_btn is not None
        assert runs_page.stop_btn is not None
        assert runs_page.delete_btn is not None

    def test_runs_results_buttons_have_feedback_roles(self, runs_page):
        from jobdesk_app.gui.button_feedback import ButtonRole

        assert runs_page.retry_btn.property("buttonRole") == ButtonRole.PRIMARY_ACTION.value
        assert runs_page.stop_btn.property("buttonRole") == ButtonRole.DANGER_ACTION.value
        assert runs_page.retry_dl_btn.property("buttonRole") == ButtonRole.TRANSFER_ACTION.value
        assert runs_page.delete_btn.property("buttonRole") == ButtonRole.DANGER_ACTION.value

    def test_runs_results_delete_feedback_pending_disables_delete(self, runs_page):
        idle_text = runs_page.delete_btn.text()

        runs_page._delete_feedback.pending("Deleting...")

        assert runs_page.delete_btn.text() == "Deleting..."
        assert not runs_page.delete_btn.isEnabled()

        runs_page._delete_feedback.restore()

        assert runs_page.delete_btn.text() == idle_text
        assert runs_page.delete_btn.isEnabled()

    def test_retry_failed_sync_submit_error_sets_feedback_error(self, runs_page):
        from jobdesk_app.gui.i18n import tr

        record = MagicMock(run_id="run_retry")

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_submit_record", side_effect=RuntimeError("boom")):
            svc.return_value.prepare_retry_failed.return_value = 1

            runs_page._retry_failed()

        assert runs_page.retry_btn.text() == tr("Retry failed", runs_page._language)
        assert runs_page.retry_btn.property("feedbackState") == "error"
        assert not runs_page.retry_btn.isEnabled()

        runs_page._retry_feedback.restore()

        assert runs_page.retry_btn.text() == tr("Retry Failed", runs_page._language)
        assert runs_page.retry_btn.isEnabled()

    def test_retry_submit_result_errors_set_feedback_error(self, runs_page):
        from jobdesk_app.gui.i18n import tr

        statuses = []
        feedback = MagicMock()
        runs_page._status_cb = statuses.append
        result = SimpleNamespace(batch_id="run_retry", errors=["chmod failed"])

        runs_page._on_submit_done(result, feedback=feedback)

        feedback.error.assert_called_once_with(tr("Retry failed", runs_page._language))
        feedback.success.assert_not_called()
        assert any("chmod failed" in status for status in statuses)

    def test_runs_feedback_pending_uses_current_language(self, runs_page):
        from jobdesk_app.gui.i18n import tr

        assert tr("Retrying...", "zh") != "Retrying..."

        runs_page.apply_language("zh")

        runs_page._retry_feedback.pending(tr("Retrying...", runs_page._language))

        assert runs_page.retry_btn.text() == tr("Retrying...", "zh")

        runs_page._retry_feedback.restore()

        assert runs_page.retry_btn.text() == tr("Retry Failed", "zh")

    def test_context_menu_has_refresh(self, runs_page, qtbot):
        """Right-click context menu should contain refresh action."""
        actions = runs_page._build_context_actions()
        assert len(actions) >= 6
        assert actions[0][1] == runs_page._refresh_all

    def test_context_menu_omits_terminal_actions(self, runs_page):
        from jobdesk_app.gui.i18n import tr

        labels = [label for label, _callback in runs_page._build_context_actions()]

        assert tr("Open Terminal Here", runs_page._language) not in labels
        assert tr("Copy SSH Command", runs_page._language) not in labels
        assert tr("Copy cd Command", runs_page._language) not in labels
        assert not hasattr(runs_page, "open_terminal_btn")

    def test_compare_selected_renders_comparison_rows(self, runs_page, qtbot):
        """Comparing >=2 selected runs renders the comparison table from compare_runs."""
        from PySide6.QtWidgets import QInputDialog, QTableWidgetItem

        from jobdesk_app.services.comparison import RunComparison

        runs_page.table.blockSignals(True)
        runs_page.table.setRowCount(2)
        runs_page.table.setItem(0, 0, QTableWidgetItem("run_a"))
        runs_page.table.setItem(1, 0, QTableWidgetItem("run_b"))
        runs_page.table.selectAll()
        runs_page.table.blockSignals(False)

        comparison = RunComparison(
            rows=[
                {"run_id": "run_a", "task_id": "t", "scf_energy": -76.1, "scf_energy_rel_kcal": 0.0},
                {"run_id": "run_b", "task_id": "t", "scf_energy": -76.0, "scf_energy_rel_kcal": 62.8},
            ],
            field_names=["run_id", "task_id", "scf_energy", "scf_energy_rel_kcal"],
        )
        with patch.object(QInputDialog, "getItem", return_value=("gaussian_opt_freq", True)), \
             patch("jobdesk_app.services.analysis_profiles.AnalysisProfileStore") as store, \
             patch("jobdesk_app.services.comparison.compare_runs", return_value=comparison):
            store.return_value.list_profiles.return_value = {"gaussian_opt_freq": object()}
            runs_page._compare_selected()
            qtbot.waitUntil(lambda: runs_page.result_table.rowCount() == 2, timeout=3000)

        assert runs_page.result_table.columnCount() == 4
        assert "scf_energy_rel_kcal" in [
            runs_page.result_table.horizontalHeaderItem(c).text()
            for c in range(runs_page.result_table.columnCount())
        ]

    def test_compare_selected_requires_two_runs(self, runs_page):
        """Comparing with <2 runs shows a hint and does not crash."""
        from PySide6.QtWidgets import QTableWidgetItem

        messages = []
        runs_page._status_cb = messages.append
        runs_page.table.blockSignals(True)
        runs_page.table.setRowCount(1)
        runs_page.table.setItem(0, 0, QTableWidgetItem("only_one"))
        runs_page.table.selectRow(0)
        runs_page.table.blockSignals(False)
        runs_page._compare_selected()
        from jobdesk_app.gui.i18n import tr
        assert tr("Select at least two runs to compare", runs_page._language) in messages

    def test_refresh_run_list_empty(self, runs_page):
        """refresh_run_list should not crash with no runs."""
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
            mock_svc.return_value.list_runs.return_value = []
            runs_page.refresh_run_list()
        assert runs_page.table.rowCount() == 0

    def test_refresh_caches_record_and_selected_record_avoids_reload(self, runs_page):
        """refresh_run_list stores the RunRecord on the row; _selected_record reads it without load_run."""
        from PySide6.QtCore import Qt

        from jobdesk_app.services.run_service import RunRecord

        rec = RunRecord(
            run_id="r1", server_id="wsl", remote_dir="/r", command_template="g16 {name}",
            max_parallel=1, mode="selected_files", created_at="t",
            run_dir=Path("rd"), manifest_path=Path("m"), batch_path=Path("b"),
        )
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
            mock_svc.return_value.list_runs.return_value = [rec]
            runs_page.refresh_run_list()
        assert runs_page.table.item(0, 0).data(Qt.UserRole) is rec
        runs_page.table.selectRow(0)
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc2:
            got = runs_page._selected_record()
            mock_svc2.return_value.load_run.assert_not_called()
        assert got is rec

    def test_refresh_preserves_manual_selection(self, runs_page):
        """A manually selected run stays selected after a refresh rebuild."""
        from jobdesk_app.services.run_service import RunRecord

        def mk(rid):
            return RunRecord(run_id=rid, server_id="wsl", remote_dir="/r", command_template="g16 {name}",
                             max_parallel=1, mode="selected_files", created_at="t",
                             run_dir=Path("rd"), manifest_path=Path("m"), batch_path=Path("b"))
        runs = [mk("a"), mk("b"), mk("c")]
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
            mock_svc.return_value.list_runs.return_value = runs
            runs_page.refresh_run_list()
            runs_page.table.selectRow(1)
            runs_page.refresh_run_list()
        assert runs_page.table.item(runs_page.table.currentRow(), 0).text() == "b"

    def test_flush_task_done_skips_when_run_in_progress(self, runs_page):
        """_flush_task_done is a no-op when the run is already being processed."""
        runs_page._in_progress.add("busy")
        runs_page._pending_task_events["busy"] = {"server_id": "wsl", "has_done": True}
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
            runs_page._flush_task_done("busy")
            mock_svc.return_value.load_run.assert_not_called()

    def test_fresh_batch_id_jumps_then_manual_selection_is_kept(self, runs_page):
        """A new current_batch_id selects that run once; later refreshes keep manual selection."""
        from jobdesk_app.services.run_service import RunRecord

        def mk(rid):
            return RunRecord(run_id=rid, server_id="wsl", remote_dir="/r", command_template="g16 {name}",
                             max_parallel=1, mode="selected_files", created_at="t",
                             run_dir=Path("rd"), manifest_path=Path("m"), batch_path=Path("b"))
        runs = [mk("new"), mk("old")]
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
            mock_svc.return_value.list_runs.return_value = runs
            runs_page.state.current_batch_id = "new"
            runs_page.refresh_run_list()
            assert runs_page.table.item(runs_page.table.currentRow(), 0).text() == "new"
            runs_page.table.selectRow(1)
            runs_page.refresh_run_list()
            assert runs_page.table.item(runs_page.table.currentRow(), 0).text() == "old"

    def test_get_download_patterns_gaussian(self, runs_page):
        """Should return Gaussian patterns for g16 command."""
        record = MagicMock()
        record.command_template = "g16 {name}"
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as mock_store:
            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = GuiSettings()
            patterns = runs_page._get_download_patterns(record)
        assert "*.log" in patterns or "*.chk" in patterns

    def test_get_download_patterns_orca(self, runs_page):
        """Should return ORCA patterns for orca command."""
        record = MagicMock()
        record.command_template = "orca {name}"
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as mock_store:
            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = GuiSettings()
            patterns = runs_page._get_download_patterns(record)
        assert "*.out" in patterns or "*.gbw" in patterns

    def test_get_download_patterns_no_misdetect_from_substring(self, runs_page):
        """A command whose program merely contains a profile name must not match it."""
        record = MagicMock()
        record.command_template = "python run_orca.py {name}"
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as mock_store:
            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = GuiSettings()
            patterns = runs_page._get_download_patterns(record)
        assert patterns == [".log", ".out"]

    def test_load_result_preview_renders_confflow_summary(self, runs_page, tmp_path):
        runs_page.state.current_project_root = tmp_path
        summary_dir = tmp_path / "results" / "run001" / "water" / "water_confflow_work"
        summary_dir.mkdir(parents=True)
        (summary_dir / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 6,
            "final_conformers": 2,
            "total_duration_seconds": 10,
            "step_status_counts": {"completed": 2},
        }), encoding="utf-8")

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = tmp_path / "runs" / "run001" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="water", batch_id="run001", remote_job_dir="/tmp/.jobdesk_runs/run001/water",
               server_id="wsl", status=TaskStatus.downloaded),
        ])
        record = MagicMock(run_id="run001", command_template="confflow {name}", manifest_path=str(manifest_path))

        runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 1
        assert runs_page.result_table.item(0, 0).text() == "water"
        assert "Done" in runs_page.result_table.item(0, 1).text()
        from jobdesk_app.gui.i18n import tr
        assert tr("Execution output parsed; scientific review required", runs_page._language) in runs_page.result_label.text()

    def test_workspace_preview_uses_basename_from_remote_source(self, runs_page, tmp_path):
        runs_page.state.current_project_root = tmp_path
        (tmp_path / "water.log").write_text("output", encoding="utf-8")
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = tmp_path / "runs" / "preview" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(
                task_id="water",
                batch_id="preview",
                remote_job_dir="/tmp/jobs/water",
                remote_task_files=["/remote/source/water.gjf"],
                server_id="wsl",
                status=TaskStatus.downloaded,
            ),
        ])
        record = MagicMock(run_id="preview", manifest_path=str(manifest_path))
        parsed = MagicMock(final_energy_au=-76.1, gibbs_au=None, normal_termination=True)

        with patch("jobdesk_app.core.parsers.gaussian.parse_gaussian_log", return_value=parsed):
            rows = runs_page._analyze_workspace_files(record, tmp_path)

        assert rows[0][1] == "water.log"

    def test_workspace_preview_ignores_stale_output_for_remote_completed_task(self, runs_page, tmp_path):
        runs_page.state.current_project_root = tmp_path
        (tmp_path / "water.log").write_text("stale previous run output", encoding="utf-8")
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = tmp_path / "runs" / "preview_stale" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(
                task_id="water",
                batch_id="preview_stale",
                remote_job_dir="/tmp/jobs/water",
                remote_task_files=["/remote/source/water.gjf"],
                server_id="wsl",
                status=TaskStatus.remote_completed,
                error_message="download failed",
            ),
        ])
        record = MagicMock(run_id="preview_stale", manifest_path=str(manifest_path))

        rows = runs_page._analyze_workspace_files(record, tmp_path)

        assert rows == []
        assert Manifest.read(manifest_path)[0].status == TaskStatus.remote_completed

    def test_async_preview_collects_workspace_root_fallback(self, runs_page, tmp_path):
        runs_page.state.current_project_root = tmp_path
        record = SimpleNamespace(run_id="preview", command_template="orca", local_dir=None)

        with patch.object(runs_page, "_auto_analyze", return_value=[]), \
             patch.object(runs_page, "_analyze_workspace_files", return_value=[["water", "water.log"]]):
            payload = runs_page._collect_result_preview(record)

        assert payload == ("analysis", [["water", "water.log"]], "Result Preview - Local Files", False)

    def test_async_workspace_preview_does_not_promote_remote_completed_from_local_file(self, runs_page, tmp_path):
        runs_page.state.current_project_root = tmp_path
        (tmp_path / "water.log").write_text("output", encoding="utf-8")
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = tmp_path / "runs" / "preview" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(
                task_id="water",
                batch_id="preview",
                remote_job_dir="/tmp/jobs/water",
                remote_task_files=["/remote/source/water.gjf"],
                server_id="wsl",
                status=TaskStatus.remote_completed,
            ),
        ])
        record = SimpleNamespace(run_id="preview", command_template="orca", local_dir=None, manifest_path=str(manifest_path))
        parsed = MagicMock(final_energy_au=-76.1, gibbs_au=None, normal_termination=True)

        with patch("jobdesk_app.core.parsers.gaussian.parse_gaussian_log", return_value=parsed), \
             patch("jobdesk_app.gui.pages.runs_results_page.RunService"), \
             patch.object(runs_page, "refresh_run_list") as refresh_run_list:
            payload = runs_page._collect_result_preview(record)

        refresh_run_list.assert_not_called()
        assert payload == ("empty",)

        with patch.object(runs_page, "refresh_run_list") as refresh_run_list:
            runs_page._apply_result_preview(payload)

        refresh_run_list.assert_not_called()

    def test_large_output_is_not_parsed_in_preview_thread(self, runs_page, tmp_path):
        result_dir = tmp_path / "results" / "large" / "water"
        result_dir.mkdir(parents=True)
        log_file = result_dir / "water.log"
        with log_file.open("wb") as handle:
            handle.truncate(26 * 1024 * 1024)

        with patch("jobdesk_app.core.parsers.gaussian.parse_gaussian_log") as parser:
            rows = runs_page._auto_analyze(result_dir.parent)

        from jobdesk_app.gui.i18n import tr
        parser.assert_not_called()
        assert rows[0][3] == tr("File too large for preview", runs_page._language)

    def test_on_activated_ignores_legacy_disabled_automatic_refresh(self, runs_page, qtbot):
        from jobdesk_app.services.gui_settings import GuiSettings

        settings = GuiSettings()
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store:
            store.return_value.load.return_value = settings
            with patch.object(runs_page, "_start_monitoring") as monitor:
                runs_page.on_activated()
                qtbot.waitUntil(lambda: monitor.called, timeout=1000)

        monitor.assert_called_once_with()
        assert runs_page._refresh_timer.isActive()

    def test_on_activated_defers_refresh_and_monitor_start(self, runs_page, qtbot):
        from jobdesk_app.services.gui_settings import GuiSettings

        settings = GuiSettings(auto_refresh_interval=15)
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store, \
             patch.object(runs_page, "refresh_run_list") as refresh, \
             patch.object(runs_page, "_start_monitoring") as monitor:
            store.return_value.load.return_value = settings

            runs_page.on_activated()

            refresh.assert_not_called()
            monitor.assert_not_called()
            qtbot.waitUntil(lambda: refresh.called and monitor.called, timeout=1000)
            assert runs_page._refresh_timer.isActive()

    def test_startup_recovery_runs_only_once_per_page_lifetime(self, runs_page):
        from jobdesk_app.services.gui_settings import GuiSettings

        captured = {}

        def capture_worker(*args, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store, \
             patch("jobdesk_app.gui.pages.runs_results_page.start_context_worker", side_effect=capture_worker) as start_worker, \
             patch.object(runs_page, "refresh_run_list") as refresh, \
             patch.object(runs_page, "_start_monitoring") as monitor:
            store.return_value.load.return_value = GuiSettings()
            runs_page.start_startup_recovery()
            captured["on_finished"]()
            runs_page.start_startup_recovery()

        start_worker.assert_called_once()
        refresh.assert_not_called()
        monitor.assert_not_called()

    def test_startup_recovery_errors_are_visible_and_signal_completion(self, runs_page):
        from jobdesk_app.services.gui_settings import GuiSettings
        from jobdesk_app.services.run_coordinator import RunOperationOutcome

        messages = []
        failures = []
        finished = []
        runs_page._status_cb = messages.append
        runs_page.startup_recovery_failed.connect(failures.append)
        runs_page.startup_recovery_finished.connect(lambda: finished.append(True))
        captured = {}

        def capture_worker(*args, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store, \
             patch("jobdesk_app.gui.pages.runs_results_page.start_context_worker", side_effect=capture_worker), \
             patch.object(runs_page, "refresh_run_list") as refresh, \
             patch.object(runs_page, "_start_monitoring") as monitor:
            store.return_value.load.return_value = GuiSettings()
            runs_page.start_startup_recovery()
            assert not runs_page._refresh_timer.isActive()
            captured["on_result"](RunOperationOutcome(errors=["database locked"]))
            captured["on_finished"]()

        assert any("database locked" in message for message in messages)
        assert failures == ["database locked"]
        assert finished == [True]
        assert not runs_page._refresh_timer.isActive()
        refresh.assert_not_called()
        monitor.assert_not_called()

    def test_startup_recovery_worker_creation_failure_releases_gate(
        self, runs_page
    ):
        messages = []
        failures = []
        finished = []
        runs_page._status_cb = messages.append
        runs_page.startup_recovery_failed.connect(failures.append)
        runs_page.startup_recovery_finished.connect(lambda: finished.append(True))

        with patch(
            "jobdesk_app.gui.pages.runs_results_page.start_context_worker",
            side_effect=RuntimeError("thread unavailable"),
        ):
            runs_page.start_startup_recovery()

        assert runs_page._recovery_running is False
        assert runs_page._recovery_complete is True
        assert failures == ["thread unavailable"]
        assert finished == [True]
        assert any("thread unavailable" in message for message in messages)

    def test_activation_never_replays_operations(self, runs_page):
        from jobdesk_app.services.gui_settings import GuiSettings

        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store, \
             patch("jobdesk_app.gui.pages.runs_results_page.start_context_worker") as start_worker, \
             patch.object(runs_page, "refresh_run_list") as refresh, \
             patch.object(runs_page, "_start_monitoring") as monitor:
            store.return_value.load.return_value = GuiSettings()
            runs_page.on_activated()
            runs_page._run_deferred_activation()

        start_worker.assert_not_called()
        refresh.assert_called_once_with()
        monitor.assert_called_once_with()

    def test_shutdown_stops_pending_activation_timer(self, runs_page):
        runs_page._activation_timer.start(1000)

        runs_page.shutdown()

        assert not runs_page._activation_timer.isActive()

    def test_auto_refresh_ignores_legacy_disabled_automatic_download(self, runs_page, qtbot):
        from jobdesk_app.services.gui_settings import GuiSettings

        settings = GuiSettings()
        record = MagicMock(
            run_id="run_done",
            server_id="wsl",
            remote_dir="/remote/work",
            manifest_path=Path("manifest.tsv"),
            status_summary={"running": 1},
        )
        updated = MagicMock(status_summary={"remote_completed": 1})

        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store, \
             patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch.object(runs_page, "_execute_refresh_use_case", return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[])) as refresh, \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.txt"]):
            store.return_value.load.return_value = settings
            service.return_value.list_runs.return_value = [record]
            service.return_value.load_run.return_value = updated
            service.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._auto_refresh_active()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_auto_refresh_running", False),
                timeout=2000,
            )

        refresh.assert_called_once()

    def test_confflow_auto_progress_chain(self, runs_page, qtbot, tmp_path):
        """Full chain: running → refresh → remote_completed → download → results readable."""
        summary_dir = tmp_path / "260523-011" / "mol_1"
        summary_dir.mkdir(parents=True)
        (summary_dir / "run_summary.json").write_text(
            json.dumps({"molecule": "mol_1", "status": "completed", "energy": -123.456}),
            encoding="utf-8",
        )

        record = MagicMock(
            run_id="260523-011",
            server_id="wsl",
            remote_dir="/remote/work",
            manifest_path=tmp_path / "260523-011" / "manifest.tsv",
            status_summary={"running": 1},
        )
        updated_after_refresh = MagicMock(status_summary={"remote_completed": 1})

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch.object(runs_page, "_execute_refresh_use_case", return_value=SimpleNamespace(errors=[], transfer_records=[MagicMock()], failures=[])) as refresh, \
             patch.object(runs_page, "_get_download_patterns", return_value=["*/run_summary.json"]):
            service.return_value.list_runs.return_value = [record]
            service.return_value.load_run.return_value = updated_after_refresh
            service.return_value.download_completed.return_value = (["260523-011/mol_1/run_summary.json"], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._auto_refresh_active()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_auto_refresh_running", False),
                timeout=2000,
            )

        refresh.assert_called_once()
        summary = json.loads((summary_dir / "run_summary.json").read_text(encoding="utf-8"))
        assert summary["status"] == "completed"
        assert summary["energy"] == -123.456

    def test_delete_run_reports_failures_instead_of_claiming_success(self, runs_page, qtbot):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        messages = []
        runs_page._status_cb = messages.append
        runs_page.table.blockSignals(True)
        runs_page.table.setRowCount(1)
        runs_page.table.setItem(0, 0, QTableWidgetItem("run_locked"))
        runs_page.table.selectRow(0)
        runs_page.table.blockSignals(False)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service:
                service.return_value.delete_run.side_effect = RuntimeError("locked")
                service.return_value.list_runs.return_value = []
                runs_page._delete_run()
                qtbot.waitUntil(lambda: any("locked" in message for message in messages), timeout=2000)

        assert any("locked" in message for message in messages)
        assert not any("Deleted: 1" in message for message in messages)

    def test_delete_run_confirmation_does_not_claim_direct_outputs_are_deleted(self, runs_page):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        questions = []
        runs_page.table.blockSignals(True)
        runs_page.table.setRowCount(1)
        runs_page.table.setItem(0, 0, QTableWidgetItem("run_direct"))
        runs_page.table.selectRow(0)
        runs_page.table.blockSignals(False)

        def fake_question(_parent, _title, message, _buttons):
            questions.append(message)
            return QMessageBox.No

        with patch.object(QMessageBox, "question", side_effect=fake_question):
            runs_page._delete_run()

        assert questions
        assert "results" not in questions[0].lower()

    def test_load_result_preview_renders_multi_molecule_batch(self, runs_page, tmp_path):
        """A batch with multiple molecules shows per-molecule status table."""
        runs_page.state.current_project_root = tmp_path
        result_dir = tmp_path / "results" / "batch01"
        for mol in ("mol1", "mol2", "mol3"):
            d = result_dir / mol / f"{mol}_confflow_work"
            d.mkdir(parents=True)
            (d / "run_summary.json").write_text(json.dumps({
                "initial_conformers": 4,
                "final_conformers": 2,
                "total_duration_seconds": 5.5,
                "step_status_counts": {"completed": 1},
            }), encoding="utf-8")
        (result_dir / "mol4").mkdir(parents=True)

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = tmp_path / "runs" / "batch01" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id=mol, batch_id="batch01", remote_job_dir=f"/tmp/.jobdesk_runs/batch01/{mol}",
               server_id="wsl", status=TaskStatus.downloaded)
            for mol in ("mol1", "mol2", "mol3", "mol4")
        ])
        record = MagicMock(run_id="batch01", command_template="confflow {name}", manifest_path=str(manifest_path))
        runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 4
        assert runs_page.result_table.item(0, 0).text() == "mol1"
        assert "Done" in runs_page.result_table.item(0, 1).text()
        assert "4→2" in runs_page.result_table.item(0, 2).text()
        assert runs_page.result_table.item(3, 0).text() == "mol4"
        assert "Missing" in runs_page.result_table.item(3, 1).text()

    def test_confflow_batch_failed_task_without_local_dir_shows_failed(self, runs_page, tmp_path):
        """A failed task with no local directory still appears as Failed in results."""
        runs_page.state.current_project_root = tmp_path
        result_dir = tmp_path / "results" / "batch02"
        d = result_dir / "mol1" / "mol1_confflow_work"
        d.mkdir(parents=True)
        (d / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 3, "final_conformers": 1,
            "total_duration_seconds": 8, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = tmp_path / "runs" / "batch02" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="mol1", batch_id="batch02", remote_job_dir="/tmp/.jobdesk_runs/batch02/mol1",
               server_id="wsl", status=TaskStatus.downloaded),
            TR(task_id="mol2", batch_id="batch02", remote_job_dir="/tmp/.jobdesk_runs/batch02/mol2",
               server_id="wsl", status=TaskStatus.failed),
        ])
        record = MagicMock(run_id="batch02", command_template="confflow {name}", manifest_path=str(manifest_path))
        runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 2
        assert runs_page.result_table.item(0, 0).text() == "mol1"
        assert "Done" in runs_page.result_table.item(0, 1).text()
        assert runs_page.result_table.item(1, 0).text() == "mol2"
        assert "Failed" in runs_page.result_table.item(1, 1).text()

    def test_confflow_batch_all_failed_still_shows_batch_table(self, runs_page, tmp_path):
        """All tasks failed with no summaries — batch table still appears."""
        runs_page.state.current_project_root = tmp_path

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = tmp_path / "runs" / "batch03" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="mol1", batch_id="batch03", remote_job_dir="/tmp/.jobdesk_runs/batch03/mol1",
               server_id="wsl", status=TaskStatus.failed),
            TR(task_id="mol2", batch_id="batch03", remote_job_dir="/tmp/.jobdesk_runs/batch03/mol2",
               server_id="wsl", status=TaskStatus.failed),
        ])
        record = MagicMock(run_id="batch03", command_template="confflow {name}", manifest_path=str(manifest_path))
        runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 2
        assert "Failed" in runs_page.result_table.item(0, 1).text()
        assert "Failed" in runs_page.result_table.item(1, 1).text()
        assert "Batch" in runs_page.result_label.text()

    def test_confflow_results_found_in_default_local_folder(self, runs_page, tmp_path):
        """Summary in default_local_folder (not workspace) should show Done, not Missing."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        default_folder = tmp_path / "downloads"
        default_folder.mkdir()
        runs_page.state.current_project_root = workspace

        d = default_folder / "results" / "run04" / "mol1" / "mol1_confflow_work"
        d.mkdir(parents=True)
        (d / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 5, "final_conformers": 3,
            "total_duration_seconds": 7, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = workspace / "runs" / "run04" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="mol1", batch_id="run04", remote_job_dir="/tmp/.jobdesk_runs/run04/mol1",
               server_id="wsl", status=TaskStatus.downloaded),
        ])
        record = MagicMock(run_id="run04", command_template="confflow {name}", manifest_path=str(manifest_path))

        with patch("jobdesk_app.services.gui_settings.GuiSettingsStore") as mock_store:
            from dataclasses import replace

            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = replace(
                GuiSettings(), default_local_folder=str(default_folder)
            )
            runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 1
        assert runs_page.result_table.item(0, 0).text() == "mol1"
        assert "Done" in runs_page.result_table.item(0, 1).text()

    def test_confflow_results_found_directly_in_local_folder(self, runs_page, tmp_path):
        """Auto-download now stores ConfFlow outputs directly in local_dir."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runs_page.state.current_project_root = workspace

        d = workspace / "mol1_confflow_work"
        d.mkdir(parents=True)
        (d / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 5, "final_conformers": 3,
            "total_duration_seconds": 7, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = workspace / "runs" / "run_direct" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="mol1", batch_id="run_direct", remote_job_dir="/tmp/.jobdesk_runs/run_direct/mol1",
               server_id="wsl", status=TaskStatus.downloaded),
        ])
        record = MagicMock(
            run_id="run_direct",
            command_template="confflow {name}",
            manifest_path=str(manifest_path),
            local_dir=str(workspace),
        )

        runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 1
        assert runs_page.result_table.item(0, 0).text() == "mol1"
        assert "Done" in runs_page.result_table.item(0, 1).text()

    def test_confflow_stale_direct_summary_does_not_override_remote_completed(self, runs_page, tmp_path):
        """A stale direct summary must not mark the current task done before download succeeds."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runs_page.state.current_project_root = workspace

        d = workspace / "mol1_confflow_work"
        d.mkdir(parents=True)
        (d / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 5, "final_conformers": 3,
            "total_duration_seconds": 7, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = workspace / "runs" / "run_direct_stale" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="mol1", batch_id="run_direct_stale", remote_job_dir="/tmp/.jobdesk_runs/run_direct_stale/mol1",
               server_id="wsl", status=TaskStatus.remote_completed, error_message="download failed"),
        ])
        record = MagicMock(
            run_id="run_direct_stale",
            command_template="confflow {name}",
            manifest_path=str(manifest_path),
            local_dir=str(workspace),
        )

        runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 1
        assert runs_page.result_table.item(0, 0).text() == "mol1"
        assert "Download Failed" in runs_page.result_table.item(0, 1).text()
        assert "Done" not in runs_page.result_table.item(0, 1).text()

    def test_confflow_legacy_run_results_preferred_over_stale_direct_summary(self, runs_page, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runs_page.state.current_project_root = workspace

        stale = workspace / "mol1_confflow_work"
        stale.mkdir(parents=True)
        (stale / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 99, "final_conformers": 88,
            "total_duration_seconds": 7, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")

        current = workspace / "results" / "run_legacy" / "mol1" / "mol1_confflow_work"
        current.mkdir(parents=True)
        (current / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 5, "final_conformers": 3,
            "total_duration_seconds": 7, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = workspace / "runs" / "run_legacy" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="mol1", batch_id="run_legacy", remote_job_dir="/tmp/.jobdesk_runs/run_legacy/mol1",
               server_id="wsl", status=TaskStatus.downloaded),
        ])
        record = MagicMock(
            run_id="run_legacy",
            command_template="confflow {name}",
            manifest_path=str(manifest_path),
            local_dir=str(workspace),
        )

        runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 1
        assert runs_page.result_table.item(0, 2).text() == "5→3"

    def test_confflow_prefers_candidate_with_summary_over_empty_dir(self, runs_page, tmp_path):
        """Empty workspace result dir should not shadow valid default_local_folder results."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        default_folder = tmp_path / "downloads"
        default_folder.mkdir()
        runs_page.state.current_project_root = workspace

        (workspace / "results" / "run05").mkdir(parents=True)

        d = default_folder / "results" / "run05" / "mol1" / "mol1_confflow_work"
        d.mkdir(parents=True)
        (d / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 4, "final_conformers": 2,
            "total_duration_seconds": 6, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        from jobdesk_app.core.manifest import TaskRecord as TR

        manifest_path = workspace / "runs" / "run05" / "manifest.tsv"
        manifest_path.parent.mkdir(parents=True)
        Manifest.write(manifest_path, [
            TR(task_id="mol1", batch_id="run05", remote_job_dir="/tmp/.jobdesk_runs/run05/mol1",
               server_id="wsl", status=TaskStatus.downloaded),
        ])
        record = MagicMock(run_id="run05", command_template="confflow {name}", manifest_path=str(manifest_path))

        with patch("jobdesk_app.services.gui_settings.GuiSettingsStore") as mock_store:
            from dataclasses import replace as dc_replace

            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = dc_replace(
                GuiSettings(), default_local_folder=str(default_folder)
            )
            runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() == 1
        assert runs_page.result_table.item(0, 0).text() == "mol1"
        assert "Done" in runs_page.result_table.item(0, 1).text()

    def test_confflow_shows_download_failed_per_molecule(self, runs_page, tmp_path):
        """ConfFlow table must show Download Failed for tasks still at remote_completed with error."""
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest, TaskRecord

        manifest = tmp_path / "manifest.tsv"
        tasks = [
            TaskRecord(task_id="mol_ok", batch_id="b", remote_job_dir="/r",
                       status=TaskStatus.downloaded),
            TaskRecord(task_id="mol_fail", batch_id="b", remote_job_dir="/r",
                       status=TaskStatus.remote_completed,
                       error_message="sftp timeout"),
            TaskRecord(task_id="mol_exec_fail", batch_id="b", remote_job_dir="/r",
                       status=TaskStatus.failed,
                       error_message="ORCA crashed"),
        ]
        Manifest.write(manifest, tasks)

        record = MagicMock(
            run_id="cf_batch", manifest_path=manifest,
            command_template="confflow {name}", status_summary={},
        )

        mol_ok_dir = tmp_path / "results" / "cf_batch" / "mol_ok" / "mol_ok_confflow_work"
        mol_ok_dir.mkdir(parents=True)
        import json as _json
        (mol_ok_dir / "run_summary.json").write_text(_json.dumps({
            "initial_conformers": 10, "final_conformers": 3,
            "total_duration_seconds": 120.5, "step_status_counts": {"opt": 3},
        }), encoding="utf-8")

        runs_page._show_confflow_batch_results(record, tmp_path / "results" / "cf_batch")

        rows = []
        for r in range(runs_page.result_table.rowCount()):
            row = [runs_page.result_table.item(r, c).text() for c in range(runs_page.result_table.columnCount())]
            rows.append(row)

        assert len(rows) == 3
        assert "Done" in rows[0][1]
        assert "Download Failed" in rows[1][1]
        assert "Failed" in rows[2][1]
        assert "ORCA crashed" in rows[2][1]

    def test_retry_download_triggers_for_remote_completed_tasks(self, runs_page, qtbot):
        """Retry Download button re-downloads remote_completed tasks."""
        record = MagicMock(
            run_id="run_dl_retry", server_id="wsl",
            remote_dir="/r", manifest_path=Path("m.tsv"),
            status_summary={"remote_completed": 2},
        )

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch.object(runs_page, "_execute_download_use_case", return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[])) as download, \
             patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
            svc.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._retry_download()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_retry_dl_running", False),
                timeout=2000,
            )

        download.assert_called_once()

    def test_open_results_folder_calls_startfile(self, runs_page, tmp_path):
        """Open Results action opens the local download directory directly."""
        record = MagicMock(run_id="run_open", local_dir="")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path), \
             patch("jobdesk_app.gui.pages.runs_results_page.os") as mock_os:
            mock_os.startfile = MagicMock()
            runs_page._open_results_folder()

        mock_os.startfile.assert_called_once_with(tmp_path)

    def test_open_results_folder_missing_dir_shows_error(self, runs_page, tmp_path):
        """If results dir doesn't exist, show status message instead of crashing."""
        record = MagicMock(run_id="run_missing", local_dir="")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path / "missing"):
            runs_page._open_results_folder()

    def test_old_record_missing_new_fields_displays_normally(self, runs_page):
        """Old run records without newer fields should still display."""
        from jobdesk_app.gui.pages.runs_results_page import _format_status
        assert _format_status({"running": 1}) != ""
        assert _format_status({}) == ""

    def test_submitting_status_uses_user_facing_label(self, runs_page):
        from jobdesk_app.gui.i18n import tr
        from jobdesk_app.gui.pages.runs_results_page import _format_status

        assert _format_status({"submitting": 1}, runs_page._language) == tr(
            "Submitting", runs_page._language
        )

    def test_uncertain_status_uses_user_facing_label(self, runs_page):
        from jobdesk_app.gui.i18n import tr
        from jobdesk_app.gui.pages.runs_results_page import _format_status

        assert _format_status({"uncertain": 1}, runs_page._language) == tr(
            "Uncertain", runs_page._language
        )

    def test_uncertain_actions_hidden_without_uncertain_selection(self, runs_page):
        record = MagicMock(status_summary={"running": 1})
        with patch.object(runs_page, "_selected_record", return_value=record):
            runs_page._update_uncertain_actions()
        assert not runs_page.confirm_submitted_btn.isVisible()
        assert not runs_page.abandon_submit_btn.isVisible()

    def test_uncertain_actions_use_only_selected_uncertain_tasks(self, runs_page):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem

        runs_page.result_table.setColumnCount(1)
        runs_page.result_table.setRowCount(2)
        for row, task_id in enumerate(("a", "b")):
            item = QTableWidgetItem(task_id)
            item.setData(Qt.UserRole, (task_id, "uncertain"))
            runs_page.result_table.setItem(row, 0, item)
        runs_page.result_table.selectRow(1)

        assert runs_page._selected_uncertain_task_ids() == ["b"]

    def test_mixed_task_selection_disables_uncertain_actions(self, runs_page):
        from PySide6.QtCore import QItemSelectionModel, Qt
        from PySide6.QtWidgets import QTableWidgetItem

        runs_page.result_table.setColumnCount(1)
        runs_page.result_table.setRowCount(2)
        for row, data in enumerate((("a", "uncertain"), ("b", "running"))):
            item = QTableWidgetItem(data[0])
            item.setData(Qt.UserRole, data)
            runs_page.result_table.setItem(row, 0, item)
            runs_page.result_table.selectionModel().select(
                runs_page.result_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        with patch.object(
            runs_page,
            "_selected_record",
            return_value=MagicMock(status_summary={"uncertain": 1}),
        ):
            runs_page._update_uncertain_actions()

        assert runs_page._selected_uncertain_task_ids() == []
        assert not runs_page.confirm_submitted_btn.isEnabled()
        assert not runs_page.abandon_submit_btn.isEnabled()

    def test_abandon_confirmation_warns_remote_job_must_not_exist(self, runs_page):
        text = runs_page._abandon_confirmation_text(1)
        assert "only after confirming the remote job does not exist" in text.lower()
        runs_page._language = "zh"
        assert "确认远端作业不存在" in runs_page._abandon_confirmation_text(1)

    def test_shutdown_closes_session_pool_without_locking(self, runs_page):
        pool = MagicMock()
        runs_page._session_pool = pool
        runs_page.shutdown()
        pool.close.assert_called_once_with()

    def test_gaussian_auto_analysis_on_downloaded(self, runs_page, tmp_path):
        """Downloaded Gaussian .log triggers auto-analysis in result preview."""
        result_dir = tmp_path / "results" / "gauss_run" / "task1"
        result_dir.mkdir(parents=True)
        (result_dir / "task1.log").write_text(
            " SCF Done:  E(RHF) =   -76.123456     A.U.\n"
            " Zero-point correction=                           0.020000 (Hartree/Particle)\n"
            " Frequencies --   -50.0000    100.0000    200.0000\n"
            " Stationary point found\n"
            " Normal termination of Gaussian 16\n",
            encoding="utf-8",
        )
        record = MagicMock(
            run_id="gauss_run", manifest_path=tmp_path / "no_manifest.tsv",
            command_template="g16 {name}", status_summary={"downloaded": 1},
        )

        with patch.object(runs_page, "_workspace", return_value=tmp_path):
            runs_page._load_result_preview(record)

        from jobdesk_app.gui.i18n import tr
        assert runs_page.result_table.rowCount() >= 1
        assert runs_page.result_table.columnCount() == 8
        energy_cell = runs_page.result_table.item(0, 3)
        assert energy_cell is not None
        assert "-76.123456" in energy_cell.text()
        assert runs_page.result_table.item(0, 5).text() == "0.020000"
        assert runs_page.result_table.item(0, 6).text() == "1"
        assert runs_page.result_table.item(0, 7).text() == tr("OK", runs_page._language)

    def test_retry_download_uses_record_local_dir_not_current_workspace(self, runs_page, qtbot, tmp_path):
        """Retry Download must use record.local_dir when set, not GUI workspace."""
        local_a = tmp_path / "project_a"
        local_a.mkdir()
        record = MagicMock(
            run_id="run_ld", server_id="wsl",
            remote_dir="/r", manifest_path=Path("m.tsv"),
            local_dir=str(local_a),
            status_summary={"remote_completed": 1},
        )

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch.object(runs_page, "_execute_download_use_case", return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[])) as download, \
             patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
            svc.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._retry_download()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_retry_dl_running", False),
                timeout=2000,
            )

        assert download.call_args.args[0] is record

    def test_manual_refresh_download_uses_record_local_dir_not_current_workspace(self, runs_page, qtbot, tmp_path):
        local_a = tmp_path / "project_a"
        local_a.mkdir()
        record = MagicMock(
            run_id="run_refresh", server_id="wsl",
            remote_dir="/r", manifest_path=Path("m.tsv"),
            local_dir=str(local_a),
        )
        updated = MagicMock(status_summary={"remote_completed": 1})

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch.object(runs_page, "_execute_refresh_use_case", return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[])) as refresh, \
             patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path / "project_b"), \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
            svc.return_value.load_run.return_value = updated
            svc.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._refresh_status()
            qtbot.waitUntil(lambda: refresh.called, timeout=2000)

        assert refresh.call_args.args[0] is record

    def test_manual_refresh_worker_does_not_replace_existing_worker_reference(self, runs_page):
        existing_worker = MagicMock()
        runs_page._worker = existing_worker
        worker = MagicMock()
        record = MagicMock(run_id="run_refresh", local_dir="")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch("jobdesk_app.gui.workers.BackgroundWorker", return_value=worker):
            runs_page._refresh_status()

        assert runs_page._worker is existing_worker
        assert worker in runs_page._bg_workers
        worker.start.assert_called_once_with()

    def test_manual_refresh_without_download_reports_refreshed(self, runs_page):
        from jobdesk_app.gui.i18n import tr

        record = MagicMock(run_id="run_refresh", local_dir="")
        outcome = SimpleNamespace(errors=[], transfer_records=[], failures=[])

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_execute_refresh_use_case", return_value=outcome), \
             patch("jobdesk_app.gui.workers.BackgroundWorker") as worker:
            runs_page._refresh_status()
            message = worker.call_args.args[0]()

        assert message == tr("Refreshed", runs_page._language)

    def test_submit_worker_delegates_session_ownership_to_coordinator(self, runs_page):
        outcome = SimpleNamespace(errors=[], submit_results=[MagicMock()])

        with patch.object(runs_page, "_coordinator_for") as coordinator_factory, \
             patch("jobdesk_app.gui.pages.runs_results_page.start_context_worker") as start_worker:
            coordinator_factory.return_value.submit.return_value = outcome
            runs_page._submit_record("run-1")
            start_worker.call_args.kwargs["target"](MagicMock())

        coordinator_factory.return_value.submit.assert_called_once_with("run-1")

    def test_cancel_worker_delegates_session_ownership_to_coordinator(self, runs_page):
        from PySide6.QtWidgets import QMessageBox

        record = MagicMock(run_id="run-1", local_dir="")
        outcome = SimpleNamespace(changed_count=1, errors=[])

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch.object(runs_page, "_coordinator_for") as coordinator_factory, \
             patch("jobdesk_app.gui.pages.runs_results_page.start_context_worker") as start_worker:
            coordinator_factory.return_value.cancel.return_value = outcome
            runs_page._stop_run()
            start_worker.call_args.kwargs["target"](MagicMock())

        coordinator_factory.return_value.cancel.assert_called_once_with("run-1")

    def test_submit_cancel_worker_overlap_keeps_single_tracked_mutation(self, runs_page):
        from PySide6.QtWidgets import QMessageBox

        record = MagicMock(run_id="run-1", local_dir="")
        captured: list[dict] = []

        def capture_worker(*args, **kwargs):
            captured.append(kwargs)
            worker = _FakeWorker()
            runs_page._bg_workers.append(worker)
            return worker

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch(
                 "jobdesk_app.gui.pages.runs_results_page.start_context_worker",
                 side_effect=capture_worker,
             ):
            runs_page._submit_record("run-1")
            tracked = list(runs_page._bg_workers)
            runs_page._stop_run()

        assert len(captured) == 1
        assert runs_page._bg_workers == tracked

    def test_mutation_worker_callbacks_are_ignored_after_shutdown(self, runs_page):
        worker = _FakeWorker()
        with patch("jobdesk_app.gui.worker_utils.BackgroundWorker", return_value=worker):
            runs_page._submit_record("run-1")

        refresh = MagicMock()
        status = MagicMock()
        runs_page.refresh_run_list = refresh
        runs_page._status_cb = status
        runs_page.shutdown()
        worker.result.emit(SimpleNamespace(batch_id="run-1", errors=[]))
        worker.error.emit("late error")

        refresh.assert_not_called()
        status.assert_not_called()

    def test_mutation_gate_releases_when_worker_start_fails(self, runs_page):
        with patch(
            "jobdesk_app.gui.pages.runs_results_page.start_context_worker",
            side_effect=RuntimeError("start failed"),
        ), pytest.raises(RuntimeError, match="start failed"):
            runs_page._submit_record("run-1")

        assert runs_page._remote_mutation_running is False

    def test_shutdown_releases_pending_mutation_gate(self, runs_page):
        worker = _FakeWorker()
        with patch("jobdesk_app.gui.worker_utils.BackgroundWorker", return_value=worker):
            runs_page._submit_record("run-1")

        assert runs_page._remote_mutation_running is True
        runs_page.shutdown()

        assert runs_page._remote_mutation_running is False

    def test_cancel_worker_start_failure_restores_feedback_and_reports_error(self, runs_page):
        from PySide6.QtWidgets import QMessageBox

        record = MagicMock(run_id="run-1", local_dir="")
        statuses: list[str] = []
        runs_page._status_cb = statuses.append

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch(
                 "jobdesk_app.gui.pages.runs_results_page.start_context_worker",
                 side_effect=RuntimeError("start failed"),
             ):
            runs_page._stop_run()

        assert runs_page._remote_mutation_running is False
        assert runs_page.stop_btn.property("feedbackState") == "error"
        assert any("start failed" in status for status in statuses)

    @pytest.mark.parametrize("action", ["retry", "rerun"])
    def test_pending_mutation_rejects_prepare_before_state_change(self, runs_page, action):
        record = MagicMock(run_id="run-1", local_dir="")
        coordinator = MagicMock()
        runs_page._remote_mutation_running = True

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_coordinator_for", return_value=coordinator):
            if action == "retry":
                runs_page._retry_failed()
            else:
                runs_page._rerun_all()

        coordinator.retry_failed.assert_not_called()
        coordinator.rerun.assert_not_called()

    def test_retry_prepare_then_worker_start_failure_releases_owned_gate(self, runs_page):
        record = MagicMock(run_id="run-1", local_dir="")
        coordinator = MagicMock()
        coordinator.retry_failed.return_value = SimpleNamespace(
            changed_count=1,
            errors=[],
        )

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_coordinator_for", return_value=coordinator), \
             patch(
                 "jobdesk_app.gui.pages.runs_results_page.start_context_worker",
                 side_effect=RuntimeError("start failed"),
             ):
            runs_page._retry_failed()

        coordinator.retry_failed.assert_called_once_with("run-1")
        assert runs_page._remote_mutation_running is False

    def test_retry_download_worker_is_removed_when_finished(self, runs_page):
        worker = _FakeWorker()
        record = MagicMock(
            run_id="run_refresh",
            server_id="wsl",
            remote_dir="/r",
            local_dir="",
            status_summary={"remote_completed": 1},
        )

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch("jobdesk_app.gui.workers.BackgroundWorker", return_value=worker):
            runs_page._retry_download()

        assert worker in runs_page._bg_workers
        worker.finished.emit()
        assert worker not in runs_page._bg_workers

    def test_retry_download_delegates_session_ownership_to_use_case(self, runs_page):
        record = MagicMock(
            run_id="run_refresh",
            status_summary={"remote_completed": 1},
        )
        outcome = SimpleNamespace(errors=[], transfer_records=[], failures=[])

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_execute_download_use_case", return_value=outcome) as download, \
             patch("jobdesk_app.gui.workers.BackgroundWorker") as worker:
            runs_page._retry_download()
            worker.call_args.args[0]()

        download.assert_called_once()

    def test_open_results_uses_record_local_dir(self, runs_page, tmp_path):
        """Open Results must use record.local_dir path."""
        local_a = tmp_path / "project_a"
        local_a.mkdir()
        record = MagicMock(run_id="run_ld2", local_dir=str(local_a))

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path / "other"), \
             patch("jobdesk_app.gui.pages.runs_results_page.os") as mock_os:
            mock_os.startfile = MagicMock()
            mock_os.path = MagicMock()
            runs_page._open_results_folder()

        mock_os.startfile.assert_called_once_with(local_a)

    def test_show_paths_uses_record_local_dir(self, runs_page, tmp_path):
        local_a = tmp_path / "project_a"
        record = MagicMock(
            run_id="run_paths",
            local_dir=str(local_a),
            run_dir=tmp_path / "run-record",
            manifest_path=tmp_path / "manifest.tsv",
        )

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path / "project_b"):
            runs_page._show_paths()

        assert str(local_a) in runs_page.result_text.toPlainText()
        assert str(local_a / "results" / "run_paths") not in runs_page.result_text.toPlainText()

    def test_empty_local_dir_falls_back_to_workspace(self, runs_page, tmp_path):
        """Old records with empty local_dir should use current workspace."""
        record = MagicMock(run_id="run_old", local_dir="")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path), \
             patch("jobdesk_app.gui.pages.runs_results_page.os") as mock_os:
            mock_os.startfile = MagicMock()
            mock_os.path = MagicMock()
            runs_page._open_results_folder()

        mock_os.startfile.assert_called_once_with(tmp_path)

    def test_shutdown_stops_background_worker_with_timeout(self, runs_page):
        worker = MagicMock()
        runs_page._bg_workers = [worker]

        runs_page.shutdown()

        worker.stop_safely.assert_called_once_with(3000)

    def test_session_pool_close_is_non_blocking_for_busy_lease(self, runs_page):
        pool = MagicMock()
        runs_page._session_pool = pool
        runs_page.shutdown()
        pool.close.assert_called_once_with()

    def test_rerun_all_reports_active_task_error_without_submit(self, runs_page):
        statuses = []
        runs_page._status_cb = statuses.append
        record = MagicMock(run_id="run_active")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch.object(runs_page, "_submit_record") as submit_record:
            svc.return_value.prepare_rerun.side_effect = ValueError("cannot rerun active remote tasks: a")

            runs_page._rerun_all()

        assert len(statuses) == 1
        assert "cannot rerun active remote tasks: a" in statuses[0]
        submit_record.assert_not_called()

    def test_delete_run_uses_record_local_dir_not_current_workspace(self, runs_page, tmp_path, qtbot):
        """Deleting a run must target record.local_dir, not the active workspace."""
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        project_a = tmp_path / "project_a"
        project_a.mkdir()

        record = MagicMock(
            run_id="run_cross", local_dir=str(project_a),
        )

        runs_page.table.blockSignals(True)
        runs_page.table.setRowCount(1)
        runs_page.table.setItem(0, 0, QTableWidgetItem("run_cross"))
        runs_page.table.selectRow(0)
        runs_page.table.blockSignals(False)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch.object(runs_page, "_workspace", return_value=tmp_path / "project_b"):
            svc.return_value.load_run.return_value = record
            svc.return_value.delete_run.return_value = None
            svc.return_value.list_runs.return_value = []
            runs_page._delete_run()
            qtbot.waitUntil(lambda: any(call.args and call.args[0] == project_a for call in svc.call_args_list), timeout=2000)

        svc.assert_any_call(project_a)

    def test_auto_refresh_includes_remote_completed_for_download(self, runs_page, tmp_path, qtbot):
        """remote_completed runs should be picked up for automatic download."""
        record = MagicMock(
            run_id="run_rc",
            server_id="wsl",
            remote_dir="/r",
            manifest_path=tmp_path / "m.tsv",
            local_dir=str(tmp_path),
            status_summary={"remote_completed": 1},
        )

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch.object(runs_page, "_workspace", return_value=tmp_path), \
             patch.object(runs_page, "_execute_download_use_case", return_value=SimpleNamespace(errors=[], transfer_records=[], failures=[])) as download, \
             patch.object(runs_page, "refresh_run_list"):
            svc.return_value.list_runs.return_value = [record]
            with patch("jobdesk_app.gui.pages.runs_results_page.load_servers"), \
                 patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client"), \
                 patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client"), \
                 patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
                runs_page._auto_refresh_active()
                qtbot.waitUntil(lambda: not runs_page._auto_refresh_running, timeout=2000)

        download.assert_called_once()
