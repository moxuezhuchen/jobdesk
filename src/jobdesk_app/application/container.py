"""Application lifetime container shared by presentation adapters."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import RLock

from .facades import FilesApplication, RunApplication, SettingsApplication, WorkflowApplication
from .outcomes import ApplicationClosedError


class ApplicationContainer:
    """Own the application facades and their process-local resources.

    Bootstrap code supplies close callbacks for resources such as monitor
    registries and session pools.  Callbacks run once in reverse registration
    order so dependants can be stopped before their underlying transports.
    """

    def __init__(
        self,
        *,
        runs: RunApplication,
        files: FilesApplication,
        workflows: WorkflowApplication,
        settings: SettingsApplication,
        close_callbacks: Iterable[Callable[[], None]] = (),
    ) -> None:
        self._runs = runs
        self._files = files
        self._workflows = workflows
        self._settings = settings
        self._close_callbacks = tuple(close_callbacks)
        self._closed = False
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def ensure_open(self) -> None:
        with self._lock:
            if self._closed:
                raise ApplicationClosedError("application container is closed")

    @property
    def runs(self) -> RunApplication:
        self.ensure_open()
        return self._runs

    @property
    def files(self) -> FilesApplication:
        self.ensure_open()
        return self._files

    @property
    def workflows(self) -> WorkflowApplication:
        self.ensure_open()
        return self._workflows

    @property
    def settings(self) -> SettingsApplication:
        self.ensure_open()
        return self._settings

    def close(self) -> None:
        """Close all registered resources once, attempting every callback."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
        errors: list[Exception] = []
        for callback in reversed(self._close_callbacks):
            try:
                callback()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("application shutdown failed", errors)

    def __enter__(self) -> "ApplicationContainer":
        self.ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


__all__ = ["ApplicationContainer"]
