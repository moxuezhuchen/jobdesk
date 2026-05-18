from datetime import datetime
from pathlib import Path
from pydantic import BaseModel, Field, model_serializer


class TaskPackage(BaseModel):
    """一个逻辑任务的完整文件集合。

    三种发现模式 (flat_single / grouped_by_stem / directory)
    均产出 TaskPackage 列表，后续流程以 TaskPackage 为源头。
    """

    task_id: str = Field(..., description="任务唯一标识")
    task_dir: Path | None = Field(default=None, description="任务根目录 (directory 模式)")
    entry_file: Path | None = Field(default=None, description="入口文件 (命令渲染入口)")
    files: list[Path] = Field(default_factory=list, description="该任务需上传的完整文件集合 (稳定排序)")
    parsed_fields: dict[str, str] = Field(default_factory=dict, description="解析字段")
    group_key: str | None = Field(default=None, description="分组键")
    execution_profile: str = Field(default="default", description="使用的执行 profile")
    discovery_name: str = Field(default="", description="产生该任务的发现规则名称")

    def sorted_files(self) -> list[Path]:
        """返回按路径排序的 files (稳定顺序)。"""
        return sorted(self.files, key=lambda p: p.as_posix())


class BatchMeta(BaseModel):
    """Batch 元数据模型。"""

    batch_id: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
        description="Batch ID，格式 YYYYMMDD_HHMMSS_ffffff",
    )
    project_name: str = Field(..., description="所属项目名称")
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="Batch 创建时间",
    )
    max_parallel: int = Field(..., ge=1, description="最大并行任务数")
    status: str = Field(default="created", description="Batch 状态")
    task_count: int = Field(default=0, ge=0, description="任务总数")
    remote_batch_dir: str = Field(..., description="远程 Batch 目录")
    manifest_path: str | None = Field(
        default=None,
        description="Manifest 文件路径",
    )
    shared_files: list["SharedFileRecord"] = Field(default_factory=list, description="共享文件记录")
    shared_target_subdir: str = Field(default="_shared", description="共享文件远程子目录名")

    @model_serializer(mode="wrap")
    def _serialize(self, handler, info):
        result = handler(self)
        if isinstance(result, dict) and "created_at" in result:
            if isinstance(result["created_at"], datetime):
                result["created_at"] = result["created_at"].isoformat()
        return result


# ---- 分析阶段数据模型 -------------------------------------------------------


class ResultRecord(BaseModel):
    """一条本地分析提取的结果记录。"""

    task_id: str = Field(..., description="所属任务 ID")
    batch_id: str = Field(..., description="所属 Batch ID")
    group_key: str | None = Field(default=None, description="分组键")
    result_id: str | None = Field(default=None, description="结果唯一标识（all 策略时区分多条）")
    source_file: str = Field(..., description="来源文件路径")
    field_name: str = Field(..., description="提取字段名")
    value: float | int | str = Field(..., description="提取到的值")
    value_type: str = Field(..., description="值类型: float / int / str")
    unit: str | None = Field(default=None, description="单位")
    is_best_for_task: bool = Field(default=False, description="是否为该任务的最佳候选")
    relative_group: float | None = Field(default=None, description="组内相对值")
    relative_global: float | None = Field(default=None, description="全局相对值")


class FailureRecord(BaseModel):
    """一条失败记录。"""

    task_id: str | None = Field(default=None, description="所属任务 ID (batch/server 级失败可为 None)")
    batch_id: str = Field(..., description="所属 Batch ID")
    stage: str = Field(..., description="失败阶段: upload/submit/refresh/download/analysis/runtime")
    reason: str = Field(..., description="失败原因")
    server_id: str | None = Field(default=None, description="关联 server_id")
    execution_profile: str | None = Field(default=None, description="关联 execution_profile")
    remote_job_dir: str | None = Field(default=None, description="远程任务目录")
    source_file: str | None = Field(default=None, description="相关源文件")
    context: str | None = Field(default=None, description="上下文信息")
    timestamp: str = Field(
        default_factory=lambda: __import__("datetime").datetime.now().isoformat(),
        description="UTC 时间戳",
    )


class BatchSummary(BaseModel):
    """Batch 列表摘要，用于 GUI 快速浏览。"""

    batch_id: str = Field(..., description="Batch ID")
    created_at: str = Field(default="", description="创建时间")
    task_count: int = Field(default=0, description="任务总数")
    status_summary: dict[str, int] = Field(default_factory=dict, description="状态 -> 数量")
    execution_profiles: list[str] = Field(default_factory=list, description="涉及的 execution_profiles")
    server_ids: list[str] = Field(default_factory=list, description="涉及的 server_id")
    shared_files_count: int = Field(default=0, description="共享文件数")


class SharedFileRecord(BaseModel):
    """一条 batch-level 共享文件记录。"""

    local_path: str = Field(..., description="本地绝对路径")
    relative_path: str = Field(..., description="相对 base_dir 的路径 (POSIX)")
    remote_name: str = Field(..., description="远程 _shared 下的相对路径 (POSIX)")
