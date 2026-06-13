from pathlib import Path

from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.pages.file_transfer_helpers import (
    connection_status_text,
    format_selection_summary,
)


def test_i18n_source_uses_ascii_escapes_for_chinese_labels():
    source = Path("src/jobdesk_app/gui/i18n.py").read_text(encoding="utf-8")
    assert source.isascii()


def test_translate_core_gui_labels_to_chinese():
    assert tr("Files", "zh") == "\u6587\u4ef6"
    assert tr("Runs", "zh") == "\u8fd0\u884c"
    assert tr("Settings", "zh") == "\u8bbe\u7f6e"
    assert tr("Local Folder", "zh") == "\u672c\u5730\u76ee\u5f55"
    assert tr("Run Selected", "zh") == "\u8fd0\u884c\u6240\u9009"


def test_translate_defaults_to_english():
    assert tr("Files", "en") == "Files"
    assert tr("Unknown Label", "zh") == "Unknown Label"


def test_translate_projects_label():
    assert tr("Projects", "en") == "Projects"
    assert tr("Projects", "zh") == "\u9879\u76ee"


def test_gui_status_helpers_translate_to_chinese():
    assert connection_status_text(None, False, language="zh") == "\u672a\u9009\u62e9\u670d\u52a1\u5668"
    assert connection_status_text("demo-server", True, language="zh") == "\u5df2\u8fde\u63a5: demo-server"
    assert format_selection_summary(1, 2, "zh") == "\u672c\u5730 1 | \u8fdc\u7a0b 2"


def test_button_feedback_labels_translate_to_chinese():
    for label in [
        "Refreshing...",
        "Refreshed",
        "Refresh failed",
        "Opened",
        "Open failed",
        "Submit failed",
        "Create failed",
        "Creating...",
        "Created {n}",
        "Submitted {n}",
    ]:
        assert tr(label, "zh", n=2) != label.format(n=2)
