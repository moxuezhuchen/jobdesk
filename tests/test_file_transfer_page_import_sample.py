"""Review-fix tests for the corrupt-config guard on the Files page.

The original ``FileTransferPage._import_sample_servers_yaml`` swallowed
any YAML parse failure and reset ``data`` to ``{}`` before writing a
sample. That turned a recoverable broken ``servers.yaml`` into a
permanent loss at exactly the moment the user needed the original
bytes most (they hit the empty-state because the broken config meant
no servers loaded). The fix is to raise
:class:`ConfigUnreadable` so the caller can show a clean error and
leave the file on disk untouched.

This file tests the parse-and-guard helper directly (no QWidget
required). The end-to-end behavior on the page is covered indirectly
through the existing ``test_settings_servers_page`` and
``test_file_transfer_page_helpers`` suites -- what we focus on here is
the safety contract: never write a sample over a broken file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.pages.file_transfer_page import (
    ConfigUnreadable,
    _load_existing_servers_data,
)


def test_load_existing_returns_empty_dict_when_missing(tmp_path: Path):
    """No file yet -> the helper returns an empty mapping without errors.

    This is the cold-start case (first time JobDesk is launched on a
    fresh user profile). The helper must NOT raise; an empty mapping
    is the correct seed for the merge step.
    """
    path = tmp_path / "servers.yaml"
    assert _load_existing_servers_data(path) == {}


def test_load_existing_returns_empty_dict_when_empty_file(tmp_path: Path):
    """An empty (zero-byte) file parses as None and becomes ``{}``."""
    path = tmp_path / "servers.yaml"
    path.write_text("", encoding="utf-8")

    assert _load_existing_servers_data(path) == {}


def test_load_existing_returns_mapping_for_well_formed_file(tmp_path: Path):
    """Happy path: a valid YAML mapping round-trips intact."""
    path = tmp_path / "servers.yaml"
    path.write_text(
        "servers:\n"
        "  wsl:\n"
        "    host: 127.0.0.1\n"
        "    username: root\n"
        "    auth_method: key\n",
        encoding="utf-8",
    )

    data = _load_existing_servers_data(path)
    assert data["servers"]["wsl"]["username"] == "root"


def test_load_existing_raises_config_unreadable_on_yaml_syntax_error(
    tmp_path: Path,
):
    """A syntax-broken YAML must raise ``ConfigUnreadable``.

    The helper must NOT swallow parse errors and return an empty dict:
    doing so would let the import step overwrite the broken file with
    a sample, locking the user out of whatever they were trying to
    repair. The file is left untouched.
    """
    path = tmp_path / "servers.yaml"
    broken = "servers:\n  wsl:\n    host: [unclosed bracket\n"
    path.write_text(broken, encoding="utf-8")
    mtime_before = path.stat().st_mtime_ns

    with pytest.raises(ConfigUnreadable) as excinfo:
        _load_existing_servers_data(path)

    # The exception must carry the file path so the dialog can point
    # the user at the offending file.
    assert excinfo.value.path == path
    # And the file must still be byte-identical -- we never wrote it.
    assert path.read_text(encoding="utf-8") == broken
    assert path.stat().st_mtime_ns == mtime_before


def test_load_existing_raises_config_unreadable_for_list_root(tmp_path: Path):
    """A YAML list at the top level is also data we must keep, not clobber."""
    path = tmp_path / "servers.yaml"
    weird = "- just\n- a\n- list\n"
    path.write_text(weird, encoding="utf-8")

    with pytest.raises(ConfigUnreadable) as excinfo:
        _load_existing_servers_data(path)

    assert excinfo.value.path == path
    # The original file is preserved exactly.
    assert path.read_text(encoding="utf-8") == weird
    # The cause names the actual structural problem so the dialog can
    # show it verbatim.
    assert "list" in str(excinfo.value.cause).lower()


def test_load_existing_raises_config_unreadable_for_scalar_root(tmp_path: Path):
    """Even a single scalar at the top level must not silently become ``{}``."""
    path = tmp_path / "servers.yaml"
    path.write_text("just a plain string\n", encoding="utf-8")

    with pytest.raises(ConfigUnreadable) as excinfo:
        _load_existing_servers_data(path)

    assert path.read_text(encoding="utf-8") == "just a plain string\n"
    assert "str" in str(excinfo.value.cause).lower()


def test_config_unreadable_carries_cause_for_error_dialog():
    """``ConfigUnreadable.cause`` exposes the original parse failure.

    The Files-page error dialog reads ``str(exc.cause)`` so the user
    sees the real YAML error (or the structural mismatch) instead of
    a vague "couldn't read" message. We assert that the cause is
    preserved untouched, not wrapped in a different exception class.
    """
    path = Path("some/servers.yaml")
    original = ValueError("mapping expected at line 3")
    exc = ConfigUnreadable(path, original)

    assert exc.path is path
    assert exc.cause is original
    assert "mapping expected at line 3" in str(exc)
