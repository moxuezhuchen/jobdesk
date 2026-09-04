"""JobDesk GUI entry point.

Launch: jobdesk-gui
Debug (with console): python -m jobdesk_app.gui.app
"""

import sys

from .dpi import configure_qt_windows_dpi_environment


def _show_startup_feedback(app):
    """Show a small, disposable first-paint window before loading the GUI.

    Importing the page graph and constructing ``MainWindow`` are deliberately
    kept on the GUI thread.  They are fast enough once the application is
    warm, but on a cold Python start they can take long enough that Windows
    shows no feedback at all.  Painting this tiny window first gives the user
    an honest startup state without changing page construction or connection
    semantics.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

    startup = QWidget()
    startup.setObjectName("JobDeskStartupWindow")
    startup.setWindowTitle("JobDesk")
    startup.setWindowFlags(Qt.WindowType.SplashScreen)
    startup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    startup.setFixedSize(460, 150)

    layout = QVBoxLayout(startup)
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(14)

    label = QLabel("Starting JobDesk…", startup)
    label.setObjectName("JobDeskStartupLabel")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(label)

    progress = QProgressBar(startup)
    progress.setObjectName("JobDeskStartupProgress")
    progress.setRange(0, 0)
    progress.setTextVisible(False)
    layout.addWidget(progress)

    # Keep this screen intentionally independent from the full application
    # stylesheet: importing that stylesheet would defeat the early-feedback
    # purpose of this helper.
    startup.setStyleSheet(
        "QWidget#JobDeskStartupWindow { background: #111827; color: #f3f4f6; }"
        "QLabel#JobDeskStartupLabel { color: #f3f4f6; font-size: 18px; }"
        "QProgressBar#JobDeskStartupProgress { min-height: 8px; max-height: 8px; "
        "border: 0; border-radius: 4px; background: #253047; }"
        "QProgressBar#JobDeskStartupProgress::chunk { border-radius: 4px; background: #38bdf8; }"
    )

    screen = startup.screen() or app.primaryScreen()
    if screen is not None:
        startup.move(screen.availableGeometry().center() - startup.rect().center())
    startup.show()
    # ``show`` only queues the native paint.  Process it once before the
    # heavyweight imports and constructor work begin so the feedback is
    # actually visible to the user.
    app.processEvents()
    return startup


def _close_startup_feedback(startup) -> None:
    """Close and release the temporary first-paint window safely."""
    if startup is None:
        return
    try:
        startup.close()
        startup.deleteLater()
    except RuntimeError:
        # The application may already be tearing down its Qt objects.
        pass


def main():
    configure_qt_windows_dpi_environment()

    import logging

    logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QStyleFactory

    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setOrganizationName("JobDesk")
    app.setApplicationName("JobDesk")

    # Keep the native Qt fallback close to the QSS body size so widgets that
    # do not expose a style selector remain readable at the same scale.
    font = QFont("Microsoft YaHei UI", 24)
    font.setWeight(QFont.Weight.Normal)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    startup = _show_startup_feedback(app)
    try:
        # Keep the status window painted while the cold-start import graph is
        # loaded.  These imports intentionally remain local: importing the
        # GUI entry point itself must stay lightweight, and the user should
        # see startup feedback before the page graph is imported.
        from ..services.session_pool import SessionPool
        from .main_window import MainWindow
        from .session import create_sftp_client, create_ssh_client

        app_session_pool = SessionPool(create_ssh_client, create_sftp_client)
        window = MainWindow(session_pool=app_session_pool)
        sys.excepthook = window._make_exception_hook()
        app.aboutToQuit.connect(window.shutdown)
        window.show()
    finally:
        _close_startup_feedback(startup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
