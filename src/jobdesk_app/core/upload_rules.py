"""上传文件选择规则 — 纯核心逻辑，不涉及 SSH/SFTP/GUI。"""

import fnmatch
import re
from pathlib import Path

from .models import TaskPackage
from ..config.schema import TaskFilesUploadConfig, MissingUploadPatternPolicy

_SUPPORTED_VARIABLES = frozenset({"task_id", "entry_name", "entry_stem", "stem", "input_file", "input_name"})


def select_upload_files(
    package: TaskPackage,
    upload_config: TaskFilesUploadConfig | None,
    input_dir: Path,
) -> tuple[list[Path], list[str]]:
    """从 TaskPackage.files 中选择需要上传的文件。

    Args:
        package: 任务包。
        upload_config: 上传选择规则，None 表示选择全部文件。
        input_dir: 本地输入目录（用于 directory 模式下构造相对路径匹配基准）。

    Returns:
        (实际上传文件列表, 对应远程文件名列表)，均为稳定排序。

    Raises:
        ValueError: on_missing=error 时 include pattern 零匹配；
                    require_entry_file=True 但 entry_file 未被选中；
                    模板变量名不合法。
    """
    if upload_config is None or (not upload_config.include and not upload_config.exclude):
        # no rules → select all
        files = sorted(package.files, key=_sort_key)
        return files, [f.name for f in files]

    entry_file = package.entry_file
    all_files = list(package.files)
    patterns = upload_config.include
    exclude_patterns = upload_config.exclude
    on_missing = upload_config.on_missing
    require_entry = upload_config.require_entry_file

    # resolve template variables
    tv = _template_vars(package)

    resolved_include = [_resolve_pattern(p, tv) for p in patterns]
    resolved_exclude = [_resolve_pattern(p, tv) for p in exclude_patterns]

    # determine match base per file
    task_dir = package.task_dir

    # ---- include step ----
    if patterns:
        selected: list[Path] = []
        matched_any = False
        for f in sorted(all_files, key=_sort_key):
            if _matches_any(f, resolved_include, task_dir, input_dir):
                selected.append(f)
                matched_any = True
        if not matched_any:
            msg = (
                f"任务 {package.task_id}: upload.task_files.include 的所有 pattern "
                f"均未匹配任何文件。\n"
                f"  include patterns: {patterns}\n"
                f"  entry_file: {entry_file}\n"
                f"  available files: {[f.name for f in all_files]}"
            )
            if on_missing == MissingUploadPatternPolicy.error:
                raise ValueError(msg)
            elif on_missing == MissingUploadPatternPolicy.warn:
                import warnings
                warnings.warn(msg, UserWarning)
            # ignore: silent continue; selected stays empty
    else:
        selected = list(all_files)

    # ---- exclude step ----
    if exclude_patterns:
        selected = [f for f in selected if not _matches_any(f, resolved_exclude, task_dir, input_dir)]

    # ---- require_entry_file ----
    if require_entry and entry_file and entry_file not in selected:
        raise ValueError(
            f"任务 {package.task_id}: require_entry_file=True，"
            f"但 entry_file 未进入最终上传列表。\n"
            f"  entry_file: {entry_file}\n"
            f"  include patterns: {patterns}\n"
            f"  exclude patterns: {exclude_patterns}\n"
            f"  final selected files: {[f.name for f in selected]}"
        )

    files = sorted(selected, key=_sort_key)
    return files, [f.name for f in files]


def _sort_key(p: Path) -> str:
    return p.as_posix()


def _template_vars(package: TaskPackage) -> dict[str, str]:
    entry = package.entry_file
    return {
        "task_id": package.task_id,
        "entry_name": entry.name if entry else "",
        "entry_stem": entry.stem if entry else "",
        "stem": entry.stem if entry else "",
        "input_file": str(entry) if entry else "",
        "input_name": entry.name if entry else "",
    }


def _resolve_pattern(pattern: str, tv: dict[str, str]) -> str:
    result = pattern
    for name in _SUPPORTED_VARIABLES:
        result = result.replace("{" + name + "}", tv.get(name, ""))
    # check for unknown variables
    for m in re.finditer(r"\{(\w+)\}", result):
        unknown = m.group(1)
        if unknown not in _SUPPORTED_VARIABLES:
            raise ValueError(
                f"上传 pattern 包含不支持的模板变量: '{{{unknown}}}'。"
                f" 支持的变量: {sorted(_SUPPORTED_VARIABLES)}"
            )
    return result


def _matches_any(
    file_path: Path,
    patterns: list[str],
    task_dir: Path | None,
    input_dir: Path,
) -> bool:
    """检查 file_path 是否匹配任一 pattern。

    directory 模式: pattern 相对 task_dir 匹配。
    其他模式: pattern 相对 input_dir 匹配。
    """
    if task_dir:
        # directory mode — match relative to task_dir
        try:
            rel = file_path.relative_to(task_dir)
        except ValueError:
            rel = file_path
        match_name = rel.as_posix()
    else:
        # flat/grouped mode — match relative to input_dir
        try:
            rel = file_path.relative_to(input_dir)
        except ValueError:
            rel = file_path
        match_name = rel.as_posix()

    # also try matching just the filename (simple pattern like "*.xyz")
    for pat in patterns:
        if fnmatch.fnmatch(match_name, pat) or fnmatch.fnmatch(file_path.name, pat):
            return True
    return False
