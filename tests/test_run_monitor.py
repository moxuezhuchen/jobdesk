"""Tests for run_monitor._Watcher backoff behavior.

Verifies that:
- Immediate EOF sessions continue exponential backoff.
- Backoff resets only after receiving stream data or 30s stable connection.
"""

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jobdesk_app.application.runs_monitor import (
    MonitorContext,
    MonitorEvent,
    MonitorSubscription,
    RunsMonitorController,
)
from jobdesk_app.infrastructure.runtime.run_monitor import (
    DEFAULT_MAX_WATCHERS,
    DEFAULT_MAX_WATCHERS_PER_SERVER,
    RunMonitor,
    WatchRejectedError,
    _Watcher,
)


class ControlledStopEvent:
    """Stop event that records wait() calls and stops after max_waits."""

    def __init__(self, max_waits):
        self.max_waits = max_waits
        self.waits: list[float] = []

    def is_set(self):
        return len(self.waits) >= self.max_waits

    def wait(self, seconds):
        self.waits.append(seconds)
        return False

    def set(self):
        self.max_waits = 0


class FakeChannel:
    """Channel that yields pre-configured actions then EOF."""

    def __init__(self, actions):
        self._actions = list(actions)

    def exec_command(self, command):
        pass

    def settimeout(self, timeout):
        pass

    def recv(self, size):
        if not self._actions:
            return b""
        action = self._actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action

    def close(self):
        pass


class OneBrokenReadChannel(FakeChannel):
    def __init__(self, exc):
        super().__init__([])
        self.exc = exc
        self.recv_calls = 0

    def recv(self, size):
        self.recv_calls += 1
        if self.recv_calls == 1:
            raise self.exc
        return b""


class FakeTransport:
    def __init__(self, channel):
        self._channel = channel

    def open_session(self):
        return self._channel


class FakeSSHClient:
    def __init__(self, channel):
        self._client = MagicMock()
        self._client.get_transport.return_value = FakeTransport(channel)

    def connect(self):
        pass

    def run(self, *args, **kwargs):
        pass

    def open_session(self):
        return self._client.get_transport().open_session()

    def close(self):
        pass


class RecordingChannel(FakeChannel):
    def __init__(self, actions):
        super().__init__(actions)
        self.commands = []

    def exec_command(self, command):
        self.commands.append(command)


class CursorSSHClient(FakeSSHClient):
    def __init__(self, channel, file_size):
        super().__init__(channel)
        self.file_size = file_size
        self.commands = []

    def run(self, command, *args, **kwargs):
        self.commands.append(command)
        if command.startswith("wc -c <"):
            return SimpleNamespace(exit_code=0, stdout=f"{self.file_size}\n")
        return SimpleNamespace(exit_code=0, stdout="", stderr="")


def _make_watcher(ssh_factory=None, **watcher_kwargs):
    events = []
    if ssh_factory is None:
        ssh_factory = MagicMock()
    watcher_kwargs.setdefault("backoff_jitter", lambda base_delay: base_delay)
    w = _Watcher(
        "run1",
        "wsl",
        "/tmp/batch",
        object(),
        lambda *a: events.append(a),
        ssh_factory,
        **watcher_kwargs,
    )
    return w, events


def _run_watcher_sessions(
    session_actions,
    max_waits,
    monotonic_values=None,
    **watcher_kwargs,
):
    """Run watcher with controlled sessions and return (waits, events)."""
    sessions = []
    for actions in session_actions:
        ch = FakeChannel(actions)
        sessions.append(FakeSSHClient(ch))
    session_iter = iter(sessions)
    w, events = _make_watcher(lambda _config: next(session_iter), **watcher_kwargs)
    w._stop_event = ControlledStopEvent(max_waits=max_waits)

    patches = []
    if monotonic_values is not None:
        patches.append(patch("jobdesk_app.infrastructure.runtime.run_monitor.time.monotonic", side_effect=monotonic_values))

    if patches:
        with patches[0]:
            w._run()
    else:
        w._run()

    return w._stop_event.waits, events


def test_watcher_backs_off_when_sessions_immediately_eof():
    """Sessions that open then immediately EOF must use exponential backoff."""
    # Each session: channel.recv returns b"" immediately (EOF)
    waits, events = _run_watcher_sessions([[], [], []], max_waits=3)
    assert waits == [10, 20, 40]
    assert events == []


def test_monitor_keeps_same_server_run_watchers_separate_by_workspace_identity():
    """A/B workspaces may legitimately watch identically named remote runs."""
    events = []
    monitor = RunMonitor(MagicMock(), events.append)
    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher"):
        monitor.watch("same", "wsl", "/remote/a", object(), watch_id="workspace-a\x1fwsl\x1fsame")
        monitor.watch("same", "wsl", "/remote/b", object(), watch_id="workspace-b\x1fwsl\x1fsame")

    assert len(monitor._watchers) == 2
    monitor._dispatch("same", "wsl", "DONE task 0", "workspace-a\x1fwsl\x1fsame")
    monitor._dispatch("same", "wsl", "DONE task 0", "workspace-b\x1fwsl\x1fsame")
    assert [event.watch_id for event in events] == ["workspace-a\x1fwsl\x1fsame", "workspace-b\x1fwsl\x1fsame"]


