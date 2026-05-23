from __future__ import annotations

import os

QT_WINDOWS_DPI_ENVIRONMENT = {
    "QT_ENABLE_HIGHDPI_SCALING": "1",
    "QT_SCALE_FACTOR_ROUNDING_POLICY": "PassThrough",
    "QT_QPA_PLATFORM": "windows",
    "QT_FONT_DPI": "96",
}


def configure_qt_windows_dpi_environment() -> None:
    """Set Qt DPI variables before QApplication exists to avoid Windows bitmap scaling."""
    for key, value in QT_WINDOWS_DPI_ENVIRONMENT.items():
        os.environ[key] = value
