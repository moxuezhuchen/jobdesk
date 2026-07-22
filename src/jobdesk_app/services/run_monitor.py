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
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

from .protocols import SSHClient

_WATCHER_STABLE_SECONDS = 30.0
_MAX_EVENT_LINE_CHARS = 64 * 1024
# Polling cadence for ConfFlow checkpoint content detection. Independent of
# events.log because ConfFlow writes checkpoint files out-of-band and we
# want the Runs page to reflect step progress even before the runner emits
# RUNNING/DONE lines.
_CHECKPOINT_PROBE_SECONDS = 20.0
logger = logging.getLogger(__name__)

_CHECKPOINT_SNAPSHOT_HEADER = "__JD_CHECKPOINT_SNAPSHOT_V1__"
_CHECKPOINT_SNAPSHOT_FOOTER = "__JD_CHECKPOINT_SNAPSHOT_END_V1__"
_CheckpointSnapshot = tuple[tuple[bool, str | None], ...]


def _build_checkpoint_probe_script(progress_paths: Iterable[str]) -> str:
    """Build a read-only probe that emits one complete ordered snapshot."""
    declared = " ".join(shlex.quote(path) for path in progress_paths)
    return (
        "set +e\n"
        'snapshot_tmp=$(mktemp "${TMPDIR:-/tmp}/jobdesk-checkpoint.XXXXXX") || exit 2\n'
        'cleanup_snapshot() { [ -z "$snapshot_tmp" ] || rm -f -- "$snapshot_tmp"; }\n'
        "trap cleanup_snapshot EXIT HUP INT TERM\n"
        "complete=1\n"
        "present=0\n"
        "index=0\n"
        f"for progress_path in {declared}; do\n"
        '  if [ -f "$progress_path" ]; then\n'
        '    digest_line=$(sha256sum -- "$progress_path") || { complete=; break; }\n'
        '    digest=${digest_line%% *}\n'
        '    if [ ! -f "$progress_path" ] || [ "${#digest}" -ne 64 ]; then complete=; break; fi\n'
        '    case "$digest" in *[!0-9a-fA-F]*) complete=; break;; esac\n'
        "    printf '%s\\tpresent\\t%s\\n' \"$index\" \"$digest\" >> \"$snapshot_tmp\" "
        "|| { complete=; break; }\n"
        "    present=1\n"
        "  else\n"
        "    printf '%s\\tmissing\\n' \"$index\" >> \"$snapshot_tmp\" "
        "|| { complete=; break; }\n"
        "  fi\n"
        "  index=$((index + 1))\n"
        "done\n"
        '[ -n "$complete" ] || exit 3\n'
        f"printf '{_CHECKPOINT_SNAPSHOT_HEADER}\\tpresent=%s\\tcount=%s\\n' "
        '"$present" "$index" || exit 4\n'
        'cat -- "$snapshot_tmp" || exit 4\n'
        f"printf '{_CHECKPOINT_SNAPSHOT_FOOTER}\\tcount=%s\\n' \"$index\" || exit 4\n"
    )


def _parse_checkpoint_snapshot(
    stdout: str,
    expected_count: int,
) -> tuple[bool, _CheckpointSnapshot] | None:
    """Parse a complete probe frame, rejecting truncation or inconsistent flags."""
    lines = stdout.splitlines()
    if len(lines) != expected_count + 2:
        return None
    header = lines[0].split("\t")
    if len(header) != 3 or header[0] != _CHECKPOINT_SNAPSHOT_HEADER:
        return None
    if header[1] not in {"present=0", "present=1"} or header[2] != f"count={expected_count}":
        return None
    declared_present = header[1] == "present=1"
    snapshot: list[tuple[bool, str | None]] = []
    for expected_index, line in enumerate(lines[1:-1]):
        fields = line.split("\t")
        if len(fields) < 2 or fields[0] != str(expected_index):
            return None
        if fields[1] == "missing" and len(fields) == 2:
            snapshot.append((False, None))
            continue
        if fields[1] != "present" or len(fields) != 3:
            return None
        digest = fields[2].lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            return None
        snapshot.append((True, digest))
    if lines[-1] != f"{_CHECKPOINT_SNAPSHOT_FOOTER}\tcount={expected_count}":
        return None
    if declared_present != any(present for present, _digest in snapshot):
        return None
    return declared_present, tuple(snapshot)


@dataclass
class DoneEvent:
    run_id: str
    server_id: str
    task_id: str
    exit_code: int | None  # None for RUNNING events
    watch_id: str | None = None


