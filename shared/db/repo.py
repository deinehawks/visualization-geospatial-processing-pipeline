
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Dict

from .connection import connect
from .schema import SCHEMA_SQL


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebODMBindingConflictError(RuntimeError):
    pass


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
        workspace_root: Optional[str] = None,
    ) -> None:
        now = utc_now_iso()
        with connect(self.db_file) as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, status, started_at, source_dir, surveys_root, year,
                    workspace_root
                )
                VALUES (?, 'running', ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=CASE
                        WHEN runs.status='paused' THEN 'paused'
                        ELSE 'running'
                    END,
                    started_at=COALESCE(runs.started_at, excluded.started_at),
                    source_dir=COALESCE(excluded.source_dir, runs.source_dir),
                    surveys_root=COALESCE(excluded.surveys_root, runs.surveys_root),
                    year=COALESCE(excluded.year, runs.year),
                    workspace_root=COALESCE(
                        runs.workspace_root,
                        excluded.workspace_root
                    )
                """,
                (
                    run_id,
                    now,
                    source_dir,
                    surveys_root,
                    year,
                    workspace_root,
                ),
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

    def mark_run_finished(
        self,
        run_id: str,
        success: bool,
        total_runtime_seconds: float,
        *,
        status: Optional[str] = None,
    ) -> None:
        now = utc_now_iso()
        status = status or ("completed" if success else "failed")
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
                       source_dir, surveys_root, year, workspace_root
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

    def mark_survey_finished(
        self,
        survey_id: str,
        success: bool,
        total_runtime_seconds: float,
        *,
        status: Optional[str] = None,
    ) -> None:
        now = utc_now_iso()
        status = status or ("completed" if success else "failed")
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
        from .migrations.m001_add_run_pause_columns import (
            MIGRATION_ID as M001_ID,
            apply as apply_m001,
        )
        from .migrations.m002_webodm_task_bindings import (
            MIGRATION_ID as M002_ID,
            apply as apply_m002,
        )
        from .migrations.m003_run_workspace_root import (
            MIGRATION_ID as M003_ID,
            apply as apply_m003,
        )

        migrations = [
            (M001_ID, apply_m001),
            (M002_ID, apply_m002),
            (M003_ID, apply_m003),
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
        """Return the newest attempt for this run and stage.

        A newer forced, paused, failed, or running attempt is authoritative for
        resume. Older completed evidence remains available through
        ``get_latest_stage_output()`` for explicit legacy-output recovery.
        """
        with connect(self.db_file) as conn:
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

    # WEBODM BINDINGS
    def get_webodm_binding(
        self,
        run_id: str,
        operation_key: str,
    ) -> Optional[dict]:
        with connect(self.db_file) as conn:
            row = conn.execute(
                '''
                SELECT id, run_id, survey_id, project_id, task_id, task_name,
                       success, runtime_seconds, created_at, options_json,
                       operation_key, remote_status, raw_status, local_status,
                       updated_at, binding_source, stage_attempt_id, audit_json
                FROM webodm_tasks
                WHERE run_id=? AND operation_key=?
                ORDER BY id DESC
                LIMIT 1
                ''',
                (run_id, operation_key),
            ).fetchone()
            return dict(row) if row else None

    def list_webodm_bindings(self, run_id: str) -> list[dict]:
        with connect(self.db_file) as conn:
            rows = conn.execute(
                '''
                SELECT id, run_id, survey_id, project_id, task_id, task_name,
                       success, runtime_seconds, created_at, options_json,
                       operation_key, remote_status, raw_status, local_status,
                       updated_at, binding_source, stage_attempt_id, audit_json
                FROM webodm_tasks
                WHERE run_id=? AND operation_key IS NOT NULL
                ORDER BY id
                ''',
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def record_webodm_binding(
        self,
        *,
        run_id: str,
        operation_key: str,
        project_id: int,
        task_id: Optional[str] = None,
        task_name: Optional[str] = None,
        survey_id: Optional[str] = None,
        remote_status: Optional[str] = None,
        raw_status: Optional[Any] = None,
        local_status: str = 'bound',
        success: Optional[bool] = None,
        runtime_seconds: Optional[float] = None,
        binding_source: str = 'pipeline',
        stage_attempt_id: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
        audit: Optional[Dict[str, Any]] = None,
        allow_rebind: bool = False,
    ) -> dict:
        if not operation_key:
            raise ValueError('operation_key is required')
        if int(project_id) <= 0:
            raise ValueError('project_id must be positive')

        normalized_task_id = str(task_id) if task_id else None
        now = utc_now_iso()
        with connect(self.db_file) as conn:
            current_row = conn.execute(
                '''
                SELECT * FROM webodm_tasks
                WHERE run_id=? AND operation_key=?
                ORDER BY id DESC LIMIT 1
                ''',
                (run_id, operation_key),
            ).fetchone()
            current = dict(current_row) if current_row else None

            if current:
                current_project = int(current['project_id'])
                current_task = (
                    str(current['task_id']) if current.get('task_id') else None
                )
                current_name = (
                    str(current['task_name'])
                    if current.get('task_name')
                    else None
                )
                project_conflict = current_project != int(project_id)
                task_conflict = bool(
                    current_task
                    and normalized_task_id
                    and current_task != normalized_task_id
                )
                name_conflict = bool(
                    current_task
                    and current_name
                    and task_name
                    and current_name != str(task_name)
                )
                repair_identity_change = bool(
                    allow_rebind
                    and (
                        project_conflict
                        or (
                            normalized_task_id is not None
                            and current_task != normalized_task_id
                        )
                        or (
                            task_name is not None
                            and current_name != str(task_name)
                        )
                    )
                )
                if (
                    project_conflict
                    or task_conflict
                    or name_conflict
                ) and not allow_rebind:
                    raise WebODMBindingConflictError(
                        f'WebODM binding conflict for run={run_id} '
                        f'operation={operation_key}: existing project/task/name='
                        f'{current_project}/{current_task}/{current_name!r}, '
                        f'requested={project_id}/{normalized_task_id}/'
                        f'{task_name!r}'
                    )

                if (
                    not project_conflict
                    and not task_conflict
                    and not name_conflict
                    and not repair_identity_change
                ):
                    audit_json = (
                        current.get('audit_json')
                        if allow_rebind and current.get('audit_json')
                        else (
                            json.dumps(audit)
                            if audit is not None
                            else None
                        )
                    )
                    conn.execute(
                        '''
                        UPDATE webodm_tasks
                        SET survey_id=COALESCE(?, survey_id),
                            task_id=COALESCE(?, task_id),
                            task_name=COALESCE(?, task_name),
                            success=COALESCE(?, success),
                            runtime_seconds=COALESCE(?, runtime_seconds),
                            options_json=COALESCE(?, options_json),
                            remote_status=COALESCE(?, remote_status),
                            raw_status=COALESCE(?, raw_status),
                            local_status=?, updated_at=?, binding_source=?,
                            stage_attempt_id=COALESCE(?, stage_attempt_id),
                            audit_json=COALESCE(?, audit_json)
                        WHERE id=?
                        ''',
                        (
                            survey_id,
                            normalized_task_id,
                            task_name,
                            None if success is None else int(success),
                            runtime_seconds,
                            json.dumps(options) if options is not None else None,
                            remote_status,
                            None if raw_status is None else str(raw_status),
                            local_status,
                            now,
                            binding_source,
                            stage_attempt_id,
                            audit_json,
                            int(current['id']),
                        ),
                    )
                    conn.commit()
                    row = conn.execute(
                        'SELECT * FROM webodm_tasks WHERE id=?',
                        (int(current['id']),),
                    ).fetchone()
                    return dict(row)

            audit_payload = dict(audit or {})
            if current and allow_rebind:
                audit_payload.setdefault(
                    'previous_binding',
                    {
                        'id': current.get('id'),
                        'project_id': current.get('project_id'),
                        'task_id': current.get('task_id'),
                        'task_name': current.get('task_name'),
                    },
                )
            cur = conn.execute(
                '''
                INSERT INTO webodm_tasks (
                    run_id, survey_id, project_id, task_id, task_name, success,
                    runtime_seconds, created_at, options_json, operation_key,
                    remote_status, raw_status, local_status, updated_at,
                    binding_source, stage_attempt_id, audit_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    run_id,
                    survey_id,
                    int(project_id),
                    normalized_task_id,
                    task_name,
                    None if success is None else int(success),
                    runtime_seconds,
                    now,
                    json.dumps(options) if options is not None else None,
                    operation_key,
                    remote_status,
                    None if raw_status is None else str(raw_status),
                    local_status,
                    now,
                    binding_source,
                    stage_attempt_id,
                    json.dumps(audit_payload) if audit_payload else None,
                ),
            )
            conn.commit()
            row = conn.execute(
                'SELECT * FROM webodm_tasks WHERE id=?',
                (int(cur.lastrowid),),
            ).fetchone()
            return dict(row)
