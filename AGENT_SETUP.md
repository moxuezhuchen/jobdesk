# confflow-agent Setup Guide

The `confflow-agent` daemon runs on a remote Linux machine (or WSL) and
executes ConfFlow workflows independently of the JobDesk GUI. This guide
covers installation, lifecycle management and systemd integration.

## Topology

```
┌─────────────────────────┐    ┌────────────────────────────────────┐
│ JobDesk GUI (Windows)   │    │ confflow-agent host (Linux/WSL)    │
│                         │    │                                    │
│  Files page             │    │  systemd unit                      │
│   ├── SSH (paramiko) ───┼────┼─► confflow-agent serve             │
│   ├── SFTP upload YAML  │    │  ├── queue/incoming  (job specs)   │
│   └── View Agent Jobs   │    │  ├── queue/pending   (worker pool) │
│                         │    │  ├── queue/working   (running)     │
│                         │    │  ├── queue/done      (results)     │
│                         │    │  └── ~/.local/share/jobdesk/agent/ │
└─────────────────────────┘    └────────────────────────────────────┘
```

## Install

```bash
# On the remote host (Linux/WSL)
pip install --user "jobdesk[agent]"
jobdesk agent install     # sets up systemd unit if available
jobdesk agent start       # ad-hoc; or `systemctl --user start confflow-agent`
```

`install_agent.sh` handles the install in 5 tiers:

| Tier | Trigger                                | Result                                |
| ---- | -------------------------------------- | ------------------------------------- |
| 1    | `systemctl --user` available           | systemd --user unit                   |
| 2    | `systemctl` (system-wide)              | system unit at /etc/systemd/system/   |
| 3    | XDG autostart                          | desktop autostart .desktop file       |
| 4    | `nohup` + `setsid`                     | detached background process           |
| 5    | tmux/screen fallback                   | ask the user to install one of above |

## CLI quick reference

```bash
jobdesk agent serve                  # start daemon (foreground for systemd)
jobdesk agent status                 # one-shot JSON status
jobdesk agent list                   # list jobs
jobdesk agent submit <yaml> <xyz>    # submit a workflow
jobdesk agent pause  <job_id>        # pause mid-run
jobdesk agent resume <job_id>        # resume from pause + re-enqueue
jobdesk agent cancel <job_id>        # cancel and remove spec
jobdesk agent logs   <job_id>        # tail logs
jobdesk agent stop                   # graceful shutdown
```

## State directories

| Path                                                      | Purpose                              |
| --------------------------------------------------------- | ------------------------------------ |
| `~/.local/share/jobdesk/agent/queue/incoming/`            | incoming JobSpec JSON files          |
| `~/.local/share/jobdesk/agent/queue/pending/`             | picked-up-but-not-yet-started        |
| `~/.local/share/jobdesk/agent/queue/working/<job_id>/`    | step directories (`PAUSE`, `STOP`)   |
| `~/.local/share/jobdesk/agent/queue/done/`                | finished job specs                   |
| `~/.local/share/jobdesk/agent/queue/status/<job_id>.json` | progress / events                    |
| `~/.local/share/jobdesk/agent/state.db`                   | SQLite jobs table + idx_jobs_status  |

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

## JobDesk GUI wiring

* Files page → "Run ConfFlow" → wizard submits via AgentBridge.
* "View Agent Jobs" button polls `jobdesk agent status` every 5 s.
* Selecting a job brings up Pause / Resume / Cancel / Logs / Download.

## Hardening checklist

* `confflow-agent` reads no files outside `~/.local/share/jobdesk/agent/`.
* The agent never executes a YAML that wasn't written by the JobDesk wizard
  or signed via the user-trusted preset list. Edit `PRESETS` to extend.
* `ssh-agent` connection from JobDesk uses parametric `~/.ssh/config` and a
  single key; configure the connection under **Settings → Servers**.
* The default `cores_per_task` and `total_memory` come from
  `jobdesk_app.workflow.shared.defaults` — change them per machine via
  `~/.config/jobdesk/defaults.yaml`.

## Troubleshooting

* **"permission denied" on start** — `~/.local/share/jobdesk/agent` must be
  writable by the user running the daemon.
* **Daemon not seen by JobDesk** — `jobdesk agent status` should print JSON.
  If not, check `journalctl --user -u confflow-agent` (systemd tier 1).
* **PAUSE does nothing** — the daemon only honors `pause` once a step is
  running; for queued jobs, `cancel` is the right call.
