"""项目服务：ProjectContext 创建与配置解析。

组合 project.yaml 和 servers.yaml，提供统一的本地路径上下文。
不进行 SSH 连接。server_id / remote_work_dir 通过 RuntimeBinding 解析。
"""

from pathlib import Path
from dataclasses import dataclass

from ..config.loader import load_project
from ..config.schema import ProjectConfig


@dataclass
class ProjectContext:
    """项目运行时上下文。

    仅包含项目本地信息。server / remote_work_dir 通过 RuntimeBinding 解析。
    """

    project_config: ProjectConfig
    project_root: Path
    local_input_dir: Path
    local_result_dir: Path
    servers_path: Path | None = None

    @property
    def project_id(self) -> str:
        return self.project_config.project_id

    @property
    def project_name(self) -> str:
        return self.project_config.project.name

    @property
    def jobdesk_meta_dir(self) -> Path:
        return self.project_root / ".jobdesk"

    @property
    def batches_dir(self) -> Path:
        return self.jobdesk_meta_dir / "batches"

    @property
    def results_batches_dir(self) -> Path:
        return self.local_result_dir / "batches"


def create_project_context(
    project_dir: str | Path,
    servers_path: str | Path | None = None,
) -> ProjectContext:
    """从项目目录创建 ProjectContext。

    Args:
        project_dir: 包含 project.yaml 的目录。
        servers_path: (已弃用，保留签名兼容性) 不再在此阶段加载服务器配置。

    Returns:
        ProjectContext 实例。

    Raises:
        FileNotFoundError: project.yaml 不存在。
    """
    project_dir = Path(project_dir).resolve()
    project_config = load_project(project_dir)

    local_input_dir = (project_dir / project_config.local_paths.input_dir).resolve()
    local_result_dir = (project_dir / project_config.local_paths.result_dir).resolve()

    return ProjectContext(
        project_config=project_config,
        project_root=project_dir,
        local_input_dir=local_input_dir,
        local_result_dir=local_result_dir,
        servers_path=Path(servers_path).resolve() if servers_path is not None else None,
    )
