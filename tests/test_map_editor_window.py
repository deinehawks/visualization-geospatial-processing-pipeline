"""Exercise window event wiring with Qt replaced; no QApplication or GUI is started."""
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import sys
import copy
import json

import pytest

from modules.map_export.editor_window import create_window_class


@pytest.fixture
def window(monkeypatch, tmp_path):
    import modules.map_export.editor_generator_page as generator_page
    monkeypatch.setattr(generator_page, 'REPO_ROOT', tmp_path)
    class Widget:
        def __init__(self, *_args):
            self.currentIndex = Mock(return_value=0)
            self.setCurrentIndex = Mock(side_effect=lambda value: setattr(self.currentIndex, 'return_value', value))
            self.isActive = Mock(return_value=False)
            self.addToolBar = Mock(side_effect=lambda *_: Widget())
            self.addAction = Mock(side_effect=lambda *_: Mock())
            self.currentData = Mock(return_value=None)
            self.currentRow = Mock(return_value=-1)
            self.setCurrentRow = Mock(side_effect=lambda value: setattr(self.currentRow, 'return_value', value))
            self.toPlainText = Mock(return_value='')
            self.text = Mock(return_value='')
        def __getattr__(self, key):
            value = Mock()
            setattr(self, key, value)
            return value

        def drawForeground(self, *_args):
            pass

    widgets = ModuleType('qgis.PyQt.QtWidgets')
    for name in ('QMainWindow', 'QWidget', 'QSplitter', 'QVBoxLayout', 'QFormLayout',
                 'QHBoxLayout', 'QPushButton', 'QComboBox', 'QDoubleSpinBox', 'QSpinBox',
                 'QPlainTextEdit', 'QLineEdit', 'QLabel', 'QFontComboBox', 'QCheckBox',
                 'QColorDialog', 'QFileDialog', 'QScrollArea', 'QGroupBox',
                 'QTabWidget', 'QListWidget', 'QProgressBar', 'QInputDialog'):
        setattr(widgets, name, Widget)
    widgets.QMessageBox = SimpleNamespace(
        Save=1, Discard=2, Cancel=4, Yes=8, No=16, question=Mock(return_value=4),
        critical=Mock(), warning=Mock(),
    )
    core = ModuleType('qgis.PyQt.QtCore')
    core.Qt = SimpleNamespace(LeftButton=1, NoBrush=0)
    core.QUrl = SimpleNamespace(fromLocalFile=Mock())
    core.QTimer = Widget
    core.QTimer.singleShot = Mock()
    core.QProcess = Widget
    core.QProcess.MergedChannels = 0
    core.QProcessEnvironment = SimpleNamespace(systemEnvironment=Mock(return_value=Mock()))
    gui = ModuleType('qgis.PyQt.QtGui')
    gui.QColor = Widget
    gui.QFont = Widget
    gui.QPen = Widget
    gui.QDesktopServices = SimpleNamespace(openUrl=Mock(return_value=True))
    qgis_gui = ModuleType('qgis.gui')
    qgis_gui.QgsLayoutView = Widget
    for name, module in [('qgis.PyQt.QtWidgets', widgets), ('qgis.PyQt.QtCore', core),
                         ('qgis.PyQt.QtGui', gui), ('qgis.gui', qgis_gui)]:
        monkeypatch.setitem(sys.modules, name, module)
    result = create_window_class()()
    result.qt_messages = widgets.QMessageBox
    result.qt_process_environment = core.QProcessEnvironment.systemEnvironment.return_value
    return result


def fake_session():
    session = Mock()
    session._previous_ctx = None
    session._map.return_value.scale.return_value = 1000
    session._map.return_value.crs.return_value.authid.return_value = 'EPSG:32651'
    return session


def test_unsaved_cancel_discard_and_save_paths(window):
    window.dirty = True
    assert not window.confirm_discard()
    window.qt_messages.question.return_value = window.qt_messages.Discard
    assert window.confirm_discard()
    window.qt_messages.question.return_value = window.qt_messages.Save
    window.save = Mock(return_value=False)
    assert not window.confirm_discard()
    window.save.return_value = True
    assert window.confirm_discard()


