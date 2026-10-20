"""Generator integration uses tiny pytest-owned files; no GIS, DB, or processes."""
import json
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest

from modules.map_export import editor_generator as generator
from modules.map_export import map_package_exporter as exporter
from modules.map_export.editor_model import atomic_json, validate_package
from modules.map_export.editor_session import ExportJob


KML = '''<kml><Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>
125,7 125.01,7 125.01,7.01 125,7.01 125,7
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></kml>'''


def survey(root, identity='AH-026132', *, name=None, raster=True):
    folder = root / (name or identity)
    rgb = folder / 'rgb'
    (rgb / 'boundary').mkdir(parents=True)
    (rgb / 'boundary/site.kml').write_text(KML, encoding='utf-8')
    atomic_json(rgb / 'manifest.json', {'survey_id': identity, 'kml_file': 'boundary/site.kml'})
    if raster:
        ortho = rgb / 'qgis/clipped/ortho'
        ortho.mkdir(parents=True)
        for task in ('t2', 't4'):
            (ortho / f'orthomosaic-clipped--site-xcb-{task}.tif').write_bytes(b'fixture, not a real TIFF')
    return folder


def request(folder, output, **kwargs):
    return dict(operation='generate', inputs=[{'query': str(folder)}], roots=[],
                output_root=str(output), map_name='Test Map', title='Survey map',
                location='Test location', map_type='orthomosaic', **kwargs)


def test_folder_rgb_id_year_and_manifest_identity_lookup(tmp_path):
    root = tmp_path / 'surveys'
    folder = survey(root / '2026')
    assert generator.find_survey_folders(str(folder), []) == [folder]
    assert generator.find_survey_folders(str(folder / 'rgb'), []) == [folder]
    assert generator.find_survey_folders('ah-026132', [root]) == [folder]
    other = survey(root, 'AH-026128', name='published-survey')
    assert generator.find_survey_folders('AH-026128', [root]) == [other]


