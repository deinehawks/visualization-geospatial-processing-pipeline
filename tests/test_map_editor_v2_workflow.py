"""Pure metadata/history checks: all filesystem changes stay under tmp_path."""
import copy
import json
from pathlib import Path

import pytest

from modules.map_export.editor_model import (
    EditorDesign, MAP_IDS, design_from_package, design_payload, load_design, save_design,
    validate_style, create_export_attempt,
)
from modules.map_export.editor_workflow import (
    DesignHistory, RecoveryStore, load_presets, location_preset, save_preset,
    style_preset, validate_preset,
)


@pytest.fixture
def design(tmp_path):
    package = tmp_path / 'source'
    package.mkdir()
    for name in ('boundaries.geojson', 'merged_boundary.geojson', 'map_metadata.json'):
        (package / name).write_text('{}')
    return EditorDesign(package, texts={'map_title': 'Original'}).validate()


class Session:
    def __init__(self, design):
        self.design = copy.deepcopy(design)
        self.restores = []

    def snapshot(self):
        return copy.deepcopy(self.design).validate()

    def restore(self, design, *, force=False):
        self.restores.append(force)
        self.design = copy.deepcopy(design)


def test_history_groups_dirty_save_undo_and_redo(design):
    session = Session(design)
    history = DesignHistory(session)
    history.begin_group()
    for text in ('N', 'Ne', 'New'):
        history.execute(lambda text=text: session.design.texts.update(map_title=text))
    assert history.dirty and history.undo_steps == []
    history.end_group()
    assert len(history.undo_steps) == 1
    history.mark_saved()
    assert not history.dirty
    history.undo()
    assert session.design.texts['map_title'] == 'Original' and history.dirty
    history.redo()
    assert session.design.texts['map_title'] == 'New' and not history.dirty


def test_history_caps_at_100_and_new_edit_invalidates_redo(design):
    session = Session(design)
    history = DesignHistory(session)
    for index in range(110):
        history.execute(lambda index=index: session.design.texts.update(map_title=str(index)))
    assert len(history.undo_steps) == 100
    history.undo()
    assert history.redo_steps
    history.execute(lambda: session.design.texts.update(map_title='Changed branch'))
    assert not history.redo_steps


def test_failed_group_rolls_back_without_an_undo_step(design):
    session = Session(design)
    history = DesignHistory(session)
    history.begin_group()
    history.execute(lambda: session.design.texts.update(map_title='Partial'))
    def fail():
        session.design.texts['map_title'] = 'Bad'
        raise RuntimeError('Controlled edit failure')
    with pytest.raises(RuntimeError, match='Controlled'):
        history.execute(fail)
    assert session.snapshot() == design
    assert not history.dirty and not history.undo_steps
    assert history.group_start is None and session.restores == [True]


def test_failed_history_restore_keeps_history_and_dirty_state(design):
    session = Session(design)
    history = DesignHistory(session)
    history.execute(lambda: session.design.texts.update(map_title='Changed'))
    session.restore = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('Cannot restore'))
    with pytest.raises(RuntimeError, match='Cannot restore'):
        history.undo()
    assert len(history.undo_steps) == 1 and not history.redo_steps and history.dirty


def test_v1_open_preserves_appearance_and_requires_separate_save(design, tmp_path):
    path = tmp_path / 'old.mapedit.json'
    payload = design_payload(design, tmp_path)
    payload['version'] = 1
    payload.pop('map_styles')
    payload.pop('grid_style')
    payload['options'].pop('overview_crs')
    payload['maps'] = {'map_inset_2': {'crs': 'EPSG:32651', 'extent': [1, 2, 101, 62]}}
    path.write_text(json.dumps(payload))
    original = path.read_bytes()
    opened = load_design(path)
    assert opened.source_version == 1 and opened.options['overview_crs'] == 'project'
    assert opened.map_styles['map_frame']['pin_size_mm'] == 4
    assert opened.grid_style['opacity'] == 0.6
    with pytest.raises(ValueError, match='separate V2'):
        save_design(opened, path)
    output = tmp_path / 'design.v2.mapedit.json'
    opened.map_styles['map_inset']['pin_color'] = '#112233'
    save_design(opened, output)
    assert path.read_bytes() == original
    assert json.loads(output.read_text())['version'] == 2
    assert load_design(output) == opened


@pytest.mark.parametrize(('values', 'grid'), (
    ({'basemap_opacity': 1.1}, False), ({'pin_size_mm': 0}, False),
    ({'pin_size_mm': 21}, False), ({'boundary_width_mm': 6}, False),
    ({'boundary_fill': 'red'}, False), ({'pin_color': '#fff'}, False),
    ({'width_mm': 0.01}, True), ({'width_mm': 3}, True),
    ({'opacity': float('nan')}, True), ({'lines_visible': 1}, True),
    ({'unknown': 1}, True), ({'boundary_opacity': True}, False),
))
def test_style_validation_rejects_invalid_values(values, grid):
    with pytest.raises(ValueError):
        validate_style(values, grid=grid)


