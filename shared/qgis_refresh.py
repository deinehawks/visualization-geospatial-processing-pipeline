"""Explicit fresh QGIS generations and recoverable, non-deleting publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import uuid
import zlib


class QGISRefreshError(RuntimeError):
    pass


def refresh_confirmation(run_id: str, task_key: str = 'task4') -> str:
    return f'REFRESH QGIS {task_key} FOR RUN {run_id}'


def validate_refresh_request(request, run_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise QGISRefreshError('Invalid refresh run ID')
    if request.get('task_key') != 'task4':
        raise QGISRefreshError('Fresh QGIS refresh currently supports task4 only')
    if request.get('confirmation') != refresh_confirmation(run_id):
        raise QGISRefreshError('Exact confirmation required: ' + refresh_confirmation(run_id))
    return {'task_key': 'task4', 'confirmation': request['confirmation']}


def safe_path(root: Path, path: Path) -> Path:
    root, path = Path(root).absolute(), Path(path).absolute()
    if root == Path(root.anchor) or root == Path(__file__).resolve().parents[1]:
        raise QGISRefreshError('Refusing a filesystem or repository root')
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise QGISRefreshError('Refresh path escapes its approved root') from exc
    current = root
    for part in ('', *parts):
        current = current / part if part else current
        if part in ('.', '..'):
            raise QGISRefreshError('Unsafe refresh path component')
        if current.is_symlink() or (current.exists() and getattr(current.lstat(), 'st_file_attributes', 0) & 0x400):
            raise QGISRefreshError('Refresh paths must not contain symlinks or junctions')
    if not path.resolve().is_relative_to(root.resolve()):
        raise QGISRefreshError('Resolved refresh path escapes its root')
    return path


def write_record(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_png(path):
    # Verify chunk CRCs and the complete zlib stream, not merely the extension.
    if path.stat().st_size > 32 * 1024 * 1024:
        raise QGISRefreshError('Unexpectedly large PNG tile: ' + str(path))
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise QGISRefreshError('Unreadable PNG tile: ' + str(path))
    offset, seen_header, seen_data, inflated = 8, False, False, 0
    decoder = zlib.decompressobj()
    while offset + 12 <= len(data):
        length = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            break
        payload = data[offset + 8:end - 4]
        if zlib.crc32(kind + payload) & 0xffffffff != struct.unpack('>I', data[end - 4:end])[0]:
            break
        if not seen_header:
            if (kind != b'IHDR' or length != 13
                    or any(not 0 < n <= 4096 for n in struct.unpack('>II', payload[:8]))):
                break
            seen_header = True
        if kind == b'IDAT':
            seen_data = True
            try:
                inflated += len(decoder.decompress(payload, 16 * 1024 * 1024))
            except zlib.error as exc:
                raise QGISRefreshError('Invalid PNG tile compression: ' + str(path)) from exc
            if decoder.unconsumed_tail or inflated > 64 * 1024 * 1024:
                break
        if kind == b'IEND':
            if length == 0 and end == len(data) and seen_data and decoder.eof:
                return
            break
        offset = end
    raise QGISRefreshError('Truncated or invalid PNG tile: ' + str(path))


def inventory(path, *, png=False):
    path = Path(path)
    if not path.exists():
        return None
    files = []
    if path.is_dir():
        safe_path(path.parent, path)
        for current, directories, names in os.walk(path, followlinks=False):
            for name in (*directories, *names):
                safe_path(path, Path(current) / name)
            files.extend(Path(current) / name for name in names)
        files.sort()
    else:
        files = [path]
    digest, count, size, tiles = hashlib.sha256(), 0, 0, 0
    for item in files:
        safe_path(path if path.is_dir() else path.parent, item)
        if not item.is_file():
            continue
        relative = item.relative_to(path).as_posix() if path.is_dir() else item.name
        if png and item.suffix.lower() == '.png':
            _verify_png(item)
            tiles += 1
        count += 1
        size += item.stat().st_size
        digest.update(relative.encode('utf-8') + b'\0' + file_digest(item).encode('ascii'))
    if png and not tiles:
        raise QGISRefreshError('Fresh tile generation produced no PNG tiles')
    return {'file_count': count, 'total_bytes': size, 'sha256': digest.hexdigest()}


def reject_pending_refresh(workspace):
    parent = safe_path(workspace, Path(workspace) / 'qgis-refresh')
    if parent.exists():
        for entry in parent.iterdir():
            record = safe_path(workspace, entry / 'refresh.json')
            if record.exists():
                try:
                    value = json.loads(record.read_text(encoding='utf-8'))
                except (ValueError, OSError) as exc:
                    raise QGISRefreshError('Unreadable refresh evidence: ' + str(record)) from exc
                if not isinstance(value, dict) or value.get('version') != 1:
                    raise QGISRefreshError('Invalid refresh evidence: ' + str(record))
                if value.get('state') not in {'building', 'preparing', 'failed', 'rolled_back', 'completed'}:
                    raise QGISRefreshError('An earlier QGIS refresh needs reconciliation: ' + str(record))


def new_generation(workspace, run_id):
    workspace = Path(workspace)
    if workspace.name != run_id:
        raise QGISRefreshError('Refresh workspace must match the exact run ID')
    reject_pending_refresh(workspace)
    generation = safe_path(workspace, workspace / 'qgis-refresh' / uuid.uuid4().hex)
    generation.mkdir(parents=True, exist_ok=False)
    write_record(generation / 'refresh.json', {'version': 1, 'run_id': run_id, 'task_key': 'task4', 'state': 'building'})
    return generation


def publish_refresh(generation, targets, *, check_control=lambda: None,
                    before_activation=lambda: None, replace=None):
    """Stage all copies first. Keep backups on success and roll back on failure.

    Targets are (approved_root, generated_source_or_None, exact_target). A None
    source invalidates a stale local cache by retaining it as a backup. The
    caller holds exclusive execution and survey publication ownership.
    """
    replace = replace or (lambda source, target: source.replace(target))
    journal = generation / 'refresh.json'
    record = json.loads(journal.read_text(encoding='utf-8'))
    if record.get('state') != 'building' or generation.parent.name != 'qgis-refresh':
        raise QGISRefreshError('Invalid fresh generation ownership')
    entries = []
    record.update(state='preparing', entries=entries)
    for root, source, target in targets:
        target = safe_path(root, target)
        if target == Path(root).absolute():
            raise QGISRefreshError('Refusing replacement of an approved root')
        for previous in entries:
            other = Path(previous['target'])
            if target == other or target.is_relative_to(other) or other.is_relative_to(target):
                raise QGISRefreshError('Refresh targets must be distinct and non-nested')
        if source is not None:
            source = safe_path(generation, source)
            if not source.exists():
                raise QGISRefreshError('Generated output missing: ' + str(source))
        pending = target.with_name('.' + target.name + '.qgis-refresh-' + generation.name + '.new')
        backup = target.with_name('.' + target.name + '.qgis-refresh-' + generation.name + '.old')
        if pending.exists() or backup.exists():
            raise QGISRefreshError('Refresh evidence already exists; refusing reuse')
        entries.append({'target': str(target), 'pending': str(pending), 'backup': str(backup),
                        'source': str(source) if source is not None else None, 'old': inventory(target),
                        'new': inventory(source) if source is not None else None,
                        'new_content_sha256': file_digest(source) if source is not None and source.is_file() else None})
    write_record(journal, record)
    moved, installed = [], []
    try:
        for entry in entries:
            check_control()
            if entry['source'] is not None:
                source, pending = Path(entry['source']), Path(entry['pending'])
                pending.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, pending)
                else:
                    shutil.copy2(source, pending)
                # File names differ for staged files; digest their contents directly.
                if (inventory(pending)['total_bytes'] != entry['new']['total_bytes']
                        or (source.is_file() and entry['new_content_sha256'] != file_digest(pending))
                        or (source.is_dir() and inventory(pending) != entry['new'])):
                    raise QGISRefreshError('Refresh copy verification failed')
        check_control()
        before_activation()
        for entry in entries:
            if inventory(Path(entry['target'])) != entry['old']:
                raise QGISRefreshError('Active output changed during refresh; refusing publication')
        record['state'] = 'publishing'
        write_record(journal, record)
        for entry in entries:
            target, backup, pending = (Path(entry[k]) for k in ('target', 'backup', 'pending'))
            if entry['old'] is not None:
                replace(target, backup)
                moved.append(entry)
            if entry['source'] is not None:
                replace(pending, target)
                installed.append(entry)
        record['state'] = 'published'
        write_record(journal, record)
    except BaseException:
        try:
            for entry in reversed(installed):
                target = Path(entry['target'])
                current = inventory(target)
                if (current is None or current['total_bytes'] != entry['new']['total_bytes']
                        or (entry['new_content_sha256'] is not None
                            and file_digest(target) != entry['new_content_sha256'])
                        or (entry['new_content_sha256'] is None and current != entry['new'])):
                    raise QGISRefreshError('Published output changed before rollback; manual recovery required')
                replace(target, target.with_name('.' + target.name + '.qgis-refresh-' + generation.name + '.failed'))
            for entry in reversed(moved):
                if Path(entry['target']).exists():
                    raise QGISRefreshError('Rollback destination is occupied; manual recovery required')
                replace(Path(entry['backup']), Path(entry['target']))
            record['state'] = 'rolled_back'
            write_record(journal, record)
        except BaseException as rollback_error:
            record['state'] = 'requires_recovery'
            write_record(journal, record)
            raise QGISRefreshError('Refresh rollback incomplete; preserve journal: ' + str(journal)) from rollback_error
        raise
    return {'manifest_path': str(journal), 'generation': str(generation), 'backups':
            [entry['backup'] for entry in entries if entry['old'] is not None]}


def acknowledge_refresh(path):
    path = Path(path)
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('state') != 'published':
        raise QGISRefreshError('Refresh is not awaiting a stage-completion receipt')
    record['state'] = 'completed'
    write_record(path, record)
