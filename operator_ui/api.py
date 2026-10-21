"""Loopback-only FastAPI application factory; no import-time resource access."""
from contextlib import asynccontextmanager
import secrets

from .models import ControlCommand, PreflightCommand, ReconcileCommand, ReviewCommand, StartCommand
from .storage import ConsoleError

ORIGIN = 'http://127.0.0.1:8765'


def create_app(service, *, launch_key, static_dir=None, on_start=None):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    import sqlite3

    @asynccontextmanager
    async def lifespan(app):
        if on_start is not None:
            on_start()
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    bootstrap = {'key': launch_key}
    service.actor_id = 'local-session:' + secrets.token_hex(12)

    @app.exception_handler(ConsoleError)
    async def console_error(_request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': str(exc)}}, status_code=exc.status)

    @app.exception_handler(sqlite3.Error)
    async def database_error(_request, _exc):
        return JSONResponse({'error': {'code': 'DATABASE_UNAVAILABLE',
            'message': 'Pipeline database is busy, unreadable, or has an unsupported schema. No migration was attempted.'}}, status_code=503)

    @app.exception_handler(OSError)
    async def filesystem_error(_request, _exc):
        return JSONResponse({'error': {'code': 'FILESYSTEM_UNAVAILABLE',
            'message': 'A local console or log file could not be read. Existing evidence was retained.'}}, status_code=503)

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.headers.get('host') != '127.0.0.1:8765':
            return JSONResponse({'error': {'code': 'INVALID_HOST'}}, status_code=403)
        origin = request.headers.get('origin')
        if origin is not None and origin != ORIGIN:
            return JSONResponse({'error': {'code': 'INVALID_ORIGIN'}}, status_code=403)
        if request.method not in ('GET', 'HEAD'):
            if origin != ORIGIN:
                return JSONResponse({'error': {'code': 'ORIGIN_REQUIRED'}}, status_code=403)
            try:
                length = int(request.headers.get('content-length', '-1'))
            except ValueError:
                length = -1
            if not 0 <= length <= 65536:
                return JSONResponse({'error': {'code': 'BODY_LIMIT'}}, status_code=413)
        protected = request.url.path.startswith('/api/') and request.url.path not in ('/api/health', '/api/bootstrap')
        if protected:
            if not secrets.compare_digest(request.cookies.get('pipeline_ui_session', ''), session):
                return JSONResponse({'error': {'code': 'SESSION_REQUIRED', 'message': 'Open the console with its launcher.'}}, status_code=401)
            if request.method not in ('GET', 'HEAD') and not secrets.compare_digest(request.headers.get('x-console-csrf', ''), csrf):
                return JSONResponse({'error': {'code': 'CSRF_REQUIRED'}}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        return response

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'scope': 'local-pipeline-console-v1'}

    @app.post('/api/bootstrap')
    def exchange(request: Request):
        key = request.headers.get('x-launch-key', '')
        if not bootstrap['key'] or not secrets.compare_digest(key, bootstrap['key']):
            raise ConsoleError('Open the console using its launcher', 'SESSION_REQUIRED', 401)
        bootstrap['key'] = None
        response = JSONResponse({'csrf': csrf})
        response.set_cookie('pipeline_ui_session', session, httponly=True, samesite='strict', path='/api')
        return response

    @app.get('/api/session')
    def current_session():
        return {'csrf': csrf}

    @app.get('/api/runs')
    def runs(before: str | None = None, limit: int = 50):
        return service.reader.runs(before, limit)

    @app.get('/api/runs/{run_id}')
    def detail(run_id: str):
        return service.run_detail(run_id)

    @app.get('/api/runs/{run_id}/stages')
    def stages(run_id: str):
        return {'items': service.reader.detail(run_id)['stages']}

    @app.get('/api/runs/{run_id}/artifacts')
    def artifacts(run_id: str):
        return {'items': service.reader.detail(run_id)['artifacts']}

    @app.get('/api/runs/{run_id}/events')
    @app.get('/api/runs/{run_id}/logs')
    def logs(run_id: str):
        return service.reader.logs(run_id)

    @app.get('/api/operations')
    def operations():
        execution = service.execution_status()
        return {'items': [service.operation(row['operation_id']) for row in service.store.operations()],
                'legacy_running': execution['legacy_running'], 'execution': execution}

    @app.get('/api/execution')
    def execution_status():
        return service.execution_status()

    @app.get('/api/operations/{operation_id}')
    def operation(operation_id: str):
        return service.operation(operation_id)

    @app.post('/api/preflight')
    def preflight(command: PreflightCommand):
        return service.preflight(command.command_id, command.inputs.model_dump())

    @app.post('/api/start')
    def start(command: StartCommand):
        return service.start(command.command_id, command.preflight_id)

    @app.post('/api/operations/{operation_id}/controls')
    def control(operation_id: str, command: ControlCommand):
        return service.control(operation_id, command.command_id, command.action)

    @app.post('/api/operations/{operation_id}/review')
    def review(operation_id: str, command: ReviewCommand):
        return service.review(operation_id, command)

    @app.post('/api/operations/{operation_id}/reconcile')
    def reconcile(operation_id: str, command: ReconcileCommand):
        return service.store.reconcile(operation_id, command.confirmation)

    if static_dir is not None:
        # Fixed build directory only; never mount surveys, runtime, logs, or repository roots.
        app.mount('/', StaticFiles(directory=str(static_dir), html=True), name='console')
    return app
