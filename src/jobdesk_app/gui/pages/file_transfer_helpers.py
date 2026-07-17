"""Pure helper functions for the Files page (no Qt widget state).

Kept importable from ``file_transfer_page`` for backward compatibility.
"""

import hashlib
import posixpath
import tempfile
from datetime import datetime
from pathlib import Path

from ...core.manifest import Manifest
from ...core.run import RunMode, RunSource, RunSpec, build_run_plan
from ...core.submit_payload import InputSource
from ...core.transfer import TransferStatus
from ..i18n import tr


def format_file_size(size: int | None) -> str:
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_remote_size(size: int | None, is_dir: bool) -> str:
    if is_dir:
        return ""
    return format_file_size(size)


def format_modified_time(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def format_transfer_speed(bytes_per_second: float) -> str:
    """Format a transfer speed for the progress label."""
    if bytes_per_second >= 1024 * 1024:
        return f"{bytes_per_second / 1024 / 1024:.1f} MB/s"
    if bytes_per_second >= 1024:
        return f"{bytes_per_second / 1024:.0f} KB/s"
    return f"{bytes_per_second:.0f} B/s"


def table_resize_mode_name() -> str:
    return "Interactive"


def format_queue_summary(statuses: list[TransferStatus], language: str = "en") -> str:
    transferred = sum(1 for s in statuses if s == TransferStatus.transferred)
    skipped = sum(1 for s in statuses if s == TransferStatus.skipped)
    failed = sum(1 for s in statuses if s == TransferStatus.failed)
    return tr(
        "Queue {transferred} ok | {skipped} skip | {failed} fail",
        language,
        transferred=transferred,
        skipped=skipped,
        failed=failed,
    )


def build_file_button_reasons(local_selected: bool, remote_selected: bool, connected: bool) -> dict[str, str]:
    return {
        "upload": "" if local_selected else "Select a local file or folder",
        "download": ""
        if connected and remote_selected
        else ("Connect to a server first" if not connected else "Select a remote file or folder"),
        "preview": ""
        if connected and remote_selected
        else ("Connect to a server first" if not connected else "Select a remote file"),
    }


def collect_remote_delete_roots(tasks_or_manifest) -> list[str]:
    if tasks_or_manifest is None:
        return []
    if isinstance(tasks_or_manifest, (str, Path)):
        manifest_path = Path(tasks_or_manifest)
        if not manifest_path.exists():
            return []
        tasks = Manifest.read(manifest_path)
    else:
        tasks = list(tasks_or_manifest)
    roots: set[str] = set()
    for task in tasks:
        if task.remote_work_dir and task.batch_id:
            roots.add(f"{task.remote_work_dir.rstrip('/')}/.jobdesk_runs/{task.batch_id}")
    return sorted(roots)


def run_button_reason(connected: bool, selected_count: int, command_template: str) -> str:
    if not connected:
        return "Connect to a server first"
    if selected_count <= 0:
        return "Select remote files or directories"
    if not command_template.strip():
        return "Enter a command template"
    return ""


def format_command_preview_rows(
    remote_paths: list[str],
    remote_dirs: list[str],
    remote_dir: str,
    command_template: str,
    run_mode: str,
    max_preview: int = 10,
) -> list[str]:
    mode = RunMode(run_mode)
    sources = [RunSource(path=p, is_dir=False) for p in remote_paths]
    sources.extend(RunSource(path=p, is_dir=True) for p in remote_dirs)
    plan = build_run_plan(
        RunSpec(
            server_id="preview",
            remote_dir=remote_dir,
            command_template=command_template,
            max_parallel=1,
            mode=mode,
            sources=sources,
        ),
        run_id="preview",
    )
    return [f"{task.task_id}: {task.command}" for task in plan.tasks[:max_preview]]


def choose_chunks_to_submit(chunks: list, submit_mode: str) -> list:
    if submit_mode == "create_only":
        return []
    if submit_mode == "first_batch":
        return chunks[:1]
    return chunks


def choose_confflow_xyz(local_files: list[str], remote_files: list[str]) -> tuple[str, list[str], str]:
    """Select XYZ files for ConfFlow batch from exactly one pane.

    Returns (origin, xyz_paths, error). error is non-empty when selection is invalid.
    """
    local_xyz = [p for p in local_files if Path(p).suffix.lower() == ".xyz"]
    remote_xyz = [p for p in remote_files if posixpath.splitext(p)[1].lower() == ".xyz"]
    if local_xyz and remote_xyz:
        return "", [], "Ambiguous: .xyz selected in both local and remote panes"
    if local_xyz:
        return "local", local_xyz, ""
    if remote_xyz:
        return "remote", remote_xyz, ""
    return "", [], "No .xyz files selected"


def choose_confflow_yaml(remote_files: list[str], xyz_origin: str) -> tuple[str, str]:
    """Find a single remote YAML for ConfFlow. Returns (yaml_path, error)."""
    remote_yamls = [p for p in remote_files if posixpath.splitext(p)[1].lower() in {".yaml", ".yml"}]
    if not remote_yamls:
        return "", ""
    if xyz_origin == "local":
        return "", "Local XYZ batch cannot use a remote YAML; choose a local YAML instead"
    if len(remote_yamls) > 1:
        return "", "Select only one remote YAML configuration file"
    return remote_yamls[0], ""


def choose_delete_scope(local_count: int, remote_count: int, focused_pane: str) -> str:
    if focused_pane == "local" and local_count > 0:
        return "local"
    if focused_pane == "remote" and remote_count > 0:
        return "remote"
    if remote_count > 0:
        return "remote"
    if local_count > 0:
        return "local"
    return ""


def default_remote_dir_for_server(server) -> str:
    username = (getattr(server, "username", "") or "").strip()
    if username == "root":
        return "/root"
    if username:
        return f"/home/{username}"
    return "/"


def remote_child_path(remote_dir: str, name: str) -> str:
    base = normalize_remote_path(remote_dir)
    child = name.strip("/")
    if not child:
        return base
    return normalize_remote_path(posixpath.join(base, child))


def remote_parent_path(remote_dir: str) -> str:
    path = normalize_remote_path(remote_dir)
    if path == "/":
        return "/"
    parent = posixpath.dirname(path.rstrip("/"))
    return parent or "/"


def local_parent_row(local_dir: str | Path) -> list[str] | None:
    path = Path(local_dir).resolve()
    parent = path.parent
    if parent == path:
        return None
    return local_table_row("..", True, "", str(parent))


def remote_parent_row(remote_dir: str) -> list[str] | None:
    path = normalize_remote_path(remote_dir)
    if path == "/":
        return None
    return remote_table_row("..", True, "", "", "", remote_parent_path(path))


def normalize_remote_path(remote_dir: str) -> str:
    path = (remote_dir or "/").replace("\\", "/").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = posixpath.normpath(path)
    return "/" if normalized == "." else normalized


def breadcrumb_parts(remote_dir: str) -> list[tuple[str, str]]:
    path = normalize_remote_path(remote_dir)
    parts = [("/", "/")]
    if path == "/":
        return parts
    current = ""
    for part in path.strip("/").split("/"):
        current = f"{current}/{part}" if current else f"/{part}"
        parts.append((part, current))
    return parts


def connection_status_text(server_id: str | None, connected: bool, error: str = "", language: str = "en") -> str:
    if error:
        return f"Connection failed: {error}"
    if not server_id:
        return tr("No server selected", language)
    key = "Connected: {server_id}" if connected else "Connecting: {server_id}"
    return tr(key, language, server_id=server_id)


def file_action_labels() -> dict[str, str]:
    return {
        "up": "Up",
        "home": "Home",
        "refresh_local": "Refresh Local",
        "refresh_remote": "Refresh Remote",
        "upload": "Upload ->",
        "download": "<- Download",
        "mkdir": "New Folder",
        "rename": "Rename",
        "delete": "Delete",
        "preview": "Preview",
    }


def file_table_headers(kind: str) -> list[str]:
    if kind == "remote":
        return ["name", "size", "modified", "permissions"]
    return ["name", "size", "modified"]


def files_layout_row_counts() -> dict[str, int]:
    return {
        "top_toolbar_rows": 1,
        "action_rows": 1,
        "run_rows": 3,
    }


def local_table_row(name: str, is_dir: bool, size: str, path: str, modified: str = "") -> list[str]:
    return [name, size, modified, "dir" if is_dir else "file", path]


def remote_table_row(name: str, is_dir: bool, size: str, modified: str, permissions: str, path: str) -> list[str]:
    return [name, size, modified, permissions, "dir" if is_dir else "file", path]


def format_selection_summary(local_count: int, remote_count: int, language: str = "en") -> str:
    return tr(
        "Local {local_count} | Remote {remote_count}",
        language,
        local_count=local_count,
        remote_count=remote_count,
    )


def build_local_rows(base: Path, hide_dot: bool) -> tuple[dict[str, float], list[list[str]], str | None]:
    """Scan a local directory and return (mtime_snapshot, table_rows, error)."""
    snapshot: dict[str, float] = {}
    rows = []
    parent = local_parent_row(base)
    if parent is not None:
        rows.append(parent)
    try:
        children = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower(), p.name))
    except (PermissionError, OSError):
        return snapshot, rows, f"No permission to access: {base}"
    for child in children:
        if hide_dot and child.name.startswith("."):
            continue
        try:
            st = child.stat()
            snapshot[str(child)] = st.st_mtime_ns if hasattr(st, "st_mtime_ns") else st.st_mtime
            is_dir = child.is_dir()
            size = "" if is_dir else format_file_size(st.st_size)
            mtime = format_modified_time(st.st_mtime)
        except (PermissionError, OSError):
            continue
        rows.append(local_table_row(child.name, is_dir, size, str(child), mtime))
    return snapshot, rows, None


