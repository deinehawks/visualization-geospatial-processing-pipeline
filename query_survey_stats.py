#!/usr/bin/env python3
"""
query_survey_stats.py
─────────────────────
Query runtime statistics and resume history for one or more survey IDs
from pipeline.db and the shared log files.

Usage:
    python query_survey_stats.py
    python query_survey_stats.py --surveys AH-026005 AH-026006
    python query_survey_stats.py --db path/to/pipeline.db --logs-dir path/to/logs

Output: rich console tables — no files written.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich import box

# ── defaults ────────────────────────────────────────────────────────────────
DEFAULT_DB      = Path("data/pipeline.db")
DEFAULT_LOGS    = Path("data/logs")
DEFAULT_SURVEYS = [
    "AH-026010", "AH-026011", "AH-026012", "AH-026013", "AH-026014", "AH-026015", "AH-026016", 
    "AH-026017", "AH-026018", "AH-026019", "AH-026020", "AH-026021", "AH-026022", "AH-026023",
    "AH-026024", "AH-026025", "AH-026026", 
]

STAGE_ORDER = [
    "data_segregation",
    "cross_run_filter",
    "kml_boundary",
    "webodm",
    "quality_gate",
    "qgis",
]

LOG_FILES = {
    "data_segregation": "data_segregation.log",
    "cross_run_filter": "cross_run_filter.log",
    "kml_boundary":     "kml.log",
    "webodm":           "webodm.log",
    "quality_gate":     "pipeline.log",
    "qgis":             "qgis.log",
    "pipeline":         "pipeline.log",
}

# Log line pattern:  HH:MM:SS | LEVEL    | logger | run_id | stage | message
LOG_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}:\d{2})"
    r"\s*\|\s*(?P<level>\S+)"
    r"\s*\|\s*(?P<logger>[^|]+?)"
    r"\s*\|\s*(?P<run_id>[^|]*?)"
    r"\s*\|\s*(?P<stage>[^|]*?)"
    r"\s*\|\s*(?P<message>.+)$"
)

console = Console(width=160)


# ── helpers ──────────────────────────────────────────────────────────────────

def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


_ISO_TIME_RE = re.compile(r"T(\d{2}:\d{2}:\d{2})")


def fmt_time_hms(iso_str: Optional[str]) -> str:
    """
    Extract just the HH:MM:SS portion from an ISO-8601 timestamp like
    '2026-06-19T06:28:27.110345+00:00'.

    Previously this was done with a naive [-8:] slice, which grabs the
    last 8 characters of the *whole string* — landing inside the
    fractional-seconds/timezone-offset tail instead of the actual time
    (e.g. '15+00:00' instead of '06:28:27'). This matches the 'T' marker
    instead, so it's correct regardless of whether microseconds or a
    timezone offset are present.
    """
    if not iso_str or iso_str == "—":
        return "—"
    m = _ISO_TIME_RE.search(iso_str)
    if m:
        return m.group(1)
    # Not ISO-shaped (e.g. already just a time, or unexpected format) —
    # fall back to showing it as-is rather than guessing wrong.
    return iso_str


def status_style(status: str) -> str:
    return {
        "completed": "green",
        "failed":    "red",
        "running":   "yellow",
        "paused":    "cyan",
    }.get(status, "white")


def stage_style(stage: str) -> str:
    return {
        "data_segregation": "bright_cyan",
        "cross_run_filter": "cyan",
        "kml_boundary":     "bright_blue",
        "webodm":           "magenta",
        "quality_gate":     "yellow",
        "qgis":             "bright_green",
    }.get(stage, "white")


# ── DB queries ───────────────────────────────────────────────────────────────

def load_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        console.print(f"[red]pipeline.db not found: {db_path}[/red]")
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_runs_for_survey(conn: sqlite3.Connection, survey_id: str) -> List[dict]:
    rows = conn.execute(
        """
        SELECT run_id, survey_id, status, started_at, finished_at,
               total_runtime_seconds, paused_at, paused_after_stage,
               pause_reason, source_dir, year
        FROM runs
        WHERE survey_id = ?
        ORDER BY started_at ASC
        """,
        (survey_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_stages_for_run(conn: sqlite3.Connection, run_id: str) -> List[dict]:
    rows = conn.execute(
        """
        SELECT id, stage_name, status, started_at, finished_at,
               runtime_seconds, error_message
        FROM stages
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_survey_totals(conn: sqlite3.Connection, survey_id: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT survey_id, status, started_at, finished_at, total_runtime_seconds
        FROM surveys
        WHERE survey_id = ?
        """,
        (survey_id,),
    ).fetchone()
    return dict(row) if row else None


def get_stage_outputs_for_run(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    """
    Return {stage_name: parsed_output_json} for every stage row belonging
    to this run, keeping the latest row per stage_name (any status — a
    'completed' row is preferred, but a 'failed' row's output_json is
    still useful for partial task info like task4 fallback attempts).

    output_json's exact shape has changed release to release (task dict
    nesting, key names, etc.), so callers should walk it defensively
    rather than assume a fixed schema — see extract_task_runtimes().
    """
    rows = conn.execute(
        """
        SELECT stage_name, status, output_json
        FROM stages
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    ).fetchall()

    outputs: Dict[str, Any] = {}
    for r in rows:
        if not r["output_json"]:
            continue
        try:
            parsed = json.loads(r["output_json"])
        except Exception:
            continue
        # Prefer the latest 'completed' row per stage, but don't clobber
        # a completed row with a later failed one (which may have partial
        # or misleading data).
        prev = outputs.get(r["stage_name"])
        if prev is None:
            outputs[r["stage_name"]] = parsed
        elif r["status"] == "completed":
            outputs[r["stage_name"]] = parsed
    return outputs


