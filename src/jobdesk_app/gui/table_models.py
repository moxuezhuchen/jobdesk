"""通用 TSV 表格加载工具 — 读取 TSV 文件并填充 QTableWidget。"""

import csv
from pathlib import Path
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView


def load_tsv_to_table(table: QTableWidget, file_path: Path) -> None:
    """读取 TSV 文件并填充到 QTableWidget。

    Args:
        table: 目标 QTableWidget。
        file_path: TSV 文件路径。
    """
    table.clear()
    table.setRowCount(0)
    table.setColumnCount(0)

    if not file_path.exists():
        return

    rows = []
    with open(file_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if row and any(cell for cell in row):
                rows.append(row)

    if not rows:
        return

    header = rows[0]
    data = rows[1:]

    load_rows_to_table(table, header, data)


def load_rows_to_table(table: QTableWidget, header: list[str], rows: list[list[str]]) -> None:
    table.clear()
    table.setRowCount(0)
    table.setColumnCount(0)
    if not header:
        return
    table.setColumnCount(len(header))
    table.setHorizontalHeaderLabels(header)
    table.setRowCount(len(rows))

    for r, row in enumerate(rows):
        for c, cell in enumerate(row[:len(header)]):
            item = QTableWidgetItem(cell)
            table.setItem(r, c, item)

    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)


def display_dict_as_table(table: QTableWidget, data: dict) -> None:
    """将 dict 显示为两列表格（Key / Value）。"""
    table.clear()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(["Key", "Value"])
    table.setRowCount(len(data))
    for r, (k, v) in enumerate(data.items()):
        table.setItem(r, 0, QTableWidgetItem(str(k)))
        table.setItem(r, 1, QTableWidgetItem(str(v)))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
