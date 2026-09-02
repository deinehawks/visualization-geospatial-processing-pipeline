from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Callable, Iterable, Mapping


GIB = 1024 ** 3
JPEG_SUFFIXES = {'.jpg', '.jpeg'}
WORKSPACE_MULTIPLIERS = {'task4': 2.0, 'task2': 3.0, 'both': 4.0}


class StorageCapacityError(RuntimeError):
    def __init__(self, message: str, *, report: Mapping[str, object]) -> None:
        super().__init__(message)
        self.report = dict(report)


def is_sqlite_full_error(exc: BaseException) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    return error_code == sqlite3.SQLITE_FULL or (
        isinstance(exc, sqlite3.Error)
        and "database or disk is full" in str(exc).casefold()
    )


@dataclass(frozen=True)
class StorageRequirement:
    role: str
    path: Path
    required_bytes: int = 0


def _nearest_existing_path(path: Path) -> Path:
    candidate = Path(path).expanduser().resolve(strict=False)
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise FileNotFoundError(
                f'No existing parent was found for storage path: {path}'
            )
        candidate = parent
    return candidate


def _volume_key(path: Path) -> str:
    resolved = Path(path).resolve(strict=False)
    if os.name == 'nt':
        if not resolved.anchor:
            raise ValueError(f'Path has no Windows volume anchor: {path}')
        return resolved.anchor.casefold()
    return f'device:{resolved.stat().st_dev}'


def estimate_jpeg_bytes(source_dir: Path) -> tuple[int, int]:
    source = Path(source_dir)
    if not source.is_dir():
        raise FileNotFoundError(f'Source directory not found: {source}')
    files = sorted(
        (
            path
            for path in source.rglob('*')
            if path.is_file() and path.suffix.lower() in JPEG_SUFFIXES
        ),
        key=lambda path: str(path).casefold(),
    )
    if not files:
        raise FileNotFoundError(
            f'No JPG/JPEG images found for storage planning: {source}'
        )
    return sum(path.stat().st_size for path in files), len(files)


def inspect_storage_requirements(
    requirements: Iterable[StorageRequirement],
    *,
    min_free_gb: int,
    min_free_percent: int,
    disk_usage: Callable[[str], object] = shutil.disk_usage,
) -> dict:
    grouped: dict[str, dict[str, object]] = {}
    for requirement in requirements:
        target = Path(requirement.path).expanduser().resolve(strict=False)
        probe = _nearest_existing_path(target)
        key = _volume_key(probe)
        group = grouped.setdefault(
            key,
            {
                'volume': key,
                'probe_path': str(probe),
                'roles': [],
                'paths': [],
                'required_bytes': 0,
            },
        )
        group['roles'].append(str(requirement.role))
        group['paths'].append(str(target))
        group['required_bytes'] = int(group['required_bytes']) + max(
            0,
            int(requirement.required_bytes),
        )

    volumes: list[dict[str, object]] = []
    for key in sorted(grouped):
        group = grouped[key]
        usage = disk_usage(str(group['probe_path']))
        total = int(getattr(usage, 'total'))
        used = int(getattr(usage, 'used'))
        free = int(getattr(usage, 'free'))
        reserve = max(
            int(min_free_gb) * GIB,
            int(total * (int(min_free_percent) / 100.0)),
        )
        required = int(group['required_bytes'])
        volumes.append(
            {
                **group,
                'roles': sorted(set(group['roles'])),
                'paths': sorted(set(group['paths'])),
                'total_bytes': total,
                'used_bytes': used,
                'free_bytes': free,
                'reserve_bytes': reserve,
                'required_with_reserve_bytes': required + reserve,
                'ok': free >= required + reserve,
            }
        )
    return {
        'ok': all(bool(volume['ok']) for volume in volumes),
        'volumes': volumes,
    }


