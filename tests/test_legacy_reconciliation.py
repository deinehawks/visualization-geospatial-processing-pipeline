"""Legacy reconciliation uses owned temporary SQLite and fake host inventories."""
import ctypes
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_pipeline_console import console, seed, uid
from operator_ui.legacy_reconciliation import process_blockers, reconcile_legacy, windows_inventory
from operator_ui.storage import ConsoleError
from tools.pipeline_control import main


def inventory():
    return [{'pid': os.getpid(), 'name': 'python.exe'}, {'pid': 10001, 'name': 'explorer.exe'}]


def preview(console, run_id='old'):
    return reconcile_legacy(console.store, console.reader.database, run_id, inventory=inventory)


def apply(console, run_id='old', **kwargs):
    phrase = preview(console, run_id)['confirmation']
    return reconcile_legacy(console.store, console.reader.database, run_id, confirmation=phrase,
                            inventory=kwargs.get('inventory', inventory))


def test_preview_is_read_only_and_reports_exact_evidence(console):
    seed(console, 'old', 'running', mode='both')
    before = console.reader.database.read_bytes()
    result = preview(console)
    assert not result['applied'] and not result['database_changed']
    assert result['latest_stage'] == 'webodm' and result['remote_status'] == 'unverified'
    assert result['confirmation'].startswith('RECONCILE LEGACY old ')
    assert result['confirmation'].endswith(' NO LOCAL PIPELINE IS RUNNING')
    assert not console.store.root.exists()
    assert console.reader.database.read_bytes() == before


def test_receipt_clears_only_exact_legacy_block_not_database_state(console):
    seed(console, 'old', 'running', mode='both')
    seed(console, 'other', 'running')
    before = console.reader.database.read_bytes()
    apply(console)
    assert console.legacy_running() == ['other']
    assert console.execution_status()['blocked']
    apply(console, 'other')
    state = console.execution_status()
    assert not state['blocked'] and state['available'] == 1
    assert len(state['legacy_reconciled']) == 2
    console.store.verify_legacy(console.reader.database)
    assert console.reader.database.read_bytes() == before
    assert console.reader.detail('old')['run']['status'] == 'running'
    console.store.configure(2, database=console.reader.database)
    assert console.store.policy()['max_runs'] == 2


def test_cli_and_browser_accept_receipts_with_original_identity(console):
    seed(console, 'old', 'running', mode='task4')
    apply(console)
    console.store.verify_legacy(console.reader.database)
    normalized = console.normalize({'action': 'resume', 'run_id': 'old'})
    assert normalized['run_id'] == 'old' and normalized['mode'] == 'task4'
    result = console.preflight(uid(), {'action': 'resume', 'run_id': 'old'})
    assert result['kind'] == 'preflight'


def test_old_database_without_binding_table_is_compatible(console):
    seed(console, 'old', 'running')
    with sqlite3.connect(console.reader.database) as connection:
        connection.execute('DROP TABLE webodm_tasks')  # pytest-owned DB only
    apply(console)
    assert console.legacy_running() == []


def test_receipts_do_not_expose_source_paths_or_task_options(console):
    seed(console, 'old', 'running')
    with sqlite3.connect(console.reader.database) as connection:
        connection.execute("INSERT INTO webodm_tasks(run_id,options_json) VALUES ('old',?)", ('{"token":"PRIVATE-CREDENTIAL"}',))
    result = apply(console)
    text = next(console.store.root.glob('legacy-reconciled-*.json')).read_text(encoding='utf-8')
    assert 'PRIVATE-CREDENTIAL' not in text and str(console.base_dir / 'source') not in text
    assert result['remote_status'] == 'unverified'


@pytest.mark.parametrize('table', ['runs', 'stages', 'webodm_tasks'])
def test_any_new_run_stage_or_remote_evidence_invalidates_receipt(console, table):
    seed(console, 'old', 'running', mode='both')
    apply(console)
    with sqlite3.connect(console.reader.database) as connection:
        if table == 'runs':
            connection.execute("UPDATE runs SET year=2025 WHERE run_id='old'")
        elif table == 'stages':
            connection.execute("INSERT INTO stages(run_id,stage_name,status) VALUES ('old','qgis','running')")
        else:
            connection.execute("INSERT INTO webodm_tasks(run_id,operation_key,task_id) VALUES ('old','task2','new-uuid')")
    assert console.legacy_running() == ['old']
    with pytest.raises(ConsoleError) as failure:
        console.store.verify_legacy(console.reader.database)
    assert failure.value.code == 'LEGACY_RUNNING'


