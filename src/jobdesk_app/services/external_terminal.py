from __future__ import annotations

import re
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
    ssh_args = ["-t"]
    if not server.external_tools.ssh_alias.strip() and server.port != 22:
        ssh_args.extend(["-p", str(server.port)])
    ssh_args.extend([_ssh_target(server), remote_command])
    ssh_command = _powershell_command("ssh", ssh_args)
    args = ["-w", "0", "new-tab", "powershell", "-Command", ssh_command]
    executable = server.external_tools.terminal_path.strip() or "wt"
    return TerminalLaunch(
        executable=executable,
        args=args,
        user_visible_command=_powershell_command(executable, args),
    )


_POWERSHELL_BARE_ARG = re.compile(r"^[A-Za-z0-9_./:@%+=,-]+$")


def _powershell_quote(arg: str) -> str:
    if arg and _POWERSHELL_BARE_ARG.fullmatch(arg):
        return arg
    return "'" + arg.replace("'", "''") + "'"


def _powershell_command(executable: str, args: list[str]) -> str:
    return " ".join(_powershell_quote(part) for part in [executable, *args])


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
    executable = server.external_tools.terminal_path.strip() or "putty.exe"
    args = ["-load", session, "-t", "-m", str(command_file)]
    return TerminalLaunch(
        executable=executable,
        args=args,
        user_visible_command=_powershell_command(executable, args),
    )


def launch_terminal(launch: TerminalLaunch):
    return subprocess.Popen([launch.executable, *launch.args])
