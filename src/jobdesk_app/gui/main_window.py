"""JobDesk GUI — 3-page layout: Files / Runs+Results / Settings+Servers."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox

from ..app_logging import configure_file_logging
from ..services.gui_settings import GuiSettingsStore
from .i18n import tr
from .layouts.shell import AppShell
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_results_page import RunsResultsPage
from .pages.settings_servers_page import SettingsServersPage
from .state import AppState
from .theme import build_app_stylesheet

_NAV_ITEMS = [
    ("folder", "Files"),
    ("rocket", "Runs"),
    ("settings", "Settings"),
]


def _show_submitted_runs(window: "MainWindow", run_ids: list[str]) -> None:
    if run_ids:
        window.state.current_batch_id = run_ids[-1]
    window.shell.sidebar.blockSignals(True)
    window.shell.sidebar.set_current(1)
    window.shell.sidebar.blockSignals(False)
    window.shell.pages.setCurrentIndex(1)
    window.shell.page_changed.emit(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JobDesk")
        self._settings_store = GuiSettingsStore()
        settings = self._settings_store.load()
        size = settings.window_size or [1320, 860]
        self.resize(size[0], size[1])
        self.state = AppState()
        self.language = settings.language
        self._file_logger = configure_file_logging()
        self.setStyleSheet(build_app_stylesheet())

        nav_items = [(icon, tr(label, self.language)) for icon, label in _NAV_ITEMS]
        self.shell = AppShell(nav_items)
        self.setCentralWidget(self.shell)

        # 3 pages
        self.files_page = FileTransferPage(self.state, self._log, self._update_status,
                                           self.show_error)
        self.runs_page = RunsResultsPage(self.state, self._log, self._update_status)
        self.settings_page = SettingsServersPage(self.state, self._log, self._update_status)
        self.settings_page.language_changed.connect(self._on_language_changed)
        self.files_page.runs_submitted.connect(
            lambda run_ids: QTimer.singleShot(0, lambda: _show_submitted_runs(self, run_ids))
        )

        self.shell.add_page(self.files_page)
        self.shell.add_page(self.runs_page)
        self.shell.add_page(self.settings_page)

        self.shell.page_changed.connect(self._on_nav)
        self._apply_language()
        self.shell.set_current(0)

    def _on_nav(self, index: int):
        self._apply_language()
        page = self.shell.pages.widget(index)
        if hasattr(page, "on_activated"):
            page.on_activated()

    def _apply_language(self):
        self.language = self._settings_store.load().language
        for i, (_icon, key) in enumerate(_NAV_ITEMS):
            self.shell.set_nav_label(i, tr(key, self.language))
        for page in (self.files_page, self.runs_page, self.settings_page):
            if hasattr(page, "apply_language"):
                page.apply_language(self.language)

    def _on_language_changed(self, language: str):
        self.language = language
        self._apply_language()

    def _log(self, msg: str):
        self._file_logger.info(msg)

    def _make_exception_hook(self):
        logger = self._file_logger
        def _hook(exc_type, exc, tb):
            logger.exception("Uncaught GUI exception: %s", exc)
        return _hook

    def _update_status(self, msg: str):
        self._file_logger.info("STATUS: %s", msg)

    def show_error(self, title: str, message: str):
        self._file_logger.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def shutdown(self):
        try:
            self._settings_store.update(window_size=[self.width(), self.height()])
        except Exception:
            pass
        for page in (self.files_page, self.runs_page, self.settings_page):
            if hasattr(page, "shutdown"):
                try:
                    page.shutdown()
                except Exception:
                    pass
        from .workers import BackgroundWorker
        BackgroundWorker.wait_all()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
