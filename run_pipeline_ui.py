"""Explicit local server launcher; importing it starts nothing."""
from pathlib import Path
import asyncio
import secrets
import webbrowser


async def serve_and_open(server, url, opener=webbrowser.open):
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(0.05)
    if server.started:
        opener(url)
    await task


def main():
    from operator_ui.api import ORIGIN, create_app
    from operator_ui.service import ConsoleService
    from operator_ui.storage import WindowsMutex, ConsoleError
    import uvicorn

    base = Path(__file__).resolve().parent
    static = base / 'web' / 'pipeline-console' / 'out'
    if not (static / 'index.html').is_file():
        raise SystemExit('Build the console first: see docs/operators/pipeline-web-console.md')
    key = secrets.token_urlsafe(32)
    try:
        with WindowsMutex(str(base) + ':web-server'):
            app = create_app(ConsoleService(base), launch_key=key, static_dir=static)
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8765, workers=1, access_log=False))
            asyncio.run(serve_and_open(server, ORIGIN + '/#launch=' + key))
    except ConsoleError as exc:
        if exc.code != 'BUSY':
            raise
        webbrowser.open(ORIGIN)


if __name__ == '__main__':
    main()
