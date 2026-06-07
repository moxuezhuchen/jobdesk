from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config.schema import ServerConfig, TerminalProvider


@dataclass(frozen=True)
class TerminalLaunch:
    executable: str
    args: list[str]
    user_visible_command: str


def build_cd_command(remote_dir: str) -> str:
    return f"cd {shlex.quote(remote_dir)}"


def build_remote_shell_command(remote_dir: str) -> str:
    return f"{build_cd_command(remote_dir)} && exec ${{SHELL:-/bin/sh}} -l"


def build_terminal_launch(
    server: ServerConfig,
    remote_dir: str,
    *,
    temp_dir: str | Path,
) -> TerminalLaunch:
    provider = server.external_tools.terminal_provider
    if provider == TerminalProvider.putty:
        return _build_putty_launch(server, remote_dir, temp_dir=Path(temp_dir))
    return _build_windows_terminal_launch(server, remote_dir)


def _ssh_target(server: ServerConfig) -> str:
    alias = server.external_tools.ssh_alias.strip()
    if alias:
        return alias
    return f"{server.username}@{server.host}"


def _build_windows_terminal_launch(server: ServerConfig, remote_dir: str) -> TerminalLaunch:
    remote_command = build_remote_shell_command(remote_dir)
    ssh_parts = ["ssh", "-t", _ssh_target(server)]
    if not server.external_tools.ssh_alias.strip() and server.port != 22:
        ssh_parts.extend(["-p", str(server.port)])
    ssh_parts.append(remote_command)
    args = ["new-tab", "powershell", "-NoExit", "-Command", *ssh_parts]
    return TerminalLaunch(
        executable="wt",
        args=args,
        user_visible_command="wt " + " ".join(shlex.quote(part) for part in args),
    )


def _build_putty_launch(
    server: ServerConfig,
    remote_dir: str,
    *,
    temp_dir: Path,
) -> TerminalLaunch:
    session = server.external_tools.putty_session.strip()
    if not session:
        raise ValueError("PuTTY saved session is required for PuTTY terminal launch")
    temp_dir.mkdir(parents=True, exist_ok=True)
    command_file = temp_dir / f"jobdesk_putty_{server.server_id}.sh"
    command_file.write_text(
        build_cd_command(remote_dir) + "\nexec ${SHELL:-/bin/sh} -l\n",
        encoding="utf-8",
    )
    args = ["-load", session, "-t", "-m", str(command_file)]
    return TerminalLaunch(
        executable="putty.exe",
        args=args,
        user_visible_command="putty.exe " + " ".join(shlex.quote(part) for part in args),
    )


def launch_terminal(launch: TerminalLaunch):
    return subprocess.Popen([launch.executable, *launch.args])
