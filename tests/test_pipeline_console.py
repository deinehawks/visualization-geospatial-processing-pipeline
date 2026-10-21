"""Hermetic console contracts: owned paths, temporary SQLite, fake processes only."""
from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from pydantic import ValidationError

from operator_ui.models import RunInput, ReviewCommand
from operator_ui.projections import ReadModel, redact, safe_url
from operator_ui.runner import BrowserInteraction, DatasetChoiceRequired, arguments, execute, require_dataset_choice
from operator_ui.service import ConsoleService
from operator_ui.storage import ConsoleError, ConsoleStore, atomic_json, cli_execution_guard, read_json


def uid():
    return uuid.uuid4().hex


class Processes:
    def __init__(self):
        self.live = {os.getpid(): 'birth'}
        self.jobs = {}

    def identity(self, pid):
        return self.live.get(pid)

    def job_active(self, name):
        return self.jobs.get(name, 0)

    def join_job(self, name):
        self.jobs[name] = 1
        def close(_):
            self.jobs[name] = 0
            self.live.pop(os.getpid(), None)
        return SimpleNamespace(CloseHandle=Mock(side_effect=close)), 1


@pytest.fixture
def console(tmp_path, temporary_sqlite_db_path):
    lock = threading.RLock()
    @contextmanager
    def locked(_):
        with lock:
            yield
    processes = Processes()
    store = ConsoleStore(tmp_path / 'console', lock_factory=locked, processes=processes)
    reader = ReadModel(temporary_sqlite_db_path, tmp_path / 'logs')
    python = tmp_path / 'fake-python.exe'
    python.write_text('not executable', encoding='utf-8')
    spawn = Mock(return_value=SimpleNamespace(pid=4242))
    service = ConsoleService(tmp_path, store=store, reader=reader, popen=spawn, pipeline_python=python)
    return service


def seed(console, run_id='original', status='failed', *, mode=None):
    with closing(sqlite3.connect(console.reader.database)) as connection, connection:
        connection.execute('INSERT INTO runs (run_id,survey_id,status,source_dir,surveys_root,year,started_at) VALUES (?,?,?,?,?,?,?)',
                           (run_id, 'AH-026136', status, str(console.base_dir / 'source'), str(console.base_dir / 'surveys'), 2026, '2026-10-20T00:00:00+00:00'))
        if mode:
            connection.execute('INSERT INTO stages (run_id,stage_name,status,output_json) VALUES (?,?,?,?)',
                               (run_id, 'webodm', 'completed', json.dumps({'mode': mode})))
    return run_id


def finish_preflight(console, operation):
    directory = console.store.directory(operation['operation_id'])
    inputs = read_json(directory / 'request.json')['inputs']
    value = {'report': {'ok': True, 'volumes': []}, 'source_dir': inputs['survey'], 'mode': inputs['mode'],
             'run_id': inputs['run_id'], 'survey_id': inputs['survey_id'], 'year': inputs['year'],
             'surveys_root': 'owned-surveys', 'workspace_root': 'owned-workspaces',
             'config_fingerprint': 'test-fingerprint', 'stage_statuses': {}}
    atomic_json(directory / 'result.json', {'preflight': value})
    console.store.update(operation['operation_id'], state='finished')
    return value


def active_operation(console, mode=None):
    operation_id = uid()
    console.store.reserve(operation_id, {'kind': 'run', 'run_id': 'original', 'preflight_id': uid(),
                                         'inputs': {'action': 'resume', 'mode': mode}})
    console.store.update(operation_id, state='running', pid=os.getpid(), birth='birth')
    return operation_id


def test_missing_database_read_does_not_create_it(tmp_path):
    database = tmp_path / 'missing.db'
    reader = ReadModel(database, tmp_path / 'logs')
    assert reader.runs()['availability'] == 'database_missing'
    assert not database.exists()
    with pytest.raises(ConsoleError, match='Run not found'):
        reader.detail('missing')


