import json
from pathlib import Path

from .atomic_write import atomic_write_text
from .models import BatchMeta


def write_batch_json(batch: BatchMeta, output_path: Path) -> None:
    """将 BatchMeta 写入 batch.json 文件。

    Args:
        batch: BatchMeta 实例。
        output_path: 目标 JSON 文件路径（含文件名）。
    """
    data = batch.model_dump()
    data["created_at"] = batch.created_at.isoformat()
    atomic_write_text(output_path, json.dumps(data, indent=2, ensure_ascii=False))


def read_batch_json(file_path: Path) -> BatchMeta:
    """从 batch.json 文件读取 BatchMeta。

    Args:
        file_path: JSON 文件路径。

    Returns:
        BatchMeta 实例。
    """
    data = json.loads(file_path.read_text(encoding="utf-8"))
    return BatchMeta(**data)


def create_batch(
    project_name: str,
    max_parallel: int,
    remote_batch_dir: str,
    task_count: int = 0,
    status: str = "created",
    manifest_path: str | None = None,
) -> BatchMeta:
    """创建一个新的 Batch 实例，自动生成 batch_id。

    Args:
        project_name: 项目名称。
        max_parallel: 最大并行数。
        remote_batch_dir: 远程 Batch 目录。
        task_count: 任务总数，默认为 0。
        status: Batch 状态，默认为 "created"。
        manifest_path: Manifest 文件路径，可选。

    Returns:
        BatchMeta 实例。
    """
    return BatchMeta(
        project_name=project_name,
        max_parallel=max_parallel,
        remote_batch_dir=remote_batch_dir,
        task_count=task_count,
        status=status,
        manifest_path=manifest_path,
    )
