from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.map_export.editor_model import EditorDesign, MAP_IDS, TEXT_IDS
from modules.map_export.editor_session import EditorSession


class Rect:
    def __init__(self, x1, y1, x2, y2):
        self.coordinates = [x1, y1, x2, y2]

    def xMinimum(self): return self.coordinates[0]
    def yMinimum(self): return self.coordinates[1]
    def xMaximum(self): return self.coordinates[2]
    def yMaximum(self): return self.coordinates[3]
    def width(self): return self.xMaximum() - self.xMinimum()
    def height(self): return self.yMaximum() - self.yMinimum()


class Map:
    def __init__(self):
        self.rectangle = Rect(0, 0, 100, 60)
        self.auth_id = 'EPSG:32651'
        self.active_layers = [Mock()]

    def crs(self): return SimpleNamespace(authid=lambda: self.auth_id)
    def setCrs(self, crs): self.auth_id = crs.authid()

    def extent(self): return self.rectangle
    def rect(self): return Rect(0, 0, 100, 60)
    def zoomToExtent(self, value): self.rectangle = value
    def setExtent(self, value): self.rectangle = value
    def scale(self): return self.rectangle.width() * 10
    def setScale(self, value):
        factor = value / self.scale()
        cx = (self.rectangle.xMinimum() + self.rectangle.xMaximum()) / 2
        cy = (self.rectangle.yMinimum() + self.rectangle.yMaximum()) / 2
        w, h = self.rectangle.width() * factor / 2, self.rectangle.height() * factor / 2
        self.rectangle = Rect(cx - w, cy - h, cx + w, cy + h)
    def refresh(self): pass
    def layers(self): return self.active_layers
    def setLayers(self, layers): self.active_layers = layers
    def setBackgroundEnabled(self, value): pass


class Label:
    def __init__(self):
        self.value = 'Label'
    def text(self): return self.value
    def font(self):
        return SimpleNamespace(family=lambda: 'Arial', pointSizeF=lambda: 12.0, bold=lambda: False)
    def fontColor(self): return SimpleNamespace(name=lambda: '#000000')
    def setText(self, value): self.value = value
    def refresh(self): pass


@pytest.fixture
def session(tmp_path):
    renderer = Mock()
    renderer.builds = []
    def build(**kwargs):
        items = {key: Map() for key in MAP_IDS}
        items.update({key: Label() for key in TEXT_IDS})
        items['map_info'] = Label()
        for key, state in kwargs['map_states'].items():
            items[key].setExtent(Rect(*state['extent']))
        overview_crs = kwargs['render_options']['overview_crs']
        items['map_inset_2'].auth_id = 'EPSG:32651' if overview_crs == 'project' else overview_crs
        for key, text in kwargs['text_values'].items():
            items[key].setText(text)
        ctx = SimpleNamespace(
            layout=SimpleNamespace(itemById=items.get, renderContext=lambda: Mock()),
            target_crs=SimpleNamespace(authid=lambda: 'EPSG:32651'),
            qgis=SimpleNamespace(QgsRectangle=Rect, QgsCoordinateReferenceSystem=lambda value: value),
            render_options=copy.deepcopy(kwargs['render_options']), project=Mock(),
            basemap_layer=Mock(), merged_layer=Mock(), map_states=kwargs['map_states'],
        )
        renderer.builds.append(ctx)
        return ctx
    renderer.build_qgis_print_layout.side_effect = build
    renderer.apply_editor_text.side_effect = lambda ctx, texts, styles: [ctx.layout.itemById(k).setText(v) for k, v in texts.items()]
    renderer._prepare_fixed_extent.return_value = Rect(5, 6, 105, 66)
    return EditorSession(EditorDesign(tmp_path), renderer)


