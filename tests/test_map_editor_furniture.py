"""Page furniture behavior with fake Qt/GIS and pytest-owned map packages."""
import copy
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from modules.map_export.editor_furniture import (
    apply_layout_settings, furniture_values, validate_layout_settings,
)
from modules.map_export.editor_model import (
    EditorDesign, PAPER_SIZES_MM, create_export_attempt, design_from_package,
    design_payload, load_design, save_design,
)
from modules.map_export.editor_session import run_snapshot_export
from modules.map_export.editor_workflow import DesignHistory, RecoveryStore, style_preset
from tests.test_map_editor_model import package
from tests.test_map_editor_session import session, Rect, Label, load_renderer
from tests.test_map_editor_window import window


SETTINGS = {'info_font_size': 72, 'scale_font_size': 60, 'scale_height_mm': 12,
            'logo_position': {'x_fraction': 0.55, 'y_fraction': 0.7}}


class Font:
    def __init__(self, other=None):
        self.size = other.size if other is not None else 12
        self.family_name, self.is_bold = 'Arial', True
    def setPointSizeF(self, value): self.size = value
    def pointSizeF(self): return self.size
    def family(self): return self.family_name
    def bold(self): return self.is_bold


class TextFormat:
    def __init__(self):
        self.value, self.unit, self.color = 10, 'pt', '#334455'
    def size(self): return self.value
    def setSize(self, value): self.value = value
    def setSizeUnit(self, value): self.unit = value


class Point:
    def __init__(self, x, y, *_args): self.coordinates = (x, y)
    def x(self): return self.coordinates[0]
    def y(self): return self.coordinates[1]


class Info(Label):
    def __init__(self):
        super().__init__()
        self.current_font, self.size = Font(), (100, 20)
    def font(self): return Font(self.current_font)
    def setFont(self, value): self.current_font = Font(value)
    def sizeWithUnits(self): return self.size
    def adjustSizeToText(self): self.size = (100, self.current_font.size * 1.2)
    def attemptResize(self, value): self.size = value


class Bar:
    def __init__(self): self.format, self.bar_height = TextFormat(), 3
    def textFormat(self): return copy.deepcopy(self.format)
    def setTextFormat(self, value): self.format = copy.deepcopy(value)
    def height(self): return self.bar_height
    def setHeight(self, value): self.bar_height = value
    def refresh(self): pass


class Logo:
    def __init__(self, width, height):
        self.point = Point(width * .72, height * .76)
        self.rectangle = Rect(0, 0, width * .18, height * .15)
    def pos(self): return self.point
    def positionWithUnits(self): return self.point
    def rect(self): return self.rectangle
    def attemptMove(self, point, reference):
        assert reference is False
        self.point = point
    def refresh(self): pass


@pytest.fixture
def furniture_session(session):
    builder = session.renderer.build_qgis_print_layout.side_effect
    def build(**kwargs):
        ctx = builder(**kwargs)
        existing = ctx.layout.itemById
        width, height = PAPER_SIZES_MM[kwargs['render_options']['paper_size']]
        items = {'map_info': Info(), 'scale_bar': Bar(), 'map_logo': Logo(width, height)}
        ctx.layout.itemById = lambda key: items.get(key) or existing(key)
        ctx.qgis.QFont, ctx.qgis.QgsLayoutPoint = Font, Point
        ctx.qgis.QgsUnitTypes = SimpleNamespace(RenderPoints='pt', LayoutMillimeters='mm')
        apply_layout_settings(ctx, kwargs.get('layout_settings', {}))
        return ctx
    session.renderer.build_qgis_print_layout.side_effect = build
    session.renderer.apply_layout_settings.side_effect = apply_layout_settings
    session.rebuild()
    return session


@pytest.mark.parametrize('settings', [None, [], {'unknown': 1}, {'info_font_size': True},
    {'info_font_size': 0}, {'scale_font_size': float('inf')}, {'scale_height_mm': float('nan')},
    {'scale_height_mm': 101}, {'info_font_size': 1001},
    {'logo_position': {'x_fraction': -0.1, 'y_fraction': 0}},
    {'logo_position': {'x_fraction': 0, 'y_fraction': 1.1}},
    {'logo_position': {'x_fraction': 0}},
    {'logo_position': {'x_fraction': '0', 'y_fraction': 0}}])
def test_invalid_settings_fail_before_rendering(settings):
    ctx = SimpleNamespace(layout=Mock())
    with pytest.raises(ValueError):
        apply_layout_settings(ctx, settings)
    ctx.layout.itemById.assert_not_called()


