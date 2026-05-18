"""分组汇总模块。

对 ResultRecord 按 group_key 分组，计算组内/全局最佳值和相对值。
不写死字段名或单位。
"""

from dataclasses import dataclass, field
from .models import ResultRecord


@dataclass
class GroupSummaryRecord:
    """一个分组的汇总结果。"""

    group_key: str
    task_count: int = 0
    result_count: int = 0
    best_task_id: str | None = None
    best_result_id: str | None = None
    best_value: float | None = None
    relative_values: dict[str, float] = field(default_factory=dict)
    global_relative: dict[str, float] | None = None


def compute_summary(
    tasks: list,  # list[TaskRecord]
    results: list[ResultRecord],
    field_name: str | None = None,
    minimize: bool = True,
) -> tuple[list[GroupSummaryRecord], dict[str, float] | None]:
    """对结果进行分组汇总。

    Args:
        tasks: TaskRecord 列表，用于获取 group_key。
        results: ResultRecord 列表。
        field_name: 要比较的字段名。若为 None，自动选第一个数值字段。
        minimize: True 表示值越小越好（能量类默认）。

    Returns:
        (group_summaries, global_relative_dict) 元组。
    """
    # 建立 task_id -> group_key 映射
    task_group_map: dict[str, str] = {}
    for t in tasks:
        task_group_map[t.task_id] = t.group_key or "__ungrouped__"

    # 如果没有 field_name，自动选择第一个数值类型可比较字段
    numeric_results = [r for r in results if isinstance(r.value, (int, float)) and not isinstance(r.value, bool)]
    if field_name is None and numeric_results:
        field_name = numeric_results[0].field_name
    elif field_name is None:
        # 没有任何数值结果
        field_name = None

    # 按 group 分组
    groups: dict[str, dict] = {}
    for t in tasks:
        gk = t.group_key or "__ungrouped__"
        if gk not in groups:
            groups[gk] = {"task_count": 0, "results": []}
        groups[gk]["task_count"] += 1

    for r in results:
        gk = r.group_key or "__ungrouped__"
        if gk not in groups:
            groups[gk] = {"task_count": 0, "results": []}
        groups[gk]["results"].append(r)

    # 计算全局最佳
    global_best: float | None = None
    if field_name is not None:
        field_results = [r for r in numeric_results if r.field_name == field_name]
        if field_results:
            global_best = _find_best(field_results, minimize)
        else:
            field_results = numeric_results
            if field_results:
                global_best = _find_best(field_results, minimize)

    # 计算各组汇总
    summaries: list[GroupSummaryRecord] = []
    for gk in sorted(groups.keys()):
        g = groups[gk]
        summary = GroupSummaryRecord(group_key=gk, task_count=g["task_count"])

        if field_name is not None:
            g_field_results = [
                r for r in g["results"]
                if r.field_name == field_name and isinstance(r.value, (int, float)) and not isinstance(r.value, bool)
            ]
            if not g_field_results:
                g_field_results = [
                    r for r in g["results"]
                    if isinstance(r.value, (int, float)) and not isinstance(r.value, bool)
                ]
        else:
            g_field_results = []

        summary.result_count = len(g["results"])

        if g_field_results:
            group_best = _find_best(g_field_results, minimize)
            best_record = next(
                (r for r in g_field_results
                 if isinstance(r.value, (int, float))
                 and abs(float(r.value) - group_best) < 1e-12),
                g_field_results[0],
            )
            summary.best_task_id = best_record.task_id
            summary.best_result_id = best_record.result_id
            summary.best_value = group_best

            # 标记 best for task
            for r in g_field_results:
                if r.result_id == best_record.result_id and r.task_id == best_record.task_id:
                    r.is_best_for_task = True

            # 组内相对值 — 写回 ResultRecord
            rel: dict[str, float] = {}
            for r in g_field_results:
                key = r.result_id or r.task_id
                if isinstance(r.value, (int, float)):
                    diff = float(r.value) - group_best
                    rel[key] = diff
                    r.relative_group = diff
            summary.relative_values = rel

            # 全局相对值 — 写回 ResultRecord
            if global_best is not None:
                global_rel: dict[str, float] = {}
                for r in g_field_results:
                    key = r.result_id or r.task_id
                    if isinstance(r.value, (int, float)):
                        diff = float(r.value) - global_best
                        global_rel[key] = diff
                        r.relative_global = diff
                summary.global_relative = global_rel

        summaries.append(summary)

    # 构建全局相对值字典
    global_relative_dict: dict[str, float] | None = None
    if global_best is not None and field_name is not None:
        global_relative_dict = {}
        for r in numeric_results:
            if r.field_name == field_name and isinstance(r.value, (int, float)):
                key = f"{r.task_id}|{r.result_id or ''}"
                global_relative_dict[key] = float(r.value) - global_best

    return summaries, global_relative_dict


def _find_best(records: list[ResultRecord], minimize: bool = True) -> float:
    """从记录列表中找到最佳值。

    Args:
        records: 包含数值 value 的 ResultRecord 列表。
        minimize: True 找最小值，False 找最大值。

    Returns:
        最佳数值。
    """
    numeric = [float(r.value) for r in records if isinstance(r.value, (int, float)) and not isinstance(r.value, bool)]
    if not numeric:
        return 0.0
    return min(numeric) if minimize else max(numeric)
