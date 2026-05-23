"""全局用户级 servers.yaml 加载与校验。

推荐位置：%APPDATA%/JobDesk/servers.yaml
"""

import os
from pathlib import Path

import yaml

from .schema import ServersConfig


def get_default_servers_path() -> Path:
    """获取默认的 servers.yaml 路径。

    Windows: %APPDATA%/JobDesk/servers.yaml
    """
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return Path(appdata) / "JobDesk" / "servers.yaml"


def load_servers(path: Path | str | None = None) -> ServersConfig:
    """加载并校验 servers.yaml。

    Args:
        path: servers.yaml 文件路径。若为 None，使用默认路径。

    Returns:
        ServersConfig 实例。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: YAML 解析或数据校验失败。
    """
    if path is None:
        path = get_default_servers_path()
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"servers.yaml 不存在: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"servers.yaml 为空: {path}")

    return ServersConfig(**raw)
