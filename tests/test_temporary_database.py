import sqlite3
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DATABASE_PATH = REPOSITORY_ROOT / "data" / "pipeline.db"


def test_temporary_repository_uses_current_schema_under_tmp_path(
    tmp_path,
    temporary_sqlite_db_path,
):
    assert tmp_path in temporary_sqlite_db_path.parents
    with sqlite3.connect(temporary_sqlite_db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"runs", "stages", "surveys", "webodm_tasks", "schema_migrations"} <= tables


def test_separate_temporary_databases_do_not_share_records(
    temporary_pipeline_db_factory,
):
    first_path = temporary_pipeline_db_factory("first.db")
    second_path = temporary_pipeline_db_factory("second.db")
    with sqlite3.connect(first_path) as connection:
        connection.execute(
            "INSERT INTO runs (run_id, status) VALUES (?, ?)",
            ("run-only-in-first", "running"),
        )
        connection.commit()

    with sqlite3.connect(first_path) as connection:
        first_record = connection.execute(
            "SELECT status FROM runs WHERE run_id=?", ("run-only-in-first",)
        ).fetchone()
    with sqlite3.connect(second_path) as connection:
        second_record = connection.execute(
            "SELECT status FROM runs WHERE run_id=?", ("run-only-in-first",)
        ).fetchone()

    assert first_record == ("running",)
    assert second_record is None


def test_temporary_database_connection_is_releasable(
    temporary_pipeline_db_factory,
):
    database_path = temporary_pipeline_db_factory("releasable.db")
    connection = sqlite3.connect(database_path)
    connection.execute("SELECT 1").fetchone()
    connection.close()

    renamed_path = database_path.with_name("released.db")
    database_path.rename(renamed_path)
    assert renamed_path.is_file()


def test_temporary_connection_fixture_allows_sqlite_work(
    temporary_sqlite_connection,
):
    temporary_sqlite_connection.execute(
        "INSERT INTO runs (run_id, status) VALUES (?, ?)",
        ("temporary-run", "running"),
    )
    temporary_sqlite_connection.commit()
    row = temporary_sqlite_connection.execute(
        "SELECT status FROM runs WHERE run_id=?",
        ("temporary-run",),
    ).fetchone()
    assert row["status"] == "running"


def test_production_database_path_is_rejected_without_opening_or_creating_it():
    existed_before = PRODUCTION_DATABASE_PATH.exists()
    modified_before = (
        PRODUCTION_DATABASE_PATH.stat().st_mtime_ns if existed_before else None
    )

    with pytest.raises(RuntimeError, match="production database"):
        sqlite3.connect(PRODUCTION_DATABASE_PATH)

    assert PRODUCTION_DATABASE_PATH.exists() is existed_before
    if existed_before:
        assert PRODUCTION_DATABASE_PATH.stat().st_mtime_ns == modified_before
