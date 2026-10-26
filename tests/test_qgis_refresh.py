import json
from pathlib import Path
import struct
import zlib

import pytest

from shared import qgis_refresh as refresh


RUN = 'refresh-run'


def png_bytes():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b''))


@pytest.fixture
def outputs(tmp_path):
    sentinel = tmp_path / '.test-owned'
    sentinel.write_text('retain')
    workspace, published = tmp_path / RUN, tmp_path / 'survey'
    workspace.mkdir()
    published.mkdir()
    generation = refresh.new_generation(workspace, RUN)
    source = generation / 'clip.tif'
    source.write_bytes(b'new-raster')
    tiles = generation / 'tiles'
    (tiles / '12').mkdir(parents=True)
    (tiles / '12' / '1.png').write_bytes(png_bytes())
    target = published / 'clip.tif'
    target.write_bytes(b'old-raster')
    target_tiles = published / 'task4'
    target_tiles.mkdir()
    (target_tiles / 'old.png').write_bytes(b'old-tile')
    task2 = published / 'task2'
    task2.mkdir()
    (task2 / 'keep.png').write_bytes(b'untouched-task2')
    yield workspace, published, generation, source, tiles, target, target_tiles
    assert sentinel.read_text() == 'retain'
    assert (task2 / 'keep.png').read_bytes() == b'untouched-task2'


@pytest.mark.parametrize('task,phrase', [('task2', 'x'), ('task4', 'wrong'), ('task4', refresh.refresh_confirmation('other'))])
def test_confirmation_is_run_and_task_scoped(task, phrase):
    with pytest.raises(refresh.QGISRefreshError):
        refresh.validate_refresh_request({'task_key': task, 'confirmation': phrase}, RUN)


def test_confirmation_accepts_only_explicit_task4_request():
    request = {'task_key': 'task4', 'confirmation': refresh.refresh_confirmation(RUN)}
    assert refresh.validate_refresh_request(request, RUN) == request
    with pytest.raises(refresh.QGISRefreshError):
        refresh.validate_refresh_request(request, '../outside')


def test_fresh_publication_retains_old_outputs_and_audit(outputs):
    workspace, root, gen, source, tiles, target, target_tiles = outputs
    refresh.inventory(tiles, png=True)
    result = refresh.publish_refresh(gen, [(root, source, target), (root, tiles, target_tiles)])
    assert target.read_bytes() == b'new-raster'
    assert (target_tiles / '12' / '1.png').read_bytes() == png_bytes()
    assert len(result['backups']) == 2
    assert Path(result['backups'][0]).read_bytes() == b'old-raster'
    assert (Path(result['backups'][1]) / 'old.png').read_bytes() == b'old-tile'
    with pytest.raises(refresh.QGISRefreshError, match='reconciliation'):
        refresh.reject_pending_refresh(workspace)
    refresh.acknowledge_refresh(result['manifest_path'])
    refresh.reject_pending_refresh(workspace)
    assert json.loads(Path(result['manifest_path']).read_text())['state'] == 'completed'


def test_publish_failure_rolls_back_all_previous_targets(outputs):
    _, root, gen, source, tiles, target, target_tiles = outputs
    def replace(src, dst):
        if src.suffix == '.new' and dst == target_tiles:
            raise OSError('controlled activation failure')
        src.replace(dst)
    with pytest.raises(OSError, match='controlled'):
        refresh.publish_refresh(gen, [(root, source, target), (root, tiles, target_tiles)], replace=replace)
    assert target.read_bytes() == b'old-raster'
    assert (target_tiles / 'old.png').read_bytes() == b'old-tile'
    assert json.loads((gen / 'refresh.json').read_text())['state'] == 'rolled_back'
    assert list(root.glob('*.failed')) or list(root.glob('.*.failed'))


def test_rollback_failure_is_preserved_and_blocks_retry(outputs):
    workspace, root, gen, source, tiles, target, target_tiles = outputs
    def replace(src, dst):
        if (src.suffix == '.new' and dst == target_tiles) or src.suffix == '.old':
            raise OSError('controlled rollback failure')
        src.replace(dst)
    with pytest.raises(refresh.QGISRefreshError, match='rollback incomplete'):
        refresh.publish_refresh(gen, [(root, source, target), (root, tiles, target_tiles)], replace=replace)
    with pytest.raises(refresh.QGISRefreshError, match='reconciliation'):
        refresh.new_generation(workspace, RUN)
    assert json.loads((gen / 'refresh.json').read_text())['state'] == 'requires_recovery'
    assert list(root.glob('.*.old'))


def test_copy_failure_does_not_touch_active_targets(monkeypatch, outputs):
    _, root, gen, source, tiles, target, target_tiles = outputs
    monkeypatch.setattr(refresh.shutil, 'copytree', lambda *a, **k: (_ for _ in ()).throw(OSError('copy failed')))
    with pytest.raises(OSError, match='copy failed'):
        refresh.publish_refresh(gen, [(root, source, target), (root, tiles, target_tiles)])
    assert target.read_bytes() == b'old-raster'
    assert (target_tiles / 'old.png').read_bytes() == b'old-tile'


