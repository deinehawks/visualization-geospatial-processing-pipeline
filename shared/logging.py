from __future__ import annotations

import contextvars
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Optional


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
GREY = "\033[90m"

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


# ================================
# Context Filter
# ================================

_STAGE_CONTEXT_BY_LOGGER: contextvars.ContextVar[dict[str, str]] = (
    contextvars.ContextVar("stage_context_by_logger", default={})
)


class ContextFilter(logging.Filter):

    def __init__(
        self,
        run_id: str = "",
        stage_name: str = "",
        *,
        logical_name: str = "",
        logger_key: str = "",
    ) -> None:
        super().__init__()
        self.run_id:     str = run_id
        self.stage_name: str = stage_name
        self.logical_name = logical_name
        self.logger_key = logger_key

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        context = _STAGE_CONTEXT_BY_LOGGER.get()
        record.stage_name = context.get(self.logger_key, self.stage_name)
        if self.logical_name:
            record.name = self.logical_name
        return True


def get_context_filter(logger: logging.Logger) -> Optional[ContextFilter]:
    for f in logger.filters:
        if isinstance(f, ContextFilter):
            return f
    return None


def set_stage_context(logger: logging.Logger, stage_name: str) -> None:
    ctx = get_context_filter(logger)
    if ctx is not None:
        stage_context = dict(_STAGE_CONTEXT_BY_LOGGER.get())
        if stage_name:
            stage_context[ctx.logger_key] = stage_name
        else:
            stage_context.pop(ctx.logger_key, None)
        _STAGE_CONTEXT_BY_LOGGER.set(stage_context)


# ================================
# Filters / Formatters
# ================================

class StripAnsiFilter(logging.Filter):

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = strip_ansi(record.msg)
        if record.args:
            record.args = tuple(
                strip_ansi(a) if isinstance(a, str) else a
                for a in record.args
            )
        if not hasattr(record, "run_id"):
            record.run_id = ""
        if not hasattr(record, "stage_name"):
            record.stage_name = ""
        return True


class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG":    CYAN,
        "INFO":     WHITE,
        "WARNING":  YELLOW,
        "ERROR":    RED,
        "CRITICAL": RED + BOLD,
    }

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "run_id"):
            record.run_id = ""
        if not hasattr(record, "stage_name"):
            record.stage_name = ""

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


# ================================
# Format strings
# ================================

_LOG_FORMAT_FILE = (
    "%(asctime)s | %(levelname)-8s | %(name)s"
    " | %(run_id)s | %(stage_name)s"
    " | %(message)s"
)

_LOG_FORMAT_CONSOLE = (
    "%(asctime)s | %(levelname)-8s | %(name)s"
    " | %(stage_name)s"
    " | %(message)s"
)

DATE_FORMAT = "%H:%M:%S"


# ================================
# Logger factory
# ================================

