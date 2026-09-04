"""Window-scoped ownership for GUI background tasks.

The supervisor deliberately knows nothing about JobDesk services or transports.
It owns Qt workers, callback generations, and busy leases so a closed or
replaced view cannot be mutated by a late background result.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from itertools import count
from threading import RLock
from typing import Any, Protocol

from PySide6.QtCore import QObject, Signal

from .worker_utils import WorkerContext
from .workers import BackgroundWorker

TaskTarget = Callable[[WorkerContext], Any]


class _SignalLike(Protocol):
    def connect(self, callback: Callable[..., Any]) -> None: ...

    def emit(self, *args: object) -> None: ...


class WorkerLike(Protocol):
    @property
    def result(self) -> _SignalLike: ...

    @property
    def error(self) -> _SignalLike: ...

    @property
    def log(self) -> _SignalLike: ...

    @property
    def progress(self) -> _SignalLike: ...

    @property
    def finished(self) -> _SignalLike: ...

    def start(self) -> None: ...

    def requestInterruption(self) -> None: ...

    def isInterruptionRequested(self) -> bool: ...

    def stop_safely(self, timeout_ms: int | None = 3000) -> None: ...

    def deleteLater(self) -> None: ...


WorkerFactory = Callable[[Callable[[], Any]], WorkerLike]


class TaskAlreadyRunningError(RuntimeError):
    """Raised when an operation key already has an active worker."""


class SupervisorClosedError(RuntimeError):
    """Raised when work is submitted after its supervisor/owner is closed."""


@dataclass(frozen=True, slots=True)
class TaskCallbacks:
    """Callbacks delivered on the supervisor's Qt thread."""

    on_result: Callable[[Any], None] | None = None
    on_error: Callable[[str], None] | None = None
    on_progress: Callable[[int, int], None] | None = None
    on_log: Callable[[str], None] | None = None
    on_finished: Callable[[], None] | None = None
    on_cancelled: Callable[[], None] | None = None


class _CallbackDispatcher(QObject):
    requested = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.requested.connect(self._dispatch)

    def post(self, callback: Callable[[], None]) -> None:
        self.requested.emit(callback)

    @staticmethod
    def _dispatch(callback: object) -> None:
        if callable(callback):
            callback()


@dataclass(slots=True)
class _TaskState:
    token: int
    owner_key: Hashable
    operation_key: Hashable
    owner_generation: int
    operation_generation: int
    worker: WorkerLike
    callbacks: TaskCallbacks
    busy_lease: BusyLease | None
    cancelled: bool = False
    finalized: bool = False


class BusyLease:
    """Idempotent ownership token for a mutually exclusive GUI scope."""

    def __init__(self, supervisor: GuiTaskSupervisor, scope: Hashable, operation: str, token: int) -> None:
        self._supervisor = supervisor
        self.scope = scope
        self.operation = operation
        self._token = token
        self._released = False

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._supervisor._release_busy(self.scope, self._token)

    def __enter__(self) -> BusyLease:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.release()


class TaskHandle:
    """Small cancellation/status handle returned to the view."""

    def __init__(self, supervisor: GuiTaskSupervisor, state: _TaskState) -> None:
        self._supervisor = supervisor
        self._state = state
        self._token = state.token
        self.worker = state.worker

    @property
    def done(self) -> bool:
        return self._state.finalized

    @property
    def cancelled(self) -> bool:
        return self._state.cancelled

    def cancel(self) -> None:
        self._supervisor._cancel_task(self._token)


