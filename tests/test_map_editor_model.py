from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path

import pytest

from modules.map_export.editor_model import (
    DEFAULT_OPTIONS, EditorDesign, apply_bat_settings, atomic_json,
    create_export_attempt, design_from_package, import_bat_settings,
    load_design, parse_bounds, save_design, validate_options,
)
from modules.map_export.editor_session import ExportJob, run_snapshot_export
from unittest.mock import Mock


@pytest.fixture
def atomic_workspace(tmp_path, monkeypatch):
    """Guard failed-write cleanup so only owned temporary files may be removed."""
    root = (tmp_path / 'atomic-owned').resolve()
    root.mkdir()
    sentinel = root / '.test-owner'
    sentinel.write_text('atomic JSON test ownership', encoding='utf-8')
    original_unlink = Path.unlink

    def owned_unlink(path, *args, **kwargs):
        target = path.resolve()
        assert sentinel.read_text(encoding='utf-8') == 'atomic JSON test ownership'
        assert target != root and target != Path(target.anchor)
        assert target.is_relative_to(root) and target != sentinel
        assert target.suffix == '.tmp'
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', owned_unlink)
    return root


def padded_directory(root, length):
    """Use real pytest-owned paths while matching the reported path length."""
    padding = length - len(str(root)) - 1
    assert 1 <= padding <= 200
    directory = root / ('p' * padding)
    directory.mkdir()
    assert len(str(directory)) == length
    return directory


def legacy_path_limit(monkeypatch):
    """Emulate the affected Windows runtime without relying on host policy."""
    writes = []
    original_write = Path.write_text

    def limited_write(path, *args, **kwargs):
        if len(str(path)) >= 260:
            raise FileNotFoundError(2, 'Legacy Windows path limit', str(path))
        writes.append(path)
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', limited_write)
    return writes


def test_atomic_json_short_temporary_name_fits_reported_snapshot_path(atomic_workspace, monkeypatch):
    folder = padded_directory(atomic_workspace, 210)
    destination = folder / 'snapshot.mapedit.json'
    assert len(str(destination)) == 232
    assert len(str(destination)) + 1 + 32 + 4 == 269  # Previous temporary path.
    writes = legacy_path_limit(monkeypatch)
    atomic_json(destination, {'title': 'Saved safely'})
    assert json.loads(destination.read_text()) == {'title': 'Saved safely'}
    assert len(writes) == 1 and writes[0].parent == folder
    assert len(str(writes[0])) == 247
    assert len(writes[0].stem) == 32 and writes[0].suffix == '.tmp'
    assert not list(folder.glob('*.tmp'))
    assert (atomic_workspace / '.test-owner').is_file()


def test_export_job_saves_snapshot_under_legacy_path_limit(package, atomic_workspace, monkeypatch):
    workspace = padded_directory(atomic_workspace, 161)
    writes = legacy_path_limit(monkeypatch)
    process = Mock()
    job = ExportJob(lambda: process)
    design = design_from_package(package)
    attempt = job.start(design, workspace, 'python', 'editor.py')
    assert len(str(attempt)) == 210
    assert load_design(attempt / 'snapshot.mapedit.json').texts == design.texts
    assert json.loads((attempt / 'attempt.json').read_text())['status'] == 'incomplete'
    assert all(path.parent == attempt and len(str(path)) < 260 for path in writes)
    process.start.assert_called_once()
    assert (atomic_workspace / '.test-owner').is_file()


@pytest.mark.parametrize('failure', ['write', 'replace', 'serialize'])
def test_atomic_json_failure_preserves_destination_and_cleans_only_owned_temp(atomic_workspace, monkeypatch, failure):
    destination = atomic_workspace / 'existing.json'
    destination.write_text('{"original": true}', encoding='utf-8')
    original_write = Path.write_text
    original_replace = Path.replace

    def failed_write(path, *args, **kwargs):
        if path.suffix == '.tmp':
            original_write(path, 'partial temporary content', encoding='utf-8')
            raise OSError('temporary write failed')
        return original_write(path, *args, **kwargs)

    def failed_replace(path, *args, **kwargs):
        if path.suffix == '.tmp':
            raise OSError('atomic replace failed')
        return original_replace(path, *args, **kwargs)

    if failure == 'write':
        monkeypatch.setattr(Path, 'write_text', failed_write)
    elif failure == 'replace':
        monkeypatch.setattr(Path, 'replace', failed_replace)
    value = {'new': float('nan') if failure == 'serialize' else True}
    with pytest.raises(ValueError if failure == 'serialize' else OSError):
        atomic_json(destination, value)
    assert destination.read_text(encoding='utf-8') == '{"original": true}'
    assert not list(atomic_workspace.glob('*.tmp'))
    assert (atomic_workspace / '.test-owner').read_text(encoding='utf-8') == 'atomic JSON test ownership'


def test_atomic_json_uses_unique_same_directory_temporary_files(atomic_workspace, monkeypatch):
    writes = legacy_path_limit(monkeypatch)
    destination = atomic_workspace / 'settings.json'
    atomic_json(destination, {'version': 1})
    atomic_json(destination, {'version': 2})
    assert len(writes) == 2 and writes[0] != writes[1]
    assert all(path.parent == destination.parent and len(path.name) == 36 for path in writes)
    assert json.loads(destination.read_text()) == {'version': 2}
    assert not list(atomic_workspace.glob('*.tmp'))


@pytest.fixture
def package(tmp_path):
    root = tmp_path / 'package'
    root.mkdir()
    (root / 'boundaries.geojson').write_text('{}')
    (root / 'merged_boundary.geojson').write_text('{}')
    (root / 'first.tif').write_bytes(b'placeholder')
    (root / 'second.tif').write_bytes(b'placeholder')
    (root / 'map_metadata.json').write_text(json.dumps({
        'layout': {'title': 'Original', 'location': 'Davao', 'map_scale': 2500},
        'include_orthomosaic': True,
        'orthomosaics': [{'relative_orthomosaic': 'first.tif'},
                         {'relative_orthomosaic': 'second.tif'}],
    }))
    return root


def test_package_open_is_read_only_and_defaults_are_isolated(package):
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    design = design_from_package(package)
    assert design.options['paper_size'] == 'A3'
    assert design.options['pdf_dpi'] == 450
    assert design.options['boundary_overlay'] == 'pin'
    assert design.options['manual_scale'] == 2500
    assert design.texts['label_inset'] == 'Davao'
    design.options['overview_bounds']['min_lon'] = 120
    assert DEFAULT_OPTIONS['overview_bounds']['min_lon'] == 125.3063
    assert before == {p.name: p.read_bytes() for p in package.iterdir()}


def test_future_metadata_seeds_render_options_and_design(package):
    path = package / 'map_metadata.json'
    metadata = json.loads(path.read_text())
    metadata['print_layout'] = {
        'paper_size': 'A4', 'render_options': {'overview_overlay': 'pin', 'overview_label': 'Philippines'},
        'editor_state': {'texts': {'map_title': 'Edited title'}},
    }
    path.write_text(json.dumps(metadata))
    design = design_from_package(package)
    assert design.options['paper_size'] == 'A4'
    assert design.options['overview_overlay'] == 'pin'
    assert design.texts['label_inset_2'] == 'Philippines'
    assert design.texts['map_title'] == 'Edited title'


def test_missing_packaged_raster_is_reported(package):
    data = json.loads((package / 'map_metadata.json').read_text())
    data['orthomosaics'][1]['relative_orthomosaic'] = 'missing.tif'
    (package / 'map_metadata.json').write_text(json.dumps(data))
    with pytest.raises(FileNotFoundError, match='missing.tif'):
        design_from_package(package)


def test_bat_import_literals_only_no_execution_or_expansion(tmp_path):
    bat = tmp_path / 'settings.bat'
    bat.write_text('''@echo off
set "MAP_EXPORT_PAPER_SIZE=A4"
set MAP_EXPORT_PDF_DPI=600
set MAP_EXPORT_OVERVIEW_LABEL=Philippines
set MAP_EXPORT_OVERVIEW_BOUNDS=116.8,4.4,127,21.3
set MAP_EXPORT_OVERVIEW_OVERLAY=pin
set MAP_EXPORT_BOUNDARY_INSET_OVERLAY=pin
set MAP_EXPORT_OVERVIEW_CRS=EPSG:4326
set TITLE=A safe title
set LOCATION=%SECRET%
set MAP_EXPORT_MAIN_BUFFER=0.2 & erase anything
set MAP_EXPORT_INSET_DPI=not-a-number
python main.py --both-tasks
set WEBODM_PASSWORD=do-not-read
''')
    options, texts, warnings = import_bat_settings(bat)
    assert options['paper_size'] == 'A4'
    assert options['pdf_dpi'] == 600
    assert options['overview_overlay'] == 'pin'
    assert options['boundary_overlay'] == 'pin'
    assert options['overview_crs'] == 'EPSG:4326'
    assert options['overview_bounds']['min_lon'] == 116.8
    assert texts == {'map_title': 'A safe title'}
    assert 'main_buffer' not in options
    assert 'inset_dpi' not in options
    assert len(warnings) == 3
    assert 'do-not-read' not in str((options, texts, warnings))


@pytest.mark.parametrize('value', ['1,2,3', '3,2,1,4', '1,91,3,92', 'nan,2,3,4', '1,2,inf,4'])
def test_invalid_bounds_rejected(value):
    with pytest.raises(ValueError):
        parse_bounds(value)


def test_bat_pair_invalidates_label_and_valid_coordinate_together(tmp_path):
    path = tmp_path / 'bad.bat'
    path.write_text('set MAP_EXPORT_OVERVIEW_LABEL=Wrong place\nset MAP_EXPORT_OVERVIEW_BOUNDS=bad\n')
    options, _, warnings = import_bat_settings(path)
    assert 'overview_label' not in options
    assert 'overview_bounds' not in options
    assert warnings


def test_bat_override_precedence_and_saved_design_round_trip(package, tmp_path):
    bat = tmp_path / 'settings.bat'
    bat.write_text('set MAP_EXPORT_PAPER_SIZE=A4\nset TITLE=From BAT\n')
    original = design_from_package(package)
    original.maps = {'map_frame': {'crs': 'EPSG:32651', 'extent': [1, 2, 101, 62]}}
    imported, warnings = apply_bat_settings(original, bat)
    assert not warnings and not imported.maps
    assert original.maps and original.texts['map_title'] == 'Original'
    imported.maps = copy.deepcopy(original.maps)
    imported.text_styles = {'map_title': {'family': 'Arial', 'size': 18.5, 'bold': True, 'color': '#123abc'}}
    path = tmp_path / 'workspace' / 'design.mapedit.json'
    save_design(imported, path)
    payload = json.loads(path.read_text())
    assert not Path(payload['package']).is_absolute()
    loaded = load_design(path)
    assert loaded == imported
    assert loaded.texts['map_title'] == 'From BAT'
    assert loaded.options['paper_size'] == 'A4'
    assert loaded.text_styles['map_title']['size'] == 18.5


def test_source_package_cannot_receive_design_or_export(package):
    design = design_from_package(package)
    with pytest.raises(ValueError, match='separate editor workspace'):
        save_design(design, package / 'design.mapedit.json')
    with pytest.raises(ValueError):
        create_export_attempt(design, package / 'outputs')
    assert not (package / 'design.mapedit.json').exists()


def test_unknown_version_rejected(package, tmp_path):
    path = tmp_path / 'design.json'
    path.write_text(json.dumps({'version': 5}))
    with pytest.raises(ValueError, match='version'):
        load_design(path)


@pytest.mark.parametrize('setting,value', [('paper_size', 'A2'), ('pdf_dpi', 0),
    ('preview_dpi', 2.5), ('overview_zoom', 0.5), ('main_buffer', float('nan')),
    ('boundary_overlay', 'bad'), ('manual_scale', -1)])
def test_options_validate(setting, value):
    with pytest.raises(ValueError):
        validate_options({setting: value})


def test_two_exports_have_isolated_immutable_snapshots(package, tmp_path):
    design = design_from_package(package)
    first = create_export_attempt(design, tmp_path / 'work')
    design.texts['map_title'] = 'Changed afterwards'
    second = create_export_attempt(design, tmp_path / 'work')
    assert first != second
    assert load_design(first / 'snapshot.mapedit.json').texts['map_title'] == 'Original'
    assert load_design(second / 'snapshot.mapedit.json').texts['map_title'] == 'Changed afterwards'
    with pytest.raises(ValueError, match='immutable'):
        save_design(design, first / 'snapshot.mapedit.json')


def test_owned_export_process_uses_argument_list_and_blocks_duplicate(package, tmp_path):
    process = Mock()
    job = ExportJob(lambda: process)
    attempt = job.start(design_from_package(package), tmp_path / 'work', 'qgis-python', 'editor.py')
    process.setProgram.assert_called_once_with('qgis-python')
    process.setArguments.assert_called_once_with(['editor.py', '--export-snapshot', str(attempt / 'snapshot.mapedit.json')])
    with pytest.raises(RuntimeError, match='already running'):
        job.start(design_from_package(package), tmp_path / 'work', 'qgis-python', 'editor.py')
    job.cancel()
    process.terminate.assert_called_once_with()
    state = job.finish(-1)
    assert state['status'] == 'cancelled' and not job.busy


@pytest.mark.parametrize('failed', [False, True])
def test_export_snapshot_keeps_source_and_previous_outputs(package, tmp_path, failed):
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    design = design_from_package(package)
    design.options['grid_font_size'] = 8.5
    design.map_styles['map_inset']['basemap_opacity'] = 0.2
    design.map_styles['map_inset_2']['pin_color'] = '#112233'
    design.grid_style['lines_visible'] = False
    design.maps = {
        'map_frame': {'crs': 'EPSG:32651', 'extent': [1, 2, 101, 62]},
        'map_inset_2': {'crs': 'EPSG:4326', 'extent': [116, 4, 128, 22]},
    }
    old = tmp_path / 'work' / 'previous'
    old.mkdir(parents=True)
    (old / 'print_map.pdf').write_bytes(b'previous pdf')
    attempt = create_export_attempt(design, tmp_path / 'work')
    renderer = Mock()
    ctx = renderer.build_qgis_print_layout.return_value
    def export(context):
        assert context.metadata_path == attempt / 'map_metadata.json'
        assert context.editor_state['maps'] == design.maps
        if failed:
            raise RuntimeError('controlled failure')
        return {'pdf': str(attempt / 'print_map.pdf')}
    renderer._export_print_outputs.side_effect = export
    if failed:
        with pytest.raises(RuntimeError, match='controlled failure'):
            run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    else:
        run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    args = renderer.build_qgis_print_layout.call_args.kwargs
    assert args['package_dir'] == package
    assert args['output_dir'] == attempt
    assert args['map_states'] == design.maps
    assert args['render_options']['grid_font_size'] == 8.5
    assert args['render_options']['boundary_overlay'] == 'pin'
    assert args['render_options']['overview_crs'] == 'EPSG:4326'
    assert args['map_styles'] == design.map_styles
    assert args['grid_style'] == design.grid_style
    assert ctx.editor_state['map_styles'] == design.map_styles
    assert ctx.editor_state['grid_style'] == design.grid_style
    assert not args['live_insets']
    assert json.loads((attempt / 'attempt.json').read_text())['status'] == ('failed' if failed else 'complete')
    assert (old / 'print_map.pdf').read_bytes() == b'previous pdf'
    assert before == {p.name: p.read_bytes() for p in package.iterdir()}
    with pytest.raises(ValueError):
        run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)


def test_worker_refuses_existing_output_attempt(package, tmp_path):
    attempt = create_export_attempt(design_from_package(package), tmp_path / 'work')
    (attempt / 'preview.png').write_bytes(b'preserve')
    with pytest.raises(FileExistsError):
        run_snapshot_export(attempt / 'snapshot.mapedit.json', Mock())
    assert (attempt / 'preview.png').read_bytes() == b'preserve'


def test_editor_modules_import_without_qt_qgis_or_effects(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'qgis', None)
    monkeypatch.setitem(sys.modules, 'osgeo', None)
    for name in ('modules.map_export.editor_model', 'modules.map_export.editor_session',
                 'modules.map_export.editor_window', 'modules.map_export.editor_workflow', 'map_editor'):
        module = importlib.import_module(name)
        importlib.reload(module)


@pytest.mark.parametrize('value', (0, -1, 73, float('inf'), float('nan'), True))
def test_coordinate_font_size_validates(value):
    with pytest.raises(ValueError):
        validate_options({'grid_font_size': value})


def test_coordinate_font_size_round_trip_and_legacy_design_compatibility(package, tmp_path):
    design = design_from_package(package)
    design.options['grid_font_size'] = 8.5
    path = tmp_path / 'design.mapedit.json'
    save_design(design, path)
    assert load_design(path).options['grid_font_size'] == 8.5
    payload = json.loads(path.read_text())
    payload['options'].pop('grid_font_size')
    path.write_text(json.dumps(payload))
    assert load_design(path).options['grid_font_size'] == 6.0


@pytest.mark.parametrize(('platform', 'expected'), [('win32', 'windows'), ('linux', 'offscreen'), ('darwin', 'offscreen')])
def test_windows_export_uses_native_font_backend(platform, expected):
    from modules.map_export.editor_session import export_qt_platform
    assert export_qt_platform(platform) == expected


@pytest.mark.parametrize('value', ('EPSG:3857', 'bad', '', None))
def test_overview_crs_rejects_unsupported_values(value):
    with pytest.raises(ValueError, match='CRS'):
        validate_options({'overview_crs': value})


def test_explicit_upper_boundary_choice_survives_save_and_metadata(package, tmp_path):
    metadata_path = package / 'map_metadata.json'
    metadata = json.loads(metadata_path.read_text())
    metadata['print_layout'] = {'render_options': {'boundary_overlay': 'boundary'}}
    metadata_path.write_text(json.dumps(metadata))
    design = design_from_package(package)
    assert design.options['boundary_overlay'] == 'boundary'
    path = tmp_path / 'boundary.mapedit.json'
    save_design(design, path)
    assert load_design(path).options['boundary_overlay'] == 'boundary'


def test_mixed_crs_design_round_trip_and_older_design_default(package, tmp_path):
    design = design_from_package(package)
    design.maps = {
        'map_frame': {'crs': 'EPSG:32651', 'extent': [1, 2, 101, 62]},
        'map_inset_2': {'crs': 'EPSG:4326', 'extent': [116, 4, 128, 22]},
    }
    path = tmp_path / 'mixed.mapedit.json'
    save_design(design, path)
    assert load_design(path) == design
    payload = json.loads(path.read_text())
    payload['options'].pop('overview_crs')
    path.write_text(json.dumps(payload))
    assert load_design(path).options['overview_crs'] == 'EPSG:4326'
    assert load_design(path).maps == design.maps
    assert validate_options({'overview_crs': 'PROJECT'})['overview_crs'] == 'project'