@pytest.mark.parametrize('confirmation', ['', 'RECONCILE LEGACY old', 'something else'])
def test_wrong_confirmation_writes_nothing(console, confirmation):
    seed(console, 'old', 'running')
    with pytest.raises(ConsoleError) as failure:
        reconcile_legacy(console.store, console.reader.database, 'old', confirmation=confirmation, inventory=inventory)
    assert failure.value.code == 'CONFIRMATION_REQUIRED'
    assert not console.store.root.exists()


def test_stale_confirmation_cannot_acknowledge_new_attempt(console):
    seed(console, 'old', 'running')
    confirmation = preview(console)['confirmation']
    with sqlite3.connect(console.reader.database) as connection:
        connection.execute("INSERT INTO stages(run_id,stage_name,status) VALUES ('old','qgis','running')")
    with pytest.raises(ConsoleError, match='stale'):
        reconcile_legacy(console.store, console.reader.database, 'old', confirmation=confirmation, inventory=inventory)
    assert not console.store.root.exists()


@pytest.mark.parametrize('name', ['python.exe','PYTHONW.EXE','python3.10.exe','pypy3.exe','qgis-bin.exe','gdal_translate.exe','ogr2ogr.exe','pdal.exe','odm.exe'])
def test_live_native_or_interpreter_blocks_local_acknowledgment(console, name):
    seed(console, 'old', 'running')
    processes = lambda: inventory() + [{'pid': 11111, 'name': name}]
    result = reconcile_legacy(console.store, console.reader.database, 'old', inventory=processes)
    assert result['process_blockers'] == [{'pid': 11111, 'name': name}]
    with pytest.raises(ConsoleError) as failure:
        reconcile_legacy(console.store, console.reader.database, 'old', confirmation=result['confirmation'], inventory=processes)
    assert failure.value.code == 'BUSY'
    assert not console.store.root.exists()


@pytest.mark.parametrize('rows', [[], None, [{'pid':1, 'name':'explorer.exe'}], [{'pid':os.getpid(), 'name':'python.exe'}, {'pid':2,'name':''}]])
def test_unknown_inventory_fails_closed(rows):
    with pytest.raises(ConsoleError) as failure:
        process_blockers(lambda: rows)
    assert failure.value.code == 'OWNERSHIP_UNAVAILABLE'


def test_uncertain_owned_children_block_even_with_empty_host_inventory(console):
    seed(console, 'old', 'running')
    operation, _ = console.store.reserve(uid(), {'kind': 'cli', 'run_id': 'unrelated'})
    console.store.update(operation['operation_id'], state='finished')
    console.store.processes.jobs[operation['job_name']] = None
    with pytest.raises(ConsoleError, match='uncertain'):
        apply(console)
    assert not list(console.store.root.glob('legacy-reconciled-*.json'))


def test_owned_run_cannot_use_legacy_bypass(console):
    seed(console, 'old', 'running')
    console.store.reserve(uid(), {'kind': 'cli', 'run_id': 'old'})
    with pytest.raises(ConsoleError, match='Owned invocations'):
        preview(console)


def test_process_appearing_between_checks_blocks_receipt(console):
    seed(console, 'old', 'running')
    probe = Mock(side_effect=[inventory(), inventory() + [{'pid':20000, 'name':'python.exe'}]])
    with pytest.raises(ConsoleError, match='appeared'):
        apply(console, inventory=probe)
    assert not console.store.root.exists()


def test_changed_database_during_inspection_is_not_acknowledged(console):
    seed(console, 'old', 'running')
    def probe():
        with sqlite3.connect(console.reader.database) as connection:
            connection.execute("UPDATE runs SET year=2025 WHERE run_id='old'")
        return inventory()
    with pytest.raises(ConsoleError, match='changed'):
        apply(console, inventory=probe)
    assert not console.store.root.exists()


