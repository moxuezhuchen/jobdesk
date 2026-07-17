"""JobDesk 配置数据模型。

使用 Pydantic v2 进行配置校验。
"""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class AuthMethod(str, Enum):
    key = "key"
    password = "password"


class TerminalProvider(str, Enum):
    windows_terminal = "windows_terminal"
    putty = "putty"


class ExtractStrategy(str, Enum):
    first = "first"
    last = "last"
    all = "all"


class ExtractType(str, Enum):
    float = "float"
    int = "int"
    str = "str"


# ---- 服务器配置 ----------------------------------------------------------


class ExternalToolsConfig(BaseModel):
    """External desktop tools associated with one server profile."""

    terminal_provider: TerminalProvider = Field(
        default=TerminalProvider.windows_terminal,
        description="External terminal provider: windows_terminal / putty",
    )
    ssh_alias: str = Field(
        default="",
        description="OpenSSH config alias used by Windows Terminal",
    )
    putty_session: str = Field(
        default="",
        description="PuTTY saved session name",
    )
    terminal_path: str = Field(
        default="",
        description="Optional path to the terminal executable, for example putty.exe",
    )


class SSHAccessConfig(BaseModel):
    """Advanced SSH connection options for Paramiko and OpenSSH interop."""

    config_alias: str = Field(
        default="",
        description="Host alias from ~/.ssh/config used for runtime SSH/SFTP",
    )
    proxy_command: str = Field(
        default="",
        description="ProxyCommand used by Paramiko, for example ssh -W %h:%p gateway",
    )
    proxy_jump: str = Field(
        default="",
        description="OpenSSH-style ProxyJump host or comma-separated jump hosts",
    )


class ServerConfig(BaseModel):
    """单台服务器的连接配置。"""

    server_id: str = Field(default="", description="服务器唯一标识（自动从 key 注入）")
    display_name: str = Field(default="", description="显示名称")
    host: str = Field(..., description="服务器主机地址")
    port: int = Field(default=22, ge=1, le=65535, description="SSH 端口")
    username: str = Field(..., description="登录用户名")
    auth_method: AuthMethod = Field(default=AuthMethod.key, description="认证方式")
    key_path: str | None = Field(default=None, description="SSH 私钥路径")

    @property
    def auth_unsupported_message(self) -> str:
        """Non-empty if auth_method is configured but not supported at runtime."""
        if self.auth_method == AuthMethod.password:
            return "password auth is not supported; use key-based authentication"
        return ""

    wsl_distro: str | None = Field(default=None, description="连接前自动唤醒的 WSL 发行版名称")
    env_init_scripts: list[str] = Field(default_factory=list, description="执行任务前 source 的额外初始化脚本路径")
    scheduler: "SchedulerConfig" = Field(default_factory=lambda: SchedulerConfig(), description="作业调度器配置")
    trust_on_first_use: bool = Field(
        default=False, description="Trust and store an unknown SSH host key on first connection"
    )
    external_tools: ExternalToolsConfig = Field(
        default_factory=ExternalToolsConfig,
        description="External terminal and file-browser integration settings",
    )
    ssh_access: SSHAccessConfig = Field(
        default_factory=SSHAccessConfig,
        description="Advanced SSH connection settings",
    )


class SchedulerConfig(BaseModel):
    """作业调度器配置（嵌套在 ServerConfig 中）。"""

    type: str = Field(default="nohup", description="调度器类型: nohup / slurm / pbs")
    default_partition: str = Field(default="", description="默认队列/分区")
    default_account: str = Field(default="", description="默认账户")
    default_walltime_minutes: int = Field(default=1440, ge=1, description="默认 walltime（分钟）")
    default_cpus: int = Field(default=1, ge=1, description="默认 CPU 核数")
    default_memory_mb: int = Field(default=2048, ge=1, description="默认内存（MB）")
    default_gpus: int = Field(default=0, ge=0, description="默认 GPU 数")
    extra_directives: list[str] = Field(default_factory=list, description="额外调度器指令（如 #SBATCH --qos=high）")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        scheduler_type = (value or "nohup").lower()
        allowed = {"nohup", "slurm", "sbatch", "pbs", "torque", "qsub"}
        if scheduler_type not in allowed:
            raise ValueError("scheduler.type must be one of: nohup, slurm, sbatch, pbs, torque, qsub")
        return scheduler_type


class ServersConfig(BaseModel):
    """servers.yaml 的顶层结构。"""

    servers: dict[str, ServerConfig] = Field(
        default_factory=dict,
        description="server_id -> ServerConfig 映射",
    )

    @field_validator("servers", mode="before")
    @classmethod
    def inject_server_ids(cls, v: dict) -> dict:
        if isinstance(v, dict):
            return {k: {**val, "server_id": k} if isinstance(val, dict) else val for k, val in v.items()}
        return v


# ---- 结果提取配置 ----------------------------------------------------------


class ExtractResult(BaseModel):
    """单条结果提取规则。"""

    name: str = Field(..., description="提取字段名")
    source_glob: str = Field(..., description="源文件 glob")
    regex: str = Field(..., description="提取正则，须包含命名组 value")
    strategy: ExtractStrategy = Field(default=ExtractStrategy.last, description="匹配策略")
    type: ExtractType = Field(default=ExtractType.float, description="值类型")
    unit: str | None = Field(default=None, description="单位")
