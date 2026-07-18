"""RunMonitor — SSH tail -f based real-time task completion listener.

Maintains one SSH connection per server, tailing _batch/events.log.
Emits a signal when a task completes (DONE line received).
"""

from __future__ import annotations

import codecs
import logging
import shlex
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .protocols import SSHClient

_WATCHER_STABLE_SECONDS = 30.0
_MAX_EVENT_LINE_CHARS = 64 * 1024
# Polling cadence for ConfFlow checkpoint mtime detection. Independent of
# events.log because ConfFlow writes checkpoint files out-of-band and we
# want the Runs page to reflect step progress even before the runner emits
# RUNNING/DONE lines.
_CHECKPOINT_PROBE_SECONDS = 20.0
logger = logging.getLogger(__name__)


@dataclass
class DoneEvent:
    run_id: str
    server_id: str
    task_id: str
    exit_code: int | None  # None for RUNNING events


class RunMonitor:
    """Framework-neutral manager for remote event watchers.

    Accepts an optional ``progress_callback`` that fires on ConfFlow
    checkpoint mtime changes (synthetic event with ``task_id`` starting
    with ``_ckpt_`` and ``exit_code=None``). The GUI bridges it into the
    same debounced refresh path used by ``DoneEvent`` so the Runs page
    updates without waiting for the next DONE/RUNNING line in
    ``events.log``.
    """

    def __init__(
        self,
        ssh_factory: Callable[[object], SSHClient],
        callback: Callable[[DoneEvent], None],
        progress_callback: Callable[[DoneEvent], None] | None = None,
    ) -> None:
        self._ssh_factory = ssh_factory
        self._callback = callback
        self._progress_callback = progress_callback or callback
        self._watchers: dict[str, _Watcher] = {}  # key: "server_id:run_id"
        self._lock = threading.Lock()

    def watch(self, run_id: str, server_id: str, remote_batch_dir: str, server_config: object) -> None:
        """Start watching a run's events.log. Idempotent."""
        key = f"{server_id}:{run_id}"
        with self._lock:
            if key in self._watchers:
                return
            w = _Watcher(
                run_id,
                server_id,
                remote_batch_dir,
                server_config,
                self._dispatch,
                self._ssh_factory,
                self._progress_callback,
            )
            self._watchers[key] = w
            w.start()

    def unwatch(self, run_id: str, server_id: str) -> None:
        key = f"{server_id}:{run_id}"
        with self._lock:
            w = self._watchers.pop(key, None)
        if w:
            w.stop()

    def stop_all(self) -> None:
        with self._lock:
            watchers = list(self._watchers.values())
            self._watchers.clear()
        for w in watchers:
            w.stop()

    def _dispatch(self, run_id: str, server_id: str, line: str) -> None:
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
            self._callback(
                DoneEvent(
                    run_id=run_id,
                    server_id=server_id,
                    task_id=task_id,
                    exit_code=rc if parts[0] == "DONE" else None,
                )
            )


