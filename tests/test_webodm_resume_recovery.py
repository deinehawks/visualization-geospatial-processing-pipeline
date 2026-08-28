from __future__ import annotations

import json
import logging
import sqlite3

import pytest
import requests

from modules.webodm.webodm_processor import (
    WebODMProcessor,
    WebODMTaskLookupError,
    WebODMTaskNotFound,
)
from shared.db.repo import PipelineRepo, WebODMBindingConflictError
from shared.stage_runner import StageRunner
from tools.repair_webodm_binding import (
    apply_repair_plan,
    build_repair_plan,
    read_repair_context,
)


@pytest.mark.parametrize(
    ('raw_status', 'expected', 'terminal'),
    [
        (10, 'queued', False),
        (20, 'running', False),
        (30, 'failed', True),
        (40, 'completed', True),
        (50, 'canceled', True),
        ('cancelled', 'canceled', True),
    ],
)
def test_webodm_status_codes_match_api(raw_status, expected, terminal):
    assert WebODMProcessor._normalize_status(raw_status) == (
        expected,
        terminal,
    )


class _LookupResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'controlled status {self.status_code}')

    def json(self):
        return dict(self._payload)


class _LookupSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


def _lookup_processor(responses):
    processor = object.__new__(WebODMProcessor)
    processor.base_url = 'http://webodm.invalid'
    processor.headers = {}
    processor.logger = logging.getLogger('tests.webodm-lookup')
    processor.session = _LookupSession(responses)
    processor._reset_session = lambda **kwargs: None
    return processor


def test_exact_task_lookup_distinguishes_confirmed_404():
    processor = _lookup_processor([_LookupResponse(404)])

    with pytest.raises(WebODMTaskNotFound):
        processor.get_task(417, 'missing-task', retries=1, backoff=0)


@pytest.mark.parametrize(
    'response',
    [
        _LookupResponse(500),
        requests.ConnectionError('controlled connection failure'),
        _LookupResponse(401),
    ],
)
def test_exact_task_lookup_failures_are_not_reported_as_absence(response):
    processor = _lookup_processor([response])

    with pytest.raises(WebODMTaskLookupError):
        processor.get_task(417, 'bound-task', retries=1, backoff=0)


def test_webodm_binding_is_updated_without_losing_uuid(
    temporary_sqlite_db_path,
):
    repo = PipelineRepo(temporary_sqlite_db_path)
    repo.create_run('binding-run')

    project_only = repo.record_webodm_binding(
        run_id='binding-run',
        operation_key='task4',
        project_id=417,
        task_name='AH-026066-RGB--xcb-t4',
        local_status='project_bound',
    )
    bound = repo.record_webodm_binding(
        run_id='binding-run',
        operation_key='task4',
        project_id=417,
        task_id='59aed1d2-19f1-499e-874d-bcdd40072d09',
        task_name='AH-026066-RGB--xcb-t4',
        remote_status='queued',
        raw_status=10,
        local_status='task_created',
    )

    assert bound['id'] == project_only['id']
    assert bound['task_id'] == '59aed1d2-19f1-499e-874d-bcdd40072d09'
    assert repo.get_webodm_binding('binding-run', 'task4')['task_id'] == (
        '59aed1d2-19f1-499e-874d-bcdd40072d09'
    )


def test_webodm_binding_conflict_fails_closed_and_repair_preserves_history(
    temporary_sqlite_db_path,
):
    repo = PipelineRepo(temporary_sqlite_db_path)
    repo.create_run('repair-history-run')
    first = repo.record_webodm_binding(
        run_id='repair-history-run',
        operation_key='task4',
        project_id=418,
        task_id='duplicate-task',
        task_name='AH-026066-RGB--xcb-t4',
    )

    with pytest.raises(WebODMBindingConflictError):
        repo.record_webodm_binding(
            run_id='repair-history-run',
            operation_key='task4',
            project_id=417,
            task_id='correct-task',
            task_name='AH-026066-RGB--xcb-t4',
        )

    repaired = repo.record_webodm_binding(
        run_id='repair-history-run',
        operation_key='task4',
        project_id=417,
        task_id='correct-task',
        task_name='AH-026066-RGB--xcb-t4',
        binding_source='operator_repair',
        audit={'reason': 'validated exact task'},
        allow_rebind=True,
    )

    history = repo.list_webodm_bindings('repair-history-run')
    assert repaired['id'] != first['id']
    assert [(row['project_id'], row['task_id']) for row in history] == [
        (418, 'duplicate-task'),
        (417, 'correct-task'),
    ]
    assert json.loads(repaired['audit_json'])['previous_binding']['id'] == (
        first['id']
    )


def test_bound_task_name_conflict_fails_closed(temporary_sqlite_db_path):
    repo = PipelineRepo(temporary_sqlite_db_path)
    repo.create_run('name-conflict-run')
    repo.record_webodm_binding(
        run_id='name-conflict-run',
        operation_key='task4',
        project_id=415,
        task_id='task-original',
        task_name='AH-TEST-RGB--xcb-t4',
    )

    with pytest.raises(WebODMBindingConflictError, match='binding conflict'):
        repo.record_webodm_binding(
            run_id='name-conflict-run',
            operation_key='task4',
            project_id=415,
            task_id='task-original',
            task_name='AH-OTHER-RGB--xcb-t4',
        )


