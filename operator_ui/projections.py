"""Bounded read projections: never initialize schemas or load .env."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from .storage import ConsoleError


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()
                if not any(word in key.lower() for word in ('password', 'token', 'secret', 'authorization', 'cookie', 'username', 'credential', 'api_key'))}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', value)
    value = re.sub(r'(?i)(authorization\s*:\s*)(?:Bearer|Basic)\s+\S+', r'\1[REDACTED]', value)
    value = re.sub(r'''(?i)((?:password|token|secret|authorization|cookie|api[_-]?key)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,}]+)''',
                   r'\1[REDACTED]', value)
    return re.sub(r'https?://[^\s"<>]+', lambda match: safe_url(match.group()), value)


def safe_url(value):
    try:
        url = urlsplit(value)
        host = url.hostname
        if not host or url.scheme not in ('http', 'https'):
            return '[invalid URL]'
        if ':' in host:
            host = '[' + host + ']'
        return urlunsplit((url.scheme, host + (f':{url.port}' if url.port else ''), url.path, '', ''))
    except ValueError:
        return '[invalid URL]'


def object_json(raw):
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


class ReadModel:
    def __init__(self, database, logs_root):
        self.database, self.logs_root = Path(database), Path(logs_root)

    def query(self, sql, parameters=()):
        if not self.database.is_file():
            return []
        with closing(sqlite3.connect(self.database.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            return [dict(row) for row in conn.execute(sql, parameters)]

    def runs(self, before=None, limit=50):
        limit = max(1, min(int(limit), 100))
        try:
            cursor = int(before) if before is not None else None
        except ValueError as exc:
            raise ConsoleError('Invalid pagination cursor', 'INVALID_INPUT', 422) from exc
        rows = self.query('SELECT rowid AS _position, * FROM runs WHERE (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT ?',
                          (cursor, cursor, limit + 1))
        result = []
        for row in rows[:limit]:
            latest = self.query('SELECT stage_name, status, error_message FROM stages WHERE run_id=? ORDER BY id DESC LIMIT 1',
                                (row['run_id'],))
            result.append({key: value for key, value in {**row, 'latest_stage': latest[0] if latest else None}.items() if key != '_position'})
        return redact({'items': result, 'next_cursor': str(rows[limit - 1]['_position']) if len(rows) > limit else None,
                       'availability': 'available' if self.database.is_file() else 'database_missing'})

    def detail(self, run_id):
        rows = self.query('SELECT * FROM runs WHERE run_id=?', (run_id,))
        if not rows:
            raise ConsoleError('Run not found', 'RUN_NOT_FOUND', 404)
        stages = self.query('SELECT id, run_id, stage_name, status, started_at, finished_at, runtime_seconds, error_message, '
                            'substr(output_json,1,262144) AS output_json, length(output_json)>262144 AS output_truncated '
                            'FROM stages WHERE run_id=? ORDER BY id DESC LIMIT 1001', (run_id,))
        if len(stages) > 1000:
            raise ConsoleError('Stage history exceeds bounded V1 view', 'HISTORY_LIMIT')
        stages.reverse()
        latest = {}
        counts = {}
        for stage in stages:
            name = stage['stage_name']
            counts[name] = counts.get(name, 0) + 1
            stage['attempt_number'] = counts[name]
            stage['output'] = object_json(stage.pop('output_json', None))
            latest[name] = stage['id']
        for stage in stages:
            stage['authoritative_attempt'] = latest[stage['stage_name']] == stage['id']
        try:
            bindings = self.query('SELECT * FROM webodm_tasks WHERE run_id=? ORDER BY id DESC LIMIT 1000', (run_id,))
        except sqlite3.OperationalError as exc:
            if 'no such table' not in str(exc):
                raise
            bindings = []
        canonical = {}
        for binding in bindings:
            for field in ('options_json', 'audit_json'):
                binding[field.removesuffix('_json')] = object_json(binding.pop(field, None))
            key = binding.get('operation_key')
            if key in ('task2', 'task4') and key not in canonical:
                canonical[key] = binding
        mode = None
        for stage in reversed(stages):
            output = stage['output']
            candidate = output.get('mode') or output.get('webodm_mode')
            if candidate in ('task2', 'task4', 'both'):
                mode = candidate
                break
        # Incomplete combined runs must not be guessed to be Task 4-only from one binding.
        if mode is None and {'task2', 'task4'} <= set(canonical):
            mode = 'both'
        return redact({'run': rows[0], 'stages': stages, 'operations': list(canonical.values()),
                       'mode': mode, 'mode_requires_confirmation': mode is None,
                       'remote_status_source': 'last_persisted_observation', 'artifacts': self.artifacts(stages)})

    @staticmethod
    def artifacts(stages):
        paths = []
        def visit(value, name):
            if isinstance(value, dict):
                for key, child in value.items():
                    visit(child, f'{name}.{key}')
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f'{name}.{index}')
            elif isinstance(value, str) and (':\\' in value or value.startswith(('\\\\', '/')) or ':/' in value):
                paths.append({'logical_name': name, 'path': value, 'availability': 'recorded_not_probed'})
        for stage in stages:
            if stage['authoritative_attempt']:
                visit(stage['output'], stage['stage_name'])
        return paths[:500]

    def logs(self, run_id, cursor=None):
        detail = self.detail(run_id)
        directory = self.logs_root
        root = self.logs_root.resolve()
        if directory.is_symlink() or not directory.resolve().is_relative_to(root) or not directory.is_dir():
            return {'items': [], 'cursor': None, 'availability': 'unavailable'}
        # Only known logical log files, bounded tails, no arbitrary path endpoint.
        values, positions = [], {}
        for name in ('pipeline.log', 'webodm.log', 'qgis.log', 'data_segregation.log', 'cross_run_filter.log', 'kml.log'):
            path = directory / name
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            start = max(0, stat.st_size - 65536)
            with path.open('rb') as stream:
                stream.seek(start)
                data = stream.read(65536).decode('utf-8', errors='replace')
            lines = data.splitlines()[1:] if start else data.splitlines()
            for index, line in enumerate(lines):
                parts = line.split('|', 5)
                if len(parts) >= 6 and parts[3].strip() == run_id:
                    values.append({'event_id': f'{name}:{stat.st_mtime_ns}:{index}', 'message': redact(line),
                                   'source': 'text_log'})
            positions[name] = [stat.st_size, stat.st_mtime_ns]
        return {'items': values[-500:], 'cursor': positions, 'availability': 'bounded_tail'}