def test_read_projection_latest_attempt_partial_state_and_pagination(console):
    seed(console, 'a', 'partially_completed', mode='both')
    seed(console, 'z')
    with sqlite3.connect(console.reader.database) as conn:
        conn.execute('INSERT INTO stages(run_id,stage_name,status,runtime_seconds) VALUES (?,?,?,?)', ('a','qgis','completed',10))
        conn.execute('INSERT INTO stages(run_id,stage_name,status,error_message) VALUES (?,?,?,?)', ('a','qgis','failed','new failure'))
    before = console.reader.database.read_bytes()
    first = console.reader.runs(limit=1)
    assert first['items'][0]['run_id'] == 'z'
    assert console.reader.runs(first['next_cursor'], 1)['items'][0]['run_id'] == 'a'
    detail = console.reader.detail('a')
    assert detail['run']['status'] == 'partially_completed' and detail['mode'] == 'both'
    qgis = [s for s in detail['stages'] if s['stage_name'] == 'qgis']
    assert [s['authoritative_attempt'] for s in qgis] == [False, True]
    assert [s['attempt_number'] for s in qgis] == [1, 2]
    assert console.reader.database.read_bytes() == before


def test_bound_output_json_and_secret_options(console):
    seed(console)
    with sqlite3.connect(console.reader.database) as conn:
        conn.execute('INSERT INTO stages(run_id,stage_name,status,output_json) VALUES (?,?,?,?)',
                     ('original','qgis','failed',json.dumps({'huge':'x'*300000})))
        conn.execute('INSERT INTO webodm_tasks(run_id,operation_key,options_json) VALUES (?,?,?)',
                     ('original','task2',json.dumps({'password':'private','nested':{'token':'secret'},'safe':1})))
    result = console.reader.detail('original')
    assert result['stages'][0]['output_truncated'] and result['stages'][0]['output'] == {}
    assert result['operations'][0]['options'] == {'nested':{},'safe':1}
    assert 'private' not in json.dumps(result) and 'secret' not in json.dumps(result)


@pytest.mark.parametrize('mode', [None, 'task2', 'task4', 'both'])
def test_resume_identity_and_mode_are_evidence_based(console, mode):
    seed(console, mode=mode)
    if mode is None:
        with pytest.raises(ConsoleError, match='unknown'):
            console.normalize({'action':'resume','run_id':'original'})
    value = console.normalize({'action':'resume','run_id':'original','mode':mode or 'task2',
                               'survey':'different', 'survey_id':'AH-026999','year':2025})
    assert value['run_id'] == 'original' and value['survey_id'] == 'AH-026136'
    assert value['survey'] == str(console.base_dir / 'source') and value['year'] == 2026


def test_one_binding_does_not_downgrade_incomplete_combined_mode(console):
    seed(console)
    with sqlite3.connect(console.reader.database) as conn:
        conn.execute('INSERT INTO webodm_tasks(run_id,operation_key) VALUES (?,?)',('original','task4'))
    assert console.reader.detail('original')['mode'] is None
    active_operation(console,mode='both')
    assert console.run_detail('original')['mode']=='both'
    assert console.run_detail('original')['mode_source']=='console_request'
    assert console.normalize({'action':'resume','run_id':'original'})['mode']=='both'


@pytest.mark.parametrize('change', [{'shell':'evil'}, {'mode':'evil'}, {'uav':'../escape'}, {'survey_id':'../id'}, {'year':-1}, {'node_id':0}])
def test_inputs_allowlist_rejects_arbitrary_options(change):
    with pytest.raises(ValidationError):
        RunInput.model_validate({'survey':'site', **change})


@pytest.mark.parametrize('value', ['../../escape', '/root', '', 'a.b', 'a/b'])
def test_operation_ids_cannot_escape_workspace(console, value):
    with pytest.raises(ConsoleError):
        console.store.directory(value)


