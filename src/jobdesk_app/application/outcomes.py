"""Stable result and failure values exposed by the application layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


class ApplicationClosedError(RuntimeError):
    """Raised when an operation is requested after application shutdown."""


@dataclass(frozen=True, slots=True)
class OperationFailure:
    """One structured, presentation-safe application operation failure."""

    stage: str
    code: str
    message: str
    retryable: bool
    task_id: str | None = None
    cause_code: str | None = None

    @property
    def display_text(self) -> str:
        """Return the user-facing text without discarding structured fields."""

        return self.message


_OutcomeValue = TypeVar("_OutcomeValue")


@dataclass(frozen=True, slots=True)
class OperationOutcome(Generic[_OutcomeValue]):
    """Immutable success value plus zero or more expected operation failures."""

    value: _OutcomeValue | None = None
    failures: tuple[OperationFailure, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "failures", tuple(self.failures))

    @property
    def ok(self) -> bool:
        """Whether the operation completed without an expected failure."""

        return not self.failures

    @classmethod
    def success(cls, value: _OutcomeValue) -> "OperationOutcome[_OutcomeValue]":
        return cls(value=value)

    @classmethod
    def failure(
        cls,
        *failures: OperationFailure,
        value: _OutcomeValue | None = None,
    ) -> "OperationOutcome[_OutcomeValue]":
        if not failures:
            raise ValueError("a failed operation outcome requires at least one failure")
        return cls(value=value, failures=tuple(failures))


__all__ = ["ApplicationClosedError", "OperationFailure", "OperationOutcome"]
