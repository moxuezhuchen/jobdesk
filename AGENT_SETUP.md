# confflow-agent Setup Guide

The `confflow-agent` daemon runs on a remote Linux machine (or WSL) and
executes ConfFlow workflows independently of the JobDesk GUI. This guide
covers installation, lifecycle management and systemd integration.

## Two CLIs

There are **two distinct CLIs** that work together:

| CLI | Runs on | Purpose |
|---|---|---|
| **`confflow-agent`** | Remote server (HPC/Linux/WSL) | The agent daemon itself; manages the queue, slots, and job lifecycle |
| **`jobdesk agent …`** | Your local machine (laptop) | Local JobDesk CLI; proxies commands to the remote daemon via SSH/SFTP |

All `confflow-agent …` examples in this guide are commands you run **on the remote server** directly (e.g., via SSH). All `jobdesk agent …` examples are commands you run **locally**; they communicate with the remote over SSH through `AgentBridge`.

## Topology

```
┌─────────────────────────┐    ┌────────────────────────────────────┐
│ JobDesk CLI (your laptop)│    │ confflow-agent host (Linux/WSL)    │
│                         │    │                                    │
│  jobdesk agent …        │    │  confflow-agent serve               │
│   └── SSH/SFTP ─────────┼────┼─► (remote daemon)                  │
│                         │    │  ├── ~/.confflow-queue/             │
│                         │    │  │     ├── incoming/  (job specs)    │
│                         │    │  │     ├── pending/   (worker pool)  │
│                         │    │  │     ├── working/   (running)      │
│                         │    │  │     └── done/      (results)      │
│                         │    │  └── ~/.local/share/confflow-agent/ │
│                         │    │       └── state.db                  │
└─────────────────────────┘    └────────────────────────────────────┘
```

## Install

### Remote side

On the remote host, install the agent package and create required directories:

```bash
# On the remote host (Linux/WSL)
pip install --user "jobdesk[agent]"
mkdir -p ~/.confflow-queue ~/.local/share/confflow-agent ~/.local/log/confflow-agent
```

### Local side

From your laptop, trigger the full install (pip + directories + systemd setup) via `AgentBridge`:

```bash
jobdesk agent install --server <server-id>   # <-- --server is required
jobdesk agent start   --server <server-id>   # or: systemctl --user start confflow-agent
```

`jobdesk agent install` handles installation in 5 tiers — it picks the best available:

| Tier | Trigger | Result |
| ---- | ------- | ------ |
| 1 | `systemctl --user` available | systemd --user unit |
| 2 | `systemctl` (system-wide) | system unit at `/etc/systemd/system/` |
| 3 | XDG autostart | desktop autostart `.desktop` file |
| 4 | `nohup` + `setsid` | detached background process |
| 5 | tmux/screen fallback | ask the user to install one of above |

## CLI quick reference

### Local — `jobdesk agent …` (JobDesk CLI, runs on your laptop)

```bash
jobdesk agent install  --server <id>              # install agent on remote
jobdesk agent start    --server <id>              # start daemon on remote
jobdesk agent stop     --server <id>              # stop daemon on remote
jobdesk agent status   --server <id> [--job-id]  # daemon/job status
jobdesk agent list     --server <id> [--no-all]  # list all jobs
jobdesk agent submit   --server <id> <config> <input_xyz>  # submit a workflow
jobdesk agent pause    --server <id> <job_id>     # pause a job
jobdesk agent resume   --server <id> <job_id>     # resume a paused job
jobdesk agent cancel   --server <id> <job_id>     # cancel a job
jobdesk agent logs     --server <id> [job_id] [--tail N]   # tail logs
jobdesk agent download --server <id> <job_id> <local_dest> [--patterns …]
```

> **Note:** `--server <id>` is required for all `jobdesk agent` subcommands.

### Remote — `confflow-agent …` (direct daemon CLI, runs on the remote server)

```bash
confflow-agent serve                         # start daemon (foreground; systemd entrypoint)
confflow-agent status  <job_id>              # one-shot human-readable status
confflow-agent list    [--no-all]            # list jobs
confflow-agent submit <config> <input_xyz> [--job-id]   # submit a workflow
confflow-agent pause  <job_id>              # pause a job
confflow-agent resume <job_id>              # resume from pause + re-enqueue
confflow-agent cancel <job_id>              # cancel and remove spec
confflow-agent logs   <job_id> [--tail N]  # tail logs
confflow-agent stop                          # pause all running jobs; agent stays running
```

## State directories

| Path | Purpose |
| ---- | ------- |
| `~/.confflow-queue/incoming/` | incoming JobSpec JSON files |
| `~/.confflow-queue/pending/` | picked-up-but-not-yet-started |
| `~/.confflow-queue/working/<job_id>/` | step directories (`PAUSE`, `STOP` beacons) |
| `~/.confflow-queue/done/` | finished job specs |
| `~/.confflow-queue/status/<job_id>.json` | progress / events |
| `~/.local/share/confflow-agent/state.db` | SQLite jobs table |

## Job lifecycle

```
PENDING → RUNNING → DONE
             │
             ├────► PAUSED  (beacon file; daemon monitors child STOP)
             ├────► FAILED  (subprocess raised; traceback recorded)
             └────► CANCELLED (operator request)
```

`pause <job_id>` writes a beacon and stops **the currently running step** at
the next checkpoint (≤ 1 s latency). `cancel <job_id>` removes the spec and
stops the daemon's child if any.

`confflow-agent stop` (and `jobdesk agent stop`) pauses **all running jobs** but
leaves the daemon itself running so new jobs can be submitted.

## JobDesk GUI wiring

- Files page → "Run ConfFlow" → wizard submits via `AgentBridge`.
- "View Agent Jobs" button polls the remote agent via SSH using `confflow-agent list`
  (parsed from human-readable text, not a JSON endpoint) every 5 s.
- Selecting a job brings up Pause / Resume / Cancel / Logs / Download.

## Hardening checklist

- `confflow-agent` reads no files outside `~/.confflow-queue/` and
  `~/.local/share/confflow-agent/`.
- The agent never executes a YAML that wasn't written by the JobDesk wizard
  or signed via the user-trusted preset list. Edit `PRESETS` to extend.
- SSH connection from JobDesk uses parametric `~/.ssh/config` and a
  single key; configure the connection under **Settings → Servers**.
- The default `cores_per_task` and `total_memory` come from
  `jobdesk_app.workflow.shared.defaults` — change them per machine via
  `~/.config/jobdesk/defaults.yaml`.

## Troubleshooting

- **"permission denied" on start** — `~/.confflow-queue` and
  `~/.local/share/confflow-agent` must be writable by the user running the daemon.
- **Daemon not seen by JobDesk** — run `jobdesk agent status --server <id>` locally.
  If it prints "Agent on <server>: stopped", check `journalctl --user -u confflow-agent`
  (systemd tier 1) or `tail ~/.local/log/confflow-agent/agent.log`.
- **PAUSE does nothing** — the daemon only honors `pause` once a step is
  running; for queued jobs, `cancel` is the right call.
