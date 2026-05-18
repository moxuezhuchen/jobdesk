"""JobDesk 主窗口 — 左侧导航 + 右侧页面 + 底部日志 + 统一错误/状态管理。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QStackedWidget,
    QTextEdit, QSplitter, QMessageBox,
)
from PySide6.QtCore import Qt

from .state import AppState
from .pages.file_transfer_page import FileTransferPage
from .pages.projects_page import ProjectsPage
from .pages.runs_page import RunsPage
from .pages.results_page import ResultsPage
from .pages.servers_page import ServersPage
from .pages.settings_page import SettingsPage
from .i18n import tr
from .theme import ThemeMetrics, build_app_stylesheet
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
        main_layout = QVBoxLayout(central)

        splitter = QSplitter(Qt.Horizontal)

        self.nav = QListWidget()
        self.nav.setMinimumWidth(ThemeMetrics.NAV_MIN_WIDTH)
        self.nav.setMaximumWidth(ThemeMetrics.NAV_MAX_WIDTH)
        for label in main_navigation_labels(self.language):
            self.nav.addItem(label)
        self.nav.currentRowChanged.connect(self._on_nav)
        splitter.addWidget(self.nav)

        self.pages = QStackedWidget()
        self.projects_page = ProjectsPage(self.state, self._log, self._update_status,
                                          self._on_project_opened)
        self.files_page = FileTransferPage(self.state, self._log, self._update_status,
                                           self.show_error)
        self.runs_page = RunsPage(self.state, self._log, self._update_status)
        self.results_page = ResultsPage(self.state, self._log)
        self.servers_page = ServersPage(self.state, self._log, self._update_status)
        self.settings_page = SettingsPage(self.state, self._log, self._update_status)
        self.settings_page.language_changed.connect(self._on_language_changed)
        self.pages.addWidget(self.projects_page)
        self.pages.addWidget(self.files_page)
        self.pages.addWidget(self.runs_page)
        self.pages.addWidget(self.results_page)
        self.pages.addWidget(self.servers_page)
        self.pages.addWidget(self.settings_page)
        splitter.addWidget(self.pages)

        main_layout.addWidget(splitter, 1)

        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMinimumHeight(0)
        self.log_area.setMaximumHeight(72)
        self.log_area.setVisible(main_window_shows_log_panel())
        main_layout.addWidget(self.log_area)

        self._apply_language()
        self._update_status(tr("Ready", self.language))
        self.nav.setCurrentRow(0)

    def _on_nav(self, index: int):
        self._apply_language()
        page = self.pages.widget(index)
        if hasattr(page, "on_activated"):
            page.on_activated()
        self.pages.setCurrentIndex(index)

    def _apply_language(self):
        self.language = GuiSettingsStore().load().language
        self.nav.blockSignals(True)
        for row, label in enumerate(main_navigation_labels(self.language)):
            item = self.nav.item(row)
            if item is not None:
                item.setText(label)
        self.nav.blockSignals(False)
        for page in (
            self.projects_page,
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
        self.log_area.append(msg)
        self._file_logger.info(msg)

    def _update_status(self, msg: str):
        self._file_logger.info("STATUS: %s", msg)

    def show_error(self, title: str, message: str):
        self._log(f"[ERROR] {title}: {message}")
        self._file_logger.error("%s: %s", title, message)
        QMessageBox.critical(self, title, message)

    def set_busy(self, text: str):
        self._update_status(text)
        self.setEnabled(False)

    def clear_busy(self):
        self._update_status(tr("Ready", self.language))
        self.setEnabled(True)

    def shutdown(self):
        for page in (
            self.files_page,
            self.runs_page,
            self.servers_page,
        ):
            if hasattr(page, "shutdown"):
                page.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def _on_project_opened(self):
        self.runs_page.refresh_run_list()
        self.results_page.refresh_batch_list()


def main_window_has_status_bar() -> bool:
    return False


def main_window_shows_log_panel() -> bool:
    return False


def main_navigation_labels(language: str) -> tuple[str, ...]:
    labels = ("Projects", "Files", "Runs", "Results", "Servers", "Settings")
    return tuple(tr(label, language) for label in labels)
