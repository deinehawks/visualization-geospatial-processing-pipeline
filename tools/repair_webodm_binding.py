from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.webodm.webodm_processor import WebODMProcessor
from shared.config import load_pipeline_config
from shared.db.repo import PipelineRepo


def _read_only_connection(database: Path) -> sqlite3.Connection:
    resolved = Path(database).resolve(strict=True)
    uri = f'file:{resolved.as_posix()}?mode=ro'
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def read_repair_context(database: Path, run_id: str, operation: str) -> dict:
    with _read_only_connection(database) as connection:
        run_row = connection.execute(
            'SELECT * FROM runs WHERE run_id=?',
            (run_id,),
        ).fetchone()
        if run_row is None:
            raise ValueError(f'Run not found: {run_id}')

        columns = {
            row['name']
            for row in connection.execute(
                'PRAGMA table_info(webodm_tasks)'
            ).fetchall()
        }
        binding = None
        if 'operation_key' in columns:
            binding_row = connection.execute(
                '''
                SELECT * FROM webodm_tasks
                WHERE run_id=? AND operation_key=?
                ORDER BY id DESC LIMIT 1
                ''',
                (run_id, operation),
            ).fetchone()
            binding = dict(binding_row) if binding_row else None

        stage_row = connection.execute(
            '''
            SELECT output_json FROM stages
            WHERE run_id=? AND stage_name='webodm'
                  AND output_json IS NOT NULL
            ORDER BY id DESC LIMIT 1
            ''',
            (run_id,),
        ).fetchone()

    stage_output = None
    if stage_row and stage_row['output_json']:
        try:
            stage_output = json.loads(stage_row['output_json'])
        except (TypeError, ValueError):
            stage_output = None
    return {
        'run': dict(run_row),
        'binding': binding,
        'stage_output': stage_output,
    }


def load_checkpoint(checkpoint_path: Path) -> dict:
    if not checkpoint_path.is_file():
        return {}
    try:
        value = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _task_from_state(state: Any, operation: str) -> Mapping[str, Any]:
    state = _webodm_state(state)
    task = state.get(operation)
    return task if isinstance(task, Mapping) else {}


def _webodm_state(state: Any) -> Mapping[str, Any]:
    if not isinstance(state, Mapping):
        return {}
    nested = state.get('webodm')
    if isinstance(nested, Mapping):
        state = nested
    return state


def build_repair_plan(
    *,
    run_id: str,
    operation: str,
    project_id: int,
    task_id: str,
    context: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    remote_task: Mapping[str, Any],
    normalize_status,
) -> dict:
    run = context['run']
    survey_id = run.get('survey_id')
    remote_id = str(remote_task.get('id') or '')
    remote_name = str(remote_task.get('name') or '')
    if remote_id != str(task_id):
        raise ValueError(
            f'Remote task ID mismatch: expected {task_id}, got {remote_id}'
        )

    expected_names = {
        str(value)
        for value in (
            (context.get('binding') or {}).get('task_name'),
            _task_from_state(context.get('stage_output'), operation).get('name'),
            _task_from_state(checkpoint, operation).get('name'),
        )
        if value
    }
    if len(expected_names) > 1:
        raise ValueError(
            'Persisted task names conflict: ' + ', '.join(sorted(expected_names))
        )
    if expected_names and remote_name not in expected_names:
        raise ValueError(
            f'Remote task name {remote_name!r} does not match '
            f'expected name {next(iter(expected_names))!r}'
        )
    if not expected_names and survey_id:
        if not remote_name.startswith(str(survey_id)) or not remote_name.endswith(
            '-t4'
        ):
            raise ValueError(
                f'Remote task name {remote_name!r} is not a Task 4 name for '
                f'survey {survey_id}'
            )

    raw_status = remote_task.get('status')
    remote_status, _terminal = normalize_status(raw_status)
    current = context.get('binding')
    conflicts = []
    if current:
        current_project_id = current.get('project_id')
        current_task_id = current.get('task_id')
        if int(current['project_id']) != int(project_id):
            conflicts.append(
                f'project_id {current_project_id} -> {project_id}'
            )
        if current.get('task_id') and str(current['task_id']) != str(task_id):
            conflicts.append(f'task_id {current_task_id} -> {task_id}')
    for source_name, source_value in (
        ('stage_output', context.get('stage_output')),
        ('checkpoint', checkpoint),
    ):
        source_state = _webodm_state(source_value)
        source_project = source_state.get('project_id')
        source_task = _task_from_state(source_state, operation)
        source_task_id = source_task.get('id')
        if source_project and int(source_project) != int(project_id):
            conflicts.append(
                f'{source_name}.project_id {source_project} -> {project_id}'
            )
        if source_task_id and str(source_task_id) != str(task_id):
            conflicts.append(
                f'{source_name}.task_id {source_task_id} -> {task_id}'
            )

    return {
        'dry_run': True,
        'run_id': run_id,
        'survey_id': survey_id,
        'operation': operation,
        'validated_binding': {
            'project_id': int(project_id),
            'task_id': str(task_id),
            'task_name': remote_name,
            'remote_status': remote_status,
            'raw_status': raw_status,
        },
        'current_binding': current,
        'conflicts': conflicts,
        'changes_required': bool(
            current is None
            or conflicts
            or str(current.get('task_id') or '') != str(task_id)
            or current.get('task_name') != remote_name
        ),
    }