def test_sparse_defaults_do_not_touch_legacy_layout():
    ctx = SimpleNamespace(layout=Mock())
    apply_layout_settings(ctx, {})
    ctx.layout.itemById.assert_not_called()


def test_furniture_sizes_preserve_wording_font_and_map_extents(furniture_session):
    s = furniture_session
    original = s.snapshot()
    for key, value in SETTINGS.items():
        s.set_layout_setting(key, value)
    info, bar = s.ctx.layout.itemById('map_info'), s.ctx.layout.itemById('scale_bar')
    assert info.font().pointSizeF() == 72 and info.font().bold()
    assert info.font().family() == 'Arial' and info.text() == 'Label'
    assert info.size[1] > 20  # Enlarge the label frame to avoid clipping larger text.
    assert bar.textFormat().size() == 60 and bar.textFormat().color == '#334455'
    assert bar.textFormat().unit == 'pt' and bar.height() == 12
    assert s.snapshot().maps == original.maps and s.snapshot().texts == original.texts
    values = furniture_values(s.ctx)
    assert values['scale_font_size'] == 60 and values['info_font_size'] == 72
    s.renderer._configure_main_map.assert_not_called()


def test_undo_removes_overrides_and_restores_actual_template_values(furniture_session):
    s = furniture_session
    before = furniture_values(s.ctx)
    history = DesignHistory(s)
    history.begin_group()
    for key, value in SETTINGS.items():
        history.execute(lambda key=key, value=value: s.set_layout_setting(key, value))
    history.end_group()
    assert len(history.undo_steps) == 1 and history.dirty
    history.undo()
    assert not history.dirty and not s.design.layout_settings
    assert furniture_values(s.ctx) == before
    assert s.ctx.layout.itemById('map_info').size == (100, 20)
    history.redo()
    assert s.design.layout_settings == SETTINGS and history.dirty


def test_logo_limits_clamp_drag_but_reject_invalid_numeric_position(furniture_session):
    s = furniture_session
    before = s.snapshot()
    history = DesignHistory(s)
    with pytest.raises(ValueError, match='entire logo'):
        history.execute(lambda: s.move_logo(419, 296))
    assert s.snapshot() == before and not history.dirty and not history.undo_steps
    s.move_logo(-100, 10000, clamp=True)
    values = furniture_values(s.ctx)
    assert values['logo_x_mm'] == 0 and values['logo_y_mm'] == values['logo_max_y_mm']


def test_missing_logo_does_not_partially_apply_info_style(furniture_session):
    ctx = furniture_session.ctx
    get = ctx.layout.itemById
    ctx.layout.itemById = lambda key: None if key == 'map_logo' else get(key)
    with pytest.raises(ValueError, match='missing'):
        apply_layout_settings(ctx, SETTINGS)
    assert get('map_info').font().pointSizeF() == 12


def test_qt_failure_rolls_back_design_and_preview(furniture_session):
    s = furniture_session
    history = DesignHistory(s)
    bar = s.ctx.layout.itemById('scale_bar')
    bar.setHeight = Mock(side_effect=RuntimeError('setter failed'))
    with pytest.raises(RuntimeError, match='setter failed'):
        history.execute(lambda: s.set_layout_setting('scale_height_mm', 12))
    assert s.ctx.layout.itemById('scale_bar').height() == 3
    assert not s.design.layout_settings and not history.dirty and not history.undo_steps


@pytest.mark.parametrize('paper', ['A4', 'A3', '4X8FT'])
def test_paper_rebuild_preserves_print_sizes_and_relative_logo_position(furniture_session, paper):
    s = furniture_session
    for key, value in SETTINGS.items(): s.set_layout_setting(key, value)
    maps = s.snapshot().maps
    s.set_paper(paper)
    values = furniture_values(s.ctx)
    width, height = PAPER_SIZES_MM[paper]
    assert values['logo_x_mm'] == pytest.approx(width * .55)
    assert values['logo_y_mm'] == pytest.approx(height * .7)
    assert values['info_font_size'] == 72 and values['scale_height_mm'] == 12
    assert s.snapshot().maps == maps
    s.set_inset_crs('map_inset', 'project')
    assert s.design.layout_settings == SETTINGS and furniture_values(s.ctx) == values