def get_qgis_selected_task(qgis_output: Optional[dict]) -> Optional[str]:
    """
    Figure out which WebODM task (task2/task4) a qgis stage's output was
    for, from its output_json. The field name for this has changed across
    pipeline versions — current code writes 'selected_webodm_task', older
    code wrote 'main_ortho_source' — so check both rather than assuming
    one schema.
    """
    if not qgis_output:
        return None
    for key in ("selected_webodm_task", "main_ortho_source", "selected_task", "task"):
        val = qgis_output.get(key)
        if val:
            return str(val).lower()
    return None


_TASK_KEY_RE = re.compile(r"^task\d+$", re.IGNORECASE)


def extract_task_runtimes(stage_outputs: Dict[str, Any]) -> Dict[str, dict]:
    """
    Walk every stage's output_json (webodm, quality_gate, etc. — task4
    fallback results can land under either depending on code version) and
    pull out any sub-dict that looks like a WebODM task record: nested
    under a key like 'task1' / 'task2' / 'task4', containing at least a
    'runtime_seconds' field.

    This is intentionally schema-loose (recursive key scan, not a fixed
    path) so it keeps working across pipeline versions that renamed or
    re-nested these fields, as long as the 'taskN' key + runtime_seconds
    convention holds.
    """
    found: Dict[str, dict] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    isinstance(value, dict)
                    and _TASK_KEY_RE.match(str(key))
                    and "runtime_seconds" in value
                ):
                    # Keep the record with the largest runtime / most info
                    # if the same task key shows up more than once.
                    existing = found.get(key.lower())
                    if existing is None or (
                        (value.get("runtime_seconds") or 0)
                        >= (existing.get("runtime_seconds") or 0)
                    ):
                        found[key.lower()] = value
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for output in stage_outputs.values():
        _walk(output)

    return found


# ── log parsing ──────────────────────────────────────────────────────────────

def parse_event_message(message: str) -> Tuple[Optional[str], Dict[str, str]]:
    """
    Parse ADR-013 `event=<name> key=value ...` messages.

    Returns `(None, {})` for historical free-form log messages. Values are
    parsed with shell-like quoting so messages emitted by shared.logging.log_event()
    round-trip without changing the outer log-file shape.
    """
    message = message.strip()
    if not message.startswith("event="):
        return None, {}

    try:
        parts = shlex.split(message, posix=True)
    except ValueError:
        return None, {}

    fields: Dict[str, str] = {}
    event_name: Optional[str] = None
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if not key:
            continue
        if key == "event":
            event_name = value
        else:
            fields[key] = value

    return event_name, fields

def parse_log_events(
    logs_dir: Path,
    run_ids: List[str],
) -> Dict[str, List[dict]]:
    """
    Parse all log files and return events keyed by run_id.
    Each event: {time, level, logger, stage, message, event, fields, log_file}
    """
    run_id_set = set(run_ids)
    events: Dict[str, List[dict]] = defaultdict(list)

    for log_name in set(LOG_FILES.values()):
        log_path = logs_dir / log_name
        if not log_path.exists():
            continue
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = LOG_RE.match(line.strip())
                if not m:
                    continue
                run_id = m.group("run_id").strip()
                if run_id not in run_id_set:
                    continue
                message = m.group("message").strip()
                event_name, fields = parse_event_message(message)
                events[run_id].append({
                    "time":     m.group("time").strip(),
                    "level":    m.group("level").strip(),
                    "logger":   m.group("logger").strip(),
                    "stage":    m.group("stage").strip(),
                    "message":  message,
                    "event":    event_name,
                    "fields":   fields,
                    "log_file": log_name,
                })

    return events


