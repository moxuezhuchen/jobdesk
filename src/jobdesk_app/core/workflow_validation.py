"""Producer-validation adapter for the workflow authoring boundary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .workflow_editor import WorkflowDiagnostic


class ConfFlowCompatibilityValidator:
    """Call ConfFlow's validator through an injectable compatibility port.

    The installed producer validator is preferred.  The optional fallback is
    retained for chem-less development and for the existing JobDesk private
    compatibility API; neither document parsing nor editor lint imports it.
    """

    def __init__(
        self,
        *,
        producer_validator: Callable[[dict[str, Any]], Sequence[str]] | None = None,
        fallback_validator: Callable[[dict[str, Any]], Sequence[str]] | None = None,
    ) -> None:
        self._producer_validator = producer_validator
        self._fallback_validator = fallback_validator

    @staticmethod
    def _load_producer_validator() -> Callable[[dict[str, Any]], Sequence[str]] | None:
        try:
            from confflow.shared.config_validation import validate_yaml_config
        except ImportError:
            return None
        return validate_yaml_config

    def _validator(self) -> Callable[[dict[str, Any]], Sequence[str]] | None:
        return self._producer_validator or self._load_producer_validator() or self._fallback_validator

    def validate(
        self,
        payload: Mapping[str, Any],
        *,
        allow_legacy_placeholder: bool = False,
    ) -> list[WorkflowDiagnostic]:
        """Return producer-owned semantic diagnostics for one canonical map."""

        validator = self._validator()
        if validator is None:
            return [
                WorkflowDiagnostic(
                    "error",
                    "producer.validator_unavailable",
                    "ConfFlow workflow validator is unavailable",
                )
            ]

        validation_payload = deepcopy(dict(payload))
        global_config = validation_payload.get("global")
        if isinstance(global_config, dict):
            # Executable paths are remote-owned.  Keeping this compatibility
            # policy here avoids making a Windows editor judge remote state.
            global_config.pop("gaussian_path", None)
            global_config.pop("orca_path", None)
        errors = list(validator(validation_payload))
        if allow_legacy_placeholder:
            errors = [
                error
                for error in errors
                if "confgen step requires 'chains'" not in str(error)
            ]
        return [WorkflowDiagnostic("error", "producer.semantic", str(error)) for error in errors]


__all__ = ["ConfFlowCompatibilityValidator"]
