"""Lazy Qt window definition: importing this module never starts Qt or QGIS."""
from __future__ import annotations

from pathlib import Path
import math
import sys

from .editor_model import (
    MAP_IDS, TEXT_IDS, REPO_ROOT, apply_bat_settings, design_from_package,
    load_design, save_design,
)
from .editor_session import EditorSession, ExportJob, export_qt_platform
from .editor_generator import HelperGate
from .editor_generator_page import create_generator_page
from .editor_workflow import (
    DesignHistory, RecoveryStore, load_presets, location_preset, style_preset, save_preset,
)


def create_window_class():
    from qgis.PyQt.QtCore import Qt, QTimer, QProcess, QProcessEnvironment, QUrl
    from qgis.PyQt.QtGui import QColor, QFont, QPen, QDesktopServices
    from qgis.PyQt.QtWidgets import (
        QMainWindow, QWidget, QSplitter, QVBoxLayout, QFormLayout, QHBoxLayout,
        QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit,
        QLineEdit, QLabel, QFontComboBox, QCheckBox, QColorDialog, QFileDialog,
        QMessageBox, QScrollArea, QGroupBox,
        QTabWidget, QListWidget, QProgressBar, QInputDialog,
    )
    from qgis.gui import QgsLayoutView

    class MapView(QgsLayoutView):
        def __init__(self, owner):
            super().__init__(owner)
            self.owner = owner
            self.anchor = None
            self.drag_map = None
            self.pending = [0.0, 0.0]
            self.canvas_anchor = None
            self.canvas_button = None
            self.space_down = False
            self.pan_timer = QTimer(self)
            self.pan_timer.setSingleShot(True)
            self.pan_timer.setInterval(40)
            self.pan_timer.timeout.connect(self.flush_pan)

        def cancel_drag(self):
            self.pan_timer.stop()
            if self.anchor is not None and self.owner.history is not None:
                self.owner.history.end_group()
            self.anchor = None
            self.pending = [0.0, 0.0]
            self.canvas_anchor = self.canvas_button = None
            self.setCursor(Qt.OpenHandCursor if self.space_down else Qt.ArrowCursor)

        def flush_pan(self):
            dx, dy = self.pending
            self.pending = [0.0, 0.0]
            if dx or dy:
                self.owner.perform(lambda: self.owner.session.pan(self.drag_map, dx, dy), grouped=True)

        def _point(self, event):
            return self.mapToScene(event.pos())

        def mousePressEvent(self, event):
            if self.owner.session:
                self.setFocus()
                if event.button() == Qt.MiddleButton or (self.space_down and event.button() == Qt.LeftButton):
                    self.flush_pan()
                    self.cancel_drag()
                    self.canvas_anchor, self.canvas_button = event.pos(), event.button()
                    self.setCursor(Qt.ClosedHandCursor)
                    event.accept()
                    return
            if self.owner.session and event.button() == Qt.LeftButton:
                point = self._point(event)
                key = self.owner.hit_item(point)
                if key:
                    self.owner.select_item(key)
                if key in MAP_IDS:
                    self.anchor = point
                    self.drag_map = key
                    if self.owner.history is not None:
                        self.owner.history.begin_group()
            event.accept()

        def mouseMoveEvent(self, event):
            if self.canvas_anchor is not None:
                position = event.pos()
                dx, dy = position.x() - self.canvas_anchor.x(), position.y() - self.canvas_anchor.y()
                self.canvas_anchor = position
                horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
                horizontal.setValue(horizontal.value() - dx)
                vertical.setValue(vertical.value() - dy)
            elif self.anchor is not None:
                point = self._point(event)
                item = self.owner.session._map(self.drag_map)
                dx = (point.x() - self.anchor.x()) / item.rect().width()
                dy = (point.y() - self.anchor.y()) / item.rect().height()
                self.anchor = point
                self.pending[0] += dx
                self.pending[1] += dy
                if not self.pan_timer.isActive():
                    self.pan_timer.start()
            event.accept()

        def mouseReleaseEvent(self, event):
            if self.canvas_anchor is not None:
                if event.button() == self.canvas_button:
                    self.canvas_anchor = self.canvas_button = None
                    self.setCursor(Qt.OpenHandCursor if self.space_down else Qt.ArrowCursor)
                event.accept()
                return
            self.pan_timer.stop()
            self.flush_pan()
            self.anchor = None
            if self.owner.history is not None:
                self.owner.history.end_group()
                self.owner.update_state()
            event.accept()

        def wheelEvent(self, event):
            delta = event.angleDelta().y()
            if not delta:
                delta = event.pixelDelta().y()
            if not delta:
                event.accept()
                return
            self.flush_pan()
            self.cancel_drag()
            if self.owner.session:
                key = self.owner.hit_item(self._point(event))
                if self.space_down or event.modifiers() & Qt.ControlModifier or key not in MAP_IDS:
                    factor = 1.2 ** max(-4, min(4, delta / 120))
                    self.owner.zoom_page(factor, anchor=event.pos())
                else:
                    self.owner.select_item(key)
                    factor = 1 / 1.2 if delta > 0 else 1.2
                    self.owner.perform(lambda: self.owner.session.zoom(key, factor))
            event.accept()

        def drawForeground(self, painter, rectangle):
            super().drawForeground(painter, rectangle)
            if self.owner.session:
                item = self.owner.session.ctx.layout.itemById(self.owner.selected_id)
                if item is not None:
                    painter.save()
                    pen = QPen(QColor('#2563eb'))
                    pen.setWidthF(1.5)
                    pen.setCosmetic(True)
                    painter.setPen(pen)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(item.sceneBoundingRect())
                    painter.restore()

        def keyPressEvent(self, event):
            if event.key() == Qt.Key_Space and self.owner.session:
                self.space_down = True
                if self.canvas_anchor is None:
                    self.setCursor(Qt.OpenHandCursor)
                event.accept()
                return
            # The fixed-layout editor never forwards delete/cut/move-item shortcuts.
            event.ignore()

        def keyReleaseEvent(self, event):
            if event.key() == Qt.Key_Space:
                if event.isAutoRepeat():
                    event.accept()
                    return
                self.space_down = False
                if self.canvas_anchor is None:
                    self.setCursor(Qt.ArrowCursor)
                event.accept()
                return
            event.ignore()

        def focusOutEvent(self, event):
            self.space_down = False
            self.cancel_drag()
            super().focusOutEvent(event)

        def contextMenuEvent(self, event):
            event.accept()

    class MapEditorWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.session = None
            self.design_path = None
            self.dirty = False
            self.loading = False
            self.history = None
            self.recovery = None
            self.recovered_paths = []
            self.selected_id = 'map_frame'
            self.export_outputs = {}
            self.presets = []
            self.canvas_focus = False
            self.font_color = '#000000'
            self.helper_gate = HelperGate()
            self.job = ExportJob(self._new_process, self.helper_gate)
            self.resize(1500, 950)
            self.setWindowTitle('Orthomosaic Map Editor V2')
            self.setStyleSheet('''
                QWidget { color: #0f172a; }
                QMainWindow { background: #f1f5f9; }
                QScrollArea, QGroupBox { background: white; }
                QToolBar { background: white; border-bottom: 1px solid #dbe3ed; spacing: 6px; padding: 6px; }
                QGroupBox { border: 1px solid #dbe3ed; border-radius: 6px; margin-top: 14px; padding: 12px 8px 8px; }
                QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #334155; }
                QPushButton { padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 4px; background: white; color: #1e293b; }
                QPushButton:hover { background: #eff6ff; border-color: #93c5fd; }
                QPushButton:disabled { color: #94a3b8; background: #f8fafc; }
                QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; color: #0f172a; padding: 4px; }
                QTabWidget::pane { border: 1px solid #dbe3ed; background: white; }
                QTabBar::tab { padding: 8px 12px; background: #f1f5f9; }
                QTabBar::tab:selected { background: white; color: #2563eb; }
                QListWidget { background: white; border: 0; padding: 6px; }
                QListWidget::item { padding: 8px; }
                QListWidget::item:selected { background: #dbeafe; color: #1e40af; }
            ''')
            toolbar = self.addToolBar('Map Editor')
            self.actions = {}
            for key, label, shortcut, handler in (
                ('open', 'Open map', 'Ctrl+O', self.open_package),
                ('open_design', 'Open design', 'Ctrl+Shift+O', self.open_design),
                ('import', 'Import BAT', '', self.import_bat),
                ('save', 'Save', 'Ctrl+S', self.save),
                ('save_as', 'Save As', 'Ctrl+Shift+S', lambda: self.save(save_as=True)),
                ('undo', 'Undo', 'Ctrl+Z', self.undo), ('redo', 'Redo', 'Ctrl+Shift+Z', self.redo),
                ('fit', 'Fit Page', 'Ctrl+0', self.fit_page),
                ('canvas_in', 'Canvas +', 'Ctrl+=', lambda: self.zoom_page(1.2)),
                ('canvas_out', 'Canvas -', 'Ctrl+-', lambda: self.zoom_page(1 / 1.2)),
                ('focus_canvas', 'Focus Canvas', 'F11', self.toggle_canvas_focus),
                ('export', 'Export PDF + PNG', 'Ctrl+E', self.export),
                ('cancel', 'Cancel export', '', self.cancel_export),
            ):
                action = toolbar.addAction(label, handler)
                if shortcut:
                    action.setShortcut(shortcut)
                if key == 'redo':
                    action.setShortcuts(['Ctrl+Y', 'Ctrl+Shift+Z'])
                if key == 'canvas_in':
                    action.setShortcuts(['Ctrl+=', 'Ctrl++'])
                if key == 'focus_canvas':
                    action.setCheckable(True)
                self.actions[key] = action
            splitter = QSplitter()
            self.editor_splitter = splitter
            objects = QWidget()
            self.object_panel = objects
            objects.setMinimumWidth(140)
            objects.setMaximumWidth(240)
            objects.setStyleSheet('background: white; color: #0f172a;')
            object_layout = QVBoxLayout(objects)
            object_layout.addWidget(QLabel('PAGE OBJECTS'))
            self.object_list = QListWidget()
            self.object_list.addItems(['Main Map', 'Upper Inset', 'Lower Inset', 'Title', 'Location',
                                       'Upper inset label', 'Lower inset label', 'Disclaimer'])
            self.object_list.currentRowChanged.connect(self.select_row)
            object_layout.addWidget(self.object_list)
            object_layout.addWidget(QLabel('Layout positions and logo are locked.'))
            splitter.addWidget(objects)
            self.view = MapView(self)
            self.view.setBackgroundBrush(QColor('#e2e8f0'))
            self.view.setMinimumSize(400, 300)
            self.view.setFocusPolicy(Qt.StrongFocus)
            canvas = QWidget()
            canvas_layout = QVBoxLayout(canvas)
            canvas_layout.setContentsMargins(0, 0, 0, 0)
            navigation = QHBoxLayout()
            self.canvas_buttons = {}
            for key, label, handler in (
                ('out', '-', lambda: self.zoom_page(1 / 1.2)),
                ('in', '+', lambda: self.zoom_page(1.2)),
                ('fit', 'Fit Page', self.fit_page),
                ('width', 'Fit Width', self.fit_page_width),
                ('actual', '100%', self.actual_page_size),
                ('focus_canvas', 'Focus Canvas', self.toggle_canvas_focus),
            ):
                button = QPushButton(label)
                if key == 'focus_canvas':
                    button.setCheckable(True)
                button.clicked.connect(handler)
                navigation.addWidget(button)
                self.canvas_buttons[key] = button
            navigation.addStretch()
            canvas_layout.addLayout(navigation)
            canvas_layout.addWidget(QLabel('Ctrl+wheel: canvas zoom | Middle/Space-drag: pan canvas'))
            canvas_layout.addWidget(self.view, 1)
            splitter.addWidget(canvas)
            self.tabs = QTabWidget()
            self.tabs.setMinimumWidth(300)
            tab_layouts = []
            for title in ('Map', 'Text', 'Style', 'Page'):
                scroll = QScrollArea()
                scroll.setWidgetResizable(True)
                controls = QWidget()
                tab_layouts.append(QVBoxLayout(controls))
                scroll.setWidget(controls)
                self.tabs.addTab(scroll, title)
            panel = tab_layouts[0]
            map_hint = QLabel('Drag/wheel over a map edits its contents. Ctrl+wheel zooms the canvas.')
            map_hint.setWordWrap(True)
            panel.addWidget(map_hint)

            maps = QGroupBox('Map framing')
            form = QFormLayout(maps)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            self.map_choice = QComboBox()
            self.map_choice.addItems(['Main Map', 'Upper Inset', 'Lower Inset'])
            self.map_choice.currentIndexChanged.connect(self.select_map)
            form.addRow('Editing', self.map_choice)
            self.scale = QDoubleSpinBox()
            self.scale.setRange(1, 1e10)
            self.scale.setDecimals(2)
            form.addRow('Scale', self.scale)
            apply_scale = QPushButton('Apply 1:scale')
            apply_scale.clicked.connect(lambda: self.perform(lambda: self.session.set_scale(self.map_id(), self.scale.value())))
            form.addRow(apply_scale)
            buttons = QWidget()
            row = QHBoxLayout(buttons)
            row.setContentsMargins(0, 0, 0, 0)
            for text, factor in [('Zoom in', 1 / 1.2), ('Zoom out', 1.2)]:
                button = QPushButton(text)
                button.clicked.connect(lambda _checked=False, f=factor: self.perform(lambda: self.session.zoom(self.map_id(), f)))
                row.addWidget(button)
            reset = QPushButton('Reset map')
            reset.clicked.connect(lambda: self.perform(lambda: self.session.reset_map(self.map_id())))
            form.addRow(buttons)
            form.addRow(reset)
            self.bounds = QLineEdit()
            self.bounds.setPlaceholderText('min_lon,min_lat,max_lon,max_lat')
            form.addRow('WGS 84 bounds', self.bounds)
            apply_bounds = QPushButton('Apply bounds to selected map')
            apply_bounds.clicked.connect(lambda: self.perform(lambda: self.session.set_bounds(self.map_id(), self.bounds.text())))
            form.addRow(apply_bounds)
            self.overlay = QComboBox()
            self.overlay.addItems(['boundary', 'pin'])
            self.overlay.currentTextChanged.connect(self.change_overlay)
            form.addRow('Inset marker', self.overlay)
            self.overview_crs = QComboBox()
            self.overview_crs.addItem('WGS 84 / EPSG:4326', 'EPSG:4326')
            self.overview_crs.addItem('Same as main map', 'project')
            self.overview_crs.currentIndexChanged.connect(self.change_overview_crs)
            form.addRow('Selected inset CRS', self.overview_crs)
            self.auto_multiplier = QDoubleSpinBox()
            self.auto_multiplier.setRange(1, 20)
            self.auto_multiplier.setSingleStep(0.1)
            form.addRow('Auto-fit context', self.auto_multiplier)
            auto = QPushButton('Auto-fit main map')
            auto.clicked.connect(self.auto_fit)
            form.addRow(auto)
            self.grid_font_size = QDoubleSpinBox()
            self.grid_font_size.setRange(1, 72)
            self.grid_font_size.setDecimals(1)
            self.grid_font_size.setSingleStep(0.5)
            self.grid_font_size.valueChanged.connect(self.change_grid_font_size)
            form.addRow('Main-map coordinates (pt)', self.grid_font_size)
            panel.addWidget(maps)
            presets = QGroupBox('Reusable presets')
            preset_layout = QVBoxLayout(presets)
            self.preset_choice = QComboBox()
            preset_layout.addWidget(self.preset_choice)
            for label, handler in (('Apply preset', self.apply_selected_preset),
                                   ('Save location preset', self.save_location_preset),
                                   ('Save style preset', self.save_style_preset)):
                button = QPushButton(label)
                button.clicked.connect(handler)
                preset_layout.addWidget(button)
            panel.addWidget(presets)
            panel.addStretch()

            panel = tab_layouts[1]
            labels = QGroupBox('Text — updates as you type')
            form = QFormLayout(labels)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            self.text_choice = QComboBox()
            self.text_choice.addItems(['Title', 'Location', 'Upper inset label', 'Lower inset label', 'Disclaimer'])
            self.text_choice.currentIndexChanged.connect(self.select_text)
            form.addRow('Label', self.text_choice)
            self.text_value = QPlainTextEdit()
            self.text_value.setMaximumHeight(90)
            self.text_value.textChanged.connect(self.change_text)
            form.addRow(self.text_value)
            self.font_family = QFontComboBox()
            self.font_family.currentFontChanged.connect(self.change_text)
            form.addRow('Font', self.font_family)
            self.font_size = QDoubleSpinBox()
            self.font_size.setRange(1, 200)
            self.font_size.valueChanged.connect(self.change_text)
            form.addRow('Size (pt)', self.font_size)
            self.bold = QCheckBox('Bold')
            self.bold.toggled.connect(self.change_text)
            form.addRow(self.bold)
            self.color_button = QPushButton('Text color')
            self.color_button.clicked.connect(self.choose_color)
            form.addRow(self.color_button)
            panel.addWidget(labels)
            panel.addStretch()

            panel = tab_layouts[2]
            self.style_controls = {}
            styling = QGroupBox('Selected map style')
            form = QFormLayout(styling)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            for key, title, low, high, step in (
                ('basemap_opacity', 'Basemap opacity (%)', 0, 100, 5),
                ('boundary_opacity', 'Boundary opacity (%)', 0, 100, 5),
                ('boundary_width_mm', 'Boundary width (mm)', 0.05, 5, 0.05),
                ('pin_size_mm', 'Pin size (mm)', 1, 20, 0.5),
            ):
                control = QDoubleSpinBox()
                control.setRange(low, high)
                control.setSingleStep(step)
                control.valueChanged.connect(self.change_map_style)
                self.style_controls[key] = control
                form.addRow(title, control)
            self.style_colors = {}
            self.style_color_buttons = {}
            for key, title in (('boundary_stroke', 'Boundary outline'), ('boundary_fill', 'Boundary fill'),
                               ('pin_color', 'Pin color')):
                button = QPushButton(title)
                button.clicked.connect(lambda _checked=False, k=key: self.choose_style_color(k))
                self.style_color_buttons[key] = button
                form.addRow(button)
            panel.addWidget(styling)
            grid = QGroupBox('Main-map coordinates and grid')
            form = QFormLayout(grid)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            self.grid_lines, self.grid_labels = QCheckBox('Show grid lines'), QCheckBox('Show coordinate labels')
            for control in (self.grid_lines, self.grid_labels):
                control.toggled.connect(self.change_grid_style)
                form.addRow(control)
            self.grid_width, self.grid_opacity = QDoubleSpinBox(), QDoubleSpinBox()
            self.grid_width.setRange(0.05, 2)
            self.grid_width.setSingleStep(0.05)
            self.grid_opacity.setRange(0, 100)
            self.grid_opacity.setSingleStep(5)
            self.grid_width.valueChanged.connect(self.change_grid_style)
            self.grid_opacity.valueChanged.connect(self.change_grid_style)
            form.addRow('Line width (mm)', self.grid_width)
            form.addRow('Line opacity (%)', self.grid_opacity)
            self.grid_color = '#5a5a5a'
            self.grid_color_button = QPushButton('Grid line color')
            self.grid_color_button.clicked.connect(self.choose_grid_color)
            form.addRow(self.grid_color_button)
            panel.addWidget(grid)
            panel.addStretch()

            panel = tab_layouts[3]
            output = QGroupBox('Page and export')
            form = QFormLayout(output)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            self.paper = QComboBox()
            self.paper.addItems(['A3', 'A4'])
            self.paper.currentTextChanged.connect(self.change_paper)
            form.addRow('Landscape paper', self.paper)
            self.dpi = {}
            for key, label in [('preview_dpi', 'PNG DPI'), ('pdf_dpi', 'PDF DPI'), ('inset_dpi', 'Inset DPI')]:
                control = QSpinBox()
                control.setRange(1, 2400)
                control.valueChanged.connect(self.change_output)
                self.dpi[key] = control
                form.addRow(label, control)
            panel.addWidget(output)
            page_zoom = QGroupBox('Page view — does not change map scale')
            form = QHBoxLayout(page_zoom)
            for label, handler in (('−', lambda: self.zoom_page(1 / 1.2)), ('Fit Page', self.fit_page),
                                   ('+', lambda: self.zoom_page(1.2))):
                button = QPushButton(label)
                button.clicked.connect(handler)
                form.addWidget(button)
            panel.addWidget(page_zoom)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumBlockCount(1000)
            self.log.setMaximumHeight(140)
            panel.addWidget(QLabel('Export progress'))
            panel.addWidget(self.log)
            self.export_busy = QProgressBar()
            panel.addWidget(self.export_busy)
            for kind, title in (('pdf', 'Open PDF'), ('preview', 'Open PNG'), ('folder', 'Open Folder')):
                action = QPushButton(title)
                action.clicked.connect(lambda _checked=False, k=kind: self.open_output(k))
                panel.addWidget(action)
                self.actions['output_' + kind] = action
            panel.addStretch()
            # Long preset/font names must not force a scroll tab wider than its
            # viewport. Keep a useful field hint and let forms wrap narrow rows.
            for choice in (self.map_choice, self.overlay, self.overview_crs,
                           self.preset_choice, self.text_choice, self.font_family, self.paper):
                choice.setMinimumContentsLength(12)
                choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            splitter.addWidget(self.tabs)
            splitter.setStretchFactor(0, 0)
            splitter.setStretchFactor(1, 1)
            splitter.setStretchFactor(2, 0)
            splitter.setSizes([170, 1100, 400])
            self.pages = QTabWidget()
            self.generator = create_generator_page(self)
            self.pages.addTab(self.generator.container, 'Generate Map')
            self.pages.addTab(splitter, 'Editor')
            self.setCentralWidget(self.pages)
            self.text_timer = QTimer(self)
            self.text_timer.setSingleShot(True)
            self.text_timer.setInterval(750)
            self.text_timer.timeout.connect(self.finish_text_group)
            self.autosave_timer = QTimer(self)
            self.autosave_timer.setInterval(30000)
            self.autosave_timer.timeout.connect(self.autosave)
            self.autosave_timer.start()
            self.update_actions()
            self.statusBar().showMessage('Generate a map from processed surveys, or open an existing map/design.')

        def map_id(self):
            return MAP_IDS[self.map_choice.currentIndex()]

        def text_id(self):
            return TEXT_IDS[self.text_choice.currentIndex()]

        def workspace(self):
            return REPO_ROOT / 'exports' / 'map_editor' / self.session.design.package_dir.name

        def hit_item(self, point):
            if self.session:
                for key in TEXT_IDS + MAP_IDS:
                    item = self.session.ctx.layout.itemById(key)
                    if item is not None and item.sceneBoundingRect().contains(point):
                        return key
            return None

        def select_row(self, row):
            if 0 <= row < len(MAP_IDS + TEXT_IDS):
                self.select_item((MAP_IDS + TEXT_IDS)[row])

        def select_map(self, index):
            if 0 <= index < len(MAP_IDS):
                self.select_item(MAP_IDS[index])

        def select_text(self, index):
            if 0 <= index < len(TEXT_IDS):
                self.select_item(TEXT_IDS[index])

        def select_item(self, key):
            if self.loading or key not in MAP_IDS + TEXT_IDS:
                return
            self.finish_text_group()
            self.selected_id = key
            self.loading = True
            try:
                self.object_list.setCurrentRow((MAP_IDS + TEXT_IDS).index(key))
                if key in MAP_IDS:
                    self.map_choice.setCurrentIndex(MAP_IDS.index(key))
                    if self.tabs.currentIndex() != 2:
                        self.tabs.setCurrentIndex(0)
                else:
                    self.text_choice.setCurrentIndex(TEXT_IDS.index(key))
                    self.tabs.setCurrentIndex(1)
            finally:
                self.loading = False
            self.refresh_controls()
            self.view.viewport().update()
            self.update_state()

        def zoom_page(self, factor, *, anchor=None):
            if not self.session or not math.isfinite(factor) or factor <= 0:
                return
            current = self.view.transform().m11()
            if not math.isfinite(current) or current <= 0:
                return
            target = max(0.1, min(50.0, current * factor))
            factor = target / current
            if factor == 1.0:
                return
            self.view.flush_pan()
            self.view.cancel_drag()
            before = self.view.mapToScene(anchor) if anchor is not None else None
            self.view.scale(factor, factor)
            if before is not None:
                after = self.view.mapToScene(anchor)
                center = self.view.mapToScene(self.view.viewport().rect().center())
                self.view.centerOn(center.x() + before.x() - after.x(), center.y() + before.y() - after.y())
            self.view.emitZoomLevelChanged()
            self.view.viewport().update()

        def fit_page_width(self):
            if self.session:
                self.view.cancel_drag()
                self.view.zoomWidth()

        def actual_page_size(self):
            if self.session:
                self.view.cancel_drag()
                self.view.zoomActual()

        def toggle_canvas_focus(self, *_args):
            if not self.session:
                return
            self.canvas_focus = not self.canvas_focus
            self.object_panel.setVisible(not self.canvas_focus)
            self.tabs.setVisible(not self.canvas_focus)
            self.actions['focus_canvas'].setChecked(self.canvas_focus)
            self.canvas_buttons['focus_canvas'].setChecked(self.canvas_focus)
            QTimer.singleShot(0, self.fit_page)

        def update_actions(self):
            active = self.session is not None
            for key in ('save', 'save_as', 'import', 'fit', 'canvas_in', 'canvas_out', 'focus_canvas'):
                self.actions[key].setEnabled(active)
            for button in self.canvas_buttons.values():
                button.setEnabled(active)
            grouped_edit = (self.history is not None and self.history.group_start is not None
                            and self.history.group_start != self.history.current)
            self.actions['undo'].setEnabled(active and self.history is not None
                                             and (bool(self.history.undo_steps) or grouped_edit))
            self.actions['redo'].setEnabled(active and self.history is not None
                                             and bool(self.history.redo_steps) and not grouped_edit)
            self.actions['export'].setEnabled(active and not self.job.busy and self.helper_gate.owner is None)
            self.actions['cancel'].setEnabled(self.job.busy)
            self.tabs.setEnabled(active)
            self.object_list.setEnabled(active)
            for kind in ('pdf', 'preview', 'folder'):
                self.actions['output_' + kind].setEnabled(kind in self.export_outputs)
            self.export_busy.setVisible(self.job.busy)
            self.export_busy.setRange(0, 0 if self.job.busy else 1)
            self.generator.refresh_busy()

        def update_state(self):
            was_dirty = self.dirty
            if self.history is not None:
                self.dirty = self.history.dirty
                if was_dirty and not self.dirty and self.recovery is not None:
                    self.resolve_recovery()
            name = self.design_path.name if self.design_path else 'New design'
            self.setWindowTitle(f'Map Editor V2 — {name}' + (' *' if self.dirty else ''))
            if self.session:
                item = self.session._map(self.map_id())
                self.statusBar().showMessage(
                    f'{"Unsaved" if self.dirty else "Saved"} | {self.selected_id} | '
                    f'{item.crs().authid()} | 1:{item.scale():,.2f} | '
                    f'Package: {self.session.design.package_dir} | Design: {self.design_path or "not saved"}')
            self.update_actions()

        def finish_text_group(self):
            self.text_timer.stop()
            if self.history is not None:
                self.history.end_group()
                self.update_state()

        def undo(self, *_args):
            self.navigate_history('undo')

        def redo(self, *_args):
            self.navigate_history('redo')

        def navigate_history(self, direction):
            if not self.history:
                return
            self.view.cancel_drag()
            self.finish_text_group()
            try:
                getattr(self.history, direction)()
            except Exception as exc:
                self.error(exc)
            self.attach_layout()
            self.refresh_controls()
            self.update_state()

        def attach_layout(self):
            if self.session and self.session._previous_ctx is not None:
                self.view.setCurrentLayout(self.session.ctx.layout)
                self.session._previous_ctx = None
            self.view.viewport().update()

        def error(self, exc):
            QMessageBox.critical(self, 'Map Editor', str(exc))

        def mark_dirty(self):
            self.dirty = True
            self.setWindowTitle('Orthomosaic Map Editor — Unsaved changes')

        def perform(self, action, *, grouped=False):
            if self.session is None or self.loading:
                return False
            if not grouped:
                self.finish_text_group()
            try:
                if self.history is not None:
                    self.history.execute(action)
                else:
                    action()
                    self.mark_dirty()
                self.attach_layout()
                self.refresh_controls()
                self.update_state()
                return True
            except Exception as exc:
                self.view.cancel_drag()
                self.attach_layout()
                self.refresh_controls()
                self.update_state()
                self.error(exc)
                return False

        def confirm_discard(self):
            if not self.dirty:
                return True
            answer = QMessageBox.question(self, 'Unsaved design', 'Save your changes before continuing?',
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Save:
                return self.save()
            if answer == QMessageBox.Discard:
                self.resolve_recovery()
                return True
            return False

        def install_design(self, design, path=None):
            self.view.cancel_drag()
            self.finish_text_group()
            self.statusBar().showMessage('Loading TIFFs and layout; large files may take time...')
            session = EditorSession(design)
            self.view.setCurrentLayout(session.ctx.layout)
            self.session = session
            self.design_path = None if path and Path(path).name == 'snapshot.mapedit.json' else path
            if self.design_path is not None:
                self.design_path = Path(self.design_path)
            self.history = DesignHistory(session)
            self.recovery = RecoveryStore(self.workspace(), design.package_dir, self.design_path)
            self.recovered_paths = []
            self.dirty = False
            self.selected_id = 'map_frame'
            self.loading = True
            self.object_list.setCurrentRow(0)
            self.map_choice.setCurrentIndex(0)
            self.loading = False
            self.setWindowTitle(f'Orthomosaic Map Editor — {design.package_dir.name}')
            self.refresh_controls()
            self.refresh_presets()
            QTimer.singleShot(0, self.fit_page)
            self.offer_recovery()
            self.update_state()
            self.pages.setCurrentIndex(1)

        def load_package(self, path):
            package = Path(path).resolve()
            snapshot = package / 'snapshot.mapedit.json'
            if snapshot.is_file():
                self.install_design(load_design(snapshot), snapshot)
                return
            workspace = REPO_ROOT / 'exports/map_editor' / package.name
            saved = workspace / 'design.v2.mapedit.json'
            if not saved.is_file():
                saved = workspace / 'design.mapedit.json'
            if saved.is_file():
                design = load_design(saved)
                if design.package_dir != package:
                    raise ValueError('Saved design points to a different package; use Open design')
                self.install_design(design, saved)
            else:
                self.install_design(design_from_package(package))

        def open_package(self):
            if not self.confirm_discard():
                return
            path = QFileDialog.getExistingDirectory(self, 'Open exported map folder', str(REPO_ROOT / 'exports/maps'))
            if path:
                try:
                    self.load_package(path)
                except Exception as exc:
                    self.error(exc)

        def open_design(self):
            if not self.confirm_discard():
                return
            path, _ = QFileDialog.getOpenFileName(self, 'Open map design', '', 'Map designs (*.mapedit.json)')
            if path:
                try:
                    self.install_design(load_design(path), Path(path))
                except Exception as exc:
                    self.error(exc)

        def import_bat(self):
            if not self.session:
                return
            path, _ = QFileDialog.getOpenFileName(self, 'Read BAT settings (does not execute)', str(REPO_ROOT), 'Batch settings (*.bat)')
            if path:
                try:
                    design, warnings = apply_bat_settings(self.session.snapshot(), path)
                    self.perform(lambda: self.session.replace_design(design))
                    if warnings:
                        QMessageBox.warning(self, 'Some settings were ignored', '\n'.join(warnings))
                except Exception as exc:
                    self.error(exc)

        def save(self, _checked=False, *, save_as=False):
            if not self.session:
                return False
            path = self.design_path
            legacy = self.session.design.source_version == 1
            if path is None or save_as or legacy:
                path, _ = QFileDialog.getSaveFileName(self, 'Save design outside the source package',
                    str(self.workspace() / 'design.v2.mapedit.json'), 'Map designs (*.mapedit.json)')
            if not path:
                return False
            try:
                self.view.cancel_drag()
                self.finish_text_group()
                save_design(self.session.snapshot(), path)
                self.design_path = Path(path)
                self.session.design.source_version = 2
                if self.history is not None:
                    self.history.mark_saved()
                self.resolve_recovery()
                self.recovery = RecoveryStore(self.workspace(), self.session.design.package_dir, self.design_path)
                self.dirty = False
                self.setWindowTitle(f'Orthomosaic Map Editor — {self.design_path.name}')
                self.statusBar().showMessage(f'Design saved: {path}')
                self.update_state()
                return True
            except Exception as exc:
                self.error(exc)
                return False

        def fit_page(self):
            if self.session:
                self.view.cancel_drag()
                page = self.session.ctx.layout.pageCollection().page(0)
                if page is not None:
                    self.view.fitInView(page.sceneBoundingRect().adjusted(-4, -4, 4, 4), Qt.KeepAspectRatio)
                    self.view.emitZoomLevelChanged()
                else:
                    self.view.zoomFull()

        def refresh_controls(self, *_args):
            if not self.session:
                return
            self.loading = True
            try:
                self.scale.setValue(self.session._map(self.map_id()).scale())
                self.bounds.setText(self.session.geographic_bounds(self.map_id()))
                is_inset = self.map_id() != 'map_frame'
                self.overlay.setEnabled(is_inset)
                prefix = 'boundary' if self.map_id() == 'map_inset' else 'overview'
                if is_inset:
                    self.overlay.setCurrentText(self.session.design.options[prefix + '_overlay'])
                item = self.session.ctx.layout.itemById(self.text_id())
                self.text_value.setPlainText(item.text())
                font = item.font()
                self.font_family.setCurrentFont(font)
                self.font_size.setValue(font.pointSizeF())
                self.bold.setChecked(font.bold())
                self.font_color = item.fontColor().name()
                self.color_button.setStyleSheet(f'color: {self.font_color}')
                self.paper.setCurrentText(self.session.design.options['paper_size'])
                crs_key = 'boundary_crs' if self.map_id() == 'map_inset' else 'overview_crs'
                self.overview_crs.setEnabled(is_inset)
                self.overview_crs.setCurrentIndex(self.overview_crs.findData(self.session.design.options[crs_key]))
                self.auto_multiplier.setValue(self.session.design.options['auto_scale_multiplier'])
                self.grid_font_size.setValue(self.session.design.options['grid_font_size'])
                for key, control in self.dpi.items():
                    control.setValue(self.session.design.options[key])
                style = self.session.design.map_styles[self.map_id()]
                for key, control in self.style_controls.items():
                    control.setValue(style[key] * (100 if key.endswith('_opacity') else 1))
                    control.setEnabled(key != 'pin_size_mm' or is_inset)
                self.style_colors = {key: style[key] for key in self.style_color_buttons}
                for key, button in self.style_color_buttons.items():
                    button.setStyleSheet(f'color: {style[key]}')
                    button.setEnabled(key != 'pin_color' or is_inset)
                grid = self.session.design.grid_style
                self.grid_lines.setChecked(grid['lines_visible'])
                self.grid_labels.setChecked(grid['labels_visible'])
                self.grid_width.setValue(grid['width_mm'])
                self.grid_opacity.setValue(grid['opacity'] * 100)
                self.grid_color = grid['color']
                self.grid_color_button.setStyleSheet(f'color: {self.grid_color}')
            finally:
                self.loading = False

        def change_text(self, *_args):
            if self.loading or not self.session:
                return
            if self.history is not None and self.history.group_start is None:
                self.history.begin_group()
            style = {'family': self.font_family.currentFont().family(), 'size': self.font_size.value(),
                     'bold': self.bold.isChecked(), 'color': self.font_color}
            key, text = self.text_id(), self.text_value.toPlainText()
            # Do not replace editor contents/cursor on each keystroke.
            try:
                action = lambda: self.session.set_text(key, text, style)
                if self.history is not None:
                    self.history.execute(action)
                else:
                    action()
                    self.mark_dirty()
                self.text_timer.start()
                self.attach_layout()
                self.update_state()
            except Exception as exc:
                self.attach_layout()
                self.refresh_controls()
                self.update_state()
                self.error(exc)

        def choose_color(self):
            color = QColorDialog.getColor(QColor(self.font_color), self)
            if color.isValid():
                self.font_color = color.name()
                self.color_button.setStyleSheet(f'color: {self.font_color}')
                self.change_text()

        def change_overlay(self, mode):
            self.perform(lambda: self.session.set_overlay(self.map_id(), mode))

        def change_output(self, *_args):
            if self.loading or not self.session:
                return
            values = {key: control.value() for key, control in self.dpi.items()}
            self.perform(lambda: self.session.design.options.update(values))

        def change_grid_font_size(self, size):
            self.perform(lambda: self.session.set_grid_font_size(size))

        def change_paper(self, paper):
            if self.loading or not self.session:
                return
            self.perform(lambda: self.session.set_paper(paper))
            self.fit_page()

        def change_overview_crs(self, *_args):
            if self.loading or not self.session:
                return
            self.view.cancel_drag()
            crs = self.overview_crs.currentData()
            self.perform(lambda: self.session.set_inset_crs(self.map_id(), crs))
            self.fit_page()

        def auto_fit(self):
            if not self.session:
                return
            def action():
                value = self.auto_multiplier.value()
                self.session.design.options['auto_scale_multiplier'] = value
                self.session.ctx.render_options['auto_scale_multiplier'] = value
                self.session.auto_fit()
            self.perform(action)

        def change_map_style(self, *_args):
            if self.loading or not self.session:
                return
            style = dict(self.session.design.map_styles[self.map_id()], **self.style_colors)
            for key, control in self.style_controls.items():
                style[key] = control.value() / (100 if key.endswith('_opacity') else 1)
            self.perform(lambda: self.session.set_map_style(self.map_id(), style))

        def choose_style_color(self, key):
            color = QColorDialog.getColor(QColor(self.style_colors[key]), self)
            if color.isValid():
                self.style_colors[key] = color.name()
                self.change_map_style()

        def change_grid_style(self, *_args):
            if self.loading or not self.session:
                return
            style = {'lines_visible': self.grid_lines.isChecked(), 'labels_visible': self.grid_labels.isChecked(),
                     'width_mm': self.grid_width.value(), 'opacity': self.grid_opacity.value() / 100,
                     'color': self.grid_color}
            self.perform(lambda: self.session.set_grid_style(style))

        def choose_grid_color(self):
            color = QColorDialog.getColor(QColor(self.grid_color), self)
            if color.isValid():
                self.grid_color = color.name()
                self.change_grid_style()

        def preset_directory(self):
            return REPO_ROOT / 'exports' / 'map_editor' / 'presets'

        def refresh_presets(self):
            self.presets, warnings = load_presets(self.preset_directory())
            self.preset_choice.clear()
            self.preset_choice.addItems([f'{item["kind"].title()}: {item["name"]}' for item in self.presets])
            for warning in warnings:
                self.log.appendPlainText('Preset warning: ' + warning)

        def apply_selected_preset(self):
            index = self.preset_choice.currentIndex()
            if self.session and 0 <= index < len(self.presets):
                preset = self.presets[index]
                if preset['kind'] == 'location' and self.selected_id not in ('map_inset', 'map_inset_2'):
                    self.error(ValueError('Select an inset before applying a location preset'))
                    return
                self.perform(lambda: self.session.apply_preset(preset, self.map_id()))

        def save_location_preset(self):
            if not self.session or self.selected_id not in ('map_inset', 'map_inset_2'):
                self.error(ValueError('Select an inset before saving a location preset'))
                return
            name, ok = QInputDialog.getText(self, 'Location preset', 'Preset name')
            if ok:
                key = self.map_id()
                prefix, label = ('boundary', 'label_inset') if key == 'map_inset' else ('overview', 'label_inset_2')
                try:
                    preset = location_preset(name, self.session.geographic_bounds(key),
                        self.session.design.options[prefix + '_crs'], self.session.design.options[prefix + '_overlay'])
                    preset['label'] = self.session.ctx.layout.itemById(label).text()
                    save_preset(self.preset_directory(), preset, self.session.design.package_dir)
                    self.refresh_presets()
                except Exception as exc:
                    self.error(exc)

        def save_style_preset(self):
            if not self.session:
                return
            name, ok = QInputDialog.getText(self, 'Style preset', 'Preset name')
            if ok:
                try:
                    save_preset(self.preset_directory(), style_preset(name, self.session.snapshot()),
                                self.session.design.package_dir)
                    self.refresh_presets()
                except Exception as exc:
                    self.error(exc)

        def autosave(self):
            if self.session and self.recovery is not None and self.dirty and not self.loading:
                try:
                    self.recovery.write(self.session.snapshot())
                except Exception as exc:
                    self.log.appendPlainText(f'Recovery autosave failed: {exc}')
                    self.statusBar().showMessage(f'Autosave failed; save your design manually: {exc}')

        def resolve_recovery(self):
            if self.recovery is not None:
                try:
                    for path in self.recovered_paths:
                        self.recovery.resolve(path)
                    self.recovery.resolve()
                    self.recovered_paths = []
                except Exception as exc:
                    self.log.appendPlainText(f'Could not resolve recovery evidence: {exc}')
                    self.error(exc)

        def offer_recovery(self):
            candidates, warnings = self.recovery.candidates()
            for warning in warnings:
                self.log.appendPlainText('Recovery warning: ' + warning)
            if not candidates:
                return
            answer = QMessageBox.question(self, 'Recover unsaved work?',
                f'Unsaved recovery from {candidates[0][0]} is available. Recover the latest? '
                'Choosing No dismisses these recovery snapshots without deleting them.',
                QMessageBox.Yes | QMessageBox.No)
            self.recovered_paths = [path for _, path in candidates]
            if answer == QMessageBox.Yes:
                try:
                    design = self.recovery.read(candidates[0][1])
                    self.perform(lambda: self.session.restore(design))
                except Exception as exc:
                    self.error(exc)
            elif answer == QMessageBox.No:
                self.resolve_recovery()

        def _new_process(self):
            process = QProcess(self)
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert('QT_QPA_PLATFORM', export_qt_platform(sys.platform))
            environment.remove('VIRTUAL_ENV')
            process.setProcessEnvironment(environment)
            process.setProcessChannelMode(QProcess.MergedChannels)
            process.readyReadStandardOutput.connect(self._read_export_log)
            process.finished.connect(self._export_finished)
            process.errorOccurred.connect(self._export_error)
            return process

        def export(self):
            if not self.session:
                return
            try:
                self.view.cancel_drag()
                self.finish_text_group()
                attempt = self.job.start(self.session.snapshot(), self.workspace() / 'exports',
                                         sys.executable, REPO_ROOT / 'map_editor.py')
                self.export_outputs = {}
                self.update_actions()
                self.tabs.setCurrentIndex(3)
                self.log.appendPlainText(f'Export started: {attempt}')
                self.statusBar().showMessage(f'Export running: {attempt}')
            except Exception as exc:
                self.error(exc)

        def _read_export_log(self):
            if self.job.process is None:
                return
            data = bytes(self.job.process.readAllStandardOutput()).decode('utf-8', errors='replace')
            if data:
                self.log.appendPlainText(data.rstrip())
                for line in data.splitlines():
                    if line.startswith(('Building ', 'Exporting ', 'Export complete:')):
                        self.statusBar().showMessage(line)
                with (self.job.attempt / 'export.log').open('a', encoding='utf-8') as log:
                    log.write(data)

        def _export_error(self, error):
            if error == QProcess.FailedToStart:
                self._export_finished(-1)

        def _export_finished(self, code, *_args):
            process = self.job.process
            if process is None:
                return
            self._read_export_log()
            try:
                state = self.job.finish(code)
                self.statusBar().showMessage(f'Export {state["status"]}: {self.job.attempt}')
                self.log.appendPlainText(f'Export {state["status"]}: {self.job.attempt}')
                if state['status'] == 'complete':
                    for kind, name in (('pdf', 'print_map.pdf'), ('preview', 'preview.png')):
                        path = (self.job.attempt / name).resolve()
                        if path.parent == self.job.attempt.resolve() and path.is_file():
                            self.export_outputs[kind] = path
                    self.export_outputs['folder'] = self.job.attempt.resolve()
            except Exception as exc:
                self.error(exc)
            process.deleteLater()
            self.update_actions()

        def open_output(self, kind):
            path = self.export_outputs.get(kind)
            if path and path.exists():
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                    self.error(RuntimeError(f'Could not open export: {path}'))

        def cancel_export(self):
            process = self.job.process
            self.job.cancel()
            if process is not None:
                QTimer.singleShot(5000, lambda: process.kill() if self.job.process is process else None)

        def closeEvent(self, event):
            self.view.cancel_drag()
            self.finish_text_group()
            if self.job.busy or self.generator.job.busy:
                answer = QMessageBox.question(self, 'Operation running', 'Cancel the running helper operation?')
                if answer == QMessageBox.Yes:
                    if self.generator.job.busy:
                        self.generator.cancel()
                    else:
                        self.cancel_export()
                event.ignore()
            elif self.confirm_discard():
                event.accept()
            else:
                event.ignore()

    return MapEditorWindow