def test_pending_copy_is_verified_before_activation(monkeypatch, outputs):
    _, root, gen, source, _, target, _ = outputs
    monkeypatch.setattr(refresh.shutil, 'copy2', lambda src, dst: Path(dst).write_bytes(b'bad-copy'))
    with pytest.raises(refresh.QGISRefreshError, match='copy verification'):
        refresh.publish_refresh(gen, [(root, source, target)])
    assert target.read_bytes() == b'old-raster'


def test_pause_before_publish_keeps_active_outputs(outputs):
    _, root, gen, source, _, target, _ = outputs
    def pause():
        raise RuntimeError('__PIPELINE_PAUSED__')
    with pytest.raises(RuntimeError, match='__PIPELINE_PAUSED__'):
        refresh.publish_refresh(gen, [(root, source, target)], check_control=pause)
    assert target.read_bytes() == b'old-raster'


@pytest.mark.parametrize('contents', [b'', b'not-png', png_bytes()[:-5], png_bytes()[:-20] + b'corrupt'])
def test_corrupt_png_rejected(tmp_path, contents):
    tile = tmp_path / 'tiles' / '12' / '1.png'
    tile.parent.mkdir(parents=True)
    tile.write_bytes(contents)
    with pytest.raises(refresh.QGISRefreshError, match='PNG'):
        refresh.inventory(tile.parent.parent, png=True)


def test_no_png_tiles_is_not_success(tmp_path):
    with pytest.raises(refresh.QGISRefreshError, match='no PNG'):
        refresh.inventory(tmp_path, png=True)


def test_reject_outside_root_and_nested_targets(outputs):
    _, root, gen, source, tiles, target, _ = outputs
    with pytest.raises(refresh.QGISRefreshError, match='escapes'):
        refresh.publish_refresh(gen, [(root, source, root.parent / 'outside.tif')])
    with pytest.raises(refresh.QGISRefreshError, match='non-nested'):
        refresh.publish_refresh(gen, [(root, tiles, root / 'a'), (root, source, root / 'a' / 'b')])
    assert target.read_bytes() == b'old-raster'


def test_reject_symlink_like_targets(monkeypatch, outputs):
    _, root, gen, source, _, target, _ = outputs
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda p: p == target or original(p))
    with pytest.raises(refresh.QGISRefreshError, match='symlinks'):
        refresh.publish_refresh(gen, [(root, source, target)])


def test_stale_cache_is_archived_not_deleted(outputs):
    _, root, gen, _, _, _, _ = outputs
    cache = root / 'cache'
    cache.mkdir()
    (cache / 'stale.tif').write_bytes(b'old cached raster')
    result = refresh.publish_refresh(gen, [(root, None, cache)])
    assert not cache.exists()
    assert (Path(result['backups'][0]) / 'stale.tif').read_bytes() == b'old cached raster'


def test_changed_active_output_fails_closed(outputs):
    _, root, gen, source, _, target, _ = outputs
    calls = []
    def external_change():
        calls.append(True)
        if len(calls) == 2:
            target.write_bytes(b'changed externally')
    with pytest.raises(refresh.QGISRefreshError, match='changed during'):
        refresh.publish_refresh(gen, [(root, source, target)], check_control=external_change)
    assert target.read_bytes() == b'changed externally'


def test_rollback_never_overwrites_externally_changed_new_output(outputs):
    workspace, root, gen, source, tiles, target, target_tiles = outputs
    def replace(src, dst):
        if src.suffix == '.new' and dst == target_tiles:
            target.write_bytes(b'external writer data')
            raise OSError('controlled activation failure')
        src.replace(dst)
    with pytest.raises(refresh.QGISRefreshError, match='rollback incomplete'):
        refresh.publish_refresh(gen, [(root, source, target), (root, tiles, target_tiles)], replace=replace)
    assert target.read_bytes() == b'external writer data'
    assert any(p.read_bytes() == b'old-raster' for p in root.glob('.*.old') if p.is_file())
    with pytest.raises(refresh.QGISRefreshError, match='reconciliation'):
        refresh.reject_pending_refresh(workspace)


@pytest.mark.parametrize('state', ['publishing', 'published', 'requires_recovery', 'unknown'])
def test_uncertain_journal_blocks_new_generation(outputs, state):
    workspace, _, gen, *_ = outputs
    refresh.write_record(gen / 'refresh.json', {'version': 1, 'state': state})
    with pytest.raises(refresh.QGISRefreshError, match='reconciliation'):
        refresh.new_generation(workspace, RUN)


def test_malformed_journal_fails_closed(outputs):
    workspace, _, gen, *_ = outputs
    (gen / 'refresh.json').write_text('{incomplete')
    with pytest.raises(refresh.QGISRefreshError, match='Unreadable'):
        refresh.reject_pending_refresh(workspace)


def test_source_validation_runs_after_copy_but_before_any_rename(outputs):
    _, root, gen, source, _, target, _ = outputs
    def invalidated():
        assert list(root.glob('.*.new'))
        assert target.read_bytes() == b'old-raster'
        raise refresh.QGISRefreshError('source changed during copy')
    with pytest.raises(refresh.QGISRefreshError, match='source changed'):
        refresh.publish_refresh(gen, [(root, source, target)], before_activation=invalidated)
    assert target.read_bytes() == b'old-raster'