def test_mouse_wheel_zooms_selected_map_without_moving_frames(window):
    window.session = fake_session()
    window.map_choice.currentIndex.return_value = 1
    window.refresh_controls = Mock()
    window.hit_item = Mock(return_value='map_inset')
    window.session._map.return_value.sceneBoundingRect.return_value.contains.return_value = True
    event = Mock()
    event.angleDelta.return_value.y.return_value = 120
    window.view.wheelEvent(event)
    window.session.zoom.assert_called_once_with('map_inset', 1 / 1.2)
    assert window.dirty
    event.accept.assert_called_once_with()


def test_drag_coalesces_movement_and_only_pans_selected_map(window):
    window.session = fake_session()
    window.map_choice.currentIndex.return_value = 2
    window.refresh_controls = Mock()
    window.hit_item = Mock(return_value='map_inset_2')
    item = window.session._map.return_value
    item.sceneBoundingRect.return_value.contains.return_value = True
    item.rect.return_value.width.return_value = 100
    item.rect.return_value.height.return_value = 50
    window.view.pan_timer.isActive.return_value = False
    points = [SimpleNamespace(x=lambda: 0, y=lambda: 0),
              SimpleNamespace(x=lambda: 10, y=lambda: 5),
              SimpleNamespace(x=lambda: 20, y=lambda: 10)]
    window.view.mapToScene.side_effect = points
    event = Mock()
    event.button.return_value = 1
    window.view.mousePressEvent(event)
    window.view.mouseMoveEvent(event)
    window.view.mouseMoveEvent(event)
    window.session.pan.assert_not_called()
    window.view.mouseReleaseEvent(event)
    window.session.pan.assert_called_once_with('map_inset_2', 0.2, 0.2)
    assert window.view.anchor is None


def test_fixed_layout_rejects_delete_and_context_menu(window):
    event = Mock()
    window.view.keyPressEvent(event)
    event.ignore.assert_called_once_with()
    window.view.contextMenuEvent(event)
    event.accept.assert_called_once_with()


def test_live_text_controls_pass_text_and_style_to_session(window):
    window.session = fake_session()
    window.text_choice.currentIndex.return_value = 0
    window.text_value.toPlainText.return_value = 'Live title'
    window.font_family.currentFont.return_value.family.return_value = 'Arial'
    window.font_size.value.return_value = 24
    window.bold.isChecked.return_value = True
    window.font_color = '#123456'
    window.change_text()
    window.session.set_text.assert_called_once_with(
        'map_title', 'Live title', {'family': 'Arial', 'size': 24, 'bold': True, 'color': '#123456'},
    )
    assert window.dirty


def test_close_cancels_only_owned_export_and_waits_for_it(window):
    window.job.process = Mock()
    window.qt_messages.question.return_value = window.qt_messages.Yes
    window.cancel_export = Mock()
    event = Mock()
    window.closeEvent(event)
    window.cancel_export.assert_called_once_with()
    event.ignore.assert_called_once_with()
    event.accept.assert_not_called()


def test_coordinate_font_size_control_updates_live_session(window):
    window.session = fake_session()
    window.refresh_controls = Mock()
    window.change_grid_font_size(8.5)
    window.session.set_grid_font_size.assert_called_once_with(8.5)
    assert window.dirty
    window.loading = True
    window.change_grid_font_size(12)
    assert window.session.set_grid_font_size.call_count == 1


def test_export_process_applies_native_backend_setting(window, monkeypatch):
    import modules.map_export.editor_window as editor_window
    monkeypatch.setattr(editor_window, 'export_qt_platform', lambda _platform: 'windows')
    process = window._new_process()
    window.qt_process_environment.insert.assert_called_once_with('QT_QPA_PLATFORM', 'windows')
    window.qt_process_environment.remove.assert_called_once_with('VIRTUAL_ENV')
    process.setProcessEnvironment.assert_called_once_with(window.qt_process_environment)


