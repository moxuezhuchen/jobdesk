"""Application-facing structural ports for remote file transfer.

The concrete :class:`FileTransferService` remains the composition-root
implementation.  Application controllers depend on this structural surface
instead, so they can be tested with small fakes and do not need to import the
service layer or the transport implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..core.file_transfer import OverwritePolicy


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


__all__ = ["FileTransferPort", "RemoteEntryLike", "TransferRecordLike"]
