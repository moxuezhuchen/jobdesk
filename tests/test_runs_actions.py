"""Tests for the Runs action boundary and GUI callback dispatcher."""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread  # noqa: E402

from jobdesk_app.application.runs_actions import (  # noqa: E402
    RunActionIntent,
    RunActionOutcome,
    RunsActionController,
)
from jobdesk_app.gui.pages.runs_results_page import _RunsGuiDispatcher  # noqa: E402


class _DispatcherOwner(QObject):
    """Minimal page-like QObject for testing dispatcher lifetime guards."""

    def __init__(self) -> None:
        super().__init__()
        self._shutting_down = False


@pytest.fixture
def runs_dispatcher(qtbot):
    """Own and deterministically destroy the page-like dispatcher pair."""
    owner = _DispatcherOwner()
    dispatcher = _RunsGuiDispatcher(owner)
    yield owner, dispatcher

    # Do not leave a parented QObject (or a queued MetaCall event referring to
    # it) for the next Qt test.  This matters when a Runs page is constructed
    # immediately after the dispatcher tests.
    dispatcher.close()
    dispatcher.setParent(None)
    dispatcher.deleteLater()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()


def test_run_action_intent_and_outcome_are_frozen_and_deduplicate_ids(tmp_path: Path):
    intent = RunActionIntent(
        action=" delete ",
        run_ids=["run-a", "run-a", "", "run-b"],
        workspace=tmp_path,
    )

    assert intent.action == "delete"
    assert intent.run_ids == ("run-a", "run-b")
    assert intent.workspace == tmp_path

    with pytest.raises(FrozenInstanceError):
        intent.action = "retry"  # type: ignore[misc]

    outcome = RunActionOutcome(
        intent=intent,
        changed_count="2",  # type: ignore[arg-type]
        errors=["partial failure", "partial failure"],
        completed_run_ids=["run-a", "run-a", "run-b"],
    )

    assert outcome.changed_count == 2
    assert outcome.errors == ("partial failure", "partial failure")
    assert outcome.completed_run_ids == ("run-a", "run-b")
    assert outcome.retired_watch_run_ids == frozenset({"run-a", "run-b"})
    assert not outcome.succeeded

    with pytest.raises(FrozenInstanceError):
        outcome.changed_count = 3  # type: ignore[misc]


@pytest.mark.parametrize("action", ["cancel", "delete", "refresh_status", "retry"])
def test_controller_rejects_selection_actions_without_selection(action: str):
    controller = RunsActionController()

    assert controller.begin(action) is None
    assert controller.active_intent is None


def test_controller_guards_busy_shutdown_and_finish(tmp_path: Path):
    controller = RunsActionController()

    assert controller.begin("delete", ["run-shutdown"], shutting_down=True) is None
    assert controller.active_intent is None

    intent = controller.begin("delete", ["run-a", "run-a"], workspace=tmp_path)
    assert intent is not None
    assert intent.run_ids == ("run-a",)
    assert controller.active_intent == intent

    # A second action is rejected while the first one owns the guard.
    assert controller.begin("retry", ["run-b"], workspace=tmp_path) is None
    assert controller.active_intent == intent

    # A stale completion must not release another action's guard.
    stale = RunActionIntent("retry", ("run-stale",), workspace=tmp_path)
    controller.finish(stale)
    assert controller.active_intent == intent

    controller.finish(intent)
    assert controller.active_intent is None

    next_intent = controller.begin("cancel", ["run-b"], workspace=tmp_path)
    assert next_intent is not None
    controller.finish()
    assert controller.active_intent is None


def test_controller_outcome_marks_only_successfully_completed_delete_ids(tmp_path: Path):
    controller = RunsActionController()
    intent = controller.begin("delete", ["run-a", "run-b"], workspace=tmp_path)
    assert intent is not None

    outcome = controller.outcome(
        intent,
        changed_count=1,
        errors=["run-b: locked"],
        completed_run_ids=["run-a", "run-a"],
    )

    assert outcome.completed_run_ids == ("run-a",)
    assert outcome.retired_watch_run_ids == frozenset({"run-a"})
    assert outcome.errors == ("run-b: locked",)
    controller.finish(intent)

    non_delete = RunActionIntent("retry", ["run-a"], workspace=tmp_path)
    assert controller.outcome(non_delete, completed_run_ids=["run-a"]).retired_watch_run_ids == frozenset()


def test_runs_dispatcher_delivers_real_thread_post_on_owner_gui_thread(qtbot, runs_dispatcher):
    owner, dispatcher = runs_dispatcher
    owner_ident = threading.get_ident()
    owner_thread = QThread.currentThread()
    callback_idents: list[int] = []
    callback_threads: list[QThread] = []
    callback_done = threading.Event()

    def callback() -> None:
        callback_idents.append(threading.get_ident())
        callback_threads.append(QThread.currentThread())
        callback_done.set()

    worker = threading.Thread(target=lambda: dispatcher.post(callback), name="runs-dispatch-test")
    worker.start()
    qtbot.waitUntil(callback_done.is_set, timeout=2000)
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert callback_idents == [owner_ident]
    assert callback_threads[0] == owner_thread
    assert callback_threads[0] == owner.thread()


@pytest.mark.parametrize("guard", ["close", "shutdown"])
def test_runs_dispatcher_discards_queued_callback_after_close_or_shutdown(qtbot, runs_dispatcher, guard: str):
    owner, dispatcher = runs_dispatcher
    callback_called = threading.Event()
    post_done = threading.Event()

    def callback() -> None:
        callback_called.set()

    def post_from_worker() -> None:
        dispatcher.post(callback)
        post_done.set()

    worker = threading.Thread(target=post_from_worker, name=f"runs-dispatch-{guard}-test")
    worker.start()
    assert post_done.wait(timeout=2)
    worker.join(timeout=2)
    assert not worker.is_alive()

    # The worker has queued the Qt signal, but the test has not yielded to the
    # event loop yet.  Closing/shutting down must invalidate that queued call.
    if guard == "close":
        dispatcher.close()
    else:
        owner._shutting_down = True

    qtbot.wait(100)
    assert not callback_called.is_set()
