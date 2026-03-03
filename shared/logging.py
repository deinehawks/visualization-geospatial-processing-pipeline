# shared/logging.py
from __future__ import annotations
from pathlib import Path
from typing import Optional

import logging
import re
import sys


# ================================
# ANSI COLORS
# ================================

RESET = "\033[0m"
BOLD = "\033[1m"

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"
WHITE = "\033[97m"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


# ================================
# Filters / Formatters
# ================================

class StripAnsiFilter(logging.Filter):
    """Ensures file logs never contain ANSI escape codes (even if message includes them)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = strip_ansi(record.msg)
        # If args contain strings with ANSI codes, you could strip them too:
        if record.args:
            new_args = []
            for a in record.args:
                if isinstance(a, str):
                    new_args.append(strip_ansi(a))
                else:
                    new_args.append(a)
            record.args = tuple(new_args)
        return True


class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "INFO": WHITE,
        "WARNING": YELLOW,
        "ERROR": RED,
        "CRITICAL": RED + BOLD,
        "DEBUG": CYAN,
    }

    def format(self, record: logging.LogRecord) -> str:
        # IMPORTANT: don't permanently mutate the record (shared by handlers)
        original_levelname = record.levelname
        original_name = record.name

        try:
            color = self.LEVEL_COLORS.get(original_levelname, WHITE)
            record.levelname = f"{color}{original_levelname:<8}{RESET}"
            record.name = f"{CYAN}{original_name}{RESET}"
            return super().format(record)
        finally:
            record.levelname = original_levelname
            record.name = original_name


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def get_logger(
    name: str,
    log_file: Optional[Path | str] = None,
    level: int = logging.INFO,
    to_console: bool = True,
) -> logging.Logger:
    """
    Create or return a configured logger.

    - Safe to call multiple times (won't duplicate handlers).
    - File handler: clean logs (no ANSI)
    - Console handler: colored logs
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if getattr(logger, "_configured", False):
        return logger

    # ---- File handler (NO COLORS + STRIP ANSI) ----
    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.addFilter(StripAnsiFilter()) 
        file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    # ---- Console handler (WITH COLORS) ----
    if to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = ColorFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    logger._configured = True  # type: ignore[attr-defined]
    return logger


# ================================
# Pretty CLI Helpers
# ================================

def line(width: int = 70) -> str:
    return "─" * width


def stage_banner(logger: logging.Logger, title: str) -> None:
    logger.info(f"{BLUE}{line()}{RESET}")
    logger.info(f"{BLUE}{BOLD}▶ {title}{RESET}")
    logger.info(f"{BLUE}{line()}{RESET}")


def pipeline_header(logger: logging.Logger, run_id: str) -> None:
    logger.info(f"{MAGENTA}{line()}{RESET}")
    logger.info(f"{MAGENTA}{BOLD}🚀 RGB PIPELINE START{RESET}")
    logger.info(f"{MAGENTA}Run ID: {run_id}{RESET}")
    logger.info(f"{MAGENTA}{line()}{RESET}")


def pipeline_footer(logger: logging.Logger, runtime_seconds: float, success: bool) -> None:
    logger.info(f"{MAGENTA}{line()}{RESET}")
    if success:
        logger.info(f"{GREEN}{BOLD}✅ PIPELINE FINISHED SUCCESSFULLY{RESET}")
    else:
        logger.info(f"{RED}{BOLD}❌ PIPELINE FAILED{RESET}")
    logger.info(f"{MAGENTA}Total runtime: {runtime_seconds:.2f}s{RESET}")
    logger.info(f"{MAGENTA}{line()}{RESET}")


def survey_log_path(base_dir: Path | str, survey_id: str, module_name: str) -> Path:
    base = Path(base_dir)
    return base / "data" / "logs" / survey_id / f"{module_name}.log"
