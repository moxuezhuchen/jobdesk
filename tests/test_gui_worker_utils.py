from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobdesk_app.gui.worker_utils import WorkerContext, start_context_worker, start_tracked_worker


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeWorker:
    def __init__(self, target=None):
        self._target_fn = target
        self.result = _Signal()
        self.error = _Signal()
        self.log = _Signal()
        self.progress = _Signal()
        self.finished = _Signal()
        self.started_count = 0
        self.deleteLater = MagicMock()

    def start(self):
        self.started_count += 1

    def isInterruptionRequested(self):
        return False


class _Owner:
    def __init__(self):
        self._workers = []


def test_start_tracked_worker_removes_worker_when_finished():
    owner = _Owner()
    worker = _FakeWorker()

    start_tracked_worker(owner, worker, registry_attr="_workers")

    assert owner._workers == [worker]
    assert worker.started_count == 1
    worker.finished.emit()
    assert owner._workers == []
    worker.deleteLater.assert_called_once_with()


def test_start_tracked_worker_rolls_back_owner_registry_on_native_start_failure():
    """The real helper must not retain a worker whose QThread never started."""
    from PySide6.QtCore import QThread

    from jobdesk_app.gui.workers import BackgroundWorker

    owner = _Owner()
    worker = BackgroundWorker(lambda: None)

    with (
        patch.object(QThread, "start", side_effect=RuntimeError("native start failed")),
        pytest.raises(RuntimeError, match="native start failed"),
    ):
        start_tracked_worker(owner, worker, registry_attr="_workers")

    assert owner._workers == []
    assert worker not in BackgroundWorker._active


def test_start_tracked_worker_wires_result_error_and_progress_callbacks():
    owner = _Owner()
    worker = _FakeWorker()
    results = []
    errors = []
    progress = []

    start_tracked_worker(
        owner,
        worker,
        registry_attr="_workers",
        on_result=results.append,
        on_error=errors.append,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    worker.result.emit("ok")
    worker.error.emit("bad")
    worker.progress.emit(5, 10)

    assert results == ["ok"]
    assert errors == ["bad"]
    assert progress == [(5, 10)]


def test_start_context_worker_passes_emitters_to_target():
    owner = _Owner()
    captured = []

    def target(ctx: WorkerContext):
        ctx.emit_log("running")
        ctx.emit_progress(1, 3)
        return "done"

    with patch("jobdesk_app.gui.worker_utils.BackgroundWorker", side_effect=lambda target: _FakeWorker(target)):
        worker = start_context_worker(
            owner,
            target=target,
            registry_attr="_workers",
            on_result=lambda value: captured.append(("result", value)),
            on_progress=lambda done, total: captured.append(("progress", done, total)),
        )

    fake_worker = worker
    assert worker is fake_worker
    assert owner._workers == [fake_worker]
    result = fake_worker._target_fn()
    fake_worker.result.emit(result)
    assert captured == [("progress", 1, 3), ("result", "done")]


def test_file_transfer_page_does_not_mutate_worker_target_function():
    source = Path("src/jobdesk_app/gui/pages/file_transfer_page.py").read_text(encoding="utf-8")
    assert "worker._target_fn =" not in source