def test_preflight_is_immutable_idempotent_and_blocks_other_commands(console):
    operation_id = uid()
    first = console.preflight(operation_id, {'survey':'test-source'})
    assert console.preflight(operation_id, {'survey':'test-source'})['run_id'] == first['run_id']
    console.popen.assert_called_once()
    with pytest.raises(ConsoleError, match='already used'):
        console.preflight(operation_id, {'survey':'another-source'})
    with pytest.raises(ConsoleError, match='active'):
        console.preflight(uid(), {'survey':'second'})
    request = read_json(console.store.directory(operation_id) / 'request.json')
    assert request['inputs']['mode'] == 'task4' and request['inputs']['run_id'] == first['run_id']
    argv, = console.popen.call_args.args
    assert argv[1:4] == ['-u','-m','operator_ui.runner']
    assert console.popen.call_args.kwargs['shell'] is False
    assert console.popen.call_args.kwargs['env']['PYTHONIOENCODING']=='utf-8'


def test_start_requires_pass_and_consumes_ticket_once(console):
    checked = console.preflight(uid(), {'survey':'test-source'})
    with pytest.raises(ConsoleError, match='preflight'):
        console.start(uid(), checked['operation_id'])
    finish_preflight(console, checked)
    started = console.start(uid(), checked['operation_id'])
    assert console.start(started['operation_id'], checked['operation_id'])['operation_id'] == started['operation_id']
    console.store.update(started['operation_id'], state='failed')
    with pytest.raises(ConsoleError, match='already been used'):
        console.start(uid(), checked['operation_id'])


def test_two_tabs_can_only_dispatch_once(console):
    def submit(_):
        try:
            return console.preflight(uid(), {'survey':'site'})['operation_id']
        except ConsoleError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(submit, range(2)))
    assert len([value for value in values if value]) == 1
    assert console.popen.call_count == 1


def test_legacy_terminal_is_monitored_not_taken_over(console):
    seed(console, status='running')
    with pytest.raises(ConsoleError, match='legacy terminal'):
        console.preflight(uid(), {'survey':'new'})
    assert console.run_detail('original')['terminal_monitor_only']
    console.popen.assert_not_called()


@pytest.mark.parametrize('live,children', [('birth',0), ('unknown',0), (None,1), (None,None)])
def test_live_uncertain_orphaned_owners_block_reconciliation(console, live, children):
    operation_id = active_operation(console)
    console.store.processes.live[os.getpid()] = live
    value = console.store.status(operation_id)
    console.store.processes.jobs[value['job_name']] = children
    assert console.store.occupied()
    with pytest.raises(ConsoleError):
        console.store.reconcile(operation_id, 'RECONCILE ' + operation_id)


def test_dead_and_reused_pid_reconciliation_preserves_evidence(console):
    operation_id = active_operation(console)
    console.store.processes.live[os.getpid()] = 'a-different-process'
    before = (console.store.directory(operation_id) / 'request.json').read_bytes()
    with pytest.raises(ConsoleError):
        console.store.reconcile(operation_id, 'wrong')
    assert console.store.reconcile(operation_id, 'RECONCILE '+operation_id)['state'] == 'resolved'
    assert (console.store.directory(operation_id) / 'request.json').read_bytes() == before
    assert list(console.store.directory(operation_id).glob('reconciled-*.json'))


def test_unresolved_remote_restart_cannot_be_cleared_by_local_reconcile(console):
    operation_id = active_operation(console)
    console.store.processes.live.clear()
    atomic_json(console.store.directory(operation_id)/'restart-intent.json', {'resolved':False})
    with pytest.raises(ConsoleError, match='Remote restart'):
        console.store.reconcile(operation_id,'RECONCILE '+operation_id)


def test_server_recreation_reconnects_only_to_verified_owner(console):
    operation_id = active_operation(console)
    other = ConsoleService(console.base_dir,store=console.store,reader=console.reader,popen=Mock())
    assert other.operation(operation_id)['controls_available']
    console.store.processes.live[os.getpid()] = 'reused'
    assert not other.operation(operation_id)['controls_available']
    with pytest.raises(ConsoleError):
        other.control(operation_id,uid(),'pause')


