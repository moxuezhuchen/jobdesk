"""JobDesk GUI entry point.

Launch: jobdesk-gui  (or python -m jobdesk_app.gui.app)
"""

import sys

from .dpi import configure_qt_windows_dpi_environment


def main():
    # Must run before QApplication is created
    configure_qt_windows_dpi_environment()

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setOrganizationName("JobDesk")
    app.setApplicationName("JobDesk")

    # Set application font — Medium weight for crisp text on Windows
    font = QFont("Microsoft YaHei UI", 10)
    font.setWeight(QFont.Weight.Medium)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    window = MainWindow()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
