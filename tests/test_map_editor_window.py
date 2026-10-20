"""Exercise window event wiring with Qt replaced; no QApplication or GUI is started."""
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

from modules.map_export.editor_window import create_window_class


@pytest.fixture
def window(monkeypatch):
    class Widget:
        def __init__(self, *_args):
            pass
        def __getattr__(self, key):
            value = Mock()
            setattr(self, key, value)
            return value

    widgets = ModuleType('qgis.PyQt.QtWidgets')
    for name in ('QMainWindow', 'QWidget', 'QSplitter', 'QVBoxLayout', 'QFormLayout',
                 'QHBoxLayout', 'QPushButton', 'QComboBox', 'QDoubleSpinBox', 'QSpinBox',
                 'QPlainTextEdit', 'QLineEdit', 'QLabel', 'QFontComboBox', 'QCheckBox',
                 'QColorDialog', 'QFileDialog', 'QScrollArea', 'QGroupBox'):
        setattr(widgets, name, Widget)
    widgets.QMessageBox = SimpleNamespace(
        Save=1, Discard=2, Cancel=4, Yes=8, question=Mock(return_value=4),
        critical=Mock(), warning=Mock(),
    )
    core = ModuleType('qgis.PyQt.QtCore')
    core.Qt = SimpleNamespace(LeftButton=1)
    core.QTimer = Widget
    core.QProcess = Widget
    core.QProcess.MergedChannels = 0
    core.QProcessEnvironment = SimpleNamespace(systemEnvironment=Mock(return_value=Mock()))
    gui = ModuleType('qgis.PyQt.QtGui')
    gui.QColor = Widget
    gui.QFont = Widget
    qgis_gui = ModuleType('qgis.gui')
    qgis_gui.QgsLayoutView = Widget
    for name, module in [('qgis.PyQt.QtWidgets', widgets), ('qgis.PyQt.QtCore', core),
                         ('qgis.PyQt.QtGui', gui), ('qgis.gui', qgis_gui)]:
        monkeypatch.setitem(sys.modules, name, module)
    result = create_window_class()()
    result.qt_messages = widgets.QMessageBox
    result.qt_process_environment = core.QProcessEnvironment.systemEnvironment.return_value
    return result


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
    window.session = Mock()
    window.map_choice.currentIndex.return_value = 1
    window.refresh_controls = Mock()
    window.session._map.return_value.sceneBoundingRect.return_value.contains.return_value = True
    event = Mock()
    event.angleDelta.return_value.y.return_value = 120
    window.view.wheelEvent(event)
    window.session.zoom.assert_called_once_with('map_inset', 1 / 1.2)
    assert window.dirty
    event.accept.assert_called_once_with()


def test_drag_coalesces_movement_and_only_pans_selected_map(window):
    window.session = Mock()
    window.map_choice.currentIndex.return_value = 2
    window.refresh_controls = Mock()
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
    window.session = Mock()
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
    window.session = Mock()
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
    window.session = Mock()
    window.refresh_controls = Mock()
    window.overview_crs.currentData.return_value = 'EPSG:4326'
    window.change_overview_crs()
    window.session.set_overview_crs.assert_called_once_with('EPSG:4326')
    window.view.setCurrentLayout.assert_called_once_with(window.session.ctx.layout)
    assert window.session._previous_ctx is None
    assert window.dirty
    window.loading = True
    window.change_overview_crs()
    assert window.session.set_overview_crs.call_count == 1
