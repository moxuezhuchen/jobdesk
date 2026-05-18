"""JobDesk main window: navigation + pages + shared error handling."""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QStackedWidget, QTabBar,
    QMessageBox, QSizePolicy,
)

from .state import AppState
from .pages.file_transfer_page import FileTransferPage
from .pages.runs_page import RunsPage
from .pages.results_page import ResultsPage
from .pages.servers_page import ServersPage
from .pages.settings_page import SettingsPage
from .i18n import tr
from .theme import build_app_stylesheet
from ..app_logging import configure_file_logging
from ..services.gui_settings import GuiSettingsStore


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JobDesk")
        self.resize(1320, 860)
        self.state = AppState()
        self.language = GuiSettingsStore().load().language
        self._file_logger = configure_file_logging()
        self.setStyleSheet(build_app_stylesheet())

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.nav = QTabBar()
        self.nav.setExpanding(False)
        for label in main_navigation_labels(self.language):
            self.nav.addTab(label)
        self.nav.currentChanged.connect(self._on_nav)
        layout.addWidget(self.nav)

        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.files_page = FileTransferPage(self.state, self._log, self._update_status,
                                           self.show_error)
        self.runs_page = RunsPage(self.state, self._log, self._update_status)
        self.results_page = ResultsPage(self.state, self._log)
        self.servers_page = ServersPage(self.state, self._log, self._update_status)
        self.settings_page = SettingsPage(self.state, self._log, self._update_status)
        self.settings_page.language_changed.connect(self._on_language_changed)
        self.pages.addWidget(self.files_page)
        self.pages.addWidget(self.runs_page)
        self.pages.addWidget(self.results_page)
        self.pages.addWidget(self.servers_page)
        self.pages.addWidget(self.settings_page)
        layout.addWidget(self.pages, 1)

        self._apply_language()
        self._update_status(tr("Ready", self.language))
        self.nav.setCurrentIndex(0)

    def _on_nav(self, index: int):
        self._apply_language()
        self.pages.setCurrentIndex(index)
        page = self.pages.widget(index)
        if hasattr(page, "on_activated"):
            page.on_activated()

    def _apply_language(self):
        self.language = GuiSettingsStore().load().language
        self.nav.blockSignals(True)
        for row, label in enumerate(main_navigation_labels(self.language)):
            self.nav.setTabText(row, label)
        self.nav.blockSignals(False)
        for page in (
            self.files_page,
            self.runs_page,
            self.results_page,
            self.servers_page,
            self.settings_page,
        ):
            if hasattr(page, "apply_language"):
                page.apply_language(self.language)

    def _on_language_changed(self, language: str):
        self.language = language
        self._apply_language()

    def _log(self, msg: str):
        self._file_logger.info(msg)

    def _update_status(self, msg: str):
        self._file_logger.info("STATUS: %s", msg)

    def show_error(self, title: str, message: str):
        self._log(f"[ERROR] {title}: {message}")
        self._file_logger.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def shutdown(self):
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
    labels = ("Files", "Runs", "Results", "Servers", "Settings")
    return tuple(tr(label, language) for label in labels)
