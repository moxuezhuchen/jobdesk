"""JobDesk 配置数据模型。

使用 Pydantic v2 进行配置校验。
不包含任何计算程序专用字段。
"""

from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator


class AuthMethod(str, Enum):
    key = "key"
    password = "password"


class DiscoveryMode(str, Enum):
    flat_single = "flat_single"
    grouped_by_stem = "grouped_by_stem"
    directory = "directory"


class TaskIdFrom(str, Enum):
    stem = "stem"
    directory_name = "directory_name"


class ExtractStrategy(str, Enum):
    first = "first"
    last = "last"
    all = "all"


class ExtractType(str, Enum):
    float = "float"
    int = "int"
    str = "str"


class OverwritePolicy(str, Enum):
    deny_cross_batch = "deny_cross_batch"


# ---- 服务器配置 ----------------------------------------------------------

class ServerConfig(BaseModel):
    """单台服务器的连接配置。

    注意：server_id 在 servers.yaml 中作为 key 出现，
    不在值内部重复声明。加载时由 ServersConfig 自动注入。
    """

    server_id: str = Field(default="", description="服务器唯一标识（自动从 key 注入）")
    display_name: str = Field(default="", description="显示名称")
    host: str = Field(..., description="服务器主机地址")
    port: int = Field(default=22, ge=1, le=65535, description="SSH 端口")
    username: str = Field(..., description="登录用户名")
    auth_method: AuthMethod = Field(default=AuthMethod.key, description="认证方式")
    key_path: str | None = Field(default=None, description="SSH 私钥路径")
    default_shell: str = Field(default="bash", description="默认 shell")


class ServersConfig(BaseModel):
    """servers.yaml 的顶层结构。"""

    servers: dict[str, ServerConfig] = Field(
        default_factory=dict,
        description="server_id -> ServerConfig 映射",
    )

    @field_validator("servers", mode="before")
    @classmethod
    def inject_server_ids(cls, v: dict) -> dict:
        """在创建 ServerConfig 之前，将 key 注入为 server_id。"""
        if isinstance(v, dict):
            result = {}
            for key, value in v.items():
                if isinstance(value, dict):
                    value = {**value, "server_id": key}
                result[key] = value
            return result
        return v


# ---- 运行时绑定 ----------------------------------------------------------

class RuntimeBinding(BaseModel):
    """单个 execution_profile 的运行时绑定。"""

    server_id: str = Field(..., min_length=1, description="目标服务器 ID")
    remote_work_dir: str = Field(..., description="远程工作目录")
    max_parallel: int | None = Field(default=None, ge=1, description="覆盖 profile 默认并行数")


class RuntimeBindingsConfig(BaseModel):
    """runtime_bindings.yaml 的顶层结构。"""

    bindings: dict[str, dict[str, RuntimeBinding]] = Field(
        default_factory=dict,
        description="project_id -> {execution_profile_name -> RuntimeBinding}",
    )


# ---- 项目配置内嵌模型 ------------------------------------------------------

class ProjectMeta(BaseModel):
    name: str = Field(..., min_length=1, description="项目名称")
    description: str | None = Field(default=None, description="项目描述")


class LocalPaths(BaseModel):
    input_dir: str = Field(..., description="本地输入目录")
    result_dir: str = Field(default="./results", description="本地结果目录")


class TaskDiscoveryRule(BaseModel):
    name: str = Field(..., min_length=1, description="该发现规则的唯一标识")
    mode: DiscoveryMode = Field(default=DiscoveryMode.flat_single, description="发现模式")
    task_id_prefix: str = Field(default="", description="任务 ID 前缀")
    entry_glob: str = Field(default="*", description="入口文件 glob")
    task_id_from: TaskIdFrom = Field(default=TaskIdFrom.stem, description="task_id 来源")
    execution_profile: str = Field(default="default", description="该发现规则产生任务使用的 execution_profile")
    associated_globs: list[str] = Field(default_factory=list, description="grouped_by_stem 关联文件 glob")
    directory_glob: str | None = Field(default=None, description="directory 模式的任务目录 glob")
    include: list[str] = Field(default_factory=list, description="directory 模式的额外包含 glob")

    @field_validator("entry_glob")
    @classmethod
    def entry_glob_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("entry_glob 不能为空")
        return v


