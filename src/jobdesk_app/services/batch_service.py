"""批次服务：本地输入发现 + Batch 创建 + Manifest 生成。

不进行 SSH/SFTP，只做本地文件扫描和 Manifest 生成。
支持 task_discoveries 多规则和 mixed-profile batch。
"""

import re
from pathlib import Path
from dataclasses import dataclass

from ..core.manifest import TaskRecord, Manifest
from ..core.models import BatchMeta, TaskPackage, BatchSummary
from ..core.batch import write_batch_json, create_batch as _create_core_batch, read_batch_json
from ..core.template import render_command
from ..core.lifecycle import TaskStatus
from ..core.upload_rules import select_upload_files
from ..core.shared_files import select_shared_files
from ..config.schema import DiscoveryMode, TaskIdFrom
from ..config.runtime import ResolvedExecutionContext
from .project_service import ProjectContext
from .errors import InputDiscoveryError


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _validate_task_id(task_id: str, rule_name: str) -> None:
    if not task_id or not _SAFE_ID_RE.fullmatch(task_id) or ".." in task_id.split("."):
        raise InputDiscoveryError(
            f"Invalid task_id {task_id!r} from discovery rule {rule_name!r}; "
            "use only letters, numbers, dot, underscore, and dash."
        )


@dataclass
class BatchCreateResult:
    """Batch 创建结果。"""

    batch_meta: BatchMeta
    tasks: list[TaskRecord]
    batch_dir: Path
    manifest_path: Path


# ---- 输入发现 -----------------------------------------------------------


def discover_task_packages(ctx: ProjectContext) -> list[TaskPackage]:
    """扫描本地输入目录，按 task_discoveries 规则发现 TaskPackage 列表。

    Args:
        ctx: ProjectContext 实例。

    Returns:
        TaskPackage 列表 (按 discovery rule 顺序 + task_id 稳定排序)。

    Raises:
        InputDiscoveryError: 输入目录不存在、discovery name 重复、
                            task_id 重复、execution_profile 不存在。
    """
    rules = ctx.project_config.task_discoveries
    if not rules:
        return []

    input_dir = ctx.local_input_dir
    if not input_dir.is_dir():
        raise InputDiscoveryError(f"本地输入目录不存在: {input_dir}")

    name_regex = ctx.project_config.name_parser.regex
    name_compiled = re.compile(name_regex) if name_regex else None

    all_packages: list[TaskPackage] = []
    seen_ids: set[str] = set()

    for rule in rules:
        ep_name = rule.execution_profile
        ctx.project_config.get_execution_profile(ep_name)

        rule_packages = _discover_with_rule(ctx, rule, input_dir, name_compiled)

        for pkg in rule_packages:
            # apply task_id_prefix
            prefix = rule.task_id_prefix
            raw_id = pkg.task_id
            pkg.task_id = prefix + raw_id
            _validate_task_id(pkg.task_id, rule.name)
            pkg.execution_profile = ep_name
            pkg.discovery_name = rule.name

            if pkg.task_id in seen_ids:
                raise InputDiscoveryError(
                    f"task_id 重复: {pkg.task_id!r}。"
                    f" 来自 discovery rule: {rule.name!r}。"
                    f" 请使用 task_id_prefix 或确保各 rule 产生唯一 task_id。"
                )
            seen_ids.add(pkg.task_id)

        all_packages.extend(rule_packages)

    return sorted(all_packages, key=lambda p: p.task_id)


def _discover_with_rule(
    ctx: ProjectContext,
    rule,
    input_dir: Path,
    name_compiled: re.Pattern | None,
) -> list[TaskPackage]:
    """对单条 discovery rule 执行发现。"""
    if rule.mode == DiscoveryMode.flat_single:
        return _discover_flat_single(ctx, rule, input_dir, name_compiled)
    elif rule.mode == DiscoveryMode.grouped_by_stem:
        return _discover_grouped_by_stem(ctx, rule, input_dir, name_compiled)
    elif rule.mode == DiscoveryMode.directory:
        return _discover_directory(ctx, rule, input_dir, name_compiled)
    else:
        raise InputDiscoveryError(f"不支持的发现模式: {rule.mode}")


