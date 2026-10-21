"""Interpreter-independent database identities, read-only and without .env."""
import ctypes
import os
from pathlib import Path

from .storage import ConsoleError


def database_identities(path):
    """Canonical identity first; verified legacy stat representation(s) follow.

    Windows' extended ID is canonical. On NTFS only, the native classic ID also
    reproduces Python 3.10's original receipt payload. Never truncate an arbitrary
    current stat result or discard file identity to manufacture compatibility.
    """
    path = Path(path).resolve(strict=True)
    if os.name != 'nt':
        value = path.stat()
        return ((value.st_dev, value.st_ino),)
    from ctypes import wintypes
    class Classic(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                    ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [('volume', ctypes.c_ulonglong), ('identifier', ctypes.c_ubyte * 16)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(Classic)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.GetVolumeInformationByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # FILE_READ_ATTRIBUTES, share read/write/delete, OPEN_EXISTING. Never create,
    # write, quarantine, migrate or take exclusive ownership of the database.
    handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0, None)
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        raise ConsoleError('Cannot verify database file identity', 'DATABASE_IDENTITY_UNAVAILABLE')
    try:
        classic, extended = Classic(), Extended()
        filesystem = ctypes.create_unicode_buffer(64)
        if (not kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(extended), ctypes.sizeof(extended))
                or not kernel.GetFileInformationByHandle(handle, ctypes.byref(classic))
                or not kernel.GetVolumeInformationByHandleW(handle, None, 0, None, None, None,
                                                             filesystem, len(filesystem))):
            raise ConsoleError('Cannot verify database file identity', 'DATABASE_IDENTITY_UNAVAILABLE')
        modern = (int(extended.volume), int.from_bytes(bytes(extended.identifier), 'little'))
        if not modern[1] or not filesystem.value:
            raise ConsoleError('Incomplete database file identity', 'DATABASE_IDENTITY_UNAVAILABLE')
        identities = [modern]
        if filesystem.value.casefold() == 'ntfs':
            legacy = (int(classic.volume), (int(classic.index_high) << 32) | int(classic.index_low))
            if legacy[1] != modern[1] or legacy[0] != modern[0] & 0xffffffff:
                raise ConsoleError('Inconsistent database file identity', 'DATABASE_IDENTITY_UNAVAILABLE')
            if legacy != modern:
                identities.append(legacy)
        # ReFS has 128-bit IDs; do not accept a weaker 64-bit legacy alias there.
        return tuple(identities)
    finally:
        kernel.CloseHandle(handle)
