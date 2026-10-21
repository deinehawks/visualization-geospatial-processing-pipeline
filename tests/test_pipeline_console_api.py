"""HTTP tested in-process; no listening sockets, real subprocesses, or source data."""
import json
import os
import asyncio
import selectors
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

pytest.importorskip('fastapi', reason='Optional console API tests require requirements-ui-dev.txt')
pytest.importorskip('httpx', reason='Optional console API test client')
from fastapi.testclient import TestClient

from operator_ui.api import create_app, ORIGIN
from operator_ui.storage import atomic_json, read_json
from tests.test_pipeline_console import console, uid, seed, active_operation, finish_preflight


class MemorySelector(selectors.BaseSelector):
    """ASGI scheduling only. Any attempt to register real I/O fails the test."""
    def register(self, *_args, **_kwargs):
        raise AssertionError('In-memory ASGI tests cannot register socket/file I/O')
    def unregister(self, *_args):
        raise AssertionError('No I/O was registered')
    def select(self, timeout=None):
        time.sleep(min(timeout or 0, 0.001))
        return []
    def get_map(self):
        return {}
    def close(self):
        pass


class MemoryLoop(asyncio.SelectorEventLoop):
    """Replace asyncio's socketpair wakeup, not the repository's network guards."""
    def __init__(self):
        super().__init__(selector=MemorySelector())
    def _make_self_pipe(self):
        pass
    def _write_to_self(self):
        pass
    def _close_self_pipe(self):
        pass


def asgi_client(app):
    return TestClient(app,base_url=ORIGIN,backend_options={'loop_factory':MemoryLoop})


@pytest.fixture
def client(console):
    app = create_app(console,launch_key='one-use-launch-key')
    with asgi_client(app) as client:
        yield client


def login(client):
    response = client.post('/api/bootstrap',json={},headers={'Origin':ORIGIN,'X-Launch-Key':'one-use-launch-key'})
    assert response.status_code == 200
    return {'Origin':ORIGIN,'X-Console-CSRF':response.json()['csrf']}


def test_health_does_not_read_database_or_create_runtime(client,console,monkeypatch):
    monkeypatch.setattr(console.reader,'query',Mock(side_effect=AssertionError('database probe')))
    assert client.get('/api/health').json()['status']=='ok'
    assert not console.store.root.exists()


def test_launcher_session_cookie_and_csrf_are_required(client,console):
    assert client.get('/api/runs').status_code==401
    headers=login(client)
    assert client.cookies.get('pipeline_ui_session')
    assert client.get('/api/runs').status_code==200
    assert client.post('/api/bootstrap',json={},headers={'Origin':ORIGIN,'X-Launch-Key':'one-use-launch-key'}).status_code==401
    assert client.post('/api/preflight',json={},headers={'Origin':ORIGIN}).status_code==403
    assert client.post('/api/preflight',json={'command_id':uid(),'inputs':{'survey':'test-source'}},headers=headers).status_code==200
    console.popen.assert_called_once()


@pytest.mark.parametrize('headers', [{'Host':'attacker.invalid:8765'},{'Origin':'https://attacker.invalid'},
                                   {'Host':'localhost:8765'}])
def test_untrusted_hosts_and_origins_are_rejected(client,headers):
    assert client.get('/api/health',headers=headers).status_code==403


def test_cookie_properties_and_read_responses_do_not_cache(client):
    response=client.post('/api/bootstrap',json={},headers={'Origin':ORIGIN,'X-Launch-Key':'one-use-launch-key'})
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/api' in cookie
    assert response.headers['cache-control']=='no-store'
    assert client.get('/api/session').json()['csrf']==response.json()['csrf']


def test_read_routes_expose_attempts_not_mutation_endpoints(client,console):
    seed(console,mode='task2')
    login(client)
    for suffix in ('','/stages','/events','/logs','/artifacts'):
        assert client.get('/api/runs/original'+suffix).status_code==200
    assert client.get('/api/runs/missing').status_code==404
    for suffix in ('/publish','/cleanup','/shell','/force-stage'):
        assert client.post('/api'+suffix,json={},headers={'Origin':ORIGIN}).status_code==403
    assert client.get('/docs').status_code==404
    console.popen.assert_not_called()


