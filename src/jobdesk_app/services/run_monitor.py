"""RunMonitor — SSH tail -f based real-time task completion listener.

Maintains one SSH connection per server, tailing _batch/events.log.
Emits a signal when a task completes (DONE line received).

Writes:
- events.log: touch only (read-only tail consumers). No writes from this module.
- mktemp under remote scratch (``${TMPDIR:-/tmp}/jobdesk-checkpoint.XXXXXX``):
  created by the probe, removed via EXIT trap; never persists beyond a single
  checkpoint probe.
"""

from __future__ import annotations

import logging
import math
import random
import shlex
import socket
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from .protocols import SSHClient

_WATCHER_STABLE_SECONDS = 30.0
_MAX_EVENT_LINE_CHARS = 64 * 1024
# Polling cadence for ConfFlow checkpoint content detection. Independent of
# events.log because ConfFlow writes checkpoint files out-of-band and we
# want the Runs page to reflect step progress even before the runner emits
# RUNNING/DONE lines.
_CHECKPOINT_PROBE_SECONDS = 20.0
_WATCHER_STOP_JOIN_SECONDS = 6.0
# The service is also consumed directly by non-Qt callers.  Keep a bounded
# budget here instead of relying on the GUI adapter to provide one.  Passing
# ``None`` explicitly remains an opt-out for a caller that owns a different
# resource budget.
DEFAULT_MAX_WATCHERS = 16
DEFAULT_MAX_WATCHERS_PER_SERVER = 4
# A provider is allowed to have a blocking close implementation (for
# example, while a Paramiko transport waits for a broken socket).  Closing in
# a daemon worker keeps the watcher shutdown path bounded; the worker itself
# is deliberately not reused for another transport.  A second close worker is
# never launched while the first one is still in flight: opening another
# transport is fail-closed until the provider finishes closing the old one.
_TRANSPORT_CLOSE_WAIT_SECONDS = 1.0


def _full_jitter(base_delay: float) -> float:
    """Return a default full-jitter delay in the inclusive [0, base] range."""
    return random.uniform(0.0, base_delay)


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
        "    digest=${digest_line%% *}\n"
        '    if [ ! -f "$progress_path" ] || [ "${#digest}" -ne 64 ]; then complete=; break; fi\n'
        '    case "$digest" in *[!0-9a-fA-F]*) complete=; break;; esac\n'
        '    printf \'%s\\tpresent\\t%s\\n\' "$index" "$digest" >> "$snapshot_tmp" '
        "|| { complete=; break; }\n"
        "    present=1\n"
        "  else\n"
        '    printf \'%s\\tmissing\\n\' "$index" >> "$snapshot_tmp" '
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


class MonitorTransportProvider(Protocol):
    """Provider for the long-lived SSH transport used by a watcher.

    The monitor deliberately owns the lifetime of a transport opened through
    this interface.  Implementations must not borrow a ``SessionPool`` lease:
    a tail channel is long-lived and would otherwise starve short operations.
    """

    def open(self, server_config: object) -> SSHClient:
        """Open a monitor transport for one server."""
        ...

    def close(self, ssh: SSHClient) -> None:
        """Close a transport opened by :meth:`open`."""
        ...


@dataclass(frozen=True, slots=True)
class _WatcherCloseMetrics:
    """Credential-free immutable state for one transport-close worker."""

    close_in_flight: bool
    close_timeouts: int
    close_worker_launches: int


@dataclass(frozen=True, slots=True)
class MonitorMetrics:
    """Immutable, credential-free monitor capacity snapshot."""

    active: int
    queued: int
    reconnecting: int
    rejected: int
    close_in_flight: int = 0
    close_timeouts: int = 0
    close_worker_launches: int = 0


class WatchRejectedError(RuntimeError):
    """Raised when a watch cannot be admitted to the bounded monitor queue."""

    def __init__(self, server_id: str, watch_id: str, reason: str) -> None:
        self.server_id = server_id
        self.watch_id = watch_id
        self.reason = reason
        super().__init__(f"watch {watch_id!r} for server {server_id!r} rejected: {reason}")