def build_input_sources(paths: list[str], *, side: str) -> list[InputSource]:
    """Wrap ``paths`` as :class:`InputSource` instances.

    ``kind`` is inferred from the file suffix (``.gjf`` → ``"gjf"``,
    ``.inp`` → ``"inp"``, otherwise ``"xyz"``).  Unknown suffixes are
    treated as ``"xyz"`` so the Submit page's kind filter still routes
    them sensibly.
    """
    suffix_map = {".gjf": "gjf", ".inp": "inp"}
    sources: list[InputSource] = []
    for raw in paths:
        p = Path(raw)
        kind = suffix_map.get(p.suffix.lower(), "xyz")
        sources.append(InputSource(path=p, side=side, kind=kind))  # type: ignore[arg-type]
    return sources


def _file_signature(path: Path) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return "missing"
    return hashlib.sha256(data).hexdigest()


def _remote_edit_temp_path(remote_path: str, server_id: str | None) -> Path:
    name = Path(remote_path).name or "remote-file"
    key = f"{server_id or ''}\0{remote_path}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "jobdesk_remote_edit" / digest / name


def _remote_list_error_allows_fallback(error: str) -> bool:
    first_line = (error.splitlines()[0] if error else "").lower()
    return (
        "filenotfounderror" in first_line
        or "errno 2" in first_line
        or "errno 20" in first_line
        or "no such file" in first_line
        or "no such directory" in first_line
        or "not a directory" in first_line
    )


def _raise_if_upload_failed(records, remote_path: str) -> None:
    items = records if isinstance(records, list) else [records]
    for item in items:
        if getattr(item, "status", None) == TransferStatus.failed:
            reason = getattr(item, "reason", "") or "upload failed"
            raise RuntimeError(f"upload failed for {remote_path}: {reason}")
