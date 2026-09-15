"""Application-wide logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


LOG_FORMAT = (
    "%(asctime)s\t[%(levelname)s]\t%(name)s\t"
    "(%(filename)s:%(lineno)s)%(funcName)s\t%(message)s"
)


def configure_logging(file_path: Path, *, backup_days: int) -> None:
    resolved = file_path.expanduser()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    target = str(resolved.resolve())
    for handler in root.handlers:
        if isinstance(handler, TimedRotatingFileHandler) and handler.baseFilename == target:
            return
    handler = TimedRotatingFileHandler(
        resolved,
        when="midnight",
        backupCount=backup_days,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
