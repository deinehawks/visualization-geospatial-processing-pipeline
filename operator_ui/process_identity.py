"""Negative-only CIM proof for an inaccessible PID reused by a newer process."""
import json
import logging
import ntpath
import os
import re
import subprocess
import time


TICKS_PER_SECOND = 10_000_000
LOG = logging.getLogger(__name__)


class IdentityProbeError(RuntimeError):
    pass


def observed_owner(processes, pid, birth):
    """Keep native identity for claims/controls; use this only for old owners."""
    if not pid:
        return None
    probe = getattr(processes, 'owner_identity', None)
    return probe(pid, birth) if probe is not None else processes.identity(pid)


def _ticks(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value)
            or int(value) >= 2 ** 64):
        return None
    return int(value)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate inspection field')
        value[key] = item
    return value


def cim_creation(pid, *, runner=None, directory=None):
    """Read two local, PID-filtered snapshots in one bounded hidden process.

    No command lines, paths, credentials or other processes are enumerated.
    Missing/failed CIM data never constitutes proof of process exit.
    """
    if os.name != 'nt' or type(pid) is not int or not 0 < pid < 2 ** 32:
        raise IdentityProbeError('Invalid process inspection target')
    from .ups_process import system_directory
    from .storage import ConsoleError
    try:
        system = (directory or system_directory)()
        if (not isinstance(system, str) or not re.match(r'^[A-Za-z]:[\\/]', system)
                or any(part in ('.', '..') for part in re.split(r'[\\/]', system))
                or any(char in system for char in '\x00\r\n')):
            raise IdentityProbeError('Unverified Windows system directory')
        executable = ntpath.join(system, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
        script = (
            "$ErrorActionPreference='Stop'; "
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
            "$samples=@(foreach($sample in 1..2) { "
            f"$rows=@(Get-CimInstance -Namespace 'root/cimv2' -ClassName Win32_Process -Filter 'ProcessId = {pid}' -ErrorAction Stop); "
            "if($rows.Count -ne 1 -or $rows[0].CreationDate -isnot [DateTime] -or "
            "$rows[0].CreationDate.Kind -eq [DateTimeKind]::Unspecified) { throw 'Unavailable process identity' }; "
            "@{pid=[int]$rows[0].ProcessId; birth=$rows[0].CreationDate.ToFileTimeUtc().ToString([Globalization.CultureInfo]::InvariantCulture)} "
            "}); @{version=1; samples=$samples} | ConvertTo-Json -Compress -Depth 4"
        )
        result = (runner or subprocess.run)(
            [executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
            shell=False, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding='utf-8-sig', errors='strict', timeout=5, cwd=system,
            creationflags=0x08000000)
        if (result.returncode != 0 or not isinstance(result.stdout, str)
                or len(result.stdout) > 4096):
            raise IdentityProbeError('Process inspection failed')
        payload = json.loads(result.stdout, object_pairs_hook=_unique_object)
        if (not isinstance(payload, dict) or set(payload) != {'version', 'samples'}
                or type(payload['version']) is not int or payload['version'] != 1
                or not isinstance(payload['samples'], list) or len(payload['samples']) != 2):
            raise IdentityProbeError('Invalid process inspection evidence')
        values = []
        for sample in payload['samples']:
            if (not isinstance(sample, dict) or set(sample) != {'pid', 'birth'}
                    or type(sample['pid']) is not int or sample['pid'] != pid
                    or _ticks(sample['birth']) is None):
                raise IdentityProbeError('Invalid process creation evidence')
            values.append(_ticks(sample['birth']))
        if values[0] != values[1]:
            raise IdentityProbeError('Process changed during inspection')
        return values[0]
    except (OSError, subprocess.SubprocessError, ValueError, ConsoleError) as exc:
        raise IdentityProbeError('Cannot verify local CIM process creation') from exc


def owner_identity(pid, birth, *, identity, runner=None, directory=None, clock=None):
    """Prove only that the current PID is clearly newer, never that it is owned.

    CIM is not exact native FILETIME evidence. Keep a conservative one-second
    ambiguity window, never use CIM for claims/controls, and never infer exit
    from absence. Native identity is rechecked after the two CIM snapshots.
    """
    if type(pid) is not int or not 0 < pid < 2 ** 32:
        return 'unknown'
    native = identity(pid)
    if native != 'unknown':
        return native
    expected = _ticks(birth)
    if expected is None:
        return 'unknown'
    try:
        current = cim_creation(pid, runner=runner, directory=directory)
    except IdentityProbeError as exc:
        LOG.warning('event=owner_identity_unverified pid=%s reason=cim_unavailable detail=%s', pid, exc)
        return 'unknown'
    # A newly available native observation always wins, including verified exit.
    native = identity(pid)
    if native != 'unknown':
        return native
    now = clock() if clock is not None else time.time_ns() // 100 + 116444736000000000
    if current > now or current <= expected + TICKS_PER_SECOND:
        LOG.warning('event=owner_identity_unverified pid=%s reason=ambiguous_creation', pid)
        return 'unknown'
    LOG.info('event=owner_pid_reuse_verified pid=%s recorded_birth=%s cim_birth=%s', pid, birth, current)
    return str(current)
