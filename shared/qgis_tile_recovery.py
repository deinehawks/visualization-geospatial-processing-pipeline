from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Callable
import uuid


QGIS_TILE_TASK_KEYS = frozenset({'task2', 'task4'})
QGIS_TILE_MODES = frozenset({'round-corners', 'soft-corners'})
QGIS_TILE_RECOVERY_MANIFEST = 'qgis-tile-rebuild.json'


class QGISTileRecoveryError(RuntimeError):
    pass


def qgis_tile_rebuild_confirmation(run_id: str, task_key: str) -> str:
    task = str(task_key).strip().lower()
    return f'REBUILD QGIS TILES {task} FOR RUN {run_id}'


def validate_qgis_tile_rebuild_request(
    request: dict[str, Any],
    run_id: str,
) -> dict[str, str]:
    task = str(request.get('task_key') or '').strip().lower()
    if task not in QGIS_TILE_TASK_KEYS:
        raise QGISTileRecoveryError(
            'QGIS tile rebuild supports only task2 or task4.'
        )
    confirmation = str(request.get('confirmation') or '')
    expected = qgis_tile_rebuild_confirmation(run_id, task)
    if confirmation != expected:
        raise QGISTileRecoveryError(
            'QGIS tile rebuild requires exact confirmation: '
            f'{expected!r}'
        )
    return {'task_key': task, 'confirmation': confirmation}


def expected_qgis_tile_directory(
    staging_root: Path,
    run_id: str,
    task_key: str,
    tile_mode: str,
) -> Path:
    return (
        Path(staging_root)
        / 'tiles'
        / run_id
        / task_key
        / 'tiles'
        / tile_mode
    )


def quarantine_qgis_tile_directory(
    staging_root: Path,
    active_directory: Path,
    run_id: str,
    task_key: str,
    tile_mode: str,
    confirmation: str,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    request = validate_qgis_tile_rebuild_request(
        {'task_key': task_key, 'confirmation': confirmation},
        run_id,
    )
    task = request['task_key']
    mode = str(tile_mode).strip().lower()
    if mode not in QGIS_TILE_MODES:
        raise QGISTileRecoveryError(
            f'Unsupported QGIS tile mode: {tile_mode!r}'
        )

    root = Path(staging_root)
    if not root.exists() or not root.is_dir():
        raise QGISTileRecoveryError(
            f'Configured QGIS staging root does not exist: {root}'
        )
    if root.is_symlink():
        raise QGISTileRecoveryError(
            f'QGIS staging root must not be a symlink: {root}'
        )
    resolved_root = root.resolve(strict=True)
    if resolved_root == Path(resolved_root.anchor):
        raise QGISTileRecoveryError(
            'Refusing a filesystem root as QGIS staging recovery root.'
        )

    expected = expected_qgis_tile_directory(
        resolved_root, run_id, task, mode
    )
    active = Path(active_directory)
    if not active.exists():
        raise QGISTileRecoveryError(
            f'No active QGIS tile directory exists: {active}'
        )
    if not active.is_dir() or active.is_symlink():
        raise QGISTileRecoveryError(
            f'QGIS tile target must be a real directory: {active}'
        )
    resolved_active = active.resolve(strict=True)
    if resolved_active != expected:
        raise QGISTileRecoveryError(
            'Refusing recovery outside the exact configured target. '
            f'Expected {expected}, got {resolved_active}.'
        )

    file_count = 0
    total_bytes = 0
    for current_root, dir_names, file_names in os.walk(resolved_active):
        current = Path(current_root)
        for name in [*dir_names, *file_names]:
            if (current / name).is_symlink():
                raise QGISTileRecoveryError(
                    'Refusing a tile tree containing symlinks: '
                    f'{current / name}'
                )
        for name in file_names:
            candidate = current / name
            file_count += 1
            total_bytes += candidate.stat().st_size

    timestamp = (now or (lambda: datetime.now(timezone.utc)))()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    stamp = timestamp.strftime('%Y%m%dT%H%M%S%fZ')
    recorded_at = timestamp.isoformat().replace('+00:00', 'Z')
    quarantine_parent = (
        resolved_root / 'quarantine' / run_id / task / stamp
    )
    quarantine_directory = quarantine_parent / mode
    if quarantine_parent.exists():
        raise QGISTileRecoveryError(
            f'QGIS tile quarantine already exists: {quarantine_parent}'
        )
    quarantine_parent.mkdir(parents=True, exist_ok=False)

    manifest = quarantine_parent / QGIS_TILE_RECOVERY_MANIFEST
    temporary = manifest.with_name(
        f'.{manifest.name}.{uuid.uuid4().hex}.tmp'
    )
    payload: dict[str, Any] = {
        'version': 1,
        'event': 'qgis_tile_rebuild_quarantined',
        'run_id': run_id,
        'task_key': task,
        'tile_mode': mode,
        'original_path': str(resolved_active),
        'quarantine_path': str(quarantine_directory),
        'confirmation': confirmation,
        'file_count': file_count,
        'total_bytes': total_bytes,
        'quarantined_at': recorded_at,
    }

    moved = False
    try:
        resolved_active.replace(quarantine_directory)
        moved = True
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )
        temporary.replace(manifest)
    except Exception as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if moved and not resolved_active.exists():
            try:
                quarantine_directory.replace(resolved_active)
            except OSError as rollback_exc:
                raise QGISTileRecoveryError(
                    'Quarantine metadata failed and rollback failed; '
                    f'manual recovery is required from {quarantine_directory}.'
                ) from rollback_exc
        raise QGISTileRecoveryError(
            'QGIS tile quarantine was rolled back because its recovery '
            'metadata could not be written.'
        ) from exc

    return {**payload, 'manifest_path': str(manifest)}
