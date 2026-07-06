"""AgentBridge — SSH/SFTP bridge for remote confflow-agent operations.

All communication with the remote agent goes over SSH (paramiko) and SFTP, consistent
with the rest of the JobDesk architecture. No new TCP port is required on the remote.

Design
------
- Reuses SessionPool / SessionLease from jobdesk_app.services.session_pool so that
  SSH sessions are pooled and reused across bridge operations.
- `AgentBridge` acquires and releases a SessionLease per operation.
- For long-running operations (download) the lease is held for the full duration.
- Remote agent communicates via the file-based queue and state DB; this bridge
  calls `confflow-agent` CLI over SSH and reads/writes files via SFTP.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.servers import load_servers
from ..config.schema import ServerConfig
from .session_pool import SessionLease, SessionPool
from .ssh_session import create_sftp_client, create_ssh_client

DEFAULT_QUEUE_DIR = "~/.confflow-queue"
DEFAULT_STATE_DB = "~/.local/share/confflow-agent/state.db"
DEFAULT_LOG_DIR = "~/.local/log/confflow-agent"
DEFAULT_AGENT_SLOTS = 2


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class BridgeResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, message: str = "", **kwargs) -> "BridgeResult":
        return cls(ok=True, message=message, data=kwargs)

    @classmethod
    def failure(cls, message: str) -> "BridgeResult":
        return cls(ok=False, message=message)

    def __bool__(self) -> bool:
        return self.ok


# ---------------------------------------------------------------------------
# Job record
# ---------------------------------------------------------------------------

@dataclass
class AgentJob:
    job_id: str
    status: str = "unknown"
    step: str = ""
    progress_pct: int = 0
    work_dir: str = ""
    submitted_at: str = ""


# ---------------------------------------------------------------------------
# AgentBridge
# ---------------------------------------------------------------------------

class AgentBridge:
    """High-level interface for remote confflow-agent operations.

    Acquires a SessionLease per operation so sessions are pooled and reused.
    """

    def __init__(
        self,
        server_id: str,
        *,
        servers_yaml: Path | None = None,
        pool: SessionPool | None = None,
    ) -> None:
        self.server_id = server_id
        servers = load_servers(servers_yaml).servers
        if server_id not in servers:
            raise ValueError(f"Unknown server: {server_id!r}. Known: {list(servers)}")
        self.server: ServerConfig = servers[server_id]
        self._pool = pool or SessionPool(
            ssh_factory=lambda cfg: create_ssh_client(cfg),
            sftp_factory=create_sftp_client,
        )
        self._agent_installed: bool | None = None

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _remote_path(path: str | Path) -> str:
        p = str(path)
        if p.startswith("~") or p.startswith("/"):
            return p
        return f"~/{p}"

    # ------------------------------------------------------------------
    # Low-level exec via pooled session
    # ------------------------------------------------------------------

    def _exec(
        self,
        cmd: str,
        timeout: int = 120,
        check: bool = True,
    ) -> tuple[int, str, str]:
        """Execute a command over SSH using a pooled session. Returns (code, stdout, stderr)."""
        with self._pool.lease(self.server_id, self.server, need_sftp=False) as lease:
            result = lease.ssh.run(cmd, timeout=timeout, check=check)
            return result.exit_code, result.stdout, result.stderr

    # ------------------------------------------------------------------
    # Low-level SFTP helpers (file transfer)
    # ------------------------------------------------------------------

    def _sftp_read_text(self, remote_path: str) -> str:
        """Read remote text file content via SFTP lease."""
        with self._pool.lease(self.server_id, self.server, need_sftp=True) as lease:
            with lease.sftp.open(remote_path, "rb") as f:
                return f.read().decode("utf-8", errors="replace")

    def _sftp_write_text(self, remote_path: str, content: str) -> None:
        """Write text content to a remote file via SFTP lease."""
        with self._pool.lease(self.server_id, self.server, need_sftp=True) as lease:
            with lease.sftp.open(remote_path, "wb") as f:
                f.write(content.encode("utf-8"))

    def _sftp_upload(self, local_path: Path, remote_path: str) -> None:
        """Upload a local file to a remote path via SFTP lease."""
        with self._pool.lease(self.server_id, self.server, need_sftp=True) as lease:
            lease.sftp.put(str(local_path), remote_path)

    def _sftp_download(self, remote_path: str, local_path: Path) -> None:
        """Download a remote file to a local path via SFTP lease."""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with self._pool.lease(self.server_id, self.server, need_sftp=True) as lease:
            lease.sftp.get(remote_path, str(local_path))

    def _sftp_exists(self, remote_path: str) -> bool:
        """Check if a remote path exists via SFTP lease."""
        with self._pool.lease(self.server_id, self.server, need_sftp=True) as lease:
            try:
                lease.sftp.stat(remote_path)
                return True
            except FileNotFoundError:
                return False

    def _sftp_makedirs(self, remote_path: str) -> None:
        """Create remote directory (and parents) via SSH exec (SFTP has no makedirs)."""
        self._exec(f"mkdir -p {shlex.quote(remote_path)}", timeout=30, check=True)

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    def is_agent_installed(self) -> bool:
        """Check whether confflow-agent is on the remote's PATH."""
        if self._agent_installed is not None:
            return self._agent_installed
        try:
            code, _, _ = self._exec("confflow-agent --version", timeout=15, check=False)
            self._agent_installed = code == 0
        except Exception:
            self._agent_installed = False
        return self._agent_installed

    def is_agent_running(self) -> bool:
        """Check if the agent daemon is currently running on the remote."""
        if not self.is_agent_installed():
            return False
        try:
            code, out, _ = self._exec(
                "systemctl --user is-active confflow-agent 2>/dev/null || "
                "(pgrep -f 'confflow-agent serve' > /dev/null && echo running) || echo stopped",
                timeout=15, check=False,
            )
            status = out.strip()
            return status in ("active", "running")
        except Exception:
            return False

    def install_agent(
        self,
        queue_dir: str = DEFAULT_QUEUE_DIR,
        state_db: str = DEFAULT_STATE_DB,
        slots: int = DEFAULT_AGENT_SLOTS,
    ) -> BridgeResult:
        """Install jobdesk[agent] on the remote and create required directories."""
        if self.is_agent_installed():
            return BridgeResult.success(f"Agent already installed on {self.server_id}")

        remote_cmds = [
            "python3 -m pip install --user --upgrade pip",
            f'python3 -m pip install --user "jobdesk[agent]"',
        ]
        for cmd in remote_cmds:
            try:
                self._exec(cmd, timeout=180)
            except RuntimeError as exc:
                return BridgeResult.failure(f"Install failed: {exc}")

        # Ensure directories exist
        self._sftp_makedirs(self._remote_path(queue_dir))
        state_db_dir = str(Path(self._remote_path(state_db)).parent)
        self._sftp_makedirs(state_db_dir)

        # Best-effort systemd lingering
        self._exec(
            "systemd-logind-ctl enable-linger $(whoami) 2>/dev/null || true",
            timeout=30, check=False,
        )

        self._agent_installed = True
        return BridgeResult.success(
            f"Agent installed on {self.server_id}. "
            f"Start: confflow-agent serve --queue-dir {queue_dir} "
            f"--state-db {state_db} --slots {slots}"
        )

    def start_agent(
        self,
        queue_dir: str = DEFAULT_QUEUE_DIR,
        state_db: str = DEFAULT_STATE_DB,
        slots: int = DEFAULT_AGENT_SLOTS,
    ) -> BridgeResult:
        """Start the agent daemon on the remote (systemd preferred, nohup fallback)."""
        if not self.is_agent_installed():
            return BridgeResult.failure(
                f"Agent not installed. Run: jobdesk agent install --server {self.server_id}"
            )

        # systemd first
        code, _, _ = self._exec(
            "systemctl --user start confflow-agent 2>/dev/null",
            timeout=30, check=False,
        )
        if code == 0:
            return BridgeResult.success(f"Agent started via systemd on {self.server_id}")

        # nohup fallback
        self._sftp_makedirs(self._remote_path(queue_dir))
        state_db_dir = str(Path(self._remote_path(state_db)).parent)
        self._sftp_makedirs(state_db_dir)
        log_dir = str(Path(self._remote_path(DEFAULT_LOG_DIR)).parent)
        self._sftp_makedirs(log_dir)

        log_file = f"{self._remote_path(DEFAULT_LOG_DIR)}/agent.log"
        serve_cmd = (
            f"nohup confflow-agent serve "
            f"--queue-dir {shlex.quote(self._remote_path(queue_dir))} "
            f"--state-db {shlex.quote(self._remote_path(state_db))} "
            f"--slots {slots} "
            f"> {shlex.quote(log_file)} 2>&1 &"
        )
        self._exec(serve_cmd, timeout=30, check=False)
        return BridgeResult.success(f"Agent started via nohup on {self.server_id}")

    def stop_agent(self) -> BridgeResult:
        """Stop the agent daemon on the remote."""
        for method in [
            "systemctl --user stop confflow-agent 2>/dev/null || true",
            "pkill -f 'confflow-agent serve' 2>/dev/null || true",
        ]:
            self._exec(method, timeout=30, check=False)
        return BridgeResult.success(f"Agent stopped on {self.server_id}")

    def get_agent_status(self) -> BridgeResult:
        """Get daemon status as a human-readable string."""
        if not self.is_agent_installed():
            return BridgeResult.failure(f"Agent not installed on {self.server_id}")
        running = self.is_agent_running()
        status = "running" if running else "stopped"
        return BridgeResult.success(f"Agent on {self.server_id}: {status}")

    # ------------------------------------------------------------------
    # Job operations
    # ------------------------------------------------------------------

    def submit_job(
        self,
        config_remote: str,
        input_remote: str,
        job_id: str | None = None,
    ) -> BridgeResult:
        """Submit a job to the remote agent queue.

        Parameters
        ----------
        config_remote:
            Remote path to the workflow YAML config.
        input_remote:
            Remote path to the input XYZ file.
        job_id:
            Optional custom job ID (auto-generated if None).
        """
        if not self.is_agent_running():
            return BridgeResult.failure(
                f"Agent not running on {self.server_id}. "
                f"Run: jobdesk agent start --server {self.server_id}"
            )
        parts = [
            shlex.quote(config_remote),
            shlex.quote(input_remote),
        ]
        if job_id:
            parts.append(f"--job-id {shlex.quote(job_id)}")
        cmd = "confflow-agent submit " + " ".join(parts)
        try:
            code, stdout, stderr = self._exec(cmd, timeout=60, check=False)
        except RuntimeError as exc:
            return BridgeResult.failure(f"SSH error: {exc}")

        if code != 0:
            return BridgeResult.failure(f"Submit failed: {stderr.strip()}")
        match = re.search(r"Job (\S+) submitted", stdout)
        submitted_id = match.group(1) if match else (job_id or "?")
        return BridgeResult.success(
            f"Job {submitted_id} submitted to queue on {self.server_id}",
            job_id=submitted_id,
        )

    def list_jobs(self, no_all: bool = False) -> BridgeResult:
        """List jobs tracked by the remote agent."""
        extra = "" if no_all else "--all"
        try:
            code, stdout, stderr = self._exec(
                f"confflow-agent list {extra} 2>/dev/null",
                timeout=30, check=False,
            )
        except RuntimeError as exc:
            return BridgeResult.failure(f"SSH error: {exc}")
        if code != 0:
            return BridgeResult.failure(f"List failed: {stderr.strip()}")
        return BridgeResult.success(stdout.strip())

    def parse_jobs(self) -> list[AgentJob]:
        """Parse `confflow-agent list --all` into AgentJob objects."""
        code, stdout, _ = self._exec(
            "confflow-agent list --all 2>/dev/null", timeout=30, check=False,
        )
        if code != 0:
            return []
        jobs: list[AgentJob] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            parts = line.strip().split(None, 5)
            if len(parts) < 2:
                continue
            job = AgentJob(
                job_id=parts[0],
                status=parts[1],
                step=parts[2] if len(parts) > 2 else "",
                progress_pct=int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0,
                work_dir=parts[4] if len(parts) > 4 else "",
                submitted_at=parts[5] if len(parts) > 5 else "",
            )
            jobs.append(job)
        return jobs

    def get_job_status(self, job_id: str) -> BridgeResult:
        """Get status text for a specific job."""
        try:
            code, stdout, stderr = self._exec(
                f"confflow-agent status {shlex.quote(job_id)}",
                timeout=30, check=False,
            )
        except RuntimeError as exc:
            return BridgeResult.failure(f"SSH error: {exc}")
        if code != 0:
            return BridgeResult.failure(f"Status failed: {stderr.strip()}")
        return BridgeResult.success(stdout.strip())

    def get_job(self, job_id: str) -> AgentJob | None:
        """Get a single job record by parsing status output."""
        code, stdout, _ = self._exec(
            f"confflow-agent status {shlex.quote(job_id)} 2>/dev/null",
            timeout=30, check=False,
        )
        if code != 0:
            return None
        job = AgentJob(job_id=job_id, status="unknown")
        for line in stdout.splitlines():
            if m := re.match(r"Status:\s*(\S+)", line):
                job.status = m.group(1)
            elif m := re.match(r"Current step:\s*(.+)", line):
                job.step = m.group(1).strip()
            elif m := re.match(r"Progress:\s*(\d+)%", line):
                job.progress_pct = int(m.group(1))
            elif m := re.match(r"Work dir:\s*(\S+)", line):
                job.work_dir = m.group(1)
        return job

    def pause_job(self, job_id: str) -> BridgeResult:
        return self._job_action("pause", job_id)

    def resume_job(self, job_id: str) -> BridgeResult:
        return self._job_action("resume", job_id)

    def cancel_job(self, job_id: str) -> BridgeResult:
        return self._job_action("cancel", job_id)

    def _job_action(self, action: str, job_id: str) -> BridgeResult:
        """Run confflow-agent <action> <job_id>."""
        try:
            code, stdout, stderr = self._exec(
                f"confflow-agent {action} {shlex.quote(job_id)}",
                timeout=30, check=False,
            )
        except RuntimeError as exc:
            return BridgeResult.failure(f"SSH error: {exc}")
        if code != 0:
            return BridgeResult.failure(f"{action.capitalize()} failed: {stderr.strip()}")
        return BridgeResult.success(stdout.strip() or f"Job {job_id} {action}ed")

    # ------------------------------------------------------------------
    # Log retrieval
    # ------------------------------------------------------------------

    def tail_logs(self, job_id: str | None = None, lines: int = 50) -> BridgeResult:
        """Read the last N lines of agent or job logs."""
        if job_id:
            remote_log = f"{self._remote_path(DEFAULT_LOG_DIR)}/{job_id}.log"
        else:
            remote_log = f"{self._remote_path(DEFAULT_LOG_DIR)}/agent.log"
        try:
            code, stdout, stderr = self._exec(
                f"tail -n {lines} {shlex.quote(remote_log)} 2>/dev/null",
                timeout=30, check=False,
            )
            if code == 0:
                return BridgeResult.success(stdout)
        except RuntimeError:
            pass
        # Fallback: try SFTP
        if self._sftp_exists(remote_log):
            try:
                content = self._sftp_read_text(remote_log)
                tail = "\n".join(content.splitlines()[-lines:])
                return BridgeResult.success(tail)
            except Exception as exc:
                return BridgeResult.failure(f"Could not read {remote_log}: {exc}")
        return BridgeResult.failure(f"Could not read {remote_log}")

    # ------------------------------------------------------------------
    # Output download
    # ------------------------------------------------------------------

    def download_job_output(
        self,
        job_id: str,
        local_dest: Path,
        patterns: list[str] | None = None,
    ) -> BridgeResult:
        """Download job output files from the remote to a local directory.

        Discovers the remote work_dir from agent status, then downloads
        files matching ``patterns`` (default: ``["*"]``).
        """
        local_dest = Path(local_dest)
        local_dest.mkdir(parents=True, exist_ok=True)

        job = self.get_job(job_id)
        if job is None:
            return BridgeResult.failure(f"Could not get status for job {job_id}")
        if not job.work_dir:
            return BridgeResult.failure(
                f"No work_dir for job {job_id} (job may not have started)"
            )

        patterns = patterns or ["*"]
        downloaded: list[str] = []
        errors: list[str] = []

        for pattern in patterns:
            try:
                code, stdout, _ = self._exec(
                    f"ls {shlex.quote(job.work_dir)}/{pattern} 2>/dev/null",
                    timeout=30, check=False,
                )
            except RuntimeError as exc:
                errors.append(f"pattern {pattern!r}: {exc}")
                continue

            if code != 0:
                continue

            for fname in stdout.splitlines():
                fname = fname.strip()
                if not fname:
                    continue
                remote_path = f"{job.work_dir}/{fname}"
                local_path = local_dest / fname
                try:
                    self._sftp_download(remote_path, local_path)
                    downloaded.append(fname)
                except Exception as exc:
                    errors.append(f"{fname}: {exc}")

        if not downloaded:
            msg = f"No files matching {patterns} found in {job.work_dir}"
            if errors:
                msg += f"; errors: {'; '.join(errors[:3])}"
            return BridgeResult.failure(msg)

        msg = f"Downloaded {len(downloaded)} file(s) to {local_dest}"
        if errors:
            msg += f"; {len(errors)} error(s)"
        return BridgeResult.success(msg, files=downloaded, errors=errors)
