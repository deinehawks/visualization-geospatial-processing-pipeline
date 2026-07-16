import sqlite3

import pytest

from tools.query_pipeline_db import query_run


def create_database(path):
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            CREATE TABLE runs (run_id TEXT PRIMARY KEY, status TEXT);
            CREATE TABLE stages (id INTEGER PRIMARY KEY, run_id TEXT, stage_name TEXT);
            INSERT INTO runs VALUES ('run-1', 'completed');
            INSERT INTO stages VALUES (2, 'run-1', 'qgis');
            INSERT INTO stages VALUES (1, 'run-1', 'webodm');
        """)
        connection.commit()
    finally:
        connection.close()


def test_query_run_reads_existing_database_without_leaving_it_open(tmp_path):
    database = tmp_path / "pipeline.db"
    create_database(database)
    run, stages = query_run(database, "run-1")
    assert run == {"run_id": "run-1", "status": "completed"}
    assert [stage["stage_name"] for stage in stages] == ["webodm", "qgis"]
    database.unlink()
    assert not database.exists()


def test_query_run_never_creates_a_missing_database(tmp_path):
    database = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        query_run(database, "run-1")
    assert not database.exists()
