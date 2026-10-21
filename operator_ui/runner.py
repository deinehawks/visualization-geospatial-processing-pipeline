"""One explicitly requested helper operation, never a queue worker."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import logging
from pathlib import Path
import time
import uuid

from .models import RunInput
from .projections import redact, safe_url
from .storage import ConsoleError, ConsoleStore, atomic_json, now, read_json


class DatasetChoiceRequired(ConsoleError):
    def __init__(self, candidates):
        super().__init__('Choose one exact dataset folder and check again', 'DATASET_CHOICE_REQUIRED', 422)
        self.candidates = [str(path) for path in candidates]


def require_dataset_choice(candidates):
    raise DatasetChoiceRequired(candidates)


def arguments(inputs, *, preflight):
    value = RunInput.model_validate(inputs)
    argv = ['--survey', value.survey, '--year', str(value.year), '--run-id', value.run_id]
    if value.survey_id:
        argv += ['--survey-id', value.survey_id]
    argv += ['--both-tasks' if value.mode == 'both' else '--' + str(value.mode)]
    if value.action != 'new':
        argv += ['--resume']
    for key in ('date', 'uav', 'node_id'):
        if getattr(value, key) is not None:
            argv += ['--' + key.replace('_', '-'), str(getattr(value, key))]
    for key in ('disable_cross_run', 'rgb', 'keep_workspace'):
        if getattr(value, key):
            argv += ['--' + key.replace('_', '-')]
    if value.action == 'rebuild_tiles':
        argv += ['--rebuild-qgis-tiles', value.rebuild_task,
                 '--qgis-tile-rebuild-confirmation', value.confirmation]
    if preflight:
        argv += ['--storage-preflight-only']
    return argv


class BrowserInteraction:
    def __init__(self, store, operation_id, *, sleep=time.sleep):
        self.store, self.operation_id, self.sleep = store, operation_id, sleep
        self.directory = store.directory(operation_id)
        self.review_id = None

    def poll(self):
        directory = self.directory / 'controls'
        if not directory.exists():
            return None
        with self.store.lock():
            for path in sorted(directory.glob('*.json')):
                if path.name.endswith('.ack.json'):
                    continue
                ack = path.with_name(path.stem + '.ack.json')
                if ack.exists():
                    continue
                command = read_json(path)
                if command.get('action') not in ('pause', 'stop'):
                    raise ConsoleError('Invalid owned control command')
                atomic_json(ack, {**command, 'state': 'acknowledged', 'at': now()})
                return command['action']
        return None

    def decision(self, *, check_control, logger=None, **fields):
        from shared.constants import WEBODM_RESTART_STAGE_NAMES
        review_id = self.review_id = uuid.uuid4().hex
        review = redact({'review_id': review_id, 'survey_id': fields.get('survey_id'),
                         'project_id': fields['project_id'],
                         'tasks': {key: fields.get(key) or {} for key in ('task1', 'task2', 'task4')},
                         'webodm_url': safe_url(fields.get('webodm_url') or ''),
                         'restart_stages': sorted(WEBODM_RESTART_STAGE_NAMES)})
        directory = self.directory / 'reviews'
        with self.store.lock():
            directory.mkdir(exist_ok=True)
            atomic_json(directory / (review_id + '.json'), review)
            atomic_json(self.directory / 'review.json', review)
            self.store.update(self.operation_id, state='awaiting_review')
        while True:
            check_control()
            path = directory / (review_id + '.decision.json')
            if path.exists():
                with self.store.lock():
                    value = read_json(path)
                    atomic_json(directory / (review_id + '.consumed.json'), {'at': now(), 'decision': value})
                    self.store.update(self.operation_id, state='running')
                if value['action'] == 'restart':
                    return f"restart {value['task']} {value['restart_from']}"
                return 'yes' if value['action'] == 'approve' else 'fail'
            self.sleep(0.5)

    def restart_started(self, project_id, task_id, restart_from):
        with self.store.lock():
            path = self.directory / 'restart-intent.json'
            if path.exists() and not read_json(path).get('resolved'):
                raise ConsoleError('Previous remote restart outcome is unknown', 'REMOTE_OUTCOME_UNKNOWN')
            payload = {'review_id': self.review_id, 'project_id': project_id, 'task_id': task_id,
                       'restart_from': restart_from, 'at': now(), 'resolved': False}
            atomic_json(self.directory / 'reviews' / (self.review_id + '.restart.json'), payload)
            atomic_json(path, payload)

    def restart_acknowledged(self):
        with self.store.lock():
            path = self.directory / 'restart-intent.json'
            atomic_json(path, {**read_json(path), 'resolved': True, 'acknowledged_at': now()})

    def restart_finished(self, task_info):
        with self.store.lock():
            atomic_json(self.directory / 'reviews' / (self.review_id + '.restart-result.json'),
                        {'task': redact(task_info), 'at': now()})


def execute(store, operation_id, *, launch=None):
    request = read_json(store.directory(operation_id) / 'request.json')
    if request['kind'] not in ('preflight', 'run'):
        raise ConsoleError('Unsupported helper request')
    if launch is None:
        # Only this explicitly dispatched helper loads production launch/config code.
        from main import main as launch
    directory = store.directory(operation_id)
    with store.claim(operation_id):
        try:
            observed = False
            interaction = BrowserInteraction(store, operation_id)
            def observe(value):
                nonlocal observed
                observed = True
                safe = redact(value)
                with store.lock():
                    atomic_json(directory / 'preflight.json', safe)
                expected = request.get('expected_preflight')
                if expected is not None:
                    keys = ('source_dir', 'mode', 'run_id', 'year', 'survey_id', 'surveys_root',
                            'workspace_root', 'config_fingerprint', 'stage_statuses')
                    if any(value.get(key) != expected.get(key) for key in keys):
                        raise ConsoleError('Inputs/configuration/state changed; perform a new preflight', 'PREFLIGHT_CHANGED')
            # A real execution always reruns the shared preflight inside launch.
            with store.lock():
                if request['kind'] == 'run':
                    store.update(operation_id, state='running')
            result = launch(arguments(request['inputs'], preflight=request['kind'] == 'preflight'),
                            observer=observe, candidate_selector=require_dataset_choice,
                            pipeline_options={'review_provider': interaction, 'control_transport': interaction,
                                              'enable_hotkeys': False},
                            guard_factory=lambda *_: nullcontext())
            if not observed:
                raise ConsoleError('Shared launch did not perform preflight', 'PREFLIGHT_MISSING')
            if request['kind'] == 'preflight':
                if not isinstance(result, dict) or not result.get('report', {}).get('ok'):
                    raise ConsoleError('Preflight did not produce a passing report', 'PREFLIGHT_FAILED')
                payload = {'preflight': result}
            else:
                if not isinstance(result, dict) or result.get('run_id') != request['run_id']:
                    raise ConsoleError('Runner returned no matching structured result', 'RESULT_MISSING')
                # The database owns stage/artifact projections. Do not poll or
                # duplicate a potentially huge image-classification state blob.
                fields = ('run_id', 'survey_id', 'success', 'paused', 'canceled', 'aborted',
                          'error', 'run_status_override', 'requires_recovery')
                summary = {key: result[key] for key in fields if key in result}
                summary['partially_completed'] = result.get('run_status_override') == 'partially_completed'
                payload = {'pipeline': summary}
            with store.lock():
                atomic_json(directory / 'result.json', redact(payload))
                intent = directory / 'restart-intent.json'
                unresolved = intent.exists() and not read_json(intent).get('resolved')
                state = 'requires_recovery' if unresolved else 'finished'
                if request['kind'] == 'run' and not result.get('success') and not result.get('paused'):
                    state = 'requires_recovery' if unresolved else 'failed'
                store.update(operation_id, state=state)
            return payload
        except BaseException as exc:
            logging.getLogger('pipeline.console.runner').exception('Operation %s failed', operation_id)
            # Explicit failures retain evidence. An abrupt process loss leaves its nonterminal receipt.
            payload = {'error': redact(str(exc)), 'code': getattr(exc, 'code', 'HELPER_FAILED')}
            if isinstance(exc, DatasetChoiceRequired):
                payload['candidates'] = exc.candidates
            with store.lock():
                had_result = (directory / 'result.json').exists()
                # A status-commit failure must not overwrite a real execution result.
                atomic_json(directory / ('failure.json' if had_result else 'result.json'), payload)
                intent = directory / 'restart-intent.json'
                unresolved = intent.exists() and not read_json(intent).get('resolved')
                pre_execution = payload['code'] in ('PREFLIGHT_CHANGED', 'PREFLIGHT_MISSING') or (
                    isinstance(exc, SystemExit) and exc.code in (2, 3)
                )
                uncertain_run = request['kind'] == 'run' and not pre_execution
                state = 'requires_recovery' if unresolved or had_result or uncertain_run else 'failed'
                store.update(operation_id, state=state, error=payload['error'])
            if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                raise
            return payload


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--operation', required=True)
    args = parser.parse_args(argv)
    # Helpers are launched with the repository as cwd, never a caller-supplied script.
    expected = Path.cwd().resolve() / 'runtime' / 'pipeline-ui'
    if args.root.resolve() != expected:
        raise ConsoleError('Helper workspace does not match this repository')
    result = execute(ConsoleStore(args.root), args.operation)
    return 1 if result.get('error') or result.get('pipeline', {}).get('success') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
