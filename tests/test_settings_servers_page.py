import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import yaml

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox, QPushButton, QScrollArea

from jobdesk_app.config.servers import load_servers as load_servers_from_path
from jobdesk_app.gui.button_feedback import ButtonRole
from jobdesk_app.gui.pages.server_editor_dialog import ServerEditorDialog
from jobdesk_app.gui.pages.settings_servers_helpers import validate_server_id_change
from jobdesk_app.gui.pages.settings_servers_page import (
    SettingsServersPage,
    ToggleSwitch,
    _test_server_connections,
)
from jobdesk_app.services.gui_settings import GuiSettings


def _make_settings_page(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n  wsl:\n    host: 127.0.0.1\n    username: root\n    auth_method: key\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()
    statuses: list[str] = []

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, statuses.append)
        qtbot.addWidget(page)

    return page, settings_store, statuses


def _make_empty_settings_page(qtbot, tmp_path):
    """Like ``_make_settings_page`` but with NO servers configured.

    Used by Phase 2.1 empty-state tests so the empty hint is visible.
    """
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text("servers: {}\n", encoding="utf-8")
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()
    statuses: list[str] = []

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, statuses.append)
        qtbot.addWidget(page)

    return page, settings_store, statuses


def test_settings_buttons_have_feedback_roles(qtbot, tmp_path):
    page, _, _ = _make_settings_page(qtbot, tmp_path)

    expected_roles = {
        page.browse_btn: ButtonRole.INSTANT_ACTION,
        page.text_editor_browse_btn: ButtonRole.INSTANT_ACTION,
        page.test_btn: ButtonRole.TEST_ACTION,
        page.edit_yaml_btn: ButtonRole.PRIMARY_ACTION,
        page.edit_srv_btn: ButtonRole.PRIMARY_ACTION,
        page.delete_srv_btn: ButtonRole.DANGER_ACTION,
        page._add_profile_btn: ButtonRole.PRIMARY_ACTION,
        page._del_profile_btn: ButtonRole.DANGER_ACTION,
        page.save_btn: ButtonRole.SETTINGS_ACTION,
        page.discard_btn: ButtonRole.SETTINGS_ACTION,
    }

    for button, role in expected_roles.items():
        assert button.property("buttonRole") == role.value


def test_settings_small_helper_text_uses_readable_size(qtbot, tmp_path):
    """Helper-text labels must stay inside the readable-size band.

    Phase 18 visual cleanup: the previous test asserted a single hard
    ``font-size: 14pt`` value across three labels.  The new design
    system centralises the readable-size values as
    ``Metrics.CARD_BODY_FONT_PX`` (20 px) and ``Metrics.HELP_TEXT_FONT_PX``
    (12 px); the test now pins those tokens so a future regression
    to either "8 px illegible" or "16 px paragraph" is caught.
    """
    from jobdesk_app.gui.design.tokens import Metrics

    page, _, _ = _make_settings_page(qtbot, tmp_path)

    setting_card_desc_size = f"{Metrics.CARD_BODY_FONT_PX}px"
    help_text_size = f"{Metrics.HELP_TEXT_FONT_PX}px"
    assert setting_card_desc_size in page._card_local.lbl_desc.styleSheet()
    assert help_text_size in page._dl_desc.styleSheet()
    assert help_text_size in page._confflow_note.styleSheet()


def test_settings_confflow_note_translates_to_chinese(qtbot, tmp_path):
    page, _, _ = _make_settings_page(qtbot, tmp_path)

    page.apply_language("zh")

    assert "ConfFlow" in page._confflow_note.text()
    assert "downloads are managed" not in page._confflow_note.text()
    assert "\u4e0b\u8f7d" in page._confflow_note.text()


def test_settings_test_connection_feedback_pending(qtbot, tmp_path):
    page, _, _ = _make_settings_page(qtbot, tmp_path)
    idle_text = page.test_btn.text()

    page._test_feedback.pending("Testing...")

    assert page.test_btn.text() == "Testing..."
    assert page.test_btn.property("feedbackState") == "pending"
    assert not page.test_btn.isEnabled()

    page._test_feedback.restore()

    assert page.test_btn.text() == idle_text
    assert page.test_btn.property("feedbackState") == "idle"
    assert page.test_btn.isEnabled()


