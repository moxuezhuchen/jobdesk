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


def _make_watcher():
    events = []
    w = _Watcher("run1", "wsl", "/tmp/batch", object(), lambda *a: events.append(a))
    return w, events


def _run_watcher_sessions(session_actions, max_waits, monotonic_values=None):
    """Run watcher with controlled sessions and return (waits, events)."""
    w, events = _make_watcher()
    w._stop_event = ControlledStopEvent(max_waits=max_waits)

    sessions = []
    for actions in session_actions:
        ch = FakeChannel(actions)
        sessions.append(FakeSSHClient(ch))
    session_iter = iter(sessions)

    patches = [
        patch("jobdesk_app.gui.session.create_ssh_client",
              side_effect=lambda config: next(session_iter)),
    ]
    if monotonic_values is not None:
        patches.append(
            patch("jobdesk_app.services.run_monitor.time.monotonic",
                  side_effect=monotonic_values)
        )

    with patches[0]:
        if len(patches) > 1:
            with patches[1]:
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



def test_watcher_logs_connection_failure(caplog):
    """Connection exceptions are logged at WARNING level."""
    import logging

    w, events = _make_watcher()
    w._stop_event = ControlledStopEvent(max_waits=1)

    with patch(
        "jobdesk_app.gui.session.create_ssh_client",
        side_effect=OSError("connection refused"),
    ), caplog.at_level(logging.WARNING, logger="jobdesk_app.services.run_monitor"):
        w._run()

    assert any("connection refused" in r.message for r in caplog.records)