@pytest.mark.parametrize('action,signal', [('pause','__PIPELINE_PAUSED__'),('stop','__PIPELINE_ABORTED__')])
def test_controls_are_requested_then_acknowledged_at_checkpoint(console, action, signal):
    from shared.pipeline_control import PipelineControl
    operation_id = active_operation(console)
    command_id = uid()
    assert console.control(operation_id,command_id,action)['state'] == 'requested'
    console.control(operation_id,command_id,action)
    with pytest.raises(ConsoleError,match='already pending'):
        console.control(operation_id,uid(),'stop' if action=='pause' else 'pause')
    transport = BrowserInteraction(console.store,operation_id)
    controller = PipelineControl(console.base_dir,'original',transport=transport)
    with pytest.raises(RuntimeError,match=signal):
        controller.check_or_raise()
    ack = console.store.directory(operation_id)/'controls'/(command_id+'.ack.json')
    assert read_json(ack)['state'] == 'acknowledged'
    console.store.update(operation_id,state='finished')
    assert console.control(operation_id,command_id,action)['state']=='acknowledged'
    assert transport.poll() is None
    assert not controller.pause_flag.exists() and not controller.abort_flag.exists()


@pytest.mark.parametrize('action,answer', [('approve','yes'),('fail','fail'),('restart','restart task4 dataset')])
def test_browser_review_waits_for_exact_question_and_dispatches_once(console, action, answer):
    operation_id = active_operation(console)
    interaction = BrowserInteraction(console.store,operation_id)
    def respond(_):
        question = console.operation(operation_id)['review']
        command = ReviewCommand(command_id=uid(),review_id=question['review_id'], action=action,
                                task='task4',restart_from='dataset',confirmation='RESTART task4 dataset FOR RUN original')
        console.review(operation_id,command)
        console.review(operation_id,command)
        with pytest.raises(ConsoleError,match='already has'):
            console.review(operation_id,command.model_copy(update={'command_id':uid()}))
    interaction.sleep = respond
    check = Mock()
    value = interaction.decision(check_control=check,project_id=1,survey_id='AH-026136',task4={'id':'exact-uuid'},
                                 webodm_url='http://user:password@localhost:8000/?token=secret')
    assert value == answer and check.call_count >= 2
    old_command=ReviewCommand.model_validate(read_json(interaction.directory/'reviews'/(interaction.review_id+'.decision.json')))
    assert console.review(operation_id,old_command)['state']=='acknowledged'
    assert console.store.status(operation_id)['state'] == 'running'
    assert list((interaction.directory/'reviews').glob('*.consumed.json'))
    assert 'password' not in (interaction.directory/'review.json').read_text()


def test_quality_wait_can_pause_without_approval(console):
    operation_id = active_operation(console)
    interaction = BrowserInteraction(console.store,operation_id)
    def pause():
        raise RuntimeError('__PIPELINE_PAUSED__')
    with pytest.raises(RuntimeError,match='PAUSED'):
        interaction.decision(check_control=pause,project_id=1)
    assert not list((interaction.directory/'reviews').glob('*.decision.json'))


@pytest.mark.parametrize('failure', ['wrong_run','no_result','config_changed','storage_fail'])
def test_helper_structured_failures_never_report_success(console,failure):
    checked = console.preflight(uid(),{'survey':'source'})
    expected = finish_preflight(console,checked)
    started = console.start(uid(),checked['operation_id'])
    def launch(argv, **options):
        assert '--storage-preflight-only' not in argv
        assert options['pipeline_options']['enable_hotkeys'] is False
        assert options['guard_factory'](None,None).__enter__().operation_id == started['operation_id']
        if failure == 'config_changed':
            options['observer']({**expected,'config_fingerprint':'changed'})
        elif failure == 'storage_fail':
            options['observer']({**expected,'report':{'ok':False}})
            raise SystemExit(3)
        else:
            options['observer'](expected)
        return None if failure == 'no_result' else {'success':True,'run_id':'wrong'}
    result = execute(console.store,started['operation_id'],launch=launch)
    assert result.get('error')
    assert console.store.status(started['operation_id'])['state'] == (
        'requires_recovery' if failure in ('wrong_run','no_result') else 'failed'
    )
    assert read_json(console.store.directory(started['operation_id'])/'request.json')['run_id'] == checked['run_id']


