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
    Thin repository layer for pipeline tracking.
    Pipelines call this; they never write SQL directly.
    """

    def __init__(self, db_file: Path):
        self.db_file = db_file
        self._init_db()

    def _init_db(self) -> None:
        with connect(self.db_file) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    # -------- Survey -------- #

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

    # -------- Stage -------- #

    def start_stage(self, survey_id: str, stage_name: str) -> int:
        now = utc_now_iso()
        with connect(self.db_file) as conn:
            cur = conn.execute(
                """
                INSERT INTO stages (survey_id, stage_name, status, started_at)
                VALUES (?, ?, 'running', ?)
                """,
                (survey_id, stage_name, now),
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
