"""Requested print sizes with fake fonts/GIS and pytest-owned designs only."""
import copy
from unittest.mock import Mock

import pytest

from modules.map_export.editor_model import (
    EditorDesign, TARPAULIN_TEXT_SIZES, TARPAULIN_LAYOUT_DEFAULTS,
    create_export_attempt, load_design, save_design, with_tarpaulin_defaults,
)
from modules.map_export.editor_session import EditorSession, run_snapshot_export
from modules.map_export.editor_workflow import DesignHistory
from tests.test_map_editor_model import package
from tests.test_map_editor_session import session
from tests.test_map_editor_furniture import furniture_session
from tests.test_map_editor_window import window


def assert_profile(design):
    assert {key: style['size'] for key, style in design.text_styles.items()} == {
        'map_title': 85, 'map_location': 55, 'label_inset': 45,
        'label_inset_2': 45, 'map_disclaimer': 40,
    }
    assert design.options['grid_font_size'] == 30
    assert design.map_styles['map_inset']['pin_size_mm'] == 10
    assert design.map_styles['map_inset_2']['pin_size_mm'] == 10
    assert all(design.layout_settings[key] == value for key, value in
               {'info_font_size': 41, 'scale_font_size': 40, 'scale_height_mm': 5}.items())


def test_selecting_large_paper_applies_all_sizes_together(furniture_session):
    s = furniture_session
    original = s.snapshot()
    s.set_paper('4X8FT')
    result = s.snapshot()
    assert_profile(result)
    assert result.maps == original.maps and result.texts == original.texts
    assert (result.options['preview_dpi'], result.options['pdf_dpi'], result.options['inset_dpi']) == (300, 300, 300)
    s.renderer._configure_main_map.assert_not_called()


def test_profile_changes_only_sizes_preserving_colors_positions_and_wording(session):
    design = session.snapshot()
    design.options.update(paper_size='4X8FT', preview_dpi=150, pdf_dpi=150, inset_dpi=300)
    for style in design.text_styles.values():
        style.update(family='Custom family', color='#123456', bold=True)
    design.map_styles['map_inset'].update(pin_color='#112233', boundary_width_mm=1.5)
    design.layout_settings = {'object_positions': {'map_title': {'x_fraction': .1, 'y_fraction': .2}}}
    original = copy.deepcopy(design)
    changed = with_tarpaulin_defaults(design)
    assert_profile(changed)
    assert design == original  # Pure profile application never mutates its input.
    assert changed.maps == original.maps and changed.texts == original.texts
    assert changed.layout_settings['object_positions'] == original.layout_settings['object_positions']
    assert changed.options['pdf_dpi'] == 150
    for key, style in changed.text_styles.items():
        assert {k: v for k, v in style.items() if k != 'size'} == {
            k: v for k, v in original.text_styles[key].items() if k != 'size'}
    assert changed.map_styles['map_inset']['pin_color'] == '#112233'
    assert changed.map_styles['map_inset']['boundary_width_mm'] == 1.5
    assert changed.grid_style == original.grid_style


@pytest.mark.parametrize('paper', ['A4', 'A3'])
def test_other_paper_styles_do_not_receive_tarpaulin_defaults(session, paper):
    before = session.snapshot()
    session.set_paper(paper)
    after = session.snapshot()
    assert after.text_styles == before.text_styles
    assert after.map_styles == before.map_styles
    assert after.layout_settings == before.layout_settings
    assert after.options['grid_font_size'] == 6
    with pytest.raises(ValueError, match='4X8FT'):
        session.apply_tarpaulin_defaults()


