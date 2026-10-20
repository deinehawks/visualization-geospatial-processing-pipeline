"""Qt/QGIS-free V2 history, presets and editor-owned recovery snapshots."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .editor_model import EditorDesign, atomic_json, design_payload, parse_bounds, require_separate_workspace


class DesignHistory:
    def __init__(self, session, limit=100):
        self.session, self.limit = session, limit
        self.undo_steps, self.redo_steps = [], []
        self.current = session.snapshot()
        self.saved = copy.deepcopy(self.current)
        self.group_start = None

    @property
    def dirty(self):
        return self.current != self.saved

    def mark_saved(self):
        self.end_group()
        self.saved = copy.deepcopy(self.current)

    def begin_group(self):
        self.end_group()
        self.group_start = copy.deepcopy(self.current)

    def end_group(self):
        before, self.group_start = self.group_start, None
        if before is not None:
            self._record(before, self.current)

    def _record(self, before, after):
        if before != after:
            self.undo_steps.append((copy.deepcopy(before), copy.deepcopy(after)))
            self.undo_steps = self.undo_steps[-self.limit:]
            self.redo_steps.clear()

    def execute(self, action):
        before = self.session.snapshot()
        try:
            action()
            after = self.session.snapshot()
        except Exception as exc:
            rollback = self.group_start if self.group_start is not None else before
            self.group_start = None
            try:
                self.session.restore(rollback, force=True)
                self.current = copy.deepcopy(rollback)
            except Exception as rollback_exc:
                raise RuntimeError(f'Edit failed ({exc}); rollback also failed: {rollback_exc}') from exc
            raise
        self.current = after
        if self.group_start is None:
            self._record(before, after)

    def undo(self):
        self.end_group()
        if self.undo_steps:
            before, after = self.undo_steps[-1]
            self.session.restore(before)
            self.undo_steps.pop()
            self.redo_steps.append((before, after))
            self.current = copy.deepcopy(before)

    def redo(self):
        self.end_group()
        if self.redo_steps:
            before, after = self.redo_steps[-1]
            self.session.restore(after)
            self.redo_steps.pop()
            self.undo_steps.append((before, after))
            self.current = copy.deepcopy(after)


BUILTIN_LOCATIONS = (
    ('Davao City', '125.3063,6.8710,125.9308,7.3282'),
    ('Kapalong, Davao del Norte', '125.305271,7.564435,125.736933,8.000306'),
    ('Philippines', '116.8,4.4,127.0,21.3'),
)


def location_preset(name, bounds, crs='EPSG:4326', mode='pin'):
    return validate_preset({'version': 1, 'kind': 'location', 'name': name,
                            'label': name, 'bounds': parse_bounds(bounds), 'crs': crs, 'mode': mode})


def style_preset(name, design):
    return validate_preset({
        'version': 1, 'kind': 'style', 'name': name,
        'map_styles': copy.deepcopy(design.map_styles), 'grid_style': copy.deepcopy(design.grid_style),
        'text_styles': copy.deepcopy(design.text_styles),
        'options': {key: design.options[key] for key in
                    ('paper_size', 'grid_font_size', 'preview_dpi', 'pdf_dpi', 'inset_dpi')},
    })


def validate_preset(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('Unsupported preset version')
    result = copy.deepcopy(value)
    if not isinstance(result.get('name'), str) or not result['name'].strip():
        raise ValueError('Enter a preset name')
    if result.get('kind') == 'location':
        if set(result) != {'version', 'kind', 'name', 'label', 'bounds', 'crs', 'mode'}:
            raise ValueError('Unexpected location preset fields')
        result['bounds'] = parse_bounds(result['bounds'])
        if result['bounds'] is None or not isinstance(result['label'], str):
            raise ValueError('Location presets require bounds and a label')
        if result['crs'] not in ('project', 'EPSG:4326') or result['mode'] not in ('boundary', 'pin'):
            raise ValueError('Invalid location CRS or marker')
    elif result.get('kind') == 'style':
        if set(result) != {'version', 'kind', 'name', 'map_styles', 'grid_style', 'text_styles', 'options'}:
            raise ValueError('Unexpected style preset fields')
        allowed = {'paper_size', 'grid_font_size', 'preview_dpi', 'pdf_dpi', 'inset_dpi'}
        if set(result['options']) != allowed:
            raise ValueError('Style presets cannot include framing or location settings')
        trial = EditorDesign(Path('.'), options=result['options'],
                             text_styles=result['text_styles'], map_styles=result['map_styles'],
                             grid_style=result['grid_style']).validate()
        result['map_styles'], result['grid_style'] = trial.map_styles, trial.grid_style
    else:
        raise ValueError('Unknown preset type')
    return result


def save_preset(directory, preset, package):
    directory = require_separate_workspace(package, directory)
    if any((parent / 'attempt.json').is_file() for parent in (directory, *directory.parents)):
        raise ValueError('Export attempts cannot store presets')
    path = directory / (uuid.uuid4().hex + '.json')
    atomic_json(path, validate_preset(preset))
    return path


def load_presets(directory):
    presets = [location_preset(name, bounds) for name, bounds in BUILTIN_LOCATIONS]
    warnings = []
    for path in sorted(Path(directory).glob('*.json')):
        try:
            presets.append(validate_preset(json.loads(path.read_text(encoding='utf-8'))))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            warnings.append(f'{path.name}: {exc}')
    return presets, warnings


class RecoveryStore:
    """Only write private JSON; never replace named designs or delete recovery evidence."""
    def __init__(self, workspace, package, design_path=None):
        self.package = Path(package).resolve()
        self.workspace = require_separate_workspace(self.package, workspace)
        identity = os.path.normcase(str(self.package)) + '|' + (
            os.path.normcase(str(Path(design_path).resolve())) if design_path else '<new>')
        self.key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        self.directory = self.workspace / 'recovery' / self.key
        self.path = self.directory / (uuid.uuid4().hex + '.json')

    def _check_directory(self):
        if not self.directory.resolve().is_relative_to(self.workspace):
            raise ValueError('Recovery directory escapes its workspace')
        if any((parent / 'attempt.json').is_file() for parent in (self.directory, *self.directory.parents)):
            raise ValueError('Export attempts cannot store recovery files')

    def write(self, design):
        self._check_directory()
        if design.package_dir.resolve() != self.package:
            raise ValueError('Recovery package does not match design')
        payload = {'version': 1, 'key': self.key, 'status': 'active',
                   'saved_at': datetime.now(timezone.utc).isoformat(),
                   'design': design_payload(design, self.directory)}
        atomic_json(self.path, payload)

    def candidates(self):
        self._check_directory()
        candidates, warnings = [], []
        for path in self.directory.glob('*.json'):
            if path == self.path:
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if data.get('version') != 1 or data.get('key') != self.key:
                    raise ValueError('Invalid recovery ownership')
                if data.get('status') == 'active':
                    datetime.fromisoformat(data['saved_at'])
                    candidates.append((data['saved_at'], path))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                warnings.append(f'{path.name}: {exc}')
        return sorted(candidates, reverse=True), warnings

    def resolve(self, path=None):
        self._check_directory()
        path = Path(path) if path else self.path
        if path.parent.resolve() != self.directory.resolve() or path.is_symlink():
            raise ValueError('Recovery file is outside this design workspace')
        if path.exists():
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('key') != self.key:
                raise ValueError('Recovery ownership mismatch')
            data['status'] = 'resolved'
            atomic_json(path, data)

    def read(self, path):
        self._check_directory()
        path = Path(path)
        if path.parent.resolve() != self.directory.resolve() or path.is_symlink():
            raise ValueError('Recovery file is outside this design workspace')
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1 or data.get('key') != self.key or data.get('status') != 'active':
            raise ValueError('Invalid active recovery snapshot')
        value = data['design']
        if value.get('version') != 2:
            raise ValueError('Invalid recovery design version')
        package = (path.parent / value['package']).resolve()
        if package != self.package:
            raise ValueError('Recovery references another package')
        # Validate referenced inputs only after the operator explicitly requests recovery.
        from .editor_model import validate_package
        validate_package(package)
        return EditorDesign(package, value['options'], value['maps'], value['texts'],
                            value['text_styles'], value['map_styles'], value['grid_style']).validate()
