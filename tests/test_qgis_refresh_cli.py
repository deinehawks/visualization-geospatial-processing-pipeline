import pytest

import main as cli
from shared.qgis_refresh import refresh_confirmation
from shared.storage_preflight import GIB, build_storage_preflight
from tests.test_main_publication_activation_cli import _prepare_cli, FakeRGBPipeline
from tests.test_storage_preflight import _usage


RUN = 'refresh-cli-run'


def arguments():
    return ['--survey', 'fixture-source', '--resume', '--run-id', RUN,
            '--task4', '--refresh-qgis', 'task4',
            '--qgis-refresh-confirmation', refresh_confirmation(RUN)]


def prepare(monkeypatch, tmp_path, extra=()):
    _prepare_cli(monkeypatch, tmp_path, [*arguments(), *extra])
    monkeypatch.setattr(cli, 'read_run_state_read_only', lambda *a: {
        'run_id': RUN, 'source_dir': str(tmp_path / 'source'),
        'workspace_root': str(tmp_path / 'workspaces')})
    monkeypatch.setattr(cli, 'read_latest_stage_statuses_read_only', lambda *a: {
        name: 'completed' for name in ('data_segregation', 'cross_run_filter',
            'kml_boundary', 'webodm', 'webodm_task4', 'quality_gate', 'qgis')})
    monkeypatch.setattr(cli, 'read_bindings_read_only', lambda *a: [])


def test_refresh_cli_accepts_completed_qgis_and_passes_exact_request(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    observed = []
    cli.main(observer=observed.append)
    [pipeline] = FakeRGBPipeline.instances
    assert pipeline.run_kwargs['qgis_refresh'] == {
        'task_key': 'task4', 'confirmation': refresh_confirmation(RUN)}
    assert pipeline.run_kwargs['force_stages'] == set()
    assert observed[0]['exclusive'] is True
    assert observed[0]['report']['include_qgis_refresh'] is True
    assert observed[0]['report']['include_upload_cache'] is False
    assert observed[0]['report']['include_task4_all_assets_zip'] is False


def test_refresh_storage_plan_does_not_construct_pipeline_or_claim_execution(monkeypatch, tmp_path, capsys):
    prepare(monkeypatch, tmp_path, ['--storage-preflight-only'])
    monkeypatch.setattr(cli, 'PipelineRepo', lambda *a: pytest.fail('No writable repository'))
    result = cli.main(guard_factory=lambda *a: pytest.fail('No execution lease'))
    assert result['exclusive'] and result['report']['include_qgis_refresh']
    assert FakeRGBPipeline.instances == []
    assert 'PLAN ONLY - outputs unchanged' in capsys.readouterr().out


@pytest.mark.parametrize('extra,remove,message', [
    ([], ['--resume'], 'requires --resume'),
    ([], ['--qgis-refresh-confirmation', refresh_confirmation(RUN)], 'must exactly match'),
    (['--force-stage', 'webodm_task4'], [], 'cannot combine'),
    (['--rebuild-qgis-tiles', 'task4'], [], 'cannot combine'),
    (['--recover-missing-raw'], [], 'cannot combine'),
])
def test_invalid_refresh_request_rejected_before_config(monkeypatch, tmp_path, capsys, extra, remove, message):
    args = [arg for arg in arguments() if arg not in remove] + extra
    _prepare_cli(monkeypatch, tmp_path, args)
    monkeypatch.setattr(cli, 'load_pipeline_config', lambda: pytest.fail('Must reject before config'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


def test_refresh_requires_latest_completed_task4_not_older_success(monkeypatch, tmp_path, capsys):
    prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, 'read_latest_stage_statuses_read_only', lambda *a: {
        'data_segregation': 'completed', 'kml_boundary': 'completed',
        'webodm': 'completed', 'webodm_task4': 'failed'})
    with pytest.raises(SystemExit):
        cli.main()
    assert 'requires completed webodm_task4' in capsys.readouterr().err
    assert FakeRGBPipeline.instances == []


def test_refresh_storage_estimates_include_generation_and_pending_copies(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'photo.jpg').write_bytes(b'x' * 400)
    kwargs = dict(source_dir=source, database_path=tmp_path / 'db.sqlite',
        logs_dir=tmp_path / 'logs', checkpoint_dir=tmp_path / 'checkpoints',
        upload_cache_root=tmp_path / 'cache', qgis_staging_root=tmp_path / 'staging',
        workspace_root=tmp_path / 'workspaces', surveys_root=tmp_path / 'surveys',
        temp_root=tmp_path / 'temp', include_upload_cache=False, webodm_mode='task4',
        disk_usage=lambda p: _usage(total_gib=100, free_gib=100),
        min_free_gb=0, min_free_percent=0, published_min_free_gb=0, published_min_free_percent=0)
    normal = build_storage_preflight(**kwargs)
    fresh = build_storage_preflight(**kwargs, include_qgis_refresh=True)
    assert normal['include_qgis_refresh'] is False
    assert fresh['estimated_qgis_staging_bytes'] == 2400
    assert fresh['estimated_workspace_bytes'] == 20 * GIB
    assert fresh['estimated_published_output_bytes'] == 1600
    assert fresh['volumes'][0]['required_bytes'] > normal['volumes'][0]['required_bytes']
