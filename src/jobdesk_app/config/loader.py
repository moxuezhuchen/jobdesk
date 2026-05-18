"""project.yaml 加载器。

加载 YAML 配置文件并校验为 ProjectConfig 数据模型。
"""

from pathlib import Path
import yaml
from .schema import ProjectConfig


def load_project(project_dir: str | Path) -> ProjectConfig:
    """加载并校验项目目录下的 project.yaml。

    Args:
        project_dir: 项目根目录（包含 project.yaml 的目录）。

    Returns:
        ProjectConfig 实例。

    Raises:
        FileNotFoundError: project.yaml 不存在。
        ValueError: YAML 解析或数据校验失败。
    """
    project_dir = Path(project_dir)
    config_path = project_dir / "project.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"project.yaml 不存在: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"project.yaml 为空: {config_path}")

    return ProjectConfig(**raw)
