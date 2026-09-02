from pathlib import Path
import sqlite3

from shared.db.migrations.m003_run_workspace_root import apply
from shared.db.repo import PipelineRepo


def test_repository_persists_workspace_root_and_does_not_rebind_it(tmp_path: Path):
    repository = PipelineRepo(tmp_path / "pipeline.db")
    original = tmp_path / "workspaces-a"
    replacement = tmp_path / "workspaces-b"

    repository.create_run("run-1", workspace_root=str(original))
    repository.create_run("run-1", workspace_root=str(replacement))

    assert repository.get_run("run-1")["workspace_root"] == str(original)


def test_workspace_root_migration_is_repeatable_for_legacy_runs(tmp_path: Path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO runs (run_id) VALUES ('legacy-run')")

        apply(connection)
        apply(connection)

        row = connection.execute(
            "SELECT run_id, workspace_root FROM runs WHERE run_id='legacy-run'"
        ).fetchone()
        assert dict(row) == {"run_id": "legacy-run", "workspace_root": None}
    finally:
        connection.close()
