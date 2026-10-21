"""Two-run policy under pytest-owned paths and injected process/lock adapters."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import logging
import sqlite3
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_pipeline_console import console, uid
from operator_ui.coordination import Execution
from operator_ui.controls import LocalControls, request_control
from operator_ui.identities import overlaps, remote_key
from operator_ui.storage import ConsoleError, read_json
from shared.pipeline_control import PipelineControl
from shared.execution import StatePersistenceError
from shared.stage_runner import StageRunner, StageRequiresRecovery
from shared.db.repo import PipelineRepo, _CommitRetry
from tools.pipeline_control import main as control_main


@pytest.fixture
def parallel(console):
    console.store.path_identity = lambda path: str(Path(path).resolve()).replace('\\', '/').rstrip('/').casefold()
    console.store.volume_identity = lambda _: 'owned-test-volume'
    console.store.configure(2, database=console.reader.database)
    return console


def report(console, name, **fields):
    return {'run_id': name, 'survey_id': 'AH-026' + name, 'year': 2026,
        'source_dir': str(console.base_dir / 'sources' / name),
        'workspace_root': str(console.base_dir / 'workspaces'),
        'surveys_root': str(console.base_dir / 'surveys'), 'report': {'ok': True, 'volumes': []},
        'webodm_url': 'http://example.invalid:8000', 'bindings': [], **fields}


def reserve(console, name, *, kind='cli', **fields):
    operation_id = uid()
    request = {'kind': kind, 'run_id': name, 'admission': report(console, name, **fields)}
    if kind == 'run':
        request['preflight_id'] = uid()
    console.store.reserve(operation_id, request)
    return Execution(console.store, operation_id)


def run(console, execution, pid):
    console.store.processes.live[pid] = 'birth:' + str(pid)
    value = console.store.status(execution.operation_id)
    console.store.processes.jobs[value['job_name']] = 1
    console.store.update(execution.operation_id, state='running', pid=pid, birth='birth:' + str(pid))


def finish(console, execution):
    value = console.store.status(execution.operation_id)
    console.store.processes.live.pop(value.get('pid'), None)
    console.store.processes.jobs[value['job_name']] = 0
    console.store.update(execution.operation_id, state='finished')


def volume(console, required=60, free=100, reserve=5):
    return {'probe_path': str(console.base_dir), 'required_bytes': required,
            'free_bytes': free, 'reserve_bytes': reserve}


def test_default_remains_one_without_creating_policy(console):
    assert console.store.policy()['max_runs'] == 1
    assert not (console.store.root / 'execution-policy.json').exists()
    reserve(console, '001')
    with pytest.raises(ConsoleError, match='active'):
        reserve(console, '002')


def test_two_updated_cli_and_browser_requests_share_limit(parallel):
    first = reserve(parallel, '001')
    run(parallel, first, 101)
    reserve(parallel, '002', kind='run')
    assert parallel.execution_status()['occupied'] == 2
    with pytest.raises(ConsoleError) as failure:
        reserve(parallel, '003')
    assert failure.value.code == 'BUSY'


def test_atomic_cross_tab_start_race_only_two_admitted(parallel):
    barrier = threading.Barrier(3)
    def start(name):
        barrier.wait()
        try:
            reserve(parallel, name)
            return 'accepted'
        except ConsoleError as exc:
            return exc.code
    with ThreadPoolExecutor(3) as pool:
        answers = list(pool.map(start, ('001', '002', '003')))
    assert answers.count('accepted') == 2 and answers.count('BUSY') == 1
    assert parallel.store.capacity()['occupied'] == 2


@pytest.mark.parametrize('conflict', ['run', 'survey', 'source', 'nested_source', 'workspace', 'output'])
def test_resolved_identity_conflicts(parallel, conflict):
    reserve(parallel, '001')
    fields = {}
    name = '002'
    if conflict == 'run':
        name = '001'
    elif conflict == 'survey':
        fields['survey_id'] = 'ah-026001'
    elif conflict in ('source', 'nested_source'):
        fields['source_dir'] = str(parallel.base_dir / 'sources' / '001') + ('/nested' if conflict == 'nested_source' else '')
    elif conflict == 'workspace':
        fields['source_dir'] = str(parallel.base_dir / 'workspaces' / '001')
    elif conflict == 'output':
        fields['published_path'] = str(parallel.base_dir / 'surveys' / '2026' / 'AH-026001' / 'rgb')
    with pytest.raises(ConsoleError) as failure:
        reserve(parallel, name, **fields)
    assert failure.value.code in ('DUPLICATE_RUN', 'IDENTITY_CONFLICT')


def test_injected_mapped_and_unc_aliases_collide(parallel):
    original = parallel.store.path_identity
    parallel.store.path_identity = lambda value: original(value).replace('/alias-source/', '/sources/')
    reserve(parallel, '001')
    with pytest.raises(ConsoleError, match='overlaps'):
        reserve(parallel, '002', source_dir=str(parallel.base_dir / 'alias-source' / '001'))


@pytest.mark.parametrize('phase', ['active', 'locally_paused'])
def test_remote_project_not_reassigned_after_local_detach(parallel, phase):
    first = reserve(parallel, '001', bindings=[{'project_id': 548}])
    run(parallel, first, 101)
    if phase == 'locally_paused':
        finish(parallel, first)
    with pytest.raises(ConsoleError) as failure:
        reserve(parallel, '002', bindings=[{'project_id': 548}])
    assert failure.value.code == 'REMOTE_IDENTITY_CONFLICT'
    assert remote_key('http://USER:PASS@EXAMPLE.invalid:80/?token=x', 5) == 'http://example.invalid/projects/5'


def test_exclusive_maintenance_blocks_both_directions(parallel):
    first = reserve(parallel, '001')
    with pytest.raises(ConsoleError):
        reserve(parallel, '002', exclusive=True)
    finish(parallel, first)
    maintenance = reserve(parallel, '002', exclusive=True)
    assert parallel.store.status(maintenance.operation_id)['exclusive']
    with pytest.raises(ConsoleError):
        reserve(parallel, '003')


@pytest.mark.parametrize('state', ['requires_recovery', 'unknown_pid', 'children', 'legacy_receipt'])
def test_uncertain_ownership_blocks_even_unused_second_slot(parallel, state):
    first = reserve(parallel, '001')
    run(parallel, first, 101)
    value = parallel.store.status(first.operation_id)
    if state == 'requires_recovery':
        parallel.store.update(first.operation_id, state='requires_recovery')
    elif state == 'unknown_pid':
        parallel.store.processes.live[101] = 'unknown'
    elif state == 'children':
        parallel.store.processes.live.pop(101)
        parallel.store.processes.jobs[value['job_name']] = 1
        parallel.store.update(first.operation_id, state='finished')
    else:
        parallel.store.update(first.operation_id, coordination_version=None)
    assert parallel.store.capacity()['available'] == 0
    with pytest.raises(ConsoleError):
        reserve(parallel, '002')


def test_policy_switch_requires_no_live_owner_or_child(parallel):
    first = reserve(parallel, '001')
    run(parallel, first, 101)
    parallel.store.update(first.operation_id, state='finished')
    with pytest.raises(ConsoleError):
        parallel.store.configure(1)
    finish(parallel, first)
    assert parallel.store.configure(1)['max_runs'] == 1


def test_shared_volume_commitments_prevent_double_admission(parallel):
    reserve(parallel, '001', report={'ok': True, 'volumes': [volume(parallel)]})
    with pytest.raises(ConsoleError) as failure:
        reserve(parallel, '002', report={'ok': True, 'volumes': [volume(parallel)]})
    assert failure.value.code == 'STORAGE_COMMITTED'


def test_disk_consumed_after_preflight_fails_before_work(parallel):
    first = reserve(parallel, '001', report={'ok': True, 'volumes': [volume(parallel)]})
    from types import SimpleNamespace
    with pytest.raises(ConsoleError, match='Storage'):
        first.recheck_storage(report(parallel, '001')['report'] | {'volumes': [volume(parallel)]},
                              disk_usage=lambda _: SimpleNamespace(free=64))
    assert parallel.store.status(first.operation_id)['storage_commitments']['owned-test-volume']['required'] == 60


@pytest.mark.parametrize('resource_name', ['compute', 'bulk_transfer'])
def test_waiting_run_can_pause_without_taking_or_releasing_other_permit(parallel, resource_name):
    first, second = reserve(parallel, '001'), reserve(parallel, '002')
    run(parallel, first, 101)
    run(parallel, second, 102)
    controls = LocalControls(parallel.store, second.operation_id)
    second.check_control = PipelineControl(parallel.base_dir, second.run_id, transport=controls).check_or_raise
    def during_wait(_):
        wait = parallel.store.status(second.operation_id)['resource_wait']
        assert wait['holder_run_id'] == first.run_id
        request_control(parallel.store, second.operation_id, uid(), 'pause')
    second.sleep = during_wait
    with first.resource(resource_name):
        with pytest.raises(RuntimeError, match='PAUSED'):
            with second.resource(resource_name):
                pytest.fail('Paused waiter entered processing')
        assert parallel.store.status(first.operation_id)['resources'] == {resource_name: True}
    assert parallel.store.status(second.operation_id)['resource_wait'] is None
    assert parallel.store.status(first.operation_id)['resources'] == {}


def test_incompatible_nested_resources_fail_without_deadlock(parallel):
    first = reserve(parallel, '001')
    run(parallel, first, 101)
    with first.resource('compute'):
        with first.resource('compute'):
            assert first.held['compute'] == 2
        with pytest.raises(ConsoleError, match='nested'):
            with first.resource('bulk_transfer'):
                pytest.fail('Incompatible nested permit')
    assert first.held == {}


def test_surviving_child_retains_permit_and_blocks_admission(parallel):
    first = reserve(parallel, '001')
    run(parallel, first, 101)
    value = parallel.store.status(first.operation_id)
    with pytest.raises(ConsoleError, match='children'):
        with first.resource('compute'):
            parallel.store.processes.jobs[value['job_name']] = 2
    assert parallel.store.status(first.operation_id)['resources']['compute']
    assert parallel.store.capacity()['blocked']


def test_run_specific_cli_control_does_not_signal_other_run(parallel, capsys):
    first, second = reserve(parallel, '001'), reserve(parallel, '002')
    run(parallel, first, 101)
    run(parallel, second, 102)
    value = control_main(['pause', '--run-id', '002'], store=parallel.store, database=parallel.reader.database)
    assert value['state'] == 'requested'
    assert LocalControls(parallel.store, first.operation_id).poll() is None
    assert LocalControls(parallel.store, second.operation_id).poll() == 'pause'
    assert parallel.operation(second.operation_id)['controls_available']
    assert 'requested' in capsys.readouterr().out


def test_automatic_survey_allocation_claims_unique_directories(parallel):
    first, second = reserve(parallel, '001', survey_id=None), reserve(parallel, '002', survey_id=None)
    root = parallel.base_dir / 'allocated'
    logger = logging.getLogger('tests.allocate')
    a = first.allocate_survey(root, 2026, None, True, False, logger)
    b = second.allocate_survey(root, 2026, None, True, False, logger)
    assert a[0] == 'AH-026001' and b[0] == 'AH-026002'
    assert a[1].is_dir() and b[1].is_dir()
    assert parallel.store.status(first.operation_id)['identity']['survey_id'] == a[0].casefold()


@pytest.mark.parametrize('inside', [False, True])
def test_persistence_error_never_reexecutes_stage(temporary_sqlite_db_path, monkeypatch, inside):
    repo = PipelineRepo(temporary_sqlite_db_path)
    runner = StageRunner(repo, 'commit-failure', logging.getLogger('tests.persistence'))
    effect = Mock()
    if inside:
        def perform():
            effect()
            raise StatePersistenceError('binding commit failed')
    else:
        def perform():
            effect()
            return {'artifact': 'retained'}
        finish = repo.finish_stage_with_status
        def record(**kwargs):
            if kwargs['status'] == 'completed':
                raise sqlite3.OperationalError('database is locked')
            return finish(**kwargs)
        monkeypatch.setattr(repo, 'finish_stage_with_status', record)
    with pytest.raises(StageRequiresRecovery):
        runner.run('work', perform, retry_attempts=3, retry_delay_seconds=0)
    assert effect.call_count == 1
    assert repo.get_latest_stage('commit-failure', 'work')['status'] == 'requires_recovery'


def test_busy_commit_retries_only_commit(monkeypatch):
    connection = Mock(in_transaction=True)
    error = sqlite3.OperationalError('busy')
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    connection.commit.side_effect = [error, None]
    monkeypatch.setattr('shared.db.repo.time.sleep', lambda _: None)
    _CommitRetry(connection).commit()
    assert connection.commit.call_count == 2
    connection.execute.assert_not_called()


def test_repository_transactions_are_serialized_and_isolated(temporary_sqlite_db_path):
    lock, observed = threading.RLock(), []
    @contextmanager
    def gate():
        with lock:
            observed.append(threading.get_ident())
            yield
    def work(name):
        repo = PipelineRepo(temporary_sqlite_db_path, transaction_gate=gate)
        repo.create_run(name)
        for _ in range(5):
            stage = repo.start_stage(name, 'test')
            repo.finish_stage_with_status(stage_id=stage, status='completed', runtime_seconds=0,
                                          output={'owner': name}, error_message=None)
        return repo.get_latest_stage(name, 'test')
    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(work, ('first', 'second')))
    assert {row['run_id'] for row in rows} == {'first', 'second'}
    assert all(row['status'] == 'completed' for row in rows) and len(observed) >= 20


def test_transaction_lock_failure_is_not_a_retryable_processing_error(temporary_sqlite_db_path):
    @contextmanager
    def unavailable():
        raise ConsoleError('lock timeout', 'BUSY')
        yield
    with pytest.raises(StatePersistenceError) as failure:
        PipelineRepo(temporary_sqlite_db_path, transaction_gate=unavailable)
    assert isinstance(failure.value.__cause__, ConsoleError)


def test_concurrent_auto_allocation_is_serialized(parallel):
    first, second = reserve(parallel, '001', survey_id=None), reserve(parallel, '002', survey_id=None)
    root = parallel.base_dir / 'concurrent-surveys'
    def allocate(execution):
        return execution.allocate_survey(root, 2026, None, True, False, logging.getLogger('tests.auto'))[0]
    with ThreadPoolExecutor(2) as pool:
        assert set(pool.map(allocate, (first, second))) == {'AH-026001', 'AH-026002'}


def test_legacy_running_database_blocks_policy_change(console):
    from tests.test_pipeline_console import seed
    seed(console, status='running')
    with pytest.raises(ConsoleError) as failure:
        console.store.configure(2, database=console.reader.database)
    assert failure.value.code == 'LEGACY_RUNNING'
    assert console.store.policy()['max_runs'] == 1


def test_corrupt_receipt_blocks_new_admission(parallel):
    execution = reserve(parallel, '001')
    (parallel.store.directory(execution.operation_id) / 'status.json').write_text('{incomplete', encoding='utf-8')
    with pytest.raises(ConsoleError) as failure:
        reserve(parallel, '002')
    assert failure.value.code == 'INVALID_JOURNAL'


def test_partial_dispatch_after_acceptance_never_replayed(parallel):
    operation = parallel.preflight(uid(), {'survey': 'source'})
    parallel.store.update(operation['operation_id'], state='requires_recovery')
    calls = parallel.popen.call_count
    assert parallel.execution_status()['available'] == 0
    with pytest.raises(ConsoleError):
        parallel.preflight(uid(), {'survey': 'other'})
    assert parallel.popen.call_count == calls


def test_resume_same_run_after_pid_reuse_and_no_children(parallel):
    first = reserve(parallel, '001')
    run(parallel, first, 101)
    finish(parallel, first)
    parallel.store.processes.live[101] = 'different-birth'
    reserve(parallel, '001')
    assert parallel.store.capacity()['occupied'] == 1


def test_webodm_mutation_failure_blocks_replay_and_local_only_reconcile(parallel):
    from shared.execution import remote_mutation
    first = reserve(parallel, '001')
    effects = Mock()
    class Remote:
        execution = first
        @remote_mutation
        def create(self):
            self._remote_mutation_started = True
            effects()
            raise TimeoutError('accepted but reply lost')
    with pytest.raises(StageRequiresRecovery):
        Remote().create()
    effects.assert_called_once()
    assert parallel.store.status(first.operation_id)['state'] == 'requires_recovery'
    with pytest.raises(ConsoleError) as failure:
        parallel.store.reconcile(first.operation_id, 'RECONCILE ' + first.operation_id)
    assert failure.value.code == 'REMOTE_OUTCOME_UNKNOWN'


def test_processing_boundaries_release_transfer_before_compute(parallel):
    from shared.execution import resource, transfer
    first = reserve(parallel, '001')
    run(parallel, first, 101)
    observed = []
    class Processor:
        execution = first
        @resource('compute')
        def compute(self):
            observed.append(dict(first.held))
        def export(self):
            transfer(self, lambda: observed.append(dict(first.held)))
            self.compute()
    Processor().export()
    assert observed == [{'bulk_transfer': 1}, {'compute': 1}]
    assert parallel.store.status(first.operation_id)['resources'] == {}


def test_new_log_projection_keeps_runs_separate(parallel):
    from tests.test_pipeline_console import seed
    seed(parallel, 'first')
    seed(parallel, 'second')
    for run_id in ('first', 'second'):
        directory = parallel.reader.logs_root / 'runs' / run_id / 'attempt'
        directory.mkdir(parents=True)
        (directory / 'pipeline.log').write_text(f'12:00 | INFO | rgb.pipeline | {run_id} | stage | owner={run_id}\n', encoding='utf-8')
    messages = parallel.reader.logs('first')['items']
    assert len(messages) == 1 and 'owner=first' in messages[0]['message']
    assert all('owner=second' not in row['message'] for row in messages)


def test_mocked_windows_map_expansion_and_unverifiable_alias(monkeypatch):
    import ctypes
    from types import SimpleNamespace
    import operator_ui.identities as identities
    class LiteralPath:
        def __init__(self, value): self.value = value
        def expanduser(self): return self
        def resolve(self, **_): return self
        def __str__(self): return self.value
    kernel = SimpleNamespace(GetDriveTypeW=Mock(return_value=4))
    keep = []
    def expand(_path, _kind, buffer, _size):
        from ctypes import wintypes
        value = ctypes.create_unicode_buffer('\\\\server\\share\\survey')
        keep.append(value)
        ctypes.cast(buffer, ctypes.POINTER(wintypes.LPWSTR))[0] = ctypes.cast(value, wintypes.LPWSTR)
        return 0
    network = SimpleNamespace(WNetGetUniversalNameW=Mock(side_effect=expand))
    monkeypatch.setattr(identities, 'Path', LiteralPath)
    monkeypatch.setattr(identities, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **_: kernel if name == 'kernel32' else network)
    assert identities.canonical_path('Z:\\survey') == '//server/share/survey'
    assert identities.canonical_path('\\\\?\\UNC\\server\\share\\survey') == '//server/share/survey'
    network.WNetGetUniversalNameW.side_effect = None
    network.WNetGetUniversalNameW.return_value = 5
    with pytest.raises(ValueError, match='identity'):
        identities.canonical_path('Z:\\survey')


def test_returned_task_identity_survives_missing_binding(parallel):
    first = reserve(parallel, '001')
    first.created(548, 'exact-returned-task')
    pending = parallel.store.status(first.operation_id)['pending_creation']
    assert read_json(parallel.store.directory(first.operation_id) / pending['receipt'])['task_id'] == 'exact-returned-task'
    finish(parallel, first)
    assert parallel.store.capacity()['blocked']
    with pytest.raises(ConsoleError, match='bound'):
        parallel.store.reconcile(first.operation_id, 'RECONCILE ' + first.operation_id)
    first.binding_committed(548, 'different-task')
    assert parallel.store.status(first.operation_id)['pending_creation']
    first.binding_committed(548, 'exact-returned-task')
    assert parallel.store.status(first.operation_id)['pending_creation'] is None
    assert parallel.store.capacity()['available'] == 2


def test_quality_restart_escalation_is_atomic_and_exclusive(parallel):
    from shared.stage_runner import StageFailedWithOutput
    first, second = reserve(parallel, '001'), reserve(parallel, '002')
    with pytest.raises(StageFailedWithOutput, match='exclusive'):
        first.require_exclusive()
    assert not parallel.store.status(first.operation_id)['exclusive']
    finish(parallel, second)
    first.require_exclusive()
    assert parallel.store.status(first.operation_id)['exclusive']
    with pytest.raises(ConsoleError):
        reserve(parallel, '003')


def test_loopback_aliases_cannot_rebind_a_remote_project():
    assert remote_key('http://localhost:8000', 548) == remote_key('http://127.0.0.1:8000', 548)
    assert remote_key('http://[::1]:8000', 548) == remote_key('http://127.0.0.1:8000', 548)


def test_physical_volume_grouping_happens_before_estimates_are_merged(tmp_path):
    from types import SimpleNamespace
    from shared.storage_preflight import StorageRequirement, inspect_storage_requirements
    left, right = tmp_path / 'left', tmp_path / 'right'
    left.mkdir()
    right.mkdir()
    requirements = [StorageRequirement('left', left, 60), StorageRequirement('right', right, 60)]
    usage = lambda _: SimpleNamespace(total=100, used=0, free=100)
    separate = inspect_storage_requirements(requirements, min_free_gb=0, min_free_percent=0,
        disk_usage=usage, volume_key=lambda path: path.name)
    assert separate['ok'] and len(separate['volumes']) == 2
    shared = inspect_storage_requirements(requirements, min_free_gb=0, min_free_percent=0,
        disk_usage=usage, volume_key=lambda _: 'same-physical-volume')
    assert not shared['ok'] and shared['volumes'][0]['required_bytes'] == 120


def test_allocation_retry_reuses_same_claim_instead_of_allocating_again(parallel):
    first = reserve(parallel, '001', survey_id=None)
    root = parallel.base_dir / 'retried-survey'
    logger = logging.getLogger('tests.retry-allocation')
    one = first.allocate_survey(root, 2026, None, True, False, logger)
    two = first.allocate_survey(root, 2026, None, True, False, logger)
    assert one == two
    assert len(list((root / '2026').iterdir())) == 1
    assert read_json(parallel.store.directory(first.operation_id) / 'survey-allocation.json')['survey_id'] == one[0]


def test_allocation_missing_marker_refuses_to_invent_ownership(parallel):
    first = reserve(parallel, '001', survey_id=None)
    root = parallel.base_dir / 'partial-survey'
    logger = logging.getLogger('tests.partial-allocation')
    claim = {'root': parallel.store.path_identity(root), 'year': 2026, 'use_year': True,
             'override': None, 'survey_id': 'AH-026001', 'path': str(root / '2026' / 'AH-026001' / 'rgb')}
    Path(claim['path']).mkdir(parents=True)
    parallel.store.update(first.operation_id, allocated_survey=claim)
    with pytest.raises(StageRequiresRecovery, match='ownership'):
        first.allocate_survey(root, 2026, None, True, False, logger)
    assert len(list((root / '2026').iterdir())) == 1


def test_resume_before_database_survey_attach_keeps_original_allocation(parallel):
    first = reserve(parallel, '001', survey_id=None)
    root = parallel.base_dir / 'surveys'
    logger = logging.getLogger('tests.resume-claim')
    original = first.allocate_survey(root, 2026, None, True, False, logger)
    finish(parallel, first)
    resumed = reserve(parallel, '001', survey_id=None)
    resumed.bind(report(parallel, '001', survey_id=None))
    assert parallel.store.status(resumed.operation_id)['identity']['survey_id'] == original[0].casefold()
    assert resumed.allocate_survey(root, 2026, None, True, False, logger) == original
    assert len(list((root / '2026').iterdir())) == 1


def test_pre_dispatch_validation_is_not_reported_as_unknown_remote_effect(parallel):
    from shared.execution import remote_mutation
    first = reserve(parallel, '001')
    class Remote:
        execution = first
        @remote_mutation
        def create(self):
            raise ValueError('invalid images before POST')
    with pytest.raises(ValueError, match='before POST'):
        Remote().create()
    assert not (parallel.store.directory(first.operation_id) / 'remote-mutation.json').exists()