def test_overview_crs_control_rebuilds_and_attaches_new_layout(window):
    window.session = fake_session()
    window.refresh_controls = Mock()
    window.overview_crs.currentData.return_value = 'EPSG:4326'
    window.map_choice.currentIndex.return_value = 2
    window.session._previous_ctx = Mock()
    window.change_overview_crs()
    window.session.set_inset_crs.assert_called_once_with('map_inset_2', 'EPSG:4326')
    window.view.setCurrentLayout.assert_called_once_with(window.session.ctx.layout)
    assert window.session._previous_ctx is None
    assert window.dirty
    window.loading = True
    window.change_overview_crs()
    assert window.session.set_inset_crs.call_count == 1


def history_session(window, tmp_path):
    from modules.map_export.editor_model import EditorDesign
    from modules.map_export.editor_workflow import DesignHistory
    session = fake_session()
    session.design = EditorDesign(tmp_path / 'source', texts={'map_title': 'Original'}).validate()
    session.snapshot.side_effect = lambda: copy.deepcopy(session.design)
    session.restore.side_effect = lambda design, **_kwargs: setattr(session, 'design', copy.deepcopy(design))
    session.set_text.side_effect = lambda key, value, style: (
        session.design.texts.update({key: value}), session.design.text_styles.update({key: style}))
    window.session = session
    window.history = DesignHistory(session)
    window.refresh_controls = Mock()
    return session


def test_v2_empty_state_disables_edit_and_export_actions(window):
    for key in ('save', 'save_as', 'undo', 'redo', 'export', 'cancel', 'output_pdf'):
        window.actions[key].setEnabled.assert_called_with(False)
    window.tabs.setEnabled.assert_called_with(False)
    assert window.autosave_timer.setInterval.call_args.args == (30000,)
    assert window.text_timer.setInterval.call_args.args == (750,)


def test_click_selects_label_and_opens_text_tab_without_dirtying_design(window):
    window.session = fake_session()
    window.refresh_controls = Mock()
    window.hit_item = Mock(return_value='map_title')
    event = Mock()
    event.button.return_value = 1
    window.view.mousePressEvent(event)
    window.text_choice.setCurrentIndex.assert_called_with(0)
    window.tabs.setCurrentIndex.assert_called_with(1)
    assert window.selected_id == 'map_title'
    assert not window.dirty and window.view.anchor is None


def test_selection_outline_is_view_only_and_does_not_change_layout(window):
    window.session = fake_session()
    painter = Mock()
    window.view.drawForeground(painter, Mock())
    painter.drawRect.assert_called_once_with(
        window.session.ctx.layout.itemById.return_value.sceneBoundingRect.return_value)
    window.session.ctx.layout.addLayoutItem.assert_not_called()


def test_page_zoom_and_fit_never_change_map_extent_or_dirty_state(window):
    window.session = fake_session()
    window.zoom_page(1.2)
    window.fit_page()
    window.view.scale.assert_called_once_with(1.2, 1.2)
    window.view.zoomFull.assert_called_once_with()
    window.session.set_extent.assert_not_called()
    assert not window.dirty


def test_text_timer_groups_typing_into_one_undo_and_tracks_saved_state(window, tmp_path):
    session = history_session(window, tmp_path)
    window.font_family.currentFont.return_value.family.return_value = 'Arial'
    window.font_size.value.return_value = 18
    window.bold.isChecked.return_value = False
    for value in ('N', 'Ne', 'New'):
        window.text_value.toPlainText.return_value = value
        window.change_text()
    assert not window.history.undo_steps and window.dirty
    window.finish_text_group()
    assert len(window.history.undo_steps) == 1
    window.undo()
    assert session.design.texts['map_title'] == 'Original' and not window.dirty
    window.redo()
    assert session.design.texts['map_title'] == 'New' and window.dirty


