"""服务器状态信息采集模块。

只读远程服务器状态：hostname、uptime、load、CPU、内存、磁盘、进程。
不修改远程文件，不提交任务。
"""

import re
from dataclasses import dataclass, field
from .ssh import SSHClientWrapper


@dataclass
class DiskEntry:
    """单条磁盘挂载信息。"""

    filesystem: str
    size: str
    used: str
    available: str
    percent: str
    mountpoint: str


@dataclass
class ProcessEntry:
    """单条用户进程信息。"""

    pid: str
    pcpu: str
    pmem: str
    etime: str
    cmd: str


@dataclass
class ServerInfo:
    """远程服务器状态摘要。"""

    hostname: str = ""
    uptime_text: str = ""
    load_average: str = ""
    cpu_summary: str | None = None
    memory_total_mb: str = ""
    memory_used_mb: str = ""
    memory_free_mb: str = ""
    disk_entries: list[DiskEntry] = field(default_factory=list)
    current_user: str = ""
    user_processes: list[ProcessEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def collect_server_info(ssh: SSHClientWrapper) -> ServerInfo:
    """从远程服务器采集状态信息。

    命令失败不会中断采集，对应的字段留空并在 warnings 中记录。
    所有命令只读，不修改远程状态。

    Returns:
        ServerInfo 实例。
    """
    info = ServerInfo()

    # hostname
    try:
        r = ssh.run("hostname", timeout=10)
        info.hostname = r.stdout.strip()
    except Exception as e:
        info.warnings.append(f"hostname 获取失败: {e}")

    # uptime
    try:
        r = ssh.run("uptime", timeout=10)
        info.uptime_text = r.stdout.strip()
        # 提取 load average
        m = re.search(r"load average:\s*([\d., ]+)", r.stdout)
        if m:
            info.load_average = m.group(1)
    except Exception as e:
        info.warnings.append(f"uptime 获取失败: {e}")

    # current user
    try:
        r = ssh.run("whoami", timeout=10)
        info.current_user = r.stdout.strip()
    except Exception as e:
        info.warnings.append(f"whoami 获取失败: {e}")

    # CPU info (lscpu header)
    try:
        r = ssh.run("lscpu | grep -E '^CPU\\(s\\)|^Model name' 2>/dev/null || true", timeout=15)
        if r.stdout.strip():
            info.cpu_summary = r.stdout.strip()
    except Exception as e:
        info.warnings.append(f"lscpu 获取失败: {e}")

    # memory: free -m
    try:
        r = ssh.run("free -m 2>/dev/null || true", timeout=10)
        lines = r.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 3:
                info.memory_total_mb = parts[1]
                info.memory_used_mb = parts[2]
                info.memory_free_mb = parts[3] if len(parts) > 3 else ""
    except Exception as e:
        info.warnings.append(f"free -m 获取失败: {e}")

    # disk: df -h
    try:
        r = ssh.run("df -h 2>/dev/null || true", timeout=10)
        for line in r.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 6:
                info.disk_entries.append(DiskEntry(
                    filesystem=parts[0],
                    size=parts[1],
                    used=parts[2],
                    available=parts[3],
                    percent=parts[4],
                    mountpoint=parts[5],
                ))
    except Exception as e:
        info.warnings.append(f"df -h 获取失败: {e}")

    # processes: ps for current user, top 20 by CPU
    try:
        user = info.current_user
        if not user:
            info.warnings.append("ps 跳过: 无法确定当前用户 (whoami 失败)")
            return info
        cmd = (
            f"ps -u '{user}' -o pid,pcpu,pmem,etime,cmd --sort=-pcpu 2>/dev/null"
            f" | head -21 || true"
        )
        r = ssh.run(cmd, timeout=15)
        lines = r.stdout.strip().split("\n")
        for line in lines[1:21]:  # skip header, max 20 rows
            parts = line.split(None, 4)
            if len(parts) >= 5:
                info.user_processes.append(ProcessEntry(
                    pid=parts[0],
                    pcpu=parts[1],
                    pmem=parts[2],
                    etime=parts[3],
                    cmd=parts[4],
                ))
    except Exception as e:
        info.warnings.append(f"ps 获取失败: {e}")

    return info
