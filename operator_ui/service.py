"""Typed command dispatch. No pipeline work is run by HTTP handlers."""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import uuid

from .models import RunInput
from .projections import ReadModel, redact
from .storage import ConsoleError, ConsoleStore, TERMINAL, atomic_json, identifier, now, read_json


class ConsoleService:
    def __init__(self, base_dir, *, store=None, reader=None, popen=None, pipeline_python=None):
        self.base_dir = Path(base_dir).resolve()
        self.store = store or ConsoleStore(self.base_dir / 'runtime' / 'pipeline-ui')
        self.reader = reader or ReadModel(self.base_dir / 'data' / 'pipeline.db', self.base_dir / 'data' / 'logs')
        self.popen = popen or subprocess.Popen
        self.actor_id = 'local-operator'
        self.pipeline_python = Path(pipeline_python) if pipeline_python else self.base_dir / '.venv310' / 'Scripts' / 'python.exe'

    def legacy_running(self):
        owned = {item.get('run_id') for item in self.store.operations() if item['kind'] in ('run', 'cli')}
        return [row['run_id'] for row in self.reader.query("SELECT run_id FROM runs WHERE status='running'")
                if row['run_id'] not in owned]

    def recorded_inputs(self, run_id):
        for row in self.store.operations():
            if row['kind'] == 'run' and row.get('run_id') == run_id:
                return read_json(self.store.directory(row['operation_id']) / 'request.json').get('inputs', {})
        return {}

    def normalize(self, inputs):
        value = RunInput.model_validate(inputs).model_dump()
        if value['action'] == 'new':
            if not value['survey'] or value['run_id']:
                raise ConsoleError('New runs require a survey and must not reuse a run ID', 'INVALID_INPUT', 422)
            value['mode'] = value['mode'] or 'task4'
            value['run_id'] = str(uuid.uuid4())
        else:
            if not value['run_id']:
                raise ConsoleError('Resume requires the original run ID', 'INVALID_INPUT', 422)
            detail = self.reader.detail(value['run_id'])
            recorded_mode = self.recorded_inputs(value['run_id']).get('mode')
            if detail['mode'] is None and recorded_mode in ('task2', 'task4', 'both'):
                detail['mode'] = recorded_mode
            run = detail['run']
            if run['status'] == 'completed':
                raise ConsoleError('Completed runs cannot be rerun through V1 controls')
            if detail['mode'] and value['mode'] and value['mode'] != detail['mode']:
                raise ConsoleError('Task mode conflicts with persisted evidence')
            value['mode'] = detail['mode'] or value['mode']
            if value['mode'] is None:
                raise ConsoleError('Historical task selection is unknown; select the intended task mode', 'MODE_REQUIRED', 422)
            value.update(survey=run.get('source_dir') or '', survey_id=run.get('survey_id'), year=run['year'])
        if value['action'] == 'rebuild_tiles':
            task = value['rebuild_task']
            if not task or (value['mode'] != 'both' and task != value['mode']):
                raise ConsoleError('Select a tile task belonging to this run', 'INVALID_INPUT', 422)
            expected = f"REBUILD QGIS TILES {task} FOR RUN {value['run_id']}"
            if value['confirmation'] != expected:
                raise ConsoleError('Exact tile-rebuild confirmation required: ' + expected, 'CONFIRMATION_REQUIRED', 422)
        elif value['rebuild_task'] or value['confirmation']:
            raise ConsoleError('Recovery options require tile-rebuild mode', 'INVALID_INPUT', 422)
        return value

    def preflight(self, command_id, inputs):
        # A repeated request must return its existing immutable normalized run ID.
        original = RunInput.model_validate(inputs).model_dump()
        with self.store.lock():
            self.store.ensure()
            path = self.store.directory(command_id)
            if path.exists():
                old = read_json(path / 'request.json')
                if old.get('original_inputs') != original or old.get('kind') != 'preflight':
                    raise ConsoleError('Command ID already used', 'IDEMPOTENCY_CONFLICT')
                return self.operation(command_id)
            if self.legacy_running():
                raise ConsoleError('A legacy terminal run is running or unverified. Monitor it until it stops/pauses.', 'LEGACY_RUNNING')
        value = self.normalize(original)
        return self.dispatch(command_id, {'kind': 'preflight', 'inputs': value,
                                         'original_inputs': original, 'run_id': value['run_id']})

    def start(self, command_id, preflight_id):
        prior = self.operation(preflight_id)
        result = prior.get('result') or {}
        if prior['kind'] != 'preflight' or prior['state'] != 'finished' or not result.get('preflight', {}).get('report', {}).get('ok'):
            raise ConsoleError('A successful matching preflight is required', 'PREFLIGHT_REQUIRED')
        inputs = read_json(self.store.directory(preflight_id) / 'request.json')['inputs']
        inputs['survey'] = result['preflight']['source_dir']
        return self.dispatch(command_id, {'kind': 'run', 'inputs': inputs, 'preflight_id': preflight_id,
                                         'run_id': inputs['run_id'], 'expected_preflight': result['preflight']})

    def dispatch(self, command_id, request):
        identifier(command_id)
        with self.store.lock():
            self.store.ensure()
            if self.legacy_running():
                raise ConsoleError('Legacy terminal execution blocks browser dispatch', 'LEGACY_RUNNING')
        value, fresh = self.store.reserve(command_id, request)
        if not fresh:
            return self.operation(command_id)
        directory = self.store.directory(command_id)
        spawned = False
        try:
            atomic_json(directory / 'actor.json', {'actor_id': self.actor_id, 'at': now(), 'action': request['kind']})
            if not self.pipeline_python.is_file():
                raise FileNotFoundError('Configured pipeline Python was not found')
            argv = [str(self.pipeline_python), '-u', '-m', 'operator_ui.runner', '--root', str(self.store.root),
                    '--operation', command_id]
            atomic_json(directory / 'dispatch-intent.json', {'at': now(), 'argv': argv})
            with (directory / 'console.log').open('xb') as log:
                env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
                process = self.popen(argv, cwd=str(self.base_dir), stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT, shell=False, close_fds=True,
                                     env=env,
                                     creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0)
                spawned = True
            atomic_json(directory / 'spawned.json', {'pid': process.pid, 'birth': self.store.processes.identity(process.pid)})
        except FileNotFoundError as exc:
            # No child was created. Retain the failed attempt; never reuse its ID.
            with self.store.lock():
                self.store.update(command_id, state='requires_recovery' if spawned else 'failed', error=str(exc))
        except Exception as exc:
            # Popen may have succeeded: outcome is uncertain, not safely retryable.
            with self.store.lock():
                self.store.update(command_id, state='requires_recovery', error=redact(str(exc)))
        return self.operation(command_id)

    def operation(self, operation_id):
        path = self.store.directory(operation_id)
        if not path.is_dir():
            raise ConsoleError('Operation not found', 'OPERATION_NOT_FOUND', 404)
        value = self.store.status(operation_id)
        spawned = path / 'spawned.json'
        if not value.get('pid') and spawned.is_file() and value['state'] not in TERMINAL:
            process = read_json(spawned)
            if self.store.processes.identity(process['pid']) is None:
                value = {**value, 'observed_state': 'requires_reconciliation',
                         'error': 'Helper exited before recording ownership. Inspect the retained helper log; no command will be replayed.'}
        live = self.store.processes.identity(value['pid']) if value.get('pid') else None
        verified = value.get('birth') is not None and live == value['birth']
        value['owner_verified'] = verified
        value['resource_busy'] = value['state'] not in TERMINAL or verified or live == 'unknown' or self.store.processes.job_active(value['job_name']) != 0
        if value['state'] not in TERMINAL and value.get('pid') and not verified:
            value = {**value, 'observed_state': 'requires_reconciliation'}
        for key, filename in (('result', 'result.json'), ('failure', 'failure.json'), ('review', 'review.json'), ('preflight', 'preflight.json')):
            if (path / filename).is_file():
                value[key] = read_json(path / filename)
        value['controls_available'] = verified and value['kind'] == 'run' and value['state'] in ('running', 'awaiting_review')
        log = path / 'console.log'
        value['log_tail'] = ''
        if log.is_file() and not log.is_symlink():
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 16384))
                value['log_tail'] = redact(stream.read(16384).decode('utf-8', errors='replace'))[-16384:]
        controls = path / 'controls'
        value['control_commands'] = []
        if controls.is_dir() and not controls.is_symlink():
            for command_path in sorted(controls.glob('*.json'))[-40:]:
                if not command_path.name.endswith('.ack.json'):
                    ack = command_path.with_name(command_path.stem + '.ack.json')
                    command = read_json(ack if ack.exists() else command_path)
                    fallback = 'not_applied_invocation_finished' if value['state'] in TERMINAL else 'requested'
                    value['control_commands'].append({**command, 'state': command.get('state', fallback)})
        return redact(value)

    def control(self, operation_id, command_id, action):
        if action not in ('pause', 'stop'):
            raise ConsoleError('Unsupported local control', 'INVALID_INPUT', 422)
        with self.store.lock():
            directory = self.store.directory(operation_id) / 'controls'
            path = directory / (identifier(command_id) + '.json')
            payload = {'command_id': command_id, 'action': action}
            if path.exists() and read_json(path) != payload:
                raise ConsoleError('Command ID already used', 'IDEMPOTENCY_CONFLICT')
            if path.exists():
                ack = path.with_name(path.stem + '.ack.json')
                return read_json(ack) if ack.exists() else {**payload, 'state': 'requested'}
            value = self.operation(operation_id)
            if not value['controls_available']:
                raise ConsoleError('This invocation does not accept controls', 'ACTION_UNAVAILABLE')
            directory.mkdir(exist_ok=True)
            for pending in directory.glob('*.json'):
                if not pending.name.endswith('.ack.json') and not pending.with_name(pending.stem + '.ack.json').exists():
                    raise ConsoleError('A local control request is already pending', 'CONTROL_PENDING')
            if not path.exists():
                self.audit(operation_id, command_id, action)
                atomic_json(path, payload)
            return {'command_id': command_id, 'state': 'requested', 'action': action}

    def review(self, operation_id, command):
        from shared.constants import WEBODM_RESTART_STAGE_NAMES
        with self.store.lock():
            path = self.store.directory(operation_id) / 'reviews' / (command.review_id + '.decision.json')
            payload = command.model_dump()
            if path.exists():
                if read_json(path) != payload:
                    raise ConsoleError('This review already has a decision', 'ALREADY_DECIDED')
                consumed = path.with_name(command.review_id + '.consumed.json')
                return {'state': 'acknowledged' if consumed.exists() else 'requested', 'command_id': command.command_id}
            value = self.operation(operation_id)
            review = value.get('review') or {}
            if not value['controls_available'] or value['state'] != 'awaiting_review' or review.get('review_id') != command.review_id:
                raise ConsoleError('Review is stale or unavailable', 'STALE_REVIEW')
            if command.action == 'restart':
                task = (review.get('tasks') or {}).get(command.task)
                if not task or not task.get('id') or command.restart_from not in WEBODM_RESTART_STAGE_NAMES:
                    raise ConsoleError('Invalid review task or restart stage', 'INVALID_INPUT', 422)
                expected = f"RESTART {command.task} {command.restart_from} FOR RUN {value['run_id']}"
                if command.confirmation != expected:
                    raise ConsoleError('Exact restart confirmation required: ' + expected, 'CONFIRMATION_REQUIRED', 422)
            if path.exists():
                if read_json(path) != payload:
                    raise ConsoleError('This review already has a decision', 'ALREADY_DECIDED')
            else:
                self.audit(operation_id, command.command_id, 'quality_' + command.action)
                atomic_json(path, payload)
            return {'state': 'requested', 'command_id': command.command_id}

    def audit(self, operation_id, command_id, action):
        directory = self.store.directory(operation_id) / 'audit'
        directory.mkdir(exist_ok=True)
        path = directory / (identifier(command_id) + '.json')
        if not path.exists():
            atomic_json(path, {'actor_id': self.actor_id, 'action': action, 'at': now()})

    def run_detail(self, run_id):
        result = self.reader.detail(run_id)
        result['console_operations'] = [self.operation(row['operation_id']) for row in self.store.operations()
                                        if row.get('run_id') == run_id]
        result['terminal_monitor_only'] = not any(row['controls_available'] for row in result['console_operations'])
        result['resume_options'] = {}
        inputs = self.recorded_inputs(run_id)
        result['resume_options'] = {key: inputs[key] for key in ('date', 'uav', 'node_id', 'rgb',
            'disable_cross_run', 'keep_workspace') if key in inputs}
        if result['mode'] is None and inputs.get('mode') in ('task2', 'task4', 'both'):
            result.update(mode=inputs['mode'], mode_requires_confirmation=False, mode_source='console_request')
        base_url = next((row.get('preflight', {}).get('webodm_url') for row in result['console_operations']
                         if row.get('preflight', {}).get('webodm_url')), None)
        result['webodm_project_links'] = []
        if base_url and not base_url.startswith('['):
            for project_id in sorted({row['project_id'] for row in result['operations'] if row.get('project_id')}):
                result['webodm_project_links'].append({'project_id': project_id,
                    'url': base_url.rstrip('/') + '/dashboard/' + str(project_id)})
        return result
