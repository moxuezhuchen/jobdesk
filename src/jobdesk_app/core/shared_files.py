"""共享文件选择规则 — batch-level shared files。

纯核心逻辑，不涉及 SSH/SFTP/GUI。
"""

from pathlib import Path

from .models import SharedFileRecord
from ..config.schema import SharedFilesUploadConfig, MissingUploadPatternPolicy


def select_shared_files(
    project_root: Path,
    shared_config: SharedFilesUploadConfig | None,
) -> list[SharedFileRecord]:
    """从项目根选择 batch-level 共享文件。

    Args:
        project_root: 项目根目录。
        shared_config: 共享文件上传配置，None 表示不选择共享文件。

    Returns:
        SharedFileRecord 列表，按 relative_path 稳定排序。

    Raises:
        ValueError: on_missing=error 且 include 零匹配；
                    base_dir 不存在或不是目录；
                    remote_name 冲突或包含 ..
    """
    if shared_config is None or not shared_config.include:
        return []

    base_dir = (project_root / shared_config.base_dir).resolve()
    project_root = project_root.resolve()
    try:
        base_dir.relative_to(project_root)
    except ValueError as e:
        raise ValueError(
            f"shared_files.base_dir must stay inside project root: {base_dir}"
        ) from e
    if not base_dir.exists():
        raise ValueError(
            f"shared_files.base_dir 不存在: {base_dir}\n"
            f"  (项目根: {project_root})"
        )
    if not base_dir.is_dir():
        raise ValueError(
            f"shared_files.base_dir 不是目录: {base_dir}"
        )

    include_patterns = shared_config.include
    exclude_patterns = shared_config.exclude
    on_missing = shared_config.on_missing

    # ---- include ----
    selected: dict[Path, Path] = {}  # abs_path -> (abs_path)
    for pat in include_patterns:
        for f in sorted(base_dir.glob(pat)):
            if f.is_file() and f not in selected:
                selected[f] = f

    if not selected:
        msg = (
            f"shared_files.include 的所有 pattern 均未匹配任何文件。\n"
            f"  base_dir: {base_dir}\n"
            f"  include patterns: {include_patterns}"
        )
        if on_missing == MissingUploadPatternPolicy.error:
            raise ValueError(msg)
        elif on_missing == MissingUploadPatternPolicy.warn:
            import warnings
            warnings.warn(msg, UserWarning)
        return []
    # on_missing=ignore → selected stays empty, return []

    # ---- exclude ----
    if exclude_patterns:
        excluded: set[Path] = set()
        for pat in exclude_patterns:
            for f in base_dir.glob(pat):
                if f in selected:
                    excluded.add(f)
        for f in excluded:
            del selected[f]

    if not selected:
        return []

    # ---- build records ----
    records: list[SharedFileRecord] = []
    remote_seen: set[str] = set()

    for abs_path in sorted(selected, key=lambda p: p.as_posix()):
        try:
            rel = abs_path.relative_to(base_dir)
        except ValueError:
            rel = abs_path
        rel_str = rel.as_posix()

        # validate remote_name
        if not rel_str:
            raise ValueError(f"共享文件路径为空: {abs_path}")
        if ".." in rel_str.split("/"):
            raise ValueError(f"共享文件 remote_name 不能包含 '..': {rel_str}")

        if rel_str in remote_seen:
            raise ValueError(f"共享文件 remote_name 冲突: {rel_str}")
        remote_seen.add(rel_str)

        records.append(SharedFileRecord(
            local_path=str(abs_path),
            relative_path=rel_str,
            remote_name=rel_str,
        ))

    return records
