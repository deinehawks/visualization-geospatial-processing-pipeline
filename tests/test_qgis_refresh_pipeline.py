import json
from pathlib import Path

import pytest

from pipelines.rgb_helpers import qgis_refresh as stage_module
from shared.artifacts import create_run_workspace
from shared.db.repo import PipelineRepo
from shared.execution import StatePersistenceError
from shared.qgis_refresh import refresh_confirmation
from shared.stage_runner import StageRequiresRecovery
from tests.fakes import FakeWebODM
from tests.test_qgis_refresh import png_bytes
from tests.test_rgb_pipeline_single_stage_execution import (
    build_pipeline, prepare_qgis_context, configure_qgis_for_fake_tools,
)


@pytest.fixture
def refresh_pipeline(monkeypatch, temporary_path_layout, sample_dataset_dir):
    repo = PipelineRepo(temporary_path_layout.database_path)
    pipeline = build_pipeline(temporary_path_layout, sample_dataset_dir, repo, FakeWebODM())
    rgb, source, boundary = prepare_qgis_context(pipeline, temporary_path_layout,
        task_key='task4', task_label='t4', task_id='task-0004')
    configure_qgis_for_fake_tools(pipeline)
    repo.attach_survey_id(pipeline.run_id, pipeline.survey_id)
    create_run_workspace(pipeline.workspace_layout)
    task = {'id': 'task-0004', 'name': 'task-four', 'flag': 'xcb', 'success': True}
    web = {'project_id': 42, 'task4': task, 'downloads': {'task4': {'orthomosaic': str(source)}}}
    repo.record_webodm_binding(run_id=pipeline.run_id, operation_key='task4', project_id=42,
                              task_id=task['id'], task_name=task['name'], success=True)
    old = {'selected_webodm_task': 'task4', 'selected_orthomosaic': pipeline.state['selected_orthomosaic']}
    values = {'data_segregation': pipeline.state['data_segregation'],
        'kml_boundary': {'boundary_geojson_path': str(boundary), 'boundary_available': True},
        'webodm': web, 'webodm_task4': web, 'quality_gate': {'passed': True}, 'qgis': old}
    for name, output in values.items():
        ident = repo.start_stage(pipeline.run_id, name)
        repo.finish_stage_with_status(ident, 'completed', 0, output=output)
    clip_name = pipeline._clipped_orthomosaic_filename(task_key='task4', flag='xcb')
    published_clip = rgb / 'qgis' / 'clipped' / 'ortho' / clip_name
    published_clip.parent.mkdir(parents=True)
    published_clip.write_bytes(b'old-published-clip')
    workspace_clip = pipeline.workspace_layout.qgis_clipped_ortho / clip_name
    workspace_clip.write_bytes(b'old-workspace-clip')
    pub_tiles = rgb / 'tiles' / 'ortho' / 'round-corners'
    pub_tiles.mkdir(parents=True)
    (pub_tiles / 'old.png').write_bytes(b'old-published-tile')
    (pipeline.workspace_layout.qgis_tiles_round / 'old.png').write_bytes(b'old-workspace-tile')
    calls, capacities = [], []
    class FreshTools:
        fail = None
        def __init__(self, **kwargs):
            pass
        def clip_raster_by_mask(self, **kwargs):
            calls.append(('clip', kwargs))
            Path(kwargs['output_tif']).write_bytes(Path(kwargs['input_tif']).read_bytes() + b'-fresh-clip')
            if self.fail == 'clip':
                raise RuntimeError('controlled fresh clip failure')
        def verify_raster_readable(self, path, **kwargs):
            calls.append(('verify', path))
            assert Path(path).is_file()
        def generate_tiles(self, **kwargs):
            calls.append(('tiles', kwargs))
            folder = Path(kwargs['output_dir']) / '12'
            folder.mkdir(parents=True)
            (folder / '1.png').write_bytes(png_bytes())
            if self.fail == 'tiles':
                raise RuntimeError('controlled fresh tile failure')
            if self.fail == 'source-change':
                source.write_bytes(b'changed while rendering')
    monkeypatch.setattr(stage_module, 'QGISTools', FreshTools)
    monkeypatch.setattr(pipeline, '_check_write_capacity', lambda **kw: capacities.append(kw))
    yield pipeline, repo, rgb, source, published_clip, workspace_clip, pub_tiles, calls, FreshTools, capacities


def run_refresh(pipeline, **kwargs):
    return pipeline.run(resume=True, raise_on_error=True,
        qgis_refresh={'task_key': 'task4', 'confirmation': refresh_confirmation(pipeline.run_id)}, **kwargs)


