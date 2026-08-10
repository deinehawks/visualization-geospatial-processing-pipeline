
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Dict

from .connection import connect
from .schema import SCHEMA_SQL


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PipelineRepo:
    """
    Thin repository layer for pipeline tracking (run_id-based).
    Pipelines call this; they never write SQL directly.

    Core idea:
    - "runs" is the parent entity that always exists from the start.
    - stages reference runs.run_id (FK-safe even before survey_id exists)
    - survey_id can be attached to a run later.
    """

    def __init__(self, db_file: Path):
        self.db_file = Path(db_file)
        self._init_db()

    @staticmethod
    def _ensure_column(conn, table: str, column: str, coltype: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
        existing_cols = [r["name"] for r in rows]
        if column not in existing_cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")

    def _init_db(self) -> None:
        with connect(self.db_file) as conn:
            conn.executescript(SCHEMA_SQL)

            # ---- migrations for older DBs ----
            self._ensure_column(conn, "runs", "paused_at", "TEXT")
            self._ensure_column(conn, "runs", "paused_after_stage", "TEXT")
            self._ensure_column(conn, "runs", "pause_reason", "TEXT")
            self._run_migrations(conn)
            conn.commit()

    # RUNS
    def create_run(
        self,
        run_id: str,
        *,
        source_dir: Optional[str] = None,
        surveys_root: Optional[str] = None,
        year: Optional[int] = None,
    ) -> None:
        now = utc_now_iso()
        with connect(self.db_file) as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, status, started_at, source_dir, surveys_root, year)
                VALUES (?, 'running', ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=CASE
                        WHEN runs.status='paused' THEN 'paused'
                        ELSE 'running'
                    END,
                    started_at=COALESCE(runs.started_at, excluded.started_at),
                    source_dir=COALESCE(excluded.source_dir, runs.source_dir),
                    surveys_root=COALESCE(excluded.surveys_root, runs.surveys_root),
                    year=COALESCE(excluded.year, runs.year)
                """,
                (run_id, now, source_dir, surveys_root, year),
            )
            conn.commit()

    def attach_survey_id(self, run_id: str, survey_id: str) -> None:
        with connect(self.db_file) as conn:
            conn.execute(
                """
                UPDATE runs
                SET survey_id=?
                WHERE run_id=?
                """,
                (survey_id, run_id),
            )
            conn.commit()

    def mark_run_finished(self, run_id: str, success: bool, total_runtime_seconds: float) -> None:
        now = utc_now_iso()
        status = "completed" if success else "failed"
        with connect(self.db_file) as conn:
            conn.execute(
                """
                UPDATE runs
                SET status=?, finished_at=?, total_runtime_seconds=?
                WHERE run_id=?
                """,
                (status, now, total_runtime_seconds, run_id),
            )
            conn.commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        with connect(self.db_file) as conn:
            row = conn.execute(
                """
                SELECT run_id, survey_id, status, started_at, finished_at, total_runtime_seconds,
                       source_dir, surveys_root, year
                FROM runs
                WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_run_paused(
        self,
        run_id: str,
        *,
        paused_after_stage: str = "",
        reason: str = "pause_flag",
    ) -> None:
        now = utc_now_iso()
        with connect(self.db_file) as conn:
            conn.execute(
                """
                UPDATE runs
                SET status='paused',
                    paused_at=?,
                    paused_after_stage=?,
                    pause_reason=?
                WHERE run_id=?
                """,
                (now, paused_after_stage, reason, run_id),
            )
            conn.commit()

    def mark_run_running(self, run_id: str) -> None:
        with connect(self.db_file) as conn:
            conn.execute(
                """
                UPDATE runs
                SET status='running',
                    paused_at=NULL,
                    paused_after_stage=NULL,
                    pause_reason=NULL
                WHERE run_id=?
                """,
                (run_id,),
            )
            conn.commit()

    # SURVEYS (optional, for survey-level analytics)
    def upsert_survey_running(self, survey_id: str) -> None:
        now = utc_now_iso()
        with connect(self.db_file) as conn:
            conn.execute(
                """
                INSERT INTO surveys (survey_id, status, started_at)
                VALUES (?, 'running', ?)
                ON CONFLICT(survey_id) DO UPDATE SET
                    status='running',
                    started_at=COALESCE(surveys.started_at, excluded.started_at)
                """,
                (survey_id, now),
            )
            conn.commit()

    def mark_survey_finished(self, survey_id: str, success: bool, total_runtime_seconds: float) -> None:
        now = utc_now_iso()
        status = "completed" if success else "failed"
        with connect(self.db_file) as conn:
            conn.execute(
                """
                UPDATE surveys
                SET status=?, finished_at=?, total_runtime_seconds=?
                WHERE survey_id=?
                """,
                (status, now, total_runtime_seconds, survey_id),
            )
            conn.commit()

    # Migration Runner
    def _has_migration(self, conn, migration_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE id=? LIMIT 1",
            (migration_id,),
        ).fetchone()
        return row is not None

    def _mark_migration(self, conn, migration_id: str) -> None:
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
            (migration_id, utc_now_iso()),
        )

    def _run_migrations(self, conn) -> None:
        from .migrations.m001_add_run_pause_columns import MIGRATION_ID, apply

        migrations = [
            (MIGRATION_ID, apply),
        ]

        for mid, fn in migrations:
            if self._has_migration(conn, mid):
                continue
            fn(conn)
            self._mark_migration(conn, mid)

    # STAGES (run_id-based)
    def start_stage(self, run_id: str, stage_name: str) -> int:
        now = utc_now_iso()
        with connect(self.db_file) as conn:
            cur = conn.execute(
                """
                INSERT INTO stages (run_id, stage_name, status, started_at)
                VALUES (?, ?, 'running', ?)
                """,
                (run_id, stage_name, now),
            )
            conn.commit()
            return int(cur.lastrowid)

    def finish_stage(
        self,
        stage_id: int,
        success: bool,
        runtime_seconds: float,
        output: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        status = "completed" if success else "failed"
        self.finish_stage_with_status(
            stage_id=stage_id,
            status=status,
            runtime_seconds=runtime_seconds,
            output=output,
            error_message=error_message,
        )

    def finish_stage_with_status(
        self,
        stage_id: int,
        status: str,
        runtime_seconds: float,
        output: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if not status:
            raise ValueError("stage status is required")
        now = utc_now_iso()
        output_json = json.dumps(output) if output is not None else None
        with connect(self.db_file) as conn:
            conn.execute(
                """
                UPDATE stages
                SET status=?, finished_at=?, runtime_seconds=?, error_message=?, output_json=?
                WHERE id=?
                """,
                (status, now, runtime_seconds, error_message, output_json, stage_id),
            )
            conn.commit()

    # -------- Helper Methods (resume) -------- #

    def get_latest_stage(self, run_id: str, stage_name: str) -> Optional[dict]:
        """
        Return the most recent stage record for this run+stage combination,
        preferring a completed record over any failed/running ones.

        Resume logic in StageRunner checks for status == "completed" to decide
        whether to skip a stage. Without this preference, a later failed retry
        (e.g. IDs 316-319 in the stages table) would shadow the original
        completed record (e.g. ID 312), causing the stage to re-run on every
        resume attempt even though it already succeeded.

        Strategy:
          1. If any completed record exists for this run+stage → return it.
          2. Otherwise return the most recent record by id (for stale-running
             detection and error reporting).
        """
        with connect(self.db_file) as conn:
            # First: look for the most recent completed record
            completed_row = conn.execute(
                """
                SELECT id, run_id, stage_name, status, started_at, finished_at,
                       runtime_seconds, error_message, output_json
                FROM stages
                WHERE run_id = ? AND stage_name = ? AND status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, stage_name),
            ).fetchone()

            if completed_row:
                return dict(completed_row)

            # Fallback: return the latest record regardless of status
            # (used for stale-running detection)
            latest_row = conn.execute(
                """
                SELECT id, run_id, stage_name, status, started_at, finished_at,
                       runtime_seconds, error_message, output_json
                FROM stages
                WHERE run_id = ? AND stage_name = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, stage_name),
            ).fetchone()

            return dict(latest_row) if latest_row else None

    def get_latest_stage_output(self, run_id: str, stage_name: str) -> Optional[Dict[str, Any]]:
        """
        Return the output_json of the latest completed stage record.
        Always reads from a completed record so resume state is consistent.
        """
        with connect(self.db_file) as conn:
            row = conn.execute(
                """
                SELECT output_json
                FROM stages
                WHERE run_id = ? AND stage_name = ? AND status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id, stage_name),
            ).fetchone()

            if not row:
                return None

            output_json = row["output_json"]
            if not output_json:
                return None

            try:
                return json.loads(output_json)
            except Exception:
                return None