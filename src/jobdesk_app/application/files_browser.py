"""Application-facing remote browser/query port for the Files page."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Callable, Protocol

from .file_transfer_ports import FileTransferPort, RemoteEntryLike


@dataclass(frozen=True, slots=True)
class FileBrowserEntrySnapshot:
    """Immutable copy of one remote directory entry."""

    name: str
    path: str
    is_dir: bool
    size_bytes: int | None
    modified_at: float | None
    permissions: str


@dataclass(frozen=True, slots=True)
class FileBrowserSnapshot:
    """Immutable result of one remote directory query."""

    remote_dir: str
    entries: tuple[FileBrowserEntrySnapshot, ...]
    generation: int = 0


class _ServiceProvider(Protocol):
    def __call__(self) -> FileTransferPort | None: ...


class FilesBrowserController:
    """Own remote listing and path normalization without Qt knowledge."""

    def __init__(self, service_provider: Callable[[], FileTransferPort | None]) -> None:
        self._service_provider = service_provider
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def list_remote(self, remote_dir: str) -> FileBrowserSnapshot:
        """List ``remote_dir`` and return copied, immutable entry values."""
        service = self._service_provider()
        if service is None:
            raise ConnectionError("Connect to a server first")
        normalized = self.normalize_remote_path(remote_dir)
        entries = tuple(self._copy_entry(entry) for entry in service.list_remote(normalized))
        self._generation += 1
        return FileBrowserSnapshot(normalized, entries, self._generation)

    @staticmethod
    def normalize_remote_path(path: str) -> str:
        value = (path or "/").strip()
        if not value.startswith("/"):
            value = "/" + value
        normalized = posixpath.normpath(value)
        return "/" if normalized in {"", "."} else normalized

    @staticmethod
    def _copy_entry(entry: RemoteEntryLike) -> FileBrowserEntrySnapshot:
        return FileBrowserEntrySnapshot(
            name=str(entry.name),
            path=str(entry.path),
            is_dir=bool(entry.is_dir),
            size_bytes=entry.size_bytes,
            modified_at=entry.modified_at,
            permissions=str(entry.permissions),
        )


__all__ = ["FileBrowserEntrySnapshot", "FileBrowserSnapshot", "FilesBrowserController"]