def test_pan_zoom_and_scale_affect_only_selected_map(session):
    session.pan('map_inset', 0.1, 0.2)
    assert session.snapshot().maps['map_inset']['extent'] == [-10, 12, 90, 72]
    assert session.snapshot().maps['map_frame']['extent'] == [0, 0, 100, 60]
    session.zoom('map_inset', 2)
    assert session._map('map_inset').extent().width() == 200
    session.set_scale('map_inset_2', 2500)
    assert session._map('map_inset_2').scale() == 2500
    session.renderer.refresh_main_map_furniture.assert_not_called()


def test_main_navigation_refreshes_furniture_without_auto_fit(session):
    session.zoom('map_frame', 0.8)
    session.pan('map_frame', 0.1, 0)
    assert session.renderer.refresh_main_map_furniture.call_count == 2
    session.renderer._configure_main_map.assert_not_called()
    assert session._map('map_frame').scale() == 800
    session.auto_fit()
    session.renderer._configure_main_map.assert_called_once_with(session.ctx)
    assert session.ctx.map_states == {}


def test_bounds_and_reset_preserve_other_maps(session):
    session.set_bounds('map_inset', '125.4,7.0,125.6,7.2')
    assert session._map('map_inset').extent().coordinates == [5, 6, 105, 66]
    session.reset_map('map_inset')
    assert session.snapshot().maps['map_inset']['extent'] == [0, 0, 100, 60]
    with pytest.raises(ValueError):
        session.set_bounds('map_frame', '')


def test_text_style_and_paper_changes_are_explicit_and_keep_framing(session):
    style = {'family': 'Arial', 'size': 20, 'bold': True, 'color': '#ff0000'}
    session.set_text('map_title', 'New title', style)
    assert session.ctx.layout.itemById('map_title').text() == 'New title'
    session.renderer.apply_editor_text.assert_called_once_with(session.ctx, {'map_title': 'New title'}, {'map_title': style})
    session.zoom('map_inset', 1.5)
    old_maps = session.snapshot().maps
    session.set_paper('A4')
    assert session.design.options['paper_size'] == 'A4'
    assert session.snapshot().maps == old_maps
    assert session.ctx.layout.itemById('map_title').text() == 'New title'
    assert len(session.renderer.builds) == 2


def test_overlay_changes_only_inset_and_removes_old_memory_layer(session):
    old = session._map('map_inset').layers()[0]
    pin = session.renderer._configure_inset_boundary_overlay.return_value
    session.set_overlay('map_inset', 'pin')
    assert session.design.options['boundary_overlay'] == 'pin'
    assert session.design.options['overview_overlay'] == 'boundary'
    assert session._map('map_inset').layers() == [pin, session.ctx.basemap_layer]
    session.ctx.project.removeMapLayer.assert_called_once_with(old.id())
    with pytest.raises(ValueError):
        session.set_overlay('map_frame', 'pin')


