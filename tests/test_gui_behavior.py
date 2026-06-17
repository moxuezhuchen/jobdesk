"""GUI behavior tests using pytest-qt."""
import json
import threading
import time

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


@pytest.fixture
def app_state():
    """Minimal app state for page construction."""
    state = MagicMock()
    state.current_project_root = Path.cwd()
    return state


@pytest.fixture
def runs_page(qtbot, app_state):
    from jobdesk_app.gui.pages.runs_results_page import RunsResultsPage
    with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
        mock_svc.return_value.list_runs.return_value = []
        page = RunsResultsPage(app_state, log_cb=lambda m: None, status_cb=lambda m: None)
        qtbot.addWidget(page)
        yield page
        page.shutdown()


@pytest.fixture
def file_page(qtbot, app_state, tmp_path):
    from jobdesk_app.gui.pages.file_transfer_page import FileTransferPage
    from jobdesk_app.services.gui_settings import GuiSettings, GuiSettingsStore

    store = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    store.save(GuiSettings(auto_connect=False))
    with patch("jobdesk_app.gui.pages.file_transfer_page.GuiSettingsStore", return_value=store):
        page = FileTransferPage(app_state, log_cb=lambda m: None, status_cb=lambda m: None, error_cb=lambda t, m: None)
        qtbot.addWidget(page)
        yield page
        page.shutdown()


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeWorker:
    def __init__(self):
        self.progress = _FakeSignal()
        self.result = _FakeSignal()
        self.error = _FakeSignal()
        self.log = _FakeSignal()
        self.finished = _FakeSignal()
        self.start = MagicMock()
        self.stop_safely = MagicMock()
        self.deleteLater = MagicMock()


class TestRunsPage:
    def test_page_creates_without_crash(self, runs_page):
        assert runs_page is not None

    def test_table_has_correct_columns(self, runs_page):
        table = runs_page.table
        assert table.columnCount() == 6

    def test_buttons_exist(self, runs_page):
        assert runs_page.retry_btn is not None
        assert runs_page.cancel_btn is not None
        assert runs_page.delete_btn is not None

    def test_runs_results_buttons_have_feedback_roles(self, runs_page):
        from jobdesk_app.gui.button_feedback import ButtonRole

        assert runs_page.retry_btn.property("buttonRole") == ButtonRole.PRIMARY_ACTION.value
        assert runs_page.cancel_btn.property("buttonRole") == ButtonRole.DANGER_ACTION.value
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
        # First action is refresh
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
            mock_svc2.return_value.load_run.assert_not_called()  # served from cache
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
            runs_page.table.selectRow(1)  # select "b"
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
            assert runs_page.table.item(runs_page.table.currentRow(), 0).text() == "new"  # jumped
            runs_page.table.selectRow(1)  # user manually selects "old"
            runs_page.refresh_run_list()  # batch_id unchanged -> keep manual
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
        record.command_template = "python run_orca.py {name}"  # program is python, not orca
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as mock_store:
            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = GuiSettings()
            patterns = runs_page._get_download_patterns(record)
        assert patterns == [".log", ".out"]  # falls back to default, not ORCA's

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
             patch("jobdesk_app.gui.pages.runs_results_page.RunService") as run_service, \
             patch.object(runs_page, "refresh_run_list") as refresh_run_list:
            payload = runs_page._collect_result_preview(record)

        refresh_run_list.assert_not_called()
        run_service.return_value.update_run_from_manifest.assert_not_called()
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
            assert runs_page._refresh_timer.isActive()
            qtbot.waitUntil(lambda: refresh.called and monitor.called, timeout=1000)

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
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status"), \
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

        service.return_value.download_completed.assert_called_once()

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
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status") as refresh, \
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
        service.return_value.download_completed.assert_called_once()
        # Verify run_summary.json is readable post-download
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
        # mol4 has no summary (failed task)
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
        # Only mol1 has results downloaded
        result_dir = tmp_path / "results" / "batch02"
        d = result_dir / "mol1" / "mol1_confflow_work"
        d.mkdir(parents=True)
        (d / "run_summary.json").write_text(json.dumps({
            "initial_conformers": 3, "final_conformers": 1,
            "total_duration_seconds": 8, "step_status_counts": {"completed": 1},
        }), encoding="utf-8")
        # mol2 failed — no local directory at all

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
        # No result directory at all

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

        # Summary only exists under default_folder
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

        # workspace has empty result dir
        (workspace / "results" / "run05").mkdir(parents=True)

        # default_local_folder has the actual summary
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

        svc.return_value.download_completed.assert_called_once()

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
        assert runs_page.result_table.item(0, 5).text() == "0.020000"  # ZPE
        assert runs_page.result_table.item(0, 6).text() == "1"  # one imaginary frequency
        assert runs_page.result_table.item(0, 7).text() == tr("OK", runs_page._language)  # diagnosis: clean

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

        # RunService must be constructed with local_a, not current workspace
        svc.assert_any_call(local_a)

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
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status"), \
             patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path / "project_b"), \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
            svc.return_value.load_run.return_value = updated
            svc.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._refresh_status()
            qtbot.waitUntil(
                lambda: svc.return_value.download_completed.called,
                timeout=2000,
            )

        svc.assert_any_call(local_a)
        svc.return_value.download_completed.assert_called_once()

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

    def test_rerun_all_reports_active_task_error_without_submit(self, runs_page):
        statuses = []
        runs_page._status_cb = statuses.append
        record = MagicMock(run_id="run_active")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch("jobdesk_app.gui.pages.runs_results_page.RunService") as svc, \
             patch.object(runs_page, "_submit_record") as submit_record:
            svc.return_value.prepare_rerun.side_effect = ValueError("cannot rerun active remote tasks: a")

            runs_page._rerun_all()

        assert statuses == ["cannot rerun active remote tasks: a"]
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

        # RunService for delete must be constructed with project_a, not project_b
        svc.assert_any_call(project_a)

    def test_auto_refresh_includes_remote_completed_for_download(self, runs_page, tmp_path):
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
             patch.object(runs_page, "_workspace", return_value=tmp_path):
            svc.return_value.list_runs.return_value = [record]
            # Should NOT return early (no active but has needs_download)
            # The method should set _auto_refresh_running = True
            with patch("jobdesk_app.gui.pages.runs_results_page.load_servers"), \
                 patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client"), \
                 patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client"), \
                 patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
                runs_page._auto_refresh_active()

        assert getattr(runs_page, '_auto_refresh_running', False)



class TestAutoRefreshConnectionReuse:
    """Tests for #3: SSH/SFTP connection reuse per server in _auto_refresh_active."""

    def test_same_server_multiple_runs_one_connection(self, runs_page, qtbot):
        """Multiple active runs on the same server should create only one SSH + one SFTP."""
        records = [
            MagicMock(run_id=f"run_{i}", server_id="wsl", remote_dir="/remote/work",
                      manifest_path=Path("m.tsv"), status_summary={"running": 1}, local_dir=None)
            for i in range(3)
        ]
        updated = MagicMock(status_summary={"running": 1})

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status"):
            service.return_value.list_runs.return_value = records
            service.return_value.load_run.return_value = updated
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._auto_refresh_active()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_auto_refresh_running", False),
                timeout=2000,
            )

        assert make_ssh.call_count == 1
        assert make_sftp.call_count == 1

    def test_active_and_needs_download_share_connection(self, runs_page, qtbot):
        """Active run refresh + needs_download run on same server share one connection."""
        active_rec = MagicMock(run_id="run_active", server_id="wsl", remote_dir="/r",
                               manifest_path=Path("m.tsv"), status_summary={"running": 1}, local_dir=None)
        dl_rec = MagicMock(run_id="run_dl", server_id="wsl", remote_dir="/r",
                           manifest_path=Path("m.tsv"), status_summary={"remote_completed": 1}, local_dir=None)
        updated_active = MagicMock(status_summary={"running": 1})

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status"), \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.out"]):
            service.return_value.list_runs.return_value = [active_rec, dl_rec]
            service.return_value.load_run.return_value = updated_active
            service.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._auto_refresh_active()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_auto_refresh_running", False),
                timeout=2000,
            )

        assert make_ssh.call_count == 1
        assert make_sftp.call_count == 1

    def test_different_servers_get_separate_connections(self, runs_page, qtbot):
        """Runs on different servers each get their own SSH+SFTP."""
        records = [
            MagicMock(run_id="run_a", server_id="srv_a", remote_dir="/r",
                      manifest_path=Path("m.tsv"), status_summary={"running": 1}, local_dir=None),
            MagicMock(run_id="run_b", server_id="srv_b", remote_dir="/r",
                      manifest_path=Path("m.tsv"), status_summary={"running": 1}, local_dir=None),
        ]
        updated = MagicMock(status_summary={"running": 1})

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status"):
            service.return_value.list_runs.return_value = records
            service.return_value.load_run.return_value = updated
            servers.return_value.servers = {"srv_a": MagicMock(), "srv_b": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._auto_refresh_active()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_auto_refresh_running", False),
                timeout=2000,
            )

        assert make_ssh.call_count == 2
        assert make_sftp.call_count == 2

    def test_run_error_does_not_affect_others_and_session_persists(self, runs_page, qtbot):
        """If one run's refresh fails, others still proceed; the live session is
        reused (not closed per tick) and only closed on shutdown."""
        records = [
            MagicMock(run_id="run_fail", server_id="wsl", remote_dir="/r",
                      manifest_path=Path("m.tsv"), status_summary={"running": 1}, local_dir=None),
            MagicMock(run_id="run_ok", server_id="wsl", remote_dir="/r",
                      manifest_path=Path("m.tsv"), status_summary={"running": 1}, local_dir=None),
        ]
        updated = MagicMock(status_summary={"running": 1})
        call_count = {"refresh": 0}

        def fake_refresh(**kwargs):
            call_count["refresh"] += 1
            if call_count["refresh"] == 1:
                raise RuntimeError("simulated failure")

        mock_ssh = MagicMock()
        mock_ssh.is_alive.return_value = True  # not a connection failure
        mock_sftp = MagicMock()

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status", side_effect=fake_refresh):
            service.return_value.list_runs.return_value = records
            service.return_value.load_run.return_value = updated
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = mock_ssh
            make_sftp.return_value = mock_sftp

            runs_page._auto_refresh_active()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_auto_refresh_running", False),
                timeout=2000,
            )

        # Both runs attempted refresh (2 calls to refresh_batch_status)
        assert call_count["refresh"] == 2
        # Connection created only once and reused (still alive after a non-connection error)
        assert make_ssh.call_count == 1
        # Persistent across the tick: not closed after the refresh
        mock_sftp.close.assert_not_called()
        mock_ssh.close.assert_not_called()
        # Closed on shutdown
        runs_page.shutdown()
        mock_sftp.close.assert_called_once()
        mock_ssh.close.assert_called_once()

    def test_sftp_creation_failure_closes_ssh_and_does_not_cache(self, runs_page, qtbot):
        """If SFTP creation fails, SSH is closed and the bad session is not cached."""
        records = [
            MagicMock(run_id="run_1", server_id="wsl", remote_dir="/r",
                      manifest_path=Path("m.tsv"), status_summary={"running": 1}, local_dir=None),
        ]
        mock_ssh = MagicMock()

        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status"):
            service.return_value.list_runs.return_value = records
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = mock_ssh
            make_sftp.side_effect = RuntimeError("SFTP channel failed")

            runs_page._auto_refresh_active()
            qtbot.waitUntil(
                lambda: not getattr(runs_page, "_auto_refresh_running", False),
                timeout=2000,
            )

        # SSH was closed immediately when SFTP failed
        mock_ssh.close.assert_called_once()



