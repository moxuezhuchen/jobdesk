"""Qt-free path and immutable payload helpers for the Runs results page."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

MAX_PREVIEW_FILE_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RunArtifactPaths:
    """Resolved, run-owned locations used by preview and downloads."""

    run_id: str
    workspace: Path
    download_dir: Path
    search_dirs: tuple[Path, ...]
    bound_workspace: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", str(self.run_id))
        object.__setattr__(self, "workspace", Path(self.workspace))
        object.__setattr__(self, "download_dir", Path(self.download_dir))
        object.__setattr__(self, "search_dirs", tuple(Path(directory) for directory in self.search_dirs))


@dataclass(frozen=True, slots=True)
class UncertainTaskPayload:
    """Immutable task projection used by preview tables and detail lookup."""

    task_id: str
    status: str
    error_message: str = ""
    remote_task_files: tuple[str, ...] = ()
    task_dir: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", str(self.task_id))
        object.__setattr__(self, "status", str(self.status))
        object.__setattr__(self, "error_message", str(self.error_message or ""))
        object.__setattr__(self, "remote_task_files", tuple(str(path) for path in self.remote_task_files))
        if self.task_dir is not None:
            object.__setattr__(self, "task_dir", Path(self.task_dir))

    @classmethod
    def from_task(cls, task: Any, *, task_dir: Path | str | None = None) -> "UncertainTaskPayload":
        """Copy only the fields needed by the page from a legacy task object."""
        if isinstance(task, cls):
            if task_dir is None:
                return task
            return cls(
                task_id=task.task_id,
                status=task.status,
                error_message=task.error_message,
                remote_task_files=task.remote_task_files,
                task_dir=Path(task_dir),
            )
        if isinstance(task, Mapping):
            task_id = task.get("task_id", "")
            status = task.get("status", "")
            error = task.get("error_message", "")
            remote_files = task.get("remote_task_files", ()) or ()
            source_task_dir = task.get("task_dir")
        else:
            task_id = getattr(task, "task_id", "")
            status = getattr(task, "status", "")
            error = getattr(task, "error_message", "")
            remote_files = getattr(task, "remote_task_files", ()) or ()
            source_task_dir = getattr(task, "task_dir", None)
        status_value = getattr(status, "value", status)
        resolved_status = str(status_value)
        selected_task_dir = task_dir if task_dir is not None else source_task_dir
        resolved_task_dir: Path | None
        if isinstance(selected_task_dir, (str, os.PathLike)):
            resolved_task_dir = Path(selected_task_dir)
        else:
            resolved_task_dir = None
        return cls(
            task_id=task_id,
            status=resolved_status,
            error_message=error,
            remote_task_files=remote_files,
            task_dir=resolved_task_dir,
        )


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    """Immutable input accepted by the background preview builder.

    This is deliberately a data-only boundary.  In particular, it must never
    contain a ``RunRecord``, Qt object, page instance, callback, or mutable
    mapping.  The GUI creates one snapshot while it owns the selected record;
    :func:`build_preview_payload` can then run without touching the GUI.
    """

    run_id: str
    result_dirs: tuple[Path, ...] = ()
    download_dir: Path = Path(".")
    workflow_kind: str = ""
    progress_dir: Path | None = None
    tasks: tuple[UncertainTaskPayload, ...] = ()
    uncertain: bool = False
    auto_analysis_label: str = "Result Preview - Auto Analysis"
    local_files_label: str = "Result Preview - Local Files"
    tsv_label: str = "Result Preview"
    file_too_large_label: str = "File too large for preview"
    parse_error_label: str = "Parse Error"
    ok_label: str = "OK"

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", str(self.run_id))
        object.__setattr__(self, "result_dirs", tuple(Path(directory) for directory in self.result_dirs))
        object.__setattr__(self, "download_dir", Path(self.download_dir))
        if self.workflow_kind is not None:
            kind = getattr(self.workflow_kind, "value", self.workflow_kind)
            object.__setattr__(self, "workflow_kind", str(kind))
        if self.progress_dir is not None:
            object.__setattr__(self, "progress_dir", Path(self.progress_dir))
        object.__setattr__(
            self,
            "tasks",
            tuple(UncertainTaskPayload.from_task(task) for task in self.tasks),
        )
        object.__setattr__(self, "uncertain", bool(self.uncertain))
        for field in (
            "auto_analysis_label",
            "local_files_label",
            "tsv_label",
            "file_too_large_label",
            "parse_error_label",
            "ok_label",
        ):
            object.__setattr__(self, field, str(getattr(self, field)))

    @property
    def search_dirs(self) -> tuple[Path, ...]:
        """Compatibility alias for callers that use artifact terminology."""
        return self.result_dirs


@dataclass(frozen=True, slots=True)
class PreviewPayload:
    """Frozen preview data allowed to cross from a worker to the GUI."""

    kind: str
    run_id: str | None = None
    result_dir: Path | None = None
    artifact_path: Path | None = None
    rows: tuple[tuple[str, ...], ...] = ()
    label: str = ""
    stale: bool = False
    tasks: tuple[UncertainTaskPayload, ...] = ()
    workspace: Path | None = None
    progress_dir: Path | None = None
    workflow_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", str(self.run_id))
        if self.result_dir is not None:
            object.__setattr__(self, "result_dir", Path(self.result_dir))
        if self.artifact_path is not None:
            object.__setattr__(self, "artifact_path", Path(self.artifact_path))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", Path(self.workspace))
        if self.progress_dir is not None:
            object.__setattr__(self, "progress_dir", Path(self.progress_dir))
        object.__setattr__(self, "rows", _freeze_rows(self.rows))
        object.__setattr__(
            self,
            "label",
            str(self.label),
        )
        object.__setattr__(self, "stale", bool(self.stale))
        object.__setattr__(self, "tasks", tuple(UncertainTaskPayload.from_task(task) for task in self.tasks))
        if self.workflow_kind is not None:
            kind = getattr(self.workflow_kind, "value", self.workflow_kind)
            object.__setattr__(self, "workflow_kind", str(kind))

    @classmethod
    def from_legacy(cls, payload: Any) -> "PreviewPayload":
        """Convert the historical tuple payload without retaining its record."""
        if isinstance(payload, cls):
            return payload
        kind = str(payload[0]) if payload else "empty"
        if kind == "uncertain":
            return cls(kind=kind, tasks=tuple(payload[1] or ()))
        if kind == "confflow":
            record = payload[1]
            return cls(
                kind=kind,
                run_id=str(getattr(record, "run_id", "")),
                result_dir=Path(payload[2]),
            )
        if kind == "analysis":
            return cls(
                kind=kind,
                rows=payload[1],
                label=str(payload[2]),
                stale=bool(payload[3]) if len(payload) > 3 else False,
            )
        if kind == "tsv":
            return cls(kind=kind, artifact_path=Path(payload[1]), label=str(payload[2]))
        return cls(kind="empty")


@dataclass(frozen=True, slots=True)
class ComparePayload:
    """Frozen comparison headers and display-ready cell values."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", tuple(str(header) for header in self.headers))
        object.__setattr__(self, "rows", _freeze_rows(self.rows))

    @classmethod
    def from_comparison(cls, comparison: Any) -> "ComparePayload":
        if isinstance(comparison, cls):
            return comparison
        headers = tuple(str(header) for header in getattr(comparison, "field_names", ()) or ())
        rows = tuple(
            tuple(str(row.get(header, "")) for header in headers) for row in (getattr(comparison, "rows", ()) or ())
        )
        return cls(headers=headers, rows=rows)