def load_renderer(monkeypatch):
    fake_osgeo = ModuleType('osgeo')
    fake_osgeo.gdal = SimpleNamespace(PushErrorHandler=lambda *_: None, CPLDefaultErrorHandler=lambda *_: None)
    monkeypatch.setitem(sys.modules, 'osgeo', fake_osgeo)
    path = Path(__file__).resolve().parents[1] / 'modules/map_export/qgis_print_exporter.py'
    spec = importlib.util.spec_from_file_location('modules.map_export.editor_renderer_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('live', (True, False))
def test_inset_uses_same_crs_and_saved_extent_for_basemap_and_overlay(monkeypatch, tmp_path, live):
    renderer = load_renderer(monkeypatch)
    core = ModuleType('qgis.core')
    core.QgsLayoutItemMap = Map
    monkeypatch.setitem(sys.modules, 'qgis.core', core)
    item = Map()
    before = set(tmp_path.iterdir())
    monkeypatch.setattr(renderer, '_prepare_fixed_extent', Mock(return_value=Rect(0, 0, 100, 60)))
    overlay = Mock()
    monkeypatch.setattr(renderer, '_configure_inset_boundary_overlay', Mock(return_value=overlay))
    render_image, add_picture = Mock(), Mock()
    monkeypatch.setattr(renderer, '_render_inset_basemap_image', render_image)
    monkeypatch.setattr(renderer, '_add_inset_basemap_picture', add_picture)
    saved = Rect(10, 10, 110, 70)
    basemap = Mock()
    target_crs = SimpleNamespace(authid=lambda: 'EPSG:4326')
    renderer._configure_inset_map(
        layout=SimpleNamespace(itemById=lambda _: item, refresh=Mock()), project=Mock(), package_dir=tmp_path,
        basemap_layer=basemap, boundary_layer=Mock(), target_crs=target_crs,
        QgsLayoutItemPicture=Mock(), map_item_id='map_inset_2', live=live,
        fixed_extent_wgs84={}, extent_override=saved, zoom_out_factor=2,
    )
    assert renderer._configure_inset_boundary_overlay.call_args.kwargs['extent'] is saved
    assert renderer._configure_inset_boundary_overlay.call_args.kwargs['target_crs'] is target_crs
    if live:
        assert item.layers() == [overlay, basemap]
        render_image.assert_not_called()
        add_picture.assert_not_called()
    else:
        assert render_image.call_args.kwargs['target_crs'] is target_crs
        assert render_image.call_args.kwargs['extent'] is saved
        add_picture.assert_called_once()
    assert set(tmp_path.iterdir()) == before


def test_refresh_grid_reuses_grid_and_tracks_scale_on_repeated_navigation(monkeypatch):
    renderer = load_renderer(monkeypatch)
    item, grid = Map(), Mock()
    item.grid = Mock(return_value=grid)
    ctx = SimpleNamespace(
        layout=SimpleNamespace(itemById=lambda key: item if key == 'map_frame' else Label()),
        target_crs=Mock(), target_epsg=32651, project=Mock(), grid_label='WGS 84',
        qgis=SimpleNamespace(
            QgsCoordinateReferenceSystem=Mock(),
            QgsCoordinateTransform=Mock(return_value=SimpleNamespace(transformBoundingBox=lambda e: e)),
            QgsLayoutItemMapGrid=SimpleNamespace(Solid='solid'), QColor=Mock(), QFont=Mock(),
        ),
    )
    monkeypatch.setattr(renderer, '_configure_scale_bar', Mock())
    renderer.refresh_main_map_furniture(ctx)
    item.setScale(2500)
    renderer.refresh_main_map_furniture(ctx)
    assert ctx.map_scale == 2500 and ctx.scale_mode == 'manual'
    assert item.grid.call_count == 2
    assert grid.setIntervalX.call_count == 2
    assert grid.setLineSymbol.call_count == 2
    assert renderer._configure_scale_bar.call_count == 2


def test_saved_extent_requires_matching_crs(monkeypatch):
    renderer = load_renderer(monkeypatch)
    ctx = SimpleNamespace(map_states={'map_frame': {'crs': 'EPSG:4326', 'extent': [1, 2, 3, 4]}},
                          target_crs=SimpleNamespace(authid=lambda: 'EPSG:32651'))
    with pytest.raises(ValueError, match='CRS'):
        renderer._saved_map_extent(ctx, 'map_frame')


@pytest.mark.parametrize(('source', 'target'), (
    ('EPSG:32651', 'EPSG:4326'), ('EPSG:4326', 'EPSG:32651'),
    ('EPSG:4326', 'EPSG:4326'),
))
def test_overview_saved_extent_transforms_only_when_crs_changes(monkeypatch, source, target):
    renderer = load_renderer(monkeypatch)
    state = {'crs': source, 'extent': [1, 2, 101, 62]}
    original = copy.deepcopy(state)
    target_crs = SimpleNamespace(authid=lambda: target)
    transformed, fitted = Rect(116, 4, 128, 22), Rect(110, 4, 140, 22)
    transform = Mock(return_value=SimpleNamespace(transformBoundingBox=Mock(return_value=transformed)))
    source_crs = Mock()
    qgis = SimpleNamespace(QgsRectangle=Rect, QgsCoordinateReferenceSystem=Mock(return_value=source_crs),
                           QgsCoordinateTransform=transform)
    ctx = SimpleNamespace(qgis=qgis, target_crs=target_crs, project=Mock(),
                          map_states={'map_inset_2': state}, layout=SimpleNamespace(itemById=lambda _: Map()))
    fit = Mock(return_value=fitted)
    monkeypatch.setattr(renderer, '_fit_extent_to_frame', fit)
    result = renderer._saved_map_extent(ctx, 'map_inset_2', target_crs=target_crs)
    if source == target:
        assert result.coordinates == state['extent']
        transform.assert_not_called()
        fit.assert_not_called()
    else:
        qgis.QgsCoordinateReferenceSystem.assert_called_once_with(source)
        transform.assert_called_once_with(source_crs, target_crs, ctx.project)
        fit.assert_called_once_with(transformed, frame_width=100, frame_height=60)
        assert result is fitted
    assert state == original


def test_overview_snapshot_bounds_and_pin_use_selected_frame_crs(session):
    states = session.snapshot().maps
    assert states['map_inset_2']['crs'] == 'EPSG:4326'
    assert states['map_frame']['crs'] == states['map_inset']['crs'] == 'EPSG:32651'
    session.set_bounds('map_inset_2', '116,4,128,22')
    assert session.renderer._prepare_fixed_extent.call_args.kwargs['target_crs'].authid() == 'EPSG:4326'
    session.set_overlay('map_inset_2', 'pin')
    assert session.renderer._configure_inset_boundary_overlay.call_args.kwargs['target_crs'].authid() == 'EPSG:4326'
    transform = Mock(return_value=SimpleNamespace(transformBoundingBox=lambda _: Rect(116, 4, 128, 22)))
    session.ctx.qgis.QgsCoordinateTransform = transform
    assert session.geographic_bounds('map_inset_2') == '116.0000000,4.0000000,128.0000000,22.0000000'
    assert transform.call_args.args[0].authid() == 'EPSG:4326'


def test_switch_overview_crs_preserves_other_maps_and_reset_transforms_original(session):
    before = session.snapshot().maps
    session.set_overview_crs('project')
    after = session.snapshot().maps
    assert after['map_inset_2']['crs'] == 'EPSG:32651'
    assert after['map_frame'] == before['map_frame']
    assert after['map_inset'] == before['map_inset']
    session.renderer._saved_map_extent.return_value = Rect(1, 2, 101, 62)
    session.reset_map('map_inset_2')
    kwargs = session.renderer._saved_map_extent.call_args.kwargs
    assert kwargs['state'] == before['map_inset_2']
    assert kwargs['target_crs'].authid() == 'EPSG:32651'
    assert session._map('map_inset_2').extent().coordinates == [1, 2, 101, 62]


def test_explicit_options_override_environment_without_mutating_it(monkeypatch, tmp_path):
    import json
    import os
    from modules.map_export.editor_model import validate_options
    renderer = load_renderer(monkeypatch)
    metadata = tmp_path / 'map_metadata.json'
    metadata.write_text(json.dumps({'layout': {'location': 'Original', 'map_scale': 15000}}))
    monkeypatch.setenv('MAP_EXPORT_PAPER_SIZE', 'A4')
    monkeypatch.setenv('MAP_EXPORT_OVERVIEW_LABEL', 'Old overview')
    monkeypatch.setenv('MAP_EXPORT_OVERVIEW_BOUNDS', '1,2,3,4')
    options = validate_options({'paper_size': 'A3', 'overview_label': 'New overview',
                                'manual_scale': None, 'preview_dpi': 250})
    ctx = renderer.ExportContext(package_dir=tmp_path, title=None, logo_path=None,
                                 dpi=400, qgis=Mock(), render_options=options)
    ctx.metadata_path = metadata
    renderer._load_metadata_config(ctx)
    assert ctx.paper_size == 'A3' and ctx.map_scale is None
    assert ctx.overview_label == 'New overview'
    assert renderer._render_option(ctx, 'preview_dpi', lambda: 120) == 250
    assert os.environ['MAP_EXPORT_PAPER_SIZE'] == 'A4'


def test_text_style_copies_template_font_instead_of_losing_italics(monkeypatch):
    renderer = load_renderer(monkeypatch)
    class EditableLabel:
        def __init__(self):
            self.original_font = Mock()
            self.setText, self.setFont, self.setFontColor, self.refresh = Mock(), Mock(), Mock(), Mock()
        def font(self): return self.original_font
    item, font = EditableLabel(), Mock()
    qgis = SimpleNamespace(QgsLayoutItemLabel=EditableLabel, QFont=Mock(return_value=font), QColor=Mock())
    ctx = SimpleNamespace(qgis=qgis, layout=SimpleNamespace(itemById=lambda _: item))
    style = {'family': 'Arial', 'size': 18.5, 'bold': True, 'color': '#123456'}
    renderer.apply_editor_text(ctx, {'map_title': 'Edited'}, {'map_title': style})
    qgis.QFont.assert_called_once_with(item.original_font)
    font.setFamily.assert_called_once_with('Arial')
    font.setPointSizeF.assert_called_once_with(18.5)
    font.setBold.assert_called_once_with(True)
    item.setText.assert_called_once_with('Edited')
    item.setFont.assert_called_once_with(font)
    item.setFontColor.assert_called_once_with(qgis.QColor.return_value)


def test_editor_uses_a_separate_project_without_clearing_global_project(monkeypatch):
    renderer = load_renderer(monkeypatch)
    project_type = Mock()
    ctx = SimpleNamespace(qgis=SimpleNamespace(QgsProject=project_type),
                          target_crs=Mock(), render_options={})
    renderer._prepare_project(ctx)
    project_type.instance.assert_not_called()
    assert ctx.project is project_type.return_value
    ctx.project.clear.assert_not_called()


def test_coordinate_font_size_reaches_preview_snapshot_and_paper_rebuild(session):
    original = session.snapshot().maps
    session.set_grid_font_size(8.5)
    assert session.snapshot().options['grid_font_size'] == 8.5
    assert session.ctx.render_options['grid_font_size'] == 8.5
    assert session.snapshot().maps == original
    session.renderer.refresh_main_map_furniture.assert_called_once_with(session.ctx)
    session.zoom('map_frame', 1.2)
    session.set_paper('A4')
    assert session.ctx.render_options['grid_font_size'] == 8.5
    assert session.snapshot().options['grid_font_size'] == 8.5


@pytest.mark.parametrize('families', ([], ['Arial', 'Segoe UI']))
def test_export_refuses_empty_font_database(monkeypatch, families):
    renderer = load_renderer(monkeypatch)
    gui = ModuleType('qgis.PyQt.QtGui')
    gui.QFontDatabase = lambda: SimpleNamespace(families=lambda: families)
    monkeypatch.setitem(sys.modules, 'qgis.PyQt.QtGui', gui)
    if families:
        renderer._require_export_fonts()
    else:
        with pytest.raises(RuntimeError, match='font database is empty'):
            renderer._require_export_fonts()


def test_empty_fonts_stop_before_any_package_or_output_work(monkeypatch, tmp_path):
    renderer = load_renderer(monkeypatch)
    monkeypatch.setattr(renderer, '_load_qgis_classes', Mock(return_value=SimpleNamespace(QgsApplication=Mock())))
    monkeypatch.setattr(renderer, '_init_qgis_application', Mock())
    monkeypatch.setattr(renderer, '_require_export_fonts', Mock(side_effect=RuntimeError('font database is empty')))
    paths, insets = Mock(), Mock()
    monkeypatch.setattr(renderer, '_validate_package_paths', paths)
    monkeypatch.setattr(renderer, '_configure_insets', insets)
    with pytest.raises(RuntimeError, match='font database is empty'):
        renderer.build_qgis_print_layout(package_dir=tmp_path, render_options={}, live_insets=False)
    paths.assert_not_called()
    insets.assert_not_called()
