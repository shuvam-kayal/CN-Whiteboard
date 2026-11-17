"""File logging configured for both source and packaged app-data paths."""

import logging
from pathlib import Path

from shared.app_paths import logs_directory


def configure_application_logging(application_name: str, *, console: bool = True) -> Path:
    """Append diagnostics to the stable log directory and optionally the console."""
    directory = logs_directory()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / f"{application_name}.log"
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_path, encoding="utf-8"),
    ]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    return log_path