def _logger_instance_name(
    name: str,
    *,
    log_file: Optional[Path | str],
    run_id: str,
) -> str:
    if log_file is None and not run_id:
        return name

    log_identity = ""
    if log_file is not None:
        log_identity = str(Path(log_file).resolve())

    digest = hashlib.sha1(
        f"{name}|{run_id}|{log_identity}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{name}.__owned__.{digest}"


def get_logger(
    name: str,
    log_file: Optional[Path | str] = None,
    level: int = logging.INFO,
    to_console: bool = True,
    *,
    run_id: str = "",
) -> logging.Logger:

    logger_name = _logger_instance_name(name, log_file=log_file, run_id=run_id)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False

    if getattr(logger, "_configured", False):
        return logger

    # ---- Context filter (run_id / stage_name) ----
    logger.addFilter(
        ContextFilter(
            run_id=run_id,
            logical_name=name,
            logger_key=logger_name,
        )
    )

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(level)
        fh.addFilter(StripAnsiFilter())
        fh.setFormatter(logging.Formatter(
            _LOG_FORMAT_FILE, datefmt=DATE_FORMAT))
        logger.addHandler(fh)

    if to_console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(ColorFormatter(
            _LOG_FORMAT_CONSOLE, datefmt=DATE_FORMAT))
        logger.addHandler(ch)

    setattr(logger, "_configured", True)
    return logger


# ================================
# Pretty dividers / banners
# ================================

def line(width: int = 70) -> str:
    return "─" * width


def stage_banner(logger: logging.Logger, title: str) -> None:
    logger.info(f"{BLUE}{line()}{RESET}")
    logger.info(f"{BLUE}{BOLD} {title}{RESET}")
    logger.info(f"{BLUE}{line()}{RESET}")


def pipeline_header(logger: logging.Logger, run_id: str) -> None:
    logger.info(f"{MAGENTA}{line()}{RESET}")
    logger.info(f"{MAGENTA}{BOLD}RGB PIPELINE START{RESET}")
    logger.info(f"{MAGENTA}Run ID : {run_id}{RESET}")
    logger.info(f"{MAGENTA}{line()}{RESET}")


def pipeline_footer(
    logger: logging.Logger,
    runtime_seconds: float,
    success: bool,
) -> None:
    logger.info(f"{MAGENTA}{line()}{RESET}")
    if success:
        logger.info(f"{GREEN}{BOLD}PIPELINE FINISHED SUCCESSFULLY{RESET}")
    else:
        logger.info(f"{RED}{BOLD}PIPELINE FAILED{RESET}")
    logger.info(f"{MAGENTA}Total runtime : {runtime_seconds:.2f}s{RESET}")
    logger.info(f"{MAGENTA}{line()}{RESET}")


def pipeline_paused(logger: logging.Logger, run_id: str, after_stage: str) -> None:
    logger.warning(f"{YELLOW}{line()}{RESET}")
    logger.warning(f"{YELLOW}{BOLD}PIPELINE PAUSED{RESET}")
    logger.warning(f"{YELLOW}   Run ID      : {run_id}{RESET}")
    logger.warning(f"{YELLOW}   After stage : {after_stage}{RESET}")
    logger.warning(
        f"{YELLOW}   Delete pause.flag and re-run to resume.{RESET}")
    logger.warning(f"{YELLOW}{line()}{RESET}")


def pipeline_canceled(logger: logging.Logger, run_id: str) -> None:
    logger.warning(f"{YELLOW}{line()}{RESET}")
    logger.warning(f"{YELLOW}{BOLD}PIPELINE CANCELED (WebODM UI){RESET}")
    logger.warning(f"{YELLOW}   Run ID : {run_id}{RESET}")
    logger.warning(f"{YELLOW}{line()}{RESET}")


def survey_log_path(base_dir: Path | str, survey_id: str, module_name: str) -> Path:
    return Path(base_dir) / "data" / "logs" / survey_id / f"{module_name}.log"


# ================================
# Module-level progress helpers
# ================================

def log_section(logger: logging.Logger, title: str, *, width: int = 52) -> None:
    bar = "═" * width
    logger.info(f"{CYAN}{bar}{RESET}")
    logger.info(f"{CYAN}{BOLD}◆  {title}{RESET}")
    logger.info(f"{CYAN}{bar}{RESET}")


def log_step(logger: logging.Logger, index: int, title: str) -> None:
    logger.info(f"{BLUE}┌─ [{index}] {title}{RESET}")


def log_ok(logger: logging.Logger, message: str) -> None:
    logger.info(f"{GREEN}└─ {message}{RESET}")


def log_warn(logger: logging.Logger, message: str) -> None:
    logger.warning(f"{YELLOW}└─ {message}{RESET}")


def log_progress(
    logger: logging.Logger,
    message: str,
    *,
    current: int,
    total: int,
    elapsed: float,
    width: int = 80,
) -> None:

    pct = int(current / total * 100) if total else 0

    # ── terminal bar ─────────────────────────────────────────────────────────
    bar_width = 16
    filled = int(bar_width * current / total) if total else 0
    bar = f"{'█' * filled}{'░' * (bar_width - filled)}"

    term_line = (
        f"{CYAN}{message}{RESET}  "
        f"{WHITE}{current:>{len(str(total))}}/{total}{RESET}  "
        f"{YELLOW}{pct:>3}%{RESET}  "
        f"{GREY}|{bar}|{RESET}  "
        f"{GREY}{elapsed:.1f}s{RESET}"
    )

    plain_len = len(strip_ansi(term_line))
    padded = term_line + " " * max(0, width - plain_len)

    sys.stdout.write(f"\r{padded}")
    sys.stdout.flush()

    logger.debug(
        f"{message} {current}/{total} ({pct}%) elapsed={elapsed:.1f}s")


def log_progress_done(logger: logging.Logger, message: str, total: int, elapsed: float) -> None:

    sys.stdout.write("\n")
    sys.stdout.flush()
    logger.info(
        f"{GREEN}└─ {message} — {total} files in {elapsed:.1f}s{RESET}")


# ================================
# Stage lifecycle log helpers
# Called exclusively by StageRunner
# ================================

def log_stage_start(logger: logging.Logger, stage_name: str) -> None:
    set_stage_context(logger, stage_name)
    logger.info(f"{BLUE}{line()}{RESET}")
    logger.info(f"{BLUE}{BOLD} START   | {stage_name}{RESET}")
    logger.info(f"{BLUE}{line()}{RESET}")


def log_stage_done(logger: logging.Logger, stage_name: str, runtime: float) -> None:
    logger.info(
        f"{GREEN}{BOLD}DONE    | {stage_name} | {runtime:.2f}s{RESET}")
    set_stage_context(logger, "")


def log_stage_skip(logger: logging.Logger, stage_name: str) -> None:
    logger.info(f"{YELLOW}SKIP    | {stage_name} (already completed){RESET}")


def log_stage_fail(logger: logging.Logger, stage_name: str, runtime: float) -> None:
    logger.error(f"{RED}{BOLD}FAILED  | {stage_name} | {runtime:.2f}s{RESET}")
    set_stage_context(logger, "")


def log_stage_canceled(logger: logging.Logger, stage_name: str, runtime: float) -> None:
    logger.warning(f"{YELLOW}CANCELED | {stage_name} | {runtime:.2f}s{RESET}")
    set_stage_context(logger, "")


def log_stage_retry(
    logger: logging.Logger,
    stage_name: str,
    attempt: int,
    max_attempts: int,
    delay: int,
    error: Exception,
) -> None:

    logger.warning(
        f"{YELLOW}RETRY   | {stage_name} | attempt {attempt}/{max_attempts} "
        f"— retrying in {delay}s | {error}{RESET}"
    )


def log_stale_stage(logger: logging.Logger, stage_name: str) -> None:

    logger.warning(
        f"{YELLOW}STALE   | {stage_name} "
        f"— previous run did not finish cleanly; marking failed and rerunning.{RESET}"
    )


def log_output_loaded(logger: logging.Logger, output_key: str) -> None:

    logger.info(
        f"{GREY} ↳ Loaded saved output → state['{output_key}']{RESET}")


# Quality Gate CLI helper
_QG_WIDTH = 54


def _qg_line(char: str = "─") -> str:
    return char * _QG_WIDTH


def quality_gate_prompt(
    logger: logging.Logger,
    survey_id: str,
    project_id: int | str,
    task1: dict,
    task2: dict,
    webodm_url: str = "",
    task4: dict | None = None,
    fallback_review: bool = False,   # kept for compat, no longer used
) -> str:
    """Display the quality gate prompt and return the operator's input."""

    def task_label(t: dict) -> str:
        name = t.get("name") or "—"
        tid  = t.get("id")   or "—"
        return f"{name}  (id={tid})"

    dashboard = f"{webodm_url}/dashboard/{project_id}" if webodm_url else "—"

    panel = [
        f"{MAGENTA}{BOLD}{_qg_line('═')}{RESET}",
        f"{MAGENTA}{BOLD}  QUALITY GATE{RESET}",
        f"{MAGENTA}{_qg_line()}{RESET}",
        f"  Survey     : {CYAN}{survey_id}{RESET}",
        f"  Project ID : {CYAN}{project_id}{RESET}",
    ]

    # Only show tasks that actually ran
    if task1 and task1.get("id"):
        panel.append(f"  Task 1     : {WHITE}{task_label(task1)}{RESET}")
    if task2 and task2.get("id"):
        panel.append(f"  Task 2     : {WHITE}{task_label(task2)}{RESET}")
    if task4 and task4.get("id"):
        panel.append(f"  Task 4     : {YELLOW}{task_label(task4)}{RESET}")

    # Show restart target hint based on what ran
    if task4 and task4.get("id"):
        restart_hint = "t4"
    elif task2 and task2.get("id"):
        restart_hint = "t2"
    else:
        restart_hint = "t1"

    panel += [
        f"  Dashboard  : {GREY}{dashboard}{RESET}",
        f"{MAGENTA}{_qg_line()}{RESET}",
        f"  {BOLD}Commands{RESET}",
        f"  {YELLOW}yes / y{RESET}                       → Approve and proceed to QGIS",
        f"  {YELLOW}fail / f{RESET}                      → Fail the pipeline",
        f"  {YELLOW}restart{RESET}                       → Restart primary task from dataset",
        f"  {YELLOW}restart {restart_hint}{RESET}                   → Restart specific task",
        f"  {YELLOW}restart {restart_hint} <stage>{RESET}           → Restart from a stage",
        f"{MAGENTA}{_qg_line()}{RESET}",
        f"  {GREY}Stages (restart-from):{RESET}",
        f"  {GREY}  dataset · opensfm (sfm) · openmvs (mvs){RESET}",
        f"  {GREY}  odm_filterpoints (filterpoints/point_filtering){RESET}",
        f"  {GREY}  odm_meshing (meshing) · mvs_texturing (texturing){RESET}",
        f"  {GREY}  odm_georeferencing (georeferencing) · odm_dem (dem){RESET}",
        f"  {GREY}  odm_orthophoto (orthophoto) · odm_report (report){RESET}",
        f"  {GREY}  odm_postprocess (postprocess){RESET}",
        f"{MAGENTA}{_qg_line('═')}{RESET}",
    ]

    for ln in panel:
        logger.info(ln)

    sys.stdout.write(f"\n{BOLD}  Your decision:{RESET} ")
    sys.stdout.flush()
    return input().strip().lower()
