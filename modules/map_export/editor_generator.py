"""Bounded, database-free processed-survey discovery and reference packaging.

Importing this module performs no discovery, configuration loading, or GIS work.
"""
from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .editor_model import REPO_ROOT, atomic_json, validate_package
from .map_package_exporter import ResolvedMapSurvey, export_map_package, _slugify
from .survey_manifest import _resolve_publication_artifact_path

SURVEY_ID = re.compile(r'^[A-Za-z]{2,5}-\d{6}$')


def contained(path, parent):
    return Path(path).resolve().is_relative_to(Path(parent).resolve())


def read_object(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return value


def ordered_roots(values):
    roots = []
    for value in values:
        path = Path(value).resolve()
        if path == Path(path.anchor) or path == REPO_ROOT.resolve():
            raise ValueError(f'Select a survey directory, not a filesystem/repository root: {path}')
        if path not in roots:
            roots.append(path)
    return roots


def survey_identity(folder):
    folder = Path(folder).resolve()
    manifest_path = folder / 'rgb' / 'manifest.json'
    manifest = read_object(manifest_path) if contained(manifest_path, folder) and manifest_path.is_file() else {}
    name = folder.name.upper() if SURVEY_ID.fullmatch(folder.name) else None
    declared = manifest.get('survey_id')
    if declared is not None:
        declared = str(declared).upper()
        if not SURVEY_ID.fullmatch(declared) or (name and name != declared):
            raise ValueError(f'Invalid or conflicting survey ID in {manifest_path}')
    identity = name or declared
    if not identity or not contained(folder / 'rgb', folder) or not (folder / 'rgb').is_dir():
        raise ValueError(f'Not a processed survey folder (expected AH-026xxx/rgb): {folder}')
    return identity, manifest


def find_survey_folders(query, roots):
    """Inspect direct locations and immediate YYYY folders only; never rglob roots."""
    query = str(query).strip()
    if not query:
        raise ValueError('Survey input must not be empty')
    if not SURVEY_ID.fullmatch(query):
        folder = Path(query).resolve()
        if folder.name.lower() == 'rgb':
            folder = folder.parent
        survey_identity(folder)
        return [folder]
    matches = []
    for root in ordered_roots(roots):
        if not root.is_dir():
            raise FileNotFoundError(f'Survey root unavailable: {root}')
        locations = [root]
        locations.extend(p for p in sorted(root.iterdir())
                         if re.fullmatch(r'\d{4}', p.name) and contained(p, root) and p.is_dir())
        for location in locations:
            candidates = [location] if location.name.upper() == query.upper() else []
            candidates.extend(p for p in sorted(location.iterdir())
                              if contained(p, root) and p.is_dir() and
                              (p.name.upper() == query.upper() or
                               (contained(p / 'rgb/manifest.json', p) and (p / 'rgb/manifest.json').is_file())))
            for folder in candidates:
                identity, _ = survey_identity(folder)
                if identity == query.upper() and folder.resolve() not in matches:
                    matches.append(folder.resolve())
    if not matches:
        raise FileNotFoundError(f'No processed folder for {query} in the selected roots')
    return matches


def artifact_candidates(folder):
    folder = Path(folder).resolve()
    identity, manifest = survey_identity(folder)
    rgb = folder / 'rgb'
    warnings, published = [], []

    def accept(path):
        path = Path(path)
        if not contained(path, folder):
            warnings.append(f'Ignored manifest reference outside selected survey: {path}')
            return None
        return path.resolve() if path.is_file() else None

    publication_path = rgb / 'publication.json'
    if contained(publication_path, folder) and publication_path.is_file():
        publication = read_object(publication_path)
        published_root = Path(publication.get('published_root') or rgb)
        if not published_root.is_absolute():
            published_root = rgb / published_root
        if not contained(published_root, folder):
            warnings.append('Ignored publication root outside selected survey')
        else:
            artifacts = publication.get('artifacts') or []
            if not isinstance(artifacts, list) or any(not isinstance(a, dict) for a in artifacts):
                raise ValueError(f'Malformed artifacts in {publication_path}')
            for artifact in artifacts:
                path = _resolve_publication_artifact_path(artifact=artifact, published_root=published_root.resolve())
                if path is None:
                    warnings.append('Ignored unsafe publication artifact reference')
                else:
                    path = accept(path)
                    if path:
                        published.append(path)

    boundaries = [p for p in published if p.suffix.lower() in {'.kml', '.kmz'}]
    boundaries.sort(key=lambda p: (p.suffix.lower() != '.kmz', len(p.parts), p.name.lower()))
    rasters = []
    kml = manifest.get('kml_file')
    if kml:
        path = Path(str(kml))
        path = accept(path if path.is_absolute() else rgb / path)
        if path and path.suffix.lower() in {'.kml', '.kmz'}:
            boundaries.append(path)
    boundary_dir = rgb / 'boundary'
    if contained(boundary_dir, folder) and boundary_dir.is_dir():
        boundaries.extend(p.resolve() for p in sorted(boundary_dir.iterdir())
                          if p.suffix.lower() in {'.kmz', '.kml'} and contained(p, folder) and p.is_file())
    rasters.extend(p for p in published if p.suffix.lower() in {'.tif', '.tiff'} and
                   p.name.lower().startswith('orthomosaic'))
    ortho_dir = rgb / 'qgis/clipped/ortho'
    if contained(ortho_dir, folder) and ortho_dir.is_dir():
        rasters.extend(p.resolve() for p in sorted(ortho_dir.iterdir())
                       if p.suffix.lower() in {'.tif', '.tiff'} and p.name.lower().startswith('orthomosaic-clipped--')
                       and contained(p, folder) and p.is_file())

    def rank(path):
        name = path.name.lower()
        task = 0 if ('-t4' in name or 'task4' in name) else 2 if ('-t2' in name or 'task2' in name) else 1
        return task, 0 if path in published else 1, name

    return {'survey_id': identity, 'folder': str(folder),
            'boundaries': [str(p) for p in dict.fromkeys(boundaries)],
            'orthomosaics': [str(p) for p in sorted(set(rasters), key=rank)], 'warnings': warnings}


def discover_inputs(inputs, roots):
    if not inputs:
        raise ValueError('Add at least one processed survey')
    result = []
    for row in inputs:
        folders = find_survey_folders(row['query'], roots)
        result.append({'query': row['query'], 'matches': [artifact_candidates(p) for p in folders]})
    return result


def resolve_selection(inputs, roots, include_orthomosaic):
    discovered = discover_inputs(inputs, roots)
    selected, seen = [], set()
    for row, found in zip(inputs, discovered):
        matches = found['matches']
        chosen = row.get('folder') or (matches[0]['folder'] if len(matches) == 1 else None)
        match = next((m for m in matches if m['folder'] == chosen), None)
        if match is None:
            raise ValueError(f'Choose an explicit survey folder for {row["query"]}')
        if match['survey_id'] in seen:
            raise ValueError(f'Duplicate survey: {match["survey_id"]}')
        seen.add(match['survey_id'])
        boundary = row.get('boundary') or next(iter(match['boundaries']), None)
        raster = row.get('orthomosaic') or next(iter(match['orthomosaics']), None)
        if boundary not in match['boundaries']:
            raise FileNotFoundError(f'No valid selected boundary for {match["survey_id"]}')
        if include_orthomosaic and raster not in match['orthomosaics']:
            raise FileNotFoundError(f'No valid selected orthomosaic for {match["survey_id"]}')
        selected.append(ResolvedMapSurvey(match['survey_id'], Path(chosen), Path(boundary),
                                         Path(raster) if include_orthomosaic else None))
    return selected


def validate_output_root(output_root, surveys):
    root = Path(output_root).resolve()
    if root == Path(root.anchor) or root == REPO_ROOT.resolve():
        raise ValueError('Output must not be a filesystem/share or repository root')
    for survey in surveys:
        if contained(root, survey.survey_dir) or contained(survey.survey_dir, root):
            raise ValueError(f'Output overlaps a selected survey: {survey.survey_dir}')
    return root


def generate_package(request, on_output=None):
    include = request.get('map_type', 'orthomosaic') == 'orthomosaic'
    if request.get('map_type', 'orthomosaic') not in {'orthomosaic', 'boundary'}:
        raise ValueError('Unknown map type')
    if not str(request.get('map_name', '')).strip() or not str(request.get('title', '')).strip():
        raise ValueError('Map name and title are required')
    print('Checking processed survey inputs...', flush=True)
    surveys = resolve_selection(request['inputs'], request.get('roots', []), include)
    output = validate_output_root(request['output_root'], surveys)
    name = f'{_slugify(request["map_name"])}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex}'
    if on_output is not None:
        on_output(output / name)
    print('Creating reference-only map package...', flush=True)
    result = export_map_package(
        survey_names=[s.survey_name for s in surveys], source_root=surveys[0].survey_dir,
        output_root=output, map_name=name, title=request['title'], location=request.get('location'),
        include_orthomosaic=include, resolved_surveys=surveys, orthomosaic_storage='reference',
        exclusive_output=True,
    )
    print('Validating completed package and TIFF references...', flush=True)
    validate_generated_package(result.output_dir, request)
    return result.output_dir


def validate_generated_package(path, request):
    package = Path(path).resolve()
    if package.parent != Path(request['output_root']).resolve() or not package.name.startswith(_slugify(request['map_name']) + '-'):
        raise ValueError('Generator result is outside the captured output location')
    _, metadata = validate_package(package)
    count = len(request['inputs'])
    if metadata.get('survey_count') != count or len(metadata.get('boundaries', [])) != count:
        raise ValueError('Generated package omitted a survey')
    expected = count if request.get('map_type', 'orthomosaic') == 'orthomosaic' else 0
    if len(metadata.get('orthomosaics', [])) != expected:
        raise ValueError('Generated package has missing or unexpected TIFF references')
    return package


def load_root_preferences(path):
    if not Path(path).is_file():
        return []
    value = read_object(path)
    if value.get('version') != 1 or not isinstance(value.get('roots'), list) or any(not isinstance(r, str) for r in value['roots']):
        raise ValueError('Invalid generator root preferences')
    # Do not stat, enumerate, or resolve remembered survey roots on startup.
    return value['roots']


def request_storage_locations(request):
    locations = [Path(p) for p in request.get('roots', [])]
    for row in request['inputs']:
        if not SURVEY_ID.fullmatch(row['query']):
            folder = Path(row['query'])
            locations.append(folder.parent if folder.name.lower() == 'rgb' else folder)
    return locations


class HelperGate:
    """Single application-owned operation slot; no queues or background workers."""
    def __init__(self):
        self.owner = None

    def acquire(self, owner):
        if self.owner is not None:
            raise RuntimeError('Another discovery, generation, or export operation is running')
        self.owner = owner

    def release(self, owner):
        if self.owner is owner:
            self.owner = None


def run_generator_request(request_path):
    """Internal non-QGIS helper entry point. One immutable request per attempt."""
    request_path = Path(request_path).resolve()
    attempt = request_path.parent
    marker = attempt / 'attempt.json'
    state = read_object(marker)
    if request_path.name != 'generator-request.json' or state != {'version': 1, 'kind': 'generator', 'status': 'incomplete'}:
        raise ValueError('Generator requires a new editor-owned incomplete attempt')
    if (attempt / 'result.json').exists():
        raise FileExistsError('Generator result already exists; create a new attempt')
    try:
        request = read_object(request_path)
        if request.get('version') != 1:
            raise ValueError('Unsupported generator request version')
        if any(contained(attempt, p) for p in request_storage_locations(request)):
            raise ValueError('Attempt workspace must be separate from selected survey storage')
        operation = request.get('operation')
        if operation == 'discover':
            print('Discovering processed survey candidates...', flush=True)
            result = {'surveys': discover_inputs(request['inputs'], request.get('roots', []))}
        elif operation == 'generate':
            def record_output(path):
                state['package'] = str(path)
                atomic_json(marker, state)
            result = {'package': str(generate_package(request, record_output))}
        else:
            raise ValueError('Unknown generator operation')
        atomic_json(attempt / 'result.json', result)
        state.update(status='complete', operation=operation)
        atomic_json(marker, state)
        print(f'{operation.title()} complete: {attempt}', flush=True)
        return result
    except Exception as exc:
        state.update(status='failed', error=str(exc))
        atomic_json(marker, state)
        print(f'Generator failed: {exc}', flush=True)
        raise


class GeneratorJob:
    """Injected QProcess boundary, immutable requests, retained failure evidence."""
    def __init__(self, process_factory, gate):
        self.process_factory, self.gate = process_factory, gate
        self.process = self.attempt = self.request = None
        self.cancel_requested = False

    @property
    def busy(self):
        return self.process is not None

    def start(self, request, workspace, program, entrypoint):
        if self.busy:
            raise RuntimeError('A generator operation is already running')
        self.gate.acquire(self)
        self.attempt = None
        try:
            self.request = copy.deepcopy(request)
            self.request['version'] = 1
            workspace = Path(workspace).resolve()
            if workspace == Path(workspace.anchor) or workspace == REPO_ROOT.resolve():
                raise ValueError('Invalid generator attempt workspace')
            # Parent UI performs lexical checks only. Physical source-path checks
            # (including network/junction resolution) belong to the cancellable helper.
            protected = request_storage_locations(self.request)
            if any(workspace.is_relative_to(Path(os.path.abspath(p))) for p in protected):
                raise ValueError('Attempt workspace must be separate from selected survey storage')
            self.attempt = workspace / f'{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex}'
            self.attempt.mkdir(parents=True, exist_ok=False)
            atomic_json(self.attempt / 'generator-request.json', self.request)
            atomic_json(self.attempt / 'attempt.json', {'version': 1, 'kind': 'generator', 'status': 'incomplete'})
            self.cancel_requested = False
            self.process = self.process_factory()
            self.process.setProgram(str(program))
            self.process.setArguments([str(entrypoint), '--generator-request', str(self.attempt / 'generator-request.json')])
            self.process.start()
        except Exception as exc:
            try:
                if self.process is not None:
                    self.finish(-1)
                elif self.attempt is not None and (self.attempt / 'attempt.json').is_file():
                    state = read_object(self.attempt / 'attempt.json')
                    state.update(status='failed', error=str(exc))
                    atomic_json(self.attempt / 'attempt.json', state)
            finally:
                self.gate.release(self)
            raise
        return self.attempt

    def cancel(self):
        if self.process is not None:
            self.cancel_requested = True
            self.process.terminate()

    def finish(self, exit_code):
        if self.process is None:
            return None
        marker = self.attempt / 'attempt.json'
        state = None
        try:
            state = read_object(marker)
            if self.cancel_requested:
                state['status'] = 'cancelled'
            elif exit_code != 0 or state.get('status') != 'complete':
                state['status'] = 'failed'
            else:
                result = read_object(self.attempt / 'result.json')
                if self.request['operation'] == 'generate':
                    if result.get('package') != state.get('package'):
                        raise ValueError('Generator result does not match its attempt receipt')
                    validate_generated_package(result['package'], self.request)
            state['exit_code'] = exit_code
            atomic_json(marker, state)
            return state
        except Exception as exc:
            if state is not None:
                state.update(status='failed', error=str(exc))
                atomic_json(marker, state)
            else:
                # Preserve a corrupt receipt as evidence instead of overwriting it.
                atomic_json(self.attempt / 'finish-error.json', {'status': 'failed', 'error': str(exc)})
            raise
        finally:
            self.process = None
            self.gate.release(self)