def test_entire_drag_makes_one_history_step_and_only_edits_hit_map(window, tmp_path):
    session = history_session(window, tmp_path)
    session.pan.side_effect = lambda key, *_args: session.design.texts.update(map_title=key)
    window.hit_item = Mock(return_value='map_inset')
    session._map.return_value.rect.return_value.width.return_value = 100
    session._map.return_value.rect.return_value.height.return_value = 50
    points = [SimpleNamespace(x=lambda: 0, y=lambda: 0), SimpleNamespace(x=lambda: 10, y=lambda: 5)]
    window.view.mapToScene.side_effect = points
    event = Mock()
    event.button.return_value = 1
    window.view.mousePressEvent(event)
    window.view.mouseMoveEvent(event)
    window.view.flush_pan()
    window.view.pending = [0.1, 0.1]
    window.view.mouseReleaseEvent(event)
    assert session.pan.call_count == 2
    assert all(call.args[0] == 'map_inset' for call in session.pan.call_args_list)
    assert len(window.history.undo_steps) == 1


def test_autosave_only_writes_recovery_when_dirty(window, tmp_path):
    session = history_session(window, tmp_path)
    window.recovery = Mock()
    window.autosave()
    window.recovery.write.assert_not_called()
    window.perform(lambda: session.design.texts.update(map_title='Changed'))
    window.autosave()
    assert window.recovery.write.call_args.args[0].texts['map_title'] == 'Changed'
    window.recovery.write.side_effect = OSError('Read-only workspace')
    window.autosave()
    assert window.dirty
    assert 'Read-only workspace' in window.log.appendPlainText.call_args.args[0]


def test_undo_back_to_saved_resolves_stale_recovery(window, tmp_path):
    session = history_session(window, tmp_path)
    window.recovery = Mock()
    window.perform(lambda: session.design.texts.update(map_title='Changed'))
    window.autosave()
    window.recovery.resolve.reset_mock()
    window.undo()
    assert not window.dirty
    window.recovery.resolve.assert_called()


@pytest.mark.parametrize('recover', (True, False))
def test_recovery_requires_consent_and_dismiss_preserves_file_evidence(window, tmp_path, recover):
    session = history_session(window, tmp_path)
    window.recovery = Mock()
    path = tmp_path / 'recovery.json'
    window.recovery.candidates.return_value = ([('2026-10-20T00:00:00+00:00', path)], [])
    restored = session.snapshot()
    restored.texts['map_title'] = 'Recovered'
    window.recovery.read.return_value = restored
    window.qt_messages.question.return_value = window.qt_messages.Yes if recover else window.qt_messages.No
    window.offer_recovery()
    if recover:
        assert session.design.texts['map_title'] == 'Recovered' and window.dirty
        window.recovery.resolve.assert_not_called()
    else:
        window.recovery.read.assert_not_called()
        assert not window.dirty
        assert window.recovery.resolve.call_args_list[0].args == (path,)


def test_failed_edit_rolls_back_and_does_not_add_history(window, tmp_path):
    session = history_session(window, tmp_path)
    def fail():
        session.design.texts['map_title'] = 'Partial'
        raise RuntimeError('Cannot render')
    assert not window.perform(fail)
    assert session.design.texts['map_title'] == 'Original'
    assert not window.history.undo_steps and not window.dirty
    window.qt_messages.critical.assert_called_once()


def test_saved_v1_design_gets_separate_v2_copy(window, tmp_path):
    from modules.map_export.editor_model import design_payload
    session = history_session(window, tmp_path)
    session.design.source_version = 1
    original = tmp_path / 'original.mapedit.json'
    data = design_payload(session.design, tmp_path)
    data['version'] = 1
    original.write_text(json.dumps(data))
    old_bytes = original.read_bytes()
    output = tmp_path / 'design.v2.mapedit.json'
    window.design_path = original
    window.workspace = Mock(return_value=tmp_path / 'work')
    widgets = sys.modules['qgis.PyQt.QtWidgets']
    widgets.QFileDialog.getSaveFileName = Mock(return_value=(str(output), ''))
    assert window.save()
    assert original.read_bytes() == old_bytes
    assert json.loads(output.read_text())['version'] == 2
    assert window.design_path == output and not window.dirty


