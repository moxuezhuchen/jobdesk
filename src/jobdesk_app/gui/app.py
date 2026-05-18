"""JobDesk GUI 入口。

启动命令: python -m jobdesk_app.gui.app
"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from .main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName("JobDesk")
    app.setApplicationName("JobDesk")
    window = MainWindow()
    window.resize(1024, 700)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
