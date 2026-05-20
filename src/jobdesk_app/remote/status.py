"""远程任务状态标记文件读取模块。

只读：读取远程 .jobdesk_status、.jobdesk_exit_code、.jobdesk_submit.log。
不生成这些文件，不修改远程状态。
"""

import shlex
from dataclasses import dataclass, field
from .ssh import SSHClientWrapper


@dataclass
class RemoteTaskStatusSnapshot:
    """远程任务目录中的 JobDesk 状态标记快照。

    所有字段代表远程文件的读取结果。文件不存在不是错误，exists 字段为 False。
    """

    task_id: str
    remote_job_dir: str
    status_marker: str = ""
    exit_code: int | None = None
    submit_log_tail: str = ""
    marker_exists: bool = False
    exit_code_exists: bool = False
    log_exists: bool = False
    warnings: list[str] = field(default_factory=list)


def read_remote_task_status(
    ssh: SSHClientWrapper,
    task_id: str,
    remote_job_dir: str,
    log_tail_lines: int = 50,
) -> RemoteTaskStatusSnapshot:
    """读取远程单个任务目录中的 JobDesk 状态标记文件。

    所有文件路径使用 shlex.quote 安全转义。
    文件不存在不会抛异常。

    Args:
        ssh: 已连接的 SSHClientWrapper。
        task_id: 任务 ID。
        remote_job_dir: 远程任务工作目录。
        log_tail_lines: submit log 读取的最大行数。

    Returns:
        RemoteTaskStatusSnapshot 实例。
    """
    snapshot = RemoteTaskStatusSnapshot(task_id=task_id, remote_job_dir=remote_job_dir)

    dir_q = shlex.quote(remote_job_dir)

    # .jobdesk_status
    status_path = f"{dir_q}/.jobdesk_status"
    try:
        r = ssh.run(
            f"test -f {status_path} && cat {status_path} || echo '__NOT_FOUND__'",
            timeout=10,
        )
        if "__NOT_FOUND__" in r.stdout:
            snapshot.marker_exists = False
        else:
            snapshot.marker_exists = True
            snapshot.status_marker = r.stdout.strip()
    except Exception as e:
        snapshot.warnings.append(f"读取 .jobdesk_status 失败: {e}")

    # .jobdesk_exit_code
    exit_code_path = f"{dir_q}/.jobdesk_exit_code"
    try:
        r = ssh.run(
            f"test -f {exit_code_path} && cat {exit_code_path} || echo '__NOT_FOUND__'",
            timeout=10,
        )
        if "__NOT_FOUND__" in r.stdout:
            snapshot.exit_code_exists = False
        else:
            snapshot.exit_code_exists = True
            try:
                snapshot.exit_code = int(r.stdout.strip())
            except ValueError:
                snapshot.warnings.append(
                    f"exit_code 文件内容不是有效整数: {r.stdout.strip()!r}"
                )
    except Exception as e:
        snapshot.warnings.append(f"读取 .jobdesk_exit_code 失败: {e}")

    # .jobdesk_submit.log (tail)
    log_path = f"{dir_q}/.jobdesk_submit.log"
    try:
        r = ssh.run(
            f"test -f {log_path} && tail -n {log_tail_lines} {log_path} 2>/dev/null"
            f" || echo '__NOT_FOUND__'",
            timeout=15,
        )
        if "__NOT_FOUND__" in r.stdout:
            snapshot.log_exists = False
        else:
            snapshot.log_exists = True
            snapshot.submit_log_tail = r.stdout
    except Exception as e:
        snapshot.warnings.append(f"读取 .jobdesk_submit.log 失败: {e}")

    return snapshot
