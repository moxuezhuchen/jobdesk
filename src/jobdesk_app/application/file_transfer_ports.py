"""Application-facing structural ports for remote file transfer.

The concrete :class:`FileTransferService` remains the composition-root
implementation.  Application controllers depend on this structural surface
instead, so they can be tested with small fakes and do not need to import the
service layer or the transport implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..core.file_transfer import OverwritePolicy

if TYPE_CHECKING:
    from .facades import FilesApplication


@runtime_checkable
class RemoteEntryLike(Protocol):
    """Structural view of one entry returned by ``list_remote``."""

    name: str
    path: str
    is_dir: bool
    size_bytes: int | None
    modified_at: float | None
    permissions: str


@runtime_checkable
class TransferRecordLike(Protocol):
    """Structural view of one file-transfer result record."""

    direction: str
    local_path: str
    remote_path: str
    size_bytes: int | None
    status: str
    reason: str | None
    dry_run: bool


@runtime_checkable
class FileTransferPort(Protocol):
    """Public application surface retained by the existing transfer service.

    The browser currently consumes only :meth:`list_remote`.  The remaining
    methods intentionally mirror the existing service operations so the
    transfer queue and remote-edit slices can migrate without changing their
    behavior or forcing callers back through a concrete service import.
    """

    def list_remote(self, remote_dir: str) -> list[RemoteEntryLike]: ...

    def upload_path(
        self,
        local_path: str | Path,
        remote_path: str,
        policy: OverwritePolicy = OverwritePolicy.skip_same_size,
        dry_run: bool = False,
        progress_callback: Callable[..., object] | None = None,
    ) -> TransferRecordLike | list[TransferRecordLike]: ...

    def download_path(
        self,
        remote_path: str,
        local_path: str | Path,
        policy: OverwritePolicy = OverwritePolicy.skip_same_size,
        dry_run: bool = False,
        progress_callback: Callable[..., object] | None = None,
    ) -> TransferRecordLike | list[TransferRecordLike]: ...

    def mkdir_remote(self, remote_dir: str) -> None: ...

    def delete_remote(
        self,
        remote_path: str,
        recursive: bool = False,
        extra_allowed_roots: list[str] | None = None,
    ) -> None: ...

    def rename_remote(self, old_path: str, new_path: str) -> None: ...

    def preview_remote_text(self, remote_path: str, max_bytes: int = 65536) -> str: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _FacadeTransferRecord:
    direction: str
    local_path: str
    remote_path: str
    size_bytes: int | None
    status: str = "transferred"
    reason: str | None = None
    dry_run: bool = False


class FacadeFileTransferPort:
    """Adapt the public Files facade to the legacy presentation port.

    This adapter carries only a server id.  It never owns a transport; the
    application container and its shared session pool retain that lifecycle.
    """

    def __init__(self, application: FilesApplication, server_id: str) -> None:
        self._application = application
        self._server_id = server_id

    @staticmethod
    def _value(outcome):
        if outcome.failures:
            raise RuntimeError("; ".join(failure.display_text for failure in outcome.failures))
        return outcome.value

    def list_remote(self, remote_dir: str):
        return list(self._value(self._application.list_remote(self._server_id, remote_dir)) or ())

    def upload_path(
        self,
        local_path: str | Path,
        remote_path: str,
        policy: OverwritePolicy = OverwritePolicy.skip_same_size,
        dry_run: bool = False,
        progress_callback: Callable[..., object] | None = None,
    ):
        batch = self._value(
            self._application.upload(
                self._server_id,
                str(local_path),
                remote_path,
                policy=policy.value,
                dry_run=dry_run,
                progress_callback=progress_callback,
            )
        )
        assert batch is not None
        if progress_callback is not None:
            total = sum(record.transferred_bytes for record in batch.records)
            progress_callback(total, total)
        return [
            _FacadeTransferRecord(
                "upload",
                record.local_path,
                record.remote_path,
                record.transferred_bytes,
                record.status,
                record.reason or None,
                dry_run,
            )
            for record in batch.records
        ]

    def download_path(
        self,
        remote_path: str,
        local_path: str | Path,
        policy: OverwritePolicy = OverwritePolicy.skip_same_size,
        dry_run: bool = False,
        progress_callback: Callable[..., object] | None = None,
    ):
        batch = self._value(
            self._application.download(
                self._server_id,
                remote_path,
                str(local_path),
                policy=policy.value,
                dry_run=dry_run,
                progress_callback=progress_callback,
            )
        )
        assert batch is not None
        if progress_callback is not None:
            total = sum(record.transferred_bytes for record in batch.records)
            progress_callback(total, total)
        return [
            _FacadeTransferRecord(
                "download",
                record.local_path,
                record.remote_path,
                record.transferred_bytes,
                record.status,
                record.reason or None,
                dry_run,
            )
            for record in batch.records
        ]

    def mkdir_remote(self, remote_dir: str) -> None:
        self._value(self._application.mkdir(self._server_id, remote_dir))

    def delete_remote(
        self,
        remote_path: str,
        recursive: bool = False,
        extra_allowed_roots: list[str] | None = None,
    ) -> None:
        self._value(
            self._application.delete(
                self._server_id,
                remote_path,
                recursive=recursive,
                allowed_roots=tuple(extra_allowed_roots or ()),
            )
        )

    def rename_remote(self, old_path: str, new_path: str) -> None:
        self._value(self._application.rename(self._server_id, old_path, new_path))

    def preview_remote_text(self, remote_path: str, max_bytes: int = 65536) -> str:
        return str(self._value(self._application.preview_text(self._server_id, remote_path, max_bytes=max_bytes)) or "")

    def close(self) -> None:
        """Do nothing: the shared application container owns all sessions."""


__all__ = ["FacadeFileTransferPort", "FileTransferPort", "RemoteEntryLike", "TransferRecordLike"]