def test_monitor_watch_is_idempotent_for_same_workspace_identity():
    monitor = RunMonitor(MagicMock(), MagicMock())

    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher") as watcher_class:
        monitor.watch("same", "wsl", "/remote", object(), watch_id="workspace\x1fwsl\x1fsame")
        monitor.watch("same", "wsl", "/remote", object(), watch_id="workspace\x1fwsl\x1fsame")

    watcher_class.assert_called_once()
    watcher_class.return_value.start.assert_called_once_with()


def test_monitor_watch_start_failure_cleans_key_and_allows_retry():
    """A failed watcher thread start must not permanently poison its key."""
    monitor = RunMonitor(MagicMock(), MagicMock())
    failed = MagicMock()
    failed.start.side_effect = RuntimeError("start failed")
    replacement = MagicMock()

    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=[failed, replacement]):
        with pytest.raises(RuntimeError, match="start failed"):
            monitor.watch("same", "wsl", "/remote", object(), watch_id="workspace\x1fwsl\x1fsame")

        assert "workspace\x1fwsl\x1fsame" not in monitor._watchers
        failed.stop.assert_called_once_with()

        monitor.watch("same", "wsl", "/remote", object(), watch_id="workspace\x1fwsl\x1fsame")

    assert monitor._watchers["workspace\x1fwsl\x1fsame"] is replacement
    replacement.start.assert_called_once_with()


def test_watcher_resets_backoff_after_receiving_stream_data():
    """After receiving data, next reconnect delay resets to 10."""
    # Session 1: immediate EOF -> wait 10
    # Session 2: immediate EOF -> wait 20
    # Session 3: sends data then EOF -> wait 10 (reset)
    # Session 4: immediate EOF -> wait 20
    waits, events = _run_watcher_sessions(
        [[], [], [b"DONE task-1 0\n"], []],
        max_waits=4,
    )
    assert waits == [10, 20, 10, 20]
    assert len(events) == 1
    assert events[0] == ("run1", "wsl", "DONE task-1 0")


def test_watcher_applies_injected_full_jitter_within_base_backoff():
    jitter_inputs = []

    def jitter(base_delay):
        jitter_inputs.append(base_delay)
        return base_delay / 2

    waits, events = _run_watcher_sessions(
        [[], [], []],
        max_waits=3,
        backoff_jitter=jitter,
    )

    assert waits == [5, 10, 20]
    assert jitter_inputs == [10, 20, 40]
    assert events == []


def test_watcher_clamps_invalid_jitter_to_the_exponential_delay_range():
    watcher, _events = _make_watcher(backoff_jitter=lambda _base: 999.0)
    assert watcher._jittered_backoff(10) == 10
    watcher._backoff_jitter = lambda _base: -1.0
    assert watcher._jittered_backoff(10) == 0


def test_watcher_default_jitter_uses_full_jitter_provider():
    watcher = _Watcher(
        "run1",
        "wsl",
        "/tmp/batch",
        object(),
        lambda *_event: None,
        MagicMock(),
    )
    with patch("jobdesk_app.infrastructure.runtime.run_monitor.random.uniform", return_value=3.0) as uniform:
        assert watcher._jittered_backoff(10) == 3.0
    uniform.assert_called_once_with(0.0, 10)


def test_watcher_idle_expiry_does_not_wait_after_expiring_quiet_tail():
    waits, events = _run_watcher_sessions(
        [[socket.timeout()]],
        max_waits=1,
        monotonic_values=[0.0, 6.0, 6.0],
        idle_expiry_seconds=5.0,
    )

    assert waits == []
    assert events == []


def test_watcher_tail_data_refreshes_idle_activity():
    waits, events = _run_watcher_sessions(
        [[b"DONE active 0\n", socket.timeout()]],
        max_waits=1,
        monotonic_values=[0.0, 4.0, 7.0, 7.0, 7.0, 7.0, 7.0],
        idle_expiry_seconds=5.0,
    )

    assert waits == [2.0]
    assert events == [("run1", "wsl", "DONE active 0")]


def test_watcher_reconnects_from_consumed_event_cursor_without_replaying_done():
    channels = [
        RecordingChannel([b"DONE first 0\n"]),
        RecordingChannel([b"DONE second 0\n"]),
    ]
    clients = [CursorSSHClient(channels[0], 100), CursorSSHClient(channels[1], 113)]
    client_iter = iter(clients)
    watcher, events = _make_watcher(lambda _config: next(client_iter))
    watcher._stop_event = ControlledStopEvent(max_waits=2)

    watcher._run()

    assert channels[0].commands == ["tail -c +101 -f /tmp/batch/_batch/events.log"]
    assert channels[1].commands == ["tail -c +114 -f /tmp/batch/_batch/events.log"]
    assert events == [
        ("run1", "wsl", "DONE first 0"),
        ("run1", "wsl", "DONE second 0"),
    ]


