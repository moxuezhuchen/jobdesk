import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import Qt

from jobdesk_app.gui.pages.file_transfer_widgets import _FileTable, _filter_rows, _load_rows, _parse_timestamp


def test_parse_timestamp_handles_unix_epoch_on_windows():
    assert _parse_timestamp("1970-01-01 08:00:00") == 8 * 60 * 60


def test_parse_timestamp_preserves_timezone_aware_instants():
    assert _parse_timestamp("1970-01-01T00:00:00+00:00") == 0


def test_file_rows_filter_case_insensitively_keep_parent_and_preserve_view(qtbot):
    table = _FileTable("local")
    qtbot.addWidget(table)
    table.setColumnCount(5)
    table.setColumnWidth(0, 287)
    _load_rows(
        table,
        [
            ["..", "", "", "dir", "C:/"],
            ["Alpha.XYZ", "1 KB", "2026-01-01", "file", "C:/work/Alpha.XYZ"],
            ["beta.inp", "2 KB", "2026-01-02", "file", "C:/work/beta.inp"],
        ],
    )
    table.sortItems(0, Qt.DescendingOrder)

    visible, total = _filter_rows(table, "ALPHA")

    assert (visible, total) == (1, 2)
    assert not table.isRowHidden(next(row for row in range(3) if table.item(row, 0).text() == ".."))
    assert table.isRowHidden(next(row for row in range(3) if table.item(row, 0).text() == "beta.inp"))
    assert table.horizontalHeader().sortIndicatorOrder() == Qt.DescendingOrder
    assert table.columnWidth(0) == 287
    alpha_row = next(row for row in range(3) if table.item(row, 0).text() == "Alpha.XYZ")
    assert table.item(alpha_row, 0).toolTip() == "C:/work/Alpha.XYZ"
    assert table.item(alpha_row, 1).toolTip() == "1 KB"
