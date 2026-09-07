"""Read-only, Google-independent run and timing projections."""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


def read_snapshot(database: Path, limit: int = 50000) -> tuple[list[dict], list[dict]]:
    path = Path(database).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Pipeline database must be an existing file")
    # Do not use PipelineRepo/connect(): they initialize schemas and change PRAGMAs.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")  # One consistent snapshot across both tables.
        runs = [dict(r) for r in conn.execute(
            "SELECT run_id, survey_id, status, started_at, finished_at, "
            "total_runtime_seconds FROM runs ORDER BY run_id LIMIT ?", (limit + 1,))]
        stages = [dict(r) for r in conn.execute(
            "SELECT id, run_id, stage_name, status, started_at, finished_at, "
            "runtime_seconds, error_message FROM stages ORDER BY id LIMIT ?", (limit + 1,))]
    if len(runs) > limit or len(stages) > limit:
        raise ValueError("Snapshot exceeds V1 safety limit; narrow/archive the reporting source")
    return runs, stages


def seconds(value):
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid persisted duration")
    return result


def local_time(value):
    if not value:
        return ""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        # No timezone inference for historical data.
        return ""
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def project(runs: list[dict], stages: list[dict]) -> dict[str, dict[str, dict]]:
    """Numeric durations are seconds here; adapters own display formatting.

    Timing buckets accumulate persisted invocations, including failures/pauses.
    Nested WebODM operation timers are drilldowns, never added to parent totals.
    Unknown retry counts, active time, and substage timings are deliberately absent.
    """
    by_run = defaultdict(list)
    for stage in stages:
        by_run[stage["run_id"]].append(stage)
    result = {"runs": {}, "stages": {}, "timing": {}}
    mapping = {
        "data_segregation": "Data Preparation",
        "cross_run_filter": "Cross-run Image Filtering",
        "kml_boundary": "KML Boundary Setter",
        "webodm_task4": "WebODM Orthomosaic (T4)",
        "webodm_task2": "WebODM 3D (T2)",
        "quality_gate": "Quality Inspection",
        "webodm": "WebODM stage total",
        "qgis": "QGIS stage total",
    }
    terminal = {"completed", "failed", "partially_completed", "requires_recovery",
                "canceled", "cancelled", "aborted"}
    for run in runs:
        rid = str(run["run_id"])
        attempts = sorted(by_run[rid], key=lambda s: s["id"])
        finished = run.get("finished_at") if run["status"] in terminal else None
        result["runs"][rid] = {
            "Pipeline Run ID": rid,
            "Survey ID": run.get("survey_id") or "",
            "Process Started Time (Raw)": run.get("started_at") or "",
            "Process Finished Time (Raw)": finished or "",
            "Processing Start Time": local_time(run.get("started_at")),
            "Processing End Time": local_time(finished),
            "Overall Status": run["status"],
            "Final Stage Reached": attempts[-1]["stage_name"] if attempts else "",
        }
        timing = {"Pipeline Run ID": rid, "Survey ID": run.get("survey_id") or "",
                  "Status": run["status"]}
        # Existing runtime is per invocation, not cumulative active time on resume.
        if finished and run.get("total_runtime_seconds") is not None:
            timing["Total Runtime"] = seconds(run["total_runtime_seconds"])
        elif run["status"] not in terminal:
            timing["Total Runtime"] = ""  # Hide stale prior-invocation runtime.
        counts = defaultdict(int)
        grouped = defaultdict(list)
        for stage in attempts:
            name = stage["stage_name"]
            counts[name] += 1
            key = f"{rid}:{stage['id']}"
            row = {
                "Stage Attempt ID": key,
                "Pipeline Run ID": rid,
                "Stage Name": name,
                "Attempt Number": counts[name],
                "Stage Start Time": local_time(stage.get("started_at")),
                "Stage End Time": local_time(stage.get("finished_at")),
                "Status": stage["status"],
                "Error Message": str(stage.get("error_message") or "")[:1000],
            }
            if stage.get("runtime_seconds") is not None:
                row["Stage Runtime"] = seconds(stage["runtime_seconds"])
            result["stages"][key] = row
            grouped[name].append(stage)
        for name, header in mapping.items():
            group = grouped[name]
            # Missing/in-progress measurements are unknown, not zero. Do not
            # silently present a partial sum as a complete stage total.
            if group:
                values = [s.get("runtime_seconds") for s in group]
                if any(s["status"] == "running" for s in group):
                    timing[header] = ""
                elif all(v is not None for v in values):
                    timing[header] = sum(seconds(v) for v in values)
        result["timing"][rid] = timing
    return result
