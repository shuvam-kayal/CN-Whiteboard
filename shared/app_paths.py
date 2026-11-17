"""Stable application-data paths for source and frozen Windows execution."""

import os
from pathlib import Path
import sys


APP_DIRECTORY_NAME = "LANWhiteboard"


def is_frozen() -> bool:
    """Return whether the current process was launched as a frozen executable."""
    return bool(getattr(sys, "frozen", False))


def app_data_directory() -> Path:
    """Return the stable per-user root for packaged data, or project root in source mode."""
    if not is_frozen():
        return Path(__file__).resolve().parents[1]

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIRECTORY_NAME
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / APP_DIRECTORY_NAME
    return Path.home() / ".local" / "share" / APP_DIRECTORY_NAME


def boards_directory() -> Path:
    return app_data_directory() / "data" / "boards"


def logs_directory() -> Path:
    return app_data_directory() / "logs"