def test_paper_change_and_apply_button_are_independently_undoable(furniture_session):
    s = furniture_session
    before = s.snapshot()
    history = DesignHistory(s)
    history.execute(lambda: s.set_paper('4X8FT'))
    assert_profile(s.snapshot())
    assert len(history.undo_steps) == 1
    history.undo()
    assert s.snapshot() == before and not history.dirty
    history.redo()
    s.design.options['grid_font_size'] = 18
    s.design.map_styles['map_inset']['pin_size_mm'] = 7
    before_apply = s.snapshot()
    history = DesignHistory(s)
    history.execute(s.apply_tarpaulin_defaults)
    assert_profile(s.snapshot())
    assert len(history.undo_steps) == 1
    history.undo()
    assert s.snapshot() == before_apply and not history.dirty
    history.redo()
    assert_profile(s.snapshot())


def test_reselection_and_reopen_preserve_manual_changes(furniture_session, package, tmp_path):
    s = furniture_session
    s.set_paper('4X8FT')
    style = copy.deepcopy(s.snapshot().text_styles['map_title'])
    style['size'] = 93
    s.set_text('map_title', 'Custom title', style)
    s.set_layout_setting('scale_font_size', 47)
    s.design.options.update(grid_font_size=24, pdf_dpi=150)
    s.design.map_styles['map_inset']['pin_size_mm'] = 12
    s.set_paper('4X8FT')
    result = s.snapshot()
    result.package_dir = package
    path = tmp_path / 'editor' / 'custom.mapedit.json'
    save_design(result, path)
    restored = EditorSession(load_design(path), s.renderer)
    actual = restored.snapshot()
    assert actual.text_styles['map_title']['size'] == 93
    assert actual.layout_settings['scale_font_size'] == 47
    assert actual.map_styles['map_inset']['pin_size_mm'] == 12
    assert actual.options['grid_font_size'] == 24 and actual.options['pdf_dpi'] == 150


def test_style_and_crs_updates_do_not_reapply_profile(furniture_session):
    s = furniture_session
    s.set_paper('4X8FT')
    s.design.options['grid_font_size'] = 22
    s.set_layout_setting('info_font_size', 49)
    s.set_inset_crs('map_inset', 'project')
    assert s.design.options['grid_font_size'] == 22
    assert s.design.layout_settings['info_font_size'] == 49


def test_apply_failure_rolls_back_all_sizes_and_history(furniture_session):
    s = furniture_session
    s.set_paper('4X8FT')
    s.design.options['grid_font_size'] = 19
    before = s.snapshot()
    history = DesignHistory(s)
    original = s.renderer.apply_layout_settings.side_effect
    def fail(ctx, settings):
        original(ctx, settings)
        raise RuntimeError('controlled profile failure')
    s.renderer.apply_layout_settings.side_effect = fail
    with pytest.raises(RuntimeError, match='controlled profile failure'):
        history.execute(s.apply_tarpaulin_defaults)
    assert s.snapshot() == before and not history.dirty and not history.undo_steps


def test_export_snapshot_gets_exact_requested_sizes(package, tmp_path, furniture_session):
    s = furniture_session
    s.set_paper('4X8FT')
    design = s.snapshot()
    design.package_dir = package
    attempt = create_export_attempt(design, tmp_path / 'editor')
    renderer = Mock()
    renderer._export_print_outputs.return_value = {}
    run_snapshot_export(attempt / 'snapshot.mapedit.json', renderer)
    args = renderer.build_qgis_print_layout.call_args.kwargs
    assert args['text_styles'] == design.text_styles
    assert args['layout_settings'] == design.layout_settings
    assert args['map_styles'] == design.map_styles
    assert args['render_options']['grid_font_size'] == 30
    assert args['map_states'] == design.maps


def test_page_button_applies_profile_through_history(window, furniture_session):
    s = furniture_session
    s.set_paper('4X8FT')
    s.design.options['grid_font_size'] = 18
    window.session, window.history = s, DesignHistory(s)
    window.refresh_controls, window.update_state = Mock(), Mock()
    window.apply_tarpaulin_defaults()
    assert_profile(s.snapshot())
    assert len(window.history.undo_steps) == 1


def test_profile_requires_resolved_fonts(package):
    with pytest.raises(ValueError, match='Resolve template fonts'):
        with_tarpaulin_defaults(EditorDesign(package, {'paper_size': '4X8FT'}))