# Timing regexes for messages that only ever exist in the logs (not
# persisted to DB). Each has a primary (current-format) pattern and a
# looser fallback, since exact wording has changed across code revisions
# and will likely change again — better to catch a rough match than miss
# it entirely.
_UPLOAD_CACHE_RE_PRIMARY = re.compile(
    r"upload cache ready.*?images=(?P<images>\d+).*?copy_time=(?P<seconds>[\d.]+)s",
    re.IGNORECASE,
)
_UPLOAD_CACHE_RE_FALLBACK = re.compile(
    r"upload cache.*?(?P<images>\d+)\s*images.*?(?P<seconds>[\d.]+)\s*s\b",
    re.IGNORECASE,
)

_CLIP_ELAPSED_RE_PRIMARY = re.compile(
    r"clip complete.*?elapsed=(?P<seconds>[\d.]+)s",
    re.IGNORECASE,
)
_CLIP_ELAPSED_RE_FALLBACK = re.compile(
    r"clip.*?elapsed[=: ]+(?P<seconds>[\d.]+)\s*s",
    re.IGNORECASE,
)

_TILE_ELAPSED_RE_PRIMARY = re.compile(
    r"tiles generated.*?~?(?P<tiles>[\d,]+)\s*tiles.*?elapsed=(?P<seconds>[\d.]+)s",
    re.IGNORECASE,
)
_TILE_ELAPSED_RE_FALLBACK = re.compile(
    r"tile.*?elapsed[=: ]+(?P<seconds>[\d.]+)\s*s",
    re.IGNORECASE,
)

# Which WebODM task (task2 / task4) the clip+tile run was for. Task 2 is
# the normal bounded output; Task 4 only exists as a fallback when Task 2
# failed quality gate — so which one shows up varies run to run, and we
# want the label attached to the timing, not just a bare duration.
#
# Preferred source: the explicit selection line QGIS logs before clipping.
_SELECTED_TASK_RE = re.compile(
    r"QGIS selected orthomosaic:\s*task=(?P<task>\w+)",
    re.IGNORECASE,
)
# Fallback source: "Clipping selected orthomosaic (task4) -> ..." — same
# info, slightly older/newer wording.
_CLIPPING_TASK_RE = re.compile(
    r"clipping selected orthomosaic\s*\((?P<task>\w+)\)",
    re.IGNORECASE,
)
# Last-resort source: the clipped output filename itself often carries a
# "-t2"/"-t4" suffix (e.g. orthomosaic-clipped--xcb-t4.tif). Oldest runs
# in this codebase's history used a generic filename with no such suffix,
# in which case this simply won't match and we fall through to "unknown".
_FILENAME_TASK_RE = re.compile(r"-t(?P<num>\d+)\.tif", re.IGNORECASE)


def _first_match(patterns: List[re.Pattern], msg: str) -> Optional[re.Match]:
    for pat in patterns:
        m = pat.search(msg)
        if m:
            return m
    return None


