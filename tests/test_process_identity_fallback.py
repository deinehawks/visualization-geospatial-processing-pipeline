"""PID reuse evidence: fake CIM/native boundaries, no real process inspection."""
import json
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import operator_ui.process_identity as module
from operator_ui.storage import ConsoleError, WindowsProcesses, atomic_json
from operator_ui.controls import request_control
from tests.test_pipeline_console import console, uid


PID = 1636
OLD = '100000000000000007'
NEW = '100000000050000000'
NOW = int(NEW) + 10 * module.TICKS_PER_SECOND


def payload(*, pid=PID, birth=NEW):
    return {'version': 1, 'samples': [{'pid': pid, 'birth': birth}, {'pid': pid, 'birth': birth}]}


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
    runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(payload())))
    return {'identity': Mock(return_value='unknown'), 'runner': runner,
            'directory': Mock(return_value=r'C:\Windows\System32'), 'clock': lambda: NOW}


def test_native_identity_fast_path_does_not_run_cim(probe):
    for value in (None, OLD, NEW):
        probe['identity'].return_value = value
        assert module.owner_identity(PID, OLD, **probe) == value
    probe['runner'].assert_not_called()
    probe['directory'].assert_not_called()


def test_only_stable_clearly_newer_process_proves_pid_reuse(probe, caplog):
    with caplog.at_level('INFO', logger=module.__name__):
        assert module.owner_identity(PID, OLD, **probe) == NEW
    assert 'event=owner_pid_reuse_verified' in caplog.text
    assert probe['identity'].call_count == 2
    assert probe['runner'].call_count == 1


def test_reported_october_pid_reuse_case(probe):
    recorded = '134372383601303920'
    current = '134372879930000000'
    probe['runner'].return_value.stdout = json.dumps(payload(birth=current))
    probe['clock'] = lambda: int(current) + module.TICKS_PER_SECOND
    assert module.owner_identity(PID, recorded, **probe) == current


@pytest.mark.parametrize('distance', [-20_000_000, -1, 0, 1, 3, 9, 1000, 9_999_999, 10_000_000])
def test_coarse_or_earlier_cim_timestamp_never_confirms_or_retires_owner(probe, distance):
    value = str(int(OLD) + distance)
    probe['runner'].return_value.stdout = json.dumps(payload(birth=value))
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'


def test_future_cim_creation_fails_closed(probe):
    probe['runner'].return_value.stdout = json.dumps(payload(birth=str(NOW + 1)))
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'


@pytest.mark.parametrize('last', [None, OLD, NEW])
def test_final_native_observation_always_wins(probe, last):
    probe['identity'].side_effect = ['unknown', last]
    assert module.owner_identity(PID, OLD, **probe) == last


@pytest.mark.parametrize('birth', [None, '', 'unknown', 'birth', 123, True, '01', '-1', '0', str(2 ** 64)])
def test_invalid_persisted_birth_cannot_invoke_fallback(probe, birth):
    assert module.owner_identity(PID, birth, **probe) == 'unknown'
    probe['runner'].assert_not_called()


@pytest.mark.parametrize('pid', [True, 0, -1, 2 ** 32, '1636', '1636;Stop-Process -Id 1'])
def test_invalid_pid_never_reaches_native_or_powershell(probe, pid):
    assert module.owner_identity(pid, OLD, **probe) == 'unknown'
    probe['identity'].assert_not_called()
    probe['runner'].assert_not_called()


@pytest.mark.parametrize('value', [
    None, [], {}, {'version': True, 'samples': []},
    {'version': 1, 'samples': []}, {'version': 1, 'samples': [payload()['samples'][0]]},
    {'version': 1, 'samples': [payload()['samples'][0]] * 3},
    {'version': 2, 'samples': payload()['samples']},
    {'version': 1, 'samples': payload()['samples'], 'unexpected': 'field'},
    {'version': 1, 'samples': [{'pid': PID + 1, 'birth': NEW}] * 2},
    {'version': 1, 'samples': [{'pid': True, 'birth': NEW}] * 2},
    {'version': 1, 'samples': [{'pid': PID, 'birth': None}] * 2},
    {'version': 1, 'samples': [{'pid': PID, 'birth': int(NEW)}] * 2},
    {'version': 1, 'samples': [{'pid': PID, 'birth': str(2 ** 64)}] * 2},
    {'version': 1, 'samples': [None, None]},
    {'version': 1, 'samples': [{}, {}]},
    {'version': 1, 'samples': [{'pid': PID}] * 2},
    {'version': 1, 'samples': [{'pid': PID, 'birth': NEW, 'extra': True}] * 2},
    {'version': 1, 'samples': [{'pid': PID, 'birth': NEW}, {'pid': PID, 'birth': OLD}]},
])
def test_missing_changed_or_malformed_cim_evidence_is_unknown(probe, value):
    probe['runner'].return_value.stdout = json.dumps(value)
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'