def build_storage_preflight(
    *,
    source_dir: Path,
    database_path: Path,
    logs_dir: Path,
    checkpoint_dir: Path,
    upload_cache_root: Path,
    qgis_staging_root: Path,
    workspace_root: Path,
    surveys_root: Path,
    temp_root: Path,
    webodm_mode: str,
    min_free_gb: int = 10,
    min_free_percent: int = 10,
    disk_usage: Callable[[str], object] = shutil.disk_usage,
) -> dict:
    mode = str(webodm_mode).strip().lower()
    if mode not in WORKSPACE_MULTIPLIERS:
        raise ValueError(f'Unsupported WebODM mode for storage planning: {mode}')

    source_bytes, image_count = estimate_jpeg_bytes(source_dir)
    cache_bytes = int(source_bytes * 1.2)
    qgis_staging_bytes = int(source_bytes * 2.0)
    workspace_bytes = max(
        20 * GIB,
        int(source_bytes * WORKSPACE_MULTIPLIERS[mode]),
    )
    report = inspect_storage_requirements(
        [
            StorageRequirement('pipeline_database', Path(database_path)),
            StorageRequirement('logs', Path(logs_dir)),
            StorageRequirement('checkpoints', Path(checkpoint_dir)),
            StorageRequirement('upload_cache', Path(upload_cache_root), cache_bytes),
            StorageRequirement(
                'qgis_local_staging',
                Path(qgis_staging_root),
                qgis_staging_bytes,
            ),
            StorageRequirement('run_workspace', Path(workspace_root), workspace_bytes),
            StorageRequirement('published_outputs', Path(surveys_root)),
            StorageRequirement('system_temp', Path(temp_root)),
        ],
        min_free_gb=min_free_gb,
        min_free_percent=min_free_percent,
        disk_usage=disk_usage,
    )
    report.update(
        {
            'source_dir': str(Path(source_dir).resolve(strict=False)),
            'source_image_count': image_count,
            'source_image_bytes': source_bytes,
            'estimated_cache_bytes': cache_bytes,
            'estimated_qgis_staging_bytes': qgis_staging_bytes,
            'estimated_workspace_bytes': workspace_bytes,
            'webodm_mode': mode,
            'min_free_gb': int(min_free_gb),
            'min_free_percent': int(min_free_percent),
        }
    )
    return report


def format_storage_report(report: Mapping[str, object]) -> str:
    result = 'PASS' if report.get('ok') else 'FAIL'
    image_count = report.get('source_image_count', 0)
    image_gib = int(report.get('source_image_bytes', 0)) / GIB
    mode = report.get('webodm_mode', 'unknown')
    lines = [
        '===== STORAGE PREFLIGHT =====',
        f'Result: {result}',
        f'Source images: {image_count} ({image_gib:.2f} GiB)',
        f'WebODM mode: {mode}',
    ]
    for value in report.get('volumes', []):
        volume = dict(value)
        status = 'PASS' if volume.get('ok') else 'FAIL'
        roles = ', '.join(volume.get('roles', []))
        free_gib = int(volume.get('free_bytes', 0)) / GIB
        writes_gib = int(volume.get('required_bytes', 0)) / GIB
        reserve_gib = int(volume.get('reserve_bytes', 0)) / GIB
        volume_name = volume.get('volume')
        lines.extend(
            [
                '',
                f'Volume: {volume_name}',
                f'Roles: {roles}',
                f'Free: {free_gib:.2f} GiB',
                f'Estimated writes: {writes_gib:.2f} GiB',
                f'Required reserve: {reserve_gib:.2f} GiB',
                f'Status: {status}',
            ]
        )
    lines.append('=============================')
    return chr(10).join(lines)


def require_storage_capacity(report: Mapping[str, object]) -> None:
    if report.get('ok'):
        return
    raise StorageCapacityError(
        format_storage_report(report),
        report=report,
    )


def check_path_capacity(
    *,
    role: str,
    path: Path,
    required_bytes: int,
    min_free_gb: int,
    min_free_percent: int,
    disk_usage: Callable[[str], object] = shutil.disk_usage,
) -> dict:
    report = inspect_storage_requirements(
        [StorageRequirement(role, path, required_bytes)],
        min_free_gb=min_free_gb,
        min_free_percent=min_free_percent,
        disk_usage=disk_usage,
    )
    require_storage_capacity(report)
    return report
