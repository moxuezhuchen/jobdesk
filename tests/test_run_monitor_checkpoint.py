"""Tests for the per-watcher ConfFlow checkpoint snapshot probe."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from jobdesk_app.services.run_monitor import (
    DoneEvent,
    _build_checkpoint_probe_script,
    _parse_checkpoint_snapshot,
    _Watcher,
)

DECLARED_PROGRESS_PATHS = [
    "/work/mol/.workflow_state.json",
    "/work/mol/workflow_stats.json",
]
HEADER = "__JD_CHECKPOINT_SNAPSHOT_V1__"
FOOTER = "__JD_CHECKPOINT_SNAPSHOT_END_V1__"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


class FakeResult:
    def __init__(self, exit_code: int, stdout: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout


class FakeSSH:
    def __init__(self, run_responses) -> None:
        self._responses = list(run_responses)
        self.run_calls = 0
        self.last_script = ""

    def run(self, script, timeout=None):
        self.run_calls += 1
        self.last_script = script
        return self._responses.pop(0)

    def close(self):
        pass


class BrokenSSH:
    def run(self, *args, **kwargs):
        raise OSError("connection lost mid-probe")

    def close(self):
        pass


def _snapshot_stdout(*entries: str | None) -> str:
    present = int(any(entry is not None for entry in entries))
    lines = [f"{HEADER}\tpresent={present}\tcount={len(entries)}"]
    for index, digest in enumerate(entries):
        if digest is None:
            lines.append(f"{index}\tmissing")
        else:
            lines.append(f"{index}\tpresent\t{digest}")
    lines.append(f"{FOOTER}\tcount={len(entries)}")
    return "\n".join(lines) + "\n"


def _make_watcher(
    responses,
    *,
    watch_id: str = "workspace-a\x1fwsl\x1frun",
    progress_paths=DECLARED_PROGRESS_PATHS,
):
    events: list[DoneEvent] = []
    ssh = FakeSSH(responses)
    watcher = _Watcher(
        run_id="run",
        server_id="wsl",
        remote_batch_dir="/tmp/run",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: ssh,
        progress_callback=events.append,
        progress_paths=progress_paths,
        watch_id=watch_id,
    )
    watcher._cached_ssh = ssh
    return watcher, ssh, events


def _working_posix_shell() -> str | None:
    candidates = [shutil.which("sh")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\sh.exe",
                r"C:\Program Files\Git\usr\bin\sh.exe",
            ]
        )
    for candidate in dict.fromkeys(path for path in candidates if path):
        try:
            result = subprocess.run(
                [candidate, "-c", "command -v sha256sum >/dev/null && command -v mktemp >/dev/null"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return candidate
    return None


def test_probe_skipped_when_no_cached_ssh():
    watcher, ssh, events = _make_watcher([FakeResult(0, _snapshot_stdout(DIGEST_A, None))])
    watcher._cached_ssh = None

    watcher._probe_checkpoint()

    assert events == []
    assert ssh.run_calls == 0
    assert watcher._checkpoint_snapshot is None
    assert watcher._checkpoint_generation == 0


def test_first_complete_snapshot_with_present_file_emits_and_is_stored_locally():
    watcher, _ssh, events = _make_watcher([FakeResult(0, _snapshot_stdout(DIGEST_A, None))])

    watcher._probe_checkpoint()

    assert len(events) == 1
    assert events[0] == DoneEvent("run", "wsl", "_ckpt_progress", None, "workspace-a\x1fwsl\x1frun")
    assert watcher._checkpoint_snapshot == ((True, DIGEST_A), (False, None))
    assert watcher._checkpoint_generation == 1


def test_first_complete_all_missing_snapshot_is_baseline_without_event():
    watcher, _ssh, events = _make_watcher([FakeResult(0, _snapshot_stdout(None, None))])

    watcher._probe_checkpoint()

    assert events == []
    assert watcher._checkpoint_snapshot == ((False, None), (False, None))
    assert watcher._checkpoint_generation == 1


def test_unchanged_content_snapshot_ignores_mtime_noise():
    output = _snapshot_stdout(DIGEST_A, None)
    watcher, _ssh, events = _make_watcher([FakeResult(0, output), FakeResult(0, output)])

    watcher._probe_checkpoint()
    watcher._probe_checkpoint()

    assert len(events) == 1
    assert watcher._checkpoint_snapshot == ((True, DIGEST_A), (False, None))
    assert watcher._checkpoint_generation == 1


def test_content_change_updates_snapshot_and_next_probe_uses_new_baseline():
    watcher, _ssh, events = _make_watcher(
        [
            FakeResult(0, _snapshot_stdout(DIGEST_A, None)),
            FakeResult(0, _snapshot_stdout(DIGEST_B, None)),
            FakeResult(0, _snapshot_stdout(DIGEST_B, None)),
        ]
    )

    watcher._probe_checkpoint()
    watcher._probe_checkpoint()
    watcher._probe_checkpoint()

    assert len(events) == 2
    assert watcher._checkpoint_snapshot == ((True, DIGEST_B), (False, None))
    assert watcher._checkpoint_generation == 2


def test_file_appearance_and_disappearance_each_emit():
    watcher, _ssh, events = _make_watcher(
        [
            FakeResult(0, _snapshot_stdout(None, None)),
            FakeResult(0, _snapshot_stdout(DIGEST_A, None)),
            FakeResult(0, _snapshot_stdout(None, None)),
        ]
    )

    watcher._probe_checkpoint()
    watcher._probe_checkpoint()
    watcher._probe_checkpoint()

    assert len(events) == 2
    assert watcher._checkpoint_snapshot == ((False, None), (False, None))


def test_incomplete_or_failed_probe_never_replaces_trusted_snapshot():
    baseline = _snapshot_stdout(DIGEST_A, None)
    truncated = "\n".join(_snapshot_stdout(DIGEST_B, None).splitlines()[:-1]) + "\n"
    watcher, _ssh, events = _make_watcher(
        [
            FakeResult(0, baseline),
            FakeResult(0, truncated),
            FakeResult(3, _snapshot_stdout(DIGEST_B, None)),
        ]
    )

    watcher._probe_checkpoint()
    watcher._probe_checkpoint()
    watcher._probe_checkpoint()

    assert len(events) == 1
    assert watcher._checkpoint_snapshot == ((True, DIGEST_A), (False, None))
    assert watcher._checkpoint_generation == 1


def test_probe_rejects_header_present_flag_inconsistent_with_records():
    inconsistent = _snapshot_stdout(DIGEST_A, None).replace("present=1", "present=0", 1)
    watcher, _ssh, events = _make_watcher([FakeResult(0, inconsistent)])

    watcher._probe_checkpoint()

    assert events == []
    assert watcher._checkpoint_snapshot is None


def test_probe_swallows_transport_exceptions_without_mutating_snapshot():
    events: list[DoneEvent] = []
    watcher = _Watcher(
        run_id="run",
        server_id="wsl",
        remote_batch_dir="/tmp/run",
        server_config={"server_id": "wsl"},
        callback=lambda *args: None,
        ssh_factory=lambda _cfg: BrokenSSH(),
        progress_callback=events.append,
        progress_paths=DECLARED_PROGRESS_PATHS,
    )
    watcher._cached_ssh = BrokenSSH()

    watcher._probe_checkpoint()

    assert events == []
    assert watcher._checkpoint_snapshot is None
    assert watcher._checkpoint_generation == 0


def test_new_watcher_and_independent_workspace_observer_each_emit_initial_state():
    output = _snapshot_stdout(DIGEST_A, None)
    watcher_a, _ssh_a, events_a = _make_watcher(
        [FakeResult(0, output)], watch_id="workspace-a\x1fwsl\x1frun"
    )
    watcher_b, _ssh_b, events_b = _make_watcher(
        [FakeResult(0, output)], watch_id="workspace-b\x1fwsl\x1frun"
    )

    watcher_a._probe_checkpoint()
    watcher_b._probe_checkpoint()

    assert len(events_a) == 1
    assert len(events_b) == 1
    assert events_a[0].watch_id == "workspace-a\x1fwsl\x1frun"
    assert events_b[0].watch_id == "workspace-b\x1fwsl\x1frun"


def test_probe_script_outputs_ordered_complete_protocol_and_quotes_spaces():
    script = _build_checkpoint_probe_script(
        ["/work/first state.json", "/work/second stats.json"]
    )

    assert "'/work/first state.json'" in script
    assert "'/work/second stats.json'" in script
    assert script.index("'/work/first state.json'") < script.index("'/work/second stats.json'")
    assert "sha256sum -- \"$progress_path\"" in script
    assert HEADER in script
    assert 'present=%s\\tcount=%s' in script
    assert FOOTER in script
    assert 'cat -- "$snapshot_tmp"' in script
    assert 'rm -f -- "$snapshot_tmp"' in script
    assert ".jobdesk_checkpoint_marker" not in script
    assert "mtime" not in script
    assert "stat " not in script


def test_probe_script_emits_no_trusted_header_until_full_snapshot_is_built():
    script = _build_checkpoint_probe_script(DECLARED_PROGRESS_PATHS)

    assert 'snapshot_tmp=$(mktemp "${TMPDIR:-/tmp}/jobdesk-checkpoint.XXXXXX")' in script
    assert "trap cleanup_snapshot EXIT HUP INT TERM" in script
    assert 'digest_line=$(sha256sum -- "$progress_path") || { complete=; break; }' in script
    assert '[ -n "$complete" ] || exit 3' in script
    assert script.index('[ -n "$complete" ] || exit 3') < script.index(HEADER)
    assert script.index(HEADER) < script.index('cat -- "$snapshot_tmp"')
    assert script.index('cat -- "$snapshot_tmp"') < script.index(FOOTER)


def test_probe_script_executes_complete_protocol_without_remote_residue(tmp_path: Path):
    shell = _working_posix_shell()
    if shell is None:
        pytest.skip("no POSIX shell with sha256sum and mktemp available")

    work_dir = tmp_path / "work dir"
    work_dir.mkdir()
    state_path = work_dir / "state file.json"
    missing_path = work_dir / "stats file.json"
    state_path.write_text("alpha", encoding="utf-8")
    state_mtime = state_path.stat().st_mtime_ns
    shell_state = state_path.as_posix()
    shell_missing = missing_path.as_posix()
    shell_tmp = tmp_path.as_posix()
    env = {**os.environ, "TMPDIR": shell_tmp}
    script = _build_checkpoint_probe_script([shell_state, shell_missing])

    first = subprocess.run(
        [shell, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
        env=env,
    )
    assert first.returncode == 0, first.stderr
    first_snapshot = _parse_checkpoint_snapshot(first.stdout, 2)
    assert first_snapshot is not None and first_snapshot[0] is True
    assert first_snapshot[1][0][0] is True
    assert first_snapshot[1][1] == (False, None)
    assert list(tmp_path.glob("jobdesk-checkpoint.*")) == []

    state_path.write_text("beta", encoding="utf-8")
    os.utime(state_path, ns=(state_path.stat().st_atime_ns, state_mtime))
    second = subprocess.run(
        [shell, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
        env=env,
    )
    second_snapshot = _parse_checkpoint_snapshot(second.stdout, 2)
    assert second.returncode == 0, second.stderr
    assert second_snapshot is not None
    assert second_snapshot[1] != first_snapshot[1]

    failed = subprocess.run(
        [shell, "-c", "sha256sum() { return 1; }\n" + script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
        env=env,
    )
    assert failed.returncode == 3
    assert HEADER not in failed.stdout
    assert list(tmp_path.glob("jobdesk-checkpoint.*")) == []
    assert ".jobdesk_checkpoint_marker" not in script
    assert shlex.quote(shell_state) in script