def test_completed_run_refreshes_clip_and_tiles_without_remote_or_review(refresh_pipeline):
    pipeline, repo, rgb, source, published, workspace, tiles, calls, _, capacities = refresh_pipeline
    binding = repo.get_webodm_binding(pipeline.run_id, 'task4')
    completed = repo.get_latest_stage(pipeline.run_id, 'qgis')
    result = run_refresh(pipeline)
    assert result['success']
    assert published.read_bytes() == source.read_bytes() + b'-fresh-clip'
    assert workspace.read_bytes() == published.read_bytes()
    assert not (tiles / 'old.png').exists()
    assert (tiles / '12' / '1.png').read_bytes() == png_bytes()
    tile_call = next(value for name, value in calls if name == 'tiles')
    assert tile_call['resume'] is False and tile_call['clean'] is False
    assert tile_call['input_tif'] != workspace
    assert pipeline.webodm_processor.calls == []
    assert repo.get_webodm_binding(pipeline.run_id, 'task4') == binding
    assert repo.get_latest_stage(pipeline.run_id, 'qgis')['id'] > completed['id']
    assert repo.get_latest_stage(pipeline.run_id, 'quality_gate')['status'] == 'completed'
    assert len([r for r in calls if r[0] == 'clip']) == 1
    receipt = result['qgis']['qgis_refresh']
    assert json.loads(Path(receipt['manifest_path']).read_text())['state'] == 'completed'
    assert Path(receipt['generation']).exists()
    assert any(Path(p).read_bytes() == b'old-published-clip' for p in receipt['backups'] if Path(p).is_file())
    manifest = json.loads((rgb / 'manifest.json').read_text())
    assert manifest['test_sentinel'] == 'preserve'
    assert manifest['qgis_refresh_task4']['source_sha256']
    assert capacities


@pytest.mark.parametrize('failure', ['clip', 'tiles', 'source-change'])
def test_failed_render_keeps_active_clip_tiles_and_manifest(refresh_pipeline, failure):
    pipeline, repo, rgb, _, published, workspace, tiles, calls, tools, _ = refresh_pipeline
    before = (rgb / 'manifest.json').read_bytes()
    tools.fail = failure
    with pytest.raises(RuntimeError):
        run_refresh(pipeline)
    assert published.read_bytes() == b'old-published-clip'
    assert workspace.read_bytes() == b'old-workspace-clip'
    assert (tiles / 'old.png').read_bytes() == b'old-published-tile'
    assert (rgb / 'manifest.json').read_bytes() == before
    assert repo.get_latest_stage(pipeline.run_id, 'qgis')['status'] == 'failed'
    assert len([r for r in calls if r[0] == 'clip']) == 1


def test_task2_outputs_and_manifest_records_are_preserved(refresh_pipeline):
    pipeline, repo, rgb, _, published, workspace, tiles, calls, _, _ = refresh_pipeline
    # Move only test-owned Task 4 fixtures into their combined-run layout.
    (rgb.parent / '.test-owned').write_text('combined-layout')
    tiles.rename(tiles.with_name('old-task4-fixture'))
    tiles.mkdir()
    tiles.with_name('old-task4-fixture').rename(tiles / 'task4')
    local = pipeline.workspace_layout.qgis_tiles_round
    (local / 'task4').mkdir()
    (local / 'old.png').rename(local / 'task4' / 'old.png')
    (tiles / 'task2').mkdir()
    (tiles / 'task2' / 'keep.png').write_bytes(b'task2 unchanged')
    task2_record = {'selected_webodm_task': 'task2', 'published_operation': {'tiles_dir': str(tiles / 'task2')}}
    previous = {'selected_webodm_task': 'task2', 'operations': {'task4': {}, 'task2': task2_record}}
    ident = repo.start_stage(pipeline.run_id, 'qgis')
    repo.finish_stage_with_status(ident, 'completed', 0, output=previous)
    manifest = json.loads((rgb / 'manifest.json').read_text())
    manifest['raster_outputs'] = {'qgis_clipped_task2': {'sentinel': 'unchanged'}}
    (rgb / 'manifest.json').write_text(json.dumps(manifest))
    result = run_refresh(pipeline)
    assert result['qgis']['operations']['task2'] == task2_record
    assert (tiles / 'task2' / 'keep.png').read_bytes() == b'task2 unchanged'
    assert json.loads((rgb / 'manifest.json').read_text())['raster_outputs']['qgis_clipped_task2'] == {'sentinel': 'unchanged'}
    assert len([r for r in calls if r[0] == 'tiles']) == 1


