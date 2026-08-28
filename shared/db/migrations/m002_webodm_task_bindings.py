from __future__ import annotations


MIGRATION_ID = '002_webodm_task_bindings'


def _ensure_column(conn, table: str, column: str, coltype: str) -> None:
    rows = conn.execute(f'PRAGMA table_info({table});').fetchall()
    columns = {row['name'] for row in rows}
    if column not in columns:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {coltype};')


def apply(conn) -> None:
    # Keep legacy rows readable; SQLite accepts UUID text in INTEGER-affinity
    # columns, while new databases declare task_id as TEXT.
    for column, coltype in (
        ('operation_key', 'TEXT'),
        ('remote_status', 'TEXT'),
        ('raw_status', 'TEXT'),
        ('local_status', 'TEXT'),
        ('updated_at', 'TEXT'),
        ('binding_source', 'TEXT'),
        ('stage_attempt_id', 'INTEGER'),
        ('audit_json', 'TEXT'),
    ):
        _ensure_column(conn, 'webodm_tasks', column, coltype)

    conn.execute(
        '''
        CREATE INDEX IF NOT EXISTS idx_webodm_tasks_run_operation
        ON webodm_tasks(run_id, operation_key, id)
        '''
    )
