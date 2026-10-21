"""Explicit local-only acknowledgments of unchanged, unowned legacy records.

The pipeline database is always opened read-only. Receipts never authorize a
remote retry or assert processing success; any changed evidence invalidates them.
"""
from contextlib import closing
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .storage import ConsoleError, atomic_json, identifier, now, read_json
from .ups_process import process_id, ups_identity, windows_ups_identity
from .file_identity import database_identities


FINGERPRINT_SCHEME = 'native-file-identity-v2'


def receipt_matches(value, row):
    if value['run_id'] != row['run_id']:
        return False
    if value.get('fingerprint_scheme') == FINGERPRINT_SCHEME:
        return value['fingerprint'] == row['fingerprint']
    return value['fingerprint'] in row['_compatible_fingerprints']


def reconciliation_phrase(run_id, fingerprint, exception=None):
    suffix = f" ALLOW VERIFIED UPSDISCORD {exception['pid']} {exception['fingerprint']}" if exception else ''
    return f'RECONCILE LEGACY {run_id} {fingerprint}{suffix} NO LOCAL PIPELINE IS RUNNING'


def windows_inventory():
    """Read a complete Toolhelp process-name snapshot, without commands or shell."""
    if os.name != 'nt':
        raise ConsoleError('Legacy reconciliation requires Windows process verification', 'OWNERSHIP_UNAVAILABLE')
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        getattr(kernel, name).restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        raise ConsoleError('Cannot inspect local processes', 'OWNERSHIP_UNAVAILABLE')
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(handle, ctypes.byref(entry)):
            raise ConsoleError('Cannot read complete process snapshot', 'OWNERSHIP_UNAVAILABLE')
        result = []
        while True:
            result.append({'pid': int(entry.pid), 'name': entry.exe})
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES only
                    raise ConsoleError('Incomplete process snapshot', 'OWNERSHIP_UNAVAILABLE')
                return result
    finally:
        kernel.CloseHandle(handle)


def process_blockers(inventory, *, exception=None, ups_inspector=windows_ups_identity):
    # Inspect the opt-in process before the full snapshot, so a slow CIM query
    # cannot hide a new Python/GIS process that appeared while it was running.
    if exception is not None:
        if ups_identity(ups_inspector(exception['pid'])) != exception:
            raise ConsoleError('UPSDiscord identity changed; preview again', 'STALE_PROCESS_EXCEPTION')
    rows = inventory()
    if (not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows)
            or not any(row.get('pid') == os.getpid() for row in rows)):
        raise ConsoleError('Process inventory cannot verify the caller', 'OWNERSHIP_UNAVAILABLE')
    if exception is not None:
        matches = [row for row in rows if row.get('pid') == exception['pid']]
        if (exception['pid'] == os.getpid() or len(matches) != 1
                or str(matches[0].get('name')).casefold() != 'python.exe'):
            raise ConsoleError('UPSDiscord process changed or is absent; preview again', 'STALE_PROCESS_EXCEPTION')
    blockers = []
    for row in rows:
        if type(row.get('pid')) is not int or not isinstance(row.get('name'), str) or not row['name']:
            raise ConsoleError('Malformed process inventory', 'OWNERSHIP_UNAVAILABLE')
        if row['pid'] == os.getpid():
            continue
        if exception is not None and row['pid'] == exception['pid']:
            continue
        name = row['name'].casefold()
        if re.match(r'^(python|pypy|qgis|gdal|ogr|pdal|odm|webodm)', name):
            blockers.append({'pid': row['pid'], 'name': row['name']})
    return blockers