def extract_log_insights(
    events: List[dict],
    run_id: str,
) -> Dict[str, Any]:
    """
    Pull useful signals out of a run's log events:
    - pause/resume/abort events
    - webodm upload size/count
    - webodm upload CACHE copy time (local staging copy, not WebODM's own
      processing — see _stage_upload_cache in rgb_pipeline.py)
    - qgis clip runtime, tile generation runtime + tile count
    - errors per stage
    """
    insights: Dict[str, Any] = {
        "pause_events":   [],
        "resume_events":  [],
        "abort_events":   [],
        "webodm_images":  None,
        "webodm_size_gb": None,
        "tile_count":     None,
        "stage_errors":   defaultdict(list),
        "explicit_event_counts": defaultdict(int),
        # New, more specific timing signals:
        "upload_cache_images":  None,
        "upload_cache_seconds": None,
        "clip_elapsed_seconds": None,
        "tile_elapsed_seconds": None,
        # Which WebODM task (task2/task4) the clip+tiling ran against.
        # None if it genuinely can't be determined from these logs (older
        # runs before this was logged) — resolved further using DB output
        # as a fallback by the caller.
        "clip_task": None,
        "tile_task": None,
        # Every individual clip attempt seen in the logs, in case the
        # pipeline retried clipping multiple times within one run (with
        # possibly-different elapsed times, or in principle a different
        # task if quality gate flipped selection mid-run).
        "clip_attempts": [],
    }

    current_task: Optional[str] = None

    for ev in events:
        msg   = ev["message"]
        stage = ev["stage"] or ev["logger"]
        event_name = ev.get("event")
        fields = ev.get("fields") or {}

        if event_name:
            insights["explicit_event_counts"][event_name] += 1

        # Pause / resume / abort
        if event_name == "run_paused" or "PAUSE" in msg or "__PIPELINE_PAUSED__" in msg:
            insights["pause_events"].append(ev["time"])
        if "Resuming paused run" in msg or "status set to running" in msg:
            insights["resume_events"].append(ev["time"])
        if event_name == "run_aborted" or "ABORT" in msg or "__PIPELINE_ABORTED__" in msg:
            insights["abort_events"].append(ev["time"])

        # WebODM upload info (unique images headed to WebODM itself)
        if "unique images" in msg and "approximate size" in msg:
            m = re.search(r"(\d+)\s+unique images.*?=\s*([\d.]+)\s*GB", msg)
            if m:
                insights["webodm_images"]  = int(m.group(1))
                insights["webodm_size_gb"] = float(m.group(2))

        # WebODM image-upload local CACHE copy time (_stage_upload_cache):
        # pre-upload copy to a fast local temp dir, distinct from any
        # WebODM-side processing/caching.
        m = _first_match(
            [_UPLOAD_CACHE_RE_PRIMARY, _UPLOAD_CACHE_RE_FALLBACK], msg
        )
        if m:
            try:
                insights["upload_cache_images"] = int(m.group("images"))
                insights["upload_cache_seconds"] = float(m.group("seconds"))
            except (ValueError, IndexError):
                pass

        # Track which WebODM task (task2/task4) is currently selected, so
        # any clip/tile timing seen after this point gets tagged with it.
        # Order matters here: events for a given log file are appended in
        # file order, so this stays correct relative to the clip/tile
        # lines that follow it in the same qgis.log.
        m = _SELECTED_TASK_RE.search(msg) or _CLIPPING_TASK_RE.search(msg)
        if m:
            current_task = m.group("task").lower()

        if event_name == "qgis_command_completed":
            tool = (fields.get("tool") or "").lower()
            try:
                seconds = float(fields.get("elapsed_seconds", ""))
            except ValueError:
                seconds = None
            if seconds is not None:
                if tool == "gdalwarp":
                    insights["clip_elapsed_seconds"] = seconds
                    insights["clip_task"] = current_task
                    insights["clip_attempts"].append(
                        {"task": current_task, "seconds": seconds, "time": ev["time"]}
                    )
                elif tool == "gdal2tiles":
                    insights["tile_elapsed_seconds"] = seconds
                    insights["tile_task"] = current_task

        # QGIS clip runtime
        m = _first_match([_CLIP_ELAPSED_RE_PRIMARY, _CLIP_ELAPSED_RE_FALLBACK], msg)
        if m:
            try:
                seconds = float(m.group("seconds"))
            except (ValueError, IndexError):
                seconds = None
            if seconds is not None:
                # Prefer a task suffix baked into this specific clip's
                # output filename over the tracked context, since it's
                # tied directly to this exact clip event.
                fm = _FILENAME_TASK_RE.search(msg)
                clip_task = f"task{fm.group('num')}" if fm else current_task
                insights["clip_elapsed_seconds"] = seconds
                insights["clip_task"] = clip_task
                insights["clip_attempts"].append(
                    {"task": clip_task, "seconds": seconds, "time": ev["time"]}
                )

        # QGIS tile generation runtime (+ tile count if present)
        m = _first_match([_TILE_ELAPSED_RE_PRIMARY, _TILE_ELAPSED_RE_FALLBACK], msg)
        if m:
            try:
                insights["tile_elapsed_seconds"] = float(m.group("seconds"))
                insights["tile_task"] = current_task
            except (ValueError, IndexError):
                pass
            try:
                if "tiles" in m.groupdict():
                    insights["tile_count"] = int(m.group("tiles").replace(",", ""))
            except (ValueError, IndexError, TypeError):
                pass

        # Tile count (older/looser fallback, in case the elapsed pattern
        # above didn't fire but a tile count is still mentioned)
        if insights["tile_count"] is None and (
            "tiles generated" in msg.lower() or ("~" in msg and "tiles" in msg)
        ):
            m2 = re.search(r"~?([\d,]+)\s+tiles", msg)
            if m2:
                try:
                    insights["tile_count"] = int(m2.group(1).replace(",", ""))
                except ValueError:
                    pass

        # Errors
        if event_name in {"run_failed", "stage_failed", "qgis_command_failed"}:
            error_text = fields.get("error_message") or msg
            error_type = fields.get("error_type")
            if error_type and error_type not in error_text:
                error_text = f"{error_type}: {error_text}"
            insights["stage_errors"][stage].append(error_text[:120])
        elif ev["level"] in ("ERROR", "CRITICAL"):
            insights["stage_errors"][stage].append(msg[:120])

    return insights


