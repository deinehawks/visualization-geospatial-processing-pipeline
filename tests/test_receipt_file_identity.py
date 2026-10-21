"""Cross-interpreter receipt identity, using temporary DBs and fake native APIs."""
from contextlib import closing
import ctypes
import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_pipeline_console import console, seed, uid
from tests.test_legacy_reconciliation import inventory
from tests.test_ups_process_exception import identity as ups_descriptor
from operator_ui.file_identity import database_identities
from operator_ui.legacy_reconciliation import (FINGERPRINT_SCHEME, reconcile_legacy,
                                             reconciliation_phrase, snapshots)
from operator_ui.storage import ConsoleError, atomic_json, now, read_json
from operator_ui.ups_process import ups_identity


MODERN = (584428423918548778, 5066549580895971)
LEGACY = (474904362, 5066549580895971)


def old_receipt(console, identity=LEGACY, *, ups=False):
    """Reproduce the old stat-based algorithm, not the patched snapshot code."""
    database = console.reader.database
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.row_factory = sqlite3.Row
        data = {'database':str(database.resolve()).casefold(), 'database_identity':list(identity),
                'run':dict(connection.execute("SELECT * FROM runs WHERE run_id='old'").fetchone())}
        for table in ('stages','webodm_tasks'):
            data[table] = [dict(row) for row in connection.execute(f"SELECT * FROM {table} WHERE run_id='old' ORDER BY id")]
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    exception = ups_identity(ups_descriptor()) if ups else None
    value = {'version':1, 'scope':'local_legacy_only', 'run_id':'old', 'fingerprint':fingerprint,
             'confirmation':reconciliation_phrase('old',fingerprint,exception), 'at':now(),
             'remote_status':'unverified', 'process_check':'toolhelp_no_other_python_or_gis'}
    if exception:
        value.update(ups_discord_exception=exception, process_check='toolhelp_with_verified_ups_exception')
    console.store.ensure()
    path = console.store.root / ('legacy-reconciled-' + uid() + '.json')
    atomic_json(path, value)
    return path


@pytest.fixture
def identities(console):
    console.store.database_identity = Mock(return_value=(MODERN,LEGACY))
    return console


@pytest.mark.parametrize('format', [LEGACY,MODERN], ids=['python310','python313'])
@pytest.mark.parametrize('ups', [False,True])
def test_existing_receipt_formats_are_recognized_without_rewrite(identities, format, ups):
    console = identities
    seed(console, 'old', 'running', mode='both')
    receipt = old_receipt(console,format,ups=ups)
    receipt_bytes, database_bytes = receipt.read_bytes(), console.reader.database.read_bytes()
    status = console.execution_status()
    assert not status['blocked'] and status['available'] == 1
    assert status['legacy_running'] == [] and len(status['legacy_reconciled']) == 1
    assert '_compatible_fingerprints' not in status['legacy_reconciled'][0]
    console.store.verify_legacy(console.reader.database)
    assert console.reader.detail('old')['run']['status'] == 'running'
    assert receipt.read_bytes() == receipt_bytes
    assert console.reader.database.read_bytes() == database_bytes


