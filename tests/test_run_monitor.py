"""Tests for run_monitor._Watcher backoff behavior.

Verifies that:
- Immediate EOF sessions continue exponential backoff.
- Backoff resets only after receiving stream data or 30s stable connection.
"""

import socket
from unittest.mock import MagicMock, patch

from jobdesk_app.services.run_monitor import _Watcher


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


def _make_watcher(ssh_factory=None):
    events = []
    if ssh_factory is None:
        ssh_factory = MagicMock()
    w = _Watcher(
        "run1",
        "wsl",
        "/tmp/batch",
        object(),
        lambda *a: events.append(a),
        ssh_factory,
    )
    return w, events


def _run_watcher_sessions(session_actions, max_waits, monotonic_values=None):
    """Run watcher with controlled sessions and return (waits, events)."""
    sessions = []
    for actions in session_actions:
        ch = FakeChannel(actions)
        sessions.append(FakeSSHClient(ch))
    session_iter = iter(sessions)
    w, events = _make_watcher(lambda _config: next(session_iter))
    w._stop_event = ControlledStopEvent(max_waits=max_waits)

    patches = []
    if monotonic_values is not None:
        patches.append(
            patch("jobdesk_app.services.run_monitor.time.monotonic",
                  side_effect=monotonic_values)
        )

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
        patch("jobdesk_app.services.run_monitor._MAX_EVENT_LINE_CHARS", 20),
        caplog.at_level(logging.WARNING, logger="jobdesk_app.services.run_monitor"),
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
            0.0,    # Session 1: connected_at
            1.0,    # Session 2: connected_at
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

    with caplog.at_level(logging.WARNING, logger="jobdesk_app.services.run_monitor"):
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
    )
    watcher._stop_event = ControlledStopEvent(max_waits=1)

    watcher._run()

    factory.assert_called_once_with(watcher._server_config)
    assert watcher._stop_event.waits == [10]
    assert events == []