def test_settings_test_connection_ignores_duplicate_requests(qtbot, tmp_path):
    page, _, _ = _make_settings_page(qtbot, tmp_path)
    started: list[dict] = []

    def capture_worker(_owner, **kwargs):
        started.append(kwargs)
        return MagicMock()

    servers_path = tmp_path / "servers.yaml"
    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.start_context_worker",
            side_effect=capture_worker,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
    ):
        page._test_connection()
        page._test_connection()

    assert page._connection_test_running is True
    assert len(started) == 1

    started[0]["on_finished"]()
    assert page._connection_test_running is False
    page._test_feedback.restore()


def test_settings_connection_result_is_timestamped_for_session(qtbot, tmp_path):
    page, _, _ = _make_settings_page(qtbot, tmp_path)
    started: list[dict] = []

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.start_context_worker",
        side_effect=lambda _owner, **kwargs: started.append(kwargs) or MagicMock(),
    ):
        page._test_connection()

    started[0]["on_log"]("wsl\tconnected")
    status = page.server_table.item(0, 4).text()
    assert status.startswith("connected · ")
    assert page._server_test_status["wsl"] == status


def test_server_connection_test_reports_timed_out_server_without_waiting():
    release = threading.Event()
    calls: list[str] = []

    servers = [
        ("fast", object()),
        ("hung", object()),
    ]

    def tester(sid, _server):
        if sid == "hung":
            release.wait(5)
            return "connected"
        return "connected"

    t0 = time.monotonic()
    try:
        _test_server_connections(
            servers,
            language="en",
            emit_log=calls.append,
            tester=tester,
            timeout_seconds=0.2,
            poll_seconds=0.01,
        )
    finally:
        release.set()

    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    assert "fast\tconnected" in calls
    assert any(call.startswith("hung\tError: timed out after") for call in calls)


def test_save_settings_load_error_sets_feedback_error(qtbot, tmp_path):
    from jobdesk_app.gui.i18n import tr

    page, settings_store, _ = _make_settings_page(qtbot, tmp_path)
    settings_store.load.side_effect = RuntimeError("broken settings")

    with pytest.raises(RuntimeError, match="broken settings"):
        page._save_settings()

    assert page.save_btn.text() == tr("Save failed", page._language)
    assert page.save_btn.property("feedbackState") == "error"
    assert not page.save_btn.isEnabled()

    page._save_feedback.restore()

    assert page.save_btn.text() == tr("Save Settings", page._language)
    assert page.save_btn.property("feedbackState") == "idle"
    assert not page.save_btn.isEnabled()


def test_local_settings_start_clean_then_save_current_clears_dirty(qtbot, tmp_path):
    page, settings_store, _ = _make_settings_page(qtbot, tmp_path)

    assert not page.save_btn.isEnabled()
    assert not page.discard_btn.isEnabled()
    assert page.unsaved_label.isHidden()

    page.local_folder_edit.setText(str(tmp_path))

    assert page.save_btn.isEnabled()
    assert page.discard_btn.isEnabled()
    assert not page.unsaved_label.isHidden()

    page.save_current()

    settings_store.save.assert_called_once()
    assert not page.save_btn.isEnabled()
    assert not page.discard_btn.isEnabled()
    assert page.unsaved_label.isHidden()


def test_text_editor_path_allows_path_command_but_rejects_missing_explicit_path(qtbot, tmp_path):
    page, settings_store, _ = _make_settings_page(qtbot, tmp_path)

    page.text_editor_edit.setText("code")
    assert page.text_editor_edit.property("validationState") == "valid"
    assert page.save_btn.isEnabled()

    page.text_editor_edit.setText(str(tmp_path / "missing" / "editor.exe"))
    assert page.text_editor_edit.property("validationState") == "error"
    assert not page.save_btn.isEnabled()
    assert "PATH" in page.text_editor_edit.toolTip()
    assert page.save_current() is False
    settings_store.save.assert_not_called()