class RunMonitor:
    """Framework-neutral manager for remote event watchers.

    Accepts an optional ``progress_callback`` that fires on ConfFlow
    checkpoint content changes (synthetic event with ``task_id`` starting
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

    def watch(
        self,
        run_id: str,
        server_id: str,
        remote_batch_dir: str,
        server_config: object,
        progress_paths: Iterable[str] = (),
        watch_id: str | None = None,
    ) -> None:
        """Start watching a run's events.log. Idempotent."""
        key = watch_id or f"{server_id}:{run_id}"
        with self._lock:
            if key in self._watchers:
                return
            w = _Watcher(
                run_id,
                server_id,
                remote_batch_dir,
                server_config,
                lambda watched_run_id, watched_server_id, line: self._dispatch(
                    watched_run_id, watched_server_id, line, watch_id
                ),
                self._ssh_factory,
                self._progress_callback,
                progress_paths,
                watch_id,
            )
            self._watchers[key] = w
            try:
                w.start()
            except Exception:
                if self._watchers.get(key) is w:
                    self._watchers.pop(key, None)
                try:
                    w.stop()
                except Exception:
                    logger.debug("failed to clean up watcher after start failure", exc_info=True)
                raise

    def unwatch(self, run_id: str, server_id: str, watch_id: str | None = None) -> None:
        key = watch_id or f"{server_id}:{run_id}"
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

    def _dispatch(self, run_id: str, server_id: str, line: str, watch_id: str | None = None) -> None:
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
                    watch_id=watch_id,
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
        progress_paths: Iterable[str] = (),
        watch_id: str | None = None,
    ) -> None:
        self._run_id = run_id
        self._server_id = server_id
        self._events_path = f"{remote_batch_dir.rstrip('/')}/_batch/events.log"
        self._server_config = server_config
        self._callback = callback
        self._ssh_factory = ssh_factory
        self._progress_callback = progress_callback or (lambda _event: None)
        self._progress_paths = tuple(dict.fromkeys(path for path in progress_paths if path))
        self._watch_id = watch_id
        self._checkpoint_snapshot: _CheckpointSnapshot | None = None
        self._checkpoint_generation = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._cached_ssh: SSHClient | None = None

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
        self._cached_ssh = None
        while not self._stop_event.is_set():
            # Close any leftover SSH from a previous iteration's probe
            # before opening a new tail channel.
            if self._cached_ssh is not None:
                try:
                    self._cached_ssh.close()
                except Exception:
                    pass
                self._cached_ssh = None
            ssh: SSHClient | None = None
            try:
                ssh = self._ssh_factory(self._server_config)
                ssh.connect()
                ssh.run(f"mkdir -p $(dirname {quoted}) && touch {quoted}", timeout=10)
                channel = ssh.open_session()
                channel.exec_command(f"tail -n 0 -f {quoted}")
                channel.settimeout(5.0)
                connected_at = time.monotonic()
                next_checkpoint_probe = connected_at + _CHECKPOINT_PROBE_SECONDS if self._progress_paths else None
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
                            if next_checkpoint_probe is not None:
                                now = time.monotonic()
                                if now >= next_checkpoint_probe:
                                    self._probe_checkpoint(ssh)
                                    next_checkpoint_probe = now + _CHECKPOINT_PROBE_SECONDS
                        except socket.timeout as exc:
                            logger.debug(
                                "watcher %s/%s channel read timeout, continuing: %s",
                                self._server_id,
                                self._run_id,
                                exc,
                            )
                            now = time.monotonic()
                            if now - connected_at >= _WATCHER_STABLE_SECONDS:
                                backoff = 10
                            if next_checkpoint_probe is not None and now >= next_checkpoint_probe:
                                self._probe_checkpoint(ssh)
                                next_checkpoint_probe = now + _CHECKPOINT_PROBE_SECONDS
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

    def _probe_checkpoint(self, ssh: SSHClient | None = None) -> None:
        """Best-effort check whether a declared ConfFlow progress file advanced.

        We inspect only the exact state/statistics paths persisted by the run
        plan. The first probe reports any already-present progress file, then
        atomically stores an ordered snapshot of path presence and content
        digests. Later probes report content/presence changes while ignoring
        mtime-only changes, and fire a synthetic DoneEvent to nudge the GUI
        to refresh. Errors are swallowed — checkpoint probing is
        opportunistic; an incomplete snapshot never replaces the last trusted
        watcher-local snapshot.

        The probe uses the active or most recently cached SSH connection, so
        it does not pay the cost of a fresh connect per
        iteration. When the cached connection is unavailable (initial loop,
        after a drop) the probe is skipped.
        """
        if self._stop_event.is_set() or not self._progress_paths:
            return
        ssh = ssh or self._cached_ssh
        if ssh is None:
            return
        probe_script = _build_checkpoint_probe_script(self._progress_paths)
        try:
            r = ssh.run(probe_script, timeout=10)
            if r.exit_code != 0:
                return
            parsed = _parse_checkpoint_snapshot(r.stdout, len(self._progress_paths))
            if parsed is None:
                logger.debug(
                    "watcher %s/%s ignored incomplete checkpoint snapshot",
                    self._server_id,
                    self._run_id,
                )
                return
            any_present, snapshot = parsed
            previous = self._checkpoint_snapshot
            snapshot_changed = previous != snapshot
            changed = (previous is None and any_present) or (previous is not None and snapshot_changed)
            self._checkpoint_snapshot = snapshot
            if snapshot_changed:
                self._checkpoint_generation += 1
            if not changed:
                return
            logger.debug(
                "watcher %s/%s detected ConfFlow checkpoint change at local generation %d",
                self._server_id,
                self._run_id,
                self._checkpoint_generation,
            )
            self._progress_callback(
                DoneEvent(
                    run_id=self._run_id,
                    server_id=self._server_id,
                    task_id="_ckpt_progress",
                    exit_code=None,
                    watch_id=self._watch_id,
                )
            )
        except Exception as exc:
            logger.debug(
                "watcher %s/%s checkpoint probe failed (ignored): %s",
                self._server_id,
                self._run_id,
                exc,
            )