def test_save_reopen_metadata_and_recovery_keep_optional_settings(package, tmp_path):
    design = EditorDesign(package, layout_settings=copy.deepcopy(SETTINGS)).validate()
    path = tmp_path / 'workspace' / 'design.v2.mapedit.json'
    save_design(design, path)
    assert load_design(path).layout_settings == SETTINGS
    data = json.loads((package / 'map_metadata.json').read_text())
    data['print_layout'] = {'editor_state': {'layout_settings': SETTINGS}}
    (package / 'map_metadata.json').write_text(json.dumps(data))
    assert design_from_package(package).layout_settings == SETTINGS
    recovery = RecoveryStore(tmp_path / 'workspace', package, path)
    recovery.write(design)
    assert recovery.read(recovery.path).layout_settings == SETTINGS
    assert 'layout_settings' not in style_preset('Existing style', design)


@pytest.mark.parametrize('version', [1, 2])
def test_old_design_without_settings_keeps_template_appearance(package, tmp_path, version):
    payload = design_payload(EditorDesign(package).validate(), tmp_path)
    payload.pop('layout_settings')
    payload['version'] = version
    path = tmp_path / 'old.mapedit.json'
    path.write_text(json.dumps(payload))
    assert load_design(path).layout_settings == {}


@pytest.mark.parametrize('fail', [False, True])
def test_export_uses_immutable_furniture_snapshot_and_preserves_sources(package, tmp_path, fail):
    design = EditorDesign(package, layout_settings=copy.deepcopy(SETTINGS)).validate()
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    earlier = tmp_path / 'workspace' / 'earlier.pdf'
    earlier.parent.mkdir()
    earlier.write_bytes(b'previous export')
    attempt = create_export_attempt(design, tmp_path / 'workspace')
    design.layout_settings['info_font_size'] = 90
    renderer = Mock()
    renderer._export_print_outputs.return_value = {}
    if fail:
        renderer._export_print_outputs.side_effect = RuntimeError('failed export')
        with pytest.raises(RuntimeError, match='failed export'):
            run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    else:
        run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    assert renderer.build_qgis_print_layout.call_args.kwargs['layout_settings'] == SETTINGS
    assert renderer.build_qgis_print_layout.return_value.editor_state['layout_settings'] == SETTINGS
    assert earlier.read_bytes() == b'previous export'
    assert {p.name: p.read_bytes() for p in package.iterdir()} == before


def test_window_selects_furniture_and_does_not_edit_on_selection(window):
    window.session = Mock()
    window.refresh_controls = Mock()
    window.update_state = Mock()
    for key in ('map_info', 'scale_bar', 'map_logo'):
        window.select_item(key)
        assert window.selected_id == key
        window.tabs.setCurrentIndex.assert_called_with(4)
    window.session.set_layout_setting.assert_not_called()
    window.session.move_logo.assert_not_called()


def test_logo_drag_is_one_history_step_and_never_pans_map(window, furniture_session):
    s = furniture_session
    window.session, window.history = s, DesignHistory(s)
    window.refresh_controls, window.update_state = Mock(), Mock()
    window.hit_item = Mock(return_value='map_logo')
    before = s.snapshot()
    points = [Point(0, 0), Point(-10, -5), Point(-20, -10)]
    window.view.mapToScene.side_effect = points
    event = Mock()
    event.button.return_value = 1
    window.view.mousePressEvent(event)
    window.view.mouseMoveEvent(event)
    window.view.mouseMoveEvent(event)
    window.view.mouseReleaseEvent(event)
    assert len(window.history.undo_steps) == 1 and window.history.dirty
    assert s.snapshot().maps == before.maps
    assert s.logo_position() == pytest.approx((420 * .72 - 20, 297 * .76 - 10))
    window.history.undo()
    assert s.snapshot() == before


def test_window_numeric_controls_use_undo_and_preserve_other_logo_axis(window, furniture_session):
    window.session, window.history = furniture_session, DesignHistory(furniture_session)
    window.refresh_controls, window.update_state = Mock(), Mock()
    x, y = furniture_session.logo_position()
    window.change_furniture('logo_x_mm', 50)
    assert furniture_session.logo_position() == (50, y)
    window.change_furniture('info_font_size', 70)
    assert furniture_session.design.layout_settings['info_font_size'] == 70
    assert len(window.history.undo_steps) == 2
    window.history.undo()
    window.history.undo()
    assert furniture_session.logo_position() == (x, y)


