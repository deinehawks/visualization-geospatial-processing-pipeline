"""Movable layout objects, independent map content, and aligned inset exports."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.map_export.editor_furniture import (
    MOVABLE_IDS, apply_layout_settings, object_position_values, validate_layout_settings,
)
from modules.map_export.editor_model import (
    EditorDesign, PAPER_SIZES_MM, create_export_attempt, design_from_package,
    design_payload, load_design, save_design,
)
from modules.map_export.editor_session import run_snapshot_export
from modules.map_export.editor_workflow import DesignHistory, RecoveryStore
from tests.test_map_editor_furniture import furniture_session, Logo, Point
from tests.test_map_editor_session import session, Rect, Label, load_renderer
from tests.test_map_editor_model import package
from tests.test_map_editor_window import window


@pytest.fixture
def positioned_session(furniture_session):
    s = furniture_session
    builder = s.renderer.build_qgis_print_layout.side_effect
    def build(**kwargs):
        settings = kwargs.get('layout_settings', {})
        # Supply position-capable objects before applying a saved design.
        ctx = builder(**dict(kwargs, layout_settings={k: v for k, v in settings.items()
                                                    if k != 'object_positions'}))
        existing = ctx.layout.itemById
        width, height = PAPER_SIZES_MM[kwargs['render_options']['paper_size']]
        items = {}
        for key in MOVABLE_IDS:
            item = existing(key)
            if item is None:
                item = Label()
            if not hasattr(item, 'pos'):
                item.point = Point(width * .05, height * .05)
                item.pos = lambda item=item: item.point
                item.positionWithUnits = lambda item=item: item.point
                def move(point, reference, item=item):
                    assert reference is False
                    item.point = point
                item.attemptMove = move
            if not hasattr(item, 'rect'):
                item.rect = lambda: Rect(0, 0, 80, 10)
            items[key] = item
        ctx.layout.itemById = lambda key: items.get(key) or existing(key)
        apply_layout_settings(ctx, settings)
        return ctx
    s.renderer.build_qgis_print_layout.side_effect = build
    s.rebuild()
    return s


@pytest.mark.parametrize('key', MOVABLE_IDS)
def test_each_object_moves_without_changing_content_size_or_other_objects(positioned_session, key):
    s = positioned_session
    before = s.snapshot()
    originals = {name: s.object_position(name) for name in MOVABLE_IDS}
    item = s.ctx.layout.itemById(key)
    size = (item.rect().width(), item.rect().height())
    s.move_object(key, 50, 40)
    assert s.object_position(key) == pytest.approx((50, 40))
    assert (item.rect().width(), item.rect().height()) == size
    assert s.snapshot().maps == before.maps and s.snapshot().texts == before.texts
    assert all(s.object_position(name) == value for name, value in originals.items() if name != key)
    assert s.design.layout_settings['object_positions'][key] == {
        'x_fraction': 50 / 420, 'y_fraction': 40 / 297,
    }
    s.renderer._configure_main_map.assert_not_called()


@pytest.mark.parametrize('key', ['map_frame', 'map_title', 'map_info', 'scale_bar', 'north_arrow'])
def test_position_history_restores_preview_and_dirty_state(positioned_session, key):
    s = positioned_session
    original = s.object_position(key)
    history = DesignHistory(s)
    history.execute(lambda: s.move_object(key, 50, 40))
    history.mark_saved()
    history.undo()
    assert s.object_position(key) == original and history.dirty
    assert not s.design.layout_settings.get('object_positions')
    history.redo()
    assert s.object_position(key) == pytest.approx((50, 40)) and not history.dirty


@pytest.mark.parametrize('paper', ['A4', 'A3', '4X8FT'])
def test_positions_survive_paper_and_crs_rebuilds_with_unchanged_extents(positioned_session, paper):
    s = positioned_session
    for key in MOVABLE_IDS:
        s.move_object(key, 30, 25)
    settings, maps = copy.deepcopy(s.design.layout_settings), s.snapshot().maps
    s.set_paper(paper)
    if paper == '4X8FT':
        settings.update(info_font_size=41, scale_font_size=40, scale_height_mm=5)
    width, height = PAPER_SIZES_MM[paper]
    for key in MOVABLE_IDS:
        assert s.object_position(key) == pytest.approx((30 / 420 * width, 25 / 297 * height))
    assert s.snapshot().maps == maps and s.design.layout_settings == settings
    s.set_inset_crs('map_inset', 'project')
    assert s.design.layout_settings == settings


def test_legacy_logo_and_generic_position_undo_have_one_original_origin(positioned_session):
    s = positioned_session
    original = s.object_position('map_logo')
    history = DesignHistory(s)
    history.execute(lambda: s.move_logo(100, 150))
    assert 'logo_position' in s.design.layout_settings
    history.execute(lambda: s.move_object('map_logo', 80, 120))
    assert 'logo_position' not in s.design.layout_settings
    history.undo()
    assert s.object_position('map_logo') == pytest.approx((100, 150))
    history.undo()
    assert s.object_position('map_logo') == original and not history.dirty
    history.redo()
    history.redo()
    s.move_logo(90, 130)  # Original logo-only controls use the same authoritative position.
    assert s.object_position('map_logo') == pytest.approx((90, 130))
    assert 'logo_position' not in s.design.layout_settings


@pytest.mark.parametrize('value', [[], {'page': {}}, {'map_inset_basemap': {}},
    {'map_title': {'x_fraction': float('nan'), 'y_fraction': 0}},
    {'map_title': {'x_fraction': 0, 'y_fraction': 2}},
    {'map_frame': {'x_fraction': True, 'y_fraction': 0}},
    {'map_info': {'x_fraction': 0}}, {'north_arrow': '0,0'}])
def test_invalid_positions_fail_before_layout_access(value):
    ctx = SimpleNamespace(layout=Mock())
    with pytest.raises(ValueError):
        apply_layout_settings(ctx, {'object_positions': value})
    ctx.layout.itemById.assert_not_called()


def test_missing_object_rejects_all_edits_before_mutation(positioned_session):
    ctx = positioned_session.ctx
    get = ctx.layout.itemById
    ctx.layout.itemById = lambda key: None if key == 'north_arrow' else get(key)
    with pytest.raises(ValueError, match='north_arrow'):
        apply_layout_settings(ctx, {'info_font_size': 80, 'object_positions': {
            'map_title': {'x_fraction': .1, 'y_fraction': .1},
            'north_arrow': {'x_fraction': .2, 'y_fraction': .2}}})
    assert get('map_info').font().pointSizeF() == 12
    assert get('map_title').pos().x() == 420 * .05


def test_invalid_move_and_partial_qt_failure_roll_back_without_history(positioned_session):
    s = positioned_session
    before, original = s.snapshot(), s.object_position('map_frame')
    history = DesignHistory(s)
    with pytest.raises(ValueError, match='entire object'):
        history.execute(lambda: s.move_object('map_frame', 419, 296))
    assert s.snapshot() == before and not history.undo_steps
    item = s.ctx.layout.itemById('map_frame')
    def fail_after_move(point, reference):
        item.point = point
        raise RuntimeError('position setter failed')
    item.attemptMove = fail_after_move
    with pytest.raises(RuntimeError, match='position setter failed'):
        history.execute(lambda: s.move_object('map_frame', 50, 40))
    assert s.object_position('map_frame') == original
    assert s.snapshot() == before and not history.dirty and not history.undo_steps


def test_drag_clamps_to_current_object_bounds(positioned_session):
    s = positioned_session
    s.move_object('map_frame', -100, 10000, clamp=True)
    values = object_position_values(s.ctx, 'map_frame')
    assert values['x_mm'] == 0 and values['y_mm'] == values['max_y_mm']


def test_both_inset_backgrounds_follow_their_own_frame_and_undo(positioned_session):
    s = positioned_session
    ctx = s.ctx
    get = ctx.layout.itemById
    backgrounds = {key + '_basemap': Logo(420, 297) for key in ('map_inset', 'map_inset_2')}
    ctx.layout.itemById = lambda key: backgrounds.get(key) or get(key)
    other = backgrounds['map_inset_2_basemap'].pos().coordinates
    origin = s.object_position('map_inset')
    history = DesignHistory(s)
    history.execute(lambda: s.move_object('map_inset', 80, 60))
    assert backgrounds['map_inset_basemap'].pos().coordinates == pytest.approx((80, 60))
    assert backgrounds['map_inset_2_basemap'].pos().coordinates == other
    history.undo()
    assert backgrounds['map_inset_basemap'].pos().coordinates == origin


def test_final_inset_pictures_have_independent_ids_and_match_frames(monkeypatch):
    renderer = load_renderer(monkeypatch)
    layout = SimpleNamespace(addLayoutItem=Mock())
    picture_type = Mock()
    for key in ('map_inset', 'map_inset_2'):
        inset = Mock()
        inset.id.return_value = key
        inset.zValue.return_value = 3
        renderer._add_inset_basemap_picture(layout=layout, inset_item=inset,
            image_path='owned-image.png', QgsLayoutItemPicture=picture_type)
        picture = picture_type.return_value
        picture.setId.assert_called_with(key + '_basemap')
        picture.attemptMove.assert_called_with(inset.positionWithUnits())
        picture.attemptResize.assert_called_with(inset.sizeWithUnits())
    assert layout.addLayoutItem.call_count == 2


def test_save_metadata_recovery_and_old_designs_preserve_positions(package, tmp_path):
    settings = {'object_positions': {key: {'x_fraction': .1, 'y_fraction': .2} for key in MOVABLE_IDS}}
    design = EditorDesign(package, layout_settings=settings).validate()
    path = tmp_path / 'workspace' / 'design.v2.mapedit.json'
    save_design(design, path)
    assert load_design(path).layout_settings == settings
    recovery = RecoveryStore(tmp_path / 'workspace', package, path)
    recovery.write(design)
    assert recovery.read(recovery.path).layout_settings == settings
    metadata_path = package / 'map_metadata.json'
    data = json.loads(metadata_path.read_text())
    data['print_layout'] = {'layout_settings': settings}
    metadata_path.write_text(json.dumps(data))
    assert design_from_package(package).layout_settings == settings
    payload = design_payload(design, tmp_path)
    for version in (1, 2):
        payload['version'] = version
        payload.pop('layout_settings', None)
        old = tmp_path / f'v{version}.mapedit.json'
        old.write_text(json.dumps(payload))
        assert load_design(old).layout_settings == {}


@pytest.mark.parametrize('fail', [False, True])
def test_helper_snapshot_retains_exact_positions_and_preserves_previous_files(package, tmp_path, fail):
    settings = {'object_positions': {key: {'x_fraction': .1, 'y_fraction': .2} for key in MOVABLE_IDS}}
    design = EditorDesign(package, layout_settings=copy.deepcopy(settings)).validate()
    before = {p.name: p.read_bytes() for p in package.iterdir()}
    previous = tmp_path / 'workspace' / 'previous.pdf'
    previous.parent.mkdir()
    previous.write_bytes(b'keep earlier export')
    attempt = create_export_attempt(design, previous.parent)
    design.layout_settings['object_positions']['map_frame']['x_fraction'] = .4
    renderer = Mock()
    renderer._export_print_outputs.return_value = {}
    if fail:
        renderer._export_print_outputs.side_effect = RuntimeError('controlled export error')
        with pytest.raises(RuntimeError, match='controlled export error'):
            run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    else:
        run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    assert renderer.build_qgis_print_layout.call_args.kwargs['layout_settings'] == settings
    assert previous.read_bytes() == b'keep earlier export'
    assert {p.name: p.read_bytes() for p in package.iterdir()} == before


@pytest.mark.parametrize('key', MOVABLE_IDS)
def test_drag_moves_every_object_in_one_history_step(window, positioned_session, key):
    s = positioned_session
    window.session, window.history, window.move_objects = s, DesignHistory(s), True
    window.refresh_controls, window.update_state = Mock(), Mock()
    window.hit_item = Mock(return_value=key)
    before, original = s.snapshot(), s.object_position(key)
    window.view.mapToScene.side_effect = [Point(0, 0), Point(10, 5), Point(20, 10)]
    event = Mock()
    event.button.return_value = 1
    window.view.mousePressEvent(event)
    window.view.mouseMoveEvent(event)
    window.view.flush_pan()  # A second render/update during the same drag stays grouped.
    window.view.mouseMoveEvent(event)
    window.view.mouseReleaseEvent(event)
    assert len(window.history.undo_steps) == 1
    assert s.object_position(key) == pytest.approx((original[0] + 20, original[1] + 10))
    assert s.snapshot().maps == before.maps and s.snapshot().texts == before.texts
    window.history.undo()
    assert s.snapshot() == before and s.object_position(key) == original


def test_move_mode_wheel_is_page_zoom_and_mode_change_is_not_an_edit(window, positioned_session):
    s = positioned_session
    window.session, window.history = s, DesignHistory(s)
    window.refresh_controls = Mock()
    window.change_move_mode(True)
    assert window.move_objects and not window.history.dirty and not window.history.undo_steps
    window.tabs.setCurrentIndex.assert_called_with(4)
    window.hit_item, window.zoom_page = Mock(return_value='map_frame'), Mock()
    event = Mock()
    event.angleDelta.return_value.y.return_value = 120
    event.modifiers.return_value = 0
    window.view.wheelEvent(event)
    window.zoom_page.assert_called_once()
    assert not window.history.dirty
    window.change_move_mode(False)
    assert not window.move_objects


def test_numeric_position_fields_only_move_selected_axis_and_support_undo(window, positioned_session):
    s = positioned_session
    window.session, window.history, window.selected_id = s, DesignHistory(s), 'north_arrow'
    window.refresh_controls, window.update_state = Mock(), Mock()
    original = s.object_position('north_arrow')
    window.change_object_position('x', 80)
    assert s.object_position('north_arrow') == pytest.approx((80, original[1]))
    window.history.undo()
    assert s.object_position('north_arrow') == original


def test_hit_testing_respects_stacking_and_selected_object_in_move_mode(window):
    items = {}
    for key, depth in [('map_frame', 0), ('map_inset', 2), ('map_title', 5)]:
        items[key] = SimpleNamespace(isVisible=lambda: True, zValue=lambda d=depth: d,
                                    sceneBoundingRect=lambda: SimpleNamespace(contains=lambda p: True))
    window.session = SimpleNamespace(ctx=SimpleNamespace(layout=SimpleNamespace(itemById=items.get)))
    assert window.hit_item(Point(0, 0)) == 'map_title'
    window.move_objects, window.selected_id = True, 'map_frame'
    assert window.hit_item(Point(0, 0)) == 'map_frame'


def test_movable_ids_cover_all_named_template_objects():
    from pathlib import Path
    from xml.etree import ElementTree
    path = Path(__file__).resolve().parents[1] / 'assets/qgis_layouts/client_boundary_map-v6.qpt'
    items = [item for item in ElementTree.parse(path).iter('LayoutItem') if item.get('id')]
    assert {item.get('id') for item in items} == set(MOVABLE_IDS)
    assert all(item.get('referencePoint') == '0' and item.get('position').endswith(',mm') for item in items)
