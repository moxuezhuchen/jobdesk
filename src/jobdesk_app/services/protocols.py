"""服务层 Protocol 定义。

提供类型安全的抽象层，支持运行时 isinstance() 检查，便于单元测试时注入 mock 实现。

Protocol 与现有实现的对应关系:
- SSHClient         ← remote.ssh.SSHClientWrapper
- SFTPClient        ← remote.sftp.SFTPClientWrapper
- SchedulerAdapter  ← remote.scheduler.{NohupAdapter,SlurmAdapter,PBSAdapter}
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from ..core.lifecycle import TaskStatus
from ..core.run import RunPlan

# ---------------------------------------------------------------------------
# SFTP Attribute 协议
# ---------------------------------------------------------------------------

@runtime_checkable
class SFTPAttr(Protocol):
    """SFTP 文件属性协议（对应 paramiko.SFTPAttributes）。"""

    st_size: int
    st_atime: float
    st_mtime: float
    st_mode: int
    filename: str


# ---------------------------------------------------------------------------
# SSH 客户端协议
# ---------------------------------------------------------------------------

@runtime_checkable
class SSHClient(Protocol):
    """SSH 客户端协议。

    对应实现: remote.ssh.SSHClientWrapper

    方法签名参考了 paramiko SSHClient 的常用调用模式。
    """

    def connect(self) -> None:
        """建立 SSH 连接。失败时抛出 SSHConnectionError。"""
        ...

    def is_alive(self) -> bool:
        """True if connected and the underlying transport is still active."""
        ...

    def run(
        self,
        command: str,
        timeout: int | None = None,
        check: bool = False,
    ) -> SSHResult:
        """Execute a remote shell command.

        Args:
            command: Shell command to execute.
            timeout: Timeout in seconds (None = connection default).
            check: Raise SSHCommandError if exit_code != 0.

        Returns:
            SSHResult with exit_code, stdout, stderr.
        """
        ...

    def open_session(self) -> SSHChannel:
        """Open a raw SSH channel session. Raises SSHConnectionError if not connected."""
        ...

    def close(self) -> None:
        """Close the SSH connection and all proxy-jump channels."""
        ...


@runtime_checkable
class SSHResult(Protocol):
    """单次远程命令执行结果。"""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


@runtime_checkable
class SSHChannel(Protocol):
    """SSH 通道协议（exec_command 返回的 channel 对象）。"""

    def exec_command(self, command: str) -> None: ...
    def settimeout(self, timeout: float | None) -> None: ...
    def recv(self, size: int) -> bytes: ...
    def recv_stderr(self, size: int) -> bytes: ...
    def send(self, data: bytes) -> int: ...
    def sendall(self, data: bytes) -> None: ...
    def recv_ready(self) -> bool: ...
    def recv_stderr_ready(self) -> bool: ...
    def exit_status_ready(self) -> bool: ...
    def recv_exit_status(self) -> int: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# SFTP 客户端协议
# ---------------------------------------------------------------------------

@runtime_checkable
class SFTPClient(Protocol):
    """SFTP 客户端协议。

    对应实现: remote.sftp.SFTPClientWrapper

    所有远程路径使用 POSIX 格式（正斜杠，不含反斜杠）。
    本地路径使用 pathlib.Path。
    """

    def is_alive(self) -> bool:
        """Return whether the SFTP channel is usable."""
        ...

    def exists(self, remote_path: str) -> bool:
        """检查远程路径是否存在。"""
        ...

    def stat(self, remote_path: str) -> SFTPAttr | None:
        """获取远程路径的 stat 信息，不存在时返回 None。"""
        ...

    def mkdir_p(self, remote_dir: str) -> None:
        """递归创建远程目录（支持已有父目录）。"""
        ...

    def list_dir(self, remote_dir: str) -> Iterator[str]:
        """List directory entries, yielding file/directory names."""
        ...

    def list_dir_info(self, remote_dir: str) -> list[RemoteEntry]:
        """列出远程目录内容，返回包含名称/大小/时间/权限的条目列表。"""
        ...

    def rename(self, old_path: str, new_path: str) -> None:
        """重命名远程文件或目录。"""
        ...

    def remove_file(self, remote_path: str) -> None:
        """删除远程文件。"""
        ...

    def remove_dir(self, remote_dir: str) -> None:
        """递归删除远程目录；symlink 只删除链接本身，绝不跟随进入目标。"""
        ...

    def read_file_bytes(self, remote_path: str, max_bytes: int = 65536) -> bytes:
        """读取远程文件前 max_bytes 字节。"""
        ...

    def is_dir(self, remote_path: str) -> bool:
        """检查远程路径是否为目录。"""
        ...

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
        progress_callback=None,
    ) -> TransferRecord:
        """上传单个文件到远程。

        Returns:
            TransferRecord。
        """
        ...

    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
        progress_callback=None,
    ) -> TransferRecord:
        """从远程下载单个文件到本地。

        Returns:
            TransferRecord。
        """
        ...

    def upload_many(
        self,
        files: list[tuple[Path, str]],
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """批量上传文件。"""
        ...

    def download_many(
        self,
        files: list[tuple[str, Path]],
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """批量下载文件。"""
        ...

    def upload_dir(
        self,
        local_dir: Path,
        remote_base: str,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """递归上传本地目录到远程，保持目录结构。"""
        ...

    def download_dir(
        self,
        remote_dir: str,
        local_base: Path,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
        dry_run: bool = False,
    ) -> list[TransferRecord]:
        """递归下载远程目录到本地，保持目录结构。"""
        ...

    def close(self) -> None:
        """关闭 SFTP channel。"""
        ...


@runtime_checkable
class RemoteEntry(Protocol):
    """list_dir_info 返回的单个目录条目。"""

    name: str
    path: str
    is_dir: bool
    size_bytes: int | None
    modified_at: float | None
    permissions: str


@runtime_checkable
class TransferRecord(Protocol):
    """单次文件传输的结果记录。"""

    direction: str  # TransferDirection enum value
    local_path: str
    remote_path: str
    size_bytes: int | None
    status: str  # TransferStatus enum value
    reason: str | None
    dry_run: bool


# ---------------------------------------------------------------------------
# 调度器适配器协议
# ---------------------------------------------------------------------------

@runtime_checkable
class SchedulerAdapter(Protocol):
    """调度器适配器协议。

    对应实现: remote.scheduler.{NohupAdapter,SlurmAdapter,PBSAdapter}

    SchedulerAdapter.submit() 返回的 job_id 会被持久化到 manifest；
    poll() 和 cancel() 通过该 job_id 与远程调度器交互。
    """

    def submit(
        self,
        ssh: SSHClient,
        script_path: str,
        resources: ResourceSpec,
    ) -> str:
        """Submit job script, return scheduler job_id.

        Args:
            ssh: 已连接的 SSH 客户端。
            script_path: 作业脚本在远程的路径。
            resources: 资源需求（CPU、内存、时长等）。

        Returns:
            调度器返回的作业 ID（字符串）。
        """
        ...

    def poll(self, ssh: SSHClient, job_id: str) -> JobState:
        """Poll job state from the remote scheduler.

        Args:
            ssh: 已连接的 SSH 客户端。
            job_id: 作业 ID（由 submit 返回）。

        Returns:
            JobState 枚举值。
        """
        ...

    def cancel(self, ssh: SSHClient, job_id: str) -> None:
        """Cancel a running/pending job.

        Args:
            ssh: 已连接的 SSH 客户端。
            job_id: 作业 ID（由 submit 返回）。
        """
        ...

    def get_remote_work_dir(self, job_id: str) -> Path | None:
        """从 job_id 推导远程工作目录（用于定位输出文件）。

        部分调度器（如 Slurm）可通过 job_id 查到 work dir；
        nohup adapter 不可用时返回 None。

        Args:
            job_id: 作业 ID。

        Returns:
            远程工作目录 Path，若无法推导则返回 None。
        """
        ...


@runtime_checkable
class JobState(Protocol):
    """作业状态枚举值（str, Enum）。"""

    value: str


@runtime_checkable
class ResourceSpec(Protocol):
    """作业资源规格。"""

    cpus: int
    memory_mb: int
    walltime_minutes: int
    partition: str
    account: str
    gpus: int
    extra_directives: list[str]

    def walltime_hms(self) -> str: ...


# ---------------------------------------------------------------------------
# 复合调度服务协议（用于 submit_use_case 等高层业务逻辑）
# ---------------------------------------------------------------------------

@runtime_checkable
class JobSubmitter(Protocol):
    """作业提交服务协议。

    高层抽象，由 submit_use_case.JobSubmitter 使用。
    组合了 SSH 连接、文件传输、作业提交多个步骤。
    """

    def submit_run(
        self,
        run_dir: Path,
        plan: RunPlan,
        ssh: SSHClient,
        sftp: SFTPClient,
    ) -> str:
        """提交一个 RunPlan 到远程并返回作业 ID。

        典型流程:
        1. 在远程创建 run_dir
        2. 通过 sftp 上传所有源文件
        3. 通过 scheduler 提交作业脚本
        4. 返回 job_id
        """
        ...


@runtime_checkable
class StatusPoller(Protocol):
    """状态轮询服务协议。"""

    def poll(
        self,
        job_id: str,
        ssh: SSHClient,
        scheduler: SchedulerAdapter,
    ) -> JobState:
        """轮询远程作业状态。"""
        ...


@runtime_checkable
class FileTransferrer(Protocol):
    """文件传输服务协议。"""

    def upload_run(
        self,
        run_dir: Path,
        plan: RunPlan,
        sftp: SFTPClient,
    ) -> list[TransferRecord]:
        """上传运行所需文件到远程。"""
        ...

    def download_results(
        self,
        job_id: str,
        remote_work_dir: Path,
        local_dir: Path,
        result_globs: list[str],
        sftp: SFTPClient,
    ) -> list[TransferRecord]:
        """从远程下载结果文件到本地。"""
        ...


# ---------------------------------------------------------------------------
# 生命周期状态协议
# ---------------------------------------------------------------------------

@runtime_checkable
class RunRecordInterface(Protocol):
    """本地 RunRecord 数据接口（用于 run_repository）。"""

    run_id: str
    server_id: str
    status: TaskStatus
    created_at: datetime
    remote_dir: str
    job_id: str | None
    task_count: int
