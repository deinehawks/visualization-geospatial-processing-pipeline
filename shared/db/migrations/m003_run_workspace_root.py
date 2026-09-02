from __future__ import annotations


MIGRATION_ID = '003_run_workspace_root'


def apply(conn) -> None:
    rows = conn.execute('PRAGMA table_info(runs);').fetchall()
    columns = {row['name'] for row in rows}
    if 'workspace_root' not in columns:
        conn.execute('ALTER TABLE runs ADD COLUMN workspace_root TEXT;')