@dataclass(frozen=True, slots=True)
class _WatchRequest:
    run_id: str
    server_id: str
    remote_batch_dir: str
    server_config: object
    progress_paths: tuple[str, ...]
    watch_id: str
    event_watch_id: str | None


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
        *,
        transport_provider: MonitorTransportProvider | None = None,
        max_watchers: int | None = DEFAULT_MAX_WATCHERS,
        max_watchers_per_server: int | None = DEFAULT_MAX_WATCHERS_PER_SERVER,
        queue_capacity: int = 0,
        idle_expiry_seconds: float | None = None,
        backoff_jitter: Callable[[float], float] | None = _full_jitter,
    ) -> None:
        if max_watchers is not None and max_watchers < 1:
            raise ValueError("max_watchers must be positive or None")
        if max_watchers_per_server is not None and max_watchers_per_server < 1:
            raise ValueError("max_watchers_per_server must be positive or None")
        if queue_capacity < 0:
            raise ValueError("queue_capacity must be non-negative")
        if idle_expiry_seconds is not None and idle_expiry_seconds <= 0:
            raise ValueError("idle_expiry_seconds must be positive or None")
        self._ssh_factory = ssh_factory
        self._transport_provider = transport_provider
        self._callback = callback
        self._progress_callback = progress_callback or callback
        self._watchers: dict[str, _Watcher] = {}  # key: "server_id:run_id"
        self._stopping: dict[str, _Watcher] = {}
        self._watch_requests: deque[_WatchRequest] = deque()
        self._reconnecting: set[str] = set()
        self._rejected = 0
        self._max_watchers = max_watchers
        self._max_watchers_per_server = max_watchers_per_server
        self._queue_capacity = queue_capacity
        self._idle_expiry_seconds = idle_expiry_seconds
        self._backoff_jitter = backoff_jitter
        self._lock = threading.Lock()

    @property
    def metrics(self) -> MonitorMetrics:
        """Return a point-in-time immutable snapshot without credentials."""
        with self._lock:
            close_metrics = [
                watcher.close_metrics
                for watcher in (*self._watchers.values(), *self._stopping.values())
                if isinstance(getattr(watcher, "close_metrics", None), _WatcherCloseMetrics)
            ]
            return MonitorMetrics(
                active=len(self._watchers) + len(self._stopping),
                queued=len(self._watch_requests),
                reconnecting=len(self._reconnecting),
                rejected=self._rejected,
                close_in_flight=sum(metric.close_in_flight for metric in close_metrics),
                close_timeouts=sum(metric.close_timeouts for metric in close_metrics),
                close_worker_launches=sum(metric.close_worker_launches for metric in close_metrics),
            )

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
        request = _WatchRequest(
            run_id=run_id,
            server_id=server_id,
            remote_batch_dir=remote_batch_dir,
            server_config=server_config,
            progress_paths=tuple(dict.fromkeys(path for path in progress_paths if path)),
            watch_id=key,
            event_watch_id=watch_id,
        )
        with self._lock:
            if (
                key in self._watchers
                or key in self._stopping
                or any(item.watch_id == key for item in self._watch_requests)
            ):
                return
            if self._has_capacity_locked(server_id):
                self._start_request_locked(request)
                return
            if len(self._watch_requests) >= self._queue_capacity:
                self._rejected += 1
                raise WatchRejectedError(server_id, key, "watcher capacity and queue are full")
            self._watch_requests.append(request)

    def unwatch(self, run_id: str, server_id: str, watch_id: str | None = None) -> None:
        key = watch_id or f"{server_id}:{run_id}"
        with self._lock:
            w = self._watchers.pop(key, None)
            if w is None:
                self._watch_requests = deque(item for item in self._watch_requests if item.watch_id != key)
            else:
                self._stopping[key] = w
                self._reconnecting.discard(key)
        if w:
            try:
                # Request stop/close, but never wait for a watcher or its
                # transport close worker from the caller (often the Qt
                # thread).  The stopping map keeps the capacity reserved
                # until the watcher thread invokes ``_watcher_finished``.
                w.request_stop()
            except Exception:
                # A failed request must remain fail-closed: the watcher is
                # still in ``_stopping`` and can only release its slot after
                # its thread has actually exited.
                logger.warning("failed to request monitor watcher shutdown", exc_info=True)
        else:
            with self._lock:
                self._drain_queue_locked()

    def stop_all(self) -> None:
        with self._lock:
            watchers = list(self._watchers.values())
            self._stopping.update({key: watcher for key, watcher in self._watchers.items()})
            self._watchers.clear()
            self._watch_requests.clear()
            self._reconnecting.clear()
        for w in watchers:
            try:
                # Do not join here.  Each watcher remains in ``_stopping``
                # until its own thread's stop callback confirms exit.
                w.request_stop()
            except Exception:
                logger.warning("failed to request monitor watcher shutdown", exc_info=True)

    def _has_capacity_locked(self, server_id: str) -> bool:
        active_count = len(self._watchers) + len(self._stopping)
        if self._max_watchers is not None and active_count >= self._max_watchers:
            return False
        if self._max_watchers_per_server is not None:
            active_for_server = sum(
                1 for watcher in (*self._watchers.values(), *self._stopping.values()) if watcher.server_id == server_id
            )
            if active_for_server >= self._max_watchers_per_server:
                return False
        return True

    def _start_request_locked(self, request: _WatchRequest) -> None:
        key = request.watch_id
        watcher_ref: list[_Watcher | None] = [None]
        watcher = _Watcher(
            request.run_id,
            request.server_id,
            request.remote_batch_dir,
            request.server_config,
            lambda watched_run_id, watched_server_id, line: self._dispatch(
                watched_run_id, watched_server_id, line, request.watch_id
            ),
            self._ssh_factory,
            self._progress_callback,
            request.progress_paths,
            request.event_watch_id,
            transport_provider=self._transport_provider,
            state_callback=lambda reconnecting: self._set_reconnecting(key, reconnecting),
            stop_callback=lambda: self._watcher_finished(key, watcher_ref[0]),
            idle_expiry_seconds=self._idle_expiry_seconds,
            backoff_jitter=self._backoff_jitter,
        )
        watcher_ref[0] = watcher
        self._watchers[key] = watcher
        try:
            watcher.start()
        except Exception:
            if self._watchers.get(key) is watcher:
                self._watchers.pop(key, None)
            self._reconnecting.discard(key)
            try:
                watcher.stop()
            except Exception:
                logger.debug("failed to clean up watcher after start failure", exc_info=True)
            raise

    def _watcher_finished(self, key: str, expected: _Watcher | None) -> None:
        """Release a slot only after the watcher thread has actually ended."""
        with self._lock:
            active = self._watchers.get(key)
            stopping = self._stopping.get(key)
            if expected is not None and active is not expected and stopping is not expected:
                return
            if active is expected or (expected is None and active is not None):
                self._watchers.pop(key, None)
            if stopping is expected or (expected is None and stopping is not None):
                self._stopping.pop(key, None)
            self._reconnecting.discard(key)
            self._drain_queue_locked()

    def _drain_queue_locked(self) -> None:
        while self._watch_requests:
            selected_index: int | None = None
            for index, request in enumerate(self._watch_requests):
                if self._has_capacity_locked(request.server_id):
                    selected_index = index
                    break
            if selected_index is None:
                return
            request = self._watch_requests[selected_index]
            del self._watch_requests[selected_index]
            try:
                self._start_request_locked(request)
            except Exception:
                self._rejected += 1
                logger.warning("queued monitor watcher %s failed to start", request.watch_id, exc_info=True)

    def _set_reconnecting(self, watch_id: str, reconnecting: bool) -> None:
        with self._lock:
            if watch_id not in self._watchers:
                return
            if reconnecting:
                self._reconnecting.add(watch_id)
            else:
                self._reconnecting.discard(watch_id)

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
        *,
        transport_provider: MonitorTransportProvider | None = None,
        state_callback: Callable[[bool], None] | None = None,
        stop_callback: Callable[[], None] | None = None,
        idle_expiry_seconds: float | None = None,
        backoff_jitter: Callable[[float], float] | None = _full_jitter,
    ) -> None:
        self._run_id = run_id
        self._server_id = server_id
        self._events_path = f"{remote_batch_dir.rstrip('/')}/_batch/events.log"
        self._server_config = server_config
        self._callback = callback
        self._ssh_factory = ssh_factory
        self._transport_provider = transport_provider
        self._state_callback = state_callback or (lambda _reconnecting: None)
        self._stop_callback = stop_callback or (lambda: None)
        self._idle_expiry_seconds = idle_expiry_seconds
        self._backoff_jitter = backoff_jitter
        self._progress_callback = progress_callback or (lambda _event: None)
        self._progress_paths = tuple(dict.fromkeys(path for path in progress_paths if path))
        self._watch_id = watch_id
        self._checkpoint_snapshot: _CheckpointSnapshot | None = None
        self._checkpoint_generation = 0
        self._stop_event = threading.Event()
        self._transport_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._active_ssh: SSHClient | None = None
        self._cached_ssh: SSHClient | None = None
        self._close_in_flight = False
        self._close_timeouts = 0
        self._close_worker_launches = 0
        # Byte offset in events.log. ``None`` means that the initial tail
        # position is not known (for example when the remote size probe
        # failed), so reconnects conservatively resume from EOF.
        self._stream_cursor: int | None = None
        self._stream_pending = bytearray()
        self._discarding_line = False
        # Lines emitted in the current logical log generation.  A rotation
        # replays the new file from byte zero; counts (rather than a set) keep
        # repeated, legitimate event lines distinct while suppressing only
        # the occurrences that were already delivered before the rotation.
        self._seen_lines: Counter[str] = Counter()
        self._rotation_dedup: Counter[str] | None = None
        self._rotation_replay_remaining = 0

    @property
    def server_id(self) -> str:
        """Stable, non-sensitive identity used by the admission controller."""
        return self._server_id

    @property
    def close_metrics(self) -> _WatcherCloseMetrics:
        """Return immutable, credential-free transport-close state."""
        with self._transport_lock:
            return _WatcherCloseMetrics(
                close_in_flight=self._close_in_flight,
                close_timeouts=self._close_timeouts,
                close_worker_launches=self._close_worker_launches,
            )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> bool:
        deadline = time.monotonic() + _WATCHER_STOP_JOIN_SECONDS
        self._stop_event.set()
        self._close_current_transport(deadline=deadline)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
            return not thread.is_alive()
        return True

    def request_stop(self) -> None:
        """Request shutdown without waiting for the watcher thread.

        The manager uses this method from UI-facing cancellation paths.  It
        sets the stop flag and asks the transport provider to close through
        the existing single-flight close worker, but deliberately performs
        no thread join and does not wait for a provider close.  The manager
        keeps the watcher capacity reserved until ``_run`` invokes its stop
        callback, so a blocked watcher cannot be replaced prematurely.
        """
        self._stop_event.set()
        self._close_current_transport(wait=False)

    def _run(self) -> None:
        try:
            self._run_loop()
        finally:
            try:
                self._stop_callback()
            except Exception:
                logger.debug("monitor stop callback failed", exc_info=True)

    def _run_loop(self) -> None:
        quoted = shlex.quote(self._events_path)
        backoff = 10
        last_activity: float | None = None
        # Cached SSH client kept alive across iterations so the checkpoint
        # probe doesn't pay the cost of a fresh connection every loop.
        # Closed when the watcher stops or the connection drops.
        self._cached_ssh = None
        while not self._stop_event.is_set():
            # Close any leftover SSH from a previous iteration's probe
            # before opening a new tail channel.
            if self._cached_ssh is not None:
                self._close_current_transport()
                self._cached_ssh = None
            ssh: SSHClient | None = None
            try:
                self._set_reconnecting(True)
                ssh = self._open_ssh()
                self._set_active_transport(ssh)
                if self._stop_event.is_set():
                    self._close_current_transport()
                    break
                ssh.connect()
                ssh.run(f"mkdir -p $(dirname {quoted}) && touch {quoted}", timeout=10)
                file_size = self._remote_file_size(ssh, quoted)
                tail_command = self._tail_command(quoted, file_size)
                channel = ssh.open_session()
                channel.exec_command(tail_command)
                channel.settimeout(5.0)
                self._set_reconnecting(False)
                connected_at = time.monotonic()
                if self._idle_expiry_seconds is not None:
                    last_activity = connected_at
                next_checkpoint_probe = connected_at + _CHECKPOINT_PROBE_SECONDS if self._progress_paths else None
                # Any unconfirmed partial line is replayed from the byte
                # cursor on reconnect. Never carry parser bytes across a new
                # tail channel, or the replay would be duplicated in memory.
                self._stream_pending.clear()
                self._discarding_line = False
                try:
                    while not self._stop_event.is_set():
                        try:
                            data = channel.recv(4096)
                            if self._stop_event.is_set():
                                break
                            if not data:
                                break
                            if last_activity is not None:
                                last_activity = time.monotonic()
                            backoff = 10
                            self._consume_stream_data(data)
                            if next_checkpoint_probe is not None:
                                now = time.monotonic()
                                if now >= next_checkpoint_probe:
                                    if self._probe_checkpoint(ssh) and last_activity is not None:
                                        last_activity = time.monotonic()
                                    next_checkpoint_probe = now + _CHECKPOINT_PROBE_SECONDS
                            if self._idle_expired(last_activity):
                                self._expire_idle()
                                break
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
                                if self._probe_checkpoint(ssh) and last_activity is not None:
                                    last_activity = time.monotonic()
                                next_checkpoint_probe = now + _CHECKPOINT_PROBE_SECONDS
                            if self._idle_expired(last_activity):
                                self._expire_idle()
                                break
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
                self._set_reconnecting(True)
                logger.warning(
                    "watcher %s/%s connection lost, reconnecting in %ds: %s",
                    self._server_id,
                    self._run_id,
                    backoff,
                    exc,
                )
                if ssh:
                    self._close_current_transport()
                self._cached_ssh = None
            if self._stop_event.is_set():
                break
            if not self._stop_event.is_set():
                self._set_reconnecting(True)
            delay = self._jittered_backoff(backoff)
            if last_activity is not None and self._idle_expiry_seconds is not None:
                remaining = self._idle_expiry_seconds - (time.monotonic() - last_activity)
                if remaining <= 0:
                    self._expire_idle()
                    break
                delay = min(delay, remaining)
            self._stop_event.wait(delay)
            if self._idle_expired(last_activity):
                self._expire_idle()
                break
            backoff = min(backoff * 2, 120)
            # Periodic checkpoint probe — independent of events.log so the
            # Runs page can pick up ConfFlow step progress between DONE
            # lines. Emits a DoneEvent with exit_code=None and a special
            # task_id so the consumer can trigger a status refresh without
            # treating it as a real completion.
            if self._probe_checkpoint() and last_activity is not None:
                last_activity = time.monotonic()
        # Loop exited (stop_event set). Close any cached SSH.
        if self._cached_ssh is not None:
            self._close_current_transport()
            self._cached_ssh = None
        self._set_reconnecting(False)

    def _consume_stream_data(self, data: bytes) -> None:
        """Consume complete newline frames and confirm their byte cursor.

        Bytes belonging to a partial frame remain unconfirmed. A reconnect
        resets this parser and asks ``tail`` to replay from that cursor, so a
        split UTF-8 sequence or partial DONE line cannot be silently lost.
        """
        self._stream_pending.extend(data)
        while self._stream_pending and not self._stop_event.is_set():
            if self._discarding_line:
                newline = self._stream_pending.find(b"\n")
                if newline < 0:
                    self._stream_pending.clear()
                    return
                del self._stream_pending[: newline + 1]
                if self._stream_cursor is not None:
                    self._stream_cursor += newline + 1
                self._advance_rotation_replay(newline + 1)
                self._clear_rotation_replay_if_done()
                self._discarding_line = False
                continue

            newline = self._stream_pending.find(b"\n")
            if newline < 0:
                if len(self._stream_pending) > _MAX_EVENT_LINE_CHARS:
                    logger.warning(
                        "watcher %s/%s discarded oversized event line",
                        self._server_id,
                        self._run_id,
                    )
                    self._stream_pending.clear()
                    self._discarding_line = True
                return

            raw_line = bytes(self._stream_pending[:newline])
            del self._stream_pending[: newline + 1]
            consumed = newline + 1
            is_replayed_line = self._advance_rotation_replay(consumed)
            if len(raw_line) > _MAX_EVENT_LINE_CHARS:
                logger.warning(
                    "watcher %s/%s discarded oversized event line",
                    self._server_id,
                    self._run_id,
                )
            else:
                line = raw_line.decode("utf-8", errors="replace").removesuffix("\r")
                if line.strip():
                    self._deliver_line(line, is_replayed_line=is_replayed_line)
            self._clear_rotation_replay_if_done()
            if self._stream_cursor is not None:
                self._stream_cursor += consumed

    def _advance_rotation_replay(self, consumed: int) -> bool:
        """Advance the rotation replay boundary and classify one complete line.

        A line is eligible for de-duplication only when its terminating byte
        belongs to the pre-existing file content.  If the old file ended in a
        partial line and the new tail bytes complete it, the line crosses the
        boundary and is delivered as new data instead of being suppressed.
        """
        if self._rotation_dedup is None or self._rotation_replay_remaining <= 0:
            return False
        is_replayed = consumed <= self._rotation_replay_remaining
        self._rotation_replay_remaining = max(0, self._rotation_replay_remaining - consumed)
        return is_replayed

    def _clear_rotation_replay_if_done(self) -> None:
        if self._rotation_replay_remaining == 0:
            self._rotation_dedup = None

    def _deliver_line(self, line: str, *, is_replayed_line: bool) -> None:
        """Deliver a line once per logical event occurrence across rotation."""
        if is_replayed_line and self._rotation_dedup is not None:
            remaining = self._rotation_dedup.get(line, 0)
            if remaining:
                if remaining == 1:
                    del self._rotation_dedup[line]
                else:
                    self._rotation_dedup[line] = remaining - 1
                # Keep the occurrence in the current generation's history so
                # a subsequent rotation can suppress it as well.
                self._seen_lines[line] += 1
                return
        self._callback(self._run_id, self._server_id, line)
        self._seen_lines[line] += 1

    def _remote_file_size(self, ssh: SSHClient, quoted_path: str) -> int | None:
        """Read the append-only event log size for cursor-based reconnects."""
        try:
            result = ssh.run(f"wc -c < {quoted_path}", timeout=10)
            if result.exit_code != 0:
                return None
            value = result.stdout.strip().splitlines()[-1]
            size = int(value)
            return size if size >= 0 else None
        except (IndexError, TypeError, ValueError, AttributeError):
            logger.debug(
                "watcher %s/%s could not determine event-log size",
                self._server_id,
                self._run_id,
                exc_info=True,
            )
            return None

    def _tail_command(self, quoted_path: str, file_size: int | None) -> str:
        """Resume after the cursor, or replay a rotated file from byte zero."""
        if self._stream_cursor is None:
            self._stream_cursor = file_size
            if file_size is not None:
                return f"tail -c +{file_size + 1} -f {quoted_path}"
            return f"tail -n 0 -f {quoted_path}"
        if file_size is not None and file_size < self._stream_cursor:
            # The old absolute cursor cannot address a rotated/truncated
            # file. Replay the complete current generation and suppress only
            # line occurrences already delivered in the previous generation.
            self._rotation_dedup = Counter(self._seen_lines)
            # Start a fresh history for the new generation.  Replayed lines
            # that are suppressed below are still recorded into this new
            # counter, so a later rotation remains generation-local instead
            # of accumulating every historical log generation forever.
            self._seen_lines.clear()
            self._rotation_replay_remaining = file_size
            if file_size == 0:
                self._rotation_dedup = None
            self._stream_cursor = 0
            return f"tail -c +1 -f {quoted_path}"
        if file_size is None:
            # An unavailable size probe cannot prove a rotation. Preserve the
            # historical fail-safe behavior and start at EOF rather than
            # guessing a cursor that could duplicate an entire log.
            self._stream_cursor = file_size
            return f"tail -n 0 -f {quoted_path}"
        return f"tail -c +{self._stream_cursor + 1} -f {quoted_path}"

    def _jittered_backoff(self, base_delay: float) -> float:
        if self._backoff_jitter is None:
            return base_delay
        try:
            delay = float(self._backoff_jitter(base_delay))
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                "watcher %s/%s received invalid backoff jitter; using base delay",
                self._server_id,
                self._run_id,
            )
            return base_delay
        if not math.isfinite(delay):
            logger.warning(
                "watcher %s/%s received non-finite backoff jitter; using base delay",
                self._server_id,
                self._run_id,
            )
            return base_delay
        # A jitter provider returns the final delay in the full-jitter range
        # [0, base]. Clamping keeps the exponential cap a hard upper bound and
        # prevents a bad provider from creating a busy loop or long stall.
        return min(max(delay, 0.0), base_delay)

    def _idle_expired(self, last_activity: float | None) -> bool:
        if self._idle_expiry_seconds is None or last_activity is None:
            return False
        return time.monotonic() - last_activity >= self._idle_expiry_seconds

    def _expire_idle(self) -> None:
        if self._stop_event.is_set():
            return
        logger.info(
            "watcher %s/%s expired after %.1fs without tail/probe activity",
            self._server_id,
            self._run_id,
            self._idle_expiry_seconds,
        )
        self._stop_event.set()
        self._close_current_transport()

    def _open_ssh(self) -> SSHClient:
        with self._transport_lock:
            if self._close_in_flight:
                raise RuntimeError("monitor transport close is still in flight")
        if self._transport_provider is not None:
            return self._transport_provider.open(self._server_config)
        return self._ssh_factory(self._server_config)

    def _close_ssh(self, ssh: SSHClient) -> None:
        try:
            if self._transport_provider is not None:
                self._transport_provider.close(ssh)
            else:
                ssh.close()
        except Exception:
            logger.debug("failed to close monitor transport", exc_info=True)

    def _set_active_transport(self, ssh: SSHClient | None) -> None:
        with self._transport_lock:
            self._active_ssh = ssh

    def _close_current_transport(self, *, deadline: float | None = None, wait: bool = True) -> bool:
        with self._transport_lock:
            # A provider close may be permanently blocked on a broken socket.
            # Do not launch an unbounded series of daemon workers while a
            # previous one is still running; the watcher will also refuse to
            # open another transport until this state clears.
            if self._close_in_flight:
                return False
            ssh = self._active_ssh
            self._active_ssh = None
            if ssh is not None:
                self._close_in_flight = True
                self._close_worker_launches += 1
        if ssh is None:
            return True

        finished = threading.Event()

        def close_transport() -> None:
            try:
                self._close_ssh(ssh)
            finally:
                with self._transport_lock:
                    self._close_in_flight = False
                finished.set()

        closer = threading.Thread(
            target=close_transport,
            name=f"jobdesk-monitor-close-{self._server_id}-{self._run_id}",
            daemon=True,
        )
        try:
            closer.start()
        except Exception:
            with self._transport_lock:
                self._close_in_flight = False
            logger.warning(
                "watcher %s/%s could not start transport close worker",
                self._server_id,
                self._run_id,
                exc_info=True,
            )
            return False
        if not wait:
            return True
        wait_for = _TRANSPORT_CLOSE_WAIT_SECONDS
        if deadline is not None:
            wait_for = min(wait_for, max(0.0, deadline - time.monotonic()))
        if finished.wait(wait_for):
            return True
        with self._transport_lock:
            self._close_timeouts += 1
            close_timeouts = self._close_timeouts
            close_worker_launches = self._close_worker_launches
        logger.warning(
            "watcher %s/%s transport close exceeded %.1fs; shutdown continues without waiting "
            "(close_in_flight=true close_timeouts=%d close_worker_launches=%d)",
            self._server_id,
            self._run_id,
            wait_for,
            close_timeouts,
            close_worker_launches,
            extra={
                "jobdesk_monitor_close_in_flight": True,
                "jobdesk_monitor_close_timeouts": close_timeouts,
                "jobdesk_monitor_close_worker_launches": close_worker_launches,
            },
        )
        return False

    def _set_reconnecting(self, reconnecting: bool) -> None:
        try:
            self._state_callback(reconnecting)
        except Exception:
            logger.debug("monitor state callback failed", exc_info=True)

    def _probe_checkpoint(self, ssh: SSHClient | None = None) -> bool:
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
            return False
        ssh = ssh or self._cached_ssh
        if ssh is None:
            return False
        probe_script = _build_checkpoint_probe_script(self._progress_paths)
        try:
            r = ssh.run(probe_script, timeout=10)
            if r.exit_code != 0:
                return False
            parsed = _parse_checkpoint_snapshot(r.stdout, len(self._progress_paths))
            if parsed is None:
                logger.debug(
                    "watcher %s/%s ignored incomplete checkpoint snapshot",
                    self._server_id,
                    self._run_id,
                )
                return False
            any_present, snapshot = parsed
            previous = self._checkpoint_snapshot
            snapshot_changed = previous != snapshot
            changed = (previous is None and any_present) or (previous is not None and snapshot_changed)
            self._checkpoint_snapshot = snapshot
            if snapshot_changed:
                self._checkpoint_generation += 1
            if not changed:
                return True
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
            return True
        except Exception as exc:
            logger.debug(
                "watcher %s/%s checkpoint probe failed (ignored): %s",
                self._server_id,
                self._run_id,
                exc,
            )
            return False
