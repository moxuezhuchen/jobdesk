"""jobdesk agent subcommand — operates on remote agents via SSH/SFTP.

Usage: jobdesk agent (install|status|start|stop|logs|submit|list|pause|resume|cancel|download) [options]

The agent must be installed on the remote server first (jobdesk agent install).
All subsequent commands communicate with the remote agent over SSH, not via a
local queue directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jobdesk_app.services.agent_bridge import AgentBridge

DEFAULT_QUEUE_DIR = "~/.confflow-queue"
DEFAULT_STATE_DB = "~/.local/share/confflow-agent/state.db"
DEFAULT_LOG_DIR = "~/.local/log/confflow-agent"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def add_parser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "agent",
        help="Manage ConfFlow agents on remote servers",
        description=__doc__,
    )
    p.add_argument(
        "--server", "-s",
        dest="server_id",
        help="Server name from servers.yaml (required for install/start/stop/logs/submit/list/pause/resume/cancel/download)",
    )
    p.set_defaults(func=_cmd_dispatch)
    return p


def _build_agent_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobdesk agent")
    sub = parser.add_subparsers(dest="agent_command", required=True)

    # ---- install ------------------------------------------------------------
    install_p = sub.add_parser("install", help="Install and enable the agent on the remote server")
    install_p.add_argument(
        "--queue-dir", default=DEFAULT_QUEUE_DIR,
        help=f"Remote queue directory (default: {DEFAULT_QUEUE_DIR})",
    )
    install_p.add_argument(
        "--state-db", default=DEFAULT_STATE_DB,
        help=f"Remote state DB path (default: {DEFAULT_STATE_DB})",
    )
    install_p.add_argument(
        "--slots", type=int, default=2,
        help="Number of concurrent agent slots (default: 2)",
    )
    install_p.set_defaults(func=_cmd_install)

    # ---- status ------------------------------------------------------------
    status_p = sub.add_parser("status", help="Show agent daemon status on the remote")
    status_p.add_argument("--job-id", help="Specific job ID to query")
    status_p.set_defaults(func=_cmd_status)

    # ---- start -------------------------------------------------------------
    start_p = sub.add_parser("start", help="Start the agent daemon on the remote (if not already running)")
    start_p.add_argument("--queue-dir", default=DEFAULT_QUEUE_DIR)
    start_p.add_argument("--state-db", default=DEFAULT_STATE_DB)
    start_p.add_argument("--slots", type=int, default=2)
    start_p.set_defaults(func=_cmd_start)

    # ---- stop --------------------------------------------------------------
    stop_p = sub.add_parser("stop", help="Stop the agent daemon on the remote")
    stop_p.set_defaults(func=_cmd_stop)

    # ---- logs --------------------------------------------------------------
    logs_p = sub.add_parser("logs", help="Tail agent logs from the remote")
    logs_p.add_argument("job_id", nargs="?", help="Job ID (optional; shows all logs if omitted)")
    logs_p.add_argument("--tail", type=int, default=50, help="Show last N lines (default: 50)")
    logs_p.set_defaults(func=_cmd_logs)

    # ---- submit ------------------------------------------------------------
    submit_p = sub.add_parser("submit", help="Submit a workflow job to the remote agent")
    submit_p.add_argument("config", help="Path to workflow YAML config (remote path or local path to upload)")
    submit_p.add_argument("input_xyz", help="Path to input XYZ file")
    submit_p.add_argument("--job-id", help="Custom job ID (default: auto-generated)")
    submit_p.set_defaults(func=_cmd_submit)

    # ---- list --------------------------------------------------------------
    list_p = sub.add_parser("list", help="List jobs known to the remote agent")
    list_p.add_argument("--no-all", dest="no_all", action="store_true",
                        help="Show only pending jobs (default: show all)")
    list_p.set_defaults(func=_cmd_list)

    # ---- pause -------------------------------------------------------------
    pause_p = sub.add_parser("pause", help="Pause a running job")
    pause_p.add_argument("job_id", help="Job ID to pause")
    pause_p.set_defaults(func=_cmd_pause)

    # ---- resume ------------------------------------------------------------
    resume_p = sub.add_parser("resume", help="Resume a paused job")
    resume_p.add_argument("job_id", help="Job ID to resume")
    resume_p.set_defaults(func=_cmd_resume)

    # ---- cancel ------------------------------------------------------------
    cancel_p = sub.add_parser("cancel", help="Cancel a job")
    cancel_p.add_argument("job_id", help="Job ID to cancel")
    cancel_p.set_defaults(func=_cmd_cancel)

    # ---- download ----------------------------------------------------------
    download_p = sub.add_parser("download", help="Download job output directory")
    download_p.add_argument("job_id", help="Job ID")
    download_p.add_argument("local_dest", type=Path, help="Local destination directory")
    download_p.add_argument(
        "--patterns", nargs="+", default=["*.xyz", "*.log", "summary.json"],
        help="File patterns to download (default: *.xyz *.log summary.json)",
    )
    download_p.set_defaults(func=_cmd_download)

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _cmd_dispatch(args) -> int:
    """Intercept 'jobdesk agent' and forward to sub-parser."""
    if args.agent_command is None:
        args.parser.print_help()
        return 0

    # The top-level parser already consumed 'agent', re-parse the remaining args
    sub = _build_agent_parser()
    # When called from jobdesk cli, remaining args are in args._agent_args
    remaining = getattr(args, "_agent_args", sys.argv[2:] if hasattr(sys, "argv") else [])
    sub_args = sub.parse_args(remaining)

    # Inject server_id from top-level --server if not overridden
    if args.server_id and not hasattr(sub_args, "server_id"):
        pass  # no server_id needed for this subcommand
    elif args.server_id and getattr(sub_args, "server_id", None) is None:
        sub_args.server_id = args.server_id

    return sub_args.func(sub_args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_bridge(args) -> "AgentBridge":
    from jobdesk_app.services.agent_bridge import AgentBridge
    server_id = getattr(args, "server_id", None)
    if not server_id:
        raise SystemExit("Error: --server <id> is required for this command")
    return AgentBridge(server_id)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_install(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.install_agent(
        queue_dir=args.queue_dir,
        state_db=args.state_db,
        slots=args.slots,
    )
    if result.ok:
        print(f"Agent installed on {args.server_id}: {result.message}")
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_status(args) -> int:
    bridge = _get_bridge(args)
    if getattr(args, "job_id", None):
        result = bridge.get_job_status(args.job_id)
    else:
        result = bridge.get_agent_status()
    if result.ok:
        print(result.message)
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_start(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.start_agent(
        queue_dir=args.queue_dir,
        state_db=args.state_db,
        slots=args.slots,
    )
    if result.ok:
        print(f"Agent started on {args.server_id}")
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_stop(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.stop_agent()
    if result.ok:
        print(f"Agent stopped on {args.server_id}")
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_logs(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.tail_logs(getattr(args, "job_id", None), args.tail)
    if result.ok:
        print(result.message, end="")
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_submit(args) -> int:
    bridge = _get_bridge(args)
    # If config/input_xyz are local paths, upload first
    config_path = Path(args.config)
    input_path = Path(args.input_xyz)
    if config_path.exists() and not config_path.is_absolute():
        raise SystemExit("Local paths must be absolute")
    job_id = getattr(args, "job_id", None)
    result = bridge.submit_job(
        config_remote=args.config,
        input_remote=args.input_xyz,
        job_id=job_id,
    )
    if result.ok:
        print(result.message)
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_list(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.list_jobs(no_all=getattr(args, "no_all", False))
    if result.ok:
        print(result.message or "No jobs found.")
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_pause(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.pause_job(args.job_id)
    if result.ok:
        print(result.message)
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_resume(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.resume_job(args.job_id)
    if result.ok:
        print(result.message)
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_cancel(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.cancel_job(args.job_id)
    if result.ok:
        print(result.message)
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1


def _cmd_download(args) -> int:
    bridge = _get_bridge(args)
    result = bridge.download_job_output(
        args.job_id,
        args.local_dest,
        args.patterns,
    )
    if result.ok:
        print(result.message)
        return 0
    print(f"Error: {result.message}", file=sys.stderr)
    return 1
