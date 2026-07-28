"""Shared remote ConfFlow capability probe.

The probe is deliberately independent of the submission or GUI layers so
both upload-time and submit-time gates execute the exact same handshake.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from typing import Any

from ..core.confflow_preflight import (
    ConfFlowCapabilities,
    parse_confflow_capabilities,
    validate_confflow_capabilities,
)


class ConfFlowCapabilityPreflightError(RuntimeError):
    """The remote ConfFlow capability contract could not be accepted."""


def quote_confflow_executable(executable: str | None = None) -> str:
    """Return one shell-safe ConfFlow executable token.

    An empty configuration deliberately preserves the historical PATH lookup.
    A configured path is quoted once and can be reused unchanged by the
    capability probe, dry-run preflight, and generated task runner.
    """
    value = (executable or "").strip() or "confflow"
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("ConfFlow executable must not contain NUL or newlines")
    return shlex.quote(value)


def build_confflow_capability_command(executable: str | None = None) -> str:
    """Build the capability invocation for one configured executable."""
    return f"{quote_confflow_executable(executable)} --capabilities --json"


def build_confflow_preflight_shell(
    command: str = "confflow --capabilities --json",
    env_init_scripts: Iterable[str] = (),
) -> str:
    """Build the shell command used for a remote capability probe."""
    lines = [
        "set +u",
        "[ -f /etc/profile ] && . /etc/profile >/dev/null 2>&1 || true",
        '[ -f "$HOME/.bash_profile" ] && . "$HOME/.bash_profile" >/dev/null 2>&1 || true',
        '[ -f "$HOME/.profile" ] && . "$HOME/.profile" >/dev/null 2>&1 || true',
        '[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc" >/dev/null 2>&1 || true',
    ]
    lines.extend(
        f"[ -f {shlex.quote(script)} ] && . {shlex.quote(script)} >/dev/null 2>&1 || true"
        for script in env_init_scripts
        if script
    )
    lines.append(command)
    return "\n".join(lines)


def probe_confflow_capabilities(
    ssh: Any,
    *,
    env_init_scripts: Iterable[str] = (),
    require_dag: bool = False,
    confflow_executable: str | None = None,
) -> ConfFlowCapabilities:
    """Run and validate the capability handshake on an SSH client.

    Raises ConfFlowCapabilityPreflightError for connection, command,
    parsing, schema, version, capability, or artifact failures.

    Returns the parsed ``ConfFlowCapabilities`` on success so the
    caller can forward reusable fields (build provenance, version)
    to the SubmitResult warnings (R-M1 / P-H0 / P-H2C).
    """
    command = build_confflow_preflight_shell(
        build_confflow_capability_command(confflow_executable), env_init_scripts=env_init_scripts
    )
    try:
        response = ssh.run(command, timeout=30)
    except Exception as exc:
        raise ConfFlowCapabilityPreflightError(f"ConfFlow capability preflight failed: {exc}") from exc
    if response.exit_code != 0:
        detail = response.stderr.strip() or response.stdout.strip() or f"exit {response.exit_code}"
        raise ConfFlowCapabilityPreflightError(f"ConfFlow capability preflight failed: {detail}")
    try:
        capabilities = parse_confflow_capabilities(response.stdout)
        validate_confflow_capabilities(capabilities, require_dag=require_dag)
    except ValueError as exc:
        raise ConfFlowCapabilityPreflightError(f"ConfFlow capability preflight failed: {exc}") from exc
    return capabilities
