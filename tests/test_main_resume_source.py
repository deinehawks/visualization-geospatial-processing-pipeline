import logging
from pathlib import Path

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
        field_data_root=tmp_path / "field-data",
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
        field_data_root=tmp_path / "field-data",
        logger=logging.getLogger("tests.main.fresh"),
        date_hint="20260730",
    )

    assert resolved == resolved_source
    assert calls == [
        {
            "source_input": tmp_path / "field-data" / "ABC_001_same_dataset",
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
            field_data_root=tmp_path / "field-data",
            logger=logging.getLogger("tests.main.missing"),
            repository=repository,
        )