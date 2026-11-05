"""Explicit local execution policy and run-ID controls; never loads .env."""
import argparse
import json
from pathlib import Path
import sys
import uuid

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operator_ui.controls import request_control
from operator_ui.projections import redact
from operator_ui.storage import ConsoleError, ConsoleStore
from operator_ui.legacy_reconciliation import reconcile_legacy


def main(argv=None, *, store=None, database=None, inventory=None, ups_inspector=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    configure = commands.add_parser('configure')
    configure.add_argument('--max-runs', type=int, choices=(1, 2), required=True)
    legacy = commands.add_parser('reconcile-legacy', help='Preview one historical record; explicit confirmation applies a local-only receipt')
    legacy.add_argument('--run-id', required=True)
    legacy.add_argument('--confirmation', default=None)
    legacy.add_argument('--allow-ups-discord-pid', type=int, default=None,
                        help='Explicitly verify this one UPSDiscord.py invocation; no permanent process whitelist')
    for name in ('pause', 'stop'):
        command = commands.add_parser(name)
        command.add_argument('--run-id', required=True)
        command.add_argument('--command-id', default=None)
    args = parser.parse_args(argv)
    base = Path(__file__).resolve().parents[1]
    store = store or ConsoleStore(base / 'runtime' / 'pipeline-ui')
    database = Path(database) if database is not None else base / 'data' / 'pipeline.db'
    if args.command == 'reconcile-legacy':
        result = reconcile_legacy(store, database, args.run_id, confirmation=args.confirmation,
                                  allow_ups_discord_pid=args.allow_ups_discord_pid,
                                  **({'inventory': inventory} if inventory is not None else {}),
                                  **({'ups_inspector': ups_inspector} if ups_inspector is not None else {}))
    elif args.command == 'configure':
        result = store.configure(args.max_runs, database=database)
    elif args.command == 'status':
        with store.lock():
            result = store.capacity()
            from operator_ui.legacy_reconciliation import legacy_status
            result.update(legacy_status(store, database))
            try:
                store.verify_legacy(database)
            except ConsoleError as exc:
                result.update(available=0, blocked=True, warning=str(exc))
    else:
        with store.lock():
            candidates = [row for row in store.occupants() if row.get('run_id') == args.run_id and row['kind'] in ('cli', 'run')]
            if len(candidates) != 1:
                raise ConsoleError('No single owned invocation matches this run ID', 'ACTION_UNAVAILABLE')
            result = request_control(store, candidates[0]['operation_id'], args.command_id or uuid.uuid4().hex, args.command)
    print(json.dumps(redact(result), indent=2))
    return result


if __name__ == '__main__':
    try:
        main()
    except ConsoleError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