@pytest.mark.parametrize('change', ['run','stage','binding','file','volume','path'])
def test_legacy_receipt_still_rejects_changed_evidence(identities, tmp_path, change):
    console = identities
    seed(console, 'old', 'running', mode='both')
    old_receipt(console)
    if change in ('run','stage','binding'):
        with closing(sqlite3.connect(console.reader.database)) as connection, connection:
            if change == 'run':
                connection.execute("UPDATE runs SET year=2025 WHERE run_id='old'")
            elif change == 'stage':
                connection.execute("INSERT INTO stages(run_id,stage_name,status) VALUES ('old','qgis','running')")
            else:
                connection.execute("INSERT INTO webodm_tasks(run_id,operation_key,task_id) VALUES ('old','task2','new-binding')")
    elif change == 'file':
        console.store.database_identity.return_value = ((MODERN[0],MODERN[1]+1),(LEGACY[0],LEGACY[1]+1))
    elif change == 'volume':
        console.store.database_identity.return_value = ((MODERN[0]+1,MODERN[1]),(LEGACY[0]+1,LEGACY[1]))
    else:
        copied = tmp_path / 'copied-test.db'
        # Include the fixture's WAL, so the copy actually contains the run.
        with closing(sqlite3.connect(console.reader.database.resolve().as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(copied)) as target:
                source.backup(target)
        console.reader.database = copied
        assert console.reader.query("SELECT run_id FROM runs WHERE run_id='old'")
    assert console.legacy_running() == ['old']
    with pytest.raises(ConsoleError) as error:
        console.store.verify_legacy(console.reader.database)
    assert error.value.code == 'LEGACY_RUNNING'


def test_new_receipt_uses_full_native_identity_not_legacy_alias(identities):
    console = identities
    seed(console, 'old', 'running')
    first = reconcile_legacy(console.store,console.reader.database,'old',inventory=inventory)
    reconcile_legacy(console.store,console.reader.database,'old',inventory=inventory,confirmation=first['confirmation'])
    value = read_json(next(console.store.root.glob('legacy-reconciled-*.json')))
    assert value['fingerprint_scheme'] == FINGERPRINT_SCHEME
    assert '_compatible_fingerprints' not in first
    # Same low DWORD serial is not enough for a new receipt: keep full volume ID.
    console.store.database_identity.return_value = ((MODERN[0] + 2**32, MODERN[1]), LEGACY)
    assert console.legacy_running() == ['old']


def test_old_receipt_is_idempotent_without_replacing_its_audit(identities):
    console = identities
    seed(console,'old','running')
    path = old_receipt(console)
    before = path.read_bytes()
    preview = reconcile_legacy(console.store,console.reader.database,'old',inventory=inventory)
    result = reconcile_legacy(console.store,console.reader.database,'old',inventory=inventory,confirmation=preview['confirmation'])
    assert result['already_applied']
    assert path.read_bytes() == before and len(list(console.store.root.glob('legacy-reconciled-*.json'))) == 1


def test_unknown_fingerprint_scheme_fails_closed(identities):
    console = identities
    seed(console,'old','running')
    path = old_receipt(console)
    value = read_json(path)
    value['fingerprint_scheme'] = 'unrecognized-future-format'
    atomic_json(path,value)
    with pytest.raises(ConsoleError) as error:
        console.execution_status()
    assert error.value.code == 'INVALID_JOURNAL'


def test_replacement_during_snapshot_is_rejected(identities):
    console = identities
    seed(console,'old','running')
    probe = Mock(side_effect=[(MODERN,LEGACY), ((MODERN[0],MODERN[1]+1),(LEGACY[0],LEGACY[1]+1))])
    with pytest.raises(ConsoleError, match='file changed'):
        snapshots(console.reader.database,'old',identity_reader=probe)


def test_failed_native_identity_cannot_fall_back_to_unverified_stat(identities):
    console = identities
    seed(console,'old','running')
    old_receipt(console)
    console.store.database_identity.side_effect = ConsoleError('Access denied','DATABASE_IDENTITY_UNAVAILABLE')
    with pytest.raises(ConsoleError) as error:
        console.execution_status()
    assert error.value.code == 'DATABASE_IDENTITY_UNAVAILABLE'


def test_no_receipts_keeps_lightweight_legacy_check(identities):
    console = identities
    seed(console,'old','running')
    console.store.database_identity.side_effect = AssertionError('No receipt needs identity verification')
    assert console.legacy_running() == ['old']


@pytest.fixture
def native(monkeypatch,tmp_path):
    import operator_ui.file_identity as module
    monkeypatch.setattr(module,'os',SimpleNamespace(name='nt'))
    kernel = SimpleNamespace(**{name:Mock(return_value=1) for name in
        ('CreateFileW','GetFileInformationByHandle','GetFileInformationByHandleEx',
         'GetVolumeInformationByHandleW','CloseHandle')})
    def classic(_handle,target):
        target._obj.volume = LEGACY[0]
        target._obj.index_high, target._obj.index_low = LEGACY[1] >> 32, LEGACY[1] & 0xffffffff
        assert ctypes.sizeof(target._obj) == 52
        return 1
    def modern(_handle,kind,target,size):
        assert kind == 18 and size == 24
        target._obj.volume = MODERN[0]
        target._obj.identifier[:] = MODERN[1].to_bytes(16,'little')
        return 1
    def volume(_handle,_name,_size,_serial,_maximum,_flags,filesystem,size):
        assert size == 64
        filesystem.value = 'NTFS'
        return 1
    kernel.GetFileInformationByHandle.side_effect = classic
    kernel.GetFileInformationByHandleEx.side_effect = modern
    kernel.GetVolumeInformationByHandleW.side_effect = volume
    monkeypatch.setattr(ctypes,'WinDLL',lambda *_,**__:kernel,raising=False)
    path = tmp_path / 'owned-native-identity.db'
    path.write_bytes(b'owned fixture, not a real database')
    return kernel,path


def test_windows_adapter_queries_one_read_only_shared_handle_and_closes(native):
    kernel,path = native
    assert database_identities(path) == (MODERN,LEGACY)
    assert kernel.CreateFileW.call_args.args == (str(path.resolve()),0x80,7,None,3,0,None)
    assert kernel.CreateFileW.restype is ctypes.c_void_p
    kernel.CloseHandle.assert_called_once_with(1)


@pytest.mark.parametrize('operation', ['open','classic','extended','volume'])
def test_native_api_failure_is_fail_closed_and_closes_handle(native,operation):
    kernel,path = native
    if operation == 'open':
        kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
    else:
        method = {'classic':'GetFileInformationByHandle','extended':'GetFileInformationByHandleEx',
                  'volume':'GetVolumeInformationByHandleW'}[operation]
        getattr(kernel,method).side_effect = None
        getattr(kernel,method).return_value = 0
    with pytest.raises(ConsoleError) as error:
        database_identities(path)
    assert error.value.code == 'DATABASE_IDENTITY_UNAVAILABLE'
    assert kernel.CloseHandle.call_count == (0 if operation == 'open' else 1)


def test_native_ntfs_representations_must_agree(native):
    kernel,path = native
    def wrong(_handle,target):
        target._obj.volume, target._obj.index_low = LEGACY[0], 999
        return 1
    kernel.GetFileInformationByHandle.side_effect = wrong
    with pytest.raises(ConsoleError, match='Inconsistent'):
        database_identities(path)
    kernel.CloseHandle.assert_called_once_with(1)


def test_refs_never_truncates_128bit_identity_for_legacy_fallback(native):
    kernel,path = native
    full = (1 << 100) + MODERN[1]
    def extended(_handle,_kind,target,_size):
        target._obj.volume = MODERN[0]
        target._obj.identifier[:] = full.to_bytes(16,'little')
        return 1
    def volume(_handle,_name,_size,_serial,_maximum,_flags,filesystem,_buffer_size):
        filesystem.value = 'ReFS'
        return 1
    kernel.GetFileInformationByHandleEx.side_effect = extended
    kernel.GetVolumeInformationByHandleW.side_effect = volume
    assert database_identities(path) == ((MODERN[0],full),)