# ── per-survey analysis ───────────────────────────────────────────────────────

def analyse_survey(
    survey_id: str,
    conn: sqlite3.Connection,
    logs_dir: Path,
) -> None:
    runs   = get_runs_for_survey(conn, survey_id)
    totals = get_survey_totals(conn, survey_id)

    if not runs:
        console.print(f"[yellow]No runs found for survey [bold]{survey_id}[/bold][/yellow]")
        return

    run_ids    = [r["run_id"] for r in runs]
    log_events = parse_log_events(logs_dir, run_ids)

    # ── Survey header ─────────────────────────────────────────────────────────
    survey_status = (totals or {}).get("status", "unknown")
    survey_total = calculate_elapsed_seconds(runs)

    final_finished_at = next(
        (
            run.get("finished_at")
            for run in reversed(runs)
            if run.get("finished_at")
        ),
        None,
    )
    
    header_color  = status_style(survey_status)

    console.print()
    console.print(Rule(
        f"[bold {header_color}]  {survey_id}  ·  {survey_status.upper()}  "
        f"·  Elapsed time: {fmt_duration(survey_total)}  ",
        style=header_color,
    ))

    console.print(
        f"  [dim]Process Started Time (Raw):[/dim] "
        f"{runs[0].get('started_at') or '—'}"
    )

    console.print(
        f"  [dim]Process Finished Time (Raw):[/dim] "
        f"{final_finished_at or '—'}"
    )

    # ── Resume / restart summary ──────────────────────────────────────────────
    total_runs    = len(runs)
    paused_runs   = sum(1 for r in runs if r["status"] == "paused")
    failed_runs   = sum(1 for r in runs if r["status"] == "failed")
    completed_run = next((r for r in reversed(runs) if r["status"] == "completed"), None)

    all_insights = {
        rid: extract_log_insights(log_events.get(rid, []), rid)
        for rid in run_ids
    }
    total_pauses  = sum(len(v["pause_events"])  for v in all_insights.values())
    total_resumes = sum(len(v["resume_events"]) for v in all_insights.values())
    total_aborts  = sum(len(v["abort_events"])  for v in all_insights.values())
    total_explicit_events = sum(
        sum(v["explicit_event_counts"].values()) for v in all_insights.values()
    )

    summary = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    summary.add_column("Key",   style="dim")
    summary.add_column("Value", style="bold")
    summary.add_row("Total pipeline runs",  str(total_runs))
    summary.add_row("Completed runs",        str(1 if completed_run else 0))
    summary.add_row("Paused runs (in DB)",   str(paused_runs))
    summary.add_row("Failed runs (in DB)",   str(failed_runs))
    summary.add_row("Pause events (logs)",   str(total_pauses))
    summary.add_row("Resume events (logs)",  str(total_resumes))
    summary.add_row("Abort events (logs)",   str(total_aborts))
    summary.add_row("Explicit events (logs)", str(total_explicit_events))
    console.print(summary)

    # ── Per-run breakdown ─────────────────────────────────────────────────────
    for run_idx, run in enumerate(runs, 1):
        rid      = run["run_id"]
        insights = all_insights[rid]
        stages   = get_stages_for_run(conn, rid)
        stage_outputs = get_stage_outputs_for_run(conn, rid)
        task_runtimes = extract_task_runtimes(stage_outputs)

        run_style = status_style(run["status"])
        console.print(
            f"  [dim]Run {run_idx}/{total_runs}[/dim]  "
            f"[bold]{rid[:8]}…[/bold]  "
            f"[{run_style}]{run['status'].upper()}[/{run_style}]  "
            f"[dim]started {run.get('started_at', '—')}[/dim]"
        )

        if not stages:
            console.print("    [dim italic]No stage records found in DB.[/dim italic]")
            continue

        t = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold dim",
            padding=(0, 1),
            expand=False,
        )
        t.add_column("#",        width=3,  justify="right")
        t.add_column("Stage",    width=20)
        t.add_column("Status",   width=10)
        t.add_column("Runtime",  width=12, justify="right")
        t.add_column("Started",  width=10)
        t.add_column("Finished", width=10)
        t.add_column("Notes",    min_width=20)

        # Best record per stage (prefer completed, then latest)
        best: Dict[str, dict] = {}
        attempts: Dict[str, int] = defaultdict(int)
        for s in stages:
            attempts[s["stage_name"]] += 1
            prev = best.get(s["stage_name"])
            if prev is None:
                best[s["stage_name"]] = s
            elif s["status"] == "completed" and prev["status"] != "completed":
                best[s["stage_name"]] = s
            elif s["status"] == "completed" and prev["status"] == "completed":
                best[s["stage_name"]] = s  # keep latest completed

        for idx, sname in enumerate(STAGE_ORDER, 1):
            s = best.get(sname)
            if s is None:
                t.add_row(
                    str(idx),
                    f"[{stage_style(sname)}]{sname}[/{stage_style(sname)}]",
                    "[dim]—[/dim]", "—", "—", "—", "[dim]not reached[/dim]",
                )
                continue

            n_attempts = attempts[sname]
            sstyle     = status_style(s["status"])
            scolor     = stage_style(sname)

            notes_parts = []
            if n_attempts > 1:
                notes_parts.append(f"[yellow]{n_attempts} attempts[/yellow]")
            if s.get("error_message"):
                err = s["error_message"][:60].replace("\n", " ")
                notes_parts.append(f"[red]err: {err}[/red]")
            if sname == "webodm" and insights["webodm_images"]:
                notes_parts.append(
                    f"[dim]{insights['webodm_images']} imgs "
                    f"({insights['webodm_size_gb']:.1f} GB)[/dim]"
                )
            if sname == "qgis" and insights["tile_count"]:
                notes_parts.append(f"[dim]~{insights['tile_count']:,} tiles[/dim]")

            t.add_row(
                str(idx),
                f"[{scolor}]{sname}[/{scolor}]",
                f"[{sstyle}]{s['status']}[/{sstyle}]",
                f"[bold]{fmt_duration(s.get('runtime_seconds'))}[/bold]",
                fmt_time_hms(s.get("started_at")),
                fmt_time_hms(s.get("finished_at")),
                " · ".join(notes_parts) or "[dim]—[/dim]",
            )

        console.print(t)

        # ── WebODM detail: upload caching + per-task runtimes ────────────────
        has_webodm_detail = (
            insights["upload_cache_seconds"] is not None
            or task_runtimes
        )
        if has_webodm_detail:
            wt = Table(
                title="[bold magenta]WebODM detail[/bold magenta]",
                box=box.MINIMAL,
                show_header=True,
                header_style="bold dim",
                padding=(0, 1),
                expand=False,
            )
            wt.add_column("Item")
            wt.add_column("Value", justify="right")

            if insights["upload_cache_seconds"] is not None:
                imgs = insights["upload_cache_images"]
                wt.add_row(
                    "Image upload caching",
                    f"{fmt_duration(insights['upload_cache_seconds'])}"
                    + (f"  ({imgs} imgs)" if imgs else ""),
                )

            # Print tasks in a stable, human order if present: task1, task2,
            # task3, task4, then anything else alphabetically.
            def _task_sort_key(k: str) -> Tuple[int, str]:
                m = re.match(r"task(\d+)", k)
                return (int(m.group(1)) if m else 999, k)

            for tkey in sorted(task_runtimes.keys(), key=_task_sort_key):
                info = task_runtimes[tkey]
                label = tkey.replace("task", "Task ").capitalize()
                rt = info.get("runtime_seconds")
                success = info.get("success")
                status_tag = ""
                if success is True:
                    status_tag = " [green]ok[/green]"
                elif success is False:
                    status_tag = " [red]failed[/red]"
                wt.add_row(f"{label} runtime", f"{fmt_duration(rt)}{status_tag}")

            console.print(wt)

        # ── QGIS detail: clip + tile generation runtimes ──────────────────────
        has_qgis_detail = (
            insights["clip_elapsed_seconds"] is not None
            or insights["tile_elapsed_seconds"] is not None
        )
        if has_qgis_detail:
            # Resolve the DB fallback once we know logs didn't have it.
            db_task = get_qgis_selected_task(stage_outputs.get("qgis"))

            def _task_label(task: Optional[str]) -> str:
                if not task:
                    return ""
                return f"  ({task})"

            qt = Table(
                title="[bold bright_green]QGIS detail[/bold bright_green]",
                box=box.MINIMAL,
                show_header=True,
                header_style="bold dim",
                padding=(0, 1),
                expand=False,
            )
            qt.add_column("Item")
            qt.add_column("Value", justify="right")

            # Clip: show one row per distinct task if the run ever switched
            # (rare, but quality gate can flip task2 -> task4 mid-run), else
            # a single row. Falls back to the DB-derived task if the logs
            # never recorded a task at all for this run (older code).
            clip_attempts = insights["clip_attempts"]
            if clip_attempts:
                tasks_seen = {a["task"] for a in clip_attempts if a["task"]}
                if len(tasks_seen) > 1:
                    # Task changed mid-run — show each task's most recent
                    # attempt separately so it's clear which is which.
                    latest_by_task: Dict[Optional[str], dict] = {}
                    for a in clip_attempts:
                        latest_by_task[a["task"]] = a  # last one wins
                    for task, a in latest_by_task.items():
                        label = task or db_task or "unknown task"
                        qt.add_row(f"Clip runtime ({label})", fmt_duration(a["seconds"]))
                else:
                    task = insights["clip_task"] or db_task
                    n = len(clip_attempts)
                    suffix = _task_label(task)
                    attempt_note = f"  [{n} attempts]" if n > 1 else ""
                    qt.add_row(
                        f"Clip runtime{suffix}",
                        f"{fmt_duration(insights['clip_elapsed_seconds'])}{attempt_note}",
                    )
            elif insights["clip_elapsed_seconds"] is not None:
                task = insights["clip_task"] or db_task
                qt.add_row(
                    f"Clip runtime{_task_label(task)}",
                    fmt_duration(insights["clip_elapsed_seconds"]),
                )

            if insights["tile_elapsed_seconds"] is not None:
                tiles = insights["tile_count"]
                task = insights["tile_task"] or db_task
                qt.add_row(
                    f"Tile generation runtime{_task_label(task)}",
                    fmt_duration(insights["tile_elapsed_seconds"])
                    + (f"  (~{tiles:,} tiles)" if tiles else ""),
                )

            console.print(qt)

        # Pause / abort / resume timeline from logs
        if insights["pause_events"] or insights["abort_events"]:
            console.print(f"    [cyan]Pauses :[/cyan] {', '.join(insights['pause_events']) or '—'}")
            console.print(f"    [red]Aborts :[/red] {', '.join(insights['abort_events']) or '—'}")
            console.print(f"    [green]Resumes:[/green] {', '.join(insights['resume_events']) or '—'}")

        # Log errors per stage
        if insights["stage_errors"]:
            console.print("    [red bold]Log errors:[/red bold]")
            for stage, errs in insights["stage_errors"].items():
                for err in errs[:3]:
                    console.print(f"    [dim]{stage}[/dim] → [red]{err}[/red]")

        console.print()

    # ── Best runtime per stage summary ────────────────────────────────────────
    best_runtimes: Dict[str, float] = {}
    for rid in run_ids:
        for s in get_stages_for_run(conn, rid):
            if (
                s["status"] == "completed"
                and s.get("runtime_seconds") is not None
            ):
                sn = s["stage_name"]
                if sn not in best_runtimes or s["runtime_seconds"] < best_runtimes[sn]:
                    best_runtimes[sn] = s["runtime_seconds"]

    if best_runtimes:
        sum_table = Table(
            title=f"[bold]Best completed runtime per stage — {survey_id}[/bold]",
            box=box.MINIMAL_DOUBLE_HEAD,
            show_header=True,
            header_style="bold",
            padding=(0, 2),
        )
        sum_table.add_column("Stage",      style="bold")
        sum_table.add_column("Runtime",    justify="right")
        sum_table.add_column("% of total", justify="right")

        total_s = sum(best_runtimes.values())
        for sname in STAGE_ORDER:
            rt = best_runtimes.get(sname)
            if rt is None:
                continue
            pct = (rt / total_s * 100) if total_s else 0
            bar = "█" * int(pct / 5)
            sum_table.add_row(
                f"[{stage_style(sname)}]{sname}[/{stage_style(sname)}]",
                f"[bold]{fmt_duration(rt)}[/bold]",
                f"[dim]{bar:<20}[/dim] {pct:5.1f}%",
            )
        sum_table.add_row(
            "[bold]TOTAL[/bold]",
            f"[bold cyan]{fmt_duration(total_s)}[/bold cyan]",
            "",
        )
        console.print(sum_table)