def test_full_http_preflight_start_and_duplicate_clicks(client,console):
    headers=login(client)
    preflight_id=uid()
    response=client.post('/api/preflight',json={'command_id':preflight_id,'inputs':{'survey':'test-source'}},headers=headers)
    checked=response.json()
    assert response.status_code==200
    command={'command_id':uid(),'preflight_id':preflight_id}
    assert client.post('/api/start',json=command,headers=headers).status_code==409
    finish_preflight(console,checked)
    started=client.post('/api/start',json=command,headers=headers)
    assert started.status_code==200 and started.json()['run_id']==checked['run_id']
    assert client.post('/api/start',json=command,headers=headers).status_code==200
    assert console.popen.call_count==2
    assert client.post('/api/start',json={**command,'command_id':uid()},headers=headers).status_code==409


@pytest.mark.parametrize('payload', [{'command_id':'../escape','inputs':{'survey':'site'}},
                                   {'command_id':'a'*32,'inputs':{'survey':'site','shell':'evil'}},
                                   {'command_id':'a'*32,'inputs':{'mode':'unknown','survey':'site'}}])
def test_schema_validation_prevents_cli_passthrough(client,console,payload):
    headers=login(client)
    assert client.post('/api/preflight',json=payload,headers=headers).status_code==422
    console.popen.assert_not_called()


def test_controls_and_stale_reviews_are_bound_to_owned_invocation(client,console):
    headers=login(client)
    operation_id=active_operation(console)
    assert client.post(f'/api/operations/{operation_id}/controls',json={'command_id':uid(),'action':'pause'},headers=headers).status_code==200
    assert client.post(f'/api/operations/{operation_id}/review',json={'command_id':uid(),'review_id':uid(),'action':'approve'},headers=headers).status_code==409
    assert client.post(f'/api/operations/{operation_id}/controls',json={'command_id':uid(),'action':'kill'},headers=headers).status_code==422
    console.store.processes.live[os.getpid()]='a-reused-pid'
    assert client.post(f'/api/operations/{operation_id}/controls',json={'command_id':uid(),'action':'stop'},headers=headers).status_code==409


def test_static_server_does_not_expose_repository_runtime_or_source(client,console,tmp_path):
    static=tmp_path/'static';static.mkdir();(static/'index.html').write_text('LOCAL CONSOLE',encoding='utf-8')
    app=create_app(console,launch_key='different',static_dir=static)
    with asgi_client(app) as browser:
        assert browser.get('/').text=='LOCAL CONSOLE'
        for path in ('/.env','/data/pipeline.db','/runtime/pipeline-ui/request.json','/api/unknown'):
            assert browser.get(path).status_code in (401,404)


def test_foreign_session_cannot_control_a_restarted_server(console):
    app=create_app(console,launch_key='first')
    with asgi_client(app) as old:
        response=old.post('/api/bootstrap',json={},headers={'Origin':ORIGIN,'X-Launch-Key':'first'})
        cookie=old.cookies.get('pipeline_ui_session')
        assert response.status_code==200
    with asgi_client(create_app(console,launch_key='second')) as new:
        new.cookies.set('pipeline_ui_session',cookie,path='/api')
        assert new.get('/api/operations').status_code==401


@pytest.mark.parametrize('fail',[False,True])
def test_launcher_only_opens_browser_after_server_is_ready(fail):
    from run_pipeline_ui import serve_and_open
    class Server:
        started=False
        async def serve(self):
            if fail:
                raise RuntimeError('port unavailable')
            await asyncio.sleep(0.001)
            self.started=True
            await asyncio.sleep(0.001)
    server=Server()
    opener=Mock(side_effect=lambda _:assert_ready())
    def assert_ready():
        assert server.started
    loop=MemoryLoop()
    try:
        if fail:
            with pytest.raises(RuntimeError,match='port unavailable'):
                loop.run_until_complete(serve_and_open(server,ORIGIN,opener))
            opener.assert_not_called()
        else:
            loop.run_until_complete(serve_and_open(server,ORIGIN,opener))
            opener.assert_called_once_with(ORIGIN)
    finally:
        loop.close()