def test_latest_stage_attempt_is_not_masked_by_older_completion(
    temporary_sqlite_db_path,
):
    repo = PipelineRepo(temporary_sqlite_db_path)
    repo.create_run('latest-attempt-run')
    completed_id = repo.start_stage('latest-attempt-run', 'webodm')
    repo.finish_stage(completed_id, True, 1.0, output={'project_id': 417})
    failed_id = repo.start_stage('latest-attempt-run', 'webodm')
    repo.finish_stage(failed_id, False, 2.0, error_message='forced retry failed')

    latest = repo.get_latest_stage('latest-attempt-run', 'webodm')
    assert latest['id'] == failed_id
    assert latest['status'] == 'failed'


def test_pause_finalizes_active_stage_as_paused(
    temporary_sqlite_db_path,
):
    repo = PipelineRepo(temporary_sqlite_db_path)
    runner = StageRunner(
        repo,
        'paused-stage-run',
        logging.getLogger('tests.paused-stage'),
    )

    with pytest.raises(RuntimeError, match='__PIPELINE_PAUSED__'):
        runner.run(
            'webodm_task4',
            lambda: (_ for _ in ()).throw(
                RuntimeError('__PIPELINE_PAUSED__')
            ),
            retry_attempts=1,
            retry_delay_seconds=0,
        )

    latest = repo.get_latest_stage('paused-stage-run', 'webodm_task4')
    assert latest['status'] == 'paused'
    assert latest['error_message'] == 'Paused by pipeline control'


def test_repair_tool_dry_run_reads_without_migrating_and_apply_is_audited(
    temporary_sqlite_db_path,
    temporary_checkpoint_dir,
):
    repo = PipelineRepo(temporary_sqlite_db_path)
    repo.create_run('repair-tool-run')
    repo.attach_survey_id('repair-tool-run', 'AH-026066')
    repo.record_webodm_binding(
        run_id='repair-tool-run',
        operation_key='task4',
        project_id=418,
        task_id='duplicate-task',
        task_name='AH-026066-RGB--xcb-t4',
    )

    context = read_repair_context(
        temporary_sqlite_db_path,
        'repair-tool-run',
        'task4',
    )
    plan = build_repair_plan(
        run_id='repair-tool-run',
        operation='task4',
        project_id=417,
        task_id='59aed1d2-19f1-499e-874d-bcdd40072d09',
        context=context,
        checkpoint={
            'project_id': 416,
            'task4': {
                'id': 'checkpoint-task',
                'name': 'AH-026066-RGB--xcb-t4',
            },
        },
        remote_task={
            'id': '59aed1d2-19f1-499e-874d-bcdd40072d09',
            'name': 'AH-026066-RGB--xcb-t4',
            'status': 40,
        },
        normalize_status=WebODMProcessor._normalize_status,
    )

    assert plan['dry_run'] is True
    assert plan['conflicts'] == [
        'project_id 418 -> 417',
        'task_id duplicate-task -> 59aed1d2-19f1-499e-874d-bcdd40072d09',
        'checkpoint.project_id 416 -> 417',
        (
            'checkpoint.task_id checkpoint-task -> '
            '59aed1d2-19f1-499e-874d-bcdd40072d09'
        ),
    ]
    assert repo.get_webodm_binding(
        'repair-tool-run',
        'task4',
    )['project_id'] == 418

    checkpoint_path = (
        temporary_checkpoint_dir
        / 'webodm_checkpoint_repair-tool-run.json'
    )
    applied = apply_repair_plan(
        database=temporary_sqlite_db_path,
        checkpoint_path=checkpoint_path,
        plan=plan,
    )

    assert applied['dry_run'] is False
    binding = repo.get_webodm_binding('repair-tool-run', 'task4')
    assert binding['project_id'] == 417
    assert binding['task_id'] == '59aed1d2-19f1-499e-874d-bcdd40072d09'
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    assert checkpoint['project_id'] == 417
    assert checkpoint['task4']['id'] == (
        '59aed1d2-19f1-499e-874d-bcdd40072d09'
    )

    repeated = apply_repair_plan(
        database=temporary_sqlite_db_path,
        checkpoint_path=checkpoint_path,
        plan=plan,
    )
    assert repeated['applied_binding_id'] == applied['applied_binding_id']
    assert len(repo.list_webodm_bindings('repair-tool-run')) == 2


def test_binding_migration_upgrades_legacy_table_repeatably(tmp_path):
    database = tmp_path / 'legacy.db'
    with sqlite3.connect(database) as connection:
        connection.executescript(
            '''
            CREATE TABLE runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL);
            CREATE TABLE stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stage_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                runtime_seconds REAL,
                error_message TEXT,
                output_json TEXT
            );
            CREATE TABLE surveys (
                survey_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE webodm_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                survey_id TEXT,
                project_id INTEGER,
                task_id INTEGER,
                task_name TEXT,
                success INTEGER,
                runtime_seconds REAL,
                created_at TEXT,
                options_json TEXT
            );
            CREATE TABLE schema_migrations (
                id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            '''
        )

    PipelineRepo(database)
    PipelineRepo(database)

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info(webodm_tasks)'
            ).fetchall()
        }
        migrations = {
            row[0]
            for row in connection.execute(
                'SELECT id FROM schema_migrations'
            ).fetchall()
        }

    assert {
        'operation_key',
        'remote_status',
        'local_status',
        'binding_source',
        'audit_json',
    } <= columns
    assert '002_webodm_task_bindings' in migrations
