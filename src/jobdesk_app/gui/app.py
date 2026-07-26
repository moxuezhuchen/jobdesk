"""JobDesk GUI entry point.

Launch: jobdesk-gui
Debug (with console): python -m jobdesk_app.gui.app
"""

import sys

from .dpi import configure_qt_windows_dpi_environment


def main():
    configure_qt_windows_dpi_environment()

    import logging

    logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QStyleFactory

    from ..services.session_pool import SessionPool
    from .main_window import MainWindow
    from .session import create_sftp_client, create_ssh_client

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

    app_session_pool = SessionPool(create_ssh_client, create_sftp_client)
    window = MainWindow(session_pool=app_session_pool)
    sys.excepthook = window._make_exception_hook()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
