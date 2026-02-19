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

    def _init_db(self) -> None:
        with connect(self.db_file) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    # ============================================================
    # RUNS
    # ============================================================

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
                    status='running',
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

    # ============================================================
    # SURVEYS (optional, for survey-level analytics)
    # ============================================================

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

    # ============================================================
    # STAGES (run_id-based)
    # ============================================================

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
        now = utc_now_iso()
        status = "completed" if success else "failed"
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
        with connect(self.db_file) as conn:
            row = conn.execute(
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

            return dict(row) if row else None

    def get_latest_stage_output(self, run_id: str, stage_name: str) -> Optional[Dict[str, Any]]:
        latest = self.get_latest_stage(run_id, stage_name)
        if not latest:
            return None

        output_json = latest.get("output_json")
        if not output_json:
            return None

        try:
            return json.loads(output_json)
        except Exception:
            return None
