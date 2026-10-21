"""One-invocation UPSDiscord identity verification, never a persistent allowlist."""
import ctypes
import hashlib
import json
import ntpath
import os
import re
import subprocess

from .storage import ConsoleError, WindowsProcesses


def process_id(value):
    if type(value) is not int or not 0 < value < 2**32:
        raise ConsoleError('Specify a positive UPSDiscord process ID', 'INVALID_PROCESS_EXCEPTION')
    return value


def ups_identity(value):
    """Accept only a two-argument python.exe + UPSDiscord.py invocation.

    A relative bare script name is an operator assertion about this invocation,
    not proof of the script's working directory or a whitelist for future PIDs.
    """
    if not isinstance(value, dict):
        raise ConsoleError('Cannot verify UPSDiscord identity', 'INVALID_PROCESS_EXCEPTION')
    pid = process_id(value.get('pid'))
    birth, executable, command = (value.get(key) for key in ('birth', 'executable', 'command_line'))
    if (not isinstance(birth, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', birth)
            or not isinstance(executable, str) or not isinstance(command, str)
            or len(executable) > 4096 or len(command) > 8192
            or any(character in executable + command for character in '\x00\r\n')):
        raise ConsoleError('UPSDiscord details unavailable; use an Administrator terminal', 'INVALID_PROCESS_EXCEPTION')
    executable = ntpath.normcase(ntpath.normpath(executable))
    if (not re.match(r'^[a-z]:\\', executable) or ntpath.basename(executable) != 'python.exe'):
        raise ConsoleError('UPSDiscord requires a verified absolute python.exe path', 'INVALID_PROCESS_EXCEPTION')
    # Deliberately constrained grammar, not a general shell/Windows-argv parser.
    # Reject -c/-m/-i, extra args, wrapper shells, and other script names.
    match = re.fullmatch(r'\s*(?:"([^"\r\n]+)"|([^"\s]+))\s+(?:"([^"\r\n]+)"|([^"\s]+))\s*', command)
    if not match:
        raise ConsoleError('Only python.exe running UPSDiscord.py without extra arguments is eligible', 'INVALID_PROCESS_EXCEPTION')
    interpreter, script = match.group(1) or match.group(2), match.group(3) or match.group(4)
    if (ntpath.normcase(ntpath.normpath(interpreter)) != executable
            or ntpath.basename(script).casefold() != 'upsdiscord.py'
            or not (script.casefold() == 'upsdiscord.py' or re.match(r'^[a-zA-Z]:[\\/]', script))):
        raise ConsoleError('Process does not match the verified UPSDiscord invocation', 'INVALID_PROCESS_EXCEPTION')
    proof = {'pid': pid, 'birth': birth, 'executable': executable, 'command_line': command}
    proof['fingerprint'] = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    if 'fingerprint' in value and value['fingerprint'] != proof['fingerprint']:
        raise ConsoleError('UPSDiscord identity evidence does not match', 'INVALID_PROCESS_EXCEPTION')
    return proof


def system_directory():
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not size or size >= len(buffer):
        raise ConsoleError('Cannot locate trusted Windows system directory', 'OWNERSHIP_UNAVAILABLE')
    return buffer.value


def windows_ups_identity(pid, *, runner=None, processes=None, directory=None):
    """Read just this PID via fixed, noninteractive local CIM; no script execution.

    PowerShell executes only the fixed inspection expression below, never the
    utility or a command supplied by the operator. No process is terminated.
    """
    process_id(pid)
    if pid == os.getpid():
        raise ConsoleError('Cannot exempt the reconciliation process itself', 'INVALID_PROCESS_EXCEPTION')
    if os.name != 'nt':
        raise ConsoleError('UPSDiscord verification requires Windows', 'OWNERSHIP_UNAVAILABLE')
    processes = processes or WindowsProcesses()
    birth = processes.identity(pid)
    if birth in (None, 'unknown'):
        raise ConsoleError('UPSDiscord process is absent or inaccessible; use an Administrator terminal', 'OWNERSHIP_UNAVAILABLE')
    directory = directory() if directory is not None else system_directory()
    if not isinstance(directory, str) or not re.match(r'^[a-zA-Z]:[\\/]', directory):
        raise ConsoleError('Cannot locate trusted Windows system directory', 'OWNERSHIP_UNAVAILABLE')
    executable = ntpath.join(directory, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    script = ("$ErrorActionPreference='Stop'; "
              "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
              f"Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId = {pid}' | "
              'Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress')
    try:
        result = (runner or subprocess.run)(
            [executable, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
            shell=False, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding='utf-8-sig', errors='strict', timeout=10, cwd=directory,
            creationflags=0x08000000)  # CREATE_NO_WINDOW; no visible helper window
        if result.returncode or not isinstance(result.stdout, str) or len(result.stdout) > 16384:
            raise ConsoleError('UPSDiscord inspection failed; no exception granted', 'OWNERSHIP_UNAVAILABLE')
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ConsoleError('UPSDiscord inspection failed; use an Administrator terminal and retry preview', 'OWNERSHIP_UNAVAILABLE') from exc
    if not isinstance(data, dict) or data.get('ProcessId') != pid or processes.identity(pid) != birth:
        raise ConsoleError('UPSDiscord exited or changed during inspection; preview again', 'OWNERSHIP_UNAVAILABLE')
    return ups_identity({'pid': pid, 'birth': birth, 'executable': data.get('ExecutablePath'),
                         'command_line': data.get('CommandLine')})