def test_watcher_replays_partial_frame_from_confirmed_cursor_after_disconnect():
    channels = [
        RecordingChannel([b"DONE first "]),
        RecordingChannel([b"DONE first 0\n"]),
    ]
    clients = [CursorSSHClient(channels[0], 100), CursorSSHClient(channels[1], 113)]
    client_iter = iter(clients)
    watcher, events = _make_watcher(lambda _config: next(client_iter))
    watcher._stop_event = ControlledStopEvent(max_waits=2)

    watcher._run()

    assert channels[0].commands == ["tail -c +101 -f /tmp/batch/_batch/events.log"]
    assert channels[1].commands == ["tail -c +101 -f /tmp/batch/_batch/events.log"]
    assert events == [("run1", "wsl", "DONE first 0")]


def test_watcher_replays_rotated_log_from_start_without_duplicate_or_loss():
    """A truncation replays the new generation and de-duplicates old lines."""
    channels = [
        RecordingChannel([b"DONE old 0\n"]),
        RecordingChannel([b"DONE old 0\nDONE fresh 0\n"]),
    ]
    # The first watcher starts at byte 100 and confirms 13 new bytes.  The
    # second generation is 26 bytes, so its complete contents are replayed.
    clients = [CursorSSHClient(channels[0], 100), CursorSSHClient(channels[1], 26)]
    client_iter = iter(clients)
    watcher, events = _make_watcher(lambda _config: next(client_iter))
    watcher._stop_event = ControlledStopEvent(max_waits=2)

    watcher._run()

    assert channels[0].commands == ["tail -c +101 -f /tmp/batch/_batch/events.log"]
    assert channels[1].commands == ["tail -c +1 -f /tmp/batch/_batch/events.log"]
    assert events == [
        ("run1", "wsl", "DONE old 0"),
        ("run1", "wsl", "DONE fresh 0"),
    ]


def test_watcher_rotation_dedup_preserves_repeated_event_occurrences():
    """Line de-duplication uses counts, so identical events are not lost."""
    repeated = b"DONE same 0\nDONE same 0\n"
    channels = [RecordingChannel([repeated]), RecordingChannel([repeated])]
    clients = [CursorSSHClient(channels[0], 100), CursorSSHClient(channels[1], len(repeated))]
    client_iter = iter(clients)
    watcher, events = _make_watcher(lambda _config: next(client_iter))
    watcher._stop_event = ControlledStopEvent(max_waits=2)

    watcher._run()

    assert events == [
        ("run1", "wsl", "DONE same 0"),
        ("run1", "wsl", "DONE same 0"),
    ]


def test_watcher_stop_bounds_blocking_transport_close(caplog):
    """A broken provider close cannot defeat the watcher shutdown deadline."""
    close_started = threading.Event()
    release_close = threading.Event()
    provider = MagicMock()

    def blocking_close(_ssh):
        close_started.set()
        release_close.wait()

    provider.close.side_effect = blocking_close
    watcher = _Watcher(
        "run1",
        "wsl",
        "/tmp/batch",
        object(),
        lambda *_event: None,
        MagicMock(),
        transport_provider=provider,
    )
    watcher._active_ssh = FakeSSHClient(FakeChannel([]))

    started = time.monotonic()
    with (
        patch("jobdesk_app.infrastructure.runtime.run_monitor._WATCHER_STOP_JOIN_SECONDS", 0.2),
        patch("jobdesk_app.infrastructure.runtime.run_monitor._TRANSPORT_CLOSE_WAIT_SECONDS", 0.05),
        caplog.at_level("WARNING", logger="jobdesk_app.infrastructure.runtime.run_monitor"),
    ):
        assert watcher.stop() is True
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    close_started.wait(0.5)
    provider.close.assert_called_once()
    assert watcher.close_metrics.close_in_flight is True
    assert watcher.close_metrics.close_timeouts == 1
    assert watcher.close_metrics.close_worker_launches == 1

    # A timed-out close is a single fail-closed state.  Repeated cleanup and
    # reconnect attempts must not create another daemon worker or transport.
    assert watcher._close_current_transport() is False
    with pytest.raises(RuntimeError, match="close is still in flight"):
        watcher._open_ssh()
    assert watcher.close_metrics.close_worker_launches == 1

    records = [record for record in caplog.records if "transport close exceeded" in record.message]
    assert len(records) == 1
    assert records[0].jobdesk_monitor_close_in_flight is True
    assert records[0].jobdesk_monitor_close_timeouts == 1
    assert records[0].jobdesk_monitor_close_worker_launches == 1

    release_close.set()
    deadline = time.monotonic() + 0.5
    while watcher.close_metrics.close_in_flight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert watcher.close_metrics.close_in_flight is False


def test_watcher_buffers_event_line_split_across_recv_calls():
    waits, events = _run_watcher_sessions(
        [[b"DONE task-", b"1 0\n"]],
        max_waits=1,
    )

    assert waits == [10]
    assert events == [("run1", "wsl", "DONE task-1 0")]


def test_watcher_incrementally_decodes_utf8_split_across_recv_calls():
    task_id = "任务-1"
    encoded = f"DONE {task_id} 0\n".encode()
    split_at = encoded.index("任".encode()) + 1

    waits, events = _run_watcher_sessions(
        [[encoded[:split_at], encoded[split_at:]]],
        max_waits=1,
    )

    assert waits == [10]
    assert events == [("run1", "wsl", f"DONE {task_id} 0")]


