"""Tests for MainWindow startup-recovery excepthook and gate behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")


class TestMainWindowExcepthook:
    def test_startup_recovery_gates_files_until_completion(self, qtbot, monkeypatch):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QWidget

        class FilesStub(QWidget):
            runs_submitted = Signal(list)

            def __init__(self, *_args):
                super().__init__()

        class RunsStub(QWidget):
            startup_recovery_failed = Signal(str)
            startup_recovery_finished = Signal()

            def __init__(self, *_args):
                super().__init__()

            def start_startup_recovery(self):
                pass

        class SettingsStub(QWidget):
            language_changed = Signal(str)

            def __init__(self, *_args):
                super().__init__()

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

        with (
            patch("jobdesk_app.gui.main_window.configure_file_logging"),
            patch("jobdesk_app.gui.main_window.GuiSettingsStore") as store,
            patch("jobdesk_app.gui.main_window.FileTransferPage", FilesStub),
            patch("jobdesk_app.gui.main_window.RunsResultsPage", RunsStub),
            patch("jobdesk_app.gui.main_window.SettingsServersPage", SettingsStub),
            patch.object(RunsStub, "start_startup_recovery") as start_recovery,
        ):
            from jobdesk_app.services.gui_settings import GuiSettings

            store.return_value.load.return_value = GuiSettings()
            from jobdesk_app.gui.main_window import MainWindow

            window = MainWindow()
            qtbot.addWidget(window)
            assert window.shell.pages.currentIndex() == 0
            assert not window.files_page.isEnabled()
            qtbot.waitUntil(lambda: start_recovery.called, timeout=1000)

            window.runs_page.startup_recovery_finished.emit()

        assert window.files_page.isEnabled()
        window.shutdown()

    def test_startup_recovery_error_releases_gate_and_is_visible(self, qtbot, monkeypatch):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QWidget

        class FilesStub(QWidget):
            runs_submitted = Signal(list)

            def __init__(self, *_args):
                super().__init__()

        class RunsStub(QWidget):
            startup_recovery_failed = Signal(str)
            startup_recovery_finished = Signal()

            def __init__(self, *_args):
                super().__init__()

            def start_startup_recovery(self):
                pass

        class SettingsStub(QWidget):
            language_changed = Signal(str)

            def __init__(self, *_args):
                super().__init__()

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

        with (
            patch("jobdesk_app.gui.main_window.configure_file_logging"),
            patch("jobdesk_app.gui.main_window.GuiSettingsStore") as store,
            patch("jobdesk_app.gui.main_window.FileTransferPage", FilesStub),
            patch("jobdesk_app.gui.main_window.RunsResultsPage", RunsStub),
            patch("jobdesk_app.gui.main_window.SettingsServersPage", SettingsStub),
            patch.object(RunsStub, "start_startup_recovery") as start_recovery,
        ):
            from jobdesk_app.services.gui_settings import GuiSettings

            store.return_value.load.return_value = GuiSettings()
            from jobdesk_app.gui.main_window import MainWindow

            window = MainWindow()
            qtbot.addWidget(window)
            with patch.object(window, "show_error") as show_error:
                qtbot.waitUntil(lambda: start_recovery.called, timeout=1000)
                window.runs_page.startup_recovery_failed.emit("database locked")

        assert window.files_page.isEnabled()
        show_error.assert_called_once()
        assert "database locked" in show_error.call_args.args[1]
        window.shutdown()

    def test_constructing_main_window_does_not_change_sys_excepthook(self, qtbot, monkeypatch):
        """B7: sys.excepthook must not be modified by MainWindow.__init__.

        The test must not leak background activity (workers, timers, SSH connections)
        that could crash subsequent tests via callbacks on destroyed widgets.
        """
        import sys

        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QWidget

        class FilesStub(QWidget):
            runs_submitted = Signal(list)

            def __init__(self, *_args):
                super().__init__()

        class RunsStub(QWidget):
            startup_recovery_failed = Signal(str)
            startup_recovery_finished = Signal()

            def __init__(self, *_args):
                super().__init__()

            def start_startup_recovery(self):
                pass

        class SettingsStub(QWidget):
            language_changed = Signal(str)

            def __init__(self, *_args):
                super().__init__()

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

        with (
            patch("jobdesk_app.gui.main_window.configure_file_logging"),
            patch("jobdesk_app.gui.main_window.FileTransferPage", FilesStub),
            patch("jobdesk_app.gui.main_window.RunsResultsPage", RunsStub),
            patch("jobdesk_app.gui.main_window.SettingsServersPage", SettingsStub),
            patch.object(RunsStub, "start_startup_recovery"),
        ):
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
