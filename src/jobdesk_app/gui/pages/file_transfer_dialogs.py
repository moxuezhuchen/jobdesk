"""Dialog helpers for the Files page.

Extracted from ``file_transfer_page`` to reduce module size.
"""
from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QLineEdit

RENAME_DIALOG_MIN_WIDTH = 460
RENAME_DIALOG_INPUT_MIN_WIDTH = 380


def build_name_input_dialog(parent, title: str, label: str, text: str) -> QInputDialog:
    dialog = QInputDialog(parent)
    dialog.setInputMode(QInputDialog.TextInput)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    dialog.setMinimumWidth(RENAME_DIALOG_MIN_WIDTH)
    input_field = dialog.findChild(QLineEdit)
    if input_field is not None:
        input_field.setMinimumWidth(RENAME_DIALOG_INPUT_MIN_WIDTH)
        input_field.selectAll()
    dialog.resize(RENAME_DIALOG_MIN_WIDTH, dialog.sizeHint().height())
    return dialog


def prompt_rename_name(parent, title: str, label: str, text: str) -> tuple[str, bool]:
    dialog = build_name_input_dialog(parent, title, label, text)
    ok = dialog.exec() == QInputDialog.Accepted
    return dialog.textValue(), ok


def prompt_new_folder_name(parent, title: str, label: str) -> tuple[str, bool]:
    dialog = build_name_input_dialog(parent, title, label, "")
    ok = dialog.exec() == QInputDialog.Accepted
    return dialog.textValue(), ok
