"""SFTP 文件传输模块。

基于 paramiko SFTPClient 的上传/下载，支持 dry-run、增量跳过、覆盖保护。
远程路径按 POSIX 处理，本地路径使用 pathlib.Path。
"""

import posixpath
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.transfer import TransferDirection, TransferRecord, TransferStatus
from .errors import RemotePathError


@dataclass
class _RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size_bytes: int | None
    modified_at: float | None
    permissions: str


def _validate_remote_path(remote_path: str) -> str:
    """校验远程路径必须为 POSIX 格式，拒绝反斜杠。"""
    if "\\" in remote_path:
        raise RemotePathError(f"远程路径不能包含反斜杠，请使用 POSIX 正斜杠: {remote_path!r}")
    return remote_path


class SFTPClientWrapper:
    """基于 paramiko SFTPClient 的文件传输封装。

    支持从 SSHClientWrapper 打开 SFTP channel，也支持直接注入 mock client 方便测试。

    用法:
        with SSHClientWrapper(server) as ssh:
            sftp = SFTPClientWrapper.from_ssh(ssh)
            sftp.upload_file(Path("local.txt"), "/remote/path/remote.txt")
    """

    def __init__(self, sftp_client: Any):
        self._sftp = sftp_client

    @classmethod
    def from_ssh(cls, ssh_client: Any) -> "SFTPClientWrapper":
        """从 SSHClientWrapper 打开 SFTP channel。"""
        sftp = ssh_client._client.open_sftp()
        return cls(sftp)

    # -- 基础查询 ----------------------------------------------------------

    def exists(self, remote_path: str) -> bool:
        """检查远程路径是否存在。"""
        _validate_remote_path(remote_path)
        try:
            self._sftp.stat(remote_path)
            return True
        except (FileNotFoundError, OSError):
            return False

    def stat(self, remote_path: str) -> Any | None:
        """获取远程路径的 stat 信息，不存在时返回 None。"""
        _validate_remote_path(remote_path)
        try:
            return self._sftp.stat(remote_path)
        except (FileNotFoundError, OSError):
            return None

    def mkdir_p(self, remote_dir: str) -> None:
        """递归创建远程目录。"""
        _validate_remote_path(remote_dir)
        if not remote_dir or remote_dir == "/":
            return
        parts = remote_dir.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else f"/{part}"
            try:
                self._sftp.stat(current)
            except (FileNotFoundError, OSError):
                self._sftp.mkdir(current)

    def list_dir_info(self, remote_dir: str) -> list:
        """列出远程目录内容，返回包含名称/大小/时间/权限的条目列表。"""
        import stat as stat_mod
        _validate_remote_path(remote_dir)
        try:
            attrs_list = self._sftp.listdir_attr(remote_dir)
        except (FileNotFoundError, OSError):
            return []
        entries = []
        for attr in sorted(attrs_list, key=lambda a: (not stat_mod.S_ISDIR(a.st_mode or 0), (a.filename or "").lower())):
            is_dir = stat_mod.S_ISDIR(attr.st_mode or 0)
            name = attr.filename
            path = posixpath.join(remote_dir, name) if remote_dir != "/" else f"/{name}"
            perm_str = stat_mod.filemode(attr.st_mode) if attr.st_mode else ""
            entries.append(_RemoteEntry(
                name=name,
                path=path,
                is_dir=is_dir,
                size_bytes=attr.st_size if not is_dir else None,
                modified_at=attr.st_mtime,
                permissions=perm_str,
            ))
        return entries

    def rename(self, old_path: str, new_path: str) -> None:
        """重命名远程文件或目录。"""
        _validate_remote_path(old_path)
        _validate_remote_path(new_path)
        self._sftp.rename(old_path, new_path)

    def remove_file(self, remote_path: str) -> None:
        """删除远程文件。"""
        _validate_remote_path(remote_path)
        self._sftp.remove(remote_path)

    def remove_dir(self, remote_dir: str, _depth: int = 0) -> None:
        """递归删除远程目录。"""
        _validate_remote_path(remote_dir)
        if _depth > 50:
            raise RemotePathError(f"remove_dir exceeded max depth (50): {remote_dir}")
        for name in self._sftp.listdir(remote_dir):
            full = posixpath.join(remote_dir, name)
            if self.is_dir(full):
                self.remove_dir(full, _depth + 1)
            else:
                self._sftp.remove(full)
        self._sftp.rmdir(remote_dir)

    def read_file_bytes(self, remote_path: str, max_bytes: int = 65536) -> bytes:
        """读取远程文件前 max_bytes 字节。"""
        _validate_remote_path(remote_path)
        with self._sftp.open(remote_path, "rb") as f:
            return f.read(max_bytes)

    def is_dir(self, remote_path: str) -> bool:
        """检查远程路径是否为目录。"""
        st = self.stat(remote_path)
        if st is None:
            return False
        import stat
        return stat.S_ISDIR(st.st_mode)

    # -- 单文件上传 ---------------------------------------------------------

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
        progress_callback=None,
    ) -> TransferRecord:
        """上传单个文件到远程。

        Args:
            local_path: 本地文件路径。
            remote_path: 远程目标路径（POSIX）。
            overwrite: 目标存在且大小不同时是否覆盖。
            skip_if_same_size: 目标存在且大小相同时跳过。
            dry_run: 仅记录 planned，不实际传输。

        Returns:
            TransferRecord。
        """
        _validate_remote_path(remote_path)
        local_size = local_path.stat().st_size if local_path.exists() else None

        rec = TransferRecord(
            direction=TransferDirection.upload,
            local_path=str(local_path),
            remote_path=remote_path,
            size_bytes=local_size,
            status=TransferStatus.planned,
            dry_run=dry_run,
        )

        if not local_path.is_file():
            rec.status = TransferStatus.failed
            rec.reason = f"本地文件不存在: {local_path}"
            return rec

        remote_st = self.stat(remote_path)

        if dry_run:
            if remote_st is not None:
                remote_size = remote_st.st_size
                if skip_if_same_size and remote_size == local_size:
                    rec.status = TransferStatus.planned
                    rec.reason = "dry-run: 目标已存在且大小相同，将跳过"
                elif not overwrite:
                    rec.status = TransferStatus.planned
                    rec.reason = "dry-run: 目标已存在且大小不同，overwrite=False，将失败"
                else:
                    rec.status = TransferStatus.planned
                    rec.reason = "dry-run: 将覆盖上传"
            else:
                rec.status = TransferStatus.planned
                rec.reason = "dry-run: 将上传"
            return rec

        # 实际执行
        if remote_st is not None:
            remote_size = remote_st.st_size
            if skip_if_same_size and remote_size == local_size:
                rec.status = TransferStatus.skipped
                rec.reason = f"跳过: 远程文件已存在且大小相同 ({local_size} bytes)"
                return rec
            if not overwrite:
                rec.status = TransferStatus.failed
                rec.reason = (
                    f"远程文件已存在但大小不同 (local={local_size}, remote={remote_size})"
                    f"，overwrite=False"
                )
                return rec

        remote_dir = posixpath.dirname(remote_path)
        if remote_dir and remote_dir != "/":
            self.mkdir_p(remote_dir)

        # Normalize CRLF→LF for text files via streaming chunked read/write.
        if _is_text_file(local_path):
            _upload_text_normalized(self._sftp, local_path, remote_path, local_size, progress_callback)
        else:
            self._sftp.put(str(local_path), remote_path, confirm=True, callback=progress_callback)
        rec.status = TransferStatus.transferred
        rec.reason = "上传成功"
        return rec

    # -- 单文件下载 ---------------------------------------------------------

    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
        progress_callback=None,
    ) -> TransferRecord:
        """从远程下载单个文件到本地。

        Args:
            remote_path: 远程文件路径（POSIX）。
            local_path: 本地目标路径。
            overwrite: 本地存在且大小不同时是否覆盖。
            skip_if_same_size: 本地存在且大小相同时跳过。
            dry_run: 仅记录 planned，不实际传输。

        Returns:
            TransferRecord。
        """
        _validate_remote_path(remote_path)
        remote_st = self.stat(remote_path)

        rec = TransferRecord(
            direction=TransferDirection.download,
            local_path=str(local_path),
            remote_path=remote_path,
            size_bytes=remote_st.st_size if remote_st else None,
            status=TransferStatus.planned,
            dry_run=dry_run,
        )

        if remote_st is None:
            rec.status = TransferStatus.failed
            rec.reason = f"远程文件不存在: {remote_path}"
            return rec

        remote_size = remote_st.st_size
        rec.size_bytes = remote_size

        if dry_run:
            if local_path.is_file():
                local_size = local_path.stat().st_size
                if skip_if_same_size and local_size == remote_size:
                    rec.status = TransferStatus.planned
                    rec.reason = "dry-run: 本地已存在且大小相同，将跳过"
                elif not overwrite:
                    rec.status = TransferStatus.planned
                    rec.reason = "dry-run: 本地已存在且大小不同，overwrite=False，将失败"
                else:
                    rec.status = TransferStatus.planned
                    rec.reason = "dry-run: 将覆盖下载"
            else:
                rec.status = TransferStatus.planned
                rec.reason = "dry-run: 将下载"
            return rec

        # 实际执行
        if local_path.is_file():
            local_size = local_path.stat().st_size
            if skip_if_same_size and local_size == remote_size:
                rec.status = TransferStatus.skipped
                rec.reason = f"跳过: 本地文件已存在且大小相同 ({remote_size} bytes)"
                return rec
            if not overwrite:
                rec.status = TransferStatus.failed
                rec.reason = (
                    f"本地文件已存在但大小不同 (local={local_size}, remote={remote_size})"
                    f"，overwrite=False"
                )
                return rec

        local_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=local_path.parent,
                prefix=f".{local_path.name}.",
                suffix=".download",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
            self._sftp.get(remote_path, str(temp_path), callback=progress_callback)
            temp_path.replace(local_path)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        rec.status = TransferStatus.transferred
        rec.reason = "下载成功"
        return rec

    # -- 批量 ----------------------------------------------------------------

    def upload_many(
        self,
        files: list[tuple[Path, str]],
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """批量上传文件。

        Args:
            files: (local_path, remote_path) 元组列表。
            overwrite, skip_if_same_size, dry_run: 同 upload_file。

        Returns:
            TransferRecord 列表。
        """
        records: list[TransferRecord] = []
        for local_path, remote_path in files:
            rec = self.upload_file(
                local_path, remote_path,
                overwrite=overwrite,
                skip_if_same_size=skip_if_same_size,
                dry_run=dry_run,
            )
            records.append(rec)
        return records

    def download_many(
        self,
        files: list[tuple[str, Path]],
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """批量下载文件。

        Args:
            files: (remote_path, local_path) 元组列表。
            overwrite, skip_if_same_size, dry_run: 同 download_file。

        Returns:
            TransferRecord 列表。
        """
        records: list[TransferRecord] = []
        for remote_path, local_path in files:
            rec = self.download_file(
                remote_path, local_path,
                overwrite=overwrite,
                skip_if_same_size=skip_if_same_size,
                dry_run=dry_run,
            )
            records.append(rec)
        return records

    # -- 目录传输 ------------------------------------------------------------

    def upload_dir(
        self,
        local_dir: Path,
        remote_base: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """递归上传本地目录到远程。

        保持目录结构：local_dir/file.txt → remote_base/file.txt

        Args:
            local_dir: 本地目录路径。
            remote_base: 远程目标根目录。
            include_globs: 只上传匹配 glob 的文件（None = 全部）。
            exclude_globs: 排除匹配 glob 的文件。
            overwrite, skip_if_same_size, dry_run: 同 upload_file。

        Returns:
            TransferRecord 列表。
        """
        _validate_remote_path(remote_base)
        records: list[TransferRecord] = []
        if not local_dir.is_dir():
            return records

        for f in sorted(local_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(local_dir)
            if not _matches_globs(rel.as_posix(), include_globs, exclude_globs):
                continue
            remote_path = posixpath.join(remote_base, rel.as_posix())
            rec = self.upload_file(
                f, remote_path,
                overwrite=overwrite,
                skip_if_same_size=skip_if_same_size,
                dry_run=dry_run,
            )
            records.append(rec)
        return records

    def download_dir(
        self,
        remote_dir: str,
        local_base: Path,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """递归下载远程目录到本地。

        保持目录结构：remote_dir/file.txt → local_base/file.txt

        Args:
            remote_dir: 远程目录路径。
            local_base: 本地目标根目录。
            include_globs: 只下载匹配 glob 的文件（None = 全部）。
            exclude_globs: 排除匹配 glob 的文件。
            overwrite, skip_if_same_size, dry_run: 同 download_file。

        Returns:
            TransferRecord 列表。
        """
        _validate_remote_path(remote_dir)
        records: list[TransferRecord] = []
        if not self.is_dir(remote_dir):
            return records

        def _walk(rdir: str, rel_prefix: str, depth: int = 0):
            import stat as stat_mod
            if depth > 50:
                raise RemotePathError(f"download_dir exceeded max depth (50): {rdir}")
            for attr in sorted(self._sftp.listdir_attr(rdir), key=lambda a: a.filename or ""):
                name = attr.filename
                full = posixpath.join(rdir, name)
                rel = posixpath.join(rel_prefix, name) if rel_prefix else name
                if stat_mod.S_ISLNK(attr.st_mode or 0):
                    continue  # skip symlinks to avoid traversal loops
                if stat_mod.S_ISDIR(attr.st_mode or 0):
                    _walk(full, rel, depth + 1)
                else:
                    if not _matches_globs(rel, include_globs, exclude_globs):
                        continue
                    local_path = local_base / rel
                    rec = self.download_file(
                        full, local_path,
                        overwrite=overwrite,
                        skip_if_same_size=skip_if_same_size,
                        dry_run=dry_run,
                    )
                    records.append(rec)

        _walk(remote_dir, "")
        return records

    # -- 清理 ---------------------------------------------------------------

    def close(self) -> None:
        """关闭 SFTP channel。"""
        if self._sftp:
            self._sftp.close()
            self._sftp = None


_TEXT_EXTENSIONS = {
    ".txt", ".sh", ".bash", ".py", ".gjf", ".com", ".inp", ".yaml", ".yml",
    ".json", ".xml", ".csv", ".tsv", ".log", ".md", ".rst", ".cfg", ".conf",
    ".toml", ".ini", ".env", ".cif", ".xyz", ".mol", ".pdb", ".smi",
}

_CRLF_CHUNK_SIZE = 256 * 1024  # 256KB chunks for streaming normalization


def _upload_text_normalized(sftp, local_path: Path, remote_path: str, total_size: int | None, progress_callback) -> None:
    """Stream-upload a text file, normalizing CRLF/CR→LF in chunks."""
    bytes_written = 0
    with sftp.open(remote_path, "wb") as remote_f:
        with open(local_path, "rb") as local_f:
            carry_cr = False
            while True:
                chunk = local_f.read(_CRLF_CHUNK_SIZE)
                if not chunk:
                    # Flush trailing CR from previous chunk
                    if carry_cr:
                        remote_f.write(b"\n")
                        bytes_written += 1
                    break
                # If previous chunk ended with \r, check if this starts with \n
                if carry_cr:
                    if chunk[0:1] == b"\n":
                        chunk = chunk[1:]  # skip \n, the \r\n pair becomes \n below
                    # Emit the pending \r as \n
                    remote_f.write(b"\n")
                    bytes_written += 1
                # Check if chunk ends with \r (might be split \r\n)
                carry_cr = chunk[-1:] == b"\r"
                if carry_cr:
                    chunk = chunk[:-1]
                # Normalize \r\n → \n, then remaining \r → \n
                normalized = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                remote_f.write(normalized)
                bytes_written += len(normalized)
    if progress_callback:
        progress_callback(bytes_written, bytes_written)


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _matches_globs(
    rel_path: str,
    includes: list[str] | None,
    excludes: list[str] | None,
) -> bool:
    """检查相对路径是否匹配 include/exclude glob 规则。

    使用 fnmatch 进行 glob 匹配。include 为空时默认包含全部。
    exclude 优先于 include。
    对于不含路径分隔符的 pattern（如 *.log），同时匹配 basename。
    """
    import fnmatch
    from posixpath import basename

    name = basename(rel_path)

    def _match(path, pat):
        if "/" not in pat and "\\" not in pat:
            return fnmatch.fnmatch(name, pat)
        return fnmatch.fnmatch(path, pat)

    if excludes:
        for pat in excludes:
            if _match(rel_path, pat):
                return False

    if includes:
        for pat in includes:
            if _match(rel_path, pat):
                return True
        return False

    return True