def test_software_profile_row_changes_mark_local_settings_dirty(qtbot, tmp_path):
    page, _, _ = _make_settings_page(qtbot, tmp_path)

    page._add_profile_row()

    assert page.save_btn.isEnabled()
    assert page.discard_btn.isEnabled()
    assert not page.unsaved_label.isHidden()


def test_server_editor_is_shared_scrollable_dialog_with_fixed_footer(qtbot):
    dialog = ServerEditorDialog(
        language="en",
        existing_ids={"wsl"},
        old_id="wsl",
        server_id="wsl",
        server={"host": "127.0.0.1", "username": "root", "auth_method": "key"},
    )
    qtbot.addWidget(dialog)

    scroll = dialog.findChild(QScrollArea, "ServerEditorScroll")
    footer = dialog.findChild(QPushButton, "ServerEditorSaveBtn").parentWidget()
    assert scroll is not None
    assert not scroll.isAncestorOf(footer)
    assert dialog.section_is_expanded("basic")
    assert not dialog.section_is_expanded("scheduler")
    assert not dialog.section_is_expanded("terminal")
    assert not dialog.section_is_expanded("ssh")


def test_server_editor_validates_inline_and_connection_test_does_not_accept(qtbot):
    tested: list[dict] = []
    started: list[dict] = []
    dialog = ServerEditorDialog(language="en", existing_ids=set(), connection_tester=tested.append)
    qtbot.addWidget(dialog)

    dialog.test_connection_btn.click()
    assert dialog.validation_label.text()
    assert dialog.host_edit.property("validationState") == "error"
    assert tested == []
    assert dialog.result() == QDialog.Rejected

    dialog.id_edit.setText("hpc")
    dialog.host_edit.setText("cluster")
    dialog.user_edit.setText("chemist")
    with patch(
        "jobdesk_app.gui.pages.server_editor_dialog.start_context_worker",
        side_effect=lambda _owner, **kwargs: started.append(kwargs) or MagicMock(),
    ):
        dialog.test_connection_btn.click()
    assert len(started) == 1
    assert tested == []
    assert dialog.connection_result_label.text() == "Testing..."
    result = started[0]["target"](MagicMock())
    assert len(tested) == 1
    started[0]["on_result"](result)
    started[0]["on_finished"]()
    assert "Connected" in dialog.connection_result_label.text()
    assert dialog.result() == QDialog.Rejected


def test_server_editor_connection_test_uses_background_worker(qtbot):
    dialog = ServerEditorDialog(
        language="en",
        existing_ids={"hpc"},
        old_id="hpc",
        server_id="hpc",
        server={"host": "cluster", "username": "chemist"},
        connection_tester=lambda _config: "connected",
    )
    qtbot.addWidget(dialog)
    started: list[dict] = []

    with patch(
        "jobdesk_app.gui.pages.server_editor_dialog.start_context_worker",
        side_effect=lambda _owner, **kwargs: started.append(kwargs) or MagicMock(),
    ):
        dialog._test_connection()

    assert len(started) == 1
    assert dialog._connection_test_running is True
    assert not dialog.test_connection_btn.isEnabled()


def test_server_editor_connection_test_keeps_gui_event_loop_responsive(qtbot):
    release = threading.Event()

    def tester(_config):
        release.wait(2)
        return "connected"

    dialog = ServerEditorDialog(
        language="en",
        existing_ids={"hpc"},
        old_id="hpc",
        server_id="hpc",
        server={"host": "cluster", "username": "chemist"},
        connection_tester=tester,
    )
    qtbot.addWidget(dialog)

    dialog._test_connection()
    assert dialog.connection_result_label.text() == "Testing..."
    QTimer.singleShot(20, release.set)

    qtbot.waitUntil(lambda: not dialog._connection_test_running, timeout=1000)
    assert "connected" in dialog.connection_result_label.text()


def test_server_editor_trust_toggle_and_form_controls_are_accessible(qtbot):
    dialog = ServerEditorDialog(language="en", existing_ids=set())
    qtbot.addWidget(dialog)

    assert dialog.host_label.buddy() is dialog.host_edit
    assert dialog.key_path_label.buddy() is dialog.key_edit
    assert dialog.host_edit.accessibleName()
    assert dialog.host_edit.accessibleDescription()
    assert dialog.trust_toggle.focusPolicy() == Qt.StrongFocus
    assert dialog.trust_toggle.accessibleName()
    assert dialog.trust_toggle.accessibleDescription()
    assert dialog.key_browse_btn.accessibleName()
    assert dialog.key_browse_btn.accessibleDescription()

    dialog.trust_toggle.setFocus()
    qtbot.keyClick(dialog.trust_toggle, Qt.Key_Space)
    assert dialog.trust_toggle.isChecked()


