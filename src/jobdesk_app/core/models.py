"""Core data models for JobDesk."""

from datetime import datetime

from pydantic import BaseModel, Field, model_serializer


class BatchMeta(BaseModel):
    """Batch 元数据模型。"""

    batch_id: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
    )
    project_name: str = Field(...)
    created_at: datetime = Field(default_factory=datetime.now)
    max_parallel: int = Field(..., ge=1)
    status: str = Field(default="created")
    task_count: int = Field(default=0, ge=0)
    remote_batch_dir: str = Field(...)
    manifest_path: str | None = Field(default=None)

    @model_serializer(mode="wrap")
    def _serialize(self, handler, info):
        result = handler(self)
        if isinstance(result, dict) and "created_at" in result:
            if isinstance(result["created_at"], datetime):
                result["created_at"] = result["created_at"].isoformat()
        return result


class ResultRecord(BaseModel):
    """一条本地分析提取的结果记录。"""

    task_id: str = Field(...)
    batch_id: str = Field(...)
    group_key: str | None = None
    result_id: str | None = None
    source_file: str = Field(...)
    field_name: str = Field(...)
    value: float | int | str = Field(...)
    value_type: str = Field(...)
    unit: str | None = None
    is_best_for_task: bool = False
    relative_group: float | None = None
    relative_global: float | None = None


class FailureRecord(BaseModel):
    """一条失败记录。"""

    task_id: str | None = None
    batch_id: str = Field(...)
    stage: str = Field(...)
    reason: str = Field(...)
    server_id: str | None = None
    execution_profile: str | None = None
    remote_job_dir: str | None = None
    source_file: str | None = None
    context: str | None = None
    timestamp: str = Field(
        default_factory=lambda: __import__("datetime").datetime.now().isoformat(),
    )