@pytest.mark.parametrize('success,paused,state', [(True,False,'finished'),(False,False,'failed'),(False,True,'finished')])
def test_helper_uses_structured_pipeline_result_not_exit_code(console,success,paused,state):
    checked=console.preflight(uid(),{'survey':'source'})
    expected=finish_preflight(console,checked)
    started=console.start(uid(),checked['operation_id'])
    def launch(argv,**options):
        options['observer'](expected)
        return {'run_id':started['run_id'],'success':success,'paused':paused}
    result=execute(console.store,started['operation_id'],launch=launch)
    assert result['pipeline']['success'] == success
    assert console.store.status(started['operation_id'])['state'] == state


def test_helper_dataset_ambiguity_is_explicit_and_never_input(console):
    checked=console.preflight(uid(),{'survey':'name'})
    def launch(argv,**options):
        options['candidate_selector']((console.base_dir/'one',console.base_dir/'two'))
    result=execute(console.store,checked['operation_id'],launch=launch)
    assert result['code']=='DATASET_CHOICE_REQUIRED' and len(result['candidates'])==2


def test_missing_executable_and_uncertain_spawn_preserve_attempts(console):
    console.pipeline_python=console.base_dir/'missing.exe'
    failed=console.preflight(uid(),{'survey':'source'})
    assert failed['state']=='failed'
    console.popen.assert_not_called()
    console.pipeline_python=console.base_dir/'fake-python.exe'
    console.popen.side_effect=RuntimeError('uncertain spawn')
    uncertain=console.preflight(uid(),{'survey':'source'})
    assert uncertain['state']=='requires_recovery'
    assert (console.store.directory(uncertain['operation_id'])/'dispatch-intent.json').exists()


def test_early_helper_exit_is_visible_with_bounded_redacted_log(console):
    operation=console.preflight(uid(),{'survey':'source'})
    path=console.store.directory(operation['operation_id'])/'console.log'
    path.write_text('old'*10000+'\ntoken=private\nImport failed before ownership',encoding='utf-8')
    value=console.operation(operation['operation_id'])
    assert value['observed_state']=='requires_reconciliation'
    assert len(value['log_tail'])<=16384 and 'private' not in value['log_tail']
    assert 'Import failed' in value['log_tail']


@pytest.mark.parametrize('task', ['task2','task4'])
def test_tile_recovery_requires_exact_phrase_and_original_identity(console,task):
    seed(console,mode=task)
    inputs={'action':'rebuild_tiles','run_id':'original','mode':task,'rebuild_task':task}
    with pytest.raises(ConsoleError,match='Exact tile'):
        console.normalize(inputs)
    inputs['confirmation']=f'REBUILD QGIS TILES {task} FOR RUN original'
    value=console.normalize(inputs)
    argv=arguments(value,preflight=True)
    assert argv[argv.index('--rebuild-qgis-tiles')+1]==task
    assert argv[argv.index('--run-id')+1]=='original'
    assert '--resume' in argv and '--storage-preflight-only' in argv
    assert '--force-stage' not in argv


@pytest.mark.parametrize('text', ['http://user:private@host:8000/path?token=secret', 'token=private', 'password=private'])
def test_projection_redacts_sensitive_log_fields(text):
    assert 'private' not in redact(text) and 'secret' not in redact(text)


@pytest.mark.parametrize('text', ['{"password": "private"}', "{'token': 'private'}", 'Authorization: Bearer private'])
def test_redaction_handles_quoted_json_and_bearer_values(text):
    assert 'private' not in redact(text)


def test_success_result_does_not_copy_large_pipeline_state(console):
    checked=console.preflight(uid(),{'survey':'source'})
    expected=finish_preflight(console,checked)
    started=console.start(uid(),checked['operation_id'])
    def launch(argv,**options):
        options['observer'](expected)
        return {'run_id':started['run_id'],'success':True,'image_classifications':['large']*10000}
    result=execute(console.store,started['operation_id'],launch=launch)
    assert result['pipeline']['success']
    assert 'image_classifications' not in result['pipeline']
    assert (console.store.directory(started['operation_id'])/'result.json').stat().st_size<1000


