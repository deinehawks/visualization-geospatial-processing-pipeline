"""An invocation's explicit coordination adapter; never a worker or singleton."""
from contextlib import contextmanager
from pathlib import Path
import time
import uuid

from .identities import remote_key
from .storage import ConsoleError, atomic_json, now


class Execution:
    def __init__(self, store, operation_id, *, sleep=time.sleep):
        self.store, self.operation_id, self.sleep = store, operation_id, sleep
        self.check_control = lambda: None
        self.held = {}

    @property
    def two_runs(self):
        return self.store.status(self.operation_id).get('max_runs', 1) == 2

    @property
    def run_id(self):
        return self.store.status(self.operation_id)['run_id']

    def bind(self, preflight):
        with self.store.lock():
            identity = self.store.identity(preflight)
            identity = self.store.apply_allocation(identity, preflight,
                self.store.status(self.operation_id).get('allocated_survey'))
            self.store.check_identity(identity, self.run_id, exclude=self.operation_id)
            budgets = self.store.commitments(preflight['report'], exclude=self.operation_id)
            self.store.update(self.operation_id, identity=identity, storage_commitments=budgets,
                              state='running')

    def remote(self, url, project_id):
        with self.store.lock():
            value = self.store.status(self.operation_id)
            identity = dict(value.get('identity') or {})
            identity['remote'] = sorted(set(identity.get('remote', [])) | {remote_key(url, project_id)})
            self.store.check_identity(identity, self.run_id, exclude=self.operation_id)
            self.store.update(self.operation_id, identity=identity)

    def require_exclusive(self):
        with self.store.lock():
            if any(row['operation_id'] != self.operation_id for row in self.store.occupants()):
                from shared.stage_runner import StageFailedWithOutput
                raise StageFailedWithOutput('Restart requires exclusive local execution; pause/finish the other run first')
            self.store.update(self.operation_id, exclusive=True)

    def recheck_storage(self, report, *, disk_usage=None):
        import shutil
        disk_usage = disk_usage or shutil.disk_usage
        checked = {'volumes': [{**volume, 'free_bytes': disk_usage(str(volume.get('probe_path') or volume['volume'])).free}
                               for volume in report.get('volumes', [])]}
        with self.store.lock():
            budgets = self.store.commitments(checked, exclude=self.operation_id)
            self.store.update(self.operation_id, storage_commitments=budgets)

    @contextmanager
    def transaction(self):
        # No DB operation waits while holding the admission mutex.
        with self.store.lock_factory(str(self.store.root) + ':database'):
            yield

    @contextmanager
    def resource(self, name):
        if name not in ('compute', 'bulk_transfer'):
            raise ValueError('Unknown execution resource')
        if self.held.get(name):
            self.held[name] += 1
            try:
                yield
            finally:
                self.held[name] -= 1
            return
        if any(self.held.values()):
            raise ConsoleError('Incompatible nested resource acquisition', 'RESOURCE_ORDER')
        acquired = False
        try:
            while not acquired:
                self.check_control()
                with self.store.lock():
                    rows = self.store.occupants()
                    holder = next((row for row in rows if row['operation_id'] != self.operation_id
                                   and (row.get('resources') or {}).get(name)), None)
                    if any(row['uncertain'] for row in rows if row['operation_id'] != self.operation_id):
                        raise ConsoleError('Resource owner needs reconciliation', 'RESOURCE_UNKNOWN')
                    value = self.store.status(self.operation_id)
                    if holder is None:
                        self.store.update(self.operation_id, resources={**value.get('resources', {}), name: True},
                                          resource_wait=None)
                        self.held[name] = 1
                        acquired = True
                    else:
                        self.store.update(self.operation_id, resource_wait={'resource': name,
                            'holder_run_id': holder.get('run_id'), 'holder_operation_id': holder['operation_id']})
                if not acquired:
                    self.sleep(0.5)
            self.check_control()
            self.check_committed_storage()
            yield
        finally:
            with self.store.lock():
                value = self.store.status(self.operation_id)
                resources = dict(value.get('resources') or {})
                # Returning from a managed subprocess must not release its permit
                # if descendants are still alive or unverifiable.
                children = self.store.processes.job_active(value['job_name'])
                if acquired and (children is None or children > 1):
                    self.store.update(self.operation_id, state='requires_recovery', resource_wait=None)
                    raise ConsoleError('Owned children still hold the resource', 'RESOURCE_UNKNOWN')
                if acquired:
                    resources.pop(name, None)
                    self.held.pop(name, None)
                self.store.update(self.operation_id, resources=resources, resource_wait=None)

    def allocate_survey(self, root, year, override, use_year, force, logger):
        from modules.data_segregation.data_segregation import generate_next_survey_id
        from .storage import read_json
        from shared.stage_runner import StageRequiresRecovery
        with self.store.lock():
            value = self.store.status(self.operation_id)
            previous = value.get('allocated_survey')
            if previous:
                if (previous['root'] != self.store.path_identity(root) or previous['year'] != year
                        or previous['use_year'] != use_year or previous['override'] != override):
                    raise StageRequiresRecovery('Survey allocation parameters changed; retained claim needs diagnosis')
                target = Path(previous['path'])
                marker = target / '.pipeline-allocation.json'
                owner = {'run_id': self.run_id, 'operation_id': previous.get('owner_operation_id') or self.operation_id}
                if not target.exists():
                    target.mkdir(parents=True, exist_ok=False)
                    atomic_json(marker, owner)
                elif not marker.is_file() or read_json(marker) != owner:
                    raise StageRequiresRecovery('Partial survey allocation has no matching ownership marker; refusing reuse')
                return previous['survey_id'], target
            survey_id = override or generate_next_survey_id(Path(root), year, logger)
            if not survey_id or survey_id in ('.', '..') or '/' in survey_id or '\\' in survey_id or ':' in survey_id:
                raise ConsoleError('Survey ID must be a single safe path component', 'INVALID_INPUT')
            target = (Path(root) / str(year) if use_year else Path(root)) / survey_id / 'rgb'
            identity = dict(value.get('identity') or {})
            identity.update(survey_id=survey_id.casefold(), output=self.store.path_identity(target))
            self.store.check_identity(identity, self.run_id, exclude=self.operation_id)
            if target.exists() and not force:
                raise FileExistsError('Survey path already exists: ' + str(target))
            claim = {'root': self.store.path_identity(root), 'year': year, 'use_year': use_year,
                     'override': override, 'survey_id': survey_id, 'path': str(target)}
            marker = target / '.pipeline-allocation.json'
            if marker.exists():
                raise StageRequiresRecovery('Existing survey allocation marker requires diagnosis before forced reuse')
            atomic_json(self.store.directory(self.operation_id) / 'survey-allocation.json', claim)
            self.store.update(self.operation_id, identity=identity, allocated_survey=claim)
            try:
                target.mkdir(parents=True, exist_ok=force)
                atomic_json(marker, {'run_id': self.run_id, 'operation_id': self.operation_id})
            except Exception as exc:
                raise StageRequiresRecovery('Survey folder claim is incomplete; preserve allocation evidence') from exc
            return survey_id, target

    def check_committed_storage(self):
        budgets = self.store.status(self.operation_id).get('storage_commitments') or {}
        self.recheck_storage({'volumes': [{'probe_path': value['probe_path'], 'required_bytes': value['required'],
            'reserve_bytes': value['reserve'], 'free_bytes': value['free']} for value in budgets.values()]})

    def failed_result(self, result):
        from .projections import redact
        with self.store.lock():
            atomic_json(self.store.directory(self.operation_id) / 'pipeline-result.json',
                        redact({key: result[key] for key in ('run_id', 'success', 'paused', 'error', 'requires_recovery') if key in result}))
            if result.get('requires_recovery') or result.get('run_status_override') == 'requires_recovery' or self.store.status(self.operation_id).get('pending_creation'):
                self.store.update(self.operation_id, state='requires_recovery')

    def created(self, project_id, task_id=None):
        with self.store.lock():
            receipt = 'creation-' + uuid.uuid4().hex + '.json'
            value = {'receipt': receipt, 'project_id': int(project_id), 'task_id': task_id, 'at': now()}
            atomic_json(self.store.directory(self.operation_id) / receipt, value)
            self.store.update(self.operation_id, pending_creation=value)

    def binding_committed(self, project_id, task_id):
        with self.store.lock():
            pending = self.store.status(self.operation_id).get('pending_creation')
            if pending and pending['project_id'] == int(project_id) and (not pending['task_id'] or pending['task_id'] == task_id):
                atomic_json(self.store.directory(self.operation_id) / (pending['receipt'] + '.bound.json'),
                            {'project_id': int(project_id), 'task_id': task_id, 'at': now()})
                self.store.update(self.operation_id, pending_creation=None)

    def uncertain_remote(self, operation):
        with self.store.lock():
            atomic_json(self.store.directory(self.operation_id) / 'remote-mutation.json',
                        {'operation': operation, 'at': now(), 'resolved': False})
            self.store.update(self.operation_id, state='requires_recovery')
