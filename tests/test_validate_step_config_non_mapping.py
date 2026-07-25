"""P-H3 (R-H3) — non-mapping step / params no longer crash the validator."""

from __future__ import annotations

from jobdesk_app.core._confflow_validation import _validate_step_config


def test_validate_step_config_returns_errors_for_null_step() -> None:
    """A ``None`` step must return a list (no TypeError)."""
    errors = _validate_step_config(None, 0)  # type: ignore[arg-type]
    assert isinstance(errors, list)
    assert errors, "expected a non-empty error list for a null step"
    assert "must be a mapping" in errors[0]


def test_validate_step_config_returns_errors_for_string_params() -> None:
    """A ``params`` value that is not a mapping must return a list (no AttributeError)."""
    errors = _validate_step_config(
        {"name": "x", "type": "calc", "params": "oops"}, 0
    )
    assert isinstance(errors, list)
    assert errors, "expected a non-empty error list for a non-dict params"
    assert "params" in errors[0]


def test_validate_step_config_returns_errors_for_list_step() -> None:
    """A step that is a list (not a dict) must return a list."""
    errors = _validate_step_config([1, 2, 3], 0)  # type: ignore[arg-type]
    assert isinstance(errors, list)
    assert errors


def test_validate_step_config_returns_empty_for_well_formed_step() -> None:
    """A well-formed step still returns an empty error list."""
    errors = _validate_step_config(
        {"name": "ok", "type": "calc", "params": {"itask": "sp"}}, 0
    )
    assert errors == []