def test_roots_keep_order_duplicates_removed_and_folder_ambiguity_requires_choice(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    first, second = survey(a), survey(b / '2026')
    assert generator.ordered_roots([b, a, b]) == [b, a]
    assert generator.find_survey_folders('AH-026132', [b, a]) == [second, first]
    rows = [{'query': 'AH-026132'}]
    with pytest.raises(ValueError, match='explicit survey folder'):
        generator.resolve_selection(rows, [b, a], True)
    rows[0]['folder'] = str(first)
    assert generator.resolve_selection(rows, [b, a], True)[0].survey_dir == first


def test_lookup_never_recurses_beyond_immediate_year(tmp_path, monkeypatch):
    root = tmp_path / 'surveys'
    survey(root / '2026/deep')
    monkeypatch.setattr(Path, 'rglob', Mock(side_effect=AssertionError('recursive root scan')))
    with pytest.raises(FileNotFoundError):
        generator.find_survey_folders('AH-026132', [root])


def test_task4_preference_override_and_publication_boundary(tmp_path):
    folder = survey(tmp_path / 'surveys')
    active = folder / 'rgb/boundary/active.kml'
    active.write_text(KML, encoding='utf-8')
    atomic_json(folder / 'rgb/publication.json', {'artifacts': [
        {'published_relative_path': 'boundary/active.kml'},
        {'published_relative_path': 'qgis/clipped/ortho/orthomosaic-clipped--site-xcb-t4.tif'},
    ]})
    candidates = generator.artifact_candidates(folder)
    assert candidates['orthomosaics'][0].endswith('-t4.tif')
    assert str(active) in candidates['boundaries']
    t2 = next(p for p in candidates['orthomosaics'] if p.endswith('-t2.tif'))
    result = generator.resolve_selection([{'query': str(folder), 'boundary': str(active), 'orthomosaic': t2}], [], True)
    assert result[0].orthomosaic_path == Path(t2)
    assert result[0].boundary_path == active


def test_outside_manifest_references_are_not_probed(tmp_path, monkeypatch):
    folder = survey(tmp_path / 'surveys')
    outside = tmp_path / 'outside.kml'
    atomic_json(folder / 'rgb/manifest.json', {'survey_id': folder.name, 'kml_file': str(outside)})
    atomic_json(folder / 'rgb/publication.json', {'published_root': str(tmp_path / 'elsewhere'),
                                                'artifacts': [{'published_relative_path': 'other.tif'}]})
    original = Path.is_file
    def guarded(path):
        assert path != outside and not path.is_relative_to(tmp_path / 'elsewhere')
        return original(path)
    monkeypatch.setattr(Path, 'is_file', guarded)
    result = generator.artifact_candidates(folder)
    assert len(result['warnings']) == 2
    assert result['boundaries'] == [str(folder / 'rgb/boundary/site.kml')]


@pytest.mark.parametrize('filename', ['manifest.json', 'publication.json'])
@pytest.mark.parametrize('content', ['{bad', '[]'])
def test_malformed_manifests_fail_explicitly(tmp_path, filename, content):
    folder = survey(tmp_path / 'surveys')
    (folder / 'rgb' / filename).write_text(content, encoding='utf-8')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        generator.artifact_candidates(folder)


def test_conflicting_manifest_identity_and_duplicate_inputs_rejected(tmp_path):
    folder = survey(tmp_path / 'surveys')
    with pytest.raises(ValueError, match='Duplicate survey'):
        generator.resolve_selection([{'query': str(folder)}, {'query': str(folder / 'rgb')}], [], True)
    atomic_json(folder / 'rgb/manifest.json', {'survey_id': 'AH-026999'})
    with pytest.raises(ValueError, match='conflicting'):
        generator.artifact_candidates(folder)


@pytest.mark.parametrize('count', [1, 2])
def test_reference_packages_single_and_combined_never_copy_tiffs(tmp_path, monkeypatch, count):
    folders = [survey(tmp_path / 'surveys', f'AH-02613{i}') for i in range(count)]
    req = request(folders[0], tmp_path / 'outputs')
    req['inputs'] = [{'query': str(p)} for p in folders]
    original = exporter.shutil.copy2
    copied = []
    def copy_small(source, target):
        assert Path(source).suffix.lower() not in {'.tif', '.tiff'}
        copied.append(Path(source))
        return original(source, target)
    monkeypatch.setattr(exporter.shutil, 'copy2', copy_small)
    monkeypatch.setattr(exporter, '_collect_from_survey_roots', Mock(side_effect=AssertionError('database lookup')))
    result = generator.generate_package(req)
    _, metadata = validate_package(result)
    assert metadata['survey_count'] == count
    assert len(metadata['orthomosaics']) == count
    assert len(json.loads((result / 'boundaries.geojson').read_text())['features']) == count
    assert metadata['orthomosaic_storage'] == 'reference'
    assert all(Path(r['source_orthomosaic']).is_absolute() for r in metadata['orthomosaics'])
    assert all('relative_orthomosaic' not in r for r in metadata['orthomosaics'])
    assert not (result / 'orthomosaics').exists()
    assert len(copied) == 2 * count
    second = generator.generate_package(req)
    assert second != result and (result / 'map_metadata.json').is_file()


def test_legacy_copy_default_is_unchanged(tmp_path, monkeypatch):
    folder = survey(tmp_path / 'surveys')
    resolved = generator.resolve_selection([{'query': str(folder)}], [], True)[0]
    monkeypatch.setattr(exporter, 'collect_boundary_files_by_survey_id', lambda **kw: [
        exporter.BoundaryFile(resolved.survey_name, folder, resolved.boundary_path)])
    monkeypatch.setattr(exporter, 'collect_orthomosaic_files', lambda **kw: [resolved.orthomosaic_path])
    result = exporter.export_map_package(survey_names=[folder.name], source_root=folder,
        survey_root=folder.parent, output_root=tmp_path / 'output', map_name='legacy', include_orthomosaic=True)
    metadata = json.loads(result.metadata_json.read_text())
    record = metadata['orthomosaics'][0]
    assert Path(record['packaged_orthomosaic']).read_bytes() == resolved.orthomosaic_path.read_bytes()
    assert (result.output_dir / record['relative_orthomosaic']).is_file()


def test_boundary_only_works_without_raster_but_orthomosaic_never_downgrades(tmp_path):
    folder = survey(tmp_path / 'surveys', raster=False)
    req = request(folder, tmp_path / 'output')
    with pytest.raises(FileNotFoundError, match='orthomosaic'):
        generator.generate_package(req)
    assert not (tmp_path / 'output').exists()
    req['map_type'] = 'boundary'
    _, metadata = validate_package(generator.generate_package(req))
    assert not metadata['include_orthomosaic'] and metadata['orthomosaics'] == []


@pytest.mark.parametrize('target', ['survey', 'nested', 'parent', 'root', 'repo'])
def test_output_containment_rejects_unsafe_locations(tmp_path, target):
    folder = survey(tmp_path / 'surveys')
    selected = generator.resolve_selection([{'query': str(folder)}], [], True)
    path = {'survey': folder, 'nested': folder / 'maps', 'parent': folder.parent,
            'root': Path(tmp_path.anchor), 'repo': generator.REPO_ROOT}[target]
    with pytest.raises(ValueError):
        generator.validate_output_root(path, selected)


def test_exclusive_output_refuses_existing_package(tmp_path):
    folder = survey(tmp_path / 'surveys')
    resolved = generator.resolve_selection([{'query': str(folder)}], [], True)
    args = dict(survey_names=[folder.name], source_root=folder, output_root=tmp_path / 'output',
                map_name='exclusive', resolved_surveys=resolved, include_orthomosaic=True,
                orthomosaic_storage='reference', exclusive_output=True)
    result = exporter.export_map_package(**args)
    original = result.metadata_json.read_bytes()
    with pytest.raises(FileExistsError):
        exporter.export_map_package(**args)
    assert result.metadata_json.read_bytes() == original


@pytest.mark.parametrize('geometry', ['<kml/>', '<bad', KML.replace('125.01', '125'), KML.replace('125.01', 'nan')])
def test_invalid_geometry_preserves_attempt_failure_evidence(tmp_path, geometry):
    folder = survey(tmp_path / 'surveys')
    (folder / 'rgb/boundary/site.kml').write_text(geometry, encoding='utf-8')
    job = generator.GeneratorJob(lambda: Mock(), generator.HelperGate())
    attempt = job.start(request(folder, tmp_path / 'output'), tmp_path / 'attempts', 'python', 'map_editor.py')
    with pytest.raises(Exception):
        generator.run_generator_request(attempt / 'generator-request.json')
    state = job.finish(1)
    assert state['status'] == 'failed' and state['error']
    assert Path(state['package']).is_dir()
    assert (attempt / 'generator-request.json').is_file()


def test_request_capture_mutual_exclusion_completion_and_cancellation(tmp_path):
    folder = survey(tmp_path / 'surveys')
    gate, process = generator.HelperGate(), Mock()
    job = generator.GeneratorJob(lambda: process, gate)
    req = request(folder, tmp_path / 'output')
    attempt = job.start(req, tmp_path / 'attempts', 'python with spaces', 'map_editor.py')
    req['inputs'][0]['query'] = 'changed after capture'
    saved = generator.read_object(attempt / 'generator-request.json')
    assert saved['inputs'][0]['query'] == str(folder)
    process.setProgram.assert_called_once_with('python with spaces')
    process.setArguments.assert_called_once_with(['map_editor.py', '--generator-request', str(attempt / 'generator-request.json')])
    export = ExportJob(lambda: Mock(), gate)
    with pytest.raises(RuntimeError, match='Another'):
        export.start(Mock(), tmp_path / 'exports', 'python', 'map_editor.py')
    with pytest.raises(RuntimeError):
        job.start(saved, tmp_path / 'attempts', 'python', 'map_editor.py')
    result = generator.run_generator_request(attempt / 'generator-request.json')
    job.cancel()
    process.terminate.assert_called_once()
    assert job.finish(0)['status'] == 'cancelled'
    assert not job.busy and gate.owner is None
    assert Path(result['package']).is_dir()  # late completion never removes data or triggers handoff
    retry = job.start(saved, tmp_path / 'attempts', 'python', 'map_editor.py')
    assert retry != attempt
    generator.run_generator_request(retry / 'generator-request.json')
    assert job.finish(0)['status'] == 'complete'


def test_discovery_helper_and_failed_process_start_release_slot(tmp_path):
    folder = survey(tmp_path / 'surveys')
    gate = generator.HelperGate()
    process = Mock()
    job = generator.GeneratorJob(lambda: process, gate)
    req = request(folder, tmp_path / 'output')
    req['operation'] = 'discover'
    attempt = job.start(req, tmp_path / 'attempts', 'python', 'map_editor.py')
    result = generator.run_generator_request(attempt / 'generator-request.json')
    assert result['surveys'][0]['matches'][0]['folder'] == str(folder)
    assert not (tmp_path / 'output').exists()
    assert job.finish(0)['status'] == 'complete'
    process.start.side_effect = OSError('start failed')
    with pytest.raises(OSError):
        job.start(req, tmp_path / 'attempts', 'python', 'map_editor.py')
    assert gate.owner is None and not job.busy
    assert generator.read_object(job.attempt / 'attempt.json')['status'] == 'failed'


def test_complete_receipt_needs_success_exit_and_valid_tiff_references(tmp_path):
    folder = survey(tmp_path / 'surveys')
    job = generator.GeneratorJob(lambda: Mock(), generator.HelperGate())
    req = request(folder, tmp_path / 'output')
    attempt = job.start(req, tmp_path / 'attempts', 'python', 'map_editor.py')
    result = generator.run_generator_request(attempt / 'generator-request.json')
    assert job.finish(9)['status'] == 'failed'
    attempt = job.start(req, tmp_path / 'attempts', 'python', 'map_editor.py')
    result = generator.run_generator_request(attempt / 'generator-request.json')
    metadata_path = Path(result['package']) / 'map_metadata.json'
    metadata = generator.read_object(metadata_path)
    metadata['orthomosaics'][0]['source_orthomosaic'] = str(tmp_path / 'missing.tif')
    atomic_json(metadata_path, metadata)
    with pytest.raises(FileNotFoundError):
        job.finish(0)
    assert not job.busy and job.gate.owner is None
    assert generator.read_object(attempt / 'attempt.json')['status'] == 'failed'


def test_preferences_read_only_literal_paths_and_entrypoint_before_qgis(tmp_path, monkeypatch):
    prefs = tmp_path / 'roots.json'
    values = ['Z:/explicit-surveys', 'X:/explicit-surveys']
    atomic_json(prefs, {'version': 1, 'roots': values})
    monkeypatch.setattr(generator, 'ordered_roots', Mock(side_effect=AssertionError('startup enumeration')))
    assert generator.load_root_preferences(prefs) == values
    import map_editor
    helper = Mock()
    monkeypatch.setattr(generator, 'run_generator_request', helper)
    assert map_editor.main(['--generator-request', str(tmp_path / 'request.json')]) == 0
    helper.assert_called_once_with(tmp_path / 'request.json')


def test_kmz_publication_boundary_is_parsed_without_external_tools(tmp_path):
    folder = survey(tmp_path / 'surveys')
    kmz = folder / 'rgb/boundary/published.kmz'
    with zipfile.ZipFile(kmz, 'w') as archive:
        archive.writestr('nested/doc.kml', KML)
    atomic_json(folder / 'rgb/publication.json', {'artifacts': [
        {'published_relative_path': 'boundary/published.kmz'}]})
    candidates = generator.artifact_candidates(folder)
    assert candidates['boundaries'][0] == str(kmz)
    _, metadata = validate_package(generator.generate_package(request(folder, tmp_path / 'output')))
    assert metadata['boundaries'][0]['source_boundary'] == str(kmz)
    assert metadata['polygon_count'] == 1


def test_missing_boundary_and_invalid_selection_do_not_create_outputs(tmp_path):
    folder = survey(tmp_path / 'surveys')
    rows = [{'query': str(folder), 'boundary': str(tmp_path / 'outside.kml')}]
    with pytest.raises(FileNotFoundError, match='selected boundary'):
        generator.resolve_selection(rows, [], True)
    empty = tmp_path / 'empty/AH-026999'
    (empty / 'rgb').mkdir(parents=True)
    req = request(empty, tmp_path / 'output')
    with pytest.raises(FileNotFoundError, match='boundary'):
        generator.generate_package(req)
    assert not (tmp_path / 'output').exists()


@pytest.mark.parametrize('case', ['empty', 'missing-root', 'filesystem-root'])
def test_lookup_refuses_empty_missing_and_broad_roots(tmp_path, case):
    with pytest.raises((ValueError, FileNotFoundError)):
        if case == 'empty':
            generator.find_survey_folders('', [])
        else:
            generator.find_survey_folders('AH-026132', [tmp_path / 'missing' if case == 'missing-root' else Path(tmp_path.anchor)])


def test_helper_cannot_replay_completed_request(tmp_path):
    folder = survey(tmp_path / 'surveys')
    job = generator.GeneratorJob(lambda: Mock(), generator.HelperGate())
    req = request(folder, tmp_path / 'outputs')
    attempt = job.start(req, tmp_path / 'attempts', 'python', 'map_editor.py')
    generator.run_generator_request(attempt / 'generator-request.json')
    result_before = (attempt / 'result.json').read_bytes()
    with pytest.raises(ValueError, match='new editor-owned'):
        generator.run_generator_request(attempt / 'generator-request.json')
    assert (attempt / 'result.json').read_bytes() == result_before
    assert job.finish(0)['status'] == 'complete'


def test_process_factory_and_corrupt_receipt_failures_release_slot_and_preserve_evidence(tmp_path):
    folder = survey(tmp_path / 'surveys')
    gate = generator.HelperGate()
    job = generator.GeneratorJob(Mock(side_effect=OSError('factory failed')), gate)
    req = request(folder, tmp_path / 'outputs')
    with pytest.raises(OSError, match='factory failed'):
        job.start(req, tmp_path / 'attempts', 'python', 'map_editor.py')
    assert gate.owner is None and not job.busy
    assert generator.read_object(job.attempt / 'attempt.json')['error'] == 'factory failed'
    job.process_factory = Mock
    attempt = job.start(req, tmp_path / 'attempts', 'python', 'map_editor.py')
    (attempt / 'attempt.json').write_text('corrupt evidence', encoding='utf-8')
    with pytest.raises(ValueError):
        job.finish(0)
    assert gate.owner is None and not job.busy
    assert (attempt / 'attempt.json').read_text() == 'corrupt evidence'
    assert generator.read_object(attempt / 'finish-error.json')['status'] == 'failed'


def test_generator_does_not_start_while_export_owns_gate(tmp_path):
    folder = survey(tmp_path / 'surveys')
    gate = generator.HelperGate()
    export_owner = object()
    gate.acquire(export_owner)
    factory = Mock()
    job = generator.GeneratorJob(factory, gate)
    with pytest.raises(RuntimeError, match='Another'):
        job.start(request(folder, tmp_path / 'output'), tmp_path / 'attempts', 'python', 'map_editor.py')
    factory.assert_not_called()
    assert not (tmp_path / 'attempts').exists() and gate.owner is export_owner


def test_parent_job_does_not_resolve_or_probe_survey_storage(tmp_path, monkeypatch):
    folder = survey(tmp_path / 'surveys')
    original = Path.resolve
    def guarded(path, *args, **kwargs):
        assert path != folder and path != folder.parent, 'source resolution belongs in helper'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', guarded)
    job = generator.GeneratorJob(Mock, generator.HelperGate())
    attempt = job.start(request(folder, tmp_path / 'output'), tmp_path / 'attempts', 'python', 'map_editor.py')
    assert (attempt / 'generator-request.json').is_file()
    job.cancel()
    assert job.finish(1)['status'] == 'cancelled'