class NameParser(BaseModel):
    regex: str | None = Field(default=None, description="任务命名解析正则")


class ExecutionProfile(BaseModel):
    """项目需要的运行环境描述。"""

    label: str = Field(..., min_length=1, description="显示标签")
    command: str = Field(..., min_length=1, description="单任务命令模板")
    requirements: dict[str, list[str]] = Field(
        default_factory=lambda: {"tags": ["cpu"]},
        description="运行环境要求",
    )
    defaults: dict[str, int] = Field(
        default_factory=lambda: {"max_parallel": 4},
        description="默认参数",
    )

    @property
    def max_parallel(self) -> int:
        return self.defaults.get("max_parallel", 4)


class SubmitConfig(BaseModel):
    shell: str = Field(default="bash", description="远程 shell")


class MissingUploadPatternPolicy(str, Enum):
    error = "error"
    warn = "warn"
    ignore = "ignore"


class TaskFilesUploadConfig(BaseModel):
    """上传文件选择规则。"""

    include: list[str] = Field(default_factory=list, description="包含文件的 glob 模式")
    exclude: list[str] = Field(default_factory=list, description="排除文件的 glob 模式")
    require_entry_file: bool = Field(default=True, description="是否要求 entry_file 必须被选中")
    on_missing: MissingUploadPatternPolicy = Field(
        default=MissingUploadPatternPolicy.error,
        description="include pattern 零匹配时的策略",
    )

    @field_validator("include")
    @classmethod
    def _validate_include_patterns(cls, v: list[str]) -> list[str]:
        _validate_template_variables(v)
        return v

    @field_validator("exclude")
    @classmethod
    def _validate_exclude_patterns(cls, v: list[str]) -> list[str]:
        _validate_template_variables(v)
        return v


def _validate_template_variables(patterns: list[str]) -> None:
    """检查模板变量名是否合法。"""
    import re
    _SUPPORTED = frozenset({"task_id", "entry_name", "entry_stem", "stem", "input_file", "input_name"})
    for pat in patterns:
        for m in re.finditer(r"\{(\w+)\}", pat):
            name = m.group(1)
            if name not in _SUPPORTED and not name.startswith("field."):
                raise ValueError(
                    f"上传 pattern 包含不支持的模板变量 '{{{name}}}'。"
                    f" 支持的变量: {sorted(_SUPPORTED)}"
                )


class SharedFilesUploadConfig(BaseModel):
    """共享 (batch-level) 文件上传选择规则。"""

    base_dir: str = Field(default=".", description="本地共享文件基准目录，相对项目根")
    include: list[str] = Field(default_factory=list, description="包含文件的 glob 模式")
    exclude: list[str] = Field(default_factory=list, description="排除文件的 glob 模式")
    target_subdir: str = Field(default="_shared", description="远程 batch 下的共享子目录名")
    on_missing: MissingUploadPatternPolicy = Field(
        default=MissingUploadPatternPolicy.error,
        description="include pattern 零匹配时的策略",
    )


    @field_validator("target_subdir")
    @classmethod
    def _validate_target_subdir(cls, v: str) -> str:
        if not v or "\\" in v or v.startswith("/") or ".." in v.split("/"):
            raise ValueError("shared_files.target_subdir must be a safe relative POSIX path")
        return v


class UploadConfig(BaseModel):
    task_files: TaskFilesUploadConfig | list[str] | None = Field(
        default=None,
        description="任务文件上传选择规则 (TaskFilesUploadConfig 或简写 list[str])",
    )
    shared_files: "SharedFilesUploadConfig | None" = Field(
        default=None,
        description="共享文件上传配置",
    )
    skip_if_same_size: bool = Field(default=True, description="相同大小则跳过")

    @field_validator("task_files", mode="before")
    @classmethod
    def _coerce_list_to_config(cls, v: object) -> object:
        """简写 list[str] → TaskFilesUploadConfig(include=...)。"""
        if isinstance(v, list):
            return {"include": v, "exclude": [], "require_entry_file": True, "on_missing": "error"}
        return v


