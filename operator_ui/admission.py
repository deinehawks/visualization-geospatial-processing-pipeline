"""Capacity/identity policy on the existing owned operation journal."""
from datetime import datetime, timezone
from pathlib import Path

from .identities import overlaps, remote_key


class AdmissionMixin:
    def policy(self):
        from .storage import read_json, ConsoleError
        path = self.root / 'execution-policy.json'
        value = read_json(path) if path.exists() else {'version': 1, 'max_runs': 1}
        if value != {'version': 1, 'max_runs': value.get('max_runs')} or type(value.get('max_runs')) is not int or value['max_runs'] not in (1, 2):
            raise ConsoleError('Invalid execution policy; refusing admission', 'INVALID_POLICY')
        return value

    def occupants(self):
        from .storage import read_json, TERMINAL
        values = []
        for value in self.operations():
            live = self.processes.identity(value['pid']) if value.get('pid') else None
            children = self.processes.job_active(value['job_name'])
            present = live == 'unknown' or (live is not None and (not value.get('birth') or live == value['birth']))
            intent = self.directory(value['operation_id']) / 'restart-intent.json'
            mutation = self.directory(value['operation_id']) / 'remote-mutation.json'
            remote_unknown = ((intent.exists() and not read_json(intent).get('resolved'))
                              or (mutation.exists() and not read_json(mutation).get('resolved'))
                              or bool(value.get('pending_creation') and not present))
            if value['state'] not in TERMINAL or present or children != 0 or remote_unknown:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['created_at'])).total_seconds()
                launching = value['state'] == 'accepted' and not value.get('pid') and age < 30
                uncertain = (value.get('coordination_version') != 2 or remote_unknown
                    or value['state'] == 'requires_recovery' or live == 'unknown' or children is None
                    or (not present and not launching and (value['state'] not in TERMINAL or children != 0)))
                values.append({**value, 'uncertain': uncertain})
        return values

    def capacity(self):
        rows = self.occupants()
        limit = self.policy()['max_runs']
        blocked = any(row['uncertain'] or row.get('exclusive') for row in rows)
        return {'max_runs': limit, 'occupied': len(rows), 'available': 0 if blocked else max(0, limit - len(rows)),
                'blocked': blocked, 'holders': rows}

    def configure(self, maximum, *, database=None):
        from .storage import ConsoleError, atomic_json
        if type(maximum) is not int or maximum not in (1, 2):
            raise ConsoleError('Only one or two local slots are supported', 'INVALID_INPUT')
        with self.lock():
            self.ensure()
            if self.occupants():
                raise ConsoleError('Policy changes require an idle reconciled installation', 'BUSY')
            if database is not None:
                self.verify_legacy(database)
            atomic_json(self.root / 'execution-policy.json', {'version': 1, 'max_runs': maximum})
        return self.capacity()

    def verify_legacy(self, database):
        from .storage import ConsoleError
        from .legacy_reconciliation import legacy_status
        if legacy_status(self, database)['legacy_running']:
            raise ConsoleError('Legacy running state needs diagnosis before new execution', 'LEGACY_RUNNING')

    def identity(self, preflight):
        run_id = preflight['run_id']
        survey = preflight.get('survey_id')
        output = preflight.get('published_path')
        if not output and survey:
            output = Path(preflight['surveys_root']) / str(preflight['year']) / survey / 'rgb'
        return {'source': self.path_identity(preflight['source_dir']),
                'workspace': self.path_identity(Path(preflight['workspace_root']) / run_id),
                'output': self.path_identity(output) if output else None,
                'survey_id': str(survey).casefold() if survey else None,
                'remote': sorted({remote_key(preflight['webodm_url'], row['project_id'])
                                  for row in preflight.get('bindings', []) if row.get('project_id')})}

    def check_identity(self, identity, run_id, *, exclude=None):
        from .storage import ConsoleError
        for row in self.occupants():
            if row['operation_id'] == exclude or row['kind'] == 'preflight':
                continue
            other = row.get('identity') or {}
            if row.get('run_id') == run_id:
                raise ConsoleError('This run already has an invocation', 'DUPLICATE_RUN')
            if identity.get('survey_id') and identity['survey_id'] == other.get('survey_id'):
                raise ConsoleError('Survey already belongs to another active invocation', 'IDENTITY_CONFLICT')
            if any(overlaps(left, right) for left in (identity.get('source'), identity.get('workspace'), identity.get('output'))
                   for right in (other.get('source'), other.get('workspace'), other.get('output'))):
                raise ConsoleError('Source/workspace/output overlaps another invocation', 'IDENTITY_CONFLICT')
        # Never adopt an existing remote project from a different historical run,
        # even after local pause; that task can still be running remotely.
        for row in self.operations():
            if row.get('run_id') != run_id and set(identity.get('remote', [])) & set((row.get('identity') or {}).get('remote', [])):
                raise ConsoleError('WebODM project belongs to another run', 'REMOTE_IDENTITY_CONFLICT')

    def inherited_allocation(self, run_id):
        for row in self.operations():
            if row.get('run_id') == run_id and row.get('allocated_survey'):
                return {**row['allocated_survey'], 'owner_operation_id':
                        row['allocated_survey'].get('owner_operation_id') or row['operation_id']}
        return None

    def apply_allocation(self, identity, report, allocation):
        from .storage import ConsoleError
        if allocation:
            if allocation['root'] != self.path_identity(report['surveys_root']) or allocation['year'] != report['year']:
                raise ConsoleError('Resume allocation root/year conflicts with its retained claim', 'IDENTITY_CONFLICT')
            if identity.get('survey_id') and identity['survey_id'] != allocation['survey_id'].casefold():
                raise ConsoleError('Resume survey conflicts with its retained claim', 'IDENTITY_CONFLICT')
            identity = {**identity, 'survey_id': allocation['survey_id'].casefold(),
                        'output': self.path_identity(allocation['path'])}
        return identity

    def commitments(self, report, *, exclude=None):
        from .storage import ConsoleError
        others = [row.get('storage_commitments') or {} for row in self.occupants()
                  if row['operation_id'] != exclude and row['kind'] != 'preflight']
        result = {}
        for volume in report.get('volumes', []):
            key = self.volume_identity(volume.get('probe_path') or volume['volume'])
            value = result.setdefault(key, {'required': 0, 'reserve': 0, 'free': int(volume['free_bytes']),
                                             'probe_path': str(volume.get('probe_path') or volume['volume'])})
            value['required'] += max(0, int(volume['required_bytes']))
            value['reserve'] = max(value['reserve'], int(volume['reserve_bytes']))
            value['free'] = min(value['free'], int(volume['free_bytes']))
        for key, value in result.items():
            committed = sum(int(other.get(key, {}).get('required', 0)) for other in others)
            reserve = max([value['reserve']] + [int(other.get(key, {}).get('reserve', 0)) for other in others])
            if value['free'] < value['required'] + committed + reserve:
                raise ConsoleError('Storage is committed to another run; check inputs/storage again', 'STORAGE_COMMITTED')
        return result

    def admit(self, request):
        from .storage import ConsoleError
        rows = self.occupants()
        policy = self.policy()
        if any(row['uncertain'] or row.get('exclusive') for row in rows):
            raise ConsoleError('An operation is active or requires reconciliation', 'BUSY')
        report = request.get('admission') or request.get('expected_preflight')
        exclusive = bool(request.get('exclusive') or (report or {}).get('exclusive')
                         or (request.get('inputs') or {}).get('action') == 'rebuild_tiles')
        if len(rows) >= policy['max_runs'] or (exclusive and rows):
            raise ConsoleError('Local execution capacity is occupied by active operations', 'BUSY')
        identity = self.identity(report) if report else None
        allocation = self.inherited_allocation(request.get('run_id')) if request['kind'] != 'preflight' else None
        if identity is not None:
            identity = self.apply_allocation(identity, report, allocation)
        if policy['max_runs'] == 2 and request['kind'] != 'preflight' and identity is None:
            raise ConsoleError('Two-run execution requires resolved identity/storage evidence', 'IDENTITY_REQUIRED')
        if identity is not None and request['kind'] != 'preflight':
            self.check_identity(identity, request.get('run_id'))
        budgets = self.commitments(report['report']) if report and request['kind'] != 'preflight' else {}
        return {'coordination_version': 2, 'identity': identity, 'exclusive': exclusive,
                'storage_commitments': budgets, 'resources': {}, 'max_runs': policy['max_runs'],
                'allocated_survey': allocation}
