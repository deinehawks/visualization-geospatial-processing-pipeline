"""Snapshot reconciliation, persistent selection, and sparse cell planning."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

TABS = {"runs": "Pipeline Runs", "stages": "Stage Execution Log",
        "timing": "Processing Time Statistics"}
KEYS = {"runs": "Pipeline Run ID", "stages": "Stage Attempt ID",
        "timing": "Pipeline Run ID"}
MANUAL = {"Organization", "Flight Date", "Data Type", "UAV Model", "Dataset Name",
          "Survey Code", "Notes", "Recovery Action", "Error Code", "Retry Count",
          "Total Retries", "Active Processing Time"}
STATUS_LABELS = {
    "completed": "Completed", "failed": "Failed", "running": "Running",
    "paused": "Paused", "pending": "Pending", "skipped": "Skipped",
    "canceled": "Cancelled", "cancelled": "Cancelled", "aborted": "Aborted",
    "partially_completed": "Partially Completed", "requires_recovery": "Requires Recovery",
}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def load_state(path: Path, identity: dict) -> dict:
    if not path.exists():
        return {"version": 1, "identity": identity, "seen": {}, "tracked": [], "initialized": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("version") != 1 or data.get("identity") != identity
            or not isinstance(data.get("seen"), dict)
            or not isinstance(data.get("tracked"), list)
            or not isinstance(data.get("initialized"), bool)):
        raise ValueError("Sync state is invalid or belongs to another database/workbook")
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True, allow_nan=False)
        stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def single_worker(path: Path):
    """OS lock releases after a crash; never delete the lock file."""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            yield
        except (BlockingIOError, PermissionError) as exc:
            if not acquired:
                raise ValueError("Another sync worker holds this database lock") from exc
            raise
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def headers(grid: list[list]) -> dict[str, int]:
    if not grid:
        raise ValueError("Missing sheet header row")
    result = {}
    for i, name in enumerate(grid[0]):
        if name == "":
            continue
        name = str(name)
        if name in result:
            raise ValueError(f"Duplicate header: {name}")
        result[name] = i
    return result


def cell(row, index):
    return row[index] if index < len(row) else ""


def index_rows(grid: list[list], key: str) -> dict[str, int]:
    column = headers(grid).get(key)
    if column is None:
        raise ValueError(f"Missing header {key}; run --prepare once")
    result = {}
    for i, row in enumerate(grid[1:], 1):
        value = str(cell(row, column))
        if not value:
            continue
        if value.startswith("=") or value in result:
            raise ValueError(f"Formula or duplicate identifier in {key}: row {i + 1}")
        result[value] = i
    return result


def select_runs(state: dict, projections: dict, grids: dict) -> set[str]:
    matched = set(index_rows(grids["runs"], KEYS["runs"]))
    matched.update(index_rows(grids["timing"], KEYS["timing"]))
    tracked = set(state["tracked"])
    stage_groups = {}
    for key, row in projections["stages"].items():
        stage_groups.setdefault(row["Pipeline Run ID"], {})[key] = row
    for rid, row in projections["runs"].items():
        fingerprint = digest([row, projections["timing"][rid], stage_groups.get(rid, {})])
        old = state["seen"].get(rid)
        if (rid in matched or row["Overall Status"] in {"running", "paused"}
                or (state["initialized"] and old != fingerprint)):
            tracked.add(rid)
        state["seen"][rid] = fingerprint
    state["tracked"] = sorted(tracked)
    state["initialized"] = True
    return tracked


def display(header: str, value):
    if header in {"Status", "Overall Status"}:
        if value not in STATUS_LABELS:
            raise ValueError(f"Unsupported persisted status: {value}")
        return STATUS_LABELS[value]
    if isinstance(value, (int, float)) and header != "Attempt Number":
        # Readable duration strings preserve the workbook's h/m/s parsing convention.
        whole = round(value, 2)
        hours = int(whole // 3600)
        minutes = int((whole % 3600) // 60)
        secs = round(whole % 60, 2)
        return f"{hours}h {minutes}m {secs:g}s"
    return value


def plan_updates(projections: dict, grids: dict, selected: set[str]) -> tuple[list[dict], int]:
    """Return per-record sparse cell edits. Never blank manual or unknown cells."""
    records = []
    skipped_formulas = 0
    for tab, rows in projections.items():
        grid = grids[tab]
        columns = headers(grid)
        index = index_rows(grid, KEYS[tab])
        next_row = len(grid)  # Beyond ALL occupied cells, including notes/legacy rows.
        if tab == "stages":
            rid_col = columns.get("Pipeline Run ID")
            if rid_col is None:
                raise ValueError("Stage log is missing Pipeline Run ID")
            for row in grid[1:]:
                if str(cell(row, rid_col)) in selected and not cell(row, columns[KEYS[tab]]):
                    raise ValueError("Selected run has an unkeyed legacy stage row; reconcile it first")
        for key, values in rows.items():
            if values["Pipeline Run ID"] not in selected:
                continue
            missing = set(values).difference(columns)
            if missing:
                raise ValueError(f"{TABS[tab]} missing headers: {', '.join(sorted(missing))}")
            row_number = index.get(key)
            if row_number is None:
                row_number = next_row
                next_row += 1
            existing = grid[row_number] if row_number < len(grid) else []
            edits = []
            for header, raw in values.items():
                if header in MANUAL:
                    raise ValueError(f"Projection attempted to own manual field: {header}")
                col = columns[header]
                old = cell(existing, col)
                if isinstance(old, str) and old.startswith("="):
                    skipped_formulas += 1
                    continue
                value = display(header, raw)
                if old != value:
                    edits.append((col, value))
            if edits:
                records.append({"tab": tab, "row": row_number, "edits": edits})
    return records, skipped_formulas
