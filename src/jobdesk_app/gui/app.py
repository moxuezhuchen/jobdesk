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

    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setOrganizationName("JobDesk")
    app.setApplicationName("JobDesk")

    font = QFont("Microsoft YaHei UI", 20)
    font.setWeight(QFont.Weight.Medium)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    window = MainWindow()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
