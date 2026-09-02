from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from shared.storage_preflight import (
    GIB,
    StorageCapacityError,
    StorageRequirement,
    build_storage_preflight,
    inspect_storage_requirements,
    is_sqlite_full_error,
)


def _usage(*, total_gib: int, free_gib: int):
    return SimpleNamespace(
        total=total_gib * GIB,
        used=(total_gib - free_gib) * GIB,
        free=free_gib * GIB,
    )


def test_same_volume_requirements_are_summed_before_reserve(tmp_path: Path):
    report = inspect_storage_requirements(
        [
            StorageRequirement("cache", tmp_path / "cache", 4 * GIB),
            StorageRequirement("workspace", tmp_path / "workspace", 7 * GIB),
        ],
        min_free_gb=10,
        min_free_percent=10,
        disk_usage=lambda _path: _usage(total_gib=100, free_gib=20),
    )

    assert report["ok"] is False
    [volume] = report["volumes"]
    assert volume["required_bytes"] == 11 * GIB
    assert volume["reserve_bytes"] == 10 * GIB
    assert volume["roles"] == ["cache", "workspace"]


def test_state_only_volume_uses_absolute_reserve_not_percentage(tmp_path: Path):
    report = inspect_storage_requirements(
        [StorageRequirement("pipeline_database", tmp_path / "pipeline.db")],
        min_free_gb=10,
        min_free_percent=10,
        disk_usage=lambda _path: _usage(total_gib=1000, free_gib=80),
    )

    assert report["ok"] is True
    [volume] = report["volumes"]
    assert volume["required_bytes"] == 0
    assert volume["reserve_bytes"] == 10 * GIB


def test_preflight_uses_exact_jpeg_bytes_and_mode_estimate(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.jpg").write_bytes(b"a" * 100)
    (source / "two.JPEG").write_bytes(b"b" * 300)
    (source / "ignored.png").write_bytes(b"c" * 500)

    report = build_storage_preflight(
        source_dir=source,
        database_path=tmp_path / "data" / "pipeline.db",
        logs_dir=tmp_path / "logs",
        checkpoint_dir=tmp_path / "checkpoints",
        upload_cache_root=tmp_path / "cache",
        qgis_staging_root=tmp_path / "qgis-staging",
        workspace_root=tmp_path / "workspaces",
        surveys_root=tmp_path / "surveys",
        temp_root=tmp_path / "temp",
        webodm_mode="both",
        min_free_gb=0,
        min_free_percent=0,
        disk_usage=lambda _path: _usage(total_gib=100, free_gib=100),
    )

    assert report["ok"] is True
    assert report["source_image_count"] == 2
    assert report["source_image_bytes"] == 400
    assert report["estimated_cache_bytes"] == 480
    assert report["estimated_qgis_staging_bytes"] == 800
    assert report["estimated_workspace_bytes"] == 20 * GIB
    [volume] = report["volumes"]
    assert "qgis_local_staging" in volume["roles"]
    assert volume["required_bytes"] == (20 * GIB) + 480 + 800


def test_qgis_only_resume_omits_completed_webodm_cache_estimate(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.jpg").write_bytes(b"a" * 400)

    report = build_storage_preflight(
        source_dir=source,
        database_path=tmp_path / "data" / "pipeline.db",
        logs_dir=tmp_path / "logs",
        checkpoint_dir=tmp_path / "checkpoints",
        upload_cache_root=tmp_path / "cache",
        qgis_staging_root=tmp_path / "qgis-staging",
        workspace_root=tmp_path / "workspaces",
        surveys_root=tmp_path / "surveys",
        temp_root=tmp_path / "temp",
        webodm_mode="task4",
        include_upload_cache=False,
        include_qgis_staging=True,
        include_workspace=True,
        min_free_gb=0,
        min_free_percent=0,
        disk_usage=lambda _path: _usage(total_gib=100, free_gib=100),
    )

    assert report["estimated_cache_bytes"] == 0
    assert report["estimated_qgis_staging_bytes"] == 800
    assert report["estimated_workspace_bytes"] == 20 * GIB
    assert report["include_upload_cache"] is False
    assert report["include_qgis_staging"] is True
    assert report["include_workspace"] is True


def test_capacity_failure_is_typed(tmp_path: Path):
    report = inspect_storage_requirements(
        [StorageRequirement("workspace", tmp_path, 2 * GIB)],
        min_free_gb=10,
        min_free_percent=10,
        disk_usage=lambda _path: _usage(total_gib=100, free_gib=11),
    )

    with pytest.raises(StorageCapacityError):
        from shared.storage_preflight import require_storage_capacity

        require_storage_capacity(report)


def test_sqlite_full_error_detection_is_specific():
    assert is_sqlite_full_error(sqlite3.OperationalError("database or disk is full"))
    assert not is_sqlite_full_error(sqlite3.OperationalError("database is locked"))
