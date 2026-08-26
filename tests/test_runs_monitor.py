"""Application-boundary tests for the Qt-free Runs monitor lifecycle."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jobdesk_app.application.runs_monitor import (
    MonitorContext,
    MonitorEvent,
    MonitorSubscription,
    RunsMonitorController,
    monitor_watch_id,
)


class _FakeMonitor:
    def __init__(self) -> None:
        self.watches: list[tuple] = []
        self.unwatches: list[tuple] = []
        self.closed = 0

    def watch(self, *args) -> None:
        self.watches.append(args)

    def unwatch(self, *args) -> None:
        self.unwatches.append(args)

    def stop_all(self) -> None:
        self.closed += 1

    def close(self) -> None:
        self.closed += 1


def _subscription(tmp_path: Path, *, watch_id: str = "watch-1") -> MonitorSubscription:
    context = MonitorContext.create(tmp_path, "run-1", "server-1")
    return MonitorSubscription.create(
        context,
        "/remote/run-1",
        {"host": "example"},
        ["/remote/state", "/remote/state"],
        watch_id,
    )


def test_context_and_event_are_immutable_and_qt_free(tmp_path: Path) -> None:
    context = MonitorContext.create(tmp_path, "run-1", "server-1")
    event = MonitorEvent("run-1", "server-1", "task-1", 0, "watch-1", tmp_path)

    assert context == (tmp_path, "run-1", "server-1")
    assert event.workspace == tmp_path
    with pytest.raises(FrozenInstanceError):
        event.run_id = "other"  # type: ignore[misc]


def test_subscribe_freezes_request_but_preserves_legacy_adapter_call_shape(tmp_path: Path) -> None:
    monitor = _FakeMonitor()
    controller = RunsMonitorController(monitor)
    request = _subscription(tmp_path)

    assert controller.subscribe(request)
    assert not controller.subscribe(request)
    assert controller.contexts[request.watch_id] == request.context
    assert monitor.watches == [
        (
            "run-1",
            "server-1",
            "/remote/run-1",
            {"host": "example"},
            ["/remote/state"],
            "watch-1",
        )
    ]


def test_failed_subscribe_rolls_back_identity() -> None:
    class BrokenMonitor(_FakeMonitor):
        def watch(self, *args) -> None:
            raise RuntimeError("cannot start")

    monitor = BrokenMonitor()
    controller = RunsMonitorController(monitor)
    request = _subscription(Path("C:/workspace"))

    with pytest.raises(RuntimeError, match="cannot start"):
        controller.subscribe(request)
    assert controller.contexts == {}


def test_event_identity_mismatch_and_late_event_fail_closed(tmp_path: Path) -> None:
    monitor = _FakeMonitor()
    controller = RunsMonitorController(monitor)
    request = _subscription(tmp_path)
    controller.subscribe(request)

    valid = MonitorEvent("run-1", "server-1", "task-1", None, "watch-1")
    mismatched = MonitorEvent("other-run", "server-1", "task-1", None, "watch-1")
    assert controller.accept_event(valid) == MonitorEvent("run-1", "server-1", "task-1", None, "watch-1", tmp_path)
    assert controller.accept_event(mismatched) is None

    assert controller.unsubscribe("watch-1")
    assert monitor.unwatches == [("run-1", "server-1", "watch-1")]
    assert controller.accept_event(valid) is None


def test_close_rejects_new_work_and_is_idempotent(tmp_path: Path) -> None:
    monitor = _FakeMonitor()
    controller = RunsMonitorController(monitor)
    controller.subscribe(_subscription(tmp_path))
    controller.close()
    controller.close()

    assert controller.closed
    assert controller.contexts == {}
    assert monitor.closed == 1
    assert not controller.subscribe(_subscription(tmp_path, watch_id="watch-2"))
    assert controller.accept_event(MonitorEvent("run-1", "server-1", "task", 0, "watch-1")) is None


def test_context_snapshot_is_immutable_and_does_not_track_later_mutations(tmp_path: Path) -> None:
    monitor = _FakeMonitor()
    controller = RunsMonitorController(monitor)
    snapshot = controller.contexts

    controller.subscribe(_subscription(tmp_path))

    assert snapshot == {}
    assert set(controller.context_keys()) == {"watch-1"}
    assert controller.get_context("watch-1") == MonitorContext.create(tmp_path, "run-1", "server-1")
    with pytest.raises(TypeError):
        snapshot["watch-2"] = MonitorContext.create(tmp_path, "run-2", "server-1")  # type: ignore[index]


def test_resubscribe_uses_new_external_token_and_rejects_queued_old_event(tmp_path: Path) -> None:
    monitor = _FakeMonitor()
    controller = RunsMonitorController(monitor)
    request = _subscription(tmp_path)

    controller.subscribe(request)
    first_external = monitor.watches[-1][-1]
    controller.unsubscribe("watch-1")
    controller.subscribe(request)
    second_external = monitor.watches[-1][-1]

    assert first_external == "watch-1"
    assert second_external != first_external
    assert second_external == "watch-1\x1e2"
    assert controller.accept_event(MonitorEvent("run-1", "server-1", "task", 0, first_external)) is None
    accepted = controller.accept_event(MonitorEvent("run-1", "server-1", "task", 0, second_external))
    assert accepted is not None
    assert accepted.watch_id == "watch-1"
    assert monitor.unwatches == [("run-1", "server-1", first_external)]


def test_legacy_facade_assignment_retires_replaced_external_token(tmp_path: Path) -> None:
    monitor = _FakeMonitor()
    controller = RunsMonitorController(monitor)
    request = _subscription(tmp_path)
    controller.subscribe(request)
    first_external = monitor.watches[-1][-1]

    replacement = MonitorContext.create(tmp_path, "replacement-run", "server-1")
    controller.legacy_contexts_view()["watch-1"] = replacement

    assert controller.accept_event(MonitorEvent("run-1", "server-1", "task", 0, first_external)) is None
    assert controller.get_context("watch-1") == replacement
    assert controller.contexts["watch-1"] == replacement


def test_replace_contexts_advances_generation_and_rejects_old_external_token(tmp_path: Path) -> None:
    monitor = _FakeMonitor()
    controller = RunsMonitorController(monitor)
    request = _subscription(tmp_path)
    controller.subscribe(request)
    first_external = monitor.watches[-1][-1]

    replacement = MonitorContext.create(tmp_path, "replacement-run", "server-1")
    controller.replace_contexts({"watch-1": replacement})
    second_external = "watch-1\x1e2"

    assert controller.accept_event(MonitorEvent("run-1", "server-1", "task", 0, first_external)) is None
    accepted = controller.accept_event(MonitorEvent("replacement-run", "server-1", "task", 0, second_external))
    assert accepted is not None
    assert accepted.watch_id == "watch-1"


def test_legacy_event_fallback_is_disabled_after_managed_watch_retires(tmp_path: Path) -> None:
    event = MonitorEvent("run-1", "server-1", "task", None)
    controller = RunsMonitorController(_FakeMonitor())

    assert controller.accept_event(event) is not None
    controller.subscribe(_subscription(tmp_path))
    controller.unsubscribe("watch-1")
    assert controller.accept_event(event) is None


def test_watch_id_is_stable_for_relative_aliases(tmp_path: Path) -> None:
    assert monitor_watch_id(tmp_path, "run", "server") == monitor_watch_id(Path(tmp_path), "run", "server")
