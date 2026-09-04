"""Application boundary for the Runs-page remote monitor.

The SSH/event watcher is deliberately kept in :mod:`jobdesk_app.infrastructure.runtime`.
This module owns only the small identity and lifecycle contract shared by the
watcher adapter and the GUI.  In particular, a worker event never carries a
``RunRecord``, a server configuration, or a Qt object across the boundary.

``MonitorContext`` is a ``NamedTuple`` for one small compatibility reason:
the pre-existing Runs page exposed ``(workspace, run_id, server_id)`` tuples
in its private registry.  It remains immutable while still comparing equal to
those legacy tuples during the migration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import NamedTuple, Protocol, TypeVar, overload

_ContextValue = TypeVar("_ContextValue")
_MISSING = object()


class MonitorContext(NamedTuple):
    """Immutable workspace/run/server identity for one watcher."""

    workspace: Path
    run_id: str
    server_id: str

    @classmethod
    def create(cls, workspace: Path, run_id: str, server_id: str) -> "MonitorContext":
        return cls(Path(workspace), str(run_id), str(server_id))


def monitor_watch_id(workspace: Path, run_id: str, server_id: str) -> str:
    """Return the stable identity used to namespace monitor state."""

    return "\x1f".join((str(Path(workspace).resolve()), str(server_id), str(run_id)))


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    """Qt-free immutable event safe to queue between worker and GUI threads."""

    run_id: str
    server_id: str
    task_id: str
    exit_code: int | None
    watch_id: str | None = None
    workspace: Path | None = None

    @classmethod
    def from_event(
        cls,
        event: object,
        *,
        context: MonitorContext | None = None,
        watch_id: str | None = None,
    ) -> "MonitorEvent":
        """Freeze a service/legacy event without retaining its owner object."""

        run_id = str(getattr(event, "run_id", ""))
        server_id = str(getattr(event, "server_id", ""))
        task_id = str(getattr(event, "task_id", ""))
        exit_code_value = getattr(event, "exit_code", None)
        exit_code = None if exit_code_value is None else int(exit_code_value)
        event_watch_id = getattr(event, "watch_id", None)
        resolved_watch_id = watch_id if watch_id is not None else event_watch_id
        if resolved_watch_id is not None:
            resolved_watch_id = str(resolved_watch_id)
        workspace = context.workspace if context is not None else getattr(event, "workspace", None)
        if workspace is not None:
            workspace = Path(workspace)
        return cls(run_id, server_id, task_id, exit_code, resolved_watch_id, workspace)


@dataclass(frozen=True, slots=True)
class MonitorSubscription:
    """Immutable input used to admit one watcher."""

    context: MonitorContext
    watch_id: str
    remote_batch_dir: str
    progress_paths: tuple[str, ...] = ()
    # The transport adapter owns this value after admission.  It is retained
    # here only while the request is handed to the adapter; events never carry
    # it and the controller does not expose it through snapshots.
    server_config: object = None

    @classmethod
    def create(
        cls,
        context: MonitorContext,
        remote_batch_dir: str,
        server_config: object,
        progress_paths: Iterable[str] = (),
        watch_id: str | None = None,
    ) -> "MonitorSubscription":
        normalized_context = MonitorContext.create(*context)
        key = str(watch_id or monitor_watch_id(*normalized_context))
        paths = tuple(dict.fromkeys(str(path) for path in progress_paths if str(path)))
        return cls(normalized_context, key, str(remote_batch_dir), paths, server_config)


class MonitorPort(Protocol):
    """The legacy-compatible watcher surface consumed by the controller."""

    def watch(
        self,
        run_id: str,
        server_id: str,
        remote_batch_dir: str,
        server_config: object,
        progress_paths: Iterable[str] = (),
        watch_id: str | None = None,
    ) -> None: ...

    def unwatch(self, run_id: str, server_id: str, watch_id: str | None = None) -> None: ...

    def stop_all(self) -> None: ...


class _LegacyContextsView(MutableMapping[str, MonitorContext]):
    """Compatibility mapping that never exposes controller storage.

    Older GUI tests and extensions assign tuple values to the page's private
    ``_monitor_contexts`` attribute.  Keep that seam functional, but route
    every operation through the controller's locked query/mutation methods.
    Production page code uses the explicit controller APIs instead.
    """

    def __init__(self, controller: "RunsMonitorController") -> None:
        self._controller = controller

    def __getitem__(self, key: str) -> MonitorContext:
        context = self._controller.get_context(key)
        if context is None:
            raise KeyError(key)
        return context

    def __setitem__(self, key: str, value: object) -> None:
        context = self._controller._coerce_context(value)
        if context is None:
            raise ValueError("monitor context must contain workspace, run_id, and server_id")
        self._controller.set_context(key, context)

    def __delitem__(self, key: str) -> None:
        if not self._controller.remove_context(key):
            raise KeyError(key)

    def __iter__(self):
        return iter(self._controller.context_keys())

    def __len__(self) -> int:
        return len(self._controller.context_keys())

    def clear(self) -> None:
        self._controller.clear_contexts()

    @overload
    def pop(self, key: str) -> MonitorContext: ...

    @overload
    def pop(self, key: str, default: _ContextValue) -> MonitorContext | _ContextValue: ...

    def pop(self, key: str, default: object = _MISSING) -> object:
        context = self._controller.remove_context(key)
        if context is None:
            if default is _MISSING:
                raise KeyError(key)
            return default
        return context

    def setdefault(self, key: str, default: object = None) -> MonitorContext:
        context = self._controller.get_context(key)
        if context is not None:
            return context
        coerced = self._controller._coerce_context(default)
        if coerced is None:
            raise ValueError("monitor context must contain workspace, run_id, and server_id")
        self._controller.set_context(key, coerced)
        return coerced


class RunsMonitorController:
    """Own monitor identity/lifecycle while delegating transport operations.

    The controller is intentionally usable without Qt.  ``monitor_getter``
    lets the existing page retain its injectable ``_monitor`` compatibility
    seam: tests and legacy callers may replace that adapter without replacing
    the application controller or its identity registry.
    """

    def __init__(self, monitor: MonitorPort | None = None, *, monitor_getter: Callable[[], MonitorPort] | None = None):
        if monitor is None and monitor_getter is None:
            raise TypeError("monitor or monitor_getter is required")
        if monitor is not None and monitor_getter is not None:
            raise TypeError("provide monitor or monitor_getter, not both")
        self._monitor = monitor
        self._monitor_getter = monitor_getter
        self._contexts: dict[str, MonitorContext] = {}
        self._external_by_logical: dict[str, str] = {}
        self._logical_by_external: dict[str, str] = {}
        self._generations: dict[str, int] = {}
        self._ever_managed = False
        self._lock = RLock()
        self._closed = False

    @property
    def contexts(self) -> Mapping[str, MonitorContext]:
        """Return a locked, immutable snapshot of active contexts."""

        return self.context_snapshot()

    def context_snapshot(self) -> Mapping[str, MonitorContext]:
        """Return an immutable point-in-time registry snapshot."""

        with self._lock:
            return MappingProxyType(dict(self._contexts))

    def context_keys(self) -> tuple[str, ...]:
        """Return active logical watcher ids in a locked snapshot."""

        with self._lock:
            return tuple(self._contexts)

    def iter_contexts(self) -> tuple[tuple[str, MonitorContext], ...]:
        """Return active ``(logical_watch_id, context)`` pairs."""

        with self._lock:
            return tuple(self._contexts.items())

    def get_context(self, watch_id: str) -> MonitorContext | None:
        """Read one active context without exposing mutable storage."""

        with self._lock:
            value = self._contexts.get(str(watch_id))
            return self._coerce_context(value)

    def legacy_contexts_view(self) -> MutableMapping[str, MonitorContext]:
        """Return the test/extension compatibility mapping facade."""

        return _LegacyContextsView(self)

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _adapter(self) -> MonitorPort:
        adapter = self._monitor_getter() if self._monitor_getter is not None else self._monitor
        if adapter is None:
            raise RuntimeError("monitor adapter is unavailable")
        return adapter

    @staticmethod
    def _coerce_context(value: object) -> MonitorContext | None:
        if isinstance(value, MonitorContext):
            return value
        if not isinstance(value, (tuple, list)) or len(value) != 3:
            return None
        workspace = Path(str(value[0]))
        run_id = str(value[1])
        server_id = str(value[2])
        return MonitorContext.create(workspace, run_id, server_id)

    def _new_external_watch_id_locked(self, logical_watch_id: str) -> str:
        generation = self._generations.get(logical_watch_id, 0) + 1
        self._generations[logical_watch_id] = generation
        if generation == 1:
            return logical_watch_id
        # The stable logical id remains the page key; this external token is
        # what the watcher embeds in events, so an old queued event cannot be
        # accepted after a retire/re-subscribe cycle.
        return f"{logical_watch_id}\x1e{generation}"

    def _activate_context_locked(self, watch_id: str, context: MonitorContext) -> str:
        external_watch_id = self._new_external_watch_id_locked(watch_id)
        self._contexts[watch_id] = context
        self._external_by_logical[watch_id] = external_watch_id
        self._logical_by_external[external_watch_id] = watch_id
        self._ever_managed = True
        return external_watch_id

    def _retire_context_locked(self, watch_id: str) -> MonitorContext | None:
        context = self._contexts.pop(watch_id, None)
        external = self._external_by_logical.pop(watch_id, None)
        if external is not None:
            self._logical_by_external.pop(external, None)
        return self._coerce_context(context)

    def replace_contexts(self, contexts: Mapping[str, object]) -> None:
        """Replace the registry while retaining legacy tuple assignments."""

        normalized: dict[str, MonitorContext] = {}
        for watch_id, value in contexts.items():
            if isinstance(value, MonitorContext):
                context = value
            else:
                coerced = self._coerce_context(value)
                if coerced is None:
                    continue
                context = coerced
            normalized[str(watch_id)] = context
        with self._lock:
            self._contexts.clear()
            self._external_by_logical.clear()
            self._logical_by_external.clear()
            for watch_id, context in normalized.items():
                self._activate_context_locked(watch_id, context)

    def set_context(self, watch_id: str, context: MonitorContext) -> None:
        """Register one compatibility context through the locked API."""

        with self._lock:
            key = str(watch_id)
            if key in self._contexts:
                self._retire_context_locked(key)
            self._activate_context_locked(key, self._coerce_context(context) or context)

    def ensure_context(self, watch_id: str, context: MonitorContext) -> MonitorContext:
        """Return an active context, creating a compatibility one if absent."""

        with self._lock:
            current = self._coerce_context(self._contexts.get(str(watch_id)))
            if current is not None:
                return current
            normalized = self._coerce_context(context) or context
            self._activate_context_locked(str(watch_id), normalized)
            return normalized

    def remove_context(self, watch_id: str) -> MonitorContext | None:
        """Retire one logical context without calling the transport."""

        with self._lock:
            return self._retire_context_locked(str(watch_id))

    def clear_contexts(self) -> None:
        """Retire all logical contexts without calling the transport."""

        with self._lock:
            for watch_id in tuple(self._contexts):
                self._retire_context_locked(watch_id)

    def subscribe(self, subscription: MonitorSubscription) -> bool:
        """Admit one idempotent watcher, rolling back on adapter failure."""

        with self._lock:
            if self._closed:
                return False
            if subscription.watch_id in self._contexts:
                return False
            external_watch_id = self._activate_context_locked(subscription.watch_id, subscription.context)
        try:
            self._adapter().watch(
                subscription.context.run_id,
                subscription.context.server_id,
                subscription.remote_batch_dir,
                subscription.server_config,
                # Keep the old adapter/test call shape (a list) while the
                # application request itself remains tuple-backed/immutable.
                list(subscription.progress_paths),
                external_watch_id,
            )
        except Exception:
            with self._lock:
                if self._contexts.get(subscription.watch_id) == subscription.context:
                    self._retire_context_locked(subscription.watch_id)
            raise
        return True

    def subscribe_values(
        self,
        workspace: Path,
        run_id: str,
        server_id: str,
        remote_batch_dir: str,
        server_config: object,
        progress_paths: Iterable[str] = (),
        watch_id: str | None = None,
    ) -> bool:
        """Convenience bridge preserving the old page call shape."""

        return self.subscribe(
            MonitorSubscription.create(
                MonitorContext.create(workspace, run_id, server_id),
                remote_batch_dir,
                server_config,
                progress_paths,
                watch_id,
            )
        )

    def unsubscribe(self, watch_id: str, context: MonitorContext | None = None) -> bool:
        """Retire identity before stopping transport so late events fail closed."""

        key = str(watch_id)
        with self._lock:
            logical_key = self._logical_by_external.get(key, key)
            external_watch_id = self._external_by_logical.get(logical_key, key)
            selected = self._retire_context_locked(logical_key)
            if selected is None:
                selected = self._coerce_context(context)
        if selected is None:
            return False
        self._adapter().unwatch(selected.run_id, selected.server_id, external_watch_id)
        return True

    def accept_event(self, event: object) -> MonitorEvent | None:
        """Validate and freeze a service event against the live registry."""

        with self._lock:
            if self._closed:
                return None
            event_watch_id = getattr(event, "watch_id", None)
            if isinstance(event_watch_id, str) and event_watch_id:
                logical_watch_id = self._logical_by_external.get(event_watch_id)
                if logical_watch_id is None:
                    # This includes a token from a previous generation and a
                    # retired watcher.  Never fall back to run/server lookup.
                    return None
                context = self._coerce_context(self._contexts.get(logical_watch_id))
                if context is None:
                    return None
                if (getattr(event, "run_id", None), getattr(event, "server_id", None)) != (
                    context.run_id,
                    context.server_id,
                ):
                    return None
                return MonitorEvent.from_event(event, context=context, watch_id=logical_watch_id)

            matches = [
                (key, context)
                for key, value in self._contexts.items()
                for context in (self._coerce_context(value),)
                if context is not None
                if (getattr(event, "run_id", None), getattr(event, "server_id", None))
                == (context.run_id, context.server_id)
            ]
            if len(matches) == 1:
                key, context = matches[0]
                return MonitorEvent.from_event(event, context=context, watch_id=key)
            if self._contexts:
                return None
            if self._ever_managed:
                # A legacy event without a token is safe only before this
                # controller has ever managed a concrete watcher.
                return None
        # Preserve the pre-watch legacy callback contract for custom monitor
        # implementations that emit events without a watcher identity.
        try:
            return MonitorEvent.from_event(event)
        except (TypeError, ValueError):
            return None

    def stop_all(self) -> None:
        """Stop transports but keep the controller reusable."""

        with self._lock:
            active = tuple(self._contexts)
            for watch_id in active:
                self._retire_context_locked(watch_id)
        self._adapter().stop_all()

    def close(self) -> None:
        """Close once and reject all future subscriptions/events."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = tuple(self._contexts)
            for watch_id in active:
                self._retire_context_locked(watch_id)
        adapter = self._adapter()
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
        else:
            adapter.stop_all()


# Singular spelling is convenient for callers while retaining one class.
RunMonitorController = RunsMonitorController


__all__ = [
    "MonitorContext",
    "MonitorEvent",
    "MonitorPort",
    "MonitorSubscription",
    "RunMonitorController",
    "RunsMonitorController",
    "monitor_watch_id",
]
