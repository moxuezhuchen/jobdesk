from __future__ import annotations

import posixpath
from pathlib import Path

from ..core.file_transfer import OverwritePolicy, policy_to_transfer_flags
from ..remote.errors import RemotePathError


def ensure_safe_remote_path(remote_path: str) -> str:
    if not remote_path or not remote_path.startswith("/"):
        raise RemotePathError(f"remote path must be absolute POSIX: {remote_path!r}")
    if "\\" in remote_path:
        raise RemotePathError(f"remote path must use POSIX separators: {remote_path!r}")
    if ".." in remote_path.split("/"):
        raise RemotePathError(f"remote path must not contain '..': {remote_path!r}")
    normalized = posixpath.normpath(remote_path)
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    return normalized


class FileTransferService:
    def __init__(
        self,
        sftp_factory,
        protected_remote_roots: list[str] | None = None,
        allowed_delete_roots: list[str] | None = None,
    ):
        self._sftp_factory = sftp_factory
        self._protected_roots = {ensure_safe_remote_path(p) for p in (protected_remote_roots or [])}
        self._allowed_delete_roots = {
            ensure_safe_remote_path(p) for p in (allowed_delete_roots or [])
        }

    def list_remote(self, remote_dir: str):
        with self._sftp() as sftp:
            return sftp.list_dir_info(ensure_safe_remote_path(remote_dir))

    def upload_path(self, local_path: str | Path, remote_path: str, policy: OverwritePolicy = OverwritePolicy.skip_same_size, dry_run: bool = False, progress_callback=None):
        overwrite, skip_same = policy_to_transfer_flags(policy)
        local_path = Path(local_path)
        remote_path = ensure_safe_remote_path(remote_path)
        with self._sftp() as sftp:
            if local_path.is_dir():
                return sftp.upload_dir(local_path, remote_path, overwrite=overwrite, skip_if_same_size=skip_same, dry_run=dry_run)
            return sftp.upload_file(local_path, remote_path, overwrite=overwrite, skip_if_same_size=skip_same, dry_run=dry_run, progress_callback=progress_callback)

    def download_path(self, remote_path: str, local_path: str | Path, policy: OverwritePolicy = OverwritePolicy.skip_same_size, dry_run: bool = False, progress_callback=None):
        overwrite, skip_same = policy_to_transfer_flags(policy)
        remote_path = ensure_safe_remote_path(remote_path)
        with self._sftp() as sftp:
            if sftp.is_dir(remote_path):
                return sftp.download_dir(remote_path, Path(local_path), overwrite=overwrite, skip_if_same_size=skip_same, dry_run=dry_run)
            return sftp.download_file(remote_path, Path(local_path), overwrite=overwrite, skip_if_same_size=skip_same, dry_run=dry_run, progress_callback=progress_callback)

    def mkdir_remote(self, remote_dir: str) -> None:
        with self._sftp() as sftp:
            sftp.mkdir_p(ensure_safe_remote_path(remote_dir))

    def delete_remote(self, remote_path: str, recursive: bool = False) -> None:
        remote_path = ensure_safe_remote_path(remote_path)
        self._ensure_deletable(remote_path)
        with self._sftp() as sftp:
            if sftp.is_dir(remote_path):
                if not recursive:
                    raise RemotePathError(f"recursive delete required for directory: {remote_path}")
                sftp.remove_dir(remote_path)
            else:
                sftp.remove_file(remote_path)

    def rename_remote(self, old_path: str, new_path: str) -> None:
        with self._sftp() as sftp:
            sftp.rename(ensure_safe_remote_path(old_path), ensure_safe_remote_path(new_path))

    def preview_remote_text(self, remote_path: str, max_bytes: int = 65536) -> str:
        with self._sftp() as sftp:
            data = sftp.read_file_bytes(ensure_safe_remote_path(remote_path), max_bytes)
        if b"\x00" in data:
            raise ValueError(f"remote file looks binary: {remote_path}")
        return data.decode("utf-8", errors="replace")

    def _ensure_deletable(self, remote_path: str) -> None:
        protected_exact_paths = {"/", "/home", "/root"}
        if remote_path in protected_exact_paths:
            raise RemotePathError(f"refusing to delete protected remote path: {remote_path}")
        if any(_is_path_at_or_under(remote_path, root) for root in self._protected_roots):
            raise RemotePathError(f"refusing to delete protected remote path: {remote_path}")
        if not self._allowed_delete_roots:
            raise RemotePathError("refusing to delete remote path: no allowed delete roots configured")
        if not any(_is_path_at_or_under(remote_path, root) for root in self._allowed_delete_roots):
            raise RemotePathError(f"refusing to delete path outside allowed roots: {remote_path}")

    def _sftp(self):
        return _SFTPContext(self._sftp_factory())


class _SFTPContext:
    def __init__(self, sftp):
        self._sftp = sftp

    def __enter__(self):
        return self._sftp

    def __exit__(self, exc_type, exc, tb):
        if hasattr(self._sftp, "close"):
            self._sftp.close()


def _is_path_at_or_under(path: str, root: str) -> bool:
    if path == root:
        return True
    return root != "/" and path.startswith(root.rstrip("/") + "/")