class DownloadConfig(BaseModel):
    patterns: list[str] = Field(default_factory=list, description="下载文件 glob 模式")
    completed_only: bool = Field(default=True, description="仅下载已完成任务")
    overwrite_policy: OverwritePolicy = Field(
        default=OverwritePolicy.deny_cross_batch,
        description="覆盖策略",
    )


class StatusConfig(BaseModel):
    success_patterns: list[str] = Field(default_factory=list, description="成功判定正则")
    failure_patterns: list[str] = Field(default_factory=list, description="失败判定正则")
    check_globs: list[str] = Field(default_factory=list, description="检查文件 glob")


class ExtractResult(BaseModel):
    name: str = Field(..., description="提取字段名")
    source_glob: str = Field(..., description="源文件 glob")
    regex: str = Field(..., description="提取正则，须包含命名组 value")
    strategy: ExtractStrategy = Field(default=ExtractStrategy.last, description="匹配策略")
    type: ExtractType = Field(default=ExtractType.float, description="值类型")
    unit: str | None = Field(default=None, description="单位")


class ExtractConfig(BaseModel):
    results: list[ExtractResult] = Field(default_factory=list, description="结果提取规则")


class OutputConfig(BaseModel):
    relative_energy_unit: str | None = Field(default=None, description="相对能量单位")
    hartree_to_kcal_mol: float | None = Field(
        default=None,
        ge=0,
        description="Hartree 到 kcal/mol 转换系数",
    )


class HooksConfig(BaseModel):
    post_download: str | None = Field(default=None, description="下载后 hook")
    post_analysis: str | None = Field(default=None, description="分析后 hook")


# ---- 项目配置顶层 ----------------------------------------------------------

class ProjectConfig(BaseModel):
    """project.yaml 的顶层结构。"""

    project_id: str = Field(..., min_length=1, description="项目稳定标识")
    project: ProjectMeta = Field(..., description="项目元信息")
    local_paths: LocalPaths = Field(..., description="本地路径")
    task_discoveries: list[TaskDiscoveryRule] = Field(default_factory=list, description="任务发现规则列表")
    execution_profiles: dict[str, ExecutionProfile] = Field(
        default_factory=dict,
        description="execution_profile_name -> ExecutionProfile",
    )

    @field_validator("task_discoveries")
    @classmethod
    def _validate_task_discoveries_names(cls, v: list[TaskDiscoveryRule]) -> list[TaskDiscoveryRule]:
        names = [rule.name for rule in v]
        if len(names) != len(set(names)):
            raise ValueError(f"task_discoveries 中存在重复的 name: {names}")
        return v

    @model_validator(mode="after")
    def _validate_task_discovery_profiles(self) -> "ProjectConfig":
        available_profiles = set(self.execution_profiles.keys())
        for rule in self.task_discoveries:
            if rule.execution_profile not in available_profiles:
                raise ValueError(
                    f"task_discoveries 中的 execution_profile={rule.execution_profile!r} "
                    f"不存在于 execution_profiles 中。可用的 profile: {sorted(available_profiles)}"
                )
        return self
    name_parser: NameParser = Field(default_factory=NameParser, description="命名解析")
    group_by: list[str] = Field(default_factory=list, description="分组字段列表")
    submit: SubmitConfig = Field(default_factory=SubmitConfig, description="提交配置")
    upload: UploadConfig = Field(default_factory=UploadConfig, description="上传配置")
    download: DownloadConfig = Field(default_factory=DownloadConfig, description="下载配置")
    status: StatusConfig = Field(default_factory=StatusConfig, description="状态判断配置")
    extract: ExtractConfig = Field(default_factory=ExtractConfig, description="结果提取配置")
    hooks: HooksConfig = Field(default_factory=HooksConfig, description="Hook 配置")
    output: OutputConfig = Field(default_factory=OutputConfig, description="输出配置")

    def get_execution_profile(self, name: str) -> ExecutionProfile:
        """获取指定名称的 ExecutionProfile。

        Raises:
            ValueError: 如果 profile 不存在。
        """
        if name not in self.execution_profiles:
            raise ValueError(
                f"project.yaml 中不存在 execution_profile={name!r}。"
                f" 可用的 profile: {list(self.execution_profiles.keys())}"
            )
        return self.execution_profiles[name]
