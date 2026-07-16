"""Local and remote filesystem operations for the Files page."""

from __future__ import annotations

import posixpath
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from ...services.file_transfer_service import FileTransferService
from ..i18n import tr
from ..worker_utils import WorkerContext
from .file_transfer_helpers import normalize_remote_path, remote_child_path


class FileOperations:
    """Implement mkdir, move, delete, rename, and copy operations."""

    def __init__(
        self,
        *,
        service_provider: Callable[[], FileTransferService | None],
        local_root_provider: Callable[[], Path | None],
        language_provider: Callable[[], str],
        on_status: Callable[[str], None],
        on_error: Callable[[str, str], None],
        on_refresh_local: Callable[[], None],
        on_refresh_remote: Callable[[], None],
        prompt_new_name: Callable[[str, str, str], tuple[str, bool]],
        prompt_new_folder: Callable[[str, str], tuple[str, bool]],
        prompt_text: Callable[[str, str], tuple[str, bool]],
        ask_confirm: Callable[[str, str], bool],
        open_editor: Callable[[Path], None],
        start_worker: Callable[..., Any],
        remote_dir_provider: Callable[[], str],
    ) -> None:
        self._service_provider = service_provider
        self._local_root_provider = local_root_provider
        self._language_provider = language_provider
        self._on_status = on_status
        self._on_error = on_error
        self._on_refresh_local = on_refresh_local
        self._on_refresh_remote = on_refresh_remote
        self._prompt_new_name = prompt_new_name
        self._prompt_new_folder = prompt_new_folder
        self._prompt_text = prompt_text
        self._ask_confirm = ask_confirm
        self._open_editor = open_editor
        self._start_worker = start_worker
        self._remote_dir_provider = remote_dir_provider

    def copy_dropped_local_paths(self, paths: list[str]) -> None:
        local_base = Path(self._local_root_provider() or Path.cwd())
        copied, failures = [], []
        for path_text in paths:
            source = Path(path_text)
            if not source.exists():
                failures.append(f"Source path does not exist: {source}")
                continue
            destination = local_base / source.name
            try:
                if source.resolve() == destination.resolve():
                    failures.append(f"Source is already in this directory: {source.name}")
                elif destination.exists():
                    failures.append(f"Destination already exists: {destination.name}")
                else:
                    shutil.copytree(source, destination) if source.is_dir() else shutil.copy2(source, destination)
                    copied.append(destination)
            except Exception as exc:
                failures.append(f"{source.name}: {exc}")
        if copied:
            self._on_refresh_local()
            self._on_status(f"Copied {len(copied)} local path(s)")
        if failures:
            self._on_error("Drop Copy Error", "\n".join(failures))

    def move_local_paths_into_directory(self, paths: list[str], target_dir_text: str) -> None:
        target_dir = Path(target_dir_text)
        moved, failures = [], []
        if not target_dir.is_dir():
            self._on_error("Move Error", f"Target directory does not exist: {target_dir}")
            return
        target_resolved = target_dir.resolve()
        for path_text in paths:
            source = Path(path_text)
            if not source.exists():
                failures.append(f"Source path does not exist: {source}")
                continue
            destination = target_dir / source.name
            source_resolved = source.resolve()
            try:
                if source_resolved == destination.resolve():
                    failures.append(f"Source is already in this directory: {source.name}")
                elif source.is_dir() and (target_resolved == source_resolved or source_resolved in target_resolved.parents):
                    failures.append(f"Cannot move directory into itself: {source.name}")
                elif destination.exists():
                    failures.append(f"Destination already exists: {destination.name}")
                else:
                    shutil.move(str(source), str(destination))
                    moved.append(destination)
            except Exception as exc:
                failures.append(f"{source.name}: {exc}")
        if moved:
            self._on_refresh_local()
            self._on_status(f"Moved {len(moved)} local path(s)")
        if failures:
            self._on_error("Move Error", "\n".join(failures))

    def move_remote_paths_into_directory(self, paths: list[str], target_dir_text: str) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return
        target_dir = normalize_remote_path(target_dir_text)
        moved, failures = 0, []
        for path_text in paths:
            source = normalize_remote_path(path_text)
            destination = remote_child_path(target_dir, posixpath.basename(source))
            if destination == source:
                failures.append(f"Source is already in this directory: {posixpath.basename(source)}")
            elif target_dir == source or target_dir.startswith(source.rstrip("/") + "/"):
                failures.append(f"Cannot move directory into itself: {posixpath.basename(source)}")
            else:
                try:
                    service.rename_remote(source, destination)
                    moved += 1
                except Exception as exc:
                    failures.append(f"{posixpath.basename(source)}: {exc}")
        if moved:
            self._on_refresh_remote()
            self._on_status(f"Moved {moved} remote path(s)")
        if failures:
            self._on_error("Move Error", "\n".join(failures))

    def mkdir_local(self) -> None:
        language = self._language_provider()
        name, ok = self._prompt_new_folder(tr("New Folder", language), tr("Folder name:", language))
        if not ok or not name.strip():
            return
        validated = self.validate_rename_name(name, self._on_error)
        if validated is None:
            return
        try:
            (Path(self._local_root_provider() or Path.cwd()) / validated).mkdir(parents=True, exist_ok=False)
            self._on_refresh_local()
        except Exception as exc:
            self._on_error("Mkdir Error", str(exc))

    def new_file_local(self) -> None:
        language = self._language_provider()
        name, ok = self._prompt_text(tr("New File", language), tr("File name:", language))
        if not ok or not name.strip():
            return
        validated = self.validate_rename_name(name, self._on_error)
        if validated is None:
            return
        new_file = Path(self._local_root_provider() or Path.cwd()) / validated
        try:
            new_file.touch(exist_ok=False)
            self._on_refresh_local()
            self._open_editor(new_file)
        except Exception as exc:
            self._on_error("New File Error", str(exc))

    def new_file_remote(self) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return
        language = self._language_provider()
        name, ok = self._prompt_text(tr("New File", language), tr("File name:", language))
        if not ok or not name.strip():
            return
        base = self._remote_dir_provider().rstrip("/") or "/"
        remote_file = f"{base}/{name.strip()}" if base != "/" else f"/{name.strip()}"
        handle = tempfile.NamedTemporaryFile(suffix=Path(name).suffix or ".tmp", delete=False)
        handle.close()
        tmp = Path(handle.name)
        try:
            tmp.write_bytes(b"")
            service.upload_path(tmp, remote_file)
            self._on_refresh_remote()
        except Exception as exc:
            self._on_error("New File Error", str(exc))
        finally:
            tmp.unlink(missing_ok=True)

    def mkdir_remote(self) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return
        name, ok = self._prompt_new_folder("New Remote Folder", "Folder name:")
        if not ok or not name.strip():
            return
        base = self._remote_dir_provider().rstrip("/") or "/"
        target = f"{base}/{name.strip()}" if base != "/" else f"/{name.strip()}"
        try:
            service.mkdir_remote(target)
            self._on_refresh_remote()
        except Exception as exc:
            self._on_error("Mkdir Error", str(exc))

    def rename_local(self, local_path: Path) -> None:
        new_name, ok = self._prompt_new_name("Rename Local Path", "New name:", local_path.name)
        if not ok:
            return
        validated = self.validate_rename_name(new_name, self._on_error)
        if validated is None:
            return
        new_path = local_path.with_name(validated)
        if new_path == local_path:
            return
        if new_path.exists():
            self._on_error("Rename Error", f"Destination already exists: {validated}")
            return
        try:
            local_path.rename(new_path)
            self._on_refresh_local()
        except Exception as exc:
            self._on_error("Rename Error", str(exc))

    def rename_remote(self, remote_path: str) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return
        new_name, ok = self._prompt_new_name("Rename Remote Path", "New name:", Path(remote_path).name)
        if not ok:
            return
        validated = self.validate_rename_name(new_name, self._on_error)
        if validated is None:
            return
        parent = remote_path.rsplit("/", 1)[0] or "/"
        new_path = f"{parent}/{validated}" if parent != "/" else f"/{validated}"
        try:
            service.rename_remote(remote_path, new_path)
            self._on_refresh_remote()
        except Exception as exc:
            self._on_error("Rename Error", str(exc))

    def delete_local(self, paths: list[Path]) -> None:
        message = "\n".join(str(path) for path in paths[:10])
        if len(paths) > 10:
            message += f"\n... {len(paths) - 10} more"
        if not self._ask_confirm("Delete Local Path", f"Delete local path(s)?\n{message}"):
            return
        def _run(_ctx: WorkerContext) -> int:
            for path in paths:
                shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
            return len(paths)
        def _on_result(count: Any) -> None:
            self._on_status(f"Deleted {count} local item(s)")
            self._on_refresh_local()
        self._start_worker(_run, _on_result, lambda error: self._on_error("Delete Local Error", error))  # type: ignore[call-arg]

    def delete_remote(self, remote_paths: list[str], current_dir: str) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return
        current_dir = current_dir.rstrip("/") or "/"
        if current_dir in {"/", "/root", "/home"}:
            self._on_error("Delete Error", f"Cannot delete items at top-level directory: {current_dir}")
            return
        valid = [p for p in remote_paths if p != current_dir and p.startswith(current_dir + "/")]
        if not valid:
            self._on_error("Delete Error", "Selected path(s) cannot be deleted from this location")
            return
        message = "\n".join(valid[:10]) + (f"\n... {len(valid) - 10} more" if len(valid) > 10 else "")
        if not self._ask_confirm("Delete Remote Path", f"Delete remote path(s)?\n{message}"):
            return
        def _run(_ctx: WorkerContext) -> int:
            for path in valid:
                service.delete_remote(path, recursive=True, extra_allowed_roots=[current_dir])
            return len(valid)
        def _on_result(count: Any) -> None:
            self._on_status(f"Deleted {count} remote item(s)")
            self._on_refresh_remote()
        self._start_worker(_run, _on_result, lambda error: self._on_error("Delete Error", error))  # type: ignore[call-arg]

    @staticmethod
    def validate_rename_name(name: str, error_cb: Callable[[str, str], None]) -> str | None:
        name = name.strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            error_cb("Invalid Name", "Name cannot contain path separators, '.' or '..'")
            return None
        return name
