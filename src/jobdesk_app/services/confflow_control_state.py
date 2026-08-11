"""Durable JobDesk-owned provenance for one selected ConfFlow backend."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Protocol, TypeAlias

from jobdesk_app.core.atomic_write import atomic_write_text

CONTROL_STATE_FILENAME = "control_backend.json"
ControlState: TypeAlias = dict[str, object]


class _RunStateService(Protocol):
    def _run_dir(self, run_id: str) -> Path: ...


def state_path(service: _RunStateService, run_id: str) -> Path:
    return service._run_dir(run_id) / CONTROL_STATE_FILENAME  # noqa: SLF001 - service owns run-dir validation


def load_state(service: _RunStateService, run_id: str) -> ControlState | None:
    path = state_path(service, run_id)
    if not isinstance(path, Path) or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid durable ConfFlow backend state for {run_id}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid durable ConfFlow backend state for {run_id}: expected object")
    backend = value.get("backend")
    if backend != "control":
        raise ValueError(
            f"run {run_id} uses retired ConfFlow backend {backend!r}; legacy runs cannot be resumed after Phase F"
        )
    if value.get("run_id") != run_id:
        raise ValueError(f"invalid durable ConfFlow backend state for {run_id}: run_id mismatch")
    return deepcopy(value)


def save_state(service: _RunStateService, run_id: str, value: ControlState) -> None:
    if value.get("run_id") != run_id:
        raise ValueError("durable ConfFlow backend state run_id mismatch")
    if value.get("backend") != "control":
        raise ValueError("durable ConfFlow backend state backend must be control")
    atomic_write_text(
        state_path(service, run_id),
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


__all__ = ["CONTROL_STATE_FILENAME", "ControlState", "load_state", "save_state", "state_path"]