def test_styles_do_not_alias_other_maps_or_defaults(design):
    original = copy.deepcopy(design.map_styles)
    design.map_styles['map_inset']['basemap_opacity'] = 0.2
    assert design.map_styles['map_frame'] == original['map_frame']
    assert EditorDesign(design.package_dir).validate().map_styles == original


def test_presets_exclude_survey_content_and_are_atomic_and_typed(design, tmp_path):
    design.maps = {'map_frame': {'crs': 'EPSG:32651', 'extent': [1, 2, 101, 62]}}
    design.texts['map_title'] = 'Private survey title'
    preset = style_preset('Clean look', design)
    serialized = json.dumps(preset)
    assert str(design.package_dir) not in serialized and 'Private survey title' not in serialized
    assert 'extent' not in serialized and 'bounds' not in serialized
    directory = tmp_path / 'work' / 'presets'
    path = save_preset(directory, preset, design.package_dir)
    presets, warnings = load_presets(directory)
    assert len(presets) == 4 and not warnings and presets[-1] == preset
    assert not list(directory.glob('*.tmp'))
    assert json.loads(path.read_text()) == preset
    preset['options']['overview_bounds'] = {}
    with pytest.raises(ValueError, match='framing'):
        validate_preset(preset)
    with pytest.raises(ValueError):
        save_preset(design.package_dir, presets[-1], design.package_dir)


def test_bad_preset_reports_warning_and_keeps_builtins(tmp_path):
    (tmp_path / 'bad.json').write_text('{')
    presets, warnings = load_presets(tmp_path)
    assert [item['name'] for item in presets] == ['Davao City', 'Kapalong, Davao del Norte', 'Philippines']
    assert warnings and 'bad.json' in warnings[0]
    value = location_preset('City', '1,2,3,4')
    value['package'] = 'not-allowed'
    with pytest.raises(ValueError):
        validate_preset(value)


def test_recovery_is_per_design_and_session_and_never_overwrites_named_design(design, tmp_path):
    workspace = tmp_path / 'work'
    named = tmp_path / 'named.mapedit.json'
    save_design(design, named)
    original = named.read_bytes()
    one, two = [RecoveryStore(workspace, design.package_dir, named) for _ in range(2)]
    assert one.path != two.path and one.key == two.key
    assert RecoveryStore(workspace, design.package_dir, tmp_path / 'other.json').key != one.key
    changed = copy.deepcopy(design)
    changed.texts['map_title'] = 'Recovered'
    one.write(changed)
    candidates, warnings = two.candidates()
    assert not warnings and candidates[0][1] == one.path
    assert design_payload(two.read(one.path), tmp_path) == design_payload(changed, tmp_path)
    two.resolve(one.path)
    assert one.path.exists() and json.loads(one.path.read_text())['status'] == 'resolved'
    assert two.candidates() == ([], [])
    assert named.read_bytes() == original
    assert not list(one.directory.glob('*.tmp'))


def test_recovery_rejects_foreign_paths_and_tampered_references(design, tmp_path):
    store = RecoveryStore(tmp_path / 'work', design.package_dir)
    store.write(design)
    with pytest.raises(ValueError, match='outside'):
        store.resolve(tmp_path / 'unowned.json')
    data = json.loads(store.path.read_text())
    data['design']['package'] = str(tmp_path / 'other-package')
    store.path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='another package'):
        store.read(store.path)


def test_recovery_and_presets_refuse_source_and_immutable_attempt(design, tmp_path):
    with pytest.raises(ValueError):
        RecoveryStore(design.package_dir, design.package_dir)
    attempt = create_export_attempt(design, tmp_path / 'exports')
    store = RecoveryStore(attempt, design.package_dir)
    with pytest.raises(ValueError, match='Export attempts'):
        store.write(design)
    with pytest.raises(ValueError, match='Export attempts'):
        save_preset(attempt, location_preset('Test', '1,2,3,4'), design.package_dir)


def test_bad_recovery_is_reported_and_missing_input_is_explicit(design, tmp_path):
    one = RecoveryStore(tmp_path / 'work', design.package_dir)
    one.write(design)
    (one.directory / 'bad.json').write_text('{')
    two = RecoveryStore(tmp_path / 'work', design.package_dir)
    candidates, warnings = two.candidates()
    assert candidates and warnings
    data = json.loads(one.path.read_text())
    data['design']['options']['pdf_dpi'] = 0
    one.path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        two.read(one.path)


def test_v2_metadata_restores_styles_from_export_state(design):
    style = copy.deepcopy(design.map_styles)
    style['map_inset_2']['pin_size_mm'] = 9
    metadata = {'print_layout': {'editor_state': {'map_styles': style, 'grid_style': {'lines_visible': False}}}}
    (design.package_dir / 'map_metadata.json').write_text(json.dumps(metadata))
    opened = design_from_package(design.package_dir)
    assert opened.map_styles['map_inset_2']['pin_size_mm'] == 9
    assert not opened.grid_style['lines_visible']
