"""Smoke test for the Files-page ``[Submit]`` button (Phase 2.0)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.gui.pages.file_transfer_page import FileTransferPage  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


class _StubState:
    current_project_root = None
    repo = None


def _build_page():
    page = FileTransferPage(
        state=_StubState(),
        log_cb=lambda msg: None,
        status_cb=lambda msg: None,
        error_cb=lambda title, msg: None,
    )
    return page


def test_submit_button_disabled_with_no_selection(qapp):
    page = _build_page()
    try:
        assert page.submit_btn.isEnabled() is False
    finally:
        page.close()
        page.deleteLater()


def test_submit_button_emits_signal_with_selection(qapp):
    page = _build_page()
    captured = []

    def _on_submit(sources):
        captured.append(sources)

    page.submit_requested_with_files.connect(_on_submit)
    page._selected_paths_for_side = lambda side: (["/tmp/a.gjf"] if side == "local" else [])
    page._build_input_sources = staticmethod(  # type: ignore[assignment]
        lambda paths, *, side: [
            type("S", (), {"path": type("P", (), {"name": "a.gjf"})(), "side": side, "kind": "gjf"})()
        ]
    )
    page._on_submit_clicked()
    assert captured, "signal must fire with at least one source"
    page.close()
    page.deleteLater()
