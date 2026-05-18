"""GUI 状态模型 — 仅保存当前交互选择，不替代 core/services 数据。"""

from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class AppState:
    """GUI 全局状态，记录当前用户选择。"""

    current_project_root: Path | None = None
    current_project_context: object | None = None  # ProjectContext
    current_batch_id: str | None = None
    current_manifest_path: Path | None = None
    last_error: str | None = None