def test_v2_package_open_prefers_saved_v2_then_v1(window, tmp_path, monkeypatch):
    import modules.map_export.editor_window as module
    package = tmp_path / 'maps' / 'example'
    package.mkdir(parents=True)
    workspace = tmp_path / 'exports/map_editor/example'
    workspace.mkdir(parents=True)
    v1, v2 = workspace / 'design.mapedit.json', workspace / 'design.v2.mapedit.json'
    v1.write_text('v1')
    v2.write_text('v2')
    monkeypatch.setattr(module, 'REPO_ROOT', tmp_path)
    loader = Mock(return_value=SimpleNamespace(package_dir=package))
    monkeypatch.setattr(module, 'load_design', loader)
    window.install_design = Mock()
    window.load_package(package)
    loader.assert_called_once_with(v2)


def test_export_busy_actions_and_confirmed_output_opening(window, tmp_path):
    window.session = fake_session()
    window.job.process = Mock()
    window.update_actions()
    window.actions['export'].setEnabled.assert_called_with(False)
    window.actions['cancel'].setEnabled.assert_called_with(True)
    window.export_busy.setRange.assert_called_with(0, 0)
    window.job.process = None
    pdf = tmp_path / 'print_map.pdf'
    pdf.write_bytes(b'test only')
    window.export_outputs = {'pdf': pdf}
    window.update_actions()
    window.open_output('pdf')
    gui = sys.modules['qgis.PyQt.QtGui']
    gui.QDesktopServices.openUrl.assert_called_once()
    window.actions['output_pdf'].setEnabled.assert_called_with(True)


def test_generator_starts_on_own_page_without_discovery(window):
    assert window.pages.currentIndex() == 0
    assert window.generator.inputs == []
    assert not window.generator.job.busy
    assert window.helper_gate.owner is None
    window.generator.open_generated.setEnabled.assert_called_with(False)


def test_generator_add_remove_reorder_and_invalidate(window):
    page = window.generator
    page.add_query('AH-026132')
    page.add_query('AH-026128')
    assert [r['query'] for r in page.inputs] == ['AH-026132', 'AH-026128']
    page.surveys.currentRow.return_value = 1
    page.move(-1)
    assert [r['query'] for r in page.inputs] == ['AH-026128', 'AH-026132']
    page.findings = [{'matches': []}]
    page.inputs[0].update(folder='old', boundary='old.kml', orthomosaic='old.tif')
    page.invalidate()
    assert not page.findings and not page.inputs[0].get('folder')
    page.surveys.currentRow.return_value = 0
    page.remove()
    assert [r['query'] for r in page.inputs] == ['AH-026132']
    assert not page.inputs[0].get('boundary')


def test_generator_controls_and_export_share_helper_slot(window):
    page = window.generator
    window.session = fake_session()
    window.helper_gate.acquire(page.job)
    window.update_actions()
    window.actions['export'].setEnabled.assert_called_with(False)
    page.check.setEnabled.assert_called_with(False)
    page.generate.setEnabled.assert_called_with(False)
    window.helper_gate.release(page.job)
    window.update_actions()
    window.actions['export'].setEnabled.assert_called_with(True)
    page.generate.setEnabled.assert_called_with(True)


def test_generator_captures_exact_selections_and_does_not_load_on_start(window, tmp_path):
    page = window.generator
    page.inputs = [{'query': 'AH-026132', 'folder': 'chosen-folder', 'boundary': 'chosen.kml', 'orthomosaic': 'chosen-t2.tif'}]
    page.findings = [{'matches': []}]
    page.roots.toPlainText.return_value = 'first-root\nsecond-root'
    page.map_name.text.return_value = 'chosen-map'
    page.title.text.return_value = 'Chosen title'
    page.location.text.return_value = 'Chosen location'
    page.output.text.return_value = str(tmp_path / 'maps')
    page.map_type.currentData.return_value = 'orthomosaic'
    page.job.start = Mock(return_value=tmp_path / 'attempt')
    window.load_package = Mock()
    page.start('generate')
    captured = page.job.start.call_args.args[0]
    assert captured['inputs'] == page.inputs and captured['roots'] == ['first-root', 'second-root']
    assert captured['map_name'] == 'chosen-map' and captured['title'] == 'Chosen title'
    assert captured['location'] == 'Chosen location' and captured['output_root'] == str(tmp_path / 'maps')
    window.load_package.assert_not_called()
    assert json.loads((page.workspace / 'roots.json').read_text())['roots'] == captured['roots']


