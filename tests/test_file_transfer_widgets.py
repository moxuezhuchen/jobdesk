import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.pages.file_transfer_widgets import _parse_timestamp


def test_parse_timestamp_handles_unix_epoch_on_windows():
    assert _parse_timestamp("1970-01-01 08:00:00") == 8 * 60 * 60


def test_parse_timestamp_preserves_timezone_aware_instants():
    assert _parse_timestamp("1970-01-01T00:00:00+00:00") == 0
