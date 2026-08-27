"""Small application-facing ports used by the Qt shell.

The GUI pages remain Qt widgets, but the shell should not inspect their
controls to coordinate pages.  This module is deliberately free of Qt,
service, and transport imports.  It turns the page's public connection
snapshot into an immutable application value and exposes only public page
actions to the shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class _RawConnectionSnapshot(Protocol):
    """Structural view returned by the Files page's public API."""

    @property
    def server_id(self) -> str | None: ...

    @property
    def server(self) -> object | None: ...

    @property
    def connected(self) -> bool: ...

    @property
    def ready(self) -> bool: ...

    @property
    def remote_dir(self) -> str: ...


class _FilesPageWidget(Protocol):
    """Only the public methods the shell needs from the Files page."""

    def connection_snapshot(self) -> _RawConnectionSnapshot: ...

    def refresh(self) -> None: ...

    def upload_path(self, local_path: str | Path, remote_path: str, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class FileTargetSnapshot:
    """Immutable target selected in the Files page toolbar."""

    server_id: str | None
    remote_dir: str


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    """Immutable application view of the current Files-page connection."""

    target: FileTargetSnapshot
    server: object | None
    connected: bool
    ready: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "connected", bool(self.connected))
        object.__setattr__(self, "ready", bool(self.ready and self.connected))

    @property
    def server_id(self) -> str | None:
        return self.target.server_id

    @property
    def remote_dir(self) -> str:
        return self.target.remote_dir


class FilesPagePort:
    """Application port for the small public Files-page surface."""

    def __init__(self, page: _FilesPageWidget) -> None:
        self._page = page

    def snapshot(self) -> ConnectionSnapshot:
        """Copy the page's public snapshot before crossing the Qt boundary."""
        raw = self._page.connection_snapshot()
        # ``connected``/``ready`` are the only connection state that crosses
        # this boundary.  The fallback derives the booleans from the legacy
        # raw ``service`` attribute without retaining that mutable object, so
        # older injected page fixtures keep working during the migration.
        connected_value = getattr(raw, "connected", None)
        if connected_value is None:
            connected_value = getattr(raw, "service", None) is not None
        ready_value = getattr(raw, "ready", connected_value)
        return ConnectionSnapshot(
            target=FileTargetSnapshot(
                server_id=raw.server_id,
                remote_dir=raw.remote_dir,
            ),
            server=raw.server,
            connected=bool(connected_value),
            ready=bool(ready_value),
        )

    def refresh(self) -> None:
        """Refresh through the Files page's explicit public action."""
        self._page.refresh()

    def upload_path(self, local_path: str | Path, remote_path: str, *args: Any, **kwargs: Any) -> Any:
        """Upload through the Files page without exporting its service.

        The page remains the owner of the live transfer service.  This public
        operation port is intentionally separate from :meth:`snapshot`, so
        callers receive status values in snapshots and invoke transfers as
        actions rather than retaining a mutable service reference.
        """
        return self._page.upload_path(local_path, remote_path, *args, **kwargs)


class PageRefreshPort:
    """Expose a page's public refresh action without private fallbacks.

    RunsResultsPage historically called its public list action
    ``refresh_run_list`` while other pages expose ``refresh``.  The adapter
    accepts both public names so the shell remains independent of page
    implementation details; a page exposing neither simply has no refresh
    action for the global F5 shortcut.
    """

    def __init__(self, callback: Any = None) -> None:
        self._callback = callback if callable(callback) else None

    @classmethod
    def for_page(cls, page: object) -> "PageRefreshPort":
        callback = getattr(page, "refresh", None)
        if not callable(callback):
            callback = getattr(page, "refresh_run_list", None)
        return cls(callback)

    def refresh(self) -> bool:
        if self._callback is None:
            return False
        self._callback()
        return True


__all__ = [
    "ConnectionSnapshot",
    "FileTargetSnapshot",
    "FilesPagePort",
    "PageRefreshPort",
]