class GuiTaskSupervisor(QObject):
    """Own background workers and reject callbacks from stale generations."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        worker_factory: WorkerFactory = BackgroundWorker,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._dispatcher = _CallbackDispatcher(self)
        self._lock = RLock()
        self._tokens = count(1)
        self._tasks: dict[int, _TaskState] = {}
        self._task_by_key: dict[tuple[Hashable, Hashable], int] = {}
        self._owner_generations: dict[Hashable, int] = {}
        self._operation_generations: dict[tuple[Hashable, Hashable], int] = {}
        self._closed_owners: set[Hashable] = set()
        self._busy: dict[Hashable, tuple[int, str]] = {}
        self._closed = False

    def start(
        self,
        owner_key: Hashable,
        operation_key: Hashable,
        target: TaskTarget,
        callbacks: TaskCallbacks | None = None,
        *,
        replace: bool = False,
        busy_lease: BusyLease | None = None,
    ) -> TaskHandle:
        """Start one operation, optionally replacing the same operation key."""

        callbacks = callbacks or TaskCallbacks()
        key = (owner_key, operation_key)
        with self._lock:
            if self._closed or owner_key in self._closed_owners:
                raise SupervisorClosedError(f"GUI task owner is closed: {owner_key!r}")
            previous_token = self._task_by_key.get(key)
            if previous_token is not None and not replace:
                raise TaskAlreadyRunningError(f"GUI task is already running: {operation_key!r}")
            if busy_lease is not None and (busy_lease.released or not self._owns_busy_lease(busy_lease)):
                raise ValueError("busy lease is no longer active")
            operation_generation = self._operation_generations.get(key, 0) + 1
            self._operation_generations[key] = operation_generation
            owner_generation = self._owner_generations.get(owner_key, 0)
            token = next(self._tokens)

        if previous_token is not None:
            self._cancel_task(previous_token)

        worker_ref: dict[str, WorkerLike] = {}

        def run_target() -> Any:
            worker = worker_ref["worker"]
            context = WorkerContext(
                emit_log=lambda message: self._emit_worker_signal(worker.log, message),
                emit_progress=lambda done, total: self._emit_worker_signal(worker.progress, done, total),
                is_interruption_requested=worker.isInterruptionRequested,
            )
            return target(context)

        worker = self._worker_factory(run_target)
        worker_ref["worker"] = worker
        state = _TaskState(
            token=token,
            owner_key=owner_key,
            operation_key=operation_key,
            owner_generation=owner_generation,
            operation_generation=operation_generation,
            worker=worker,
            callbacks=callbacks,
            busy_lease=busy_lease,
        )
        with self._lock:
            self._tasks[token] = state
            self._task_by_key[key] = token

        worker.result.connect(lambda value: self._post_if_current(state, callbacks.on_result, value))
        worker.error.connect(lambda error: self._post_if_current(state, callbacks.on_error, str(error)))
        worker.progress.connect(lambda done, total: self._post_if_current(state, callbacks.on_progress, done, total))
        worker.log.connect(lambda message: self._post_if_current(state, callbacks.on_log, str(message)))
        worker.finished.connect(lambda: self._worker_finished(state))
        if hasattr(worker, "deleteLater"):
            worker.finished.connect(worker.deleteLater)

        try:
            worker.start()
        except Exception:
            self._finalize(state, notify=False)
            raise
        return TaskHandle(self, state)

    def acquire_busy(self, scope: Hashable, operation: str) -> BusyLease | None:
        """Acquire an exclusive scope or return ``None`` when it is busy."""

        with self._lock:
            if self._closed or scope in self._busy:
                return None
            token = next(self._tokens)
            self._busy[scope] = (token, operation)
        return BusyLease(self, scope, operation, token)

    def invalidate(self, owner_key: Hashable) -> None:
        """Advance an owner's generation and interrupt its current tasks."""

        with self._lock:
            self._owner_generations[owner_key] = self._owner_generations.get(owner_key, 0) + 1
            states = [state for state in self._tasks.values() if state.owner_key == owner_key]
        for state in states:
            self._request_interruption(state)

    def shutdown(self, owner_key: Hashable | None = None, *, timeout_ms: int | None = 3000) -> None:
        """Stop one owner, or permanently close this supervisor, idempotently."""

        with self._lock:
            if owner_key is None:
                if self._closed and not self._tasks:
                    return
                self._closed = True
                owner_keys = {state.owner_key for state in self._tasks.values()}
                self._closed_owners.update(owner_keys)
                states = list(self._tasks.values())
                for key in owner_keys:
                    self._owner_generations[key] = self._owner_generations.get(key, 0) + 1
            else:
                self._closed_owners.add(owner_key)
                self._owner_generations[owner_key] = self._owner_generations.get(owner_key, 0) + 1
                states = [state for state in self._tasks.values() if state.owner_key == owner_key]

        for state in states:
            self._request_interruption(state)
        for state in states:
            try:
                state.worker.stop_safely(timeout_ms)
            except RuntimeError:
                pass
            finally:
                self._finalize(state, notify=False)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def task_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def is_busy(self, scope: Hashable) -> bool:
        with self._lock:
            return scope in self._busy

    @staticmethod
    def _emit_worker_signal(signal: object, *args: object) -> None:
        emit = getattr(signal, "emit", None)
        if callable(emit):
            emit(*args)

    def _worker_finished(self, state: _TaskState) -> None:
        self._finalize(state, notify=True)

    def _finalize(self, state: _TaskState, *, notify: bool) -> None:
        with self._lock:
            if state.finalized:
                return
            state.finalized = True
            self._tasks.pop(state.token, None)
            key = (state.owner_key, state.operation_key)
            if self._task_by_key.get(key) == state.token:
                self._task_by_key.pop(key, None)
            cancelled = state.cancelled or state.worker.isInterruptionRequested()
        if state.busy_lease is not None:
            state.busy_lease.release()
        if notify:
            if cancelled:
                self._post_if_current(state, state.callbacks.on_cancelled, allow_cancelled=True)
            self._post_if_current(state, state.callbacks.on_finished, allow_cancelled=True)

    def _post_if_current(
        self,
        state: _TaskState,
        callback: Callable[..., Any] | None,
        *args: object,
        allow_cancelled: bool = False,
    ) -> None:
        if callback is None or not self._is_current(state, allow_cancelled=allow_cancelled):
            return

        def deliver() -> None:
            if self._is_current(state, allow_cancelled=allow_cancelled):
                callback(*args)

        self._dispatcher.post(deliver)

    def _is_current(self, state: _TaskState, *, allow_cancelled: bool = False) -> bool:
        key = (state.owner_key, state.operation_key)
        with self._lock:
            return (
                not self._closed
                and state.owner_key not in self._closed_owners
                and (allow_cancelled or not state.cancelled)
                and self._owner_generations.get(state.owner_key, 0) == state.owner_generation
                and self._operation_generations.get(key, 0) == state.operation_generation
            )

    def _request_interruption(self, state: _TaskState) -> None:
        with self._lock:
            if state.finalized:
                return
            state.cancelled = True
        try:
            state.worker.requestInterruption()
        except RuntimeError:
            self._finalize(state, notify=False)

    def _cancel_task(self, token: int) -> None:
        with self._lock:
            state = self._tasks.get(token)
        if state is not None:
            self._request_interruption(state)

    def _owns_busy_lease(self, lease: BusyLease) -> bool:
        current = self._busy.get(lease.scope)
        return lease._supervisor is self and current is not None and current[0] == lease._token

    def _release_busy(self, scope: Hashable, token: int) -> None:
        with self._lock:
            current = self._busy.get(scope)
            if current is not None and current[0] == token:
                self._busy.pop(scope, None)


__all__ = [
    "BusyLease",
    "GuiTaskSupervisor",
    "SupervisorClosedError",
    "TaskAlreadyRunningError",
    "TaskCallbacks",
    "TaskHandle",
]
