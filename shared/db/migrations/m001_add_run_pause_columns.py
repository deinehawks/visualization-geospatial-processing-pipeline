from __future__ import annotations

from shared.db.repo import utc_now_iso

MIGRATION_ID = "001_add_run_pause_columns"


def _ensure_column(conn, table: str, column: str, coltype: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    cols = {r["name"] for r in rows}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")


def apply(conn) -> None:
    _ensure_column(conn, "runs", "paused_at", "TEXT")
    _ensure_column(conn, "runs", "paused_after_stage", "TEXT")
    _ensure_column(conn, "runs", "pause_reason", "TEXT")