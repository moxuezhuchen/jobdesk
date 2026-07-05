"""AgentBridge — thin wrapper around SFTP + SSH exec for remote agent operations.

This module provides a high-level interface for the JobDesk GUI and CLI to
communicate with a remote `confflow-agent` daemon without requiring a dedicated
port or HTTP server.  All operations are performed over SSH (exec) and SFTP
(file transfer), consistent with the rest of the JobDesk architecture.

Design principles
-----------------
- No new TCP port on the remote — agent communication uses existing SSH channel.
- Remote agent must already be installed (pip installed with `[agent]` extras).
- File-based queue (`~/.confflow-queue/`) lives on the remote server.
- State DB (`~/.local/share/confflow-agent/state.db`) is the source of truth for
  job status; the CLI reads it directly via `confflow-agent list --all`.

Usage
-----
    bridge = AgentBridge("wsl")   # server_id from servers.yaml
    result = bridge.install_agent()
    result = bridge.submit_job("confflow.yaml", "mol.xyz")
    result = bridge.list_jobs()
    result = bridge.pause_job("job_abc123")
    result = bridge.download_job_output("job_abc123", Path("/local/output"))
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Local imports (jobdesk_staging itself)
from ..config.servers import ServerConfig, load_servers
from .session_pool import SessionLease, SessionPool

DEFAULT_QUEUE_DIR = "~/.confflow-queue"
DEFAULT_STATE_DB = "~/.local/share/confflow-agent/state.db"
DEFAULT_LOG_DIR = "~/.local/log/confflow-agent"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class BridgeResult:
    """Result of a bridge operation."""
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
# AgentBridge
# ---------------------------------------------------------------------------

class AgentBridge:
    """High-level interface for remote confflow-agent operations.

    Uses the JobDesk SessionPool for SSH/SFTP connections, so sessions are
    reused across multiple bridge operations within the same process.

    Parameters
    ----------
    server_id:
        Server key in ``servers.yaml`` (e.g. ``"wsl"``, ``"linux-cluster"``).
    servers_yaml:
        Path to a custom ``servers.yaml``.  ``None`` uses the default
        (``~/.jobdesk/servers.yaml``).
    pool:
        Optional ``SessionPool`` instance.  ``None`` creates a new one.
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
        self._pool = pool or SessionPool()
        self._agent_installed: bool | None = None
        self.server.host: str = str(self.server.host)

    # ------------------------------------------------------------------
    # Low-level SSH helpers
    # ------------------------------------------------------------------

    def _exec(self, cmd: str, check: bool = True) -> subprocess.CompletedProcess:
        """Execute a command on the remote via SSH and return the result."""
        result = subprocess.run(
            ["ssh", self.server.host, cmd],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"SSH exec failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return result

    def _sftp_get(self, remote_path: str, local_path: Path) -> None:
        """Download a remote file to a local path via SFTP."""
        subprocess.run(
            ["sftp", f"{self.server.host}:{remote_path}", str(local_path.parent)],
            check=True,
            capture_output=True,
        )

    def _sftp_put(self, local_path: Path, remote_path: str) -> None:
        """Upload a local file to a remote path via SFTP."""
        subprocess.run(
            ["sftp", str(local_path), f"{self.server.host}:{remote_path}"],
            check=True,
            capture_output=True,
        )

    def _remote_path(self, path: str | Path) -> str:
        """Ensure a path is remote-style (expand ~)."""
        p = str(path)
        if p.startswith("~"):
            return p
        if p.startswith("/"):
            return p
        return f"~/{p}"

    # ------------------------------------------------------------------
    # Session-based helpers (for use within a lease)
    # ------------------------------------------------------------------

    def _lease_exec(
        self,
        lease: SessionLease,
        cmd: str,
        check: bool = True,
    ) -> tuple[int, str, str]:
        """Execute command over an open SSH session lease. Returns (code, stdout, stderr)."""
        transport = lease.ssh.get_TRANSPORT()  # type: ignore[attr-defined]
        channel = transport.open_session()  # type: ignore[attr-defined]
        channel.exec_command(cmd)
        stdout_text = channel.makefile("rb").read().decode("utf-8", errors="replace")
        stderr_text = channel.makefile_stderr("rb").read().decode("utf-8", errors="replace")
        code = channel.recv_exit_status()
        if check and code != 0:
            raise RuntimeError(f"Remote command failed ({code}): {stderr_text.strip()}")
        return code, stdout_text, stderr_text

    def _lease_sftp_get(self, lease: SessionLease, remote_path: str, local_path: Path) -> None:
        """Download via SFTP session lease."""
        remote_file = lease.sftp.open(remote_path, "rb")
        try:
            local_file = open(local_path, "wb")
            try:
                shutil.copyfileobj(remote_file, local_file)
            finally:
                local_file.close()
        finally:
            remote_file.close()

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    def is_agent_installed(self) -> bool:
        """Check whether confflow-agent is on the remote's PATH."""
        if self._agent_installed is not None:
            return self._agent_installed
        try:
            result = subprocess.run(
                ["ssh", self.server.host, "confflow-agent --version"],
                capture_output=True, text=True, timeout=10,
            )
            self._agent_installed = result.returncode == 0
        except Exception:
            self._agent_installed = False
        return self._agent_installed

    def install_agent(
        self,
        queue_dir: str = DEFAULT_QUEUE_DIR,
        state_db: str = DEFAULT_STATE_DB,
        slots: int = 2,
        pip_extra: str = "agent",
    ) -> BridgeResult:
        """Install and enable the agent on the remote via SSH.

        Uses ``pip install "jobdesk[agent]"`` and optionally sets up a
        systemd user service (if systemd is available and lingering is enabled).

        Returns
        -------
        BridgeResult
            ``ok=True`` if installation succeeded.
        """
        if self.is_agent_installed():
            return BridgeResult.success(f"Agent already installed on {self.server_id}")

        remote_cmds = [
            # Upgrade pip first
            f"python3 -m pip install --user --upgrade pip",
            # Install jobdesk with agent extras
            f'python3 -m pip install --user "jobdesk[{pip_extra}]"',
        ]

        for cmd in remote_cmds:
            result = subprocess.run(
                ["ssh", self.server.host, cmd],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                return BridgeResult.failure(
                    f"Installation command failed: {cmd}\n{result.stderr.strip()}"
                )

        # Try to enable systemd lingering (Tier 2 fallback mechanism)
        subprocess.run(
            ["ssh", self.server.host,
             "systemd-logind-ctl enable-linger $(whoami) 2>/dev/null || true"],
            capture_output=True, timeout=30,
        )

        self._agent_installed = True
        return BridgeResult.success(
            f"Agent installed on {self.server_id}. "
            f"Run: confflow-agent serve --queue-dir {queue_dir} --state-db {state_db} --slots {slots}"
        )

    def start_agent(
        self,
        queue_dir: str = DEFAULT_QUEUE_DIR,
        state_db: str = DEFAULT_STATE_DB,
        slots: int = 2,
        use_systemd: bool = True,
    ) -> BridgeResult:
        """Start the agent daemon on the remote.

        If ``use_systemd=True`` (default), attempts to start via
        ``systemctl --user start confflow-agent``.  Falls back to
        direct ``confflow-agent serve`` via nohup otherwise.
        """
        if not self.is_agent_installed():
            return BridgeResult.failure(
                f"Agent not installed on {self.server_id}. Run: jobdesk agent install"
            )

        if use_systemd:
            result = subprocess.run(
                ["ssh", self.server.host,
                 f"systemctl --user start confflow-agent 2>/dev/null"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return BridgeResult.success(f"Agent started via systemd on {self.server_id}")

        # Fallback: direct serve
        serve_cmd = (
            f"mkdir -p {self._remote_path(queue_dir)} {self._remote_path(state_db)} && "
            f"nohup confflow-agent serve "
            f"--queue-dir {self._remote_path(queue_dir)} "
            f"--state-db {self._remote_path(state_db)} "
            f"--slots {slots} "
            f"> {self._remote_path(DEFAULT_LOG_DIR)}/agent.log 2>&1 &"
        )
        subprocess.run(["ssh", self.server.host, serve_cmd], timeout=30)
        return BridgeResult.success(f"Agent started via nohup on {self.server_id}")

    def stop_agent(self) -> BridgeResult:
        """Stop the agent daemon on the remote."""
        if not self.is_agent_installed():
            return BridgeResult.failure(f"Agent not installed on {self.server_id}")

        for method in [
            f"systemctl --user stop confflow-agent 2>/dev/null",
            f"pkill -f 'confflow-agent serve' 2>/dev/null || true",
        ]:
            subprocess.run(["ssh", self.server.host, method], timeout=30)
        return BridgeResult.success(f"Agent stopped on {self.server_id}")

    def get_agent_status(self) -> BridgeResult:
        """Check if the agent daemon is running on the remote."""
        if not self.is_agent_installed():
            return BridgeResult.failure(f"Agent not installed on {self.server_id}")

        result = subprocess.run(
            ["ssh", self.server.host,
             "systemctl --user is-active confflow-agent 2>/dev/null || "
             "pgrep -f 'confflow-agent serve' > /dev/null && echo running || echo stopped"],
            capture_output=True, text=True, timeout=15,
        )
        status = result.stdout.strip()
        return BridgeResult.success(f"Agent status on {self.server_id}: {status}")

    # ------------------------------------------------------------------
    # Job operations
    # ------------------------------------------------------------------

    def submit_job(
        self,
        config_remote: str,
        input_remote: str,
        job_id: str | None = None,
    ) -> BridgeResult:
        """Submit a job to the remote agent via SFTP + SSH exec.

        Parameters
        ----------
        config_remote:
            Remote path to the workflow YAML config file.
        input_remote:
            Remote path to the input XYZ file.
        job_id:
            Optional custom job ID.  If ``None`` the agent auto-generates one.
        """
        cmd = "confflow-agent submit " + " ".join([
            f'"{config_remote}"',
            f'"{input_remote}"',
            f"--job-id {job_id}" if job_id else "",
        ])
        result = subprocess.run(
            ["ssh", self.server.host, cmd.strip()],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return BridgeResult.failure(f"Submit failed: {result.stderr.strip()}")
        # Parse job_id from output: "Job <job_id> submitted to queue."
        match = re.search(r"Job (\S+) submitted", result.stdout)
        submitted_id = match.group(1) if match else (job_id or "?")
        return BridgeResult.success(f"Job {submitted_id} submitted to queue on {self.server_id}")

    def list_jobs(self, no_all: bool = False) -> BridgeResult:
        """List jobs tracked by the remote agent."""
        extra = "" if no_all else "--all"
        result = subprocess.run(
            ["ssh", self.server.host,
             f"confflow-agent list {extra} 2>/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return BridgeResult.failure(f"List failed: {result.stderr.strip()}")
        return BridgeResult.success(result.stdout.strip())

    def get_job_status(self, job_id: str) -> BridgeResult:
        """Get the status of a specific job."""
        result = subprocess.run(
            ["ssh", self.server.host,
             f"confflow-agent status {job_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return BridgeResult.failure(f"Status failed: {result.stderr.strip()}")
        return BridgeResult.success(result.stdout.strip())

    def pause_job(self, job_id: str) -> BridgeResult:
        """Pause a running or pending job."""
        result = subprocess.run(
            ["ssh", self.server.host,
             f"confflow-agent pause {job_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return BridgeResult.failure(f"Pause failed: {result.stderr.strip()}")
        return BridgeResult.success(result.stdout.strip())

    def resume_job(self, job_id: str) -> BridgeResult:
        """Resume a paused job."""
        result = subprocess.run(
            ["ssh", self.server.host,
             f"confflow-agent resume {job_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return BridgeResult.failure(f"Resume failed: {result.stderr.strip()}")
        return BridgeResult.success(result.stdout.strip())

    def cancel_job(self, job_id: str) -> BridgeResult:
        """Cancel a job."""
        result = subprocess.run(
            ["ssh", self.server.host,
             f"confflow-agent cancel {job_id}"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return BridgeResult.failure(f"Cancel failed: {result.stderr.strip()}")
        return BridgeResult.success(result.stdout.strip())

    # ------------------------------------------------------------------
    # Log retrieval
    # ------------------------------------------------------------------

    def tail_logs(self, job_id: str | None, lines: int = 50) -> BridgeResult:
        """Tail the last N lines of agent or job logs."""
        if job_id:
            remote_log = f"{self._remote_path(DEFAULT_LOG_DIR)}/{job_id}.log"
        else:
            remote_log = f"{self._remote_path(DEFAULT_LOG_DIR)}/agent.log"
        result = subprocess.run(
            ["ssh", self.server.host, f'tail -n {lines} "{remote_log}" 2>/dev/null'],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return BridgeResult.failure(f"Could not read {remote_log}: {result.stderr.strip()}")
        return BridgeResult.success(result.stdout)

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

        Parameters
        ----------
        job_id:
            The job ID whose output to download.
        local_dest:
            Local destination directory.
        patterns:
            Glob patterns for files to download (e.g. ``["*.xyz", "*.log"]``).
            ``None`` downloads all files in the job's output directory.
        """
        local_dest = Path(local_dest)
        local_dest.mkdir(parents=True, exist_ok=True)

        # Find the remote run directory from the agent state DB
        result = subprocess.run(
            ["ssh", self.server.host,
             f"confflow-agent status {job_id} 2>/dev/null | grep 'Work Dir'"],
            capture_output=True, text=True, timeout=30,
        )
        work_dir = None
        for line in result.stdout.splitlines():
            m = re.search(r"Work Dir:\s*(\S+)", line)
            if m:
                work_dir = m.group(1)
                break

        if not work_dir:
            return BridgeResult.failure(f"Could not determine work_dir for job {job_id}")

        # Download each pattern via SFTP
        patterns = patterns or ["*"]
        downloaded = []
        errors = []
        for pattern in patterns:
            glob_cmd = f"ls {work_dir}/{pattern} 2>/dev/null"
            ls_result = subprocess.run(
                ["ssh", self.server.host, glob_cmd],
                capture_output=True, text=True, timeout=30,
            )
            if ls_result.returncode != 0:
                continue
            for fname in ls_result.stdout.splitlines():
                if not fname.strip():
                    continue
                remote_path = f"{work_dir}/{fname.strip()}"
                local_path = local_dest / fname.strip()
                try:
                    subprocess.run(
                        ["sftp", f"{self.server.host}:{remote_path}", str(local_dest)],
                        check=True, capture_output=True, timeout=60,
                    )
                    downloaded.append(fname.strip())
                except Exception as exc:
                    errors.append(f"{fname.strip()}: {exc}")

        if not downloaded:
            if errors:
                return BridgeResult.failure("; ".join(errors))
            return BridgeResult.failure(f"No files matching {patterns} found in {work_dir}")

        msg = f"Downloaded {len(downloaded)} file(s) to {local_dest}"
        if errors:
            msg += f"; {len(errors)} error(s): {'; '.join(errors[:3])}"
        return BridgeResult.success(msg, files=downloaded, errors=errors)
