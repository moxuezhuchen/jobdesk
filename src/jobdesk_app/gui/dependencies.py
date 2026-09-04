"""GUI-local dependency registry configured by the composition entry points.

Leaf widgets use narrow, duck-typed collaborators without importing the
process bootstrap module.  Production factories are registered by ``app`` or
``MainWindow`` before pages are built; lightweight in-memory settings keep
isolated widget tests deterministic.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Protocol


class GuiSettingsStorePort(Protocol):
    def load(self) -> Any: ...

    def update(self, **values: Any) -> Any: ...

_settings_store_factory: Callable[[], Any] | None = None
_ssh_factory: Callable[..., Any] | None = None
_sftp_factory: Callable[..., Any] | None = None
_monitor_factory: Callable[..., Any] | None = None

class _MemorySettingsStore:
    def __init__(self) -> None:
        self._values: dict[str, Any] = {
            "column_widths": {},
            "collapsed_library_groups": [],
            "show_onboarding": True,
        }

    def load(self) -> Any:
        return SimpleNamespace(**self._values)

    def update(self, **values: Any) -> Any:
        self._values.update(values)
        return self.load()


_memory_settings = _MemorySettingsStore()


def configure_gui_dependencies(
    *,
    settings_store_factory: Callable[[], Any] | None = None,
    ssh_factory: Callable[..., Any] | None = None,
    sftp_factory: Callable[..., Any] | None = None,
    monitor_factory: Callable[..., Any] | None = None,
) -> None:
    global _settings_store_factory, _ssh_factory, _sftp_factory, _monitor_factory
    if settings_store_factory is not None:
        _settings_store_factory = settings_store_factory
    if ssh_factory is not None:
        _ssh_factory = ssh_factory
    if sftp_factory is not None:
        _sftp_factory = sftp_factory
    if monitor_factory is not None:
        _monitor_factory = monitor_factory


def settings_store() -> GuiSettingsStorePort:
    return _settings_store_factory() if _settings_store_factory is not None else _memory_settings


def create_ssh(server: Any) -> Any:
    if _ssh_factory is None:
        raise RuntimeError("GUI SSH factory was not configured by the composition root")
    return _ssh_factory(server)


def create_sftp(ssh: Any) -> Any:
    if _sftp_factory is None:
        raise RuntimeError("GUI SFTP factory was not configured by the composition root")
    return _sftp_factory(ssh)


def create_monitor(*args: Any, **kwargs: Any) -> Any:
    if _monitor_factory is None:
        raise RuntimeError("GUI monitor factory was not configured by the composition root")
    return _monitor_factory(*args, **kwargs)


__all__ = [
    "configure_gui_dependencies",
    "create_monitor",
    "create_sftp",
    "create_ssh",
    "settings_store",
]