@pytest.mark.parametrize('output', ['', 'private-not-json', 'x' * 4097,
    '{"version":1,"version":1,"samples":[]}',
    '{"version":1,"samples":[{"pid":1636,"birth":"1","birth":"2"}]}'])
def test_invalid_oversized_or_duplicate_output_not_logged(probe, output, caplog):
    probe['runner'].return_value.stdout = output
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'
    assert output not in caplog.text if output else 'cim_unavailable' in caplog.text


@pytest.mark.parametrize('error', [OSError('private-spawn-error'),
    subprocess.TimeoutExpired('private-query', 5), UnicodeError('private-encoding-error')])
def test_probe_errors_fail_closed_without_disclosing_output(probe, caplog, error):
    probe['runner'].side_effect = error
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'
    assert 'private-' not in caplog.text
    with pytest.raises(module.IdentityProbeError) as failure:
        module.cim_creation(PID, runner=probe['runner'], directory=probe['directory'])
    assert failure.value.__cause__ is error


def test_nonzero_exit_not_interpreted_as_absence(probe):
    probe['runner'].return_value.returncode = 1
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'


@pytest.mark.parametrize('output', [None, 123, b'not-decoded-json'])
def test_non_text_output_fails_closed(probe, output):
    probe['runner'].return_value.stdout = output
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'


def test_native_directory_failure_preserves_cause_and_never_spawns(probe):
    cause = ConsoleError('Unverified system directory')
    probe['directory'].side_effect = cause
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'
    probe['runner'].assert_not_called()
    with pytest.raises(module.IdentityProbeError) as failure:
        module.cim_creation(PID, runner=probe['runner'], directory=probe['directory'])
    assert failure.value.__cause__ is cause


def test_cim_boundary_is_fixed_local_scoped_noninteractive_and_read_only(probe):
    assert module.owner_identity(PID, OLD, **probe) == NEW
    args, options = probe['runner'].call_args
    command = args[0]
    assert command[0] == r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    assert command[1:5] == ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command']
    script = command[5]
    assert "-Filter 'ProcessId = 1636'" in script
    assert "-Namespace 'root/cimv2'" in script and '1..2' in script
    assert 'CreationDate.ToFileTimeUtc()' in script
    assert '[DateTimeKind]::Unspecified' in script
    assert all(text not in script for text in ('Stop-Process', 'Terminate', 'Remove-', 'CommandLine', '-ComputerName'))
    assert options['shell'] is False and options['stdin'] == subprocess.DEVNULL
    assert options['timeout'] == 5 and options['creationflags'] == 0x08000000
    assert options['cwd'] == r'C:\Windows\System32'
    assert options['encoding'] == 'utf-8-sig' and options['errors'] == 'strict'


@pytest.mark.parametrize('directory', ['', 'powershell.exe', r'\\server\share', r'C:\Windows\..\Temp', 'C:\\bad\npath'])
def test_untrusted_inspector_path_fails_closed(probe, directory):
    probe['directory'].return_value = directory
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'
    probe['runner'].assert_not_called()


def test_unsupported_host_never_starts_probe(monkeypatch, probe):
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='posix'))
    assert module.owner_identity(PID, OLD, **probe) == 'unknown'
    probe['runner'].assert_not_called()


def retired(console, monkeypatch, probe, *, state='finished', children=0):
    operation = uid()
    console.store.reserve(operation, {'kind': 'cli', 'run_id': 'retired-run'})
    value = console.store.update(operation, coordination_version=2, state=state,
        pid=PID, birth=OLD, exclusive=True, resources={})
    adapter = WindowsProcesses(cim_runner=probe['runner'], system_directory=probe['directory'])
    monkeypatch.setattr(adapter, 'identity', probe['identity'])
    monkeypatch.setattr(adapter, 'job_active', Mock(return_value=children))
    console.store.processes = adapter
    return operation, value


