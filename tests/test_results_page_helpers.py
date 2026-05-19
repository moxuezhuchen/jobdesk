"""Tests for results_page helper functions."""
import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.pages.results_page import _fill_table
from PySide6.QtWidgets import QApplication, QTableWidget

_app = QApplication.instance() or QApplication([])


class TestFillTable:
    def test_sets_column_count(self):
        table = QTableWidget()
        _fill_table(table, ["a", "b", "c"], [{"a": "1", "b": "2", "c": "3"}])
        assert table.columnCount() == 3

    def test_sets_row_count(self):
        table = QTableWidget()
        rows = [{"x": str(i)} for i in range(5)]
        _fill_table(table, ["x"], rows)
        assert table.rowCount() == 5

    def test_cell_values(self):
        table = QTableWidget()
        _fill_table(table, ["name", "energy"], [{"name": "mol1", "energy": "-78.5"}])
        assert table.item(0, 0).text() == "mol1"
        assert table.item(0, 1).text() == "-78.5"

    def test_missing_key_shows_empty(self):
        table = QTableWidget()
        _fill_table(table, ["a", "b"], [{"a": "x"}])
        assert table.item(0, 1).text() == ""

    def test_empty_rows(self):
        table = QTableWidget()
        _fill_table(table, ["a", "b"], [])
        assert table.rowCount() == 0
        assert table.columnCount() == 2
