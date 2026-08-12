"""Compatibility import path for the application-owned config contract."""

from __future__ import annotations

from jobdesk_app.application import confflow_config_contract as _implementation
from jobdesk_app.application.confflow_config_contract import *  # noqa: F401,F403

__all__ = _implementation.__all__
