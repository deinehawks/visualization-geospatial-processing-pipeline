import logging
from pathlib import Path
import sqlite3

import pytest

import main as rgb_main
from shared.db.repo import PipelineRepo


def test_resume_source_dir_reuses_stored_run_source_without_prompt(monkeypatch, tmp_path):
    repository = PipelineRepo(tmp_path / "pipeline.db")
    stored_source = tmp_path / "field-data" / "20260730" / "CLIENT" / "ABC_001_same_dataset"
    repository.create_run(
        "run-resume",
        source_dir=str(stored_source),
        surveys_root=str(tmp_path / "surveys"),
        year=2026,
    )

    def fail_if_resolver_is_called(*_args, **_kwargs):
        pytest.fail("resume should not re-run ambiguous dataset resolution")

    monkeypatch.setattr(rgb_main, "resolve_source_dataset_dir", fail_if_resolver_is_called)

    resolved = rgb_main.resolve_cli_source_dir(
        survey="ABC_001_same_dataset",
        resume=True,
        run_id="run-resume",
        logger=logging.getLogger("tests.main.resume"),
        repository=repository,
    )

    assert resolved == stored_source


def test_fresh_source_dir_still_uses_dataset_resolver(monkeypatch, tmp_path):
    calls = []
    resolved_source = tmp_path / "resolved" / "ABC_001_same_dataset"

    def fake_resolver(source_input, logger, date_hint=None):
        calls.append(
            {
                "source_input": source_input,
                "logger": logger,
                "date_hint": date_hint,
            }
        )
        return resolved_source

    monkeypatch.setattr(rgb_main, "resolve_source_dataset_dir", fake_resolver)

    resolved = rgb_main.resolve_cli_source_dir(
        survey="ABC_001_same_dataset",
        resume=False,
        run_id=None,
        logger=logging.getLogger("tests.main.fresh"),
        date_hint="20260730",
    )

    assert resolved == resolved_source
    assert calls == [
        {
            "source_input": Path("ABC_001_same_dataset"),
            "logger": logging.getLogger("tests.main.fresh"),
            "date_hint": "20260730",
        }
    ]


def test_resume_source_dir_requires_existing_run_record(tmp_path):
    repository = PipelineRepo(tmp_path / "pipeline.db")

    with pytest.raises(ValueError, match="no run record"):
        rgb_main.resolve_cli_source_dir(
            survey="ABC_001_same_dataset",
            resume=True,
            run_id="missing-run",
            logger=logging.getLogger("tests.main.missing"),
            repository=repository,
        )


def test_fresh_run_uses_configured_workspace_root(tmp_path):
    configured = tmp_path / "configured-workspaces"

    result = rgb_main.resolve_cli_workspace_root(
        base_dir=tmp_path,
        config={"paths": {"workspace_root": configured}},
        resume=False,
        run_id="fresh-run",
        run_record=None,
    )

    assert result == configured


def test_resume_uses_persisted_workspace_root_even_if_config_changed(tmp_path):
    persisted = tmp_path / "original-workspaces"

    result = rgb_main.resolve_cli_workspace_root(
        base_dir=tmp_path,
        config={"paths": {"workspace_root": tmp_path / "new-workspaces"}},
        resume=True,
        run_id="existing-run",
        run_record={"workspace_root": str(persisted)},
    )

    assert result == persisted


def test_legacy_resume_without_persisted_root_stays_on_legacy_path(tmp_path):
    result = rgb_main.resolve_cli_workspace_root(
        base_dir=tmp_path,
        config={"paths": {"workspace_root": tmp_path / "new-workspaces"}},
        resume=True,
        run_id="legacy-run",
        run_record={"workspace_root": None},
    )

    assert result == tmp_path / "data" / "workspaces"


def test_resume_state_reader_supports_legacy_schema_without_migrating(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                survey_id TEXT,
                status TEXT,
                source_dir TEXT,
                surveys_root TEXT,
                year INTEGER
            )
            """
        )
        connection.execute(
            """
            INSERT INTO runs (
                run_id, survey_id, status, source_dir, surveys_root, year
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-run",
                "AH-026001",
                "paused",
                str(tmp_path / "source"),
                str(tmp_path / "surveys"),
                2026,
            ),
        )

    record = rgb_main.read_run_state_read_only(database, "legacy-run")

    assert record["workspace_root"] is None
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runs)")
        }
    assert "workspace_root" not in columns