def test_navigation_keeps_auto_values_and_reapplies_furniture(monkeypatch, furniture_session):
    exporter = load_renderer(monkeypatch)
    ctx = furniture_session.ctx
    ctx.target_epsg, ctx.grid_label = 32651, 'WGS 84'
    ctx.qgis.QgsCoordinateTransform = lambda *args: SimpleNamespace(transformBoundingBox=lambda e: e)
    ctx.qgis.QgsLayoutItemMapGrid, ctx.qgis.QColor = Mock(), Mock()
    monkeypatch.setattr(exporter, '_add_coordinate_grid', Mock(return_value=.001))
    monkeypatch.setattr(exporter, '_configure_scale_bar', Mock())
    apply_layout_settings(ctx, SETTINGS)
    for _ in range(3): exporter.refresh_main_map_furniture(ctx)
    assert 'Scale 1:1,000' in ctx.layout.itemById('map_info').text()
    assert ctx.layout.itemById('map_info').font().pointSizeF() == 72
    assert ctx.layout.itemById('scale_bar').textFormat().size() == 60
    assert ctx.layout_settings == SETTINGS
    assert ctx.grid_interval_degrees == .001


@pytest.mark.parametrize('live', [True, False])
def test_shared_builder_applies_furniture_after_labels_and_insets(monkeypatch, tmp_path, live):
    renderer = load_renderer(monkeypatch)
    events = []
    monkeypatch.setattr(renderer, '_load_qgis_classes', lambda: SimpleNamespace(QgsApplication=object))
    monkeypatch.setattr(renderer, '_init_qgis_application', Mock())
    monkeypatch.setattr(renderer, '_require_export_fonts', Mock())
    for name in ('_validate_package_paths', '_load_metadata_config', '_compute_target_crs',
                 '_prepare_project', '_load_boundary_layers', '_load_orthomosaic_layers',
                 '_assemble_map_layers', '_build_print_layout', '_configure_main_map',
                 '_configure_layout_variables', '_apply_layout_variables', '_configure_insets'):
        monkeypatch.setattr(renderer, name, lambda ctx, name=name: events.append(name))
    monkeypatch.setattr(renderer, 'apply_editor_text', lambda *args: events.append('text'))
    apply = Mock(side_effect=lambda *args: events.append('furniture'))
    monkeypatch.setattr(renderer, 'apply_layout_settings', apply)
    ctx = renderer.build_qgis_print_layout(package_dir=tmp_path, live_insets=live, layout_settings=SETTINGS)
    apply.assert_called_once_with(ctx, SETTINGS)
    assert events[-3:] == ['_configure_insets', 'text', 'furniture']


def test_invalid_builder_settings_never_initialize_gis(monkeypatch, tmp_path):
    renderer = load_renderer(monkeypatch)
    bootstrap = Mock()
    monkeypatch.setattr(renderer, '_load_qgis_classes', bootstrap)
    with pytest.raises(ValueError):
        renderer.build_qgis_print_layout(package_dir=tmp_path, layout_settings={'scale_font_size': -1})
    bootstrap.assert_not_called()


def test_furniture_controls_display_actual_sizes_and_page_limits(window, furniture_session):
    s = furniture_session
    window.session = s
    window.session.geographic_bounds = lambda key: '125,7,126,8'
    window.selected_id = 'map_logo'
    window.refresh_controls()
    for key, value in furniture_values(s.ctx).items():
        if key in window.furniture_controls:
            window.furniture_controls[key].setValue.assert_called_with(value)
    window.furniture_controls['logo_x_mm'].setMaximum.assert_called_with(420 * .82)
    window.furniture_controls['info_font_size'].setKeyboardTracking.assert_called_with(False)
    assert not s.design.layout_settings


def test_wheel_over_logo_zooms_page_without_moving_logo_or_map(window):
    window.session = Mock()
    window.hit_item = Mock(return_value='map_logo')
    window.zoom_page = Mock()
    event = Mock()
    event.angleDelta.return_value.y.return_value = 120
    event.modifiers.return_value = 0
    window.view.wheelEvent(event)
    window.zoom_page.assert_called_once()
    window.session.move_logo.assert_not_called()
    window.session.zoom.assert_not_called()


def test_loading_controls_cannot_trigger_furniture_edits(window):
    window.session, window.loading = Mock(), True
    window.change_furniture('info_font_size', 80)
    window.session.set_layout_setting.assert_not_called()


def test_default_template_uses_expected_ids_mm_units_and_top_left_logo():
    from xml.etree import ElementTree
    template = Path(__file__).resolve().parents[1] / 'assets/qgis_layouts/client_boundary_map-v6.qpt'
    items = {item.get('id'): item for item in ElementTree.parse(template).iter('LayoutItem')}
    assert {'map_info', 'scale_bar', 'map_logo'} <= items.keys()
    assert items['map_logo'].get('referencePoint') == '0'
    assert items['map_logo'].get('itemRotation') == '0'
    assert items['map_logo'].get('position').endswith(',mm')
    assert items['map_logo'].get('size').endswith(',mm')
