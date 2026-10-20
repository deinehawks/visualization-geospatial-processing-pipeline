from datetime import datetime, timezone
import json

import pytest

from shared.qgis_tile_recovery import (
    QGISTileRecoveryError,
    expected_qgis_tile_directory,
    qgis_tile_rebuild_confirmation,
    quarantine_qgis_tile_directory,
    validate_qgis_tile_rebuild_request,
)


RUN_ID = '47cd8922-630c-4a22-90d0-8494669ecb80'
TASK_KEY = 'task4'


def confirmation() -> str:
    return qgis_tile_rebuild_confirmation(RUN_ID, TASK_KEY)


def test_request_requires_exact_run_task_confirmation():
    request = {'task_key': TASK_KEY, 'confirmation': confirmation()}
    assert validate_qgis_tile_rebuild_request(request, RUN_ID) == request

    with pytest.raises(QGISTileRecoveryError, match='exact confirmation'):
        validate_qgis_tile_rebuild_request(
            {'task_key': TASK_KEY, 'confirmation': 'wrong'},
            RUN_ID,
        )

    with pytest.raises(QGISTileRecoveryError, match='task2 or task4'):
        validate_qgis_tile_rebuild_request(
            {'task_key': 'task9', 'confirmation': 'irrelevant'},
            RUN_ID,
        )


def test_quarantine_moves_exact_tree_and_writes_atomic_manifest(tmp_path):
    staging = tmp_path / 'qgis-staging'
    active = expected_qgis_tile_directory(
        staging, RUN_ID, TASK_KEY, 'round-corners'
    )
    tile = active / '24' / '10' / '20.png'
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b'partial-tile')
    instant = datetime(2026, 9, 10, 2, 30, tzinfo=timezone.utc)

    result = quarantine_qgis_tile_directory(
        staging,
        active,
        RUN_ID,
        TASK_KEY,
        'round-corners',
        confirmation(),
        now=lambda: instant,
    )

    quarantined = staging / 'quarantine' / RUN_ID / TASK_KEY
    quarantined = (
        quarantined / '20260910T023000000000Z' / 'round-corners'
    )
    assert not active.exists()
    assert (quarantined / '24' / '10' / '20.png').read_bytes() == b'partial-tile'
    manifest = json.loads(
        (quarantined.parent / 'qgis-tile-rebuild.json').read_text()
    )
    assert manifest['file_count'] == 1
    assert manifest['total_bytes'] == len(b'partial-tile')
    assert result['quarantine_path'] == str(quarantined)


def test_quarantine_rejects_path_outside_exact_run_task_target(tmp_path):
    staging = tmp_path / 'qgis-staging'
    staging.mkdir()
    wrong = tmp_path / 'other' / 'round-corners'
    wrong.mkdir(parents=True)
    (wrong / 'tile.png').write_bytes(b'tile')

    with pytest.raises(QGISTileRecoveryError, match='exact configured target'):
        quarantine_qgis_tile_directory(
            staging,
            wrong,
            RUN_ID,
            TASK_KEY,
            'round-corners',
            confirmation(),
        )

    assert (wrong / 'tile.png').read_bytes() == b'tile'


def test_quarantine_rejects_symlink_like_active_target(monkeypatch, tmp_path):
    staging = tmp_path / 'qgis-staging'
    active = expected_qgis_tile_directory(
        staging, RUN_ID, TASK_KEY, 'round-corners'
    )
    active.mkdir(parents=True)
    real_is_symlink = type(active).is_symlink

    def mark_active_as_symlink(path):
        return path == active or real_is_symlink(path)

    monkeypatch.setattr(type(active), 'is_symlink', mark_active_as_symlink)
    with pytest.raises(QGISTileRecoveryError, match='real directory'):
        quarantine_qgis_tile_directory(
            staging,
            active,
            RUN_ID,
            TASK_KEY,
            'round-corners',
            confirmation(),
        )


def test_manifest_failure_rolls_quarantine_back(monkeypatch, tmp_path):
    staging = tmp_path / 'qgis-staging'
    active = expected_qgis_tile_directory(
        staging, RUN_ID, TASK_KEY, 'round-corners'
    )
    tile = active / '12' / '1' / '2.png'
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b'preserve-me')
    real_write = type(tile).write_text

    def fail_manifest(path, *args, **kwargs):
        if 'qgis-tile-rebuild.json' in path.name:
            raise OSError('controlled manifest failure')
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(type(tile), 'write_text', fail_manifest)
    with pytest.raises(QGISTileRecoveryError, match='rolled back'):
        quarantine_qgis_tile_directory(
            staging,
            active,
            RUN_ID,
            TASK_KEY,
            'round-corners',
            confirmation(),
        )

    assert tile.read_bytes() == b'preserve-me'
