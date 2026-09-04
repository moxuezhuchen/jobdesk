from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QThread

from jobdesk_app.gui.task_supervisor import (
    GuiTaskSupervisor,
    SupervisorClosedError,
    TaskAlreadyRunningError,
    TaskCallbacks,
)


class _Signal:
    def __init__(self) -> None:
        self.callbacks: list[Callable[..., Any]] = []

    def connect(self, callback: Callable[..., Any]) -> None:
        self.callbacks.append(callback)

    def emit(self, *args: object) -> None:
        for callback in list(self.callbacks):
            callback(*args)


class _Worker:
    def __init__(self, target: Callable[[], Any], *, fail_start: bool = False) -> None:
        self.target = target
        self.fail_start = fail_start
        self.result = _Signal()
        self.error = _Signal()
        self.log = _Signal()
        self.progress = _Signal()
        self.finished = _Signal()
        self.interrupted = False
        self.stop_calls = 0
        self.delete_calls = 0

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("native start failed")

    def requestInterruption(self) -> None:
        self.interrupted = True

    def isInterruptionRequested(self) -> bool:
        return self.interrupted

    def stop_safely(self, timeout_ms: int | None = 3000) -> None:
        del timeout_ms
        self.stop_calls += 1
        self.interrupted = True

    def deleteLater(self) -> None:
        self.delete_calls += 1


class _Factory:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.workers: list[_Worker] = []

    def __call__(self, target: Callable[[], Any]) -> _Worker:
        worker = _Worker(target, fail_start=self.fail_start)
        self.workers.append(worker)
        return worker


def test_start_failure_rolls_back_task_and_busy_lease(qapp) -> None:
    factory = _Factory(fail_start=True)
    supervisor = GuiTaskSupervisor(worker_factory=factory)
    lease = supervisor.acquire_busy("remote-mutation", "delete")
    assert lease is not None

    with pytest.raises(RuntimeError, match="native start failed"):
        supervisor.start("runs", "delete", lambda _ctx: None, busy_lease=lease)

    assert supervisor.task_count == 0
    assert lease.released
    assert not supervisor.is_busy("remote-mutation")


@pytest.mark.parametrize("terminal", ["success", "error", "cancel"])
def test_terminal_paths_release_registration_and_busy_exactly_once(qapp, terminal: str) -> None:
    factory = _Factory()
    supervisor = GuiTaskSupervisor(worker_factory=factory)
    lease = supervisor.acquire_busy("remote-mutation", terminal)
    assert lease is not None
    events: list[str] = []
    handle = supervisor.start(
        "runs",
        terminal,
        lambda _ctx: "value",
        TaskCallbacks(
            on_result=lambda _value: events.append("result"),
            on_error=lambda _error: events.append("error"),
            on_finished=lambda: events.append("finished"),
            on_cancelled=lambda: events.append("cancelled"),
        ),
        busy_lease=lease,
    )
    worker = factory.workers[-1]

    if terminal == "success":
        worker.result.emit("value")
    elif terminal == "error":
        worker.error.emit("boom")
    else:
        handle.cancel()
    worker.finished.emit()
    worker.finished.emit()

    expected = ["result", "finished"] if terminal == "success" else ["error", "finished"]
    if terminal == "cancel":
        expected = ["cancelled", "finished"]
    assert events == expected
    assert handle.done
    assert lease.released
    assert not supervisor.is_busy("remote-mutation")
    assert supervisor.task_count == 0
    assert worker.delete_calls == 2  # QObject deletion connection follows each synthetic finish emission.


def test_generation_discards_callbacks_after_owner_invalidation(qapp) -> None:
    factory = _Factory()
    supervisor = GuiTaskSupervisor(worker_factory=factory)
    seen: list[str] = []
    handle = supervisor.start(
        "runs",
        "preview",
        lambda _ctx: None,
        TaskCallbacks(on_result=seen.append, on_error=seen.append, on_finished=lambda: seen.append("finished")),
    )

    supervisor.invalidate("runs")
    handle.worker.result.emit("late result")
    handle.worker.error.emit("late error")
    handle.worker.finished.emit()

    assert seen == []
    assert handle.done


