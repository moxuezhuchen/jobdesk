"""Tests for the new ``WorkflowPage`` (Phase 2.0)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.gui.pages.workflow_page import WorkflowPage  # noqa: E402
from jobdesk_app.services.method_presets import MethodPresetStore  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


class _StubState:
    """Minimal AppState stub for constructing ``WorkflowPage`` outside MainWindow."""
    current_project_root = None
    repo = None


def test_default_view_loads_empty(qapp):
    page = WorkflowPage(state=_StubState(),
                        language="en",
                        preset_store=MethodPresetStore())
    assert page.preset_combo.count() >= 1
    page.close()
    page.deleteLater()


def test_use_for_submit_emits_signal(qapp):
    page = WorkflowPage(state=_StubState(),
                        language="en",
                        preset_store=MethodPresetStore())
    captured = []
    page.preset_chosen_for_submit.connect(
        lambda name, source: captured.append((name, source))
    )
    page._on_use_for_submit()
    assert captured, "signal must fire when a preset is selected"
    page.close()
    page.deleteLater()


def test_save_user_prompt_emits_saved(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "jobdesk_app.services.method_presets.get_app_data_dir",
        lambda: tmp_path,
    )
    store = MethodPresetStore()
    page = WorkflowPage(state=_StubState(),
                        language="en",
                        preset_store=store)
    captured = []
    page.preset_saved.connect(lambda name, source: captured.append((name, source)))
    # Force the save path with a known name
    page._save_as_user("user_xyz")
    assert captured == [("user_xyz", "user")]
    page.close()
    page.deleteLater()


def test_apply_language_translates(qapp):
    page = WorkflowPage(state=_StubState(),
                        language="en",
                        preset_store=MethodPresetStore())
    page.apply_language("zh")
    # Cheap assertion: title text rotated.
    assert page.preset_label.text() == "\u5de5\u4f5c\u6d41\u9884\u8bbe"
    page.close()
    page.deleteLater()
