"""JobDesk main window: sidebar navigation + page stack."""

import sys

from PySide6.QtWidgets import QMainWindow, QMessageBox

from .state import AppState
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_page import RunsPage
from .pages.results_page import ResultsPage
from .pages.servers_page import ServersPage
from .pages.settings_page import SettingsPage
from .i18n import tr
from .layouts.shell import AppShell
from .theme import build_app_stylesheet
from ..app_logging import configure_file_logging
from ..services.gui_settings import GuiSettingsStore


# (icon_name, i18n_key)
_NAV_ITEMS = [
    ("folder", "Files"),
    ("rocket", "Runs"),
    ("bar-chart", "Results"),
    ("server", "Servers"),
    ("settings", "Settings"),
]


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
        sys.excepthook = self._make_exception_hook()
        self.setStyleSheet(build_app_stylesheet())

        # Build AppShell with translated nav labels
        nav_items = [(icon, tr(label, self.language)) for icon, label in _NAV_ITEMS]
        self.shell = AppShell(nav_items)
        self.setCentralWidget(self.shell)

        # Pages
        self.files_page = FileTransferPage(self.state, self._log, self._update_status,
                                           self.show_error)
        self.runs_page = RunsPage(self.state, self._log, self._update_status)
        self.results_page = ResultsPage(self.state, self._log)
        self.servers_page = ServersPage(self.state, self._log, self._update_status)
        self.settings_page = SettingsPage(self.state, self._log, self._update_status)
        self.settings_page.language_changed.connect(self._on_language_changed)

        self.shell.add_page(self.files_page)
        self.shell.add_page(self.runs_page)
        self.shell.add_page(self.results_page)
        self.shell.add_page(self.servers_page)
        self.shell.add_page(self.settings_page)

        self.shell.page_changed.connect(self._on_nav)
        self._apply_language()
        self._update_status(tr("Ready", self.language))
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
        for page in (self.files_page, self.runs_page, self.results_page,
                     self.servers_page, self.settings_page):
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
        self._log(f"[ERROR] {title}: {message}")
        self._file_logger.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def shutdown(self):
        # Save window size
        from dataclasses import replace
        current = self._settings_store.load()
        self._settings_store.save(replace(current, window_size=[self.width(), self.height()]))
        for page in (self.files_page, self.runs_page, self.servers_page):
            if hasattr(page, "shutdown"):
                page.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)


def main_window_has_status_bar() -> bool:
    return False


def main_window_shows_log_panel() -> bool:
    return False


def main_navigation_labels(language: str) -> tuple[str, ...]:
    return tuple(tr(key, language) for _icon, key in _NAV_ITEMS)
