from unittest.mock import MagicMock, patch

import pytest
import yaml

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton

from jobdesk_app.config.servers import load_servers as load_servers_from_path
from jobdesk_app.gui.pages.settings_servers_helpers import validate_server_id_change
from jobdesk_app.gui.pages.settings_servers_page import SettingsServersPage, ToggleSwitch
from jobdesk_app.services.gui_settings import GuiSettings


def test_validate_server_id_change_allows_unchanged_id():
    assert validate_server_id_change({"wsl", "hpc"}, old_id="wsl", new_id="wsl") is None


def test_validate_server_id_change_allows_new_unique_id():
    assert validate_server_id_change({"wsl"}, old_id=None, new_id="hpc") is None


def test_validate_server_id_change_rejects_blank_id():
    assert validate_server_id_change({"wsl"}, old_id=None, new_id="   ") == "Server ID is required"


def test_validate_server_id_change_rejects_duplicate_add():
    assert validate_server_id_change({"wsl"}, old_id=None, new_id="wsl") == "Server ID already exists: wsl"


def test_validate_server_id_change_rejects_duplicate_rename():
    assert validate_server_id_change({"wsl", "hpc"}, old_id="wsl", new_id="hpc") == "Server ID already exists: hpc"


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


def test_edit_server_saves_external_terminal_fields(qtbot, tmp_path):
    from PySide6.QtWidgets import QComboBox

    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  hpc:\n"
        "    host: cluster\n"
        "    username: chemist\n"
        "    auth_method: key\n"
        "    external_tools:\n"
        "      terminal_provider: windows_terminal\n"
        "      ssh_alias: old-alias\n"
        "      winscp_session: hidden-site\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()

    def accept_with_external_tools(dialog):
        provider_combo = next(
            c for c in dialog.findChildren(QComboBox)
            if "windows_terminal" in [c.itemText(i) for i in range(c.count())]
            and "putty" in [c.itemText(i) for i in range(c.count())]
        )
        provider_combo.setCurrentText("putty")
        edits = dialog.findChildren(QLineEdit)
        ssh_alias = next(edit for edit in edits if edit.text() == "old-alias")
        ssh_alias.setText("cluster-a")
        putty_session = next(edit for edit in edits if edit.placeholderText() == "PuTTY saved session")
        putty_session.setText("cluster-a-putty")
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
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_external_tools):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    external = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["hpc"]["external_tools"]
    assert external["terminal_provider"] == "putty"
    assert external["ssh_alias"] == "cluster-a"
    assert external["putty_session"] == "cluster-a-putty"
    assert external["winscp_session"] == "hidden-site"


def test_edit_server_saves_ssh_access_fields(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  hpc:\n"
        "    host: cluster\n"
        "    username: chemist\n"
        "    auth_method: key\n"
        "    ssh_access:\n"
        "      config_alias: old-runtime-alias\n"
        "      hidden_ssh_key: preserved\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()

    def accept_with_ssh_access(dialog):
        edits = dialog.findChildren(QLineEdit)
        config_alias = next(edit for edit in edits if edit.text() == "old-runtime-alias")
        config_alias.setText("cluster-runtime")
        proxy_command = next(edit for edit in edits if edit.placeholderText() == "ssh -W %h:%p gateway")
        proxy_command.setText("ssh -W %h:%p login-node")
        proxy_jump = next(edit for edit in edits if edit.placeholderText() == "gateway")
        proxy_jump.setText("login-node")
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
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_ssh_access):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    ssh_access = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["hpc"]["ssh_access"]
    assert ssh_access["config_alias"] == "cluster-runtime"
    assert ssh_access["proxy_command"] == "ssh -W %h:%p login-node"
    assert ssh_access["proxy_jump"] == "login-node"
    assert ssh_access["hidden_ssh_key"] == "preserved"


def test_shutdown_stops_worker_with_timeout(qtbot):
    with patch("jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore") as settings_store:
        settings_store.return_value.load.return_value = GuiSettings()
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)

    worker = MagicMock()
    page._worker = worker

    page.shutdown()

    worker.stop_safely.assert_called_once_with(3000)


def test_text_editor_setting_loads_and_saves(qtbot):
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings(text_editor_path="C:/Tools/notepad++.exe")

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
        return_value=settings_store,
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)

    assert page.text_editor_edit.text() == "C:/Tools/notepad++.exe"

    page.text_editor_edit.setText("C:/Tools/code.exe")
    page._save_settings()

    saved = settings_store.save.call_args.args[0]
    assert saved.text_editor_path == "C:/Tools/code.exe"


