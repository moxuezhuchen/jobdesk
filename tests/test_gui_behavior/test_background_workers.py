"""Tests for BackgroundWorker registry and start_tracked_worker lifecycle."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from tests.test_gui_behavior.conftest import _FakeWorker

pytest.importorskip("PySide6", reason="PySide6 not installed")


def test_started_worker_is_kept_alive_in_registry():
    """Regression: rapid submissions overwrote the only reference to a running
    QThread, letting it be GC'd mid-run and aborting the process with
    'QThread: Destroyed while thread is still running'. start() must keep a strong
    reference in the registry until the thread finishes."""
    from PySide6.QtCore import QThread

    from jobdesk_app.gui.workers import BackgroundWorker

    worker = BackgroundWorker(lambda: None)
    with patch.object(QThread, "start"):  # register without spawning a real thread
        worker.start()
    assert worker in BackgroundWorker._active  # strong reference prevents GC
    worker._unregister()  # simulate the finished signal
    assert worker not in BackgroundWorker._active


def test_wait_all_tolerates_deleted_worker():
    """wait_all must not raise when a registered worker's C++ object was already
    deleted (e.g. on test/app teardown)."""
    from jobdesk_app.gui.workers import BackgroundWorker

    dead = MagicMock()
    dead.wait.side_effect = RuntimeError("Internal C++ object already deleted")
    BackgroundWorker._active.add(dead)
    BackgroundWorker.wait_all()
    assert dead not in BackgroundWorker._active


def test_tracked_worker_ignores_callbacks_after_owner_shutdown():
    from jobdesk_app.gui.worker_utils import start_tracked_worker

    owner = MagicMock()
    owner._shutting_down = True
    owner._workers = []
    worker = _FakeWorker()
    on_result = MagicMock()
    on_error = MagicMock()
    on_log = MagicMock()
    on_progress = MagicMock()

    start_tracked_worker(
        owner,
        worker,
        registry_attr="_workers",
        on_result=on_result,
        on_error=on_error,
        on_log=on_log,
        on_progress=on_progress,
    )

    worker.result.emit(object())
    worker.error.emit("error")
    worker.log.emit("log")
    worker.progress.emit(1, 2)

    on_result.assert_not_called()
    on_error.assert_not_called()
    on_log.assert_not_called()
    on_progress.assert_not_called()
