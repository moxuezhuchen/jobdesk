"""GUI behavior tests for the File Transfer page."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tests.test_gui_behavior.conftest import _FakeWorker

pytest.importorskip("PySide6", reason="PySide6 not installed")


class TestFileTransferPage:
    @staticmethod
    def _name_label_point(table, row: int):
        from PySide6.QtCore import QPoint

        rect = table.visualItemRect(table.item(row, 0))
        if not rect.isValid():
            return QPoint(8, 8)
        return QPoint(rect.left() + 18, rect.center().y())

    @staticmethod
    def _name_column_blank_point(table, row: int):
        from PySide6.QtCore import QPoint

        rect = table.visualItemRect(table.item(row, 0))
        if not rect.isValid():
            return QPoint(120, 8)
        return QPoint(rect.right() - 6, rect.center().y())

    def test_page_creates_without_crash(self, file_page):
        assert file_page is not None

    def test_file_transfer_buttons_have_feedback_roles(self, file_page):
        from jobdesk_app.gui.button_feedback import ButtonRole

        # The Files page no longer carries the run buttons — they moved
        # to SubmitPage. Refresh / open terminal stay on this page.
        assert file_page.refresh_btn.property("buttonRole") == ButtonRole.REFRESH_ACTION.value
        assert file_page.open_terminal_btn.property("buttonRole") == ButtonRole.INSTANT_ACTION.value
        assert file_page.submit_btn.objectName() == "FilesSubmitBtn"
        assert not file_page.connection_label.isHidden()

    def test_file_transfer_refresh_feedback_stays_pending_until_async_completion(self, file_page):
        from jobdesk_app.gui.i18n import tr

        with (
            patch.object(file_page, "_refresh_local") as refresh_local,
            patch.object(file_page, "_refresh_remote") as refresh_remote,
        ):
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
        from dataclasses import replace

        from jobdesk_app.gui.i18n import tr

        file_page._service = None
        file_page._gui_settings = replace(file_page._gui_settings, auto_connect=False)

        with patch.object(file_page, "_refresh_local") as refresh_local:
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

        with (
            patch("jobdesk_app.gui.pages.file_transfer_page.build_terminal_launch", return_value=launch) as build,
            patch("jobdesk_app.gui.pages.file_transfer_page.launch_terminal") as launcher,
        ):
            file_page._open_terminal_here()

        build.assert_called_once()
        assert build.call_args.args[0] is server
        assert build.call_args.args[1] == "/home/xianj/qhf"
        launcher.assert_called_once_with(launch)

    def test_transfer_progress_shows_download_speed(self, file_page):
        worker = _FakeWorker()

        with patch("jobdesk_app.gui.pages.file_transfer_page.time.monotonic", side_effect=[100.0, 104.0]):
            file_page._start_transfer_worker(worker, "Download", lambda: None)
            worker.progress.emit(4 * 1024 * 1024, 8 * 1024 * 1024)

        assert file_page.progress_bar.value() == 50
        assert file_page.progress_bar.format() == "Download: 4096K / 8192K @ 1.0 MB/s"

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
        file_page.local_table.move_local_files.connect(lambda paths, target: moves.append((paths, target)))

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
        file_page.remote_table.move_remote_files.connect(lambda paths, target: moves.append((paths, target)))

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

    def test_external_local_url_drop_on_remote_table_uploads_to_current_remote_dir(self, file_page, qtbot, tmp_path):
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

    def test_remote_path_drop_on_local_table_downloads_to_current_local_dir(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import QMimeData

        from jobdesk_app.core.file_transfer import OverwritePolicy
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
        assert service.download_path.call_args.args[2] == OverwritePolicy.overwrite

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
        from dataclasses import replace

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
        from dataclasses import replace

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

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.start_context_worker", return_value=worker
        ) as start_worker:
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

        with (
            patch("jobdesk_app.gui.pages.file_transfer_page.tempfile.gettempdir", return_value=str(tmp_path)),
            patch.object(file_page._remote_edit_manager, "open_in_text_editor", return_value=True),
            patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker,
        ):
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
        from jobdesk_app.core.file_transfer import OverwritePolicy

        temp_file = tmp_path / "result.gjf"
        temp_file.write_text("before\n", encoding="utf-8")
        service = MagicMock()
        file_page._service = service

        file_page._register_remote_edit_session("/remote/work/result.gjf", temp_file)
        temp_file.write_text("after\n\n", encoding="utf-8")

        with (
            patch("jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question") as question,
            patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker,
        ):
            file_page._check_remote_edit_sessions()
            target = start_worker.call_args.kwargs["target"]
            result = target(MagicMock())
            start_worker.call_args.kwargs["on_result"](result)

        question.assert_not_called()
        service.upload_path.assert_called_once()
        call_args = service.upload_path.call_args
        assert Path(call_args.args[0]) == temp_file
        assert call_args.args[1] == "/remote/work/result.gjf"
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

    def test_new_local_file_uses_configured_text_editor(self, file_page, tmp_path):
        from dataclasses import replace

        file_page.state.current_project_root = tmp_path
        file_page._gui_settings = replace(file_page._gui_settings, text_editor_path="C:/Tools/editor.exe")

        with (
            patch(
                "jobdesk_app.gui.pages.file_transfer_page.QInputDialog.getText",
                return_value=("new.txt", True),
            ),
            patch.object(file_page, "_refresh_local"),
            patch.object(file_page._remote_edit_manager, "open_in_text_editor") as open_editor,
        ):
            file_page._file_operations.new_file_local()

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

        from jobdesk_app.core.file_transfer import OverwritePolicy
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
            TransferDirection.upload,
            str(local_file),
            "/remote/dir/test.gjf",
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
            TransferDirection.download,
            str(tmp_path / "result.log"),
            "/remote/result.log",
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

        with (
            patch(
                "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker,
        ):
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

        with (
            patch(
                "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ),
            patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker", create=True) as start_worker,
        ):
            file_page._delete_local()

        assert local_file.exists()
        target = start_worker.call_args.kwargs["target"]
        target(MagicMock())
        assert not local_file.exists()

    def test_upload_dropped_uses_non_destructive_skip_policy(self, file_page, tmp_path):
        """Ordinary drag-drop must not overwrite a remote destination silently."""
        from jobdesk_app.core.file_transfer import OverwritePolicy
        from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus

        local_file = tmp_path / "mol.xyz"
        local_file.write_text("xyz", encoding="utf-8")
        file_page.remote_path.setText("/remote/dir")

        service = MagicMock()
        service.upload_path.return_value = TransferRecord(
            TransferDirection.upload,
            str(local_file),
            "/remote/dir/mol.xyz",
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
        assert call_args[0][2] == OverwritePolicy.skip_same_size
        assert call_args[1].get("progress_callback") is not None

    def test_download_dropped_uses_transfer_progress_worker(self, file_page, tmp_path):
        from jobdesk_app.core.file_transfer import OverwritePolicy
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

        file_page._file_operations.copy_dropped_local_paths([str(source)])

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

        file_page._file_operations.copy_dropped_local_paths([str(source)])

        assert destination.read_text(encoding="utf-8") == "existing"
        assert errors

    def test_move_local_path_into_directory(self, file_page, tmp_path):
        source = tmp_path / "source.log"
        target_dir = tmp_path / "archive"
        source.write_text("contents", encoding="utf-8")
        target_dir.mkdir()

        file_page._file_operations.move_local_paths_into_directory([str(source)], str(target_dir))

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

        file_page._file_operations.move_local_paths_into_directory([str(source)], str(target_dir))

        assert source.read_text(encoding="utf-8") == "incoming"
        assert destination.read_text(encoding="utf-8") == "existing"
        assert errors

    def test_move_local_directory_rejects_descendant_target(self, file_page, tmp_path):
        errors = []
        source = tmp_path / "source"
        target_dir = source / "nested"
        target_dir.mkdir(parents=True)
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._file_operations.move_local_paths_into_directory([str(source)], str(target_dir))

        assert source.exists()
        assert errors

    def test_move_remote_path_into_directory_uses_rename(self, file_page):
        service = MagicMock()
        file_page._service = service

        with patch.object(file_page, "_refresh_remote") as refresh_remote:
            file_page._file_operations.move_remote_paths_into_directory(["/remote/source.log"], "/remote/archive")

        service.rename_remote.assert_called_once_with("/remote/source.log", "/remote/archive/source.log")
        refresh_remote.assert_called_once_with()

    def test_move_remote_directory_rejects_descendant_target(self, file_page):
        errors = []
        service = MagicMock()
        file_page._service = service
        file_page._error_cb = lambda title, message: errors.append((title, message))

        file_page._file_operations.move_remote_paths_into_directory(["/remote/source"], "/remote/source/nested")

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

        dialog = file_page._build_name_input_dialog(
            "Rename Local Path",
            "New name:",
            "tbu-zr-s-ml-rpdd-site2-sp.inp",
        )

        assert dialog.minimumWidth() >= 460
        assert dialog.findChild(QLineEdit).minimumWidth() >= 380

    def test_new_folder_dialog_matches_rename_dialog_size(self, file_page):
        from PySide6.QtWidgets import QLineEdit

        rename_dialog = file_page._build_name_input_dialog(
            "Rename Remote Path",
            "New name:",
            "tbu-zr-s-ml-rpdd-site2-sp.inp",
        )
        folder_dialog = file_page._build_name_input_dialog(
            "New Remote Folder",
            "Folder name:",
            "",
        )

        assert folder_dialog.minimumWidth() == rename_dialog.minimumWidth()
        assert folder_dialog.findChild(QLineEdit).minimumWidth() == (rename_dialog.findChild(QLineEdit).minimumWidth())

    def test_mkdir_local_uses_wide_new_folder_prompt(self, file_page, tmp_path):
        from jobdesk_app.gui.i18n import tr

        file_page.state.current_project_root = tmp_path

        with (
            patch.object(file_page, "_prompt_new_folder_name", return_value=("created", True)) as prompt,
            patch(
                "jobdesk_app.gui.pages.file_transfer_page.QInputDialog.getText",
                side_effect=AssertionError("default QInputDialog.getText should not be used"),
            ),
            patch.object(file_page, "_refresh_local") as refresh_local,
        ):
            file_page._file_operations.mkdir_local()

        prompt.assert_called_once_with(
            tr("New Folder", file_page._language),
            tr("Folder name:", file_page._language),
        )
        assert (tmp_path / "created").is_dir()
        refresh_local.assert_called_once_with()

    def test_mkdir_remote_uses_wide_new_folder_prompt(self, file_page):
        file_page._service = MagicMock()
        file_page.remote_path.setText("/remote/jobs")

        with (
            patch.object(file_page, "_prompt_new_folder_name", return_value=("created", True)) as prompt,
            patch(
                "jobdesk_app.gui.pages.file_transfer_page.QInputDialog.getText",
                side_effect=AssertionError("default QInputDialog.getText should not be used"),
            ),
            patch.object(file_page, "_refresh_remote") as refresh_remote,
        ):
            file_page._file_operations.mkdir_remote()

        prompt.assert_called_once_with("New Remote Folder", "Folder name:")
        file_page._service.mkdir_remote.assert_called_once_with("/remote/jobs/created")
        refresh_remote.assert_called_once_with()

    def test_second_click_on_selected_local_item_starts_delayed_rename(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import Qt
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
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                self._name_label_point(file_page.local_table, 0),
            )
            qtbot.waitUntil(lambda: rename_local.called, timeout=1500)

        rename_local.assert_called_once_with()

    def test_second_click_on_selected_remote_item_starts_delayed_rename(self, file_page, qtbot):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("before.out"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/before.out"))
        file_page.remote_table.selectRow(0)
        file_page.remote_table.setCurrentCell(0, 0)

        with patch.object(file_page, "_rename_remote") as rename_remote:
            QTest.mouseClick(
                file_page.remote_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                self._name_label_point(file_page.remote_table, 0),
            )
            qtbot.waitUntil(lambda: rename_remote.called, timeout=1500)

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
            qtbot.wait(900)

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
            qtbot.wait(900)

        rename_local.assert_not_called()

    def test_click_selected_name_column_blank_area_does_not_start_rename(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import Qt
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
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                self._name_column_blank_point(file_page.local_table, 0),
            )
            qtbot.wait(900)

        rename_local.assert_not_called()

    @pytest.mark.parametrize("modifier_name", ["ControlModifier", "ShiftModifier"])
    def test_modified_click_on_selected_name_does_not_start_rename(self, file_page, qtbot, tmp_path, modifier_name):
        from PySide6.QtCore import Qt
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
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                getattr(Qt, modifier_name),
                self._name_label_point(file_page.local_table, 0),
            )
            qtbot.wait(900)

        rename_local.assert_not_called()

    def test_click_selected_name_with_multiple_rows_selected_does_not_start_rename(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import QItemSelectionModel, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        file_page.local_table.setRowCount(2)
        for row, path in enumerate([first, second]):
            file_page.local_table.setItem(row, 0, QTableWidgetItem(path.name))
            file_page.local_table.setItem(row, 3, QTableWidgetItem("file"))
            file_page.local_table.setItem(row, 4, QTableWidgetItem(str(path)))
        file_page.local_table.setCurrentCell(0, 0)
        selection = file_page.local_table.selectionModel()
        for row in (0, 1):
            index = file_page.local_table.model().index(row, 0)
            selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)

        with patch.object(file_page, "_rename_local") as rename_local:
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                self._name_label_point(file_page.local_table, 0),
            )
            qtbot.wait(900)

        rename_local.assert_not_called()

    def test_pending_selected_click_rename_cancels_when_selection_becomes_multiple(
        self,
        file_page,
        qtbot,
        tmp_path,
    ):
        from PySide6.QtCore import QItemSelectionModel, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        file_page.local_table.setRowCount(2)
        for row, path in enumerate([first, second]):
            file_page.local_table.setItem(row, 0, QTableWidgetItem(path.name))
            file_page.local_table.setItem(row, 3, QTableWidgetItem("file"))
            file_page.local_table.setItem(row, 4, QTableWidgetItem(str(path)))
        file_page.local_table.selectRow(0)
        file_page.local_table.setCurrentCell(0, 0)

        with patch.object(file_page, "_rename_local") as rename_local:
            QTest.mouseClick(
                file_page.local_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                self._name_label_point(file_page.local_table, 0),
            )
            file_page.local_table.selectionModel().select(
                file_page.local_table.model().index(1, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
            qtbot.wait(900)

        rename_local.assert_not_called()

    def test_f2_renames_selected_local_item(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import Qt
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
            QTest.keyClick(file_page.local_table, Qt.Key_F2)
            qtbot.wait(50)

        rename_local.assert_called_once_with()

    def test_f2_does_not_rename_multiple_selected_local_items(self, file_page, qtbot, tmp_path):
        from PySide6.QtCore import QItemSelectionModel, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        first = tmp_path / "first.txt"
        second = tmp_path / "second.txt"
        first.write_text("one", encoding="utf-8")
        second.write_text("two", encoding="utf-8")
        file_page.local_table.setRowCount(2)
        for row, path in enumerate([first, second]):
            file_page.local_table.setItem(row, 0, QTableWidgetItem(path.name))
            file_page.local_table.setItem(row, 3, QTableWidgetItem("file"))
            file_page.local_table.setItem(row, 4, QTableWidgetItem(str(path)))
        file_page.local_table.setCurrentCell(0, 0)
        selection = file_page.local_table.selectionModel()
        for row in (0, 1):
            selection.select(
                file_page.local_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )

        with patch.object(file_page, "_rename_local") as rename_local:
            QTest.keyClick(file_page.local_table, Qt.Key_F2)
            qtbot.wait(50)

        rename_local.assert_not_called()

    def test_f2_renames_selected_remote_item(self, file_page, qtbot):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("before.out"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/before.out"))
        file_page.remote_table.selectRow(0)
        file_page.remote_table.setCurrentCell(0, 0)

        with patch.object(file_page, "_rename_remote") as rename_remote:
            QTest.keyClick(file_page.remote_table, Qt.Key_F2)
            qtbot.wait(50)

        rename_remote.assert_called_once_with()

    def test_f2_does_not_rename_multiple_selected_remote_items(self, file_page, qtbot):
        from PySide6.QtCore import QItemSelectionModel, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QTableWidgetItem

        file_page.remote_table.setRowCount(2)
        for row, name in enumerate(["first.out", "second.out"]):
            file_page.remote_table.setItem(row, 0, QTableWidgetItem(name))
            file_page.remote_table.setItem(row, 4, QTableWidgetItem("file"))
            file_page.remote_table.setItem(row, 5, QTableWidgetItem(f"/remote/{name}"))
        file_page.remote_table.setCurrentCell(0, 0)
        selection = file_page.remote_table.selectionModel()
        for row in (0, 1):
            selection.select(
                file_page.remote_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )

        with patch.object(file_page, "_rename_remote") as rename_remote:
            QTest.keyClick(file_page.remote_table, Qt.Key_F2)
            qtbot.wait(50)

        rename_remote.assert_not_called()

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

        with (
            patch.object(file_page, "_rename_local") as rename_local,
            patch.object(file_page._remote_edit_manager, "open_in_text_editor") as open_editor,
        ):
            rect = file_page.local_table.visualItemRect(file_page.local_table.item(0, 0))
            point = self._name_label_point(file_page.local_table, 0) if rect.isValid() else QPoint(4, 4)
            QTest.mouseClick(file_page.local_table.viewport(), Qt.LeftButton, Qt.NoModifier, point)
            file_page._open_local_item(file_page.local_table.item(0, 0))
            qtbot.wait(900)

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

    def test_deleted_remote_list_ui_ignores_late_worker_error(self, file_page):
        """A queued worker signal must not touch C++ widgets after Qt deletes them."""
        errors = []
        file_page._error_cb = lambda title, message: errors.append((title, message))
        request_id = file_page._remote_list_request_id

        with (
            patch(
                "jobdesk_app.gui.pages.file_transfer_page.is_qobject_valid",
                side_effect=[True, False],
            ),
            patch.object(file_page, "_set_connection_status") as set_status,
        ):
            file_page._on_remote_list_error(request_id, "late remote error")

        set_status.assert_not_called()
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