def test_edit_server_exposes_key_auth_only_and_saves_explicit_tofu(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  wsl:\n"
        "    host: 127.0.0.1\n"
        "    username: root\n"
        "    auth_method: key\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()

    def accept_with_tofu(dialog):
        combos = dialog.findChildren(__import__("PySide6.QtWidgets", fromlist=["QComboBox"]).QComboBox)
        assert any(combo.itemText(0) == "key" and combo.count() == 1 for combo in combos)
        toggle = dialog.findChildren(ToggleSwitch)[0]
        toggle.setChecked(True)
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
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_tofu):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    saved = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["wsl"]
    assert saved["auth_method"] == "key"
    assert saved["trust_on_first_use"] is True


def test_edit_server_saves_scheduler_fields_and_preserves_hidden_keys(qtbot, tmp_path):
    from PySide6.QtWidgets import QComboBox, QSpinBox
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  hpc:\n"
        "    host: cluster\n"
        "    username: chemist\n"
        "    auth_method: key\n"
        "    scheduler:\n"
        "      type: nohup\n"
        "      default_cpus: 1\n"
        "      extra_directives:\n"
        "        - '#SBATCH --qos=high'\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()

    def accept_with_scheduler(dialog):
        type_combo = next(
            c for c in dialog.findChildren(QComboBox)
            if [c.itemText(i) for i in range(c.count())] == ["nohup", "slurm", "pbs"]
        )
        type_combo.setCurrentIndex(1)  # slurm
        spins = dialog.findChildren(QSpinBox)
        # spins order: port, cpus, mem, walltime
        spins[1].setValue(16)   # cpus
        spins[2].setValue(32000)  # memory MB
        spins[3].setValue(720)  # walltime minutes
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
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_scheduler):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    sched = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["hpc"]["scheduler"]
    assert sched["type"] == "slurm"
    assert sched["default_cpus"] == 16
    assert sched["default_memory_mb"] == 32000
    assert sched["default_walltime_minutes"] == 720
    assert sched["extra_directives"] == ["#SBATCH --qos=high"]  # hidden key preserved


def test_edit_server_rejects_duplicate_server_id(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  wsl:\n"
        "    host: 127.0.0.1\n"
        "    username: root\n"
        "    auth_method: key\n"
        "  hpc:\n"
        "    host: cluster\n"
        "    username: chemist\n"
        "    auth_method: key\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()
    statuses: list[str] = []

    def accept_with_duplicate_id(dialog):
        id_edit = next(edit for edit in dialog.findChildren(QLineEdit) if edit.text() == "wsl")
        id_edit.setText("hpc")
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
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_duplicate_id), patch(
        "PySide6.QtWidgets.QMessageBox.warning",
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, statuses.append)
        qtbot.addWidget(page)
        for row in range(page.server_table.rowCount()):
            if page.server_table.item(row, 0).text() == "wsl":
                page.server_table.selectRow(row)
                break
        page._edit_server()

    saved = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]
    assert set(saved) == {"wsl", "hpc"}
    assert saved["hpc"]["host"] == "cluster"
    assert statuses == ["Server ID already exists: hpc"]


def test_add_server_rejects_duplicate_server_id(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  wsl:\n"
        "    host: 127.0.0.1\n"
        "    username: root\n"
        "    auth_method: key\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()
    statuses: list[str] = []

    def accept_with_duplicate_id(dialog):
        edits = dialog.findChildren(QLineEdit)
        id_edit = next(edit for edit in edits if "myserver" in edit.placeholderText())
        host_edit = next(edit for edit in edits if "192.168" in edit.placeholderText())
        user_edit = next(edit for edit in edits if "root" in edit.placeholderText())
        id_edit.setText("wsl")
        host_edit.setText("cluster")
        user_edit.setText("chemist")
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
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_duplicate_id), patch(
        "PySide6.QtWidgets.QMessageBox.warning",
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, statuses.append)
        qtbot.addWidget(page)
        page._add_server()

    saved = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]
    assert set(saved) == {"wsl"}
    assert saved["wsl"]["host"] == "127.0.0.1"
    assert statuses == ["Server ID already exists: wsl"]