def test_idempotent_reapply_preserves_receipt_and_database(console):
    seed(console, 'old', 'running')
    apply(console)
    receipt = next(console.store.root.glob('legacy-reconciled-*.json'))
    before = receipt.read_bytes()
    assert apply(console)['already_applied']
    assert receipt.read_bytes() == before and len(list(console.store.root.glob('legacy-reconciled-*.json'))) == 1


def test_failed_receipt_write_keeps_guard_closed(console, monkeypatch):
    seed(console, 'old', 'running')
    import operator_ui.legacy_reconciliation as module
    monkeypatch.setattr(module, 'atomic_json', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        apply(console)
    assert console.legacy_running() == ['old']


def test_malformed_receipt_fails_closed(console):
    seed(console, 'old', 'running')
    apply(console)
    next(console.store.root.glob('legacy-reconciled-*.json')).write_text('{}', encoding='utf-8')
    with pytest.raises(ConsoleError, match='Malformed'):
        console.execution_status()


def test_missing_database_not_created(console, tmp_path):
    missing = tmp_path / 'missing.db'
    with pytest.raises(ConsoleError, match='missing'):
        reconcile_legacy(console.store, missing, 'old', inventory=inventory)
    assert not missing.exists() and not console.store.root.exists()


@pytest.mark.parametrize('status', ['completed','failed','paused'])
def test_only_running_records_are_eligible(console, status):
    seed(console, 'old', status)
    with pytest.raises(ConsoleError, match='Only unowned running'):
        preview(console)


def test_operator_cli_preview_apply_and_status(console, capsys):
    seed(console, 'old', 'running')
    kwargs = {'store':console.store, 'database':console.reader.database, 'inventory':inventory}
    result = main(['reconcile-legacy', '--run-id','old'], **kwargs)
    assert not result['applied']
    main(['reconcile-legacy','--run-id','old','--confirmation',result['confirmation']], **kwargs)
    status = main(['status'], **kwargs)
    assert not status['blocked'] and status['legacy_running'] == []
    assert len(status['legacy_reconciled']) == 1
    assert 'NO LOCAL PIPELINE IS RUNNING' in capsys.readouterr().out


@pytest.fixture
def kernel(monkeypatch):
    import operator_ui.legacy_reconciliation as module
    monkeypatch.setattr(module, 'os', SimpleNamespace(name='nt'))
    kernel = SimpleNamespace(**{key:Mock(return_value=1) for key in
        ('CreateToolhelp32Snapshot','Process32FirstW','Process32NextW','CloseHandle')})
    monkeypatch.setattr(ctypes,'WinDLL',lambda *_,**__:kernel,raising=False)
    monkeypatch.setattr(ctypes,'get_last_error',lambda:18,raising=False)
    return kernel


def test_windows_process_snapshot_abi_and_handle_cleanup(kernel):
    def first(handle, target):
        entry = target._obj
        assert entry.dwSize == ctypes.sizeof(entry)
        entry.pid, entry.exe = 42, 'python.exe'
        return 1
    kernel.Process32FirstW.side_effect = first
    kernel.Process32NextW.return_value = 0
    assert windows_inventory() == [{'pid':42,'name':'python.exe'}]
    kernel.CreateToolhelp32Snapshot.assert_called_once_with(2,0)
    assert kernel.CreateToolhelp32Snapshot.restype is ctypes.c_void_p
    kernel.CloseHandle.assert_called_once_with(1)


@pytest.mark.parametrize('failure', ['snapshot','first','next'])
def test_windows_snapshot_failure_never_returns_partial_success(kernel, monkeypatch, failure):
    if failure == 'snapshot':
        kernel.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
    elif failure == 'first':
        kernel.Process32FirstW.return_value = 0
    else:
        kernel.Process32NextW.return_value = 0
        monkeypatch.setattr(ctypes,'get_last_error',lambda:5)
    with pytest.raises(ConsoleError) as error:
        windows_inventory()
    assert error.value.code == 'OWNERSHIP_UNAVAILABLE'
    assert kernel.CloseHandle.call_count == (0 if failure == 'snapshot' else 1)