def snapshot(connection, database, run_id, *, identities):
    connection.row_factory = sqlite3.Row
    run = connection.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
    if run is None:
        raise ConsoleError('Legacy run not found', 'RUN_NOT_FOUND')
    data = {'database': str(Path(database).resolve()).casefold(),
            'database_identity': list(identities[0]), 'run': dict(run)}
    for table in ('stages', 'webodm_tasks'):
        exists = connection.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()
        rows = connection.execute(f'SELECT * FROM {table} WHERE run_id=? ORDER BY id LIMIT 2001', (run_id,)).fetchall() if exists else []
        if len(rows) > 2000:
            raise ConsoleError('Legacy history exceeds reconciliation bound; diagnose separately', 'HISTORY_LIMIT')
        data[table] = [dict(row) for row in rows]
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    if len(encoded) > 8 * 1024 * 1024:
        raise ConsoleError('Legacy history exceeds reconciliation bound; diagnose separately', 'HISTORY_LIMIT')
    compatible = []
    for identity in identities:
        data['database_identity'] = list(identity)
        candidate = json.dumps(data, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
        compatible.append(hashlib.sha256(candidate).hexdigest())
    latest = data['stages'][-1] if data['stages'] else {}
    return {'run_id': run_id, 'survey_id': run['survey_id'], 'persisted_status': run['status'],
            'latest_stage': latest.get('stage_name'), 'latest_stage_status': latest.get('status'),
            'last_recorded_at': latest.get('finished_at') or latest.get('started_at') or run['started_at'],
            'fingerprint': compatible[0], 'fingerprint_scheme': FINGERPRINT_SCHEME,
            '_compatible_fingerprints': compatible,
            'remote_status': 'unverified'}


def snapshots(database, run_id=None, *, exclude=(), fingerprints=None, identity_reader=None):
    path = Path(database)
    if not path.is_file():
        if run_id:
            raise ConsoleError('Pipeline database missing; nothing to reconcile', 'DATABASE_MISSING')
        return []
    identity_reader = identity_reader or database_identities
    # Capture identity before SQLite opens the path, then recheck after reading.
    # A replaced path must not attach an old connection's rows to a new file ID.
    identities = identity_reader(path) if run_id is not None or fingerprints is None or fingerprints else None
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        ids = [run_id] if run_id else [row[0] for row in connection.execute("SELECT run_id FROM runs WHERE status='running'")]
        selected = [value for value in ids if value not in exclude]
        needs_identity = any(fingerprints is None or value in fingerprints for value in selected)
        result = [snapshot(connection, database, value, identities=identities) if fingerprints is None or value in fingerprints
                  else {'run_id': value, 'fingerprint': None} for value in selected]
        if needs_identity and identity_reader(path) != identities:
            raise ConsoleError('Database file changed during inspection; retry preview', 'STALE_RECONCILIATION')
        return result


def receipts(store):
    values = []
    if not store.root.exists():
        return values
    # Ownership validation is performed by operations() before this function.
    for path in store.root.glob('legacy-reconciled-*.json'):
        if path.stat().st_size > 16384:
            raise ConsoleError('Oversized legacy reconciliation receipt', 'INVALID_JOURNAL')
        value = read_json(path)
        exception = value.get('ups_discord_exception')
        if exception is not None:
            try:
                if ups_identity(exception) != exception:
                    raise ConsoleError('Unexpected process exception fields')
            except ConsoleError as exc:
                raise ConsoleError('Malformed legacy process exception receipt', 'INVALID_JOURNAL') from exc
        process_check = 'toolhelp_with_verified_ups_exception' if exception else 'toolhelp_no_other_python_or_gis'
        if (type(value.get('version')) is not int or value['version'] != 1 or value.get('scope') != 'local_legacy_only'
                or value.get('remote_status') != 'unverified'
                or not isinstance(value.get('run_id'), str)
                or not isinstance(value.get('fingerprint'), str)
                or not re.fullmatch('[0-9a-f]{64}', value.get('fingerprint', ''))
                or not isinstance(value.get('at'), str)
                or value.get('fingerprint_scheme', FINGERPRINT_SCHEME) != FINGERPRINT_SCHEME
                or value.get('process_check') != process_check
                or value.get('confirmation') != reconciliation_phrase(value['run_id'], value['fingerprint'], exception)):
            raise ConsoleError('Malformed legacy reconciliation receipt', 'INVALID_JOURNAL')
        values.append(value)
    return values


def legacy_status(store, database):
    owned = {row.get('run_id') for row in store.operations() if row['kind'] in ('cli', 'run')}
    acknowledgments = receipts(store)
    pending, reconciled = [], []
    for row in snapshots(database, exclude=owned, fingerprints={value['run_id'] for value in acknowledgments},
                         identity_reader=store.database_identity):
        if any(receipt_matches(value, row) for value in acknowledgments if value['run_id'] == row['run_id']):
            row.pop('_compatible_fingerprints')
            reconciled.append(row)
        else:
            pending.append(row['run_id'])
    return {'legacy_running': pending, 'legacy_reconciled': reconciled}


def reconcile_legacy(store, database, run_id, *, confirmation=None, inventory=windows_inventory,
                     allow_ups_discord_pid=None, ups_inspector=windows_ups_identity):
    """Preview by default. Apply only to exact unchanged evidence on an idle host."""
    identifier(run_id)
    if allow_ups_discord_pid is not None:
        process_id(allow_ups_discord_pid)
        if allow_ups_discord_pid == os.getpid():
            raise ConsoleError('Cannot exempt the reconciliation process itself', 'INVALID_PROCESS_EXCEPTION')
    with store.lock():
        operations = store.operations()
        if any(row.get('run_id') == run_id and row['kind'] in ('cli', 'run') for row in operations):
            raise ConsoleError('Owned invocations use operation reconciliation, not legacy acknowledgment', 'ACTION_UNAVAILABLE')
        row = snapshots(database, run_id, identity_reader=store.database_identity)[0]
        if row['persisted_status'] != 'running':
            raise ConsoleError('Only unowned running records need this acknowledgment', 'ACTION_UNAVAILABLE')
        exception = ups_identity(ups_inspector(allow_ups_discord_pid)) if allow_ups_discord_pid is not None else None
        if exception is not None and exception['pid'] != allow_ups_discord_pid:
            raise ConsoleError('UPSDiscord process ID does not match inspection', 'INVALID_PROCESS_EXCEPTION')
        expected = reconciliation_phrase(run_id, row['fingerprint'], exception)
        result = {**{key: value for key, value in row.items() if not key.startswith('_')},
                  'confirmation': expected, 'database_changed': False}
        if exception:
            result['ups_discord_exception'] = exception
        check = {'exception': exception, 'ups_inspector': ups_inspector}
        if confirmation is None:
            return {**result, 'applied': False, 'owned_operations_blocking': bool(store.occupants()),
                    'process_blockers': process_blockers(inventory, **check)}
        if confirmation != expected:
            raise ConsoleError('Confirmation is missing or stale; preview this exact run again', 'CONFIRMATION_REQUIRED')
        if store.occupants():
            raise ConsoleError('Owned or uncertain executions must be resolved first', 'BUSY')
        if process_blockers(inventory, **check):
            raise ConsoleError('Close other Python/QGIS/GDAL applications, console server and legacy pipelines first; preview lists PIDs', 'BUSY')
        # Recheck after potentially slow process inspection. Never acknowledge a
        # different attempt/binding, even when an old launcher ignores our mutex.
        if snapshots(database, run_id, identity_reader=store.database_identity)[0]['fingerprint'] != row['fingerprint']:
            raise ConsoleError('Legacy evidence changed; preview again', 'STALE_RECONCILIATION')
        if process_blockers(inventory, **check):
            raise ConsoleError('Local execution appeared during verification', 'BUSY')
        store.ensure()
        if any(receipt_matches(value, row)
               and value.get('ups_discord_exception') == exception for value in receipts(store)):
            return {**result, 'applied': True, 'already_applied': True}
        receipt = {'version': 1, 'scope': 'local_legacy_only', 'run_id': run_id,
                   'fingerprint': row['fingerprint'], 'fingerprint_scheme': FINGERPRINT_SCHEME,
                   'confirmation': expected, 'at': now(),
                   'remote_status': 'unverified', 'process_check': 'toolhelp_no_other_python_or_gis'}
        if exception:
            receipt.update(ups_discord_exception=exception, process_check='toolhelp_with_verified_ups_exception')
        if len(json.dumps(receipt, ensure_ascii=False).encode('utf-8')) > 16384:
            raise ConsoleError('Process exception evidence exceeds receipt size limit', 'INVALID_PROCESS_EXCEPTION')
        atomic_json(store.root / ('legacy-reconciled-' + uuid.uuid4().hex + '.json'), receipt)
        return {**result, 'applied': True, 'already_applied': False}