def test_watcher_discards_incomplete_line_when_reconnecting():
    waits, events = _run_watcher_sessions(
        [[b"DONE stale-task 0"], [b"DONE fresh-task 0\n"]],
        max_waits=2,
    )

    assert waits == [10, 10]
    assert events == [("run1", "wsl", "DONE fresh-task 0")]


def test_watcher_discards_oversized_line_then_recovers(caplog):
    import logging

    with (
        patch("jobdesk_app.infrastructure.runtime.run_monitor._MAX_EVENT_LINE_CHARS", 20),
        caplog.at_level(logging.WARNING, logger="jobdesk_app.infrastructure.runtime.run_monitor"),
    ):
        waits, events = _run_watcher_sessions(
            [[b"DONE oversized-", b"task 0", b" ignored\nDONE fresh-task 0\n"]],
            max_waits=1,
        )

    assert waits == [10]
    assert events == [("run1", "wsl", "DONE fresh-task 0")]
    warnings = [record for record in caplog.records if "oversized event line" in record.message]
    assert len(warnings) == 1


def test_watcher_resets_backoff_after_30s_stable_silent_connection():
    """A quiet tail -f session open for 30+ seconds resets backoff."""
    # Session 1: immediate EOF (connected_at call) -> wait 10
    # Session 2: immediate EOF (connected_at call) -> wait 20
    # Session 3: socket.timeout then EOF (connected_at + exception check) -> wait 10 (reset)
    # Session 4: immediate EOF (connected_at call) -> wait 20
    waits, events = _run_watcher_sessions(
        [[], [], [socket.timeout(), b""], []],
        max_waits=4,
        monotonic_values=[
            0.0,  # Session 1: connected_at
            1.0,  # Session 2: connected_at
            100.0,  # Session 3: connected_at
            131.0,  # Session 3: exception check (131 - 100 = 31 >= 30)
            200.0,  # Session 4: connected_at
        ],
    )
    assert waits == [10, 20, 10, 20]
    assert events == []


def test_watcher_reconnects_after_non_timeout_channel_error():
    """Broken channels must reconnect instead of spinning in recv()."""
    channel = OneBrokenReadChannel(OSError("channel closed"))
    w, events = _make_watcher(lambda _config: FakeSSHClient(channel))
    w._stop_event = ControlledStopEvent(max_waits=1)

    w._run()

    assert channel.recv_calls == 1
    assert w._stop_event.waits == [10]
    assert events == []


def test_watcher_does_not_dispatch_data_returned_after_stop():
    watcher, events = _make_watcher()

    class StopThenDataChannel(FakeChannel):
        def recv(self, size):
            watcher._stop_event.set()
            return b"DONE task-1 0\n"

    watcher._ssh_factory = lambda _config: FakeSSHClient(StopThenDataChannel([]))

    watcher._run()

    assert events == []


def test_watcher_logs_connection_failure(caplog):
    """Connection exceptions are logged at WARNING level."""
    import logging

    def _raise_connection_error(_config):
        raise OSError("connection refused")

    w, events = _make_watcher(_raise_connection_error)
    w._stop_event = ControlledStopEvent(max_waits=1)

    with caplog.at_level(logging.WARNING, logger="jobdesk_app.infrastructure.runtime.run_monitor"):
        w._run()

    assert any("connection refused" in r.message for r in caplog.records)


def test_watcher_uses_injected_ssh_factory():
    """The service watcher must not reach into the GUI session module."""
    channel = FakeChannel([])
    ssh = FakeSSHClient(channel)
    factory = MagicMock(return_value=ssh)
    events = []
    watcher = _Watcher(
        "run1",
        "wsl",
        "/tmp/batch",
        object(),
        lambda *event: events.append(event),
        factory,
        backoff_jitter=lambda base_delay: base_delay,
    )
    watcher._stop_event = ControlledStopEvent(max_waits=1)

    watcher._run()

    factory.assert_called_once_with(watcher._server_config)
    assert watcher._stop_event.waits == [10]
    assert events == []


def test_watcher_uses_injected_long_lived_transport_provider():
    """A monitor transport owns open/close without involving SessionPool."""
    channel = FakeChannel([])
    ssh = FakeSSHClient(channel)
    provider = MagicMock()
    provider.open.return_value = ssh
    watcher = _Watcher(
        "run1",
        "wsl",
        "/tmp/batch",
        object(),
        lambda *_event: None,
        MagicMock(),
        transport_provider=provider,
    )
    watcher._stop_event = ControlledStopEvent(max_waits=1)

    watcher._run()

    provider.open.assert_called_once_with(watcher._server_config)
    provider.close.assert_called_once_with(ssh)


def test_watcher_stop_closes_active_transport_before_joining_thread():
    channel = FakeChannel([])
    ssh = FakeSSHClient(channel)
    provider = MagicMock()
    watcher = _Watcher(
        "run1",
        "wsl",
        "/tmp/batch",
        object(),
        lambda *_event: None,
        MagicMock(),
        transport_provider=provider,
    )
    watcher._active_ssh = ssh
    watcher._thread = MagicMock()

    watcher.stop()

    assert watcher._stop_event.is_set()
    provider.close.assert_called_once_with(ssh)
    watcher._thread.join.assert_called_once()


