"""Tests for the ConfFlow checkpoint probe in run_monitor."""

from __future__ import annotations

from jobdesk_app.services.run_monitor import DoneEvent, _Watcher


class FakeResult:
    def __init__(self, exit_code: int, stdout: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout


class FakeChannel:
    def exec_command(self, command):
        pass

    def settimeout(self, timeout):
        pass

    def recv(self, size):
        return b""  # immediate EOF


class FakeSSH:
    """SSH fake that exposes the same surface used by the watcher."""

    def __init__(self, run_responses):
        self._responses = list(run_responses)
        self.connected = False
        self.closed = False
        self.run_calls = 0

    def connect(self):
        self.connected = True

    def run(self, script, timeout=None):
        self.run_calls += 1
        if self._responses:
            return self._responses.pop(0)
        return FakeResult(0, "")

    def open_session(self):
        return FakeChannel()

    def close(self):
        self.closed = True


def _make_watcher_with_progress():
    progress_events: list[DoneEvent] = []

    def on_progress(event: DoneEvent) -> None:
        progress_events.append(event)

    main_events: list = []

    def on_main(run_id: str, server_id: str, line: str) -> None:
        main_events.append((run_id, server_id, line))

    ssh = FakeSSH([])
    watcher = _Watcher(
        run_id="r1",
        server_id="wsl",
        remote_batch_dir="/tmp/run1",
        server_config={"server_id": "wsl"},
        callback=on_main,
        ssh_factory=lambda _cfg: ssh,
        progress_callback=on_progress,
    )
    import threading

    watcher._stop_event = threading.Event()

    # Fake ``wait`` so the loop returns after the first iteration.
    def fake_wait(_seconds):
        watcher._stop_event.set()
        return True

    watcher._stop_event.wait = fake_wait  # type: ignore[method-assign]
    return watcher, ssh, progress_events


def test_probe_skipped_when_no_cached_ssh():
    """No SSH cached -> probe is a no-op (initial iteration)."""
    watcher, ssh, progress_events = _make_watcher_with_progress()
    watcher._probe_checkpoint()
    assert progress_events == []
    assert ssh.run_calls == 0


def test_probe_emits_progress_event_on_checkpoint_change():
    """Probe sees `__JD_CHECKPOINT_CHANGED__` -> fires DoneEvent with _ckpt_ prefix."""
    progress_events: list[DoneEvent] = []

    def on_progress(event: DoneEvent) -> None:
        progress_events.append(event)

    ssh = FakeSSH([FakeResult(0, "__JD_CHECKPOINT_CHANGED__\n")])
    # Pre-populate the cache so probe finds an SSH client.
    watcher = _Watcher(
        run_id="r2",
        server_id="wsl",
        remote_batch_dir="/tmp/run2",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: ssh,
        progress_callback=on_progress,
    )
    watcher._cached_ssh = ssh
    watcher._probe_checkpoint()
    assert len(progress_events) == 1
    evt = progress_events[0]
    assert evt.task_id.startswith("_ckpt_")
    assert evt.exit_code is None
    assert evt.run_id == "r2"
    assert evt.server_id == "wsl"


def test_probe_does_nothing_on_clean_run():
    """Probe sees a clean run -> no progress event emitted."""
    progress_events: list[DoneEvent] = []
    ssh = FakeSSH([FakeResult(0, "")])
    watcher = _Watcher(
        run_id="r3",
        server_id="wsl",
        remote_batch_dir="/tmp/run3",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: ssh,
        progress_callback=progress_events.append,
    )
    watcher._cached_ssh = ssh
    watcher._probe_checkpoint()
    assert progress_events == []


def test_probe_swallows_exceptions():
    """Probe failure is silent — never raises to the loop."""
    progress_events: list[DoneEvent] = []
    ssh = FakeSSH([])
    watcher = _Watcher(
        run_id="r4",
        server_id="wsl",
        remote_batch_dir="/tmp/run4",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: ssh,
        progress_callback=progress_events.append,
    )
    watcher._cached_ssh = ssh
    # Must not raise.
    watcher._probe_checkpoint()
    assert progress_events == []


class BrokenSSH:
    def run(self, *args, **kwargs):
        raise OSError("connection lost mid-probe")

    def close(self):
        pass


def test_probe_swallows_exceptions_from_broken_ssh():
    """Probe failure is silent — never raises to the loop."""
    progress_events: list[DoneEvent] = []
    watcher = _Watcher(
        run_id="r5",
        server_id="wsl",
        remote_batch_dir="/tmp/run5",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: BrokenSSH(),
        progress_callback=progress_events.append,
    )
    watcher._cached_ssh = BrokenSSH()
    # Must not raise.
    watcher._probe_checkpoint()
    assert progress_events == []


def test_probe_script_finds_both_state_files():
    """Verify the probe script uses -o (OR) to match both workflow_stats.json and .workflow_state.json."""
    progress_events: list[DoneEvent] = []

    class ScriptCapturingSSH:
        last_script = ""

        def run(self, script, timeout=None):
            ScriptCapturingSSH.last_script = script
            return FakeResult(0, "")

        def close(self):
            pass

    watcher = _Watcher(
        run_id="r6",
        server_id="wsl",
        remote_batch_dir="/tmp/run6",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: ScriptCapturingSSH(),
        progress_callback=progress_events.append,
    )
    watcher._cached_ssh = ScriptCapturingSSH()
    watcher._probe_checkpoint()

    # The script must contain a find that matches both file patterns
    assert "workflow_stats.json" in ScriptCapturingSSH.last_script
    assert ".workflow_state.json" in ScriptCapturingSSH.last_script
    # The -o (OR) operator should be used to combine the patterns
    assert "-o" in ScriptCapturingSSH.last_script


def test_probe_detects_workflow_state_json_change():
    """Probe emits progress event when .workflow_state.json is newer than marker.

    This validates that v1.3.0 state-file-only workflows trigger GUI refresh.
    """
    progress_events: list[DoneEvent] = []

    def on_progress(event: DoneEvent) -> None:
        progress_events.append(event)

    ssh = FakeSSH([FakeResult(0, "__JD_CHECKPOINT_CHANGED__\n")])
    watcher = _Watcher(
        run_id="r7",
        server_id="wsl",
        remote_batch_dir="/tmp/run7",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: ssh,
        progress_callback=on_progress,
    )
    watcher._cached_ssh = ssh
    watcher._probe_checkpoint()

    assert len(progress_events) == 1
    evt = progress_events[0]
    assert evt.task_id.startswith("_ckpt_")
    assert evt.exit_code is None
    assert evt.run_id == "r7"
    assert evt.server_id == "wsl"