class _Watcher:
    """Background thread that SSH tail -f's events.log for one run."""

    def __init__(
        self,
        run_id: str,
        server_id: str,
        remote_batch_dir: str,
        server_config: object,
        callback: Callable[[str, str, str], None],
        ssh_factory: Callable[[object], SSHClient],
        progress_callback: Callable[[DoneEvent], None] | None = None,
    ) -> None:
        self._run_id = run_id
        self._server_id = server_id
        self._events_path = f"{remote_batch_dir.rstrip('/')}/_batch/events.log"
        self._server_config = server_config
        self._callback = callback
        self._ssh_factory = ssh_factory
        self._progress_callback = progress_callback or (lambda _event: None)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        quoted = shlex.quote(self._events_path)
        backoff = 10
        # Cached SSH client kept alive across iterations so the checkpoint
        # probe doesn't pay the cost of a fresh connection every loop.
        # Closed when the watcher stops or the connection drops.
        self._cached_ssh: object | None = None
        while not self._stop_event.is_set():
            # Close any leftover SSH from a previous iteration's probe
            # before opening a new tail channel.
            if self._cached_ssh is not None:
                try:
                    self._cached_ssh.close()
                except Exception:
                    pass
                self._cached_ssh = None
            ssh = None
            try:
                ssh = self._ssh_factory(self._server_config)
                ssh.connect()
                ssh.run(f"mkdir -p $(dirname {quoted}) && touch {quoted}", timeout=10)
                channel = ssh.open_session()
                channel.exec_command(f"tail -n 0 -f {quoted}")
                channel.settimeout(5.0)
                connected_at = time.monotonic()
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                line_parts: list[str] = []
                line_length = 0
                discarding_line = False
                try:
                    while not self._stop_event.is_set():
                        try:
                            data = channel.recv(4096)
                            if self._stop_event.is_set():
                                break
                            if not data:
                                break
                            backoff = 10
                            decoded = decoder.decode(data)
                            while decoded and not self._stop_event.is_set():
                                fragment, separator, decoded = decoded.partition("\n")
                                if discarding_line:
                                    if separator:
                                        discarding_line = False
                                    else:
                                        break
                                    continue
                                if line_length + len(fragment) > _MAX_EVENT_LINE_CHARS:
                                    logger.warning(
                                        "watcher %s/%s discarded oversized event line",
                                        self._server_id,
                                        self._run_id,
                                    )
                                    line_parts.clear()
                                    line_length = 0
                                    discarding_line = not separator
                                    continue
                                if fragment:
                                    line_parts.append(fragment)
                                    line_length += len(fragment)
                                if not separator:
                                    break
                                line = "".join(line_parts).removesuffix("\r")
                                line_parts.clear()
                                line_length = 0
                                if line.strip():
                                    self._callback(self._run_id, self._server_id, line)
                        except socket.timeout as exc:
                            logger.debug(
                                "watcher %s/%s channel read timeout, continuing: %s",
                                self._server_id,
                                self._run_id,
                                exc,
                            )
                            if time.monotonic() - connected_at >= _WATCHER_STABLE_SECONDS:
                                backoff = 10
                            continue
                        except Exception as exc:
                            logger.debug(
                                "watcher %s/%s channel read error, reconnecting: %s",
                                self._server_id,
                                self._run_id,
                                exc,
                            )
                            break
                finally:
                    channel.close()
                    # Keep the underlying SSH client open so the next
                    # checkpoint probe can run on the same connection. We
                    # close it when the *next* main-loop iteration takes
                    # over (see top of the try block).
                    self._cached_ssh = ssh
            except Exception as exc:
                logger.warning(
                    "watcher %s/%s connection lost, reconnecting in %ds: %s",
                    self._server_id,
                    self._run_id,
                    backoff,
                    exc,
                )
                if ssh:
                    try:
                        ssh.close()
                    except Exception:
                        pass
                self._cached_ssh = None
            self._stop_event.wait(backoff)
            backoff = min(backoff * 2, 120)
            # Periodic checkpoint probe — independent of events.log so the
            # Runs page can pick up ConfFlow step progress between DONE
            # lines. Emits a DoneEvent with exit_code=None and a special
            # task_id so the consumer can trigger a status refresh without
            # treating it as a real completion.
            self._probe_checkpoint()
        # Loop exited (stop_event set). Close any cached SSH.
        if self._cached_ssh is not None:
            try:
                self._cached_ssh.close()
            except Exception:
                pass
            self._cached_ssh = None

    def _probe_checkpoint(self) -> None:
        """Best-effort check that the ConfFlow checkpoint dir advanced.

        We invoke ``find <work_dir> -name workflow_stats.json -newer sentinel
        -print`` once per loop iteration. ``sentinel`` is an empty marker
        file we touch on first probe, so subsequent probes only flag a
        change. If any new file appears, we fire a synthetic DoneEvent to
        nudge the GUI to refresh. Errors are swallowed — checkpoint probing
        is opportunistic.

        The probe reuses the most recent live SSH connection (cached by the
        main loop) so it does not pay the cost of a fresh connect per
        iteration. When the cached connection is unavailable (initial loop,
        after a drop) the probe is skipped.
        """
        if self._stop_event.is_set():
            return
        ssh = getattr(self, "_cached_ssh", None)
        if ssh is None:
            return
        probe_script = (
            "set +e\n"
            f"marker={shlex.quote(self._events_path.rsplit('/', 1)[0])}/.jobdesk_checkpoint_marker\n"
            '[ -f "$marker" ] || touch "$marker"\n'
            f"updated=$(find {shlex.quote(self._events_path.rsplit('/', 1)[0])} "
            r'\( -name workflow_stats.json -o -name .workflow_state.json \) '
            '-newer "$marker" -print -quit 2>/dev/null)\n'
            'touch "$marker"\n'
            "if [ -n \"$updated\" ]; then printf '__JD_CHECKPOINT_CHANGED__\\n'; fi\n"
        )
        try:
            r = ssh.run(probe_script, timeout=10)
            if r.exit_code == 0 and "__JD_CHECKPOINT_CHANGED__" in r.stdout:
                logger.debug(
                    "watcher %s/%s detected ConfFlow checkpoint change",
                    self._server_id,
                    self._run_id,
                )
                self._progress_callback(
                    DoneEvent(
                        run_id=self._run_id,
                        server_id=self._server_id,
                        task_id="_ckpt_progress",
                        exit_code=None,
                    )
                )
        except Exception as exc:
            logger.debug(
                "watcher %s/%s checkpoint probe failed (ignored): %s",
                self._server_id,
                self._run_id,
                exc,
            )