def test_watcher_marks_reconnecting_until_tail_channel_is_ready():
    states = []
    watcher, _events = _make_watcher(lambda _config: FakeSSHClient(FakeChannel([])))
    watcher._state_callback = states.append
    watcher._stop_event = ControlledStopEvent(max_waits=1)

    watcher._run()

    assert states[:2] == [True, False]


def test_service_monitor_defaults_are_bounded_but_explicit_overrides_are_preserved():
    default_monitor = RunMonitor(MagicMock(), MagicMock())
    assert default_monitor._max_watchers == DEFAULT_MAX_WATCHERS
    assert default_monitor._max_watchers_per_server == DEFAULT_MAX_WATCHERS_PER_SERVER

    configured_monitor = RunMonitor(
        MagicMock(),
        MagicMock(),
        max_watchers=2,
        max_watchers_per_server=1,
    )
    assert configured_monitor._max_watchers == 2
    assert configured_monitor._max_watchers_per_server == 1

    # ``None`` remains an explicit compatibility opt-out for callers that
    # own and enforce a different resource budget.
    unbounded_monitor = RunMonitor(
        MagicMock(),
        MagicMock(),
        max_watchers=None,
        max_watchers_per_server=None,
    )
    assert unbounded_monitor._max_watchers is None
    assert unbounded_monitor._max_watchers_per_server is None


def _patch_recording_watcher():
    """Return a patched watcher factory whose server identity is inspectable."""
    created = []

    def make_watcher(*args, **_kwargs):
        watcher = MagicMock()
        watcher.server_id = args[1]
        created.append(watcher)
        return watcher

    return patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=make_watcher), created


def test_monitor_enforces_global_and_per_server_limits_and_drains_fairly():
    monitor = RunMonitor(
        MagicMock(),
        MagicMock(),
        max_watchers=2,
        max_watchers_per_server=1,
        queue_capacity=2,
    )
    watcher_patch, created = _patch_recording_watcher()
    with watcher_patch:
        monitor.watch("r1", "s1", "/r1", object(), watch_id="w1")
        monitor.watch("r2", "s2", "/r2", object(), watch_id="w2")
        monitor.watch("r3", "s1", "/r3", object(), watch_id="w3")
        monitor.watch("r4", "s2", "/r4", object(), watch_id="w4")

        assert monitor.metrics.active == 2
        assert monitor.metrics.queued == 2

        # The head s1 request remains blocked; the eligible s2 request behind
        # it is promoted only after the watcher thread confirms that it has
        # exited.  ``unwatch`` itself is deliberately non-blocking.
        monitor.unwatch("r2", "s2", "w2")
        assert monitor.metrics.active == 2
        assert monitor.metrics.queued == 2
        monitor._watcher_finished("w2", created[1])

    assert monitor.metrics.active == 2
    assert monitor.metrics.queued == 1
    assert [watcher.server_id for watcher in created] == ["s1", "s2", "s2"]


def test_monitor_rejects_when_bounded_queue_is_full_and_metrics_are_immutable():
    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1, queue_capacity=1)
    watcher_patch, _created = _patch_recording_watcher()
    with watcher_patch:
        monitor.watch("r1", "s1", "/r1", {"password": "secret"}, watch_id="w1")
        monitor.watch("r2", "s2", "/r2", {"password": "secret"}, watch_id="w2")
        with pytest.raises(WatchRejectedError, match="capacity and queue are full") as exc_info:
            monitor.watch("r3", "s3", "/r3", {"password": "secret"}, watch_id="w3")

    assert exc_info.value.server_id == "s3"
    assert monitor.metrics.active == 1
    assert monitor.metrics.queued == 1
    assert monitor.metrics.rejected == 1
    assert "secret" not in repr(monitor.metrics)
    with pytest.raises(AttributeError):
        monitor.metrics.active = 2


def test_monitor_unwatch_cancels_queued_watch_and_stop_all_cancels_active():
    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1, queue_capacity=2)
    watcher_patch, created = _patch_recording_watcher()
    with watcher_patch:
        monitor.watch("r1", "s1", "/r1", object(), watch_id="w1")
        monitor.watch("r2", "s2", "/r2", object(), watch_id="w2")
        monitor.unwatch("r2", "s2", "w2")
        assert monitor.metrics.queued == 0

        monitor.stop_all()

    created[0].request_stop.assert_called_once_with()
    # A request is not a thread-exit acknowledgement.  Capacity remains
    # reserved until the watcher invokes the manager callback.
    assert monitor.metrics.active == 1
    assert monitor.metrics.queued == 0
    monitor._watcher_finished("w1", created[0])
    assert monitor.metrics.active == 0


