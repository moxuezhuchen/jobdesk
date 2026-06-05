from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .workers import BackgroundWorker


@dataclass(frozen=True)
class WorkerContext:
    emit_log: Callable[[str], None]
    emit_progress: Callable[[int, int], None]
    is_interruption_requested: Callable[[], bool]


def is_owner_shutting_down(owner: object) -> bool:
    return bool(getattr(owner, "_shutting_down", False))


def start_tracked_worker(
    owner: object,
    worker: BackgroundWorker,
    *,
    registry_attr: str,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    delete_later: bool = True,
) -> BackgroundWorker:
    registry = getattr(owner, registry_attr, None)
    if registry is None:
        registry = []
        setattr(owner, registry_attr, registry)

    def _remove_worker() -> None:
        current = getattr(owner, registry_attr, [])
        if worker in current:
            current.remove(worker)

    def _guarded(callback):
        def _call(*args):
            if not is_owner_shutting_down(owner):
                callback(*args)
        return _call

    if on_result is not None:
        worker.result.connect(_guarded(on_result))
    if on_error is not None:
        worker.error.connect(_guarded(on_error))
    if on_progress is not None:
        worker.progress.connect(_guarded(on_progress))
    if on_log is not None:
        worker.log.connect(_guarded(on_log))
    if on_finished is not None:
        worker.finished.connect(_guarded(on_finished))
    worker.finished.connect(_remove_worker)
    if delete_later and hasattr(worker, "deleteLater"):
        worker.finished.connect(worker.deleteLater)
    registry.append(worker)
    worker.start()
    return worker


def start_context_worker(
    owner: object,
    *,
    target: Callable[[WorkerContext], Any],
    registry_attr: str,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    delete_later: bool = True,
) -> BackgroundWorker:
    worker_ref: dict[str, BackgroundWorker] = {}

    def _run() -> Any:
        worker = worker_ref["worker"]
        ctx = WorkerContext(
            emit_log=worker.log.emit,
            emit_progress=worker.progress.emit,
            is_interruption_requested=worker.isInterruptionRequested,
        )
        return target(ctx)

    worker = BackgroundWorker(_run)
    worker_ref["worker"] = worker
    return start_tracked_worker(
        owner,
        worker,
        registry_attr=registry_attr,
        on_result=on_result,
        on_error=on_error,
        on_progress=on_progress,
        on_log=on_log,
        on_finished=on_finished,
        delete_later=delete_later,
    )
