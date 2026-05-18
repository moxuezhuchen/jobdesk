"""运行时绑定存储与解析。

不依赖 SQLite，使用轻量 YAML 文件。
"""

import os
from pathlib import Path
from dataclasses import dataclass
import yaml

from .schema import (
    RuntimeBinding,
    RuntimeBindingsConfig,
    ServerConfig,
    ExecutionProfile,
    ProjectConfig,
)
from .servers import load_servers, get_default_servers_path


def _default_runtime_bindings_path() -> Path:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return Path(appdata) / "JobDesk" / "runtime_bindings.yaml"


@dataclass
class ResolvedExecutionContext:
    """解析后的执行上下文 — 将 ExecutionProfile + RuntimeBinding 合并为运行时所需信息。"""

    project_id: str
    execution_profile_name: str
    server_id: str
    server_config: ServerConfig
    remote_work_dir: str
    command_template: str
    max_parallel: int

    @property
    def display_label(self) -> str:
        return f"{self.project_id}/{self.execution_profile_name} @ {self.server_id}"


class RuntimeBindingStore:
    """本机 runtime_bindings.yaml 的读写封装。"""

    def __init__(self, path: Path | None = None):
        self._path = path or _default_runtime_bindings_path()
        self._config: RuntimeBindingsConfig | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> RuntimeBindingsConfig:
        if self._config is not None:
            return self._config
        if self._path.exists():
            raw = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
            self._config = RuntimeBindingsConfig(**raw)
        else:
            self._config = RuntimeBindingsConfig()
        return self._config

    def get_binding(self, project_id: str, execution_profile: str) -> RuntimeBinding | None:
        """查找指定 project + profile 的运行时绑定，找不到返回 None。"""
        cfg = self._load()
        proj_bindings = cfg.bindings.get(project_id, {})
        return proj_bindings.get(execution_profile)

    def save_binding(
        self, project_id: str, execution_profile: str, binding: RuntimeBinding
    ) -> None:
        """保存一条绑定。"""
        cfg = self._load()
        if project_id not in cfg.bindings:
            cfg.bindings[project_id] = {}
        cfg.bindings[project_id][execution_profile] = binding
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            yaml.safe_dump(cfg.model_dump(exclude_defaults=True), allow_unicode=True),
            encoding="utf-8",
        )


def resolve_execution_context(
    project_config: ProjectConfig,
    execution_profile_name: str,
    binding_store: RuntimeBindingStore | None = None,
    servers_path: Path | None = None,
) -> ResolvedExecutionContext:
    """将 project.yaml + runtime_bindings.yaml 解析为完整执行上下文。

    Args:
        project_config: 已加载的项目配置。
        execution_profile_name: 要解析的 execution_profile 名称。
        binding_store: RuntimeBindingStore 实例，None 使用默认路径。
        servers_path: servers.yaml 路径，None 使用默认路径。

    Returns:
        ResolvedExecutionContext。

    Raises:
        ValueError: 如果 profile 不存在，或缺少运行时绑定，或 server_id 无效。
    """
    profile = project_config.get_execution_profile(execution_profile_name)

    if binding_store is None:
        binding_store = RuntimeBindingStore()
    binding = binding_store.get_binding(project_config.project_id, execution_profile_name)

    if binding is None:
        raise ValueError(
            f"项目 {project_config.project_id!r} 的 execution_profile"
            f" {execution_profile_name!r} 未绑定运行时。\n"
            f"请在 {binding_store.path} 中为"
            f" bindings.{project_config.project_id}.{execution_profile_name}"
            f" 配置 server_id 和 remote_work_dir。"
        )

    if servers_path is None:
        servers_path = get_default_servers_path()
    servers_config = load_servers(servers_path)

    if binding.server_id not in servers_config.servers:
        raise ValueError(
            f"runtime_bindings.yaml 中 project_id={project_config.project_id!r}"
            f" profile={execution_profile_name!r} 引用的 server_id={binding.server_id!r}"
            f" 在 servers.yaml 中不存在。"
            f" 可用 server_id: {list(servers_config.servers.keys())}"
        )

    server_config = servers_config.servers[binding.server_id]

    max_parallel = binding.max_parallel
    if max_parallel is None:
        max_parallel = profile.max_parallel

    return ResolvedExecutionContext(
        project_id=project_config.project_id,
        execution_profile_name=execution_profile_name,
        server_id=binding.server_id,
        server_config=server_config,
        remote_work_dir=binding.remote_work_dir.rstrip("/"),
        command_template=profile.command,
        max_parallel=max_parallel,
    )


def resolve_execution_contexts_for_project(
    project_config: ProjectConfig,
    profiles: set[str],
    binding_store: RuntimeBindingStore | None = None,
    servers_path: Path | None = None,
) -> dict[str, ResolvedExecutionContext]:
    """批量解析多个 execution_profile 的运行时上下文。

    Args:
        project_config: 已加载的项目配置。
        profiles: 需要解析的 execution_profile 名称集合。
        binding_store: RuntimeBindingStore 实例。
        servers_path: servers.yaml 路径。

    Returns:
        execution_profile_name → ResolvedExecutionContext 映射。

    Raises:
        ValueError: 任何 profile 的解析失败。
    """
    result: dict[str, ResolvedExecutionContext] = {}
    for ep_name in sorted(profiles):
        result[ep_name] = resolve_execution_context(
            project_config, ep_name, binding_store, servers_path,
        )
    return result