def test_state_commit_failure_does_not_replay_gis_and_blocks_refresh(monkeypatch, refresh_pipeline):
    pipeline, repo, _, _, _, _, _, calls, _, _ = refresh_pipeline
    original = repo.finish_stage_with_status
    def fail_completion(stage_id, status, *args, **kwargs):
        if status == 'completed':
            raise StatePersistenceError('controlled state commit failure')
        return original(stage_id, status, *args, **kwargs)
    monkeypatch.setattr(repo, 'finish_stage_with_status', fail_completion)
    with pytest.raises(StageRequiresRecovery, match='persistence failed') as failure:
        run_refresh(pipeline)
    assert isinstance(failure.value.__cause__, StatePersistenceError)
    assert len([r for r in calls if r[0] == 'clip']) == 1
    with pytest.raises(StageRequiresRecovery, match='reconciliation'):
        run_refresh(pipeline)
    with pytest.raises(StageRequiresRecovery, match='reconciliation'):
        pipeline.run(resume=True, raise_on_error=True)
    assert len([r for r in calls if r[0] == 'clip']) == 1


def test_refresh_refuses_authoritative_publication_set(refresh_pipeline):
    pipeline, _, rgb, _, published, _, _, calls, _, _ = refresh_pipeline
    (rgb / 'publication.json').write_text('{"existing":"generation"}')
    with pytest.raises(RuntimeError, match='authoritative publication'):
        run_refresh(pipeline)
    assert published.read_bytes() == b'old-published-clip'
    assert calls == []


def test_mismatched_binding_refused_before_gis(refresh_pipeline):
    pipeline, repo, _, _, published, _, _, calls, _, _ = refresh_pipeline
    repo.record_webodm_binding(run_id=pipeline.run_id, operation_key='task4',
        project_id=42, task_id='different-task', allow_rebind=True)
    with pytest.raises(StageRequiresRecovery, match='matching completed'):
        run_refresh(pipeline)
    assert calls == [] and published.read_bytes() == b'old-published-clip'


def test_binding_without_recorded_success_is_not_adopted(monkeypatch, refresh_pipeline):
    pipeline, repo, _, _, published, _, _, calls, _, _ = refresh_pipeline
    original = repo.get_webodm_binding
    monkeypatch.setattr(repo, 'get_webodm_binding', lambda *a: {**original(*a), 'success': None})
    with pytest.raises(StageRequiresRecovery, match='matching completed'):
        run_refresh(pipeline)
    assert calls == [] and published.read_bytes() == b'old-published-clip'


def test_insufficient_capacity_does_not_render_or_replace(monkeypatch, refresh_pipeline):
    pipeline, _, _, _, published, _, _, calls, _, _ = refresh_pipeline
    def full(**kwargs):
        raise RuntimeError('controlled capacity failure')
    monkeypatch.setattr(pipeline, '_check_write_capacity', full)
    with pytest.raises(RuntimeError, match='capacity failure'):
        run_refresh(pipeline)
    assert calls == [] and published.read_bytes() == b'old-published-clip'


def test_refresh_archives_only_task4_staging_cache(refresh_pipeline):
    pipeline, _, rgb, _, _, _, _, _, _, _ = refresh_pipeline
    staging = rgb.parent / 'test-staging'
    pipeline.config['qgis']['local_staging'] = {'enabled': True, 'dir': str(staging)}
    parent = staging / 'tiles' / pipeline.run_id
    cache = parent / 'task4'
    cache.mkdir(parents=True)
    (cache / 'stale.tif').write_bytes(b'cached-old-clip')
    sibling = parent / 'task2'
    sibling.mkdir()
    (sibling / 'keep.png').write_bytes(b'keep-task2-cache')
    result = run_refresh(pipeline)
    assert not cache.exists()
    backups = result['qgis']['qgis_refresh']['backups']
    assert any((Path(path) / 'stale.tif').read_bytes() == b'cached-old-clip'
               for path in backups if (Path(path) / 'stale.tif').exists())
    assert (sibling / 'keep.png').read_bytes() == b'keep-task2-cache'


def test_failed_completion_receipt_blocks_ordinary_resume(monkeypatch, refresh_pipeline):
    import pipelines.rgb_pipeline as module
    pipeline, _, _, _, _, _, _, calls, _, _ = refresh_pipeline
    def fail_receipt(*args):
        raise OSError('controlled receipt failure')
    monkeypatch.setattr(module, 'acknowledge_refresh', fail_receipt)
    with pytest.raises(StageRequiresRecovery, match='completion receipt'):
        run_refresh(pipeline)
    with pytest.raises(StageRequiresRecovery, match='reconciliation'):
        pipeline.run(resume=True, raise_on_error=True)
    assert len([r for r in calls if r[0] == 'tiles']) == 1


def test_future_resume_retains_refresh_workspace(monkeypatch, refresh_pipeline):
    pipeline, _, _, _, _, _, _, _, _, _ = refresh_pipeline
    run_refresh(pipeline)
    captured = []
    monkeypatch.setattr(pipeline, '_workspace_cleanup_result', lambda **kw: captured.append(kw))
    # Single-stage resume exercises the normal skip path without terminal review.
    result = pipeline.run(resume=True, selected_stages={'qgis'}, raise_on_error=True)
    assert result['success'] and captured[0]['keep_workspace'] is True
