"""Lazy Qt generator page. Discovery and packaging run only in the owned helper."""
from pathlib import Path
import sys

from .editor_generator import GeneratorJob, load_root_preferences, read_object
from .editor_model import REPO_ROOT, atomic_json
from .editor_session import export_qt_platform


def create_generator_page(owner):
    from qgis.PyQt.QtCore import QProcess, QProcessEnvironment, QTimer
    from qgis.PyQt.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
        QLineEdit, QPlainTextEdit, QListWidget, QComboBox, QFileDialog, QProgressBar,
        QSplitter, QScrollArea,
    )

    class GeneratorPage(QWidget):
        def __init__(self):
            super().__init__()
            self.inputs, self.findings = [], []
            self.filling = False
            self.generated_package = None
            self.workspace = REPO_ROOT / 'exports/map_editor/generator'
            self.job = GeneratorJob(self.new_process, owner.helper_gate)
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel('GENERATE MAP — existing processed surveys only; no pipeline processing'))
            split = QSplitter()
            layout.addWidget(split)
            controls = QWidget()
            left = QVBoxLayout(controls)
            self.query = QLineEdit()
            self.query.setPlaceholderText('Survey ID, e.g. AH-026132')
            left.addWidget(self.query)
            row = QHBoxLayout()
            self.edit_buttons = []

            def button(label, handler, target):
                control = QPushButton(label)
                control.clicked.connect(handler)
                target.addWidget(control)
                self.edit_buttons.append(control)
                return control

            button('Add ID', self.add_id, row)
            button('Add survey folder…', self.add_folder, row)
            left.addLayout(row)
            self.surveys = QListWidget()
            self.surveys.currentRowChanged.connect(self.show_candidates)
            left.addWidget(self.surveys)
            row = QHBoxLayout()
            button('Remove', self.remove, row)
            button('Move up', lambda: self.move(-1), row)
            button('Move down', lambda: self.move(1), row)
            left.addLayout(row)
            left.addWidget(QLabel('Survey roots, in search order (one per line). Used for ID lookup only.'))
            self.roots = QPlainTextEdit()
            self.roots.setMaximumHeight(95)
            try:
                self.roots.setPlainText('\n'.join(load_root_preferences(self.workspace / 'roots.json')))
            except Exception as exc:
                owner.error(exc)
            self.roots.textChanged.connect(self.invalidate)
            left.addWidget(self.roots)
            button('Add survey root…', self.add_root, left)
            self.check = button('Check Inputs', lambda: self.start('discover'), left)
            left.addWidget(QLabel('Check Inputs reads only the selected storage. No automatic drive scans.'))
            split.addWidget(controls)

            details = QWidget()
            right = QVBoxLayout(details)
            form = QFormLayout()
            self.folder_choice, self.boundary_choice, self.raster_choice = QComboBox(), QComboBox(), QComboBox()
            for choice in (self.folder_choice, self.boundary_choice, self.raster_choice):
                choice.setMaximumWidth(600)
            self.folder_choice.currentIndexChanged.connect(self.choose_folder)
            self.boundary_choice.currentIndexChanged.connect(self.choose_artifact)
            self.raster_choice.currentIndexChanged.connect(self.choose_artifact)
            form.addRow('Resolved survey folder', self.folder_choice)
            form.addRow('Boundary candidate', self.boundary_choice)
            form.addRow('TIFF candidate (Task 4 preferred)', self.raster_choice)
            right.addLayout(form)
            self.paths = QPlainTextEdit()
            self.paths.setReadOnly(True)
            self.paths.setMaximumHeight(160)
            right.addWidget(self.paths)
            form = QFormLayout()
            self.map_name, self.title, self.location = QLineEdit(), QLineEdit(), QLineEdit()
            self.map_name.setPlaceholderText('e.g. sentrom-map')
            self.output = QLineEdit(str(REPO_ROOT / 'exports/maps'))
            self.map_type = QComboBox()
            self.map_type.addItem('Orthomosaic + boundary', 'orthomosaic')
            self.map_type.addItem('Boundary only', 'boundary')
            for label, control in [('Map name', self.map_name), ('Title', self.title), ('Location (optional)', self.location),
                                    ('Output directory', self.output), ('Map type', self.map_type)]:
                form.addRow(label, control)
            right.addLayout(form)
            button('Choose output directory…', self.choose_output, right)
            right.addWidget(QLabel('TIFFs stay in their original storage. Keep that storage available when editing/exporting.'))
            self.generate = button('Generate and open in editor', lambda: self.start('generate'), right)
            self.open_generated = QPushButton('Open Generated Map')
            self.open_generated.clicked.connect(self.handoff)
            right.addWidget(self.open_generated)
            split.addWidget(details)
            self.busy_bar = QProgressBar()
            self.busy_bar.setRange(0, 0)
            layout.addWidget(self.busy_bar)
            self.cancel_button = QPushButton('Cancel operation')
            self.cancel_button.clicked.connect(self.cancel)
            layout.addWidget(self.cancel_button)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            layout.addWidget(self.log)
            self.refresh_busy()

        def roots_values(self):
            return [line.strip() for line in self.roots.toPlainText().splitlines() if line.strip()]

        def invalidate(self):
            self.findings = []
            for row in self.inputs:
                for key in ('folder', 'boundary', 'orthomosaic'):
                    row.pop(key, None)
            self.show_candidates(self.surveys.currentRow())

        def add_query(self, query):
            if self.job.busy or not query.strip():
                return
            self.inputs.append({'query': query.strip()})
            self.refresh_rows(len(self.inputs) - 1)

        def add_id(self):
            self.add_query(self.query.text())

        def add_folder(self):
            path = QFileDialog.getExistingDirectory(self, 'Choose AH-026xxx or rgb folder')
            if path:
                self.add_query(path)

        def add_root(self):
            path = QFileDialog.getExistingDirectory(self, 'Choose an explicit survey root')
            if path:
                self.roots.setPlainText('\n'.join(dict.fromkeys(self.roots_values() + [path])))

        def choose_output(self):
            path = QFileDialog.getExistingDirectory(self, 'Choose package output directory', self.output.text())
            if path:
                self.output.setText(path)

        def refresh_rows(self, index):
            self.surveys.clear()
            self.surveys.addItems([r['query'] for r in self.inputs])
            self.invalidate()
            self.surveys.setCurrentRow(index)

        def remove(self):
            index = self.surveys.currentRow()
            if not self.job.busy and 0 <= index < len(self.inputs):
                self.inputs.pop(index)
                self.refresh_rows(min(index, len(self.inputs) - 1))

        def move(self, delta):
            index = self.surveys.currentRow()
            target = index + delta
            if not self.job.busy and 0 <= index < len(self.inputs) and 0 <= target < len(self.inputs):
                self.inputs[index], self.inputs[target] = self.inputs[target], self.inputs[index]
                self.refresh_rows(target)

        def show_candidates(self, index):
            self.filling = True
            self.folder_choice.clear()
            self.boundary_choice.clear()
            self.raster_choice.clear()
            if 0 <= index < len(self.findings):
                matches = self.findings[index]['matches']
                if len(matches) != 1:
                    self.folder_choice.addItem('Choose a survey folder explicitly…', None)
                for match in matches:
                    self.folder_choice.addItem(match['folder'], match)
                chosen = self.inputs[index].get('folder')
                for offset, match in enumerate(matches):
                    if match['folder'] == chosen:
                        self.folder_choice.setCurrentIndex(offset + (len(matches) != 1))
            self.filling = False
            self.choose_folder()

        def choose_folder(self, *_args):
            if self.filling:
                return
            self.filling = True
            self.boundary_choice.clear()
            self.raster_choice.clear()
            match = self.folder_choice.currentData()
            index = self.surveys.currentRow()
            saved = dict(self.inputs[index]) if 0 <= index < len(self.inputs) else {}
            if 0 <= index < len(self.inputs):
                self.inputs[index].update(folder=match['folder'] if match else None, boundary=None, orthomosaic=None)
            if match:
                for path in match['boundaries']:
                    self.boundary_choice.addItem(path, path)
                for path in match['orthomosaics']:
                    self.raster_choice.addItem(path, path)
                if saved.get('folder') == match['folder']:
                    for control, values, key in ((self.boundary_choice, match['boundaries'], 'boundary'),
                                                  (self.raster_choice, match['orthomosaics'], 'orthomosaic')):
                        if saved.get(key) in values:
                            control.setCurrentIndex(values.index(saved[key]))
            self.filling = False
            self.choose_artifact()

        def choose_artifact(self, *_args):
            index = self.surveys.currentRow()
            if self.filling or not 0 <= index < len(self.inputs):
                return
            row = self.inputs[index]
            row.update(boundary=self.boundary_choice.currentData(), orthomosaic=self.raster_choice.currentData())
            match = self.folder_choice.currentData()
            self.paths.setPlainText('\n'.join([f'Folder: {row.get("folder") or "Choose / Check Inputs"}',
                f'Boundary: {row.get("boundary") or "MISSING"}', f'TIFF: {row.get("orthomosaic") or "MISSING"}'] +
                (match['warnings'] if match else [])))

        def refresh_busy(self):
            busy = owner.helper_gate.owner is not None
            for control in self.edit_buttons + [self.surveys, self.roots, self.query,
                    self.folder_choice, self.boundary_choice, self.raster_choice, self.map_type,
                    self.map_name, self.title, self.location, self.output]:
                control.setEnabled(not busy)
            self.busy_bar.setVisible(self.job.busy)
            self.cancel_button.setEnabled(self.job.busy)
            self.open_generated.setEnabled(self.generated_package is not None and not busy)

        def start(self, operation):
            try:
                request = {'operation': operation, 'inputs': self.inputs, 'roots': self.roots_values(),
                    'map_name': self.map_name.text(), 'title': self.title.text(), 'location': self.location.text(),
                    'output_root': self.output.text(), 'map_type': self.map_type.currentData()}
                if not self.inputs:
                    raise ValueError('Add at least one processed survey')
                if operation == 'generate' and (not self.findings or any(not r.get('folder') or not r.get('boundary') or
                        (request['map_type'] == 'orthomosaic' and not r.get('orthomosaic')) for r in self.inputs)):
                    raise ValueError('Check Inputs and choose each survey folder, boundary, and required TIFF first')
                attempt = self.job.start(request, self.workspace / 'attempts', sys.executable, REPO_ROOT / 'map_editor.py')
                self.log.appendPlainText(f'{operation.title()} started: {attempt}')
                atomic_json(self.workspace / 'roots.json', {'version': 1, 'roots': request['roots']})
            except Exception as exc:
                owner.error(exc)
            owner.update_actions()

        def new_process(self):
            process = QProcess(owner)
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert('QT_QPA_PLATFORM', export_qt_platform(sys.platform))
            environment.remove('VIRTUAL_ENV')
            process.setProcessEnvironment(environment)
            process.setProcessChannelMode(QProcess.MergedChannels)
            process.readyReadStandardOutput.connect(self.read_log)
            process.finished.connect(self.finished)
            process.errorOccurred.connect(self.process_error)
            return process

        def read_log(self):
            if self.job.process is not None:
                data = bytes(self.job.process.readAllStandardOutput()).decode('utf-8', errors='replace')
                if data:
                    self.log.appendPlainText(data.rstrip())
                    owner.statusBar().showMessage(data.splitlines()[-1])
                    with (self.job.attempt / 'generator.log').open('a', encoding='utf-8') as stream:
                        stream.write(data)

        def process_error(self, error):
            if error == QProcess.FailedToStart:
                if self.job.process is not None:
                    self.log.appendPlainText(f'Helper could not start: {self.job.process.errorString()}')
                self.finished(-1)

        def finished(self, code, *_args):
            process = self.job.process
            if process is None:
                return
            try:
                self.read_log()
                state = self.job.finish(code)
                self.log.appendPlainText(f'{state["status"]}: {self.job.attempt}' +
                                         (f'\n{state["error"]}' if state.get('error') else ''))
                if state['status'] == 'complete':
                    result = read_object(self.job.attempt / 'result.json')
                    if self.job.request['operation'] == 'discover':
                        self.findings = result['surveys']
                        for row, found in zip(self.inputs, self.findings):
                            if len(found['matches']) == 1:
                                match = found['matches'][0]
                                row.update(folder=match['folder'], boundary=next(iter(match['boundaries']), None),
                                           orthomosaic=next(iter(match['orthomosaics']), None))
                        self.surveys.setCurrentRow(0)
                        self.show_candidates(0)
                    else:
                        self.generated_package = Path(result['package'])
                        self.log.appendPlainText(f'Generated package: {self.generated_package}')
                        self.handoff()
            except Exception as exc:
                owner.error(exc)
            finally:
                process.deleteLater()
                owner.update_actions()

        def handoff(self):
            if self.generated_package is None or owner.helper_gate.owner is not None:
                return
            if not owner.confirm_discard():
                self.log.appendPlainText(f'Editor unchanged. Use Open Generated Map later: {self.generated_package}')
                return
            try:
                owner.load_package(self.generated_package)
            except Exception as exc:
                self.log.appendPlainText(f'Package completed, but editor loading failed: {self.generated_package}\n{exc}')
                owner.error(exc)

        def cancel(self):
            process = self.job.process
            self.job.cancel()
            if process is not None:
                self.log.appendPlainText('Cancellation requested; waiting for the owned helper to stop...')
                QTimer.singleShot(5000, lambda: process.kill() if self.job.process is process else None)

    # The form stays usable on smaller displays through normal Qt scrolling.
    page = GeneratorPage()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    page.container = scroll
    return page