def test_monitor_releases_old_watcher_before_promoting_queued_watcher():
    order = []

    def make_watcher(*args, **_kwargs):
        watcher = MagicMock()
        watcher.server_id = args[1]
        watcher.start.side_effect = lambda: order.append(f"start:{args[8]}")
        watcher.request_stop.side_effect = lambda: order.append(f"request_stop:{args[8]}")
        return watcher

    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1, queue_capacity=1)
    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=make_watcher):
        monitor.watch("r1", "s1", "/r1", object(), watch_id="w1")
        monitor.watch("r2", "s2", "/r2", object(), watch_id="w2")
        monitor.unwatch("r1", "s1", "w1")
        assert order == ["start:w1", "request_stop:w1"]
        assert monitor.metrics.active == 1
        assert monitor.metrics.queued == 1
        monitor._watcher_finished("w1", monitor._stopping["w1"])

    assert order == ["start:w1", "request_stop:w1", "start:w2"]


def test_monitor_keeps_slot_reserved_until_watcher_confirms_thread_exit():
    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1, queue_capacity=1)
    watcher_patch, created = _patch_recording_watcher()
    created_request_results = []

    def make_watcher(*args, **_kwargs):
        watcher = MagicMock()
        watcher.server_id = args[1]
        watcher.request_stop.side_effect = lambda: created_request_results.append(watcher)
        created.append(watcher)
        return watcher

    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=make_watcher):
        monitor.watch("r1", "s1", "/r1", object(), watch_id="w1")
        monitor.watch("r2", "s2", "/r2", object(), watch_id="w2")
        monitor.unwatch("r1", "s1", "w1")

        assert monitor.metrics.active == 1
        assert monitor.metrics.queued == 1
        # A same-key retry during the stopping window is idempotently ignored.
        monitor.watch("r1", "s1", "/r1", object(), watch_id="w1")
        assert len(created) == 1

        old = created[0]
        monitor._watcher_finished("w1", old)

    assert monitor.metrics.active == 1
    assert monitor.metrics.queued == 0
    assert len(created_request_results) == 1


def test_monitor_keeps_slot_reserved_when_transport_close_blocks():
    """A timed-out close cannot promote a queued watcher early."""
    release_close = threading.Event()
    release_thread = threading.Event()
    provider = MagicMock()
    provider.close.side_effect = lambda _ssh: release_close.wait()
    watcher = _Watcher(
        "r1",
        "s1",
        "/remote",
        object(),
        lambda *_event: None,
        MagicMock(),
        transport_provider=provider,
    )
    watcher._active_ssh = FakeSSHClient(FakeChannel([]))
    watcher._thread = threading.Thread(target=release_thread.wait, daemon=True)
    watcher.start = MagicMock(side_effect=watcher._thread.start)
    replacement = MagicMock()
    replacement.server_id = "s2"
    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1, queue_capacity=1)

    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=[watcher, replacement]):
        started = time.monotonic()
        monitor.watch("r1", "s1", "/remote", object(), watch_id="w1")
        monitor.watch("r2", "s2", "/remote", object(), watch_id="w2")
        monitor.unwatch("r1", "s1", "w1")
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert monitor.metrics.active == 1
        assert monitor.metrics.queued == 1
        assert monitor.metrics.close_in_flight == 1
        assert monitor.metrics.close_timeouts == 0
        assert monitor.metrics.close_worker_launches == 1
        assert watcher.close_metrics.close_worker_launches == 1

        release_close.set()
        release_thread.set()
        watcher._thread.join(timeout=1.0)
        monitor._watcher_finished("w1", watcher)

    assert monitor.metrics.active == 1
    assert monitor.metrics.queued == 0


def test_monitor_stop_all_is_nonblocking_for_two_blocking_watchers_and_no_duplicate_close_workers():
    """Manager shutdown never waits on multiple transport close operations."""
    release_close = threading.Event()
    release_threads = [threading.Event(), threading.Event()]
    provider = MagicMock()
    provider.close.side_effect = lambda _ssh: release_close.wait()
    watchers = []

    for index in range(2):
        watcher = _Watcher(
            f"r{index + 1}",
            f"s{index + 1}",
            "/remote",
            object(),
            lambda *_event: None,
            MagicMock(),
            transport_provider=provider,
        )
        watcher._active_ssh = FakeSSHClient(FakeChannel([]))
        watcher._thread = threading.Thread(target=release_threads[index].wait, daemon=True)
        watcher.start = MagicMock(side_effect=watcher._thread.start)
        watchers.append(watcher)

    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=2, queue_capacity=1)
    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=watchers):
        monitor.watch("r1", "s1", "/remote", object(), watch_id="w1")
        monitor.watch("r2", "s2", "/remote", object(), watch_id="w2")

        started = time.monotonic()
        monitor.stop_all()
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert monitor.metrics.active == 2
        assert monitor.metrics.queued == 0
        assert monitor.metrics.close_in_flight == 2
        assert monitor.metrics.close_worker_launches == 2

        # Repeated manager shutdown must not ask either watcher to launch a
        # second close worker while the first close remains in flight.
        monitor.stop_all()
        assert monitor.metrics.close_worker_launches == 2

    release_close.set()
    for event in release_threads:
        event.set()
    for watcher in watchers:
        watcher._thread.join(timeout=1.0)
    monitor._watcher_finished("w1", watchers[0])
    monitor._watcher_finished("w2", watchers[1])
    assert monitor.metrics.active == 0