def test_generator_requires_check_and_explicit_ambiguous_choice(window):
    page = window.generator
    page.inputs = [{'query': 'AH-026132'}]
    page.job.start = Mock()
    page.start('generate')
    page.job.start.assert_not_called()
    window.qt_messages.critical.assert_called_once()
    page.findings = [{'matches': [{'folder': 'first'}, {'folder': 'second'}]}]
    page.start('generate')
    page.job.start.assert_not_called()


def test_generator_selection_preserves_overrides_when_revisiting_row(window):
    page = window.generator
    match = {'folder': 'chosen', 'boundaries': ['b1', 'b2'], 'orthomosaics': ['t4', 't2'], 'warnings': []}
    page.inputs = [{'query': 'AH-026132', 'folder': 'chosen', 'boundary': 'b2', 'orthomosaic': 't2'}]
    page.surveys.currentRow.return_value = 0
    page.folder_choice.currentData.return_value = match
    page.boundary_choice.currentData.return_value = 'b2'
    page.raster_choice.currentData.return_value = 't2'
    page.choose_folder()
    page.boundary_choice.setCurrentIndex.assert_called_with(1)
    page.raster_choice.setCurrentIndex.assert_called_with(1)
    assert page.inputs[0]['orthomosaic'] == 't2'
    page.paths.setPlainText.assert_called_with('Folder: chosen\nBoundary: b2\nTIFF: t2')


@pytest.mark.parametrize('status', ['failed', 'cancelled'])
def test_failed_or_cancelled_generation_never_hands_off(window, tmp_path, status):
    page = window.generator
    page.job.process = Mock()
    process = page.job.process
    process.readAllStandardOutput.return_value = b''
    page.job.attempt = tmp_path / 'attempt'
    page.job.finish = Mock(return_value={'status': status})
    window.load_package = Mock()
    page.finished(1)
    window.load_package.assert_not_called()
    assert page.generated_package is None
    process.deleteLater.assert_called_once()


def test_successful_generator_handoff_cancel_and_load_failure_preserve_package(window, tmp_path):
    page = window.generator
    package = tmp_path / 'generated'
    package.mkdir()
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    (attempt / 'result.json').write_text(json.dumps({'package': str(package)}))
    page.job.attempt = attempt
    page.job.request = {'operation': 'generate'}
    page.job.process = Mock()
    page.job.process.readAllStandardOutput.return_value = b''
    page.job.finish = Mock(return_value={'status': 'complete'})
    window.confirm_discard = Mock(return_value=False)
    window.load_package = Mock()
    original_session = window.session
    page.finished(0)
    assert page.generated_package == package and window.session is original_session
    window.load_package.assert_not_called()
    page.open_generated.setEnabled.assert_called_with(True)
    window.confirm_discard.return_value = True
    window.load_package.side_effect = RuntimeError('load failed')
    page.handoff()
    assert package.is_dir() and page.generated_package == package
    window.qt_messages.critical.assert_called_once()
    window.load_package.side_effect = None
    page.handoff()
    window.load_package.assert_called_with(package)


def test_generator_log_and_cancel_are_scoped_to_owned_process(window, tmp_path):
    page = window.generator
    page.job.attempt = tmp_path
    process = Mock()
    process.readAllStandardOutput.return_value = b'Checking inputs...\nCreating package...\n'
    page.job.process = process
    page.read_log()
    assert (tmp_path / 'generator.log').read_text() == 'Checking inputs...\nCreating package...\n'
    page.cancel()
    process.terminate.assert_called_once()
    assert page.job.cancel_requested
    assert window.job.process is None


def test_successful_design_install_switches_to_editor(window, tmp_path, monkeypatch):
    import modules.map_export.editor_window as module
    session = history_session(window, tmp_path)
    window.workspace = Mock(return_value=tmp_path / 'editor-workspace')
    window.refresh_controls = Mock()
    window.refresh_presets = Mock()
    window.offer_recovery = Mock()
    monkeypatch.setattr(module, 'EditorSession', Mock(return_value=session))
    window.install_design(session.snapshot())
    assert window.pages.currentIndex() == 1
    assert window.session is session and not window.dirty
