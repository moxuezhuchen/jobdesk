from __future__ import annotations

import pytest

from jobdesk_app.services.run_coordinator import RunOperationError


def test_run_operation_error_is_string_compatible_but_typed() -> None:
    error = RunOperationError(
        code="remote_timeout",
        stage="refresh",
        message="refresh timed out",
        retryable=True,
        task_id="task-1",
        detail="ssh timeout",
    )
    assert isinstance(error, str)
    assert str(error) == "refresh timed out"
    assert error.code == "remote_timeout"
    assert error.stage == "refresh"
    assert error.retryable is True
    assert error.task_id == "task-1"
    with pytest.raises(AttributeError):
        error.code = "other"  # type: ignore[misc]
