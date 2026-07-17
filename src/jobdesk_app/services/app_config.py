"""Unified application configuration management.

This module provides a centralized AppConfig singleton that manages all paths
and configuration locations for JobDesk. It ensures backward compatibility
with existing configuration access patterns while offering a clean, type-safe
interface for new code.

Configuration Locations:
    - App data: %APPDATA%/JobDesk (configurable via JOBDESK_APPDATA env var)
    - Servers: %APPDATA%/JobDesk/servers.yaml
    - Settings: %APPDATA%/JobDesk/settings.json
    - Runs DB: %APPDATA%/JobDesk/runs/jobdesk.db
    - Logs: %APPDATA%/JobDesk/logs
    - Resources: src/jobdesk_app/resources/ (built-in)
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import ClassVar


class AppConfig:
    """Immutable application configuration singleton with lazy initialization.

    All paths are computed on first access and cached thereafter.
    Thread-safe via double-checked locking pattern.

    Example:
        >>> config = AppConfig.from_default()
        >>> config.ensure_dirs()
        >>> print(config.runs_dir)
        C:\\Users\\...\\AppData\\Roaming\\JobDesk\\runs
    """

    _instance: ClassVar[AppConfig | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    # Path configuration
    app_data_dir: Path
    runs_dir: Path
    logs_dir: Path

    # Configuration files
    servers_path: Path
    settings_path: Path
    presets_path: Path

    # Resource file paths
    resources_dir: Path
    method_presets_dir: Path
    step_presets_dir: Path
    workflow_examples_dir: Path

    def __init__(self) -> None:
        """Initialize paths from environment and project structure.

        This method should not be called directly; use from_default() instead.
        """
        # Determine app data base directory
        app_data_base = os.environ.get("JOBDESK_APPDATA", os.environ.get("APPDATA", str(Path.home())))
        self.app_data_dir = Path(app_data_base) / "JobDesk"

        # Derived directories
        self.runs_dir = self.app_data_dir / "runs"
        self.logs_dir = self.app_data_dir / "logs"

        # Configuration files
        self.servers_path = self.app_data_dir / "servers.yaml"
        self.settings_path = self.app_data_dir / "settings.json"
        self.presets_path = self.app_data_dir / "method_presets.json"

        # Built-in resources (relative to package)
        self.resources_dir = self._get_resources_dir()
        self.method_presets_dir = self.resources_dir / "method_presets"
        self.step_presets_dir = self.resources_dir / "step_presets"
        self.workflow_examples_dir = self.resources_dir / "workflow_examples"

    @staticmethod
    def _get_resources_dir() -> Path:
        """Locate the resources directory.

        Uses the package's __file__ path to find resources, with fallback
        to project root for development environments.
        """
        try:
            # Try to find resources relative to this module
            import jobdesk_app

            package_dir = Path(jobdesk_app.__file__).parent
            resources = package_dir / "resources"
            if resources.exists():
                return resources

            # Fallback: project root / src / jobdesk_app / resources
            project_root = package_dir.parent.parent
            fallback = project_root / "resources"
            if fallback.exists():
                return fallback

        except (ImportError, AttributeError):
            pass

        # Last resort: current working directory
        return Path.cwd() / "src" / "jobdesk_app" / "resources"

    @classmethod
    def from_default(cls) -> "AppConfig":
        """Get the singleton AppConfig instance.

        Thread-safe singleton creation using double-checked locking.

        Returns:
            The singleton AppConfig instance.
        """
        if cls._instance is None:
            with cls._lock:
                # Double-check after acquiring lock
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def ensure_dirs(self) -> None:
        """Ensure all application directories exist.

        Creates the following directories if they don't exist:
        - app_data_dir (%APPDATA%/JobDesk)
        - runs_dir (%APPDATA%/JobDesk/runs)
        - logs_dir (%APPDATA%/JobDesk/logs)

        Thread-safe: uses filesystem-level atomic mkdir with exist_ok=True.
        """
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    # Backward compatibility helpers

    def get_default_servers_path(self) -> Path:
        """Get the default servers.yaml path.

        This provides backward compatibility with the existing
        config.servers module.

        Returns:
            Path to servers.yaml.
        """
        return self.servers_path

    def get_default_runs_db_path(self) -> Path:
        """Get the default run database path.

        Returns:
            Path to jobdesk.db.
        """
        return self.runs_dir / "jobdesk.db"

    def get_logs_dir_path(self) -> Path:
        """Get the logs directory path.

        Returns:
            Path to logs directory.
        """
        return self.logs_dir

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"AppConfig(app_data_dir={self.app_data_dir!r}, "
            f"runs_dir={self.runs_dir!r}, logs_dir={self.logs_dir!r}, "
            f"servers_path={self.servers_path!r}, "
            f"settings_path={self.settings_path!r}, "
            f"resources_dir={self.resources_dir!r})"
        )


# Convenience function for quick access
def get_config() -> AppConfig:
    """Get the global AppConfig singleton.

    This is the recommended way to access application configuration.

    Returns:
        The singleton AppConfig instance.
    """
    return AppConfig.from_default()