def test_monitor_unwatch_releases_slot_and_promotes_queue_only_from_exit_callback():
    """A fast cancellation request cannot release a still-running slot."""
    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1, queue_capacity=1)
    watcher_patch, created = _patch_recording_watcher()
    with watcher_patch:
        monitor.watch("r1", "s1", "/r1", object(), watch_id="w1")
        monitor.watch("r2", "s2", "/r2", object(), watch_id="w2")

        started = time.monotonic()
        monitor.unwatch("r1", "s1", "w1")
        elapsed = time.monotonic() - started

        assert elapsed < 0.5
        assert created[0].request_stop.call_count == 1
        assert monitor.metrics.active == 1
        assert monitor.metrics.queued == 1
        assert len(created) == 1

        monitor._watcher_finished("w1", created[0])

    assert monitor.metrics.active == 1
    assert monitor.metrics.queued == 0
    assert len(created) == 2
    created[1].start.assert_called_once_with()


def test_monitor_reconnecting_metric_tracks_watcher_state_without_credentials():
    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1)
    watcher_patch, _created = _patch_recording_watcher()
    with watcher_patch as patched:
        monitor.watch("r1", "s1", "/r1", {"token": "secret"}, watch_id="w1")
        state_callback = patched.call_args.kwargs["state_callback"]
        state_callback(True)
        assert monitor.metrics.reconnecting == 1
        state_callback(False)
        assert monitor.metrics.reconnecting == 0
    assert "secret" not in repr(monitor.metrics)


def test_monitor_max_startups_capacity_rejection_keeps_transport_budget_bounded():
    """A burst cannot exceed global/per-server slots or the admission queue."""
    monitor = RunMonitor(
        MagicMock(),
        MagicMock(),
        max_watchers=3,
        max_watchers_per_server=2,
        queue_capacity=4,
    )
    watcher_patch, created = _patch_recording_watcher()
    rejected: list[str] = []

    with watcher_patch:
        for index in range(12):
            server_id = f"server-{index % 2}"
            watch_id = f"watch-{index}"
            try:
                monitor.watch(f"run-{index}", server_id, f"/remote/{index}", object(), watch_id=watch_id)
            except WatchRejectedError as exc:
                rejected.append(exc.watch_id)

    metrics = monitor.metrics
    assert len(created) == 3
    assert metrics.active == 3
    assert metrics.queued == 4
    assert metrics.rejected == 5
    assert rejected == [f"watch-{index}" for index in range(7, 12)]
    assert metrics.active <= 3
    assert metrics.queued <= 4
    assert metrics.close_in_flight == 0
    assert metrics.close_worker_launches == 0


def test_monitor_promotes_many_watchers_without_exceeding_budget_or_leaking_slots():
    """Every queued watcher is promoted in order while active transport stays bounded."""
    monitor = RunMonitor(
        MagicMock(),
        MagicMock(),
        max_watchers=3,
        max_watchers_per_server=1,
        queue_capacity=12,
    )
    created = []
    requested: dict[str, tuple[str, str]] = {}
    high_water: list[tuple[int, int]] = []

    def make_watcher(*args, **_kwargs):
        watcher = MagicMock()
        watcher.server_id = args[1]
        created.append(watcher)
        return watcher

    requests = [(f"run-{index}", f"server-{index % 3}", f"watch-{index}") for index in range(15)]
    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=make_watcher):
        for run_id, server_id, watch_id in requests:
            requested[watch_id] = (run_id, server_id)
            monitor.watch(run_id, server_id, f"/remote/{run_id}", object(), watch_id=watch_id)
            metrics = monitor.metrics
            high_water.append((metrics.active, metrics.queued))

        assert high_water[-1] == (3, 12)

        # Release one real slot at a time.  The manager must acknowledge the
        # old watcher before it can promote the next queued request.
        while monitor.metrics.queued:
            watch_id, watcher = next(iter(monitor._watchers.items()))
            run_id, server_id = requested[watch_id]
            monitor.unwatch(run_id, server_id, watch_id)
            assert monitor.metrics.active == 3
            monitor._watcher_finished(watch_id, watcher)
            metrics = monitor.metrics
            high_water.append((metrics.active, metrics.queued))

        while monitor._watchers:
            watch_id, watcher = next(iter(monitor._watchers.items()))
            run_id, server_id = requested[watch_id]
            monitor.unwatch(run_id, server_id, watch_id)
            monitor._watcher_finished(watch_id, watcher)

    assert [watcher.server_id for watcher in created] == [server for _run, server, _watch in requests]
    assert max(active for active, _queued in high_water) <= 3
    assert max(queued for _active, queued in high_water) <= 12
    assert monitor.metrics.active == 0
    assert monitor.metrics.queued == 0
    assert monitor.metrics.rejected == 0
    assert monitor._watchers == {}
    assert monitor._stopping == {}
    assert monitor._reconnecting == set()


