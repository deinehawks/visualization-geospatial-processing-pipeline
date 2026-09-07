from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Callable, Iterable, Mapping

from shared.source_images import discover_source_images, image_selection_mode


GIB = 1024 ** 3
WORKSPACE_MULTIPLIERS = {'task4': 2.0, 'task2': 3.0, 'both': 4.0}


class StorageCapacityError(RuntimeError):
    def __init__(self, message: str, *, report: Mapping[str, object]) -> None:
        super().__init__(message)
        self.report = dict(report)


def is_sqlite_full_error(exc: BaseException) -> bool:
    error_code = getattr(exc, "sqlite_errorcode", None)
    sqlite_full_code = getattr(sqlite3, "SQLITE_FULL", 13)
    return error_code == sqlite_full_code or (
        isinstance(exc, sqlite3.Error)
        and "database or disk is full" in str(exc).casefold()
    )


@dataclass(frozen=True)
class StorageRequirement:
    role: str
    path: Path
    required_bytes: int = 0
    min_free_gb: int | None = None
    min_free_percent: int | None = None


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


def estimate_jpeg_bytes(
    source_dir: Path,
    *,
    rgb_only: bool = False,
) -> tuple[int, int]:
    source = Path(source_dir)
    files = discover_source_images(source, rgb_only=rgb_only)
    if not files:
        expected = 'images ending in _D.JPG' if rgb_only else 'JPG/JPEG images'
        raise FileNotFoundError(
            f'No {expected} found for storage planning: {source}'
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
                'min_free_gb': 0,
                'write_min_free_percent': 0,
            },
        )
        group['roles'].append(str(requirement.role))
        group['paths'].append(str(target))
        group['required_bytes'] = int(group['required_bytes']) + max(
            0,
            int(requirement.required_bytes),
        )
        effective_min_gb = (
            int(min_free_gb)
            if requirement.min_free_gb is None
            else int(requirement.min_free_gb)
        )
        effective_min_percent = (
            int(min_free_percent)
            if requirement.min_free_percent is None
            else int(requirement.min_free_percent)
        )
        group['min_free_gb'] = max(
            int(group['min_free_gb']),
            effective_min_gb,
        )
        if int(requirement.required_bytes) > 0:
            group['write_min_free_percent'] = max(
                int(group['write_min_free_percent']),
                effective_min_percent,
            )

    volumes: list[dict[str, object]] = []
    for key in sorted(grouped):
        group = grouped[key]
        usage = disk_usage(str(group['probe_path']))
        total = int(getattr(usage, 'total'))
        used = int(getattr(usage, 'used'))
        free = int(getattr(usage, 'free'))
        required = int(group['required_bytes'])
        percentage_reserve = int(
            total * (int(group['write_min_free_percent']) / 100.0)
        )
        reserve = max(int(group['min_free_gb']) * GIB, percentage_reserve)
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
    include_upload_cache: bool = True,
    include_qgis_staging: bool = True,
    include_workspace: bool = True,
    include_published_outputs: bool = True,
    include_task4_all_assets_zip: bool = False,
    min_free_gb: int = 10,
    min_free_percent: int = 5,
    published_min_free_gb: int = 10,
    published_min_free_percent: int = 0,
    rgb_only: bool = False,
    disk_usage: Callable[[str], object] = shutil.disk_usage,
) -> dict:
    mode = str(webodm_mode).strip().lower()
    if mode not in WORKSPACE_MULTIPLIERS:
        raise ValueError(f'Unsupported WebODM mode for storage planning: {mode}')

    source_bytes, image_count = estimate_jpeg_bytes(
        source_dir,
        rgb_only=rgb_only,
    )
    cache_bytes = int(source_bytes * 1.2) if include_upload_cache else 0
    qgis_staging_bytes = int(source_bytes * 2.0) if include_qgis_staging else 0
    task4_all_assets_zip_bytes = (
        source_bytes if include_task4_all_assets_zip else 0
    )
    workspace_bytes = (
        (
            max(
                20 * GIB,
                int(source_bytes * WORKSPACE_MULTIPLIERS[mode]),
            )
            + task4_all_assets_zip_bytes
        )
        if include_workspace
        else 0
    )
    published_bytes = (
        source_bytes + task4_all_assets_zip_bytes
        if include_published_outputs
        else 0
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
            StorageRequirement(
                'published_outputs',
                Path(surveys_root),
                published_bytes,
                min_free_gb=published_min_free_gb,
                min_free_percent=published_min_free_percent,
            ),
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
            'image_selection_mode': image_selection_mode(rgb_only=rgb_only),
            'estimated_cache_bytes': cache_bytes,
            'estimated_qgis_staging_bytes': qgis_staging_bytes,
            'estimated_task4_all_assets_zip_bytes': (
                task4_all_assets_zip_bytes
            ),
            'estimated_workspace_bytes': workspace_bytes,
            'estimated_published_output_bytes': published_bytes,
            'webodm_mode': mode,
            'include_upload_cache': bool(include_upload_cache),
            'include_qgis_staging': bool(include_qgis_staging),
            'include_workspace': bool(include_workspace),
            'include_published_outputs': bool(include_published_outputs),
            'include_task4_all_assets_zip': bool(
                include_task4_all_assets_zip
            ),
            'min_free_gb': int(min_free_gb),
            'min_free_percent': int(min_free_percent),
            'published_min_free_gb': int(published_min_free_gb),
            'published_min_free_percent': int(published_min_free_percent),
        }
    )
    return report


def format_storage_report(report: Mapping[str, object]) -> str:
    result = 'PASS' if report.get('ok') else 'FAIL'
    image_count = report.get('source_image_count', 0)
    image_gib = int(report.get('source_image_bytes', 0)) / GIB
    mode = report.get('webodm_mode', 'unknown')
    task4_zip_enabled = bool(report.get('include_task4_all_assets_zip'))
    task4_zip_gib = int(
        report.get('estimated_task4_all_assets_zip_bytes', 0)
    ) / GIB
    lines = [
        '===== STORAGE PREFLIGHT =====',
        f'Result: {result}',
        f'Source images: {image_count} ({image_gib:.2f} GiB)',
        f'WebODM mode: {mode}',
        (
            'Task 4 full ODM ZIP: enabled '
            f'(estimated {task4_zip_gib:.2f} GiB)'
            if task4_zip_enabled
            else 'Task 4 full ODM ZIP: disabled'
        ),
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
