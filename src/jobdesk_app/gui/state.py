"""GUI 状态模型 — 仅保存当前交互选择，不替代 core/services 数据。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppState:
    """GUI 全局状态，记录当前用户选择。"""

    current_project_root: Path | None = None
    current_project_context: object | None = None
    current_batch_id: str | None = None
    current_manifest_path: Path | None = None
    last_error: str | None = None
    last_agent_server: str | None = None  # server_id of last agent view
