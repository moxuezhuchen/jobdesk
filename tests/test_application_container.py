"""Tests for the stable application outcome, facade, and lifetime contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import get_type_hints

import pytest

from jobdesk_app.application import (
    ApplicationClosedError,
    ApplicationContainer,
    FilesApplication,
    OperationFailure,
    OperationOutcome,
    RunApplication,
    SettingsApplication,
    WorkflowApplication,
)


class _Runs:
    pass


class _Files:
    pass


class _Workflows:
    pass


class _Settings:
    pass


def _container(*, close_callbacks=()) -> ApplicationContainer:
    return ApplicationContainer(
        runs=_Runs(),  # type: ignore[arg-type]
        files=_Files(),  # type: ignore[arg-type]
        workflows=_Workflows(),  # type: ignore[arg-type]
        settings=_Settings(),  # type: ignore[arg-type]
        close_callbacks=close_callbacks,
    )


def test_operation_values_are_frozen_and_structured() -> None:
    failure = OperationFailure("submit", "offline", "server unavailable", True, task_id="task-1")
    outcome = OperationOutcome.failure(failure, value="durable-run")

    assert outcome.ok is False
    assert outcome.value == "durable-run"
    assert outcome.failures == (failure,)
    assert failure.display_text == "server unavailable"
    with pytest.raises(FrozenInstanceError):
        failure.message = "changed"  # type: ignore[misc]


def test_success_and_failure_factories_enforce_outcome_shape() -> None:
    success = OperationOutcome.success(42)
    assert success.ok is True
    assert success.value == 42
    assert success.failures == ()
    with pytest.raises(ValueError, match="at least one failure"):
        OperationOutcome.failure()


def test_container_exposes_facades_while_open_and_rejects_access_after_close() -> None:
    container = _container()
    assert container.runs is not None
    assert container.files is not None
    assert container.workflows is not None
    assert container.settings is not None

    container.close()

    assert container.closed is True
    with pytest.raises(ApplicationClosedError):
        _ = container.runs
    with pytest.raises(ApplicationClosedError):
        container.ensure_open()


def test_container_closes_resources_once_in_reverse_order() -> None:
    calls: list[str] = []
    container = _container(close_callbacks=(lambda: calls.append("pool"), lambda: calls.append("monitor")))

    container.close()
    container.close()

    assert calls == ["monitor", "pool"]


def test_container_attempts_all_closers_and_remains_closed_on_failure() -> None:
    calls: list[str] = []

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("broken closer")

    container = _container(close_callbacks=(lambda: calls.append("last"), fail))
    with pytest.raises(ExceptionGroup, match="application shutdown failed"):
        container.close()

    assert calls == ["fail", "last"]
    assert container.closed is True
    container.close()


def test_public_facade_protocols_do_not_expose_unbounded_any() -> None:
    for protocol in (RunApplication, FilesApplication, WorkflowApplication, SettingsApplication):
        for name, member in vars(protocol).items():
            if name.startswith("_") or not callable(member):
                continue
            hints = get_type_hints(member)
            assert "Any" not in repr(hints), f"{protocol.__name__}.{name} exposes Any"