def test_finished_reused_pid_and_empty_job_no_longer_blocks_capacity(console, monkeypatch, probe):
    operation, value = retired(console, monkeypatch, probe)
    path = console.store.directory(operation) / 'status.json'
    before = path.read_bytes()
    status = console.execution_status()
    assert status['occupied'] == 0 and status['available'] == 1 and not status['blocked']
    assert console.store.occupied() is None
    detail = console.operation(operation)
    assert not detail['resource_busy'] and not detail['owner_verified'] and not detail['controls_available']
    assert path.read_bytes() == before
    assert console.store.status(operation) == value
    accepted, created = console.store.reserve(uid(), {'kind': 'cli', 'run_id': 'different-run'})
    assert created and accepted['state'] == 'accepted'


def test_reused_finished_owner_does_not_consume_second_run_slot(console, monkeypatch, probe):
    operation, _ = retired(console, monkeypatch, probe)
    before = (console.store.directory(operation) / 'status.json').read_bytes()
    result = console.store.configure(2, database=console.reader.database)
    assert result['occupied'] == 0 and result['available'] == 2 and not result['blocked']
    assert (console.store.directory(operation) / 'status.json').read_bytes() == before


@pytest.mark.parametrize('children', [1, 2, None])
def test_descendants_or_unknown_job_still_block_and_cannot_reconcile(console, monkeypatch, probe, children):
    operation, _ = retired(console, monkeypatch, probe, children=children)
    assert console.execution_status()['blocked']
    with pytest.raises(ConsoleError, match='descendants'):
        console.store.reconcile(operation, 'RECONCILE ' + operation)
    assert console.store.status(operation)['state'] == 'finished'


@pytest.mark.parametrize('state', ['running', 'accepted', 'requires_recovery'])
def test_nonterminal_states_still_require_explicit_reconciliation(console, monkeypatch, probe, state):
    operation, _ = retired(console, monkeypatch, probe, state=state)
    assert console.execution_status()['blocked']
    result = console.store.reconcile(operation, 'RECONCILE ' + operation)
    assert result['state'] == 'resolved'
    assert not console.execution_status()['blocked']
    assert list(console.store.directory(operation).glob('reconciled-*.json'))


@pytest.mark.parametrize('name', ['restart-intent.json', 'remote-mutation.json'])
def test_uncertain_remote_outcome_is_never_cleared(console, monkeypatch, probe, name):
    operation, _ = retired(console, monkeypatch, probe)
    atomic_json(console.store.directory(operation) / name, {'resolved': False})
    assert console.execution_status()['blocked']
    with pytest.raises(ConsoleError, match='separate reconciliation'):
        console.store.reconcile(operation, 'RECONCILE ' + operation)
    assert console.store.status(operation)['state'] == 'finished'


def test_pending_creation_still_blocks(console, monkeypatch, probe):
    operation, _ = retired(console, monkeypatch, probe)
    console.store.update(operation, pending_creation={'operation': 'task4'})
    assert console.execution_status()['blocked']
    with pytest.raises(ConsoleError, match='binding repair'):
        console.store.reconcile(operation, 'RECONCILE ' + operation)


def test_reused_pid_cannot_receive_controls_or_be_adopted(console, monkeypatch, probe):
    operation, _ = retired(console, monkeypatch, probe, state='running')
    with pytest.raises(ConsoleError, match='does not accept controls'):
        request_control(console.store, operation, uid(), 'pause')
    detail = console.operation(operation)
    assert detail['observed_state'] == 'requires_reconciliation'
    assert not detail['owner_verified'] and not detail['controls_available']
    assert not (console.store.directory(operation) / 'controls').exists()


def test_ambiguous_cim_identity_keeps_finished_owner_blocking(console, monkeypatch, probe):
    operation, _ = retired(console, monkeypatch, probe)
    probe['runner'].return_value.stdout = json.dumps(payload(birth=str(int(OLD) - 7)))
    assert console.execution_status()['blocked']
    with pytest.raises(ConsoleError, match='cannot be verified'):
        console.store.reconcile(operation, 'RECONCILE ' + operation)


def test_identity_is_not_cached_between_checks(console, monkeypatch, probe):
    operation, _ = retired(console, monkeypatch, probe)
    assert not console.execution_status()['blocked']
    probe['runner'].return_value.returncode = 1
    assert console.execution_status()['blocked']
    assert console.store.status(operation)['state'] == 'finished'


def test_import_does_not_probe_native_or_cim(monkeypatch):
    def prohibited(*args, **kwargs):
        pytest.fail('Import must not inspect processes or start helpers')
    monkeypatch.setattr(subprocess, 'run', prohibited)
    import ctypes
    monkeypatch.setattr(ctypes, 'WinDLL', prohibited, raising=False)
    spec = importlib.util.spec_from_file_location('safe_owner_identity_import', Path(module.__file__))
    imported = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imported)
    assert callable(imported.owner_identity)
