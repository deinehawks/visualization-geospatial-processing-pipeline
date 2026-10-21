"""Run-specific cooperative controls; standard library only for terminal use."""
from .storage import ConsoleError, atomic_json, identifier, now, read_json


class LocalControls:
    def __init__(self, store, operation_id):
        self.store, self.operation_id = store, operation_id
        self.directory = store.directory(operation_id)

    def poll(self):
        directory = self.directory / 'controls'
        if not directory.exists():
            return None
        with self.store.lock():
            for path in sorted(directory.glob('*.json')):
                if path.name.endswith('.ack.json'):
                    continue
                ack = path.with_name(path.stem + '.ack.json')
                if ack.exists():
                    continue
                command = read_json(path)
                if command.get('action') not in ('pause', 'stop'):
                    raise ConsoleError('Invalid owned control command')
                atomic_json(ack, {**command, 'state': 'acknowledged', 'at': now()})
                return command['action']
        return None


def request_control(store, operation_id, command_id, action, audit=None):
    if action not in ('pause', 'stop'):
        raise ConsoleError('Unsupported local control', 'INVALID_INPUT', 422)
    with store.lock():
        directory = store.directory(operation_id) / 'controls'
        path = directory / (identifier(command_id) + '.json')
        payload = {'command_id': command_id, 'action': action}
        if path.exists():
            if read_json(path) != payload:
                raise ConsoleError('Command ID already used', 'IDEMPOTENCY_CONFLICT')
            ack = path.with_name(path.stem + '.ack.json')
            return read_json(ack) if ack.exists() else {**payload, 'state': 'requested'}
        value = store.status(operation_id)
        live = store.processes.identity(value['pid']) if value.get('pid') else None
        if not value.get('birth') or live != value['birth'] or value['kind'] not in ('cli', 'run') or value['state'] not in ('running', 'awaiting_review'):
            raise ConsoleError('This invocation does not accept controls', 'ACTION_UNAVAILABLE')
        directory.mkdir(exist_ok=True)
        for pending in directory.glob('*.json'):
            if not pending.name.endswith('.ack.json') and not pending.with_name(pending.stem + '.ack.json').exists():
                raise ConsoleError('A local control request is already pending', 'CONTROL_PENDING')
        if audit is not None:
            audit(operation_id, command_id, action)
        else:
            audit_dir = store.directory(operation_id) / 'audit'
            audit_dir.mkdir(exist_ok=True)
            atomic_json(audit_dir / (command_id + '.json'), {'actor_id': 'local-cli', 'action': action, 'at': now()})
        atomic_json(path, payload)
        return {**payload, 'state': 'requested'}