# ── cross-survey comparison ───────────────────────────────────────────────────

def print_comparison(
    survey_ids: List[str],
    conn: sqlite3.Connection,
) -> None:
    console.print()
    console.print(
        Rule(
            "[bold white]  CROSS-SURVEY COMPARISON — BEST STAGES + ELAPSED TIME  ",
            style="white",
        )
    )

    t = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )

    t.add_column("Survey ID",   style="bold",    width=14, no_wrap=True)
    t.add_column("Status",                        width=11, no_wrap=True)
    t.add_column("Runs",        justify="center", width=5,  no_wrap=True)
    t.add_column("Pauses",      justify="center", width=7,  no_wrap=True)
    t.add_column("Segregation", justify="right",  width=12, no_wrap=True)
    t.add_column("Filter",      justify="right",  width=10, no_wrap=True)
    t.add_column("KML",         justify="right",  width=8,  no_wrap=True)
    t.add_column("WebODM",      justify="right",  width=14, no_wrap=True)
    t.add_column("QGate",       justify="right",  width=10, no_wrap=True)
    t.add_column("QGIS",        justify="right",  width=12, no_wrap=True)
    t.add_column("Stage Total", justify="right",  width=12, no_wrap=True)
    t.add_column("Elapsed",     justify="right",  width=12, no_wrap=True)

    for sid in survey_ids:
        runs = get_runs_for_survey(conn, sid)
        totals = get_survey_totals(conn, sid)

        if not runs:
            t.add_row(
                sid,
                "[dim]no data[/dim]",
                *["—"] * 10,
            )
            continue

        status = (totals or {}).get("status", "?")
        elapsed_seconds = calculate_elapsed_seconds(runs)

        sstyle = status_style(status)
        total_runs = len(runs)
        paused_count = sum(
            1 for run in runs
            if run["status"] == "paused"
        )

        # Find the fastest completed runtime for each stage.
        best: Dict[str, float] = {}

        for run in runs:
            stages = get_stages_for_run(conn, run["run_id"])

            for stage in stages:
                runtime = stage.get("runtime_seconds")

                if (
                    stage["status"] != "completed"
                    or runtime is None
                ):
                    continue

                stage_name = stage["stage_name"]

                if (
                    stage_name not in best
                    or runtime < best[stage_name]
                ):
                    best[stage_name] = runtime

        # Sum all best completed stage runtimes.
        stage_total = sum(best.values()) if best else None

        t.add_row(
            sid,
            f"[{sstyle}]{status}[/{sstyle}]",
            str(total_runs),
            str(paused_count) if paused_count else "[dim]0[/dim]",
            fmt_duration(best.get("data_segregation")),
            fmt_duration(best.get("cross_run_filter")),
            fmt_duration(best.get("kml_boundary")),
            fmt_duration(best.get("webodm")),
            fmt_duration(best.get("quality_gate")),
            fmt_duration(best.get("qgis")),
            f"[bold cyan]{fmt_duration(stage_total)}[/bold cyan]",
            f"[bold]{fmt_duration(elapsed_seconds)}[/bold]",
        )

    console.print(t)