def test_server_deletion_cancels_queued_and_active_watches_without_leaking_slots(tmp_path: Path):
    """Removing a server retires its page identities and leaves other servers live."""
    monitor = RunMonitor(
        MagicMock(),
        MagicMock(),
        max_watchers=3,
        max_watchers_per_server=2,
        queue_capacity=3,
    )
    controller = RunsMonitorController(monitor)
    watcher_patch, created = _patch_recording_watcher()

    def subscription(run_id: str, server_id: str, watch_id: str) -> MonitorSubscription:
        return MonitorSubscription.create(
            MonitorContext.create(tmp_path, run_id, server_id),
            f"/remote/{run_id}",
            {"server_id": server_id},
            watch_id=watch_id,
        )

    deleted = [subscription(f"deleted-{index}", "deleted-server", f"deleted-watch-{index}") for index in range(5)]
    survivor = subscription("survivor", "survivor-server", "survivor-watch")
    with watcher_patch:
        for request in [*deleted[:2], survivor, *deleted[2:]]:
            assert controller.subscribe(request)

        assert len(created) == 3
        assert monitor.metrics.active == 3
        assert monitor.metrics.queued == 3
        assert len(controller.contexts) == 6

        for watch_id, context in controller.iter_contexts():
            if context.server_id == "deleted-server":
                assert controller.unsubscribe(watch_id, context)

        # The two active deleted-server watchers remain counted until their
        # worker callbacks confirm exit; queued deleted watches disappear at
        # once.  The survivor remains the only live transport after drain.
        assert controller.contexts == {survivor.watch_id: survivor.context}
        assert monitor.metrics.active == 3
        assert monitor.metrics.queued == 0
        assert monitor.metrics.reconnecting == 0

        for watch_id, watcher in list(monitor._stopping.items()):
            monitor._watcher_finished(watch_id, watcher)

    assert monitor.metrics.active == 1
    assert monitor.metrics.queued == 0
    assert set(monitor._watchers) == {survivor.watch_id}
    assert monitor._stopping == {}
    assert controller.context_keys() == (survivor.watch_id,)

    monitor.unwatch(survivor.context.run_id, survivor.context.server_id, survivor.watch_id)
    monitor._watcher_finished(survivor.watch_id, monitor._stopping[survivor.watch_id])
    controller.remove_context(survivor.watch_id)
    assert monitor.metrics.active == 0
    assert monitor.metrics.queued == 0
    assert controller.contexts == {}


def test_rapid_page_resubscribe_preserves_identity_cursor_and_metrics(tmp_path: Path):
    """Rapid page churn keeps one transport and rejects stale event tokens."""
    monitor = RunMonitor(MagicMock(), MagicMock(), max_watchers=1, queue_capacity=1)
    controller = RunsMonitorController(monitor)
    context = MonitorContext.create(tmp_path, "run-rapid", "server-rapid")
    created = []

    def make_watcher(*args, **_kwargs):
        watcher = MagicMock()
        watcher.server_id = args[1]
        created.append(watcher)
        return watcher

    previous_token: str | None = None
    with patch("jobdesk_app.infrastructure.runtime.run_monitor._Watcher", side_effect=make_watcher):
        for generation in range(32):
            request = MonitorSubscription.create(
                context,
                "/remote/run-rapid",
                {"server_id": "server-rapid"},
                watch_id="rapid-page-watch",
            )
            assert controller.subscribe(request)
            token = next(iter(monitor._watchers))
            assert previous_token != token
            assert monitor.metrics.active == 1
            assert monitor.metrics.queued == 0
            assert (
                controller.accept_event(MonitorEvent("run-rapid", "server-rapid", f"task-{generation}", 0, token))
                is not None
            )
            if previous_token is not None:
                assert (
                    controller.accept_event(MonitorEvent("run-rapid", "server-rapid", "stale", 0, previous_token))
                    is None
                )

            assert controller.unsubscribe("rapid-page-watch")
            assert controller.contexts == {}
            assert monitor.metrics.active == 1
            assert monitor.metrics.queued == 0
            old_watcher = monitor._stopping[token]
            monitor._watcher_finished(token, old_watcher)
            assert monitor.metrics.active == 0
            assert monitor.metrics.queued == 0
            assert monitor.metrics.reconnecting == 0
            assert monitor.metrics.close_in_flight == 0
            previous_token = token

    assert len(created) == 32
    assert monitor._watchers == {}
    assert monitor._stopping == {}
    assert not monitor._watch_requests
    assert controller.contexts == {}

    # A reconnect must resume from the last confirmed newline cursor, not
    # replay the partial frame that was present when the first transport died.
    channels = [RecordingChannel([b"DONE recovered "]), RecordingChannel([b"DONE recovered 0\n"])]
    clients = [CursorSSHClient(channels[0], 100), CursorSSHClient(channels[1], 113)]
    client_iter = iter(clients)
    watcher, events = _make_watcher(lambda _config: next(client_iter))
    watcher._stop_event = ControlledStopEvent(max_waits=2)
    watcher._run()

    assert channels[0].commands == ["tail -c +101 -f /tmp/batch/_batch/events.log"]
    assert channels[1].commands == ["tail -c +101 -f /tmp/batch/_batch/events.log"]
    assert events == [("run1", "wsl", "DONE recovered 0")]