def _discover_flat_single(
    ctx: ProjectContext,
    rule,
    input_dir: Path,
    name_compiled: re.Pattern | None,
) -> list[TaskPackage]:
    packages: list[TaskPackage] = []
    entry_files = sorted(input_dir.glob(rule.entry_glob))
    for f in entry_files:
        if not f.is_file():
            continue
        pid, parsed = _resolve_task_id_and_parsed(f, input_dir, rule, name_compiled, ctx)
        packages.append(TaskPackage(
            task_id=pid,
            entry_file=f,
            files=[f],
            parsed_fields=parsed,
            group_key=_derive_group_key(parsed, ctx),
        ))
    return packages


def _discover_grouped_by_stem(
    ctx: ProjectContext,
    rule,
    input_dir: Path,
    name_compiled: re.Pattern | None,
) -> list[TaskPackage]:
    associated = rule.associated_globs
    if not associated:
        raise InputDiscoveryError("grouped_by_stem 模式需要 associated_globs")

    packages: list[TaskPackage] = []
    entry_files = sorted(input_dir.glob(rule.entry_glob))
    for f in entry_files:
        if not f.is_file():
            continue
        stem = f.stem
        pid, parsed = _resolve_task_id_and_parsed(f, input_dir, rule, name_compiled, ctx)

        all_files = [f]
        for assoc_glob in associated:
            resolved = assoc_glob.replace("{stem}", stem)
            matches = sorted(input_dir.glob(resolved))
            matches = [m for m in matches if m != f and m.is_file()]
            if not matches:
                raise InputDiscoveryError(
                    f"grouped_by_stem: 任务 {pid} 缺少关联文件，"
                    f"pattern '{assoc_glob}' (解析为 '{resolved}') 无匹配"
                )
            if len(matches) > 1:
                raise InputDiscoveryError(
                    f"grouped_by_stem: 任务 {pid} 的关联 pattern '{assoc_glob}'"
                    f" (解析为 '{resolved}') 匹配到多个文件: {[m.name for m in matches]}"
                )
            all_files.extend(matches)

        packages.append(TaskPackage(
            task_id=pid,
            entry_file=f,
            files=sorted(all_files, key=lambda p: p.as_posix()),
            parsed_fields=parsed,
            group_key=_derive_group_key(parsed, ctx),
        ))
    return packages


def _discover_directory(
    ctx: ProjectContext,
    rule,
    input_dir: Path,
    name_compiled: re.Pattern | None,
) -> list[TaskPackage]:
    dir_glob = rule.directory_glob or "*"
    entry_glob = rule.entry_glob
    include_globs = rule.include or ["**/*"]

    task_dirs = sorted(d for d in input_dir.glob(dir_glob) if d.is_dir())

    packages: list[TaskPackage] = []
    for task_dir in task_dirs:
        entry_matches = sorted(f for f in task_dir.glob(entry_glob) if f.is_file())
        if not entry_matches:
            raise InputDiscoveryError(
                f"directory 模式: 目录 {task_dir.name} 缺少 entry file "
                f"(glob '{entry_glob}' 无匹配)"
            )
        if len(entry_matches) > 1:
            raise InputDiscoveryError(
                f"directory 模式: 目录 {task_dir.name} 的 entry_glob '{entry_glob}'"
                f" 匹配到多个文件: {[m.name for m in entry_matches]}"
            )
        entry_file = entry_matches[0]

        all_files: list[Path] = [entry_file]
        seen: set[Path] = {entry_file}
        for inc_glob in include_globs:
            for f in sorted(task_dir.glob(inc_glob)):
                if f.is_file() and f not in seen:
                    all_files.append(f)
                    seen.add(f)

        pid, parsed = _resolve_task_id_and_parsed(
            entry_file, input_dir, rule, name_compiled, ctx, task_dir=task_dir,
        )

        packages.append(TaskPackage(
            task_id=pid,
            task_dir=task_dir,
            entry_file=entry_file,
            files=sorted(all_files, key=lambda p: p.as_posix()),
            parsed_fields=parsed,
            group_key=_derive_group_key(parsed, ctx),
        ))
    return packages


def _resolve_task_id_and_parsed(
    file_path: Path,
    input_dir: Path,
    rule,
    name_compiled: re.Pattern | None,
    ctx: ProjectContext,
    task_dir: Path | None = None,
) -> tuple[str, dict[str, str]]:
    parsed: dict[str, str] = {}
    task_id: str

    if rule.task_id_from == TaskIdFrom.directory_name and task_dir:
        task_id = task_dir.name
    elif rule.task_id_from == TaskIdFrom.stem:
        task_id = file_path.stem
    else:
        task_id = file_path.stem

    rel = file_path.relative_to(input_dir)
    rel_str = rel.as_posix()

    if name_compiled:
        m = name_compiled.search(rel_str)
        if m:
            parsed = m.groupdict()
            if "task_id" in parsed and rule.task_id_from != TaskIdFrom.directory_name:
                task_id = parsed["task_id"]
            for k, v in m.groupdict().items():
                parsed[k] = v

    return task_id, parsed


def _derive_group_key(parsed: dict[str, str], ctx: ProjectContext) -> str | None:
    group_fields = ctx.project_config.group_by
    if not group_fields:
        return None
    parts = []
    for gf in group_fields:
        val = parsed.get(gf, "")
        if not val:
            val = parsed.get("task_id", "")
        parts.append(str(val))
    return "_".join(parts) if parts else None


# ---- Batch 创建 ----------------------------------------------------------


def create_batch(
    ctx: ProjectContext,
    packages: list[TaskPackage],
    resolved_contexts: dict[str, ResolvedExecutionContext],
    batch_id: str | None = None,
) -> BatchCreateResult:
    """从 TaskPackage 列表创建 mixed-profile Batch 和 Manifest。

    Args:
        ctx: ProjectContext 实例。
        packages: TaskPackage 列表（可包含多个 execution_profile）。
        resolved_contexts: execution_profile → ResolvedExecutionContext 映射。
        batch_id: 指定 batch_id。

    Returns:
        BatchCreateResult。
    """
    if not packages:
        raise ValueError("不能创建空的 batch")

    # compute effective max_parallel for BatchMeta (use max across profiles)
    global_max = max(
        (rc.max_parallel for rc in resolved_contexts.values()),
        default=4,
    )

    batch_meta = _create_core_batch(
        project_name=ctx.project_name,
        max_parallel=global_max,
        remote_batch_dir="",  # no single root; each task has own
        task_count=len(packages),
    )
    if batch_id:
        batch_meta.batch_id = batch_id

    batch_dir = ctx.batches_dir / batch_meta.batch_id
    manifest_path = batch_dir / "manifest.tsv"
    batch_meta.manifest_path = str(manifest_path)

    shared_cfg = ctx.project_config.upload.shared_files
    shared_records = select_shared_files(ctx.project_root, shared_cfg)
    if shared_cfg:
        batch_meta.shared_target_subdir = shared_cfg.target_subdir
    batch_meta.shared_files = shared_records

    tasks: list[TaskRecord] = []
    for pkg in packages:
        ep = pkg.execution_profile
        rctx = resolved_contexts.get(ep)
        if rctx is None:
            raise ValueError(
                f"任务 {pkg.task_id} 的 execution_profile={ep!r} 缺少解析后的执行上下文。"
            )

        remote_work_dir = rctx.remote_work_dir
        remote_job_dir = f"{remote_work_dir}/{batch_meta.batch_id}/{pkg.task_id}"

        entry_file = pkg.entry_file
        input_name = entry_file.name if entry_file else ""
        stem = entry_file.stem if entry_file else ""
        entry_path_str = str(entry_file) if entry_file else ""

        upload_cfg = ctx.project_config.upload.task_files
        selected_files, remote_names = select_upload_files(pkg, upload_cfg, ctx.local_input_dir)
        task_files = [str(p) for p in selected_files]
        remote_task_files = remote_names

        command_template = rctx.command_template
        variables = {
            "task_id": pkg.task_id,
            "job_dir": remote_job_dir,
            "input_file": entry_path_str,
            "input_name": input_name,
            "entry_name": input_name,
            "stem": stem,
            "entry_stem": stem,
            "batch_id": batch_meta.batch_id,
            "shared_dir": f"../{batch_meta.shared_target_subdir}",
            "shared_dir_abs": f"{remote_work_dir}/{batch_meta.batch_id}/{batch_meta.shared_target_subdir}",
        }
        rendered = render_command(command_template, variables)

        task = TaskRecord(
            task_id=pkg.task_id,
            batch_id=batch_meta.batch_id,
            group_key=pkg.group_key,
            remote_job_dir=remote_job_dir,
            task_files=task_files,
            remote_task_files=remote_task_files,
            task_dir=str(pkg.task_dir) if pkg.task_dir else None,
            entry_file=entry_path_str,
            parsed_fields=pkg.parsed_fields,
            execution_profile=ep,
            discovery_name=pkg.discovery_name,
            server_id=rctx.server_id,
            remote_work_dir=remote_work_dir,
            max_parallel=rctx.max_parallel,
            rendered_command=rendered,
            status=TaskStatus.local_ready,
        )
        tasks.append(task)

    write_batch_json(batch_meta, batch_dir / "batch.json")
    Manifest.write(manifest_path, tasks)

    return BatchCreateResult(
        batch_meta=batch_meta,
        tasks=tasks,
        batch_dir=batch_dir,
        manifest_path=manifest_path,
    )


# ---- Batch 查询 -----------------------------------------------------------


def list_batches(ctx: ProjectContext) -> list[BatchSummary]:
    """列出项目下所有 batch 摘要。

    Returns:
        BatchSummary 列表，按 batch_id 降序。
    """
    summaries: list[BatchSummary] = []
    bd = ctx.batches_dir
    if not bd.exists():
        return summaries

    for d in sorted(bd.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        bj = d / "batch.json"
        mt = d / "manifest.tsv"
        if not bj.exists() or not mt.exists():
            continue
        try:
            bm = read_batch_json(bj)
            tasks = Manifest.read(mt)
            status_counts: dict[str, int] = {}
            profiles: set[str] = set()
            servers: set[str] = set()
            for t in tasks:
                s = t.status.value
                status_counts[s] = status_counts.get(s, 0) + 1
                if t.execution_profile:
                    profiles.add(t.execution_profile)
                if t.server_id:
                    servers.add(t.server_id)
            summaries.append(BatchSummary(
                batch_id=bm.batch_id,
                created_at=bm.created_at.isoformat() if bm.created_at else "",
                task_count=bm.task_count,
                status_summary=status_counts,
                execution_profiles=sorted(profiles),
                server_ids=sorted(servers),
                shared_files_count=len(bm.shared_files),
            ))
        except Exception:
            continue
    return summaries


def load_batch(ctx: ProjectContext, batch_id: str) -> BatchCreateResult | None:
    """加载已有 batch 的 BatchMeta + Manifest。

    Returns:
        BatchCreateResult，失败返回 None。
    """
    bd = ctx.batches_dir / batch_id
    bj = bd / "batch.json"
    mt = bd / "manifest.tsv"
    if not bj.exists() or not mt.exists():
        return None
    try:
        bm = read_batch_json(bj)
    except Exception as exc:
        raise ValueError(f"Failed to load batch {batch_id!r}: invalid batch.json at {bj}: {exc}") from exc
    try:
        tasks = Manifest.read(mt)
    except Exception as exc:
        raise ValueError(f"Failed to load batch {batch_id!r}: invalid manifest.tsv at {mt}: {exc}") from exc
    return BatchCreateResult(
        batch_meta=bm,
        tasks=tasks,
        batch_dir=bd,
        manifest_path=mt,
    )


def load_latest_batch(ctx: ProjectContext) -> BatchCreateResult | None:
    summaries = list_batches(ctx)
    if not summaries:
        return None
    return load_batch(ctx, summaries[0].batch_id)
