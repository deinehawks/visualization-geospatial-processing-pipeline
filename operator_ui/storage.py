"""Owned, atomic console journals; no pipeline database writes."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone

from .admission import AdmissionMixin
from .identities import canonical_path, volume_identity


class ConsoleError(RuntimeError):
    def __init__(self, message, code='CONFLICT', status=409):
        super().__init__(message)
        self.code, self.status = code, status


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ConsoleError('Invalid identifier', 'INVALID_INPUT', 422)
    return value


def read_json(path):
    if Path(path).is_symlink():
        raise ConsoleError('Console journal cannot be a symlink')
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise ConsoleError('Unreadable console journal; reconciliation required', 'INVALID_JOURNAL') from exc
    if not isinstance(value, dict):
        raise ConsoleError('Invalid console journal', 'INVALID_JOURNAL')
    return value


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(uuid.uuid4().hex + '.tmp')
    # Keep failed temporary evidence instead of deleting any runtime file.
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, allow_nan=False, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class WindowsMutex:
    """Single-host, cross-process exclusion, including updated CLI launches."""
    def __init__(self, scope):
        self.name = 'Global\\VizPipeline-' + hashlib.sha256(str(scope).casefold().encode()).hexdigest()
        self.handle = None

    def __enter__(self):
        if os.name != 'nt':
            raise ConsoleError('Execution ownership currently requires Windows', 'UNSUPPORTED_HOST')
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        handle = kernel.CreateMutexW(None, False, self.name)
        if not handle:
            raise ConsoleError('Cannot open execution guard', 'OWNERSHIP_UNAVAILABLE')
        result = kernel.WaitForSingleObject(handle, 5000)
        if result not in (0, 128):
            kernel.CloseHandle(handle)
            raise ConsoleError('Another command owns the execution guard', 'BUSY')
        self.handle = handle
        return self

    def __exit__(self, *_):
        self.kernel.ReleaseMutex(self.handle)
        self.kernel.CloseHandle(self.handle)


class WindowsProcesses:
    """Verified PID creation times and named job membership, never PID-only liveness."""
    def _kernel(self):
        if os.name != 'nt':
            raise ConsoleError('Process verification requires Windows', 'UNSUPPORTED_HOST')
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.c_void_p] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenJobObjectW.restype = wintypes.HANDLE
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    wintypes.DWORD, ctypes.c_void_p]
        return kernel

    def identity(self, pid):
        kernel = self._kernel()
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return None if ctypes.get_last_error() == 87 else 'unknown'
        try:
            code = ctypes.c_ulong()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return 'unknown'
            if code.value != 259:
                return None
            times = [ctypes.c_ulonglong() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
                return 'unknown'
            return str(times[0].value)
        finally:
            kernel.CloseHandle(handle)

    def job_active(self, name):
        kernel = self._kernel()
        handle = kernel.OpenJobObjectW(4, False, name)
        if not handle:
            return 0 if ctypes.get_last_error() == 2 else None
        class Accounting(ctypes.Structure):
            _fields_ = [('times', ctypes.c_longlong * 4), ('faults', ctypes.c_uint32),
                        ('total', ctypes.c_uint32), ('active', ctypes.c_uint32), ('terminated', ctypes.c_uint32)]
        try:
            data = Accounting()
            if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(data), ctypes.sizeof(data), None):
                return None
            return int(data.active)
        finally:
            kernel.CloseHandle(handle)

    def join_job(self, name):
        kernel = self._kernel()
        from ctypes import wintypes
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, name)
        if not handle or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
            if handle:
                kernel.CloseHandle(handle)
            raise ConsoleError('Cannot establish descendant ownership', 'OWNERSHIP_UNAVAILABLE')
        # No KILL_ON_JOB_CLOSE: API/browser closure must not kill processing.
        return kernel, handle


TERMINAL = frozenset({'finished', 'failed', 'canceled', 'resolved'})


class ConsoleStore(AdmissionMixin):
    def __init__(self, root, *, lock_factory=WindowsMutex, processes=None,
                 path_identity=canonical_path, volume_key=volume_identity, database_identity=None):
        from .file_identity import database_identities
        self.root = Path(root).resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError('Console workspace cannot be a filesystem root')
        self.lock_factory = lock_factory
        self.processes = processes or WindowsProcesses()
        self.path_identity = path_identity
        self.volume_identity = volume_key
        self.database_identity = database_identity or database_identities

    def lock(self):
        return self.lock_factory(self.root)

    def ensure(self):
        if self.root.is_symlink():
            raise ConsoleError('Console workspace cannot be a symlink')
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / '.pipeline-ui-owned.json'
        if not marker.exists():
            if list(self.root.iterdir()):
                raise ConsoleError('Refusing an unowned console workspace')
            atomic_json(marker, {'owner': 'pipeline-ui-v1'})
        if read_json(marker) != {'owner': 'pipeline-ui-v1'}:
            raise ConsoleError('Invalid console workspace ownership')

    def directory(self, operation_id):
        path = self.root / identifier(operation_id)
        if path.is_symlink() or path.resolve().parent != self.root:
            raise ConsoleError('Invalid operation containment')
        return path

    def operations(self):
        if not self.root.exists():
            return []
        if not (self.root / '.pipeline-ui-owned.json').is_file():
            raise ConsoleError('Unowned console workspace')
        values = []
        for directory in self.root.iterdir():
            if directory.is_dir():
                if directory.is_symlink():
                    raise ConsoleError('Invalid operation containment')
                values.append(self.status(directory.name))
        return sorted(values, key=lambda value: value['created_at'], reverse=True)

    def status(self, operation_id):
        return read_json(self.directory(operation_id) / 'status.json')

    def update(self, operation_id, **fields):
        # Caller holds the store mutex. Requests/results are separate immutable evidence.
        value = {**self.status(operation_id), **fields, 'updated_at': now()}
        atomic_json(self.directory(operation_id) / 'status.json', value)
        return value

    def occupied(self):
        for value in self.operations():
            pid, birth = value.get('pid'), value.get('birth')
            live = self.processes.identity(pid) if pid else None
            children = self.processes.job_active(value['job_name'])
            owner_present = live == 'unknown' or (live is not None and (birth is None or live == birth))
            intent = self.directory(value['operation_id']) / 'restart-intent.json'
            unresolved_restart = intent.exists() and not read_json(intent).get('resolved')
            if value['state'] not in TERMINAL or owner_present or children != 0 or unresolved_restart:
                return value
        return None

    def reserve(self, operation_id, request):
        with self.lock():
            self.ensure()
            directory = self.directory(operation_id)
            if directory.exists():
                if read_json(directory / 'request.json') != request:
                    raise ConsoleError('Command ID already has another request', 'IDEMPOTENCY_CONFLICT')
                return self.status(operation_id), False
            admission = self.admit(request)
            if request['kind'] == 'run' and any(
                read_json(self.directory(row['operation_id']) / 'request.json').get('preflight_id') == request['preflight_id']
                for row in self.operations()
            ):
                raise ConsoleError('This preflight has already been used; check again', 'PREFLIGHT_CONSUMED')
            directory.mkdir(exist_ok=False)
            atomic_json(directory / 'request.json', request)
            created = now()
            value = {'operation_id': operation_id, 'created_at': created, 'updated_at': created,
                     'state': 'accepted', 'kind': request['kind'], 'run_id': request.get('run_id'),
                     'job_name': 'Global\\VizPipelineJob-' + uuid.uuid4().hex, **admission}
            atomic_json(directory / 'status.json', value)
            return value, True

    @contextmanager
    def claim(self, operation_id):
        with self.lock():
            value = self.status(operation_id)
            if value['state'] != 'accepted' or value.get('pid'):
                raise ConsoleError('Operation already dispatched', 'ALREADY_DISPATCHED')
            pid = os.getpid()
            birth = self.processes.identity(pid)
            if birth in (None, 'unknown'):
                raise ConsoleError('Cannot verify runner identity', 'OWNERSHIP_UNAVAILABLE')
            job = self.processes.join_job(value['job_name'])
            self.update(operation_id, state='preparing', pid=pid, birth=birth)
        try:
            yield
        finally:
            kernel, handle = job
            kernel.CloseHandle(handle)

    def reconcile(self, operation_id, confirmation):
        with self.lock():
            value = self.status(operation_id)
            if confirmation != f'RECONCILE {operation_id}':
                raise ConsoleError('Exact reconciliation confirmation required', 'CONFIRMATION_REQUIRED')
            if value.get('pid'):
                live = self.processes.identity(value['pid'])
                if live == 'unknown' or (live is not None and (not value.get('birth') or live == value['birth'])):
                    raise ConsoleError('Runner identity is live or cannot be verified')
            if self.processes.job_active(value['job_name']) != 0:
                raise ConsoleError('Owned descendants are live or cannot be verified')
            intent = self.directory(operation_id) / 'restart-intent.json'
            if intent.exists() and not read_json(intent).get('resolved'):
                raise ConsoleError('Remote restart outcome needs separate reconciliation', 'REMOTE_OUTCOME_UNKNOWN')
            mutation = self.directory(operation_id) / 'remote-mutation.json'
            if mutation.exists() and not read_json(mutation).get('resolved'):
                raise ConsoleError('Remote creation outcome needs separate reconciliation', 'REMOTE_OUTCOME_UNKNOWN')
            if value.get('pending_creation'):
                raise ConsoleError('Returned remote identity is not durably bound; use binding repair first', 'REMOTE_OUTCOME_UNKNOWN')
            atomic_json(self.directory(operation_id) / ('reconciled-' + uuid.uuid4().hex + '.json'),
                        {'confirmation': confirmation, 'at': now(), 'previous_state': value['state']})
            return self.update(operation_id, state='resolved')


@contextmanager
def cli_execution_guard(base_dir, run_id, *, admission=None, exclusive=False):
    from .coordination import Execution
    from .projections import safe_url
    if admission is not None:
        admission = {**admission, 'webodm_url': safe_url(admission.get('webodm_url') or '')}
    store = ConsoleStore(Path(base_dir) / 'runtime' / 'pipeline-ui')
    operation_id = uuid.uuid4().hex
    with store.lock():
        store.verify_legacy(Path(base_dir) / 'data' / 'pipeline.db')
        store.reserve(operation_id, {'kind': 'cli', 'run_id': run_id,
                                     **({'admission': admission} if admission is not None else {}),
                                     **({'exclusive': exclusive} if exclusive else {})})
    with store.claim(operation_id):
        execution = Execution(store, operation_id)
        try:
            yield execution
        except BaseException:
            with store.lock():
                store.update(operation_id, state='requires_recovery')
            raise
        else:
            with store.lock():
                current = store.status(operation_id)
                if current['state'] != 'requires_recovery':
                    store.update(operation_id, state='finished')
