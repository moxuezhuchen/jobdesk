"""Pure validated configuration models shared across JobDesk layers.

The models preserve the existing ``servers.yaml`` wire shape.  Loading files,
choosing user directories, and handling the operating system belong to
infrastructure rather than this module.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AuthMethod(str, Enum):
    key = "key"
    password = "password"


class TerminalProvider(str, Enum):
    windows_terminal = "windows_terminal"
    putty = "putty"


class ExternalToolsConfig(BaseModel):
    """External desktop tools associated with one server profile."""

    terminal_provider: TerminalProvider = Field(
        default=TerminalProvider.windows_terminal,
        description="External terminal provider: windows_terminal / putty",
    )
    ssh_alias: str = Field(default="", description="OpenSSH config alias used by Windows Terminal")
    putty_session: str = Field(default="", description="PuTTY saved session name")
    terminal_path: str = Field(
        default="",
        description="Optional path to the terminal executable, for example putty.exe",
    )


class SSHAccessConfig(BaseModel):
    """Advanced SSH connection options for Paramiko and OpenSSH interop."""

    config_alias: str = Field(default="", description="Host alias from ~/.ssh/config used for runtime SSH/SFTP")
    proxy_command: str = Field(
        default="",
        description="ProxyCommand used by Paramiko, for example ssh -W %h:%p gateway",
    )
    proxy_jump: str = Field(
        default="",
        description="OpenSSH-style ProxyJump host or comma-separated jump hosts",
    )


class SchedulerConfig(BaseModel):
    """Scheduler configuration nested in :class:`ServerConfig`."""

    type: str = Field(default="nohup", description="Scheduler type: nohup / slurm / pbs")
    default_partition: str = Field(default="", description="Default queue or partition")
    default_account: str = Field(default="", description="Default account")
    default_walltime_minutes: int = Field(default=1440, ge=1, description="Default walltime in minutes")
    default_cpus: int = Field(default=1, ge=1, description="Default CPU core count")
    default_memory_mb: int = Field(default=2048, ge=1, description="Default memory in MB")
    default_gpus: int = Field(default=0, ge=0, description="Default GPU count")
    extra_directives: list[str] = Field(default_factory=list, description="Additional scheduler directives")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        scheduler_type = (value or "nohup").lower()
        allowed = {"nohup", "slurm", "sbatch", "pbs", "torque", "qsub"}
        if scheduler_type not in allowed:
            raise ValueError("scheduler.type must be one of: nohup, slurm, sbatch, pbs, torque, qsub")
        return scheduler_type


class ServerConfig(BaseModel):
    """Validated connection configuration for one server."""

    server_id: str = Field(default="", description="Unique server identifier injected from the mapping key")
    display_name: str = Field(default="", description="Display name")
    host: str = Field(..., description="Server host")
    port: int = Field(default=22, ge=1, le=65535, description="SSH port")
    username: str = Field(..., description="Login username")
    auth_method: AuthMethod = Field(default=AuthMethod.key, description="Authentication method")
    key_path: str | None = Field(default=None, description="SSH private key path")
    max_cores: int | None = Field(default=None, ge=1, description="Maximum effective core slots")
    wsl_distro: str | None = Field(default=None, description="WSL distribution to wake before connecting")
    env_init_scripts: list[str] = Field(default_factory=list, description="Additional environment init scripts")
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig, description="Scheduler configuration")
    confflow_executable: str = Field(
        default="",
        description="Optional absolute ConfFlow executable; empty resolves it from PATH",
    )
    trust_on_first_use: bool = Field(default=False, description="Trust and store an unknown host key")
    external_tools: ExternalToolsConfig = Field(default_factory=ExternalToolsConfig)
    ssh_access: SSHAccessConfig = Field(default_factory=SSHAccessConfig)

    @property
    def auth_unsupported_message(self) -> str:
        if self.auth_method == AuthMethod.password:
            return "password auth is not supported; use key-based authentication"
        return ""


class ServersConfig(BaseModel):
    """Top-level ``servers.yaml`` document."""

    servers: dict[str, ServerConfig] = Field(default_factory=dict)

    @field_validator("servers", mode="before")
    @classmethod
    def inject_server_ids(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: {**item, "server_id": key} if isinstance(item, dict) else item for key, item in value.items()}
        return value


__all__ = [
    "AuthMethod",
    "ExternalToolsConfig",
    "SchedulerConfig",
    "ServerConfig",
    "ServersConfig",
    "SSHAccessConfig",
    "TerminalProvider",
]
