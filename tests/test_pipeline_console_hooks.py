"""Pipeline and source adapter behavior under mocked external boundaries."""
from contextlib import nullcontext
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

# Reuse the established EXIF-refusing import boundary for minimal dev environments.
from tests.test_rgb_pipeline_single_stage_execution import RGBPipeline
from shared.stage_runner import StageRequiresRecovery
from modules.data_segregation import data_segregation as source


def quality_pipeline(tmp_path):
    pipeline=object.__new__(RGBPipeline)
    pipeline.survey_id='AH-026136'
    pipeline.loggers={'pipeline':logging.getLogger('test.browser.gate'),'webodm':logging.getLogger('test.browser.remote')}
    pipeline.config={'webodm':{'url':'http://localhost:8000'}}
    pipeline.state={'webodm':{'project_id':544,'task4':{'id':'exact-task-uuid','name':'existing-task'}}}
    pipeline._webodm_fallback_task_key=lambda:'task4'
    pipeline._webodm_task_flag=lambda *_,**__:'rgb'
    path=tmp_path/'placeholder.tif';path.write_text('not a raster',encoding='utf-8')
    pipeline._select_existing_task_orthomosaic=lambda **_: {'task_key':'task4','source_path':str(path)}
    pipeline._cleanup_webodm_upload_cache_from_state=Mock()
    pipeline._check_control_or_raise=Mock()
    pipeline.review_provider=Mock()
    return pipeline


@pytest.mark.parametrize('answer,passed',[('yes',True),('fail',False)])
def test_browser_review_injection_never_calls_terminal_input(tmp_path,answer,passed):
    pipeline=quality_pipeline(tmp_path)
    pipeline.review_provider.decision.return_value=answer
    result=pipeline.stage_quality_gate()
    assert result['passed'] is passed
    assert callable(pipeline.review_provider.decision.call_args.kwargs['check_control'])


def test_restart_ambiguous_response_is_not_retried(tmp_path):
    pipeline=quality_pipeline(tmp_path)
    pipeline.review_provider.decision.return_value='restart task4 dataset'
    remote=Mock();remote.restart_task.side_effect=TimeoutError('accepted but reply lost')
    pipeline._create_webodm_processor=lambda _:remote
    with pytest.raises(StageRequiresRecovery) as failure:
        pipeline.stage_quality_gate()
    assert isinstance(failure.value.__cause__,TimeoutError)
    remote.restart_task.assert_called_once_with(project_id=544,task_id='exact-task-uuid',restart_from='dataset')
    pipeline.review_provider.restart_started.assert_called_once_with(544,'exact-task-uuid','dataset')
    pipeline.review_provider.restart_acknowledged.assert_not_called()


def test_restart_acknowledges_same_uuid_before_wait_and_requires_new_review(tmp_path):
    pipeline=quality_pipeline(tmp_path)
    pipeline.review_provider.decision.side_effect=['restart task4 dataset','yes']
    remote=Mock();remote.wait_for_completion.return_value=(True,2.5,{'status':'completed'})
    pipeline._create_webodm_processor=lambda _:remote
    assert pipeline.stage_quality_gate()['passed']
    remote.restart_task.assert_called_once()
    pipeline.review_provider.restart_acknowledged.assert_called_once()
    pipeline.review_provider.restart_finished.assert_called_once_with({'status':'completed'})
    assert pipeline.review_provider.decision.call_count==2


def test_pause_during_acknowledged_restart_remains_pause_not_unknown_failure(tmp_path):
    pipeline=quality_pipeline(tmp_path)
    pipeline.review_provider.decision.return_value='restart task4 dataset'
    remote=Mock();remote.wait_for_completion.side_effect=RuntimeError('__PIPELINE_PAUSED__')
    pipeline._create_webodm_processor=lambda _:remote
    with pytest.raises(RuntimeError,match='PAUSED') as failure:
        pipeline.stage_quality_gate()
    assert not isinstance(failure.value,StageRequiresRecovery)
    pipeline.review_provider.restart_acknowledged.assert_called_once()


def test_dataset_ambiguity_uses_explicit_selector_without_terminal_input(monkeypatch,tmp_path):
    roots=[tmp_path/'root'];roots[0].mkdir()
    candidates=[roots[0]/'MCS-301_first',roots[0]/'MCS-301_second']
    for candidate in candidates:candidate.mkdir()
    monkeypatch.setattr(source,'_get_field_data_roots',lambda:roots)
    monkeypatch.setattr(source,'_find_dataset_candidates',lambda **_:candidates)
    selector=Mock(return_value=candidates[1])
    selected=source.resolve_source_dataset_dir(Path('MCS-301'),logging.getLogger('test.dataset'),candidate_selector=selector)
    assert selected==candidates[1]
    assert set(selector.call_args.args[0])==set(candidates)
    with pytest.raises(ValueError,match='not one of'):
        source.resolve_source_dataset_dir(Path('MCS-301'),logging.getLogger('test.dataset'),candidate_selector=lambda _:tmp_path/'foreign')


@pytest.mark.parametrize('action,status',[('pause','paused'),('stop','failed')])
def test_browser_control_after_blocking_final_stage_precedes_cleanup(
    monkeypatch,temporary_path_layout,sample_dataset_dir,action,status,
):
    from tests.test_rgb_pipeline_single_stage_execution import build_pipeline, FakeWebODM
    from shared.db.repo import PipelineRepo
    import pipelines.rgb_pipeline as module
    repository=PipelineRepo(temporary_path_layout.database_path)
    pipeline=build_pipeline(temporary_path_layout,sample_dataset_dir,repository,FakeWebODM())
    pipeline.enable_hotkeys=False
    pipeline.control.transport=Mock()
    pipeline.control.transport.poll.side_effect=[None,action]
    pipeline.control.start_hotkeys=Mock(side_effect=AssertionError('browser hotkeys'))
    pipeline._preflight_stage=lambda _:None
    pipeline._hydrate_from_state=lambda:None
    pipeline.stage_qgis=Mock(return_value={'fake_tiles':'owned fixture result'})
    cleanup=Mock(side_effect=AssertionError('cleanup after requested control'))
    monkeypatch.setattr(module,'cleanup_completed_run_workspace',cleanup)
    result=pipeline.run(resume=True,selected_stages={'qgis'})
    assert result['success'] is False
    assert result['paused' if action=='pause' else 'aborted']
    assert repository.get_run(pipeline.run_id)['status']==status
    assert repository.get_latest_stage(pipeline.run_id,'qgis')['status']=='completed'
    pipeline.control.start_hotkeys.assert_not_called()
    cleanup.assert_not_called()