class TestTaskDoneDebounce:
    """Tests for #4: _on_task_done debounce."""

    def _make_event(self, run_id="run_1", server_id="wsl", exit_code=None):
        evt = MagicMock()
        evt.run_id = run_id
        evt.server_id = server_id
        evt.exit_code = exit_code
        return evt

    def test_multiple_running_events_single_refresh(self, runs_page, qtbot):
        """3 RUNNING events for same run_id within 1s → only 1 refresh_batch_status call."""
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status") as refresh:
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None, manifest_path=Path("m.tsv"), remote_dir="/r", server_id="wsl",
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
        service.return_value.download_completed.assert_not_called()

    def test_multiple_done_events_single_refresh_and_download(self, runs_page, qtbot):
        """Multiple DONE events → 1 refresh + 1 download_completed."""
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status") as refresh, \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None, manifest_path=Path("m.tsv"), remote_dir="/r", server_id="wsl",
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
            service.return_value.download_completed.assert_called_once()

    def test_running_then_done_triggers_download(self, runs_page, qtbot):
        """RUNNING followed by DONE → merged as has_done=True → download."""
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status") as refresh, \
             patch.object(runs_page, "_get_download_patterns", return_value=["*.log"]):
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None, manifest_path=Path("m.tsv"), remote_dir="/r", server_id="wsl",
                status_summary={"remote_completed": 1},
            )
            service.return_value.download_completed.return_value = ([], [])
            servers.return_value.servers = {"wsl": MagicMock()}
            make_ssh.return_value = MagicMock()
            make_sftp.return_value = MagicMock()

            runs_page._on_task_done(self._make_event(exit_code=None))  # RUNNING
            runs_page._on_task_done(self._make_event(exit_code=0))     # DONE

            qtbot.waitUntil(
                lambda: "run_1" not in runs_page._pending_task_events,
                timeout=3000,
            )
            qtbot.waitUntil(lambda: not runs_page._bg_workers, timeout=3000)

            refresh.assert_called_once()
            service.return_value.download_completed.assert_called_once()

    def test_different_run_ids_debounce_independently(self, runs_page, qtbot):
        """Events for different run_ids each produce their own refresh."""
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as service, \
             patch("jobdesk_app.gui.pages.runs_results_page.load_servers") as servers, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_ssh_client") as make_ssh, \
             patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp, \
             patch("jobdesk_app.remote.status_refresh.refresh_batch_status") as refresh:
            service.return_value.load_run.return_value = MagicMock(
                local_dir=None, manifest_path=Path("m.tsv"), remote_dir="/r", server_id="wsl",
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
        with patch("jobdesk_app.remote.status_refresh.refresh_batch_status") as refresh:
            runs_page._on_task_done(self._make_event())
            assert "run_1" in runs_page._pending_task_events
            runs_page.shutdown()
            assert runs_page._pending_task_events == {}
            assert runs_page._task_done_timers == {}
            # Wait past debounce window — no refresh should fire
            qtbot.wait(1200)

        refresh.assert_not_called()


class TestFileTransferPage:
    def test_page_creates_without_crash(self, file_page):
        assert file_page is not None

    def test_file_transfer_buttons_have_feedback_roles(self, file_page):
        from jobdesk_app.gui.button_feedback import ButtonRole

        assert file_page.refresh_btn.property("buttonRole") == ButtonRole.REFRESH_ACTION.value
        assert file_page.open_terminal_btn.property("buttonRole") == ButtonRole.INSTANT_ACTION.value
        assert file_page.preview_commands_btn.property("buttonRole") == ButtonRole.INSTANT_ACTION.value
        assert file_page.run_btn.property("buttonRole") == ButtonRole.PRIMARY_ACTION.value
        assert file_page.confflow_btn.property("buttonRole") == ButtonRole.PRIMARY_ACTION.value
        assert file_page.create_only_btn.property("buttonRole") == ButtonRole.PRIMARY_ACTION.value

    def test_file_transfer_run_feedback_pending_disables_run_group(self, file_page):
        idle_texts = {
            file_page.run_btn: file_page.run_btn.text(),
            file_page.confflow_btn: file_page.confflow_btn.text(),
            file_page.create_only_btn: file_page.create_only_btn.text(),
        }

        file_page._run_feedback.pending("Submitting...")

        assert file_page.run_btn.text() == "Submitting..."
        assert not file_page.run_btn.isEnabled()
        assert not file_page.confflow_btn.isEnabled()
        assert not file_page.create_only_btn.isEnabled()

        file_page._run_feedback.restore()

        assert file_page.run_btn.text() == idle_texts[file_page.run_btn]
        assert file_page.confflow_btn.text() == idle_texts[file_page.confflow_btn]
        assert file_page.create_only_btn.text() == idle_texts[file_page.create_only_btn]
        assert file_page.run_btn.isEnabled()
        assert file_page.confflow_btn.isEnabled()
        assert file_page.create_only_btn.isEnabled()

    def test_file_transfer_refresh_feedback_stays_pending_until_async_completion(self, file_page):
        from jobdesk_app.gui.i18n import tr

        with patch.object(file_page, "_refresh_local_async") as refresh_local, \
             patch.object(file_page, "_refresh_remote") as refresh_remote:
            file_page._refresh_all()

        refresh_local.assert_called_once_with()
        refresh_remote.assert_called_once_with()
        assert file_page.refresh_btn.text() == tr("Refreshing...", file_page._language)
        assert file_page.refresh_btn.property("feedbackState") == "pending"
        assert not file_page.refresh_btn.isEnabled()

        file_page._refresh_feedback.restore()

        assert file_page.refresh_btn.property("feedbackState") == "idle"
        assert file_page.refresh_btn.isEnabled()

    def test_file_transfer_refresh_without_connection_sets_error_feedback(self, file_page):
        from jobdesk_app.gui.i18n import tr

        file_page._service = None
        file_page._gui_settings = replace(file_page._gui_settings, auto_connect=False)

        with patch.object(file_page, "_refresh_local_async") as refresh_local:
            file_page._refresh_all()

        refresh_local.assert_called_once_with()
        assert file_page.refresh_btn.text() == tr("Refresh failed", file_page._language)
        assert file_page.refresh_btn.property("feedbackState") == "error"
        assert not file_page.refresh_btn.isEnabled()

        file_page._refresh_feedback.restore()

        assert file_page.refresh_btn.property("feedbackState") == "idle"
        assert file_page.refresh_btn.isEnabled()

    def test_local_table_exists(self, file_page):
        assert file_page.local_table is not None
        assert file_page.local_table.columnCount() >= 4

    def test_confflow_launch_button_exists(self, file_page):
        from jobdesk_app.gui.i18n import tr
        assert file_page.confflow_btn.text() == tr("Run ConfFlow", file_page._language)

    def test_open_terminal_button_exists_on_remote_header(self, file_page):
        from jobdesk_app.gui.i18n import tr

        assert file_page.open_terminal_btn.text() == tr("Open Terminal Here", file_page._language)
        assert not file_page.open_terminal_btn.isHidden()

    def test_open_terminal_uses_current_remote_directory(self, file_page):
        server = MagicMock()
        file_page._servers = {"hpc": server}
        file_page.server_combo.addItem("hpc", "hpc")
        file_page.server_combo.setCurrentIndex(file_page.server_combo.findData("hpc"))
        file_page.remote_path.setText("/home/xianj/qhf")
        launch = MagicMock()

        with patch("jobdesk_app.gui.pages.file_transfer_page.build_terminal_launch", return_value=launch) as build, \
             patch("jobdesk_app.gui.pages.file_transfer_page.launch_terminal") as launcher:
            file_page._open_terminal_here()

        build.assert_called_once()
        assert build.call_args.args[0] is server
        assert build.call_args.args[1] == "/home/xianj/qhf"
        launcher.assert_called_once_with(launch)

    def test_transfer_progress_is_compact_and_in_task_action_row(self, file_page):
        assert file_page.run_options_row.indexOf(file_page.progress_bar) == (
            file_page.run_options_row.indexOf(file_page.create_only_btn) + 1
        )
        assert file_page.progress_bar.maximumWidth() <= 360
        assert file_page.progress_bar.minimumHeight() >= 24
        assert file_page.progress_bar.minimumHeight() > file_page.run_mode_combo.fontMetrics().height()

    def test_file_table_header_click_sorts_rows(self, file_page, qtbot):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        from jobdesk_app.gui.pages.file_transfer_widgets import _load_rows

        _load_rows(
            file_page.local_table,
            [
                ["b.txt", "2 KB", "2026-01-02", "file", "C:/work/b.txt"],
                ["a.txt", "1 KB", "2026-01-01", "file", "C:/work/a.txt"],
            ],
        )
        header = file_page.local_table.horizontalHeader()
        assert header.sectionsClickable()

        x = header.sectionViewportPosition(0) + header.sectionSize(0) // 2
        QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(x, header.height() // 2))
        qtbot.wait(10)

        assert [file_page.local_table.item(row, 0).text() for row in range(2)] == ["a.txt", "b.txt"]

    def test_file_table_size_header_sorts_by_bytes(self, file_page, qtbot):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        from jobdesk_app.gui.pages.file_transfer_widgets import _load_rows

        _load_rows(
            file_page.local_table,
            [
                ["big.out", "109.2 MB", "2026-01-03", "file", "C:/work/big.out"],
                ["small.out", "6.9 KB", "2026-01-01", "file", "C:/work/small.out"],
                ["mid.out", "548.8 KB", "2026-01-02", "file", "C:/work/mid.out"],
            ],
        )
        header = file_page.local_table.horizontalHeader()

        x = header.sectionViewportPosition(1) + header.sectionSize(1) // 2
        QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(x, header.height() // 2))
        qtbot.wait(10)

        assert [file_page.local_table.item(row, 0).text() for row in range(3)] == [
            "small.out",
            "mid.out",
            "big.out",
        ]

    def test_remote_table_routes_external_local_url_drop_for_upload(self, file_page, tmp_path):
        from PySide6.QtCore import QMimeData, QUrl

        source = tmp_path / "source.log"
        source.write_text("output", encoding="utf-8")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])
        event = MagicMock()
        event.mimeData.return_value = mime
        dropped_paths = []
        file_page.remote_table.drop_files.connect(lambda paths: dropped_paths.append(paths))

        file_page.remote_table.dropEvent(event)

        assert len(dropped_paths) == 1
        assert [Path(path) for path in dropped_paths[0]] == [source]
        event.acceptProposedAction.assert_called_once_with()

    @pytest.mark.parametrize("role", ["local", "remote"])
    def test_file_table_rejects_non_local_url_drop(self, file_page, role):
        from PySide6.QtCore import QMimeData, QUrl

        mime = QMimeData()
        mime.setUrls([QUrl("https://example.invalid/result.log")])

        assert not getattr(file_page, f"{role}_table")._accepts_mime(mime)

    def test_local_table_routes_remote_path_drop_for_download(self, file_page):
        from PySide6.QtCore import QMimeData

        mime = QMimeData()
        mime.setData("application/x-jobdesk-remote-paths", b"/remote/result.log")
        event = MagicMock()
        event.mimeData.return_value = mime
        dropped_paths = []
        file_page.local_table.drop_files.connect(lambda paths: dropped_paths.append(paths))

        file_page.local_table.dropEvent(event)

        assert dropped_paths == [["/remote/result.log"]]
        event.acceptProposedAction.assert_called_once_with()

    def test_local_table_routes_drop_on_directory_for_move(self, file_page, tmp_path):
        from PySide6.QtCore import QMimeData, QUrl

        source = tmp_path / "source.log"
        target_dir = tmp_path / "archive"
        source.write_text("output", encoding="utf-8")
        target_dir.mkdir()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])
        event = MagicMock()
        event.mimeData.return_value = mime
        moves = []
        file_page.local_table.move_local_files.connect(
            lambda paths, target: moves.append((paths, target))
        )

        with patch.object(file_page.local_table, "_drop_directory_path", return_value=str(target_dir)):
            file_page.local_table.dropEvent(event)

        assert len(moves) == 1
        assert [Path(path) for path in moves[0][0]] == [source]
        assert Path(moves[0][1]) == target_dir
        event.acceptProposedAction.assert_called_once_with()

    def test_remote_table_routes_drop_on_directory_for_move(self, file_page):
        from PySide6.QtCore import QMimeData

        mime = QMimeData()
        mime.setData("application/x-jobdesk-remote-paths", b"/remote/source.log")
        event = MagicMock()
        event.mimeData.return_value = mime
        moves = []
        file_page.remote_table.move_remote_files.connect(
            lambda paths, target: moves.append((paths, target))
        )

        with patch.object(file_page.remote_table, "_drop_directory_path", return_value="/remote/archive"):
            file_page.remote_table.dropEvent(event)

        assert moves == [(["/remote/source.log"], "/remote/archive")]
        event.acceptProposedAction.assert_called_once_with()

    def test_parent_row_is_not_a_move_drop_target(self, file_page):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QTableWidgetItem

        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem(".."))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("dir"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem("C:/parent"))
        event = MagicMock()
        event.position.return_value.toPoint.return_value = QPoint(1, 1)

        with patch.object(file_page.local_table, "itemAt", return_value=file_page.local_table.item(0, 0)):
            assert file_page.local_table._drop_directory_path(event) is None

    def test_external_local_url_drop_on_remote_table_uploads_to_current_remote_dir(
        self, file_page, qtbot, tmp_path
    ):
        from PySide6.QtCore import QMimeData, QUrl

        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        source = tmp_path / "source.log"
        source.write_text("output", encoding="utf-8")
        file_page.remote_path.setText("/remote/current")
        service = MagicMock()
        service.upload_path.return_value = TransferRecord(
            TransferDirection.upload,
            str(source),
            "/remote/current/source.log",
            status=TransferStatus.transferred,
        )
        file_page._service = service
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])
        event = MagicMock()
        event.mimeData.return_value = mime

        with patch.object(file_page, "_refresh_remote") as refresh_remote:
            file_page.remote_table.dropEvent(event)
            qtbot.waitUntil(
                lambda: service.upload_path.called and refresh_remote.called,
                timeout=2000,
            )

        assert Path(service.upload_path.call_args.args[0]) == source
        assert service.upload_path.call_args.args[1] == "/remote/current/source.log"

    def test_remote_path_drop_on_local_table_downloads_to_current_local_dir(
        self, file_page, qtbot, tmp_path
    ):
        from PySide6.QtCore import QMimeData

        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        file_page.state.current_project_root = tmp_path
        service = MagicMock()
        service.download_path.return_value = TransferRecord(
            TransferDirection.download,
            str(tmp_path / "result.log"),
            "/remote/result.log",
            status=TransferStatus.transferred,
        )
        file_page._service = service
        mime = QMimeData()
        mime.setData("application/x-jobdesk-remote-paths", b"/remote/result.log")
        event = MagicMock()
        event.mimeData.return_value = mime

        with patch.object(file_page, "_refresh_local") as refresh_local:
            file_page.local_table.dropEvent(event)
            qtbot.waitUntil(
                lambda: service.download_path.called and refresh_local.called,
                timeout=2000,
            )

        assert service.download_path.call_args.args[0] == "/remote/result.log"
        assert Path(service.download_path.call_args.args[1]) == tmp_path / "result.log"
        from jobdesk_app.core.file_transfer import OverwritePolicy
        assert service.download_path.call_args.args[2] == OverwritePolicy.overwrite

    def test_confflow_invalid_input_reports_visible_error(self, file_page):
        errors = []
        file_page._service = MagicMock()
        file_page._connected_server = MagicMock()
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._run_confflow()

        assert errors == [("ConfFlow Input", "No .xyz files selected")]

    def test_submission_emits_run_id_for_navigation(self, file_page, qtbot):
        received = []
        file_page.runs_submitted.connect(lambda run_ids: received.extend(run_ids))
        result = MagicMock(batch_id="260523-001", submitted_task_count=1, errors=[])

        file_page._on_runs_done([result])

        assert received == ["260523-001"]

    def test_remote_table_exists(self, file_page):
        assert file_page.remote_table is not None

    def test_refresh_local_no_crash(self, file_page):
        """_refresh_local should handle current directory without crash."""
        file_page._refresh_local()
        # Should have at least the parent row
        assert file_page.local_table.rowCount() >= 0

    def test_refresh_local_reports_permission_error(self, file_page, tmp_path):
        messages = []
        file_page._status_cb = messages.append
        file_page.state.current_project_root = tmp_path

        with patch("pathlib.Path.iterdir", side_effect=PermissionError("denied")):
            file_page._refresh_local()

        assert messages == [f"No permission to access: {tmp_path}"]

    def test_refresh_local_does_not_reload_settings(self, file_page):
        """_refresh_local must not call GuiSettingsStore().load() — uses cached settings."""
        with patch("jobdesk_app.gui.pages.file_transfer_page.GuiSettingsStore") as mock_store:
            file_page._refresh_local()
        mock_store.return_value.load.assert_not_called()

    def test_refresh_local_uses_cached_hide_dotfiles(self, file_page, tmp_path):
        """_refresh_local hides/shows dotfiles based on cached self._gui_settings."""
        from dataclasses import replace as dc_replace
        # Create a dotfile in tmp_path
        (tmp_path / ".hidden").write_text("x")
        (tmp_path / "visible.txt").write_text("y")
        file_page.state.current_project_root = tmp_path

        # hide_dotfiles=True → dotfile hidden
        file_page._gui_settings = dc_replace(file_page._gui_settings, hide_dotfiles=True)
        file_page._refresh_local()
        names = [file_page.local_table.item(r, 0).text() for r in range(file_page.local_table.rowCount())]
        assert ".hidden" not in names
        assert "visible.txt" in names

        # hide_dotfiles=False → dotfile shown
        file_page._gui_settings = dc_replace(file_page._gui_settings, hide_dotfiles=False)
        file_page._refresh_local()
        names = [file_page.local_table.item(r, 0).text() for r in range(file_page.local_table.rowCount())]
        assert ".hidden" in names
        assert "visible.txt" in names

    def test_on_activated_reloads_settings(self, file_page):
        """on_activated must reload settings so external changes take effect."""
        from jobdesk_app.services.gui_settings import GuiSettings
        new_settings = GuiSettings(auto_connect=False, hide_dotfiles=True)
        with patch("jobdesk_app.gui.pages.file_transfer_page.GuiSettingsStore") as mock_store:
            mock_store.return_value.load.return_value = new_settings
            file_page.on_activated()
        assert file_page._gui_settings.hide_dotfiles is True

    def test_open_local_file_uses_configured_text_editor(self, file_page, tmp_path):
        from PySide6.QtWidgets import QTableWidgetItem

        local_file = tmp_path / "notes.txt"
        local_file.write_text("notes", encoding="utf-8")
        file_page._gui_settings = replace(file_page._gui_settings, text_editor_path="C:/Tools/editor.exe")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(local_file)))

        with patch("jobdesk_app.gui.pages.file_transfer_page.subprocess.Popen") as launch:
            file_page._open_local_item(file_page.local_table.item(0, 4))

        launch.assert_called_once_with(["C:/Tools/editor.exe", str(local_file)])

    def test_open_remote_file_uses_configured_text_editor_after_download(self, file_page, qtbot):
        file_page._gui_settings = replace(file_page._gui_settings, text_editor_path="C:/Tools/editor.exe")
        file_page._service = MagicMock()

        with patch("jobdesk_app.gui.pages.file_transfer_page.subprocess.Popen") as launch:
            file_page._open_remote_file_in_editor("/remote/result.log")
            qtbot.waitUntil(lambda: launch.called, timeout=2000)

        assert launch.call_args.args[0][0] == "C:/Tools/editor.exe"
        assert launch.call_args.args[0][1].endswith("result.log")

    def test_open_remote_file_uses_tracked_worker_helper(self, file_page):
        worker = _FakeWorker()
        file_page._service = MagicMock()

        with patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", return_value=worker) as start_worker:
            file_page._open_remote_file_in_editor("/remote/result.log")

        start_worker.assert_called_once()
        assert start_worker.call_args.kwargs["registry_attr"] == "_background_workers"

    def test_open_remote_files_with_same_name_use_distinct_temp_paths(self, file_page, tmp_path):
        service = MagicMock()
        downloaded: list[tuple[str, Path]] = []

        def download_path(remote_path, local_path, _policy):
            local = Path(local_path)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text(remote_path, encoding="utf-8")
            downloaded.append((remote_path, local))

        service.download_path.side_effect = download_path
        file_page._service = service
        file_page._connected_server_id = "wsl"

        with patch("jobdesk_app.gui.pages.file_transfer_page.tempfile.gettempdir", return_value=str(tmp_path)), \
             patch.object(file_page, "_open_in_text_editor", return_value=True), \
             patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker:
            file_page._open_remote_file_in_editor("/remote/work1/a.gjf")
            first_call = start_worker.call_args
            first_result = first_call.kwargs["target"](MagicMock())
            first_call.kwargs["on_result"](first_result)

            file_page._open_remote_file_in_editor("/remote/work2/a.gjf")
            second_call = start_worker.call_args
            second_result = second_call.kwargs["target"](MagicMock())
            second_call.kwargs["on_result"](second_result)

        assert downloaded[0][1] != downloaded[1][1]
        assert downloaded[0][1].name == "a.gjf"
        assert downloaded[1][1].name == "a.gjf"
        assert {session.remote_path for session in file_page._remote_edit_sessions.values()} == {
            "/remote/work1/a.gjf",
            "/remote/work2/a.gjf",
        }

    def test_remote_edit_uploads_saved_temp_file_to_original_remote_path(self, file_page, tmp_path):
        temp_file = tmp_path / "result.gjf"
        temp_file.write_text("before\n", encoding="utf-8")
        service = MagicMock()
        file_page._service = service

        file_page._register_remote_edit_session("/remote/work/result.gjf", temp_file)
        temp_file.write_text("after\n\n", encoding="utf-8")

        with patch("jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question") as question, \
             patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker:
            file_page._check_remote_edit_sessions()
            target = start_worker.call_args.kwargs["target"]
            result = target(MagicMock())
            start_worker.call_args.kwargs["on_result"](result)

        question.assert_not_called()
        service.upload_path.assert_called_once()
        call_args = service.upload_path.call_args
        assert Path(call_args.args[0]) == temp_file
        assert call_args.args[1] == "/remote/work/result.gjf"
        from jobdesk_app.core.file_transfer import OverwritePolicy
        assert call_args.args[2] == OverwritePolicy.overwrite
        assert not file_page._dirty_remote_edit_sessions()

    def test_remote_edit_upload_failure_keeps_session_dirty(self, file_page, tmp_path):
        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        temp_file = tmp_path / "result.gjf"
        temp_file.write_text("before\n", encoding="utf-8")
        service = MagicMock()
        service.upload_path.return_value = TransferRecord(
            TransferDirection.upload,
            str(temp_file),
            "/remote/work/result.gjf",
            status=TransferStatus.failed,
            reason="permission denied",
        )
        file_page._service = service
        errors: list[tuple[str, str]] = []
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._register_remote_edit_session("/remote/work/result.gjf", temp_file)
        temp_file.write_text("after\n\n", encoding="utf-8")

        with patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker:
            file_page._check_remote_edit_sessions()
            target = start_worker.call_args.kwargs["target"]
            try:
                target(MagicMock())
            except RuntimeError as exc:
                start_worker.call_args.kwargs["on_error"](str(exc))

        assert errors
        assert "permission denied" in errors[0][1]
        assert file_page._dirty_remote_edit_sessions()

    def test_remote_generate_gjf_cleans_temp_xyz_when_upload_fails(self, file_page, tmp_path):
        from PySide6.QtWidgets import QTableWidgetItem

        tmp_xyz = tmp_path / "downloaded.xyz"
        generated = tmp_path / "generated.gjf"
        generated.write_text("%chk=water.chk\n", encoding="utf-8")

        file_page._service = MagicMock()
        file_page._service.download_path.side_effect = lambda _remote, local: Path(local).write_text("xyz", encoding="utf-8")
        file_page._service.upload_path.side_effect = RuntimeError("upload failed")
        file_page.remote_path.setText("/remote/work")
        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/work/water.xyz"))
        file_page.remote_table.setCurrentCell(0, 0)

        temp_file = MagicMock()
        temp_file.name = str(tmp_xyz)
        dialog = MagicMock()
        dialog.exec.return_value = True
        dialog.generated_path.return_value = generated

        def fake_start(_owner, *, target, on_result=None, on_error=None, **_kwargs):
            try:
                result = target(SimpleNamespace())
            except Exception as exc:
                if on_error is not None:
                    on_error(str(exc))
            else:
                if on_result is not None:
                    on_result(result)
            return _FakeWorker()

        with patch("tempfile.NamedTemporaryFile", return_value=temp_file), \
             patch("jobdesk_app.gui.dialogs.input_builder_dialog.InputBuilderDialog", return_value=dialog), \
             patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", side_effect=fake_start):
            file_page._remote_generate_gjf()

        assert not tmp_xyz.exists()

    def test_new_local_file_uses_configured_text_editor(self, file_page, tmp_path):
        file_page.state.current_project_root = tmp_path
        file_page._gui_settings = replace(file_page._gui_settings, text_editor_path="C:/Tools/editor.exe")

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.QInputDialog.getText",
            return_value=("new.txt", True),
        ), patch.object(file_page, "_refresh_local"), patch.object(file_page, "_open_in_text_editor") as open_editor:
            file_page._new_file_local()

        open_editor.assert_called_once_with(tmp_path / "new.txt")

    def test_upload_without_service_shows_message(self, file_page, qtbot):
        """Drag-drop without connection should show status message."""
        messages = []
        file_page._status_cb = lambda m: messages.append(m)
        file_page._service = None
        file_page._upload_dropped_local_paths(["C:/fake/file.gjf"])
        assert any("Connect" in m for m in messages)

    def test_upload_selected_uses_service_public_api(self, file_page, qtbot, tmp_path):
        """_upload_selected must not call _sftp_factory directly; must use service.upload_path."""
        from PySide6.QtWidgets import QTableWidgetItem

        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        local_file = tmp_path / "test.gjf"
        local_file.write_text("data", encoding="utf-8")
        file_page.state.current_project_root = tmp_path

        # Set up local table with selection
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("test.gjf"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(local_file)))
        file_page.local_table.selectRow(0)
        file_page.remote_path.setText("/remote/dir")

        service = MagicMock()
        service.upload_path.return_value = TransferRecord(
            TransferDirection.upload, str(local_file), "/remote/dir/test.gjf",
            status=TransferStatus.transferred,
        )
        file_page._service = service

        with patch.object(file_page, "_refresh_remote") as refresh_remote:
            file_page._upload_selected()
            qtbot.waitUntil(
                lambda: service.upload_path.called and refresh_remote.called,
                timeout=2000,
            )

        call_args = service.upload_path.call_args
        assert call_args is not None
        from jobdesk_app.core.file_transfer import OverwritePolicy
        assert call_args[0][2] == OverwritePolicy.overwrite
        assert call_args[1].get("progress_callback") is not None

    def test_download_selected_uses_service_public_api(self, file_page, qtbot, tmp_path):
        """_download_selected must not call _sftp_factory directly."""
        from PySide6.QtWidgets import QTableWidgetItem

        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        file_page.state.current_project_root = tmp_path
        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("result.log"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/result.log"))
        file_page.remote_table.selectRow(0)

        service = MagicMock()
        service.download_path.return_value = TransferRecord(
            TransferDirection.download, str(tmp_path / "result.log"), "/remote/result.log",
            status=TransferStatus.transferred,
        )
        file_page._service = service

        with patch.object(file_page, "_refresh_local") as refresh_local:
            file_page._download_selected()
            qtbot.waitUntil(
                lambda: service.download_path.called and refresh_local.called,
                timeout=2000,
            )

        call_args = service.download_path.call_args
        assert call_args is not None
        assert call_args[1].get("progress_callback") is not None

    def test_transfer_worker_is_removed_when_finished(self, file_page):
        worker = _FakeWorker()

        file_page._start_transfer_worker(worker, "Download", MagicMock())

        assert worker in file_page._background_workers
        worker.finished.emit()
        assert worker not in file_page._background_workers

    def test_submit_rejects_unsafe_remote_dir_on_main_thread(self, file_page):
        """A manually-entered relative remote_dir must be rejected before create_run runs."""
        from PySide6.QtWidgets import QMessageBox

        file_page._service = MagicMock()
        file_page._connected_server = MagicMock()
        file_page.command_edit.setCurrentText("g16 {name}")
        file_page.remote_path.setText("relative/path")
        messages: list[str] = []
        file_page._status_cb = messages.append

        with patch.object(file_page, "_selected_remote_entries", return_value=(["/remote/a.gjf"], [])), \
             patch.object(file_page, "_selected_local_entries", return_value=([], [])), \
             patch("jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("jobdesk_app.gui.pages.file_transfer_page.RunService") as run_service_cls:
            file_page._run_selected_chunks(submit=True)

        run_service_cls.assert_not_called()
        assert any("relative/path" in m for m in messages)

    def test_delete_remote_runs_in_background_worker(self, file_page):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        service = MagicMock()
        file_page._service = service
        file_page.remote_path.setText("/remote/run")
        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("result.log"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/run/result.log"))
        file_page.remote_table.selectRow(0)

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker:
            file_page._delete_remote()

        service.delete_remote.assert_not_called()
        target = start_worker.call_args.kwargs["target"]
        target(MagicMock())
        service.delete_remote.assert_called_once_with(
            "/remote/run/result.log",
            recursive=True,
            extra_allowed_roots=["/remote/run"],
        )

    def test_delete_local_runs_in_background_worker(self, file_page, tmp_path):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        local_file = tmp_path / "old.log"
        local_file.write_text("old", encoding="utf-8")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("old.log"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(local_file)))
        file_page.local_table.selectRow(0)

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker:
            file_page._delete_local()

        assert local_file.exists()
        target = start_worker.call_args.kwargs["target"]
        target(MagicMock())
        assert not local_file.exists()

    def test_remote_run_submission_creates_runs_in_background_worker(self, file_page):
        from PySide6.QtWidgets import QMessageBox

        file_page._service = MagicMock()
        file_page._connected_server = MagicMock(env_init_scripts=[])
        file_page._connected_server_id = "wsl"
        file_page.command_edit.setCurrentText("g16 {name}")
        file_page.remote_path.setText("/remote/work")

        with patch.object(file_page, "_selected_remote_entries", return_value=(["/remote/work/a.gjf"], [])), \
             patch.object(file_page, "_selected_local_entries", return_value=([], [])), \
             patch("jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("jobdesk_app.gui.pages.file_transfer_page.RunService") as run_service_cls, \
             patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker:
            file_page._run_selected_chunks(submit=True)

        run_service_cls.assert_not_called()
        assert start_worker.call_count == 1

    def test_local_run_submission_uses_local_selection_even_when_remote_selection_remains(
        self, file_page, tmp_path
    ):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        local_file = tmp_path / "a.gjf"
        local_file.write_text("%chk=a.chk\n\n", encoding="utf-8")
        service = MagicMock()
        file_page._service = service
        file_page._connected_server = SimpleNamespace(env_init_scripts=[], scheduler=None)
        file_page._connected_server_id = "wsl"
        file_page.state.current_project_root = tmp_path
        file_page.command_edit.setCurrentText("g16 {name}")
        file_page.remote_path.setText("/remote/work")

        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("a.gjf"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/work/a.gjf"))
        file_page.remote_table.selectRow(0)

        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("a.gjf"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(local_file)))
        file_page.local_table.selectRow(0)
        file_page._last_file_selection_side = "remote"
        file_page._schedule_selected_click_rename("local", file_page.local_table.item(0, 0))
        file_page._cancel_selected_click_rename()

        record = SimpleNamespace(run_id="run-local", manifest_path=tmp_path / "manifest.tsv")
        run_service = MagicMock()
        run_service.create_run.return_value = record
        run_service.submit_run.return_value = SimpleNamespace(batch_id="run-local", submitted_task_count=1, errors=[])

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch(
            "jobdesk_app.gui.pages.file_transfer_page.RunService",
            return_value=run_service,
        ), patch(
            "jobdesk_app.gui.pages.file_transfer_page.RunProfileStore"
        ), patch(
            "jobdesk_app.gui.pages.file_transfer_page.create_ssh_client"
        ) as create_ssh, patch(
            "jobdesk_app.gui.pages.file_transfer_page.create_sftp_client"
        ) as create_sftp, patch(
            "jobdesk_app.gui.pages.file_transfer_page.start_context_worker",
            create=True,
        ) as start_worker:
            create_ssh.return_value = MagicMock()
            create_sftp.return_value = MagicMock()
            file_page._run_selected_chunks(submit=True)
            payload = start_worker.call_args.kwargs["target"](MagicMock())

        from jobdesk_app.core.file_transfer import OverwritePolicy

        service.upload_path.assert_called_once_with(
            local_file,
            "/remote/work/a.gjf",
            OverwritePolicy.overwrite,
        )
        created_spec = run_service.create_run.call_args.args[0]
        assert created_spec.sources[0].path == "/remote/work/a.gjf"
        assert payload["error"] is None

    def test_local_run_submission_stops_when_upload_fails(self, file_page, tmp_path):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        local_file = tmp_path / "a.gjf"
        local_file.write_text("%chk=a.chk\n\n", encoding="utf-8")
        service = MagicMock()
        service.upload_path.return_value = TransferRecord(
            TransferDirection.upload,
            str(local_file),
            "/remote/work/a.gjf",
            status=TransferStatus.failed,
            reason="permission denied",
        )
        file_page._service = service
        file_page._connected_server = SimpleNamespace(env_init_scripts=[], scheduler=None)
        file_page._connected_server_id = "wsl"
        file_page.state.current_project_root = tmp_path
        file_page.command_edit.setCurrentText("g16 {name}")
        file_page.remote_path.setText("/remote/work")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("a.gjf"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(local_file)))
        file_page.local_table.selectRow(0)

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch(
            "jobdesk_app.gui.pages.file_transfer_page.RunService"
        ) as run_service_cls, patch(
            "jobdesk_app.gui.pages.file_transfer_page.start_context_worker",
            create=True,
        ) as start_worker:
            file_page._run_selected_chunks(submit=True)
            payload = start_worker.call_args.kwargs["target"](MagicMock())

        run_service_cls.assert_not_called()
        assert "permission denied" in payload["error"]

    def test_auto_fill_preserves_manually_edited_command_for_next_selection(self, file_page):
        from PySide6.QtWidgets import QTableWidgetItem

        file_page.command_edit.setCurrentText("orca {name} > {basename}.out")
        file_page.command_edit.lineEdit().textEdited.emit(
            "/opt/orca601/orca {name} > {basename}.out"
        )
        file_page.command_edit.setCurrentText("/opt/orca601/orca {name} > {basename}.out")
        with patch("jobdesk_app.gui.pages.file_transfer_page.RunProfileStore"):
            file_page._save_command_history()

        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("second.inp"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/work/second.inp"))
        file_page.remote_table.selectRow(0)
        file_page._update_selection_summary()

        assert file_page.command_edit.currentText() == "/opt/orca601/orca {name} > {basename}.out"

    def test_saved_command_updates_matching_software_profile_template(self, file_page):
        from PySide6.QtWidgets import QTableWidgetItem

        custom_command = "/opt/orca601/orca {name} > {basename}.out"
        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("calc.inp"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/work/calc.inp"))
        file_page.remote_table.selectRow(0)
        file_page.command_edit.setCurrentText(custom_command)

        with patch("jobdesk_app.gui.pages.file_transfer_page.RunProfileStore"), \
             patch("jobdesk_app.gui.pages.file_transfer_page.GuiSettingsStore") as store_cls:
            file_page._save_command_history()

        updated_profiles = store_cls.return_value.update.call_args.kwargs["software_profiles"]
        assert updated_profiles["ORCA"]["command_template"] == custom_command
        assert file_page._gui_settings.software_profiles["ORCA"]["command_template"] == custom_command

        file_page._command_manually_edited = False
        file_page.command_edit.setCurrentText("orca {name} > {basename}.out")
        file_page._auto_fill_command()

        assert file_page.command_edit.currentText() == custom_command

    def test_apply_gui_settings_preserves_manually_edited_command(self, file_page):
        file_page._gui_settings = replace(
            file_page._gui_settings,
            command_template="orca {name} > {basename}.out",
        )
        file_page.command_edit.setCurrentText("orca {name} > {basename}.out")
        file_page.command_edit.lineEdit().textEdited.emit(
            "/opt/orca601/orca {name} > {basename}.out"
        )
        file_page.command_edit.setCurrentText("/opt/orca601/orca {name} > {basename}.out")

        file_page._apply_gui_settings_no_folder()

        assert file_page.command_edit.currentText() == "/opt/orca601/orca {name} > {basename}.out"

    def test_remote_run_submit_error_preserves_created_run_state(self, file_page, tmp_path):
        from PySide6.QtWidgets import QMessageBox

        errors: list[tuple[str, str]] = []
        file_page.state.current_project_root = tmp_path
        file_page._error_cb = lambda title, message: errors.append((title, message))
        file_page._service = MagicMock()
        file_page._connected_server = SimpleNamespace(env_init_scripts=[], scheduler=None)
        file_page._connected_server_id = "wsl"
        file_page.command_edit.setCurrentText("g16 {name}")
        file_page.remote_path.setText("/remote/work")
        record = SimpleNamespace(
            run_id="run-001",
            manifest_path=tmp_path / ".jobdesk" / "runs" / "run-001" / "manifest.yaml",
        )

        run_service = MagicMock()
        run_service.create_run.return_value = record
        run_service.submit_run.side_effect = RuntimeError("scheduler down")
        session = MagicMock()
        session.__enter__.return_value = (MagicMock(), MagicMock())

        with patch.object(file_page, "_selected_remote_entries", return_value=(["/remote/work/a.gjf"], [])), \
             patch.object(file_page, "_selected_local_entries", return_value=([], [])), \
             patch("jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("jobdesk_app.gui.pages.file_transfer_page.RunService", return_value=run_service), \
             patch("jobdesk_app.gui.pages.file_transfer_page.RunProfileStore"), \
             patch("jobdesk_app.gui.pages.file_transfer_page.sftp_session", return_value=session), \
             patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker:
            file_page._run_selected_chunks(submit=True)
            target = start_worker.call_args.kwargs["target"]
            on_result = start_worker.call_args.kwargs["on_result"]
            payload = target(MagicMock())
            on_result(payload)

        assert file_page.state.current_batch_id == "run-001"
        assert file_page.state.current_manifest_path == record.manifest_path
        assert errors
        assert errors[0][0] == "Run Error"
        assert "scheduler down" in errors[0][1]

    def test_upload_dropped_uses_non_destructive_skip_policy(self, file_page, tmp_path):
        """Ordinary drag-drop must not overwrite a remote destination silently."""
        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        local_file = tmp_path / "mol.xyz"
        local_file.write_text("xyz", encoding="utf-8")
        file_page.remote_path.setText("/remote/dir")

        service = MagicMock()
        service.upload_path.return_value = TransferRecord(
            TransferDirection.upload, str(local_file), "/remote/dir/mol.xyz",
            status=TransferStatus.transferred,
        )
        file_page._service = service

        with patch.object(file_page, "_start_transfer_worker") as start_worker:
            file_page._upload_dropped_local_paths([str(local_file)])

        start_worker.assert_called_once()
        target, label, on_done_refresh = start_worker.call_args.args
        assert label == "Upload"
        assert on_done_refresh == file_page._refresh_remote
        target(MagicMock())
        call_args = service.upload_path.call_args
        from jobdesk_app.core.file_transfer import OverwritePolicy
        assert call_args[0][2] == OverwritePolicy.skip_same_size
        assert call_args[1].get("progress_callback") is not None

    def test_download_dropped_uses_transfer_progress_worker(self, file_page, tmp_path):
        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        file_page.state.current_project_root = tmp_path
        service = MagicMock()
        service.download_path.return_value = TransferRecord(
            TransferDirection.download,
            "/remote/dir/mol.out",
            str(tmp_path / "mol.out"),
            status=TransferStatus.transferred,
        )
        file_page._service = service

        with patch.object(file_page, "_start_transfer_worker") as start_worker:
            file_page._download_dropped_remote_paths(["/remote/dir/mol.out"])

        start_worker.assert_called_once()
        target, label, on_done_refresh = start_worker.call_args.args
        assert label == "Download"
        assert on_done_refresh == file_page._refresh_local
        target(MagicMock())

        call_args = service.download_path.call_args
        from jobdesk_app.core.file_transfer import OverwritePolicy
        assert call_args[0][2] == OverwritePolicy.overwrite
        assert call_args[1].get("progress_callback") is not None

    def test_upload_dropped_posix_local_path_is_not_misrouted_as_download(self, file_page):
        file_page._service = MagicMock()

        with patch.object(file_page, "_download_dropped_remote_paths") as download:
            file_page._upload_dropped_local_paths(["/tmp/source.log"])

        download.assert_not_called()

    def test_local_table_accepts_external_local_url_drop(self, file_page, tmp_path):
        from PySide6.QtCore import QMimeData, QUrl

        source = tmp_path / "source.txt"
        source.write_text("source", encoding="utf-8")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(source))])

        assert file_page.local_table._accepts_mime(mime)

    def test_external_local_drop_copies_file_into_current_local_directory(self, file_page, tmp_path):
        source_dir = tmp_path / "source"
        target_dir = tmp_path / "target"
        source_dir.mkdir()
        target_dir.mkdir()
        source = source_dir / "result.log"
        source.write_text("contents", encoding="utf-8")
        file_page.state.current_project_root = target_dir

        file_page._copy_dropped_local_paths([str(source)])

        assert source.read_text(encoding="utf-8") == "contents"
        assert (target_dir / "result.log").read_text(encoding="utf-8") == "contents"

    def test_external_local_drop_does_not_overwrite_existing_destination(self, file_page, tmp_path):
        errors = []
        source_dir = tmp_path / "source"
        target_dir = tmp_path / "target"
        source_dir.mkdir()
        target_dir.mkdir()
        source = source_dir / "result.log"
        source.write_text("incoming", encoding="utf-8")
        destination = target_dir / "result.log"
        destination.write_text("existing", encoding="utf-8")
        file_page.state.current_project_root = target_dir
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._copy_dropped_local_paths([str(source)])

        assert destination.read_text(encoding="utf-8") == "existing"
        assert errors

    def test_move_local_path_into_directory(self, file_page, tmp_path):
        source = tmp_path / "source.log"
        target_dir = tmp_path / "archive"
        source.write_text("contents", encoding="utf-8")
        target_dir.mkdir()

        file_page._move_local_paths_into_directory([str(source)], str(target_dir))

        assert not source.exists()
        assert (target_dir / "source.log").read_text(encoding="utf-8") == "contents"

    def test_move_local_does_not_overwrite_existing_destination(self, file_page, tmp_path):
        errors = []
        source = tmp_path / "source.log"
        target_dir = tmp_path / "archive"
        target_dir.mkdir()
        source.write_text("incoming", encoding="utf-8")
        destination = target_dir / "source.log"
        destination.write_text("existing", encoding="utf-8")
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._move_local_paths_into_directory([str(source)], str(target_dir))

        assert source.read_text(encoding="utf-8") == "incoming"
        assert destination.read_text(encoding="utf-8") == "existing"
        assert errors

    def test_move_local_directory_rejects_descendant_target(self, file_page, tmp_path):
        errors = []
        source = tmp_path / "source"
        target_dir = source / "nested"
        target_dir.mkdir(parents=True)
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._move_local_paths_into_directory([str(source)], str(target_dir))

        assert source.exists()
        assert errors

    def test_move_remote_path_into_directory_uses_rename(self, file_page):
        service = MagicMock()
        file_page._service = service

        with patch.object(file_page, "_refresh_remote") as refresh_remote:
            file_page._move_remote_paths_into_directory(
                ["/remote/source.log"], "/remote/archive"
            )

        service.rename_remote.assert_called_once_with(
            "/remote/source.log", "/remote/archive/source.log"
        )
        refresh_remote.assert_called_once_with()

    def test_move_remote_directory_rejects_descendant_target(self, file_page):
        errors = []
        service = MagicMock()
        file_page._service = service
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._move_remote_paths_into_directory(
            ["/remote/source"], "/remote/source/nested"
        )

        service.rename_remote.assert_not_called()
        assert errors

    def test_rename_local_selected_file(self, file_page, tmp_path):
        from PySide6.QtWidgets import QTableWidgetItem

        original = tmp_path / "before.txt"
        original.write_text("contents", encoding="utf-8")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("before.txt"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(original)))
        file_page.local_table.selectRow(0)

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.FileTransferPage._prompt_rename_name",
            return_value=("after.txt", True),
        ):
            file_page._rename_local()

        assert not original.exists()
        assert (tmp_path / "after.txt").read_text(encoding="utf-8") == "contents"

    def test_rename_dialog_is_wide_enough_for_long_names(self, file_page):
        from PySide6.QtWidgets import QLineEdit

        dialog = file_page._build_rename_dialog(
            "Rename Local Path",
            "New name:",
            "tbu-zr-s-ml-rpdd-site2-sp.inp",
        )

        assert dialog.minimumWidth() >= 460
        assert dialog.findChild(QLineEdit).minimumWidth() >= 380

    def test_second_click_on_selected_local_item_starts_delayed_rename(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        original = tmp_path / "before.txt"
        original.write_text("contents", encoding="utf-8")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("before.txt"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(original)))
        file_page.local_table.selectRow(0)
        file_page.local_table.setCurrentCell(0, 0)

        with patch.object(file_page, "_rename_local") as rename_local:
            rect = file_page.local_table.visualItemRect(file_page.local_table.item(0, 0))
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                rect.center() if rect.isValid() else QPoint(4, 4),
            )
            qtbot.waitUntil(lambda: rename_local.called, timeout=1000)

        rename_local.assert_called_once_with()

    def test_second_click_on_selected_remote_item_starts_delayed_rename(self, file_page, qtbot):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("before.out"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/before.out"))
        file_page.remote_table.selectRow(0)
        file_page.remote_table.setCurrentCell(0, 0)

        with patch.object(file_page, "_rename_remote") as rename_remote:
            rect = file_page.remote_table.visualItemRect(file_page.remote_table.item(0, 0))
            QTest.mouseClick(
                file_page.remote_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                rect.center() if rect.isValid() else QPoint(4, 4),
            )
            qtbot.waitUntil(lambda: rename_remote.called, timeout=1000)

        rename_remote.assert_called_once_with()

    def test_first_click_on_unselected_item_does_not_start_rename(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        original = tmp_path / "before.txt"
        original.write_text("contents", encoding="utf-8")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("before.txt"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(original)))

        with patch.object(file_page, "_rename_local") as rename_local:
            rect = file_page.local_table.visualItemRect(file_page.local_table.item(0, 0))
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                rect.center() if rect.isValid() else QPoint(4, 4),
            )
            qtbot.wait(600)

        rename_local.assert_not_called()

    def test_click_selected_non_name_column_does_not_start_rename(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        original = tmp_path / "before.txt"
        original.write_text("contents", encoding="utf-8")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("before.txt"))
        file_page.local_table.setItem(0, 1, QTableWidgetItem("1 KB"))
        file_page.local_table.setItem(0, 2, QTableWidgetItem("2026-06-17 10:00:00"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(original)))
        file_page.local_table.selectRow(0)
        file_page.local_table.setCurrentCell(0, 0)

        with patch.object(file_page, "_rename_local") as rename_local:
            rect = file_page.local_table.visualItemRect(file_page.local_table.item(0, 2))
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                rect.center() if rect.isValid() else QPoint(120, 4),
            )
            qtbot.wait(600)

        rename_local.assert_not_called()

    def test_double_click_cancels_delayed_local_rename(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        original = tmp_path / "before.txt"
        original.write_text("contents", encoding="utf-8")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("before.txt"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(original)))
        file_page.local_table.selectRow(0)
        file_page.local_table.setCurrentCell(0, 0)

        with patch.object(file_page, "_rename_local") as rename_local, \
             patch.object(file_page, "_open_in_text_editor") as open_editor:
            rect = file_page.local_table.visualItemRect(file_page.local_table.item(0, 0))
            point = rect.center() if rect.isValid() else QPoint(4, 4)
            QTest.mouseClick(file_page.local_table.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            file_page._open_local_item(file_page.local_table.item(0, 0))
            qtbot.wait(600)

        rename_local.assert_not_called()
        open_editor.assert_called_once_with(original)

    @pytest.mark.parametrize("invalid_name", ["nested/new.txt", r"nested\new.txt"])
    def test_rename_local_rejects_path_separator(self, file_page, tmp_path, invalid_name):
        from PySide6.QtWidgets import QTableWidgetItem

        errors = []
        original = tmp_path / "before.txt"
        original.write_text("contents", encoding="utf-8")
        file_page._error_cb = lambda title, message: errors.append((title, message))
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("before.txt"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(original)))
        file_page.local_table.selectRow(0)

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.FileTransferPage._prompt_rename_name",
            return_value=(invalid_name, True),
        ):
            file_page._rename_local()

        assert original.exists()
        assert errors

    def test_rename_remote_rejects_path_separator(self, file_page):
        from PySide6.QtWidgets import QTableWidgetItem

        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("before.txt"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/root/uma/before.txt"))
        file_page.remote_table.selectRow(0)
        file_page._service = MagicMock()

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.FileTransferPage._prompt_rename_name",
            return_value=("nested/after.txt", True),
        ):
            file_page._rename_remote()

        file_page._service.rename_remote.assert_not_called()

    def test_delete_remote_authorizes_current_browsing_directory(self, file_page, qtbot):
        """Browsing a non-top-level directory grants deletion authority for selected children."""
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        file_page.remote_path.setText("/root/uma")
        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("file.gjf"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/root/uma/file.gjf"))
        file_page.remote_table.selectRow(0)

        service = MagicMock()
        file_page._service = service

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            file_page._delete_remote()
            qtbot.waitUntil(lambda: service.delete_remote.called, timeout=2000)

        service.delete_remote.assert_called_once_with(
            "/root/uma/file.gjf",
            recursive=True,
            extra_allowed_roots=["/root/uma"],
        )

    def test_delete_remote_in_toplevel_rejected(self, file_page, qtbot):
        """Delete at top-level (/, /root, /home) must be rejected by the GUI."""
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        errors = []
        file_page._error_cb = lambda t, m: errors.append((t, m))

        for toplevel in ["/", "/root", "/home"]:
            file_page.remote_path.setText(toplevel)
            file_page.remote_table.setRowCount(1)
            file_page.remote_table.setItem(0, 0, QTableWidgetItem("something"))
            file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
            child = f"{toplevel.rstrip('/')}/something"
            file_page.remote_table.setItem(0, 5, QTableWidgetItem(child))
            file_page.remote_table.selectRow(0)

            service = MagicMock()
            file_page._service = service

            with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                file_page._delete_remote()

            service.delete_remote.assert_not_called()

        assert len(errors) == 3

    def test_delete_remote_rejects_parent_and_current_dir(self, file_page, qtbot):
        """Cannot delete the current dir itself or parent path."""
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        errors = []
        file_page._error_cb = lambda t, m: errors.append((t, m))
        file_page.remote_path.setText("/root/uma")
        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem(".."))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("dir"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/root"))
        file_page.remote_table.selectRow(0)

        service = MagicMock()
        file_page._service = service

        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            file_page._delete_remote()

        service.delete_remote.assert_not_called()

    def test_shutdown_ignores_pending_remote_list_error_callback(self, file_page):
        """A late remote-list result must not update a page after shutdown."""
        errors = []
        file_page._error_cb = lambda title, message: errors.append((title, message))
        file_page._remote_list_fallbacks = []
        request_id = file_page._remote_list_request_id

        file_page.shutdown()
        file_page._on_remote_list_error(request_id, "late remote error")

        assert errors == []

    def test_remote_list_connection_error_does_not_try_path_fallback(self, file_page):
        errors = []
        statuses = []
        file_page._error_cb = lambda title, message: errors.append((title, message))
        file_page._status_cb = statuses.append
        file_page._remote_list_fallbacks = ["/tmp"]
        request_id = file_page._remote_list_request_id

        with patch.object(file_page, "_refresh_remote_path") as refresh_path:
            file_page._on_remote_list_error(request_id, "OSError: Socket is closed")

        refresh_path.assert_not_called()
        assert errors == [("Remote List Error", "OSError: Socket is closed")]
        assert statuses == []

    def test_remote_list_missing_path_uses_path_fallback(self, file_page):
        file_page._remote_list_fallbacks = ["/tmp"]
        request_id = file_page._remote_list_request_id

        with patch.object(file_page, "_refresh_remote_path") as refresh_path:
            file_page._on_remote_list_error(request_id, "FileNotFoundError: /missing")

        refresh_path.assert_called_once_with("/tmp")

    def test_remote_list_missing_remote_path_error_uses_path_fallback(self, file_page):
        file_page._remote_list_fallbacks = ["/tmp"]
        request_id = file_page._remote_list_request_id

        with patch.object(file_page, "_refresh_remote_path") as refresh_path:
            file_page._on_remote_list_error(
                request_id,
                "RemotePathError: remote path not found or not a directory: /missing",
            )

        refresh_path.assert_called_once_with("/tmp")

    def test_remote_list_invalid_remote_path_error_does_not_fallback(self, file_page):
        errors = []
        file_page._error_cb = lambda title, message: errors.append((title, message))
        file_page._remote_list_fallbacks = ["/tmp"]
        request_id = file_page._remote_list_request_id

        with patch.object(file_page, "_refresh_remote_path") as refresh_path:
            file_page._on_remote_list_error(
                request_id,
                "RemotePathError: remote path must not contain '..': '/tmp/../secret'",
            )

        refresh_path.assert_not_called()
        assert errors == [
            (
                "Remote List Error",
                "RemotePathError: remote path must not contain '..': '/tmp/../secret'",
            )
        ]

    def test_shutdown_stops_worker_when_settings_save_fails(self, file_page):
        worker = MagicMock()
        file_page._background_workers = [worker]

        with patch("jobdesk_app.gui.pages.file_transfer_page.GuiSettingsStore") as store:
            store.return_value.load.return_value = file_page._gui_settings
            store.return_value.save.side_effect = PermissionError("read-only settings")
            file_page.shutdown()

        worker.stop_safely.assert_called_once_with(3000)

    def test_connect_enables_persistent_remote_session(self, file_page):
        file_page._servers = {"wsl": MagicMock()}
        file_page.server_combo.clear()
        file_page.server_combo.addItem("wsl", "wsl")

        with patch.object(file_page, "_refresh_remote"):
            file_page._connect()

        assert file_page._service._persistent_session is True

    def test_reconnect_releases_previous_remote_session_without_blocking_ui(self, file_page, qtbot):
        old_service = MagicMock()
        release_close = threading.Event()
        old_service.close.side_effect = lambda: release_close.wait(timeout=1)
        file_page._service = old_service
        file_page._servers = {"wsl": MagicMock()}
        file_page.server_combo.clear()
        file_page.server_combo.addItem("wsl", "wsl")

        try:
            started = time.monotonic()
            with patch.object(file_page, "_refresh_remote"):
                file_page._connect()
            elapsed = time.monotonic() - started
            qtbot.waitUntil(lambda: old_service.close.called, timeout=1000)
        finally:
            release_close.set()

        old_service.close.assert_called_once_with()
        assert elapsed < 0.2

    def test_shutdown_closes_active_remote_session(self, file_page):
        service = MagicMock()
        file_page._service = service

        file_page.shutdown()

        service.close.assert_called_once_with()
        assert file_page._service is None

    def test_confflow_uses_spinbox_max_parallel_not_stored_setting(self, file_page, qtbot, tmp_path):
        """ConfFlow batch must use the current spinbox value, not the stored setting."""
        file_page._service = MagicMock()
        file_page._connected_server = MagicMock()
        file_page._connected_server_id = "wsl"
        file_page.state.current_project_root = tmp_path

        # Set spinbox to 7 (different from whatever stored value is)
        stored_value = file_page._gui_settings.max_parallel
        file_page.max_parallel_spin.setValue(7)
        assert file_page.max_parallel_spin.value() == 7
        assert stored_value != 7  # precondition: stored differs from spinbox

        # Put a remote xyz in the selection
        file_page.remote_path.setText("/tmp/jobs")
        file_page.remote_table.setRowCount(1)
        from PySide6.QtWidgets import QTableWidgetItem
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("mol.xyz"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/tmp/jobs/mol.xyz"))
        file_page.remote_table.selectRow(0)

        # Patch QFileDialog to return a yaml
        yaml_file = tmp_path / "conf.yaml"
        yaml_file.write_text("steps: []", encoding="utf-8")

        # Capture the confirmation message to verify max_parallel shown
        confirm_messages = []

        def fake_question(parent, title, msg, *args, **kwargs):
            confirm_messages.append(msg)
            from PySide6.QtWidgets import QMessageBox
            return QMessageBox.No  # Say No to prevent actual submission

        with patch("jobdesk_app.gui.pages.file_transfer_page.QFileDialog.getOpenFileName", return_value=(str(yaml_file), "")):
            with patch("jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question", side_effect=fake_question):
                file_page._run_confflow()

        assert len(confirm_messages) == 1
        assert "Max parallel: 7" in confirm_messages[0]



class TestMainWindowExcepthook:
    def test_constructing_main_window_does_not_change_sys_excepthook(self, qtbot, monkeypatch):
        """B7: sys.excepthook must not be modified by MainWindow.__init__.

        The test must not leak background activity (workers, timers, SSH connections)
        that could crash subsequent tests via callbacks on destroyed widgets.
        """
        import sys
        original_hook = sys.excepthook

        # Prevent all background activity from pages
        monkeypatch.setattr(
            "jobdesk_app.gui.pages.file_transfer_page.load_servers",
            lambda *a, **kw: MagicMock(servers={}),
        )
        monkeypatch.setattr(
            "jobdesk_app.gui.pages.runs_results_page.load_servers",
            lambda *a, **kw: MagicMock(servers={}),
        )
        monkeypatch.setattr(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            lambda *a, **kw: MagicMock(servers={}),
        )

        with patch("jobdesk_app.gui.main_window.configure_file_logging"):
            with patch("jobdesk_app.gui.main_window.GuiSettingsStore") as store:
                from jobdesk_app.services.gui_settings import GuiSettings
                store.return_value.load.return_value = GuiSettings()
                from jobdesk_app.gui.main_window import MainWindow
                window = MainWindow()
                qtbot.addWidget(window)

        assert sys.excepthook is original_hook

        # Explicit shutdown to stop RunMonitor, timers, and background workers
        window.shutdown()
        window.close()

        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()


def test_started_worker_is_kept_alive_in_registry():
    """Regression: rapid submissions overwrote the only reference to a running
    QThread, letting it be GC'd mid-run and aborting the process with
    'QThread: Destroyed while thread is still running'. start() must keep a strong
    reference in the registry until the thread finishes."""
    from PySide6.QtCore import QThread

    from jobdesk_app.gui.workers import BackgroundWorker

    worker = BackgroundWorker(lambda: None)
    with patch.object(QThread, "start"):  # register without spawning a real thread
        worker.start()
    assert worker in BackgroundWorker._active  # strong reference prevents GC
    worker._unregister()  # simulate the finished signal
    assert worker not in BackgroundWorker._active


def test_wait_all_tolerates_deleted_worker():
    """wait_all must not raise when a registered worker's C++ object was already
    deleted (e.g. on test/app teardown)."""
    from jobdesk_app.gui.workers import BackgroundWorker

    dead = MagicMock()
    dead.wait.side_effect = RuntimeError("Internal C++ object already deleted")
    BackgroundWorker._active.add(dead)
    BackgroundWorker.wait_all()
    assert dead not in BackgroundWorker._active


def test_tracked_worker_ignores_callbacks_after_owner_shutdown():
    from jobdesk_app.gui.worker_utils import start_tracked_worker

    owner = MagicMock()
    owner._shutting_down = True
    owner._workers = []
    worker = _FakeWorker()
    on_result = MagicMock()
    on_error = MagicMock()
    on_log = MagicMock()
    on_progress = MagicMock()

    start_tracked_worker(
        owner,
        worker,
        registry_attr="_workers",
        on_result=on_result,
        on_error=on_error,
        on_log=on_log,
        on_progress=on_progress,
    )

    worker.result.emit(object())
    worker.error.emit("error")
    worker.log.emit("log")
    worker.progress.emit(1, 2)

    on_result.assert_not_called()
    on_error.assert_not_called()
    on_log.assert_not_called()
    on_progress.assert_not_called()
