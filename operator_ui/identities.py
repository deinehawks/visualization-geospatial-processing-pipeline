"""Canonical local/share identities. No probing occurs at import time."""
import ctypes
import ntpath
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def canonical_path(value):
    path = Path(value).expanduser().resolve(strict=False)
    text = str(path)
    if text.startswith('\\\\?\\UNC\\'):
        text = '\\\\' + text[8:]
    elif text.startswith('\\\\?\\'):
        text = text[4:]
    if os.name == 'nt':
        from ctypes import wintypes
        # resolve() uses Windows final paths; additionally expand mapped shares
        # when the leaf does not yet exist. Never fall back to an unverified map.
        if not text.startswith('\\\\'):
            drive = ntpath.splitdrive(text)[0]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
            if kernel.GetDriveTypeW(drive + '\\') == 4:
                network = ctypes.WinDLL('mpr', use_last_error=True)
                network.WNetGetUniversalNameW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                                         ctypes.c_void_p, ctypes.c_void_p]
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_string_buffer(size.value)
                if network.WNetGetUniversalNameW(text, 1, buffer, ctypes.byref(size)) != 0:
                    raise ValueError('Cannot establish mapped-share identity')
                text = ctypes.cast(buffer, ctypes.POINTER(wintypes.LPWSTR)).contents.value
        text = ntpath.normpath(text)
    return text.replace('\\', '/').rstrip('/').casefold()


def overlaps(left, right):
    return bool(left and right and (left == right or left.startswith(right + '/') or right.startswith(left + '/')))


def remote_key(url, project_id):
    value = urlsplit(url)
    if value.scheme not in ('http', 'https') or not value.hostname:
        raise ValueError('Cannot establish WebODM endpoint identity')
    host = value.hostname.casefold()
    if host in ('localhost', '127.0.0.1', '::1'):
        host = '127.0.0.1'
    if ':' in host:
        host = '[' + host + ']'
    port = value.port
    if port and port != (443 if value.scheme == 'https' else 80):
        host += ':' + str(port)
    endpoint = urlunsplit((value.scheme, host, value.path.rstrip('/'), '', ''))
    return endpoint + '/projects/' + str(int(project_id))


def volume_identity(path, canonicalizer=canonical_path):
    value = canonicalizer(path)
    if value.startswith('//'):
        return '/'.join(value.split('/')[:4])
    if os.name != 'nt':
        probe = Path(path).resolve(strict=False)
        while not probe.exists():
            probe = probe.parent
        return 'device:' + str(probe.stat().st_dev)
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    mount, name = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(32768)
    probe = Path(path).resolve(strict=False)
    while not probe.exists():
        if probe.parent == probe:
            raise ValueError('Cannot establish storage volume identity')
        probe = probe.parent
    if not kernel.GetVolumePathNameW(str(probe), mount, len(mount)) or not kernel.GetVolumeNameForVolumeMountPointW(mount.value, name, len(name)):
        raise ValueError('Cannot establish storage volume identity')
    return name.value.casefold()
