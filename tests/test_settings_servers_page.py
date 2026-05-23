from unittest.mock import MagicMock, patch

import pytest
import yaml

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton

from jobdesk_app.config.servers import load_servers as load_servers_from_path
from jobdesk_app.gui.pages.settings_servers_page import SettingsServersPage
from jobdesk_app.services.gui_settings import GuiSettings


def test_edit_server_browse_key_path_preserves_hidden_config(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  wsl:\n"
        "    host: 127.0.0.1\n"
        "    port: 22\n"
        "    username: root\n"
        "    auth_method: key\n"
        "    key_path: C:/old/id_rsa\n"
        "    env_init_scripts:\n"
        "      - /opt/g16/bsd/g16.profile\n",
        encoding="utf-8",
    )
    selected_key = str(tmp_path / "id_ed25519")
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()

    def accept_with_browsed_key(dialog):
        browse_button = next(
            button for button in dialog.findChildren(QPushButton)
            if button.text().strip() == "..."
        )
        browse_button.click()
        key_edits = [
            edit for edit in dialog.findChildren(QLineEdit)
            if edit.text() == selected_key
        ]
        assert key_edits
        return QDialog.Accepted

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
        return_value=settings_store,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
        return_value=servers_path,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.load_servers",
        side_effect=lambda: load_servers_from_path(servers_path),
    ), patch(
        "PySide6.QtWidgets.QFileDialog.getOpenFileName",
        return_value=(selected_key, ""),
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_browsed_key):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    saved = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["wsl"]
    assert saved["key_path"] == selected_key
    assert saved["env_init_scripts"] == ["/opt/g16/bsd/g16.profile"]


def test_shutdown_waits_for_worker_without_timeout(qtbot):
    with patch("jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore") as settings_store:
        settings_store.return_value.load.return_value = GuiSettings()
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)

    worker = MagicMock()
    page._worker = worker

    page.shutdown()

    worker.stop_safely.assert_called_once_with()
