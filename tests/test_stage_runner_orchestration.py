import logging
import sqlite3

import pytest

from shared.db.repo import PipelineRepo
from shared.stage_runner import StageRunner
from tests.fakes import FakeWebODM


RUN_ID = "hermetic-orchestration-run"
STAGE_NAME = "webodm_orchestration_probe"


def read_stage(database_path):
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT status, error_message, output_json
            FROM stages
            WHERE run_id=? AND stage_name=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (RUN_ID, STAGE_NAME),
        ).fetchone()
    return dict(row)


def build_runner(database_path):
    repo = PipelineRepo(database_path)
    logger = logging.getLogger("tests.stage_runner_orchestration")
    return repo, StageRunner(repo, RUN_ID, logger)


def test_stage_runner_records_successful_fake_webodm_orchestration(
    temporary_path_layout,
    sample_dataset_dir,
    placeholder_image_files,
):
    fake = FakeWebODM()
    repo, runner = build_runner(temporary_path_layout.database_path)
    state = {}

    def create_fake_task():
        fake.authenticate()
        assert fake.preflight()
        project_id = fake.create_project("hermetic-project")
        task_id = fake.create_task_with_images(
            project_id,
            "hermetic-task",
            str(sample_dataset_dir),
        )
        return {
            "project_id": project_id,
            "task_id": task_id,
            "image_folder": str(sample_dataset_dir),
        }

    result = runner.run(
        STAGE_NAME,
        create_fake_task,
        output_key="webodm_probe",
        state=state,
        retry_attempts=1,
        retry_delay_seconds=0,
    )

    assert result == {
        "project_id": 100,
        "task_id": "task-0001",
        "image_folder": str(sample_dataset_dir),
    }
    assert state["webodm_probe"] == result
    assert [call.method for call in fake.calls] == [
        "authenticate",
        "preflight",
        "create_project",
        "create_task_with_images",
    ]
    assert repo.get_run(RUN_ID)["status"] == "running"
    stage = read_stage(temporary_path_layout.database_path)
    assert stage["status"] == "completed"
    assert stage["error_message"] is None
    assert repo.get_latest_stage_output(RUN_ID, STAGE_NAME) == result
    assert temporary_path_layout.application_root in sample_dataset_dir.parents
    assert all(
        temporary_path_layout.application_root in path.parents
        for path in placeholder_image_files
    )


def test_stage_runner_records_failed_fake_webodm_orchestration(
    temporary_path_layout,
    sample_dataset_dir,
):
    fake = FakeWebODM()
    repo, runner = build_runner(temporary_path_layout.database_path)

    def create_fake_task():
        fake.authenticate()
        assert fake.preflight()
        raise ValueError("controlled orchestration failure")

    with pytest.raises(ValueError, match="controlled orchestration failure"):
        runner.run(
            STAGE_NAME,
            create_fake_task,
            retry_attempts=1,
            retry_delay_seconds=0,
        )

    assert [call.method for call in fake.calls] == [
        "authenticate",
        "preflight",
    ]
    assert fake.calls_for("create_project") == []
    assert fake.calls_for("create_task_with_images") == []
    assert fake.calls_for("wait_for_completion") == []
    assert repo.get_run(RUN_ID)["status"] == "running"
    stage = read_stage(temporary_path_layout.database_path)
    assert stage["status"] == "failed"
    assert stage["error_message"] == "controlled orchestration failure"
    assert stage["output_json"] is None
    assert temporary_path_layout.application_root in sample_dataset_dir.parents
