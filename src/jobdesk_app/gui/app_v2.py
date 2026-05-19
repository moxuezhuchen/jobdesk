"""JobDesk GUI v2 entry point.

Launch: jobdesk-gui-v2
"""

import sys

from .dpi import configure_qt_windows_dpi_environment


def main():
    configure_qt_windows_dpi_environment()

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QStyleFactory

    from .main_window_v2 import MainWindowV2

    app = QApplication(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setOrganizationName("JobDesk")
    app.setApplicationName("JobDesk")

    font = QFont("Microsoft YaHei UI", 20)
    font.setWeight(QFont.Weight.Medium)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)

    window = MainWindowV2()
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