def test_open_server_editor_retranslates_immediately(qtbot):
    dialog = ServerEditorDialog(language="en", existing_ids=set())
    qtbot.addWidget(dialog)

    dialog.apply_language("zh")

    assert dialog.language == "zh"
    assert dialog.host_label.text() != "Host:"
    assert dialog.test_connection_btn.text() != "Test Connection"


def test_settings_page_retranslates_registered_open_server_editor(qtbot, tmp_path):
    page, _, _ = _make_settings_page(qtbot, tmp_path)
    dialog = ServerEditorDialog(language="en", existing_ids=set(), parent=page)
    qtbot.addWidget(dialog)
    page._register_server_dialog(dialog)

    page.apply_language("zh")

    assert dialog.language == "zh"


def test_server_editor_confirms_only_false_to_true_unknown_host_trust(qtbot):
    dialog = ServerEditorDialog(
        language="en",
        existing_ids={"hpc"},
        old_id="hpc",
        server_id="hpc",
        server={"host": "cluster", "username": "chemist", "trust_on_first_use": False},
    )
    qtbot.addWidget(dialog)
    dialog.trust_toggle.setChecked(True)

    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.No) as question:
        dialog.save_button.click()

    question.assert_called_once()
    assert dialog.result() == QDialog.Rejected

    already_trusted = ServerEditorDialog(
        language="en",
        existing_ids={"hpc"},
        old_id="hpc",
        server_id="hpc",
        server={"host": "cluster", "username": "chemist", "trust_on_first_use": True},
    )
    qtbot.addWidget(already_trusted)
    with patch("PySide6.QtWidgets.QMessageBox.question") as question:
        already_trusted.save_button.click()
    question.assert_not_called()
    assert already_trusted.result() == QDialog.Accepted


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
        browse_button = next(button for button in dialog.findChildren(QPushButton) if button.text().strip() == "...")
        browse_button.click()
        key_edits = [edit for edit in dialog.findChildren(QLineEdit) if edit.text() == selected_key]
        assert key_edits
        return QDialog.Accepted

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
        patch(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            return_value=(selected_key, ""),
        ),
        patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_browsed_key),
    ):
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
        "      terminal_path: old-path\n"
        "      winscp_session: hidden-site\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()
    terminal_executable = tmp_path / "putty.exe"
    terminal_executable.write_text("", encoding="utf-8")

    def accept_with_external_tools(dialog):
        provider_combo = next(
            c
            for c in dialog.findChildren(QComboBox)
            if "windows_terminal" in [c.itemText(i) for i in range(c.count())]
            and "putty" in [c.itemText(i) for i in range(c.count())]
        )
        provider_combo.setCurrentText("putty")
        edits = dialog.findChildren(QLineEdit)
        ssh_alias = next(edit for edit in edits if edit.text() == "old-alias")
        ssh_alias.setText("cluster-a")
        putty_session = next(edit for edit in edits if edit.placeholderText() == "PuTTY saved session")
        putty_session.setText("cluster-a-putty")
        terminal_path = next(edit for edit in edits if edit.placeholderText() == "Path to terminal executable")
        terminal_path.setText(str(terminal_executable))
        return QDialog.Accepted

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
        patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_external_tools),
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    external = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]["hpc"]["external_tools"]
    assert external["terminal_provider"] == "putty"
    assert external["ssh_alias"] == "cluster-a"
    assert external["putty_session"] == "cluster-a-putty"
    assert external["terminal_path"] == str(terminal_executable)
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

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
        patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_ssh_access),
    ):
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


