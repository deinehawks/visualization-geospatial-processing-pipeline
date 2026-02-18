# shared/logging.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(
    name: str,
    log_file: Optional[Path | str] = None,
    level: int = logging.INFO,
    to_console: bool = True,
) -> logging.Logger:
    """
    Create or return a configured logger.

    - Safe to call multiple times (won't duplicate handlers).
    - If log_file is provided, writes to file.
    - If to_console is True, logs also print to stdout.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # prevent double logging via root

    # Avoid adding duplicate handlers if get_logger is called multiple times
    _already_configured = getattr(logger, "_configured", False)
    if _already_configured:
        return logger

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if to_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    logger._configured = True  # type: ignore[attr-defined]
    return logger


def survey_log_path(base_dir: Path | str, survey_id: str, module_name: str) -> Path:
    """
    Standard path: <base_dir>/data/logs/<survey_id>/<module_name>.log
    """
    base = Path(base_dir)
    return base / "data" / "logs" / survey_id / f"{module_name}.log"