def _write_checkpoint_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temp_path.write_text(
            json.dumps(dict(payload), indent=2),
            encoding='utf-8',
        )
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def apply_repair_plan(
    *,
    database: Path,
    checkpoint_path: Path,
    plan: Mapping[str, Any],
) -> dict:
    validated = plan['validated_binding']
    repo = PipelineRepo(database)
    binding = repo.record_webodm_binding(
        run_id=str(plan['run_id']),
        operation_key=str(plan['operation']),
        project_id=int(validated['project_id']),
        task_id=str(validated['task_id']),
        task_name=str(validated['task_name']),
        survey_id=plan.get('survey_id'),
        remote_status=str(validated['remote_status']),
        raw_status=validated.get('raw_status'),
        local_status='operator_repaired',
        binding_source='operator_repair',
        audit={
            'tool': 'repair_webodm_binding',
            'conflicts': list(plan.get('conflicts') or []),
        },
        allow_rebind=True,
    )

    checkpoint = load_checkpoint(checkpoint_path)
    checkpoint['project_id'] = int(validated['project_id'])
    checkpoint.setdefault(
        'project_name',
        plan.get('survey_id'),
    )
    checkpoint[str(plan['operation'])] = {
        'id': str(validated['task_id']),
        'name': str(validated['task_name']),
        'remote_status': str(validated['remote_status']),
        'raw_status': validated.get('raw_status'),
    }
    checkpoint.setdefault('downloads', {}).setdefault(
        str(plan['operation']),
        {},
    )
    _write_checkpoint_atomic(checkpoint_path, checkpoint)
    return {
        **dict(plan),
        'dry_run': False,
        'applied_binding_id': int(binding['id']),
        'checkpoint_path': str(checkpoint_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Validate and repair one WebODM run/task binding.'
    )
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--operation', choices=['task4'], default='task4')
    parser.add_argument('--project-id', required=True, type=int)
    parser.add_argument('--task-id', required=True)
    parser.add_argument(
        '--database',
        type=Path,
        default=REPO_ROOT / 'data' / 'pipeline.db',
    )
    parser.add_argument(
        '--checkpoint-dir',
        type=Path,
        default=REPO_ROOT / 'data' / 'logs',
    )
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    checkpoint_path = (
        args.checkpoint_dir
        / f'webodm_checkpoint_{args.run_id}.json'
    )
    context = read_repair_context(
        args.database,
        args.run_id,
        args.operation,
    )
    checkpoint = load_checkpoint(checkpoint_path)

    config = load_pipeline_config()
    webodm = config['webodm']
    processor = WebODMProcessor(
        url=webodm['url'],
        username=webodm['username'],
        password=webodm['password'],
        logger=logging.getLogger('webodm_binding_repair'),
    )
    remote_task = processor.get_task(args.project_id, args.task_id)
    plan = build_repair_plan(
        run_id=args.run_id,
        operation=args.operation,
        project_id=args.project_id,
        task_id=args.task_id,
        context=context,
        checkpoint=checkpoint,
        remote_task=remote_task,
        normalize_status=processor._normalize_status,
    )
    result = (
        apply_repair_plan(
            database=args.database,
            checkpoint_path=checkpoint_path,
            plan=plan,
        )
        if args.apply
        else plan
    )
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