def _freeze_rows(rows: Iterable[Iterable[Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(str(value) for value in row) for row in rows)


def has_workspace_binding(local_dir: object) -> bool:
    return isinstance(local_dir, str) and bool(local_dir)


def resolve_result_workspace(local_dir: object, fallback_workspace: Path) -> Path:
    if isinstance(local_dir, (str, os.PathLike)) and local_dir:
        return Path(local_dir)
    return Path(fallback_workspace)


def resolve_run_artifacts(
    run_id: str,
    local_dir: object,
    workspace: Path,
    *,
    default_local_folder: str | os.PathLike[str] | None = None,
    candidate_roots: Iterable[Path] | None = None,
) -> RunArtifactPaths:
    """Resolve bound/legacy result locations with deterministic precedence."""
    anchor = resolve_result_workspace(local_dir, workspace)
    bound = has_workspace_binding(local_dir)
    download_dir = anchor / "results" / str(run_id)
    if bound:
        return RunArtifactPaths(str(run_id), anchor, download_dir, (download_dir,), True)

    roots: list[Path] = []
    raw_roots = tuple(candidate_roots or ())
    if not raw_roots:
        raw_roots = (anchor,)
        if default_local_folder:
            raw_roots += (Path(default_local_folder),)
        gui_workspace = Path(workspace)
        if gui_workspace not in raw_roots:
            raw_roots += (gui_workspace,)
    for root in raw_roots:
        path = Path(root)
        if path not in roots:
            roots.append(path)
    run_owned = [root / "results" / str(run_id) for root in roots]
    search_dirs = run_owned + [root for root in roots if root not in run_owned]
    return RunArtifactPaths(str(run_id), anchor, anchor / "results" / str(run_id), tuple(search_dirs), False)


def choose_existing_artifact(
    search_dirs: Iterable[Path],
    names: Iterable[str],
    *,
    minimum_bytes: int = 0,
) -> Path | None:
    """Choose the first existing regular file in search/name order."""
    for directory in search_dirs:
        for name in names:
            candidate = Path(directory) / str(name)
            try:
                if candidate.is_file() and candidate.stat().st_size > minimum_bytes:
                    return candidate
            except OSError:
                continue
    return None


def is_preview_too_large(path: Path, *, max_bytes: int = MAX_PREVIEW_FILE_BYTES) -> bool:
    try:
        return Path(path).stat().st_size > max_bytes
    except OSError:
        return False


def build_preview_payload(request: PreviewRequest) -> PreviewPayload:
    """Build a preview payload from an immutable, Qt-free request.

    The function intentionally has no page/record dependency.  It only reads
    the paths and task projections carried by ``request`` and returns frozen
    display data.  The GUI may apply the resulting payload later, but no Qt
    object is needed while files are inspected or parsed here.
    """
    if not isinstance(request, PreviewRequest):
        raise TypeError("build_preview_payload expects PreviewRequest")

    workflow_kind = request.workflow_kind or None
    if request.uncertain:
        return PreviewPayload(kind="uncertain", tasks=request.tasks, workflow_kind=workflow_kind)

    if request.workflow_kind in {"confflow", "dag"}:
        best_dir: Path | None = None
        fallback_dir: Path | None = None
        for result_dir in request.result_dirs:
            if not result_dir.exists():
                continue
            if _confflow_result_dir_has_summary(result_dir, request.tasks):
                best_dir = result_dir
                break
            if fallback_dir is None:
                fallback_dir = result_dir
        return PreviewPayload(
            kind="confflow",
            run_id=request.run_id,
            result_dir=best_dir or fallback_dir or request.download_dir,
            tasks=request.tasks,
            progress_dir=request.progress_dir,
            workflow_kind=workflow_kind,
        )

    for result_dir in request.result_dirs:
        if not result_dir.exists():
            continue
        rows = _build_auto_analysis(result_dir, request)
        if rows:
            return PreviewPayload(
                kind="analysis",
                rows=rows,
                label=request.auto_analysis_label,
                tasks=_preview_task_snapshots(request.tasks, result_dir, rows),
                workspace=result_dir,
                workflow_kind=workflow_kind,
            )

    for result_dir in request.result_dirs:
        rows = _build_workspace_analysis(result_dir, request)
        if rows:
            return PreviewPayload(
                kind="analysis",
                rows=rows,
                label=request.local_files_label,
                tasks=_preview_task_snapshots(request.tasks, result_dir, rows),
                workspace=result_dir,
                workflow_kind=workflow_kind,
            )

    tsv = choose_existing_artifact(
        request.result_dirs,
        ("final_results.tsv", "analysis_preview.tsv"),
        minimum_bytes=30,
    )
    if tsv is not None:
        return PreviewPayload(
            kind="tsv",
            artifact_path=tsv,
            label=f"{request.tsv_label} - {tsv.name}",
            workflow_kind=workflow_kind,
        )
    return PreviewPayload(kind="empty", workflow_kind=workflow_kind)


def _build_auto_analysis(result_dir: Path, request: PreviewRequest) -> tuple[tuple[str, ...], ...]:
    """Parse task-directory outputs without a GUI-owned cache."""
    from ..core.parsers.gaussian import diagnose_gaussian_result, parse_gaussian_log
    from ..core.parsers.orca import diagnose_orca_result, parse_orca_out

    try:
        directories = sorted(directory for directory in result_dir.iterdir() if directory.is_dir())
    except OSError:
        return ()
    if not directories:
        directories = [result_dir]

    rows: list[list[str]] = []
    for task_dir in directories:
        stem = task_dir.name
        log_file = task_dir / f"{stem}.log"
        if log_file.is_file():
            rows.append(
                _parse_preview_file(
                    log_file,
                    stem,
                    "Gaussian",
                    parse_gaussian_log,
                    diagnose_gaussian_result,
                    request,
                )
            )
        out_file = task_dir / f"{stem}.out"
        if out_file.is_file():
            rows.append(
                _parse_preview_file(
                    out_file,
                    stem,
                    "ORCA",
                    parse_orca_out,
                    diagnose_orca_result,
                    request,
                )
            )
    return _freeze_rows(rows)


def _build_workspace_analysis(result_dir: Path, request: PreviewRequest) -> tuple[tuple[str, ...], ...]:
    """Parse legacy flat workspace outputs using frozen task metadata."""
    from ..core.parsers.gaussian import diagnose_gaussian_result, parse_gaussian_log
    from ..core.parsers.orca import diagnose_orca_result, parse_orca_out

    rows: list[list[str]] = []
    for task in request.tasks:
        if task.status not in {"downloaded", "analyzed"} or not task.remote_task_files:
            continue
        stem = PurePosixPath(task.remote_task_files[0]).stem
        log_file = Path(result_dir) / f"{stem}.log"
        if log_file.is_file():
            rows.append(
                _parse_preview_file(
                    log_file,
                    task.task_id,
                    "Gaussian",
                    parse_gaussian_log,
                    diagnose_gaussian_result,
                    request,
                )
            )
        out_file = Path(result_dir) / f"{stem}.out"
        if out_file.is_file():
            rows.append(
                _parse_preview_file(
                    out_file,
                    task.task_id,
                    "ORCA",
                    parse_orca_out,
                    diagnose_orca_result,
                    request,
                )
            )
    return _freeze_rows(rows)


def _parse_preview_file(
    path: Path,
    task_id: str,
    program: str,
    parser,
    diagnoser,
    request: PreviewRequest,
) -> list[str]:
    if is_preview_too_large(path):
        return _placeholder_preview_row(task_id, path.name, program, request.file_too_large_label)
    try:
        result = parser(path)
        return _analysis_preview_row(task_id, path.name, program, result, diagnoser(result), request.ok_label)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Failed to parse preview file: %s", path)
        return _placeholder_preview_row(task_id, path.name, program, request.parse_error_label)


def _analysis_preview_row(task_id: str, file_name: str, program: str, result, diagnosis, ok_label: str) -> list[str]:
    energy = f"{result.final_energy_au:.6f}" if result.final_energy_au else ""
    gibbs = f"{result.gibbs_au:.6f}" if result.gibbs_au else ""
    zpe = f"{result.zpe_au:.6f}" if result.zpe_au else ""
    imag = str(result.imaginary_freq_count)
    return [task_id, file_name, program, energy, gibbs, zpe, imag, diagnosis or ok_label]


def _placeholder_preview_row(task_id: str, file_name: str, program: str, diagnosis: str) -> list[str]:
    return [task_id, file_name, program, diagnosis, "", "", "", ""]


def _preview_task_snapshots(
    tasks: tuple[UncertainTaskPayload, ...],
    result_dir: Path,
    rows: Iterable[Iterable[Any]],
) -> tuple[UncertainTaskPayload, ...]:
    by_id = {task.task_id: task for task in tasks}
    snapshots: list[UncertainTaskPayload] = []
    for row in rows:
        values = tuple(row)
        task_id = str(values[0]) if values else ""
        source = by_id.get(task_id) or UncertainTaskPayload(task_id=task_id, status="downloaded")
        snapshots.append(UncertainTaskPayload.from_task(source, task_dir=Path(result_dir) / task_id))
    return tuple(snapshots)


def _confflow_result_dir_has_summary(
    result_dir: Path,
    tasks: tuple[UncertainTaskPayload, ...],
) -> bool:
    from ..core.confflow_contract import RUN_SUMMARY_FILE, WORK_DIR_SUFFIX

    for task in tasks:
        if task.status not in {"downloaded", "analyzed"}:
            continue
        candidates = (
            result_dir / f"{task.task_id}{WORK_DIR_SUFFIX}" / RUN_SUMMARY_FILE,
            result_dir / task.task_id / f"{task.task_id}{WORK_DIR_SUFFIX}" / RUN_SUMMARY_FILE,
        )
        if any(candidate.exists() for candidate in candidates):
            return True
    return False


__all__ = [
    "PreviewRequest",
    "ComparePayload",
    "MAX_PREVIEW_FILE_BYTES",
    "PreviewPayload",
    "RunArtifactPaths",
    "UncertainTaskPayload",
    "choose_existing_artifact",
    "build_preview_payload",
    "has_workspace_binding",
    "is_preview_too_large",
    "resolve_result_workspace",
    "resolve_run_artifacts",
]
