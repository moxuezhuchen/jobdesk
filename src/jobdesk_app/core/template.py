"""命令模板渲染模块。

支持的变量:
    {task_id}   - 任务 ID
    {job_dir}   - 远程任务目录
    {input_file} - 输入文件完整路径
    {input_name} - 输入文件名
    {stem}      - 输入文件名不含扩展名
    {batch_id}  - Batch ID
"""

import os
import shlex

_SUPPORTED_VARIABLES = frozenset({
    "task_id",
    "job_dir",
    "input_file",
    "input_name",
    "entry_name",
    "stem",
    "entry_stem",
    "batch_id",
    "shared_dir",
    "shared_dir_abs",
})


def render_command(template: str, variables: dict[str, str]) -> str:
    """渲染命令模板，将 {var} 替换为对应值。

    Args:
        template: 命令模板字符串，如 "g16 {input_name}"。
        variables: 变量字典，键为变量名，值为替换值。

    Returns:
        渲染后的命令字符串。

    Raises:
        ValueError: 如果模板引用了不支持的变量，或缺少必需的变量。
    """
    if "{stem}" in template and "stem" not in variables:
        stem_from = variables.get("input_name", "")
        if stem_from:
            variables = dict(variables)
            variables["stem"] = os.path.splitext(stem_from)[0]

    if "{entry_stem}" in template and "entry_stem" not in variables:
        stem_from = variables.get("entry_name", "") or variables.get("input_name", "")
        if stem_from:
            variables = dict(variables)
            variables["entry_stem"] = os.path.splitext(stem_from)[0]

    if "{entry_name}" in template and "entry_name" not in variables:
        name_from = variables.get("input_name", "")
        if name_from:
            variables = dict(variables)
            variables["entry_name"] = name_from

    result = template
    for var_name in _SUPPORTED_VARIABLES:
        placeholder = "{" + var_name + "}"
        if placeholder in result:
            if var_name not in variables:
                raise ValueError(
                    f"命令模板需要变量 {{{var_name}}}，但未提供该变量的值。"
                    f" 可用变量: {sorted(_SUPPORTED_VARIABLES)}，"
                    f" 已提供: {sorted(variables.keys())}"
                )
            result = result.replace(placeholder, shlex.quote(str(variables[var_name])))

    import re
    remaining = re.findall(r"\{(\w+)\}", result)
    if remaining:
        unknown = [v for v in remaining if v not in _SUPPORTED_VARIABLES]
        if unknown:
            raise ValueError(
                f"命令模板包含不支持的变量: {unknown}。"
                f" 支持的变量: {sorted(_SUPPORTED_VARIABLES)}"
            )

    return result
