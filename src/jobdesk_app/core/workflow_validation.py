"""Non-authoritative producer diagnostics for workflow authoring.

This module is a diagnostics port only.  It must never be used to accept or
reject a saved workflow; the remote producer contract validation at submit
time is the sole acceptance gate.  There is deliberately no local fallback
validator: keeping a second semantic rule list would create producer drift.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .workflow_editor import WorkflowDiagnostic


class ConfFlowCompatibilityValidator:
    """Call ConfFlow's validator through an injectable diagnostic port.

    The installed producer validator is optional and diagnostic-only.  When
    it is unavailable, the editor reports that fact as a warning instead of
    applying a JobDesk-owned semantic mirror.
    """

    def __init__(
        self,
        *,
        producer_validator: Callable[[dict[str, Any]], Sequence[str]] | None = None,
    ) -> None:
        self._producer_validator = producer_validator

    @staticmethod
    def _load_producer_validator() -> Callable[[dict[str, Any]], Sequence[str]] | None:
        try:
            from confflow.shared.config_validation import validate_yaml_config
        except ImportError:
            return None
        return validate_yaml_config

    def _validator(self) -> Callable[[dict[str, Any]], Sequence[str]] | None:
        return self._producer_validator or self._load_producer_validator()

    def validate(
        self,
        payload: Mapping[str, Any],
        *,
        allow_legacy_placeholder: bool = False,
    ) -> list[WorkflowDiagnostic]:
        """Return producer-owned semantic diagnostics for one canonical map."""

        del allow_legacy_placeholder
        validator = self._validator()
        if validator is None:
            return [
                WorkflowDiagnostic(
                    "warning",
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
        return [WorkflowDiagnostic("warning", "producer.semantic", str(error)) for error in errors]


__all__ = ["ConfFlowCompatibilityValidator"]
