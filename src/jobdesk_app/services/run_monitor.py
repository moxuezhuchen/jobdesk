"""RunMonitor — SSH tail -f based real-time task completion listener.

Maintains one SSH connection per server, tailing _batch/events.log.
Emits a signal when a task completes (DONE line received).
"""
from __future__ import annotations

import logging
import shlex
import socket
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

_WATCHER_STABLE_SECONDS = 30.0
logger = logging.getLogger(__name__)


@dataclass
class DoneEvent:
    run_id: str
    server_id: str
    task_id: str
    exit_code: int | None  # None for RUNNING events


class RunMonitor(QObject):
    """Monitors remote events.log via SSH tail -f, emits task_done on completion."""

    task_done = Signal(object)  # DoneEvent

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watchers: dict[str, _Watcher] = {}  # key: "server_id:run_id"
        self._lock = threading.Lock()

    def watch(self, run_id: str, server_id: str, remote_batch_dir: str, server_config):
        """Start watching a run's events.log. Idempotent."""
        key = f"{server_id}:{run_id}"
        with self._lock:
            if key in self._watchers:
                return
            w = _Watcher(run_id, server_id, remote_batch_dir, server_config, self._dispatch)
            self._watchers[key] = w
            w.start()

    def unwatch(self, run_id: str, server_id: str):
        key = f"{server_id}:{run_id}"
        with self._lock:
            w = self._watchers.pop(key, None)
        if w:
            w.stop()

    def stop_all(self):
        with self._lock:
            watchers = list(self._watchers.values())
            self._watchers.clear()
        for w in watchers:
            w.stop()

    def _dispatch(self, run_id: str, server_id: str, line: str):
        """Called from background thread — emit signal (thread-safe via AutoConnection)."""
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] in ("DONE", "RUNNING"):
            task_id = parts[1]
            rc = -1
            if parts[0] == "DONE" and len(parts) >= 3:
                try:
                    rc = int(parts[2])
                except ValueError:
                    rc = -1
            self.task_done.emit(DoneEvent(
                run_id=run_id, server_id=server_id, task_id=task_id,
                exit_code=rc if parts[0] == "DONE" else None,
            ))


class _Watcher:
    """Background thread that SSH tail -f's events.log for one run."""

    def __init__(self, run_id, server_id, remote_batch_dir, server_config, callback):
        self._run_id = run_id
        self._server_id = server_id
        self._events_path = f"{remote_batch_dir.rstrip('/')}/_batch/events.log"
        self._server_config = server_config
        self._callback = callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run(self):
        from ..gui.session import create_ssh_client
        quoted = shlex.quote(self._events_path)
        backoff = 10
        while not self._stop_event.is_set():
            ssh = None
            try:
                ssh = create_ssh_client(self._server_config)
                ssh.connect()
                ssh.run(f"mkdir -p $(dirname {quoted}) && touch {quoted}", timeout=10)
                channel = ssh.open_session()
                channel.exec_command(f"tail -n 0 -f {quoted}")
                channel.settimeout(5.0)
                connected_at = time.monotonic()
                try:
                    while not self._stop_event.is_set():
                        try:
                            data = channel.recv(4096)
                            if not data:
                                break
                            backoff = 10
                            for line in data.decode("utf-8", errors="replace").splitlines():
                                if line.strip():
                                    self._callback(self._run_id, self._server_id, line)
                        except socket.timeout as exc:
                            logger.debug(
                                "watcher %s/%s channel read timeout, continuing: %s",
                                self._server_id, self._run_id, exc,
                            )
                            if time.monotonic() - connected_at >= _WATCHER_STABLE_SECONDS:
                                backoff = 10
                            continue
                        except Exception as exc:
                            logger.debug(
                                "watcher %s/%s channel read error, reconnecting: %s",
                                self._server_id, self._run_id, exc,
                            )
                            break
                finally:
                    channel.close()
                    ssh.close()
                    ssh = None
            except Exception as exc:
                logger.warning(
                    "watcher %s/%s connection lost, reconnecting in %ds: %s",
                    self._server_id, self._run_id, backoff, exc,
                )
                if ssh:
                    try:
                        ssh.close()
                    except Exception:
                        pass
            self._stop_event.wait(backoff)
            backoff = min(backoff * 2, 120)