def test_result_is_preserved_if_final_status_commit_fails(console,monkeypatch):
    checked=console.preflight(uid(),{'survey':'source'})
    expected=finish_preflight(console,checked)
    started=console.start(uid(),checked['operation_id'])
    original=console.store.update
    def fail_commit(operation_id,**fields):
        if fields.get('state')=='finished':
            raise OSError('status commit failed after result')
        return original(operation_id,**fields)
    monkeypatch.setattr(console.store,'update',fail_commit)
    def launch(argv,**options):
        options['observer'](expected)
        return {'run_id':started['run_id'],'success':True}
    failure=execute(console.store,started['operation_id'],launch=launch)
    directory=console.store.directory(started['operation_id'])
    assert failure['error']=='status commit failed after result'
    assert read_json(directory/'result.json')['pipeline']['success'] is True
    assert read_json(directory/'failure.json')['error']==failure['error']
    assert console.store.status(started['operation_id'])['state']=='requires_recovery'


def test_shared_cli_guard_and_browser_dispatch_use_the_same_slot(console,monkeypatch):
    import operator_ui.storage as storage
    real_store=storage.ConsoleStore
    monkeypatch.setattr(storage,'ConsoleStore',lambda *_:console.store)
    with storage.cli_execution_guard(console.base_dir,'cli-run'):
        assert console.store.occupied()['kind']=='cli'
        with pytest.raises(ConsoleError,match='active'):
            console.preflight(uid(),{'survey':'source'})
    assert console.store.operations()[0]['state']=='finished'
    assert console.store.occupied() is None
    monkeypatch.setattr(storage,'ConsoleStore',real_store)


def test_webodm_links_are_derived_only_from_recorded_safe_configuration(console):
    seed(console,mode='task2')
    with sqlite3.connect(console.reader.database) as conn:
        conn.execute('INSERT INTO webodm_tasks(run_id,operation_key,project_id) VALUES (?,?,?)',('original','task2',544))
    operation_id=active_operation(console)
    atomic_json(console.store.directory(operation_id)/'preflight.json',{'webodm_url':'http://localhost:8000'})
    assert console.run_detail('original')['webodm_project_links']==[{'project_id':544,'url':'http://localhost:8000/dashboard/544'}]


def test_logs_are_bounded_run_attributed_and_rotation_safe(console):
    seed(console)
    console.reader.logs_root.mkdir()
    path=console.reader.logs_root/'pipeline.log'
    path.write_text('10:00 | INFO | pipeline | other | qgis | private-other-run\n'
                    '10:00 | INFO | pipeline | original | qgis | token=private\n',encoding='utf-8')
    first=console.reader.logs('original')
    assert len(first['items'])==1 and 'private' not in first['items'][0]['message']
    path.write_text('10:01 | INFO | pipeline | original | qgis | new rotated output\n',encoding='utf-8')
    second=console.reader.logs('original',first['cursor'])
    assert len(second['items'])==1 and 'rotated' in second['items'][0]['message']
    assert second['cursor']!=first['cursor']


def test_readonly_api_cannot_write_even_if_given_write_sql(console):
    with pytest.raises(sqlite3.OperationalError,match='readonly'):
        console.reader.query('DELETE FROM runs')


def test_imports_do_not_start_server_or_load_configuration(monkeypatch):
    import builtins
    monkeypatch.setattr(builtins,'input',Mock(side_effect=AssertionError('input')))
    for name in ('operator_ui.storage','operator_ui.projections','operator_ui.models','operator_ui.service',
                 'operator_ui.runner','operator_ui.api','run_pipeline_ui'):
        original = importlib.import_module(name)
        spec = importlib.util.spec_from_file_location('operator_ui.safe_' + name.rsplit('.',1)[-1], original.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