def test_cancel_discards_result_but_delivers_cancel_and_finish(qapp) -> None:
    factory = _Factory()
    supervisor = GuiTaskSupervisor(worker_factory=factory)
    seen: list[str] = []
    handle = supervisor.start(
        "runs",
        "download",
        lambda _ctx: None,
        TaskCallbacks(
            on_result=seen.append,
            on_cancelled=lambda: seen.append("cancelled"),
            on_finished=lambda: seen.append("finished"),
        ),
    )

    handle.cancel()
    handle.worker.result.emit("late")
    handle.worker.finished.emit()

    assert seen == ["cancelled", "finished"]


def test_duplicate_rejected_and_replace_invalidates_old_operation(qapp) -> None:
    factory = _Factory()
    supervisor = GuiTaskSupervisor(worker_factory=factory)
    old_seen: list[str] = []
    first = supervisor.start(
        "files",
        "remote-list",
        lambda _ctx: None,
        TaskCallbacks(on_result=old_seen.append),
    )
    with pytest.raises(TaskAlreadyRunningError):
        supervisor.start("files", "remote-list", lambda _ctx: None)

    new_seen: list[str] = []
    second = supervisor.start(
        "files",
        "remote-list",
        lambda _ctx: None,
        TaskCallbacks(on_result=new_seen.append),
        replace=True,
    )
    first.worker.result.emit("old")
    second.worker.result.emit("new")

    assert first.cancelled
    assert old_seen == []
    assert new_seen == ["new"]


def test_shutdown_is_idempotent_and_rejects_new_work(qapp) -> None:
    factory = _Factory()
    supervisor = GuiTaskSupervisor(worker_factory=factory)
    handle = supervisor.start("runs", "refresh", lambda _ctx: None)
    worker = factory.workers[-1]

    supervisor.shutdown()
    supervisor.shutdown()

    assert supervisor.closed
    assert handle.done
    assert worker.stop_calls == 1
    assert supervisor.task_count == 0
    with pytest.raises(SupervisorClosedError):
        supervisor.start("runs", "another", lambda _ctx: None)


def test_owner_shutdown_does_not_affect_other_owner_or_supervisor(qapp) -> None:
    first_factory = _Factory()
    second_factory = _Factory()
    first_window = GuiTaskSupervisor(worker_factory=first_factory)
    second_window = GuiTaskSupervisor(worker_factory=second_factory)
    first = first_window.start("runs", "refresh", lambda _ctx: None)
    second = second_window.start("runs", "refresh", lambda _ctx: None)

    first_window.shutdown("runs")

    assert first.done
    assert first_factory.workers[0].stop_calls == 1
    assert not second.done
    assert second_factory.workers[0].stop_calls == 0
    second.worker.result.emit("still alive")
    assert not second_window.closed


def test_busy_lease_is_exclusive_and_release_is_idempotent(qapp) -> None:
    supervisor = GuiTaskSupervisor(worker_factory=_Factory())
    first = supervisor.acquire_busy("runs", "delete")
    assert first is not None
    assert supervisor.acquire_busy("runs", "cancel") is None

    first.release()
    first.release()

    assert not supervisor.is_busy("runs")
    assert supervisor.acquire_busy("runs", "cancel") is not None


def test_real_worker_callback_is_dispatched_on_supervisor_qt_thread(qapp, qtbot) -> None:
    supervisor = GuiTaskSupervisor()
    callback_threads: list[QThread] = []
    supervisor.start(
        "runs",
        "query",
        lambda _ctx: "done",
        TaskCallbacks(on_result=lambda _result: callback_threads.append(QThread.currentThread())),
    )

    qtbot.waitUntil(lambda: bool(callback_threads), timeout=3000)

    assert callback_threads == [supervisor.thread()]
    supervisor.shutdown()
