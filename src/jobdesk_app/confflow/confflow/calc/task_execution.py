#!/usr/bin/env python3
"""Future/worker exception classification used by ``calc.async_exec``.

The async executor wrapper catches generic exceptions raised from
``concurrent.futures`` futures and needs to map them onto the same
``error_kind`` vocabulary that the rest of ``confflow.calc`` already uses
(see ``retry_runner.RetryAwareTaskRunner.TRANSIENT_KINDS`` and the
``error_kind`` strings produced by ``components.executor`` and
``components.task_runner``).

Keeping the classifier here rather than inside ``async_exec/__init__.py``
keeps the async executor decoupled from internal implementation details
of ``concurrent.futures`` and makes the mapping unit-testable in
isolation.
"""

from __future__ import annotations

from concurrent.futures.process import BrokenProcessPool

try:
    # ``BrokenThreadPool`` was added by PEP 713 and may not exist on the
    # oldest Python versions still in the project's matrix; flag it so
    # the runtime isinstance branch can be skipped on those builds.
    from concurrent.futures import BrokenThreadPool as _BrokenThreadPool
    _HAS_BROKEN_THREAD_POOL = True
except ImportError:  # pragma: no cover - depends on Python version
    _BrokenThreadPool = None  # type: ignore[assignment]
    _HAS_BROKEN_THREAD_POOL = False

__all__ = ["_classify_future_exception"]


def _classify_future_exception(exc: BaseException) -> str:
    """Map a ``concurrent.futures`` exception onto a ``calc`` error_kind.

    Returns
    -------
    str
        One of ``"broken_process_pool"``, ``"serialization_error"``,
        ``"worker_exception"``, or a fallback ``"exec_error"`` for
        anything else. The return value is intended to feed
        ``TaskResult.error_kind``; downstream retry logic checks whether
        the kind is in ``RetryConfig.retry_on``.
    """
    if isinstance(exc, BrokenProcessPool):
        return "broken_process_pool"
    if _HAS_BROKEN_THREAD_POOL and isinstance(exc, _BrokenThreadPool):
        return "broken_process_pool"
    # ``concurrent.futures`` pickles the task payload before dispatching;
    # a pickling failure surfaces as ``TypeError`` from inside the future
    # resolution rather than a typed exception.
    if isinstance(exc, (TypeError, ValueError)):
        return "serialization_error"
    return "worker_exception"