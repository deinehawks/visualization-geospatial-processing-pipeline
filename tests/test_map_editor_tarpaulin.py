"""Large print metadata and mocked Qt/GIS only: no giant image allocations."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

from modules.map_export.editor_model import (
    EditorDesign, PAPER_SIZES_MM, validate_options, design_from_package, save_design,
    load_design, import_bat_settings, apply_bat_settings, create_export_attempt,
)
from modules.map_export.editor_session import run_snapshot_export
from modules.map_export.editor_workflow import DesignHistory, style_preset, validate_preset
from tests.test_map_editor_model import package
from tests.test_map_editor_session import session
from tests.test_map_editor_window import window, fake_session
from tests.test_qgis_print_exporter_config import _load_exporter_module


def test_exact_tarpaulin_landscape_dimensions_and_300_dpi_defaults():
    options = validate_options({'paper_size': ' 4x8ft '})
    assert options['paper_size'] == '4X8FT'
    assert PAPER_SIZES_MM['4X8FT'] == (2438.4, 1219.2)
    assert [round(mm / 25.4 * 300) for mm in PAPER_SIZES_MM['4X8FT']] == [28800, 14400]
    assert all(options[key] == 300 for key in ('preview_dpi', 'pdf_dpi', 'inset_dpi'))


@pytest.mark.parametrize('paper', ['A4', 'A3'])
def test_existing_paper_dpi_defaults_are_unchanged(paper):
    options = validate_options({'paper_size': paper})
    assert (options['preview_dpi'], options['pdf_dpi'], options['inset_dpi']) == (300, 450, 800)


@pytest.mark.parametrize('key', ['preview_dpi', 'pdf_dpi', 'inset_dpi'])
@pytest.mark.parametrize('dpi', [301, 450, 800, 2400])
def test_large_paper_rejects_oversized_dpi_before_rendering(key, dpi):
    with pytest.raises(ValueError, match='at most 300'):
        validate_options({'paper_size': '4X8FT', key: dpi})
    assert validate_options({'paper_size': 'A3', key: dpi})[key] == dpi


def test_lower_draft_dpi_is_preserved_when_reopened(package, tmp_path):
    design = EditorDesign(package, {'paper_size': '4X8FT', 'preview_dpi': 75, 'pdf_dpi': 150}).validate()
    path = tmp_path / 'editor' / 'tarpaulin.mapedit.json'
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    save_design(design, path)
    loaded = load_design(path)
    assert loaded.options == design.options and loaded.source_version == 2
    assert loaded.options['inset_dpi'] == 300
    assert {p.name: p.read_bytes() for p in package.iterdir()} == before


@pytest.mark.parametrize('printed', [
    {'paper_size': '4X8FT'},
    {'render_options': {'paper_size': '4X8FT', 'pdf_dpi': 150}},
])
def test_reopen_package_uses_large_paper_defaults_not_old_a3_dpi(package, printed):
    path = package / 'map_metadata.json'
    data = json.loads(path.read_text())
    data['print_layout'] = printed
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    design = design_from_package(package)
    assert design.options['paper_size'] == '4X8FT'
    assert design.options['preview_dpi'] == design.options['inset_dpi'] == 300
    assert design.options['pdf_dpi'] == printed.get('render_options', {}).get('pdf_dpi', 300)
    assert path.read_bytes() == before


def test_literal_bat_can_select_tarpaulin_without_execution(package, tmp_path):
    bat = tmp_path / 'settings.bat'
    bat.write_text('set MAP_EXPORT_PAPER_SIZE=4X8FT\nset MAP_EXPORT_PREVIEW_DPI=150\n')
    options, _, warnings = import_bat_settings(bat)
    assert not warnings and options['paper_size'] == '4X8FT'
    updated, warnings = apply_bat_settings(design_from_package(package), bat)
    assert not warnings
    assert (updated.options['preview_dpi'], updated.options['pdf_dpi'], updated.options['inset_dpi']) == (150, 300, 300)


def test_paper_preset_is_one_undoable_edit_with_exact_dpi(session):
    before = session.snapshot()
    history = DesignHistory(session)
    history.execute(lambda: session.set_paper('4X8FT'))
    after = session.snapshot()
    assert len(history.undo_steps) == 1 and history.dirty
    assert after.options['paper_size'] == '4X8FT'
    assert (after.options['preview_dpi'], after.options['pdf_dpi'], after.options['inset_dpi']) == (300, 300, 300)
    assert after.maps == before.maps and after.texts == before.texts
    assert after.text_styles == before.text_styles and after.map_styles == before.map_styles
    assert after.grid_style == before.grid_style
    session.renderer._configure_main_map.assert_not_called()
    history.undo()
    assert session.snapshot() == before and not history.dirty
    history.redo()
    assert session.snapshot() == after


def test_large_page_uses_light_preview_without_lowering_saved_output_dpi(session):
    context = Mock()
    original = session.renderer.build_qgis_print_layout.side_effect
    def build(**kwargs):
        result = original(**kwargs)
        result.layout.renderContext = lambda: context
        return result
    session.renderer.build_qgis_print_layout.side_effect = build
    session.set_paper('4X8FT')
    context.setDpi.assert_called_with(25)
    assert session.snapshot().options['preview_dpi'] == 300
    session.set_paper('A4')
    context.setDpi.assert_called_with(96)


def test_reselecting_tarpaulin_does_not_reset_manual_draft_dpi(session):
    session.set_paper('4X8FT')
    session.design.options['pdf_dpi'] = 150
    session.set_paper('4X8FT')
    assert session.snapshot().options['pdf_dpi'] == 150


def test_style_preset_round_trips_large_paper_without_geography(package):
    design = EditorDesign(package, {'paper_size': '4X8FT'}, texts={'map_title': 'Private wording'}).validate()
    preset = validate_preset(style_preset('Tarpaulin', design))
    assert preset['options']['paper_size'] == '4X8FT' and preset['options']['pdf_dpi'] == 300
    assert 'maps' not in preset and 'texts' not in preset and 'package' not in preset


def test_page_controls_offer_preset_and_limit_only_its_dpi(window, tmp_path):
    window.paper.addItems.assert_called_once_with(['A3', 'A4', '4X8FT'])
    window.session = fake_session()
    window.session.design = EditorDesign(tmp_path, {'paper_size': '4X8FT'}).validate()
    window.refresh_controls()
    for control in window.dpi.values():
        control.setMaximum.assert_called_with(300)
        control.setValue.assert_called_with(300)
    assert '28,800 x 14,400' in window.paper_hint.setText.call_args.args[0]
    window.session.design.options['paper_size'] = 'A3'
    window.refresh_controls()
    for control in window.dpi.values():
        control.setMaximum.assert_called_with(2400)
    window.paper_hint.setText.assert_called_with('')


@pytest.mark.parametrize('key,env,default', [
    ('preview_dpi', 'MAP_EXPORT_PREVIEW_DPI', 120),
    ('pdf_dpi', 'MAP_EXPORT_PDF_DPI', 300),
    ('inset_dpi', 'MAP_EXPORT_INSET_DPI', 800),
])
def test_shared_renderer_uses_safe_large_defaults_and_rejects_oversized_env(monkeypatch, key, env, default):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.delenv(env, raising=False)
    ctx = SimpleNamespace(paper_size='4X8FT', render_options=None)
    assert exporter._output_dpi(ctx, key, env, default) == 300
    monkeypatch.setenv(env, '800')
    with pytest.raises(ValueError, match='1 to 300'):
        exporter._output_dpi(ctx, key, env, default)
    ctx.render_options = validate_options({'paper_size': '4X8FT'})
    assert exporter._output_dpi(ctx, key, env, default) == 300


def test_both_inset_renders_receive_300_dpi_and_saved_extents(monkeypatch, tmp_path):
    exporter = _load_exporter_module(monkeypatch)
    configure = Mock()
    monkeypatch.setattr(exporter, '_configure_inset_map', configure)
    monkeypatch.setattr(exporter, '_inset_basemap_with_native_dpi', lambda *args: None)
    states = {'map_inset': object(), 'map_inset_2': object()}
    monkeypatch.setattr(exporter, '_saved_map_extent', lambda ctx, key, **kw: states[key])
    ctx = SimpleNamespace(qgis=SimpleNamespace(QgsLayoutItemPicture=object, QgsCoordinateReferenceSystem=lambda x: x),
        paper_size='4X8FT', render_options=validate_options({'paper_size': '4X8FT'}),
        layout=object(), project=object(), package_dir=tmp_path, target_crs='EPSG:32651',
        merged_layer=object(), boundary_inset_bounds_wgs84={}, overview_bounds_wgs84={})
    exporter._configure_insets(ctx)
    assert configure.call_count == 2
    for call in configure.call_args_list:
        assert call.kwargs['inset_dpi'] == 300
        assert call.kwargs['extent_override'] is states[call.kwargs['map_item_id']]


@pytest.mark.parametrize('fail', [False, True])
def test_export_helper_receives_exact_large_snapshot_and_preserves_originals(package, tmp_path, fail):
    design = EditorDesign(package, {'paper_size': '4X8FT'},
        maps={'map_frame': {'crs': 'EPSG:32651', 'extent': [1, 2, 101, 62]}},
        texts={'map_title': 'Large map'}).validate()
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    old = tmp_path / 'workspace' / 'earlier'
    old.mkdir(parents=True)
    (old / 'print_map.pdf').write_bytes(b'keep previous pdf')
    attempt = create_export_attempt(design, tmp_path / 'workspace')
    renderer = Mock()
    if fail:
        renderer._export_print_outputs.side_effect = RuntimeError('controlled export failure')
        with pytest.raises(RuntimeError, match='controlled'):
            run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    else:
        renderer._export_print_outputs.return_value = {'pdf': str(attempt / 'print_map.pdf')}
        run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    args = renderer.build_qgis_print_layout.call_args.kwargs
    assert args['render_options'] == design.options
    assert args['map_states'] == design.maps and args['text_values'] == design.texts
    assert args['output_dir'] == attempt and not args['live_insets']
    assert (old / 'print_map.pdf').read_bytes() == b'keep previous pdf'
    assert {p.name: p.read_bytes() for p in package.iterdir()} == before
    assert json.loads((attempt / 'attempt.json').read_text())['status'] == ('failed' if fail else 'complete')


def test_export_rejects_oversized_resolution_before_touching_output_paths(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    output = MagicMock()
    ctx = SimpleNamespace(qgis=Mock(), output_dir=output, package_dir=output,
        paper_size='4X8FT', render_options={'preview_dpi': 300, 'pdf_dpi': 800})
    with pytest.raises(ValueError, match='1 to 300'):
        exporter._export_print_outputs(ctx)
    ctx.qgis.QgsLayoutExporter.assert_not_called()
    output.__truediv__.return_value.unlink.assert_not_called()


def test_final_png_pdf_settings_and_metadata_use_full_300_dpi_page(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    events, written, settings = [], [], {}
    class MemoryPath:
        def __init__(self, name):
            self.name = name
        def __truediv__(self, name):
            return MemoryPath(self.name + '/' + name)
        def __str__(self):
            return self.name
        def unlink(self, **kwargs):
            events.append(('unlink', self.name))
        def replace(self, target):
            events.append(('replace', str(target)))
        def write_text(self, value, **kwargs):
            written.append(json.loads(value))
    class LayoutExporter:
        Success = 0
        ImageExportSettings = SimpleNamespace
        PdfExportSettings = SimpleNamespace
        def __init__(self, layout):
            pass
        def exportToImage(self, path, value):
            settings['png'] = value
            return 0
        def exportToPdf(self, path, value):
            settings['pdf'] = value
            return 0
    monkeypatch.setattr(exporter, '_run_layout_export', lambda **kw: kw['export_callable'](str(kw['output_path'])))
    ctx = SimpleNamespace(qgis=SimpleNamespace(QgsLayoutExporter=LayoutExporter),
        output_dir=MemoryPath('owned-attempt'), package_dir=MemoryPath('read-only-package'),
        metadata_path=MemoryPath('owned-attempt/map_metadata.json'), metadata={},
        layout=SimpleNamespace(itemById=lambda _: SimpleNamespace(text=lambda: 'Tarpaulin')),
        paper_size='4X8FT', render_options=validate_options({'paper_size': '4X8FT'}),
        target_epsg=32651, effective_map_scale=2500, scale_mode='manual', fitted_map_scale=961,
        auto_scale_multiplier=None, selected_map_scale=2500, extent_source='orthomosaic',
        grid_interval_degrees=0.001, dpi=400, logo_path=None)
    result = exporter._export_print_outputs(ctx)
    assert settings['png'].dpi == settings['pdf'].dpi == 300
    assert settings['pdf'].rasterizeWholeImage is True
    layout = written[0]['print_layout']
    assert layout['page_size_mm'] == {'width': 2438.4, 'height': 1219.2}
    assert layout['dpi'] == 300
    assert layout['render_options'] == ctx.render_options
    assert result['pdf'] == 'owned-attempt/print_map.pdf'
    assert all('read-only-package' not in name for _, name in events)
