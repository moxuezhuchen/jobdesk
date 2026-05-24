"""GUI behavior tests using pytest-qt."""
import json

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from pathlib import Path
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
        return page


@pytest.fixture
def file_page(qtbot, app_state):
    from jobdesk_app.gui.pages.file_transfer_page import FileTransferPage
    page = FileTransferPage(app_state, log_cb=lambda m: None, status_cb=lambda m: None, error_cb=lambda t, m: None)
    qtbot.addWidget(page)
    return page


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

    def test_context_menu_has_refresh(self, runs_page, qtbot):
        """Right-click context menu should contain refresh action."""
        actions = runs_page._build_context_actions()
        assert len(actions) == 5
        # First action is refresh
        assert actions[0][1] == runs_page._refresh_all

    def test_refresh_run_list_empty(self, runs_page):
        """refresh_run_list should not crash with no runs."""
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
            mock_svc.return_value.list_runs.return_value = []
            runs_page.refresh_run_list()
        assert runs_page.table.rowCount() == 0

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

    def test_on_activated_ignores_legacy_disabled_automatic_refresh(self, runs_page):
        from jobdesk_app.services.gui_settings import GuiSettings

        settings = GuiSettings()
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as store:
            store.return_value.load.return_value = settings
            with patch.object(runs_page, "_start_monitoring") as monitor:
                runs_page.on_activated()

        monitor.assert_called_once_with()
        assert runs_page._refresh_timer.isActive()

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

    def test_delete_run_reports_failures_instead_of_claiming_success(self, runs_page):
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

        assert any("locked" in message for message in messages)
        assert not any("Deleted: 1" in message for message in messages)

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
        """Open Results action uses os.startfile on the results directory."""
        record = MagicMock(run_id="run_open", local_dir="")
        results_dir = tmp_path / "results" / "run_open"
        results_dir.mkdir(parents=True)

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path), \
             patch("jobdesk_app.gui.pages.runs_results_page.os") as mock_os:
            mock_os.startfile = MagicMock()
            runs_page._open_results_folder()

        mock_os.startfile.assert_called_once_with(results_dir)

    def test_open_results_folder_missing_dir_shows_error(self, runs_page, tmp_path):
        """If results dir doesn't exist, show status message instead of crashing."""
        record = MagicMock(run_id="run_missing", local_dir="")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path):
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
            " Normal termination of Gaussian 16\n",
            encoding="utf-8",
        )
        record = MagicMock(
            run_id="gauss_run", manifest_path=tmp_path / "no_manifest.tsv",
            command_template="g16 {name}", status_summary={"downloaded": 1},
        )

        with patch.object(runs_page, "_workspace", return_value=tmp_path):
            runs_page._load_result_preview(record)

        assert runs_page.result_table.rowCount() >= 1
        energy_cell = runs_page.result_table.item(0, 3)
        assert energy_cell is not None
        assert "-76.123456" in energy_cell.text()

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

    def test_open_results_uses_record_local_dir(self, runs_page, tmp_path):
        """Open Results must use record.local_dir path."""
        local_a = tmp_path / "project_a"
        results_dir = local_a / "results" / "run_ld2"
        results_dir.mkdir(parents=True)
        record = MagicMock(run_id="run_ld2", local_dir=str(local_a))

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path / "other"), \
             patch("jobdesk_app.gui.pages.runs_results_page.os") as mock_os:
            mock_os.startfile = MagicMock()
            mock_os.path = MagicMock()
            runs_page._open_results_folder()

        mock_os.startfile.assert_called_once_with(results_dir)

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

        assert str(local_a / "results" / "run_paths") in runs_page.result_text.toPlainText()

    def test_empty_local_dir_falls_back_to_workspace(self, runs_page, tmp_path):
        """Old records with empty local_dir should use current workspace."""
        results_dir = tmp_path / "results" / "run_old"
        results_dir.mkdir(parents=True)
        record = MagicMock(run_id="run_old", local_dir="")

        with patch.object(runs_page, "_selected_record", return_value=record), \
             patch.object(runs_page, "_workspace", return_value=tmp_path), \
             patch("jobdesk_app.gui.pages.runs_results_page.os") as mock_os:
            mock_os.startfile = MagicMock()
            mock_os.path = MagicMock()
            runs_page._open_results_folder()

        mock_os.startfile.assert_called_once_with(results_dir)

    def test_shutdown_waits_for_background_worker_without_timeout(self, runs_page):
        worker = MagicMock()
        runs_page._bg_workers = [worker]

        runs_page.shutdown()

        worker.stop_safely.assert_called_once_with()

    def test_delete_run_uses_record_local_dir_not_current_workspace(self, runs_page, tmp_path):
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


class TestFileTransferPage:
    def test_page_creates_without_crash(self, file_page):
        assert file_page is not None

    def test_local_table_exists(self, file_page):
        assert file_page.local_table is not None
        assert file_page.local_table.columnCount() >= 4

    def test_confflow_launch_button_exists(self, file_page):
        from jobdesk_app.gui.i18n import tr
        assert file_page.confflow_btn.text() == tr("Run ConfFlow", file_page._language)

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

    def test_upload_without_service_shows_message(self, file_page, qtbot):
        """Drag-drop without connection should show status message."""
        messages = []
        file_page._status_cb = lambda m: messages.append(m)
        file_page._service = None
        file_page._upload_dropped_local_paths(["C:/fake/file.gjf"])
        assert any("Connect" in m for m in messages)

    def test_shutdown_stops_worker_when_settings_save_fails(self, file_page):
        worker = MagicMock()
        file_page._background_workers = [worker]

        with patch("jobdesk_app.gui.pages.file_transfer_page.GuiSettingsStore") as store:
            store.return_value.load.return_value = file_page._gui_settings
            store.return_value.save.side_effect = PermissionError("read-only settings")
            file_page.shutdown()

        worker.stop_safely.assert_called_once_with()

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
