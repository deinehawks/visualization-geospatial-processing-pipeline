"""Three-slot extension with temporary state and fake process/service boundaries."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path

import pytest

from operator_ui.controls import LocalControls, request_control
from operator_ui.storage import ConsoleError, atomic_json
from shared.pipeline_control import PipelineControl
from shared.stage_runner import StageFailedWithOutput
from tests.test_pipeline_console import console, uid
from tests.test_two_run_execution import reserve, run, finish, volume
from tools.pipeline_control import main as control_main


@pytest.fixture
def three(console):
    console.store.path_identity = lambda path: str(Path(path).resolve()).replace('\\', '/').rstrip('/').casefold()
    console.store.volume_identity = lambda _: 'owned-test-volume'
    console.store.configure(3, database=console.reader.database)
    return console


def test_cli_configures_three_and_can_roll_back_while_idle(console):
    before = console.reader.database.read_bytes()
    result = control_main(['configure', '--max-runs', '3'], store=console.store, database=console.reader.database)
    assert result['max_runs'] == 3 and result['available'] == 3 and not result['blocked']
    assert console.store.policy() == {'version': 1, 'max_runs': 3}
    result = control_main(['configure', '--max-runs', '2'], store=console.store, database=console.reader.database)
    assert result['max_runs'] == 2 and result['available'] == 2
    assert console.reader.database.read_bytes() == before
    console.popen.assert_not_called()


@pytest.mark.parametrize('value', [0, 4, -1, True, False, None, '3', 3.0, float('inf')])
def test_invalid_configure_values_leave_policy_unchanged(three, value):
    path = three.store.root / 'execution-policy.json'
    before = path.read_bytes()
    with pytest.raises(ConsoleError) as failure:
        three.store.configure(value, database=three.reader.database)
    assert failure.value.code == 'INVALID_INPUT'
    assert path.read_bytes() == before


@pytest.mark.parametrize('value', [0, 4, True, None, '3', 3.0])
def test_invalid_saved_policy_still_fails_closed(three, value):
    atomic_json(three.store.root / 'execution-policy.json', {'version': 1, 'max_runs': value})
    with pytest.raises(ConsoleError) as failure:
        three.store.capacity()
    assert failure.value.code == 'INVALID_POLICY'


def test_three_slots_still_require_resolved_run_identity(three):
    operation_id = uid()
    with pytest.raises(ConsoleError) as failure:
        three.store.reserve(operation_id, {'kind': 'cli', 'run_id': 'unresolved'})
    assert failure.value.code == 'IDENTITY_REQUIRED'
    assert not three.store.directory(operation_id).exists()


def test_pending_preflight_consumes_third_slot_without_a_worker_queue(three):
    reserve(three, '001')
    reserve(three, '002', kind='run')
    three.store.reserve(uid(), {'kind': 'preflight', 'run_id': '003'})
    assert three.store.capacity()['occupied'] == 3
    with pytest.raises(ConsoleError) as failure:
        reserve(three, '004')
    assert failure.value.code == 'BUSY'
    three.popen.assert_not_called()


@pytest.mark.parametrize('owner', ['001', '002'])
@pytest.mark.parametrize('conflict', ['run', 'survey', 'source', 'workspace', 'output', 'remote'])
def test_third_run_checks_identity_against_each_existing_owner(three, owner, conflict):
    projects = {'001': 548, '002': 549}
    for name in projects:
        reserve(three, name, bindings=[{'project_id': projects[name]}])
    fields, name = {}, '003'
    if conflict == 'run':
        name = owner
    elif conflict == 'survey':
        fields['survey_id'] = 'ah-026' + owner
    elif conflict == 'source':
        fields['source_dir'] = str(three.base_dir / 'sources' / owner / 'nested')
    elif conflict == 'workspace':
        fields['source_dir'] = str(three.base_dir / 'workspaces' / owner)
    elif conflict == 'output':
        fields['published_path'] = str(three.base_dir / 'surveys' / '2026' / ('AH-026' + owner) / 'rgb')
    else:
        fields['bindings'] = [{'project_id': projects[owner]}]
    with pytest.raises(ConsoleError) as failure:
        reserve(three, name, **fields)
    assert failure.value.code in ('DUPLICATE_RUN', 'IDENTITY_CONFLICT', 'REMOTE_IDENTITY_CONFLICT')
    assert three.store.capacity()['occupied'] == 2


def test_three_run_storage_commitments_are_summed(three):
    for name in ('001', '002'):
        reserve(three, name, report={'ok': True, 'volumes': [volume(three, required=35)]})
    assert three.store.capacity()['available'] == 1
    with pytest.raises(ConsoleError) as failure:
        reserve(three, '003', report={'ok': True, 'volumes': [volume(three, required=30)]})
    assert failure.value.code == 'STORAGE_COMMITTED'
    assert three.store.capacity()['occupied'] == 2


@pytest.mark.parametrize('target', [1, 2, 3])
def test_policy_change_cannot_reclassify_live_invocations(three, target):
    first = reserve(three, '001')
    run(three, first, 101)
    path = three.store.root / 'execution-policy.json'
    before = path.read_bytes()
    with pytest.raises(ConsoleError) as failure:
        three.store.configure(target, database=three.reader.database)
    assert failure.value.code == 'BUSY'
    assert path.read_bytes() == before


def test_quality_restart_escalation_checks_both_other_runs(three):
    first, second, third = (reserve(three, name) for name in ('001', '002', '003'))
    finish(three, second)
    with pytest.raises(StageFailedWithOutput, match='exclusive'):
        first.require_exclusive()
    assert not three.store.status(first.operation_id)['exclusive']
    finish(three, third)
    first.require_exclusive()
    assert three.store.capacity()['blocked'] and three.store.capacity()['available'] == 0
    with pytest.raises(ConsoleError) as failure:
        reserve(three, '004')
    assert failure.value.code == 'BUSY'


def test_targeted_control_is_independent_for_all_three_runs(three):
    executions = [reserve(three, name) for name in ('001', '002', '003')]
    for pid, execution in enumerate(executions, 101):
        run(three, execution, pid)
    control_main(['pause', '--run-id', '003'], store=three.store, database=three.reader.database)
    assert [LocalControls(three.store, execution.operation_id).poll() for execution in executions] == [None, None, 'pause']


@pytest.mark.parametrize('resource_name', ['compute', 'bulk_transfer'])
def test_two_waiters_never_share_a_permit_and_can_pause(three, resource_name):
    executions = [reserve(three, name) for name in ('001', '002', '003')]
    for pid, execution in enumerate(executions, 101):
        run(three, execution, pid)
    first = executions[0]
    with first.resource(resource_name):
        for waiter in executions[1:]:
            controls = LocalControls(three.store, waiter.operation_id)
            waiter.check_control = PipelineControl(three.base_dir, waiter.run_id, transport=controls).check_or_raise
            def pause_waiter(_):
                wait = three.store.status(waiter.operation_id)['resource_wait']
                assert wait['holder_operation_id'] == first.operation_id
                request_control(three.store, waiter.operation_id, uid(), 'pause')
            waiter.sleep = pause_waiter
            with pytest.raises(RuntimeError, match='PAUSED'):
                with waiter.resource(resource_name):
                    pytest.fail('Paused waiter entered processing')
            assert three.store.status(first.operation_id)['resources'] == {resource_name: True}
    assert all(three.store.status(execution.operation_id)['resources'] == {} for execution in executions)


def test_three_automatic_survey_allocations_claim_distinct_folders(three):
    executions = [reserve(three, name, survey_id=None) for name in ('001', '002', '003')]
    root = three.base_dir / 'allocated-surveys'
    def allocate(execution):
        return execution.allocate_survey(root, 2026, None, True, False, logging.getLogger('tests.three-allocation'))[0]
    with ThreadPoolExecutor(3) as pool:
        assert set(pool.map(allocate, executions)) == {'AH-026001', 'AH-026002', 'AH-026003'}
    assert len(list((root / '2026').iterdir())) == 3


def test_three_slot_cli_never_enables_global_hotkeys(three, monkeypatch, tmp_path):
    import main as rgb_main
    from tests.test_main_publication_activation_cli import _prepare_cli, FakeRGBPipeline
    _prepare_cli(monkeypatch, tmp_path, ['--survey', 'source', '--run-id', '001'])
    monkeypatch.chdir(tmp_path)
    execution = reserve(three, '001')
    assert execution.two_runs is True
    @contextmanager
    def guard(*_):
        yield execution
    rgb_main.main(guard_factory=guard, pipeline_options={'enable_hotkeys': True})
    assert FakeRGBPipeline.instances[0].init_kwargs['enable_hotkeys'] is False