def calculate_elapsed_seconds(runs: List[dict]) -> Optional[float]:
    """
    Calculate elapsed wall-clock time from the earliest run start
    to the latest available run finish.

    This includes gaps, waiting time, pauses, and restarts.
    """
    if not runs:
        return None

    first_started = next(
        (
            run.get("started_at")
            for run in runs
            if run.get("started_at")
        ),
        None,
    )

    last_finished = next(
        (
            run.get("finished_at")
            for run in reversed(runs)
            if run.get("finished_at")
        ),
        None,
    )

    if not first_started or not last_finished:
        return None

    try:
        started_dt = datetime.fromisoformat(first_started)
        finished_dt = datetime.fromisoformat(last_finished)
    except (TypeError, ValueError):
        return None

    elapsed = (finished_dt - started_dt).total_seconds()
    return elapsed if elapsed >= 0 else None


# ── entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query pipeline runtime statistics for one or more survey IDs."
    )
    parser.add_argument(
        "--surveys", nargs="+", default=DEFAULT_SURVEYS,
        metavar="SURVEY_ID",
        help="Survey IDs to query (default: AH-026005 … AH-026009)",
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"Path to pipeline.db (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--logs-dir", type=Path, default=DEFAULT_LOGS,
        metavar="DIR",
        help=f"Directory containing *.log files (default: {DEFAULT_LOGS})",
    )
    args = parser.parse_args()

    if not args.logs_dir.exists():
        console.print(
            f"[yellow]Warning: logs directory not found: {args.logs_dir}[/yellow]\n"
            f"[dim]Log-based insights (pauses, upload size, tile count) will be empty.[/dim]"
        )

    conn = load_db(args.db)

    console.print(Panel(
        f"[bold]Pipeline Statistics Report[/bold]\n"
        f"[dim]DB      :[/dim] {args.db}\n"
        f"[dim]Logs    :[/dim] {args.logs_dir}\n"
        f"[dim]Surveys :[/dim] {', '.join(args.surveys)}",
        expand=False,
    ))

    for sid in args.surveys:
        analyse_survey(sid, conn, args.logs_dir)

    if len(args.surveys) > 1:
        print_comparison(args.surveys, conn)

    conn.close()


if __name__ == "__main__":
    main()