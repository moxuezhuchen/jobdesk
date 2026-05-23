"""结果提取引擎。

从本地结果文件中根据 ExtractResult 规则提取数值或字符串结果。
不依赖 SSH、远程连接，仅操作本地文件。
"""

import re
from pathlib import Path

from ..config.schema import ExtractResult, ExtractStrategy, ExtractType
from .models import FailureRecord, ResultRecord


def analyze_tasks(
    extract_rules: list[ExtractResult],
    tasks: list,  # list[TaskRecord]
    results_base_dir: Path | str,
    batch_id: str,
) -> tuple[list[ResultRecord], list[FailureRecord]]:
    """对一批已下载任务执行结果提取。

    Args:
        extract_rules: ExtractResult 规则列表。
        tasks: TaskRecord 列表。
        results_base_dir: 本地结果根目录。
        batch_id: 当前 Batch ID。

    Returns:
        (results, failures) 元组。
    """
    results: list[ResultRecord] = []
    failures: list[FailureRecord] = []

    if not extract_rules:
        return results, failures

    results_base = Path(results_base_dir)

    for task in tasks:
        task_result_dir = results_base / batch_id / task.task_id
        task_results, task_failures = _analyze_one_task(
            task_id=task.task_id,
            group_key=task.group_key,
            result_dir=task_result_dir,
            extract_rules=extract_rules,
            batch_id=batch_id,
        )
        results.extend(task_results)
        failures.extend(task_failures)

    return results, failures


def analyze_one_task(
    task_id: str,
    group_key: str | None,
    result_dir: Path | str,
    extract_rules: list[ExtractResult],
    batch_id: str,
) -> tuple[list[ResultRecord], list[FailureRecord]]:
    """对单个任务执行结果提取（公开函数，便于单任务测试）。"""
    return _analyze_one_task(task_id, group_key, Path(result_dir), extract_rules, batch_id)


def _analyze_one_task(
    task_id: str,
    group_key: str | None,
    result_dir: Path,
    extract_rules: list[ExtractResult],
    batch_id: str,
) -> tuple[list[ResultRecord], list[FailureRecord]]:
    results: list[ResultRecord] = []
    failures: list[FailureRecord] = []

    for rule in extract_rules:
        rule_results, rule_failures = _extract_field(
            task_id=task_id,
            group_key=group_key,
            result_dir=result_dir,
            rule=rule,
            batch_id=batch_id,
        )
        results.extend(rule_results)
        failures.extend(rule_failures)

    return results, failures


def _extract_field(
    task_id: str,
    group_key: str | None,
    result_dir: Path,
    rule: ExtractResult,
    batch_id: str,
) -> tuple[list[ResultRecord], list[FailureRecord]]:
    results: list[ResultRecord] = []
    failures: list[FailureRecord] = []

    pattern = f"**/{rule.source_glob}" if "/" not in rule.source_glob else rule.source_glob
    source_files = sorted(result_dir.glob(pattern))

    if not source_files:
        failures.append(FailureRecord(
            task_id=task_id,
            batch_id=batch_id,
            stage="analysis",
            reason=f"未找到匹配 {rule.source_glob} 的源文件",
            source_file=None,
            context=f"搜索目录: {result_dir}",
        ))
        return results, failures

    try:
        compiled_re = re.compile(rule.regex)
    except re.error as e:
        failures.append(FailureRecord(
            task_id=task_id,
            batch_id=batch_id,
            stage="analysis",
            reason=f"正则表达式无效: {e}",
            source_file=None,
        ))
        return results, failures

    has_any_value_group = "value" in compiled_re.groupindex
    all_matches: list[tuple[Path, re.Match]] = []

    for sf in source_files:
        try:
            content = sf.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            failures.append(FailureRecord(
                task_id=task_id,
                batch_id=batch_id,
                stage="analysis",
                reason=f"无法读取文件: {e}",
                source_file=_rel_path(sf, result_dir),
            ))
            continue

        for m in compiled_re.finditer(content):
            all_matches.append((sf, m))

    if not all_matches:
        failures.append(FailureRecord(
            task_id=task_id,
            batch_id=batch_id,
            stage="analysis",
            reason=f"正则无匹配: {rule.regex}",
            source_file=_rel_path(source_files[0], result_dir) if source_files else None,
            context=f"匹配文件数: {len(source_files)}",
        ))
        return results, failures

    # 根据 strategy 筛选
    if rule.strategy == ExtractStrategy.first:
        selected = [all_matches[0]]
    elif rule.strategy == ExtractStrategy.last:
        selected = [all_matches[-1]]
    elif rule.strategy == ExtractStrategy.all:
        selected = all_matches
    else:
        selected = [all_matches[0]]

    for idx, (sf, m) in enumerate(selected):
        if has_any_value_group:
            raw_value = m.group("value")
        else:
            raw_value = m.group(0)

        try:
            typed_value, value_type = _convert_value(raw_value, rule.type)
        except ValueError as e:
            result_id = f"{rule.name}_{idx}" if len(selected) > 1 else rule.name
            failures.append(FailureRecord(
                task_id=task_id,
                batch_id=batch_id,
                stage="analysis",
                reason=f"类型转换失败: {e}",
                source_file=_rel_path(sf, result_dir),
                context=f"raw_value={raw_value!r}, expected_type={rule.type.value}",
            ))
            continue

        result_id = f"{rule.name}_{idx}" if len(selected) > 1 else rule.name
        results.append(ResultRecord(
            task_id=task_id,
            batch_id=batch_id,
            group_key=group_key,
            result_id=result_id,
            source_file=_rel_path(sf, result_dir),
            field_name=rule.name,
            value=typed_value,
            value_type=value_type,
            unit=rule.unit,
        ))

    return results, failures


def _convert_value(raw: str, target_type: ExtractType) -> tuple[float | int | str, str]:
    raw = raw.strip()
    if target_type == ExtractType.float:
        return float(raw), "float"
    elif target_type == ExtractType.int:
        return int(raw), "int"
    elif target_type == ExtractType.str:
        return raw, "str"
    else:
        return raw, "str"


def _rel_path(file_path: Path, base_dir: Path) -> str:
    """返回 file_path 相对于 base_dir 的路径字符串，统一使用正斜杠。"""
    try:
        rel = file_path.relative_to(base_dir)
    except ValueError:
        rel = Path(file_path.name)
    return rel.as_posix()
