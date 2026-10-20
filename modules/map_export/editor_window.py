"""Lazy Qt window definition: importing this module never starts Qt or QGIS."""
from __future__ import annotations

from pathlib import Path
import sys

from .editor_model import (
    MAP_IDS, TEXT_IDS, REPO_ROOT, apply_bat_settings, design_from_package,
    load_design, save_design,
)
from .editor_session import EditorSession, ExportJob, export_qt_platform


def create_window_class():
    from qgis.PyQt.QtCore import Qt, QTimer, QProcess, QProcessEnvironment
    from qgis.PyQt.QtGui import QColor, QFont
    from qgis.PyQt.QtWidgets import (
        QMainWindow, QWidget, QSplitter, QVBoxLayout, QFormLayout, QHBoxLayout,
        QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QPlainTextEdit,
        QLineEdit, QLabel, QFontComboBox, QCheckBox, QColorDialog, QFileDialog,
        QMessageBox, QScrollArea, QGroupBox,
    )
    from qgis.gui import QgsLayoutView

    class MapView(QgsLayoutView):
        def __init__(self, owner):
            super().__init__(owner)
            self.owner = owner
            self.anchor = None
            self.drag_map = None
            self.pending = [0.0, 0.0]
            self.pan_timer = QTimer(self)
            self.pan_timer.setSingleShot(True)
            self.pan_timer.setInterval(40)
            self.pan_timer.timeout.connect(self.flush_pan)

        def cancel_drag(self):
            self.pan_timer.stop()
            self.anchor = None
            self.pending = [0.0, 0.0]

        def flush_pan(self):
            dx, dy = self.pending
            self.pending = [0.0, 0.0]
            if dx or dy:
                self.owner.perform(lambda: self.owner.session.pan(self.drag_map, dx, dy))

        def _point(self, event):
            return self.mapToScene(event.pos())

        def mousePressEvent(self, event):
            if self.owner.session and event.button() == Qt.LeftButton:
                item = self.owner.session._map(self.owner.map_id())
                point = self._point(event)
                if item.sceneBoundingRect().contains(point):
                    self.anchor = point
                    self.drag_map = self.owner.map_id()
            event.accept()

        def mouseMoveEvent(self, event):
            if self.anchor is not None:
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
            self.pan_timer.stop()
            self.flush_pan()
            self.anchor = None
            event.accept()

        def wheelEvent(self, event):
            self.flush_pan()
            if self.owner.session:
                item = self.owner.session._map(self.owner.map_id())
                if item.sceneBoundingRect().contains(self._point(event)):
                    factor = 1 / 1.2 if event.angleDelta().y() > 0 else 1.2
                    self.owner.perform(lambda: self.owner.session.zoom(self.owner.map_id(), factor))
            event.accept()

        def keyPressEvent(self, event):
            # The fixed-layout editor never forwards delete/cut/move-item shortcuts.
            event.ignore()

        def contextMenuEvent(self, event):
            event.accept()

    class MapEditorWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.session = None
            self.design_path = None
            self.dirty = False
            self.loading = False
            self.font_color = '#000000'
            self.job = ExportJob(self._new_process)
            self.resize(1500, 950)
            self.setWindowTitle('Orthomosaic Map Editor')
            toolbar = self.addToolBar('Map Editor')
            for label, handler in (
                ('Open map folder', self.open_package), ('Open design', self.open_design),
                ('Import BAT settings', self.import_bat), ('Save design', self.save),
                ('Save as', lambda: self.save(save_as=True)), ('Fit page', self.fit_page),
                ('Export PDF + PNG', self.export), ('Cancel export', self.cancel_export),
            ):
                toolbar.addAction(label, handler)
            splitter = QSplitter()
            self.view = MapView(self)
            splitter.addWidget(self.view)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            controls = QWidget()
            panel = QVBoxLayout(controls)
            panel.addWidget(QLabel('Choose a map, then drag or scroll inside its frame.'))

            maps = QGroupBox('Map framing')
            form = QFormLayout(maps)
            self.map_choice = QComboBox()
            self.map_choice.addItems(['Main Map', 'Upper Inset', 'Lower Inset'])
            self.map_choice.currentIndexChanged.connect(self.refresh_controls)
            form.addRow('Editing', self.map_choice)
            self.scale = QDoubleSpinBox()
            self.scale.setRange(1, 1e10)
            self.scale.setDecimals(2)
            scale_row = QWidget()
            scale_layout = QHBoxLayout(scale_row)
            scale_layout.setContentsMargins(0, 0, 0, 0)
            scale_layout.addWidget(self.scale)
            apply_scale = QPushButton('Apply 1:scale')
            apply_scale.clicked.connect(lambda: self.perform(lambda: self.session.set_scale(self.map_id(), self.scale.value())))
            scale_layout.addWidget(apply_scale)
            form.addRow('Scale', scale_row)
            buttons = QWidget()
            row = QHBoxLayout(buttons)
            for text, factor in [('Zoom in', 1 / 1.2), ('Zoom out', 1.2)]:
                button = QPushButton(text)
                button.clicked.connect(lambda _checked=False, f=factor: self.perform(lambda: self.session.zoom(self.map_id(), f)))
                row.addWidget(button)
            reset = QPushButton('Reset map')
            reset.clicked.connect(lambda: self.perform(lambda: self.session.reset_map(self.map_id())))
            row.addWidget(reset)
            form.addRow(buttons)
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
            form.addRow('Lower inset CRS', self.overview_crs)
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

            labels = QGroupBox('Text — updates as you type')
            form = QFormLayout(labels)
            self.text_choice = QComboBox()
            self.text_choice.addItems(['Title', 'Location', 'Upper inset label', 'Lower inset label', 'Disclaimer'])
            self.text_choice.currentIndexChanged.connect(self.refresh_controls)
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

            output = QGroupBox('Page and export')
            form = QFormLayout(output)
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
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setMaximumBlockCount(1000)
            self.log.setMaximumHeight(140)
            panel.addWidget(QLabel('Export progress'))
            panel.addWidget(self.log)
            panel.addStretch()
            scroll.setWidget(controls)
            splitter.addWidget(scroll)
            splitter.setStretchFactor(0, 1)
            splitter.setSizes([1050, 450])
            self.setCentralWidget(splitter)
            self.statusBar().showMessage('Open an exported map folder to begin.')

        def map_id(self):
            return MAP_IDS[self.map_choice.currentIndex()]

        def text_id(self):
            return TEXT_IDS[self.text_choice.currentIndex()]

        def workspace(self):
            return REPO_ROOT / 'exports' / 'map_editor' / self.session.design.package_dir.name

        def error(self, exc):
            QMessageBox.critical(self, 'Map Editor', str(exc))

        def mark_dirty(self):
            self.dirty = True
            self.setWindowTitle('Orthomosaic Map Editor — Unsaved changes')

        def perform(self, action):
            if self.session is None or self.loading:
                return
            try:
                action()
                self.mark_dirty()
                self.refresh_controls()
                self.view.viewport().update()
            except Exception as exc:
                self.error(exc)

        def confirm_discard(self):
            if not self.dirty:
                return True
            answer = QMessageBox.question(self, 'Unsaved design', 'Save your changes before continuing?',
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Save:
                return self.save()
            return answer == QMessageBox.Discard

        def install_design(self, design, path=None):
            self.view.cancel_drag()
            self.statusBar().showMessage('Loading TIFFs and layout; large files may take time...')
            session = EditorSession(design)
            self.view.setCurrentLayout(session.ctx.layout)
            self.session = session
            self.design_path = None if path and Path(path).name == 'snapshot.mapedit.json' else path
            self.dirty = False
            self.setWindowTitle(f'Orthomosaic Map Editor — {design.package_dir.name}')
            self.refresh_controls()
            QTimer.singleShot(0, self.fit_page)
            self.statusBar().showMessage('Ready. Select a map to pan/zoom; select a label to edit text.')

        def load_package(self, path):
            package = Path(path).resolve()
            snapshot = package / 'snapshot.mapedit.json'
            if snapshot.is_file():
                self.install_design(load_design(snapshot), snapshot)
                return
            saved = REPO_ROOT / 'exports/map_editor' / package.name / 'design.mapedit.json'
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
                    self.install_design(design, self.design_path)
                    self.mark_dirty()
                    if warnings:
                        QMessageBox.warning(self, 'Some settings were ignored', '\n'.join(warnings))
                except Exception as exc:
                    self.error(exc)

        def save(self, _checked=False, *, save_as=False):
            if not self.session:
                return False
            path = self.design_path
            if path is None or save_as:
                path, _ = QFileDialog.getSaveFileName(self, 'Save design outside the source package',
                    str(self.workspace() / 'design.mapedit.json'), 'Map designs (*.mapedit.json)')
            if not path:
                return False
            try:
                save_design(self.session.snapshot(), path)
                self.design_path = Path(path)
                self.dirty = False
                self.setWindowTitle(f'Orthomosaic Map Editor — {self.design_path.name}')
                self.statusBar().showMessage(f'Design saved: {path}')
                return True
            except Exception as exc:
                self.error(exc)
                return False

        def fit_page(self):
            if self.session:
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
                self.overview_crs.setCurrentIndex(self.overview_crs.findData(self.session.design.options['overview_crs']))
                self.auto_multiplier.setValue(self.session.design.options['auto_scale_multiplier'])
                self.grid_font_size.setValue(self.session.design.options['grid_font_size'])
                for key, control in self.dpi.items():
                    control.setValue(self.session.design.options[key])
            finally:
                self.loading = False

        def change_text(self, *_args):
            if self.loading or not self.session:
                return
            try:
                style = {'family': self.font_family.currentFont().family(), 'size': self.font_size.value(),
                         'bold': self.bold.isChecked(), 'color': self.font_color}
                self.session.set_text(self.text_id(), self.text_value.toPlainText(), style)
                self.mark_dirty()
                self.view.viewport().update()
            except Exception as exc:
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
            for key, control in self.dpi.items():
                self.session.design.options[key] = control.value()
            self.mark_dirty()

        def change_grid_font_size(self, size):
            self.perform(lambda: self.session.set_grid_font_size(size))

        def change_paper(self, paper):
            if self.loading or not self.session:
                return
            self.perform(lambda: self.session.set_paper(paper))
            self.view.setCurrentLayout(self.session.ctx.layout)
            self.session._previous_ctx = None
            self.fit_page()

        def change_overview_crs(self, *_args):
            if self.loading or not self.session:
                return
            self.view.cancel_drag()
            self.perform(lambda: self.session.set_overview_crs(self.overview_crs.currentData()))
            self.view.setCurrentLayout(self.session.ctx.layout)
            self.session._previous_ctx = None
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
                attempt = self.job.start(self.session.snapshot(), self.workspace() / 'exports',
                                         sys.executable, REPO_ROOT / 'map_editor.py')
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
            except Exception as exc:
                self.error(exc)
            process.deleteLater()

        def cancel_export(self):
            process = self.job.process
            self.job.cancel()
            if process is not None:
                QTimer.singleShot(5000, lambda: process.kill() if self.job.process is process else None)

        def closeEvent(self, event):
            if self.job.busy:
                answer = QMessageBox.question(self, 'Export running', 'Cancel the running export?')
                if answer == QMessageBox.Yes:
                    self.cancel_export()
                event.ignore()
            elif self.confirm_discard():
                event.accept()
            else:
                event.ignore()

    return MapEditorWindow