def test_text_editor_setting_loads_and_saves(qtbot, tmp_path):
    settings_store = MagicMock()
    original_editor = tmp_path / "notepad++.exe"
    updated_editor = tmp_path / "code.exe"
    original_editor.write_text("", encoding="utf-8")
    updated_editor.write_text("", encoding="utf-8")
    settings_store.load.return_value = GuiSettings(text_editor_path=str(original_editor))

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
        return_value=settings_store,
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, lambda message: None)
        qtbot.addWidget(page)

    assert page.text_editor_edit.text() == str(original_editor)

    page.text_editor_edit.setText(str(updated_editor))
    page._save_settings()

    saved = settings_store.save.call_args.args[0]
    assert saved.text_editor_path == str(updated_editor)


def test_edit_server_exposes_key_auth_only_and_saves_explicit_tofu(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n  wsl:\n    host: 127.0.0.1\n    username: root\n    auth_method: key\n",
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

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
        patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_tofu),
    ):
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
            c
            for c in dialog.findChildren(QComboBox)
            if [c.itemText(i) for i in range(c.count())] == ["nohup", "slurm", "pbs"]
        )
        type_combo.setCurrentIndex(1)  # slurm
        spins = dialog.findChildren(QSpinBox)
        # spins order: port, cpus, mem, walltime
        spins[1].setValue(16)  # cpus
        spins[2].setValue(32000)  # memory MB
        spins[3].setValue(720)  # walltime minutes
        return QDialog.Accepted

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
        patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_scheduler),
    ):
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

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
        patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_duplicate_id),
        patch(
            "PySide6.QtWidgets.QMessageBox.warning",
        ),
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
        "servers:\n  wsl:\n    host: 127.0.0.1\n    username: root\n    auth_method: key\n",
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

    with (
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
            return_value=settings_store,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
            return_value=servers_path,
        ),
        patch(
            "jobdesk_app.gui.pages.settings_servers_page.load_servers",
            side_effect=lambda: load_servers_from_path(servers_path),
        ),
        patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_duplicate_id),
        patch(
            "PySide6.QtWidgets.QMessageBox.warning",
        ),
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, statuses.append)
        qtbot.addWidget(page)
        page._add_server()

    saved = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]
    assert set(saved) == {"wsl"}
    assert saved["wsl"]["host"] == "127.0.0.1"
    assert statuses == ["Server ID already exists: wsl"]


# -- Phase 2.1: empty-state hint tests --


def test_empty_hint_visible_when_no_servers(qtbot, tmp_path):
    """The empty-state hint shows when servers.yaml has no entries.

    Constructs the page with an empty servers file (no patches over
    load_servers that hide the empty-state branch), then asserts
    the hint was *unhidden* by _load_servers() and carries the
    expected English title. Note: ``isVisible()`` requires the parent
    widget tree to be ``show()``n, so we assert the more deterministic
    ``not isHidden()`` instead.
    """
    page, _, _ = _make_empty_settings_page(qtbot, tmp_path)
    page.show()
    QApplication.processEvents()

    assert page._empty_hint.isHidden() is False
    assert page._empty_hint.isVisible() is True
    assert "Add a server to get started" in page._empty_hint._title_label.text()


def test_empty_hint_hidden_when_servers_present(qtbot, tmp_path):
    """Sanity counterpart to test_empty_hint_visible_when_no_servers.

    With the default factory's single-server YAML the hint must NOT be
    visible -- otherwise the empty-state would overlap the populated
    server table.
    """
    page, _, _ = _make_settings_page(qtbot, tmp_path)
    page.show()
    QApplication.processEvents()

    assert page._empty_hint.isVisible() is False


def test_copy_sample_writes_to_clipboard(qtbot, tmp_path):
    """Clicking copy_sample drops a YAML snippet on the clipboard.

    _on_empty_action uses QApplication.clipboard() to push a
    small server-template snippet; we assert both that the snippet
    contains the canonical 'servers:' prefix and that the status
    callback was invoked with the i18n'd success string.
    """
    page, _, statuses = _make_empty_settings_page(qtbot, tmp_path)

    clipboard = QApplication.clipboard()
    clipboard.clear()

    page._on_empty_action("copy_sample")

    text = clipboard.text()
    assert "servers:" in text
    assert "host: my-linux.example.edu" in text
    assert statuses  # at least one status callback fired
    assert any("clipboard" in status for status in statuses)
