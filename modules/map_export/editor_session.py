"""Editor actions against the same QGIS layout used for final export."""
from __future__ import annotations

import copy
from pathlib import Path

from .editor_model import MAP_IDS, TEXT_IDS, EditorDesign, parse_bounds


def export_qt_platform(platform_name):
    # Qt 5's offscreen plugin on Windows uses a separate FreeType font database,
    # not the native Windows font database used by the editor's visible window.
    return 'windows' if platform_name == 'win32' else 'offscreen'


class EditorSession:
    def __init__(self, design: EditorDesign, renderer=None):
        if renderer is None:
            from . import qgis_print_exporter as renderer
        self.renderer = renderer
        self.design = copy.deepcopy(design).validate()
        self.ctx = None
        self.rebuild()
        self.original_maps = copy.deepcopy(self.snapshot().maps)

    def rebuild(self):
        self.design.validate()
        candidate = self.renderer.build_qgis_print_layout(
            package_dir=self.design.package_dir, render_options=copy.deepcopy(self.design.options),
            live_insets=True, map_states=copy.deepcopy(self.design.maps),
            text_values=self.design.texts, text_styles=self.design.text_styles,
            map_styles=copy.deepcopy(self.design.map_styles), grid_style=copy.deepcopy(self.design.grid_style),
        )
        for key in MAP_IDS:
            if candidate.layout.itemById(key) is None:
                raise ValueError(f'The template is missing a required map: {key}')
        self._previous_ctx = self.ctx  # Keep the old scene alive until its view is detached.
        self.ctx = candidate
        self.ctx.layout.renderContext().setDpi(96)

    def snapshot(self):
        result = copy.deepcopy(self.design)
        for key in MAP_IDS:
            item = self.ctx.layout.itemById(key)
            extent = item.extent()
            result.maps[key] = {
                'crs': item.crs().authid(),
                'extent': [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
            }
        for key in TEXT_IDS:
            item = self.ctx.layout.itemById(key)
            if item is None:
                raise ValueError(f'The template is missing an editable label: {key}')
            font = item.font()
            result.texts[key] = item.text()
            result.text_styles[key] = {'family': font.family(), 'size': font.pointSizeF(),
                                       'bold': font.bold(), 'color': item.fontColor().name()}
        return result.validate()

    def _map(self, key):
        if key not in MAP_IDS:
            raise ValueError('Unknown map')
        return self.ctx.layout.itemById(key)

    def changed_map(self, key):
        if key == 'map_frame':
            self.renderer.refresh_main_map_furniture(self.ctx)
        self._map(key).refresh()

    def set_extent(self, key, coordinates):
        item = self._map(key)
        item.zoomToExtent(self.ctx.qgis.QgsRectangle(*coordinates))
        self.changed_map(key)

    def pan(self, key, dx_fraction, dy_fraction):
        e = self._map(key).extent()
        dx, dy = -dx_fraction * e.width(), dy_fraction * e.height()
        self.set_extent(key, [e.xMinimum() + dx, e.yMinimum() + dy,
                              e.xMaximum() + dx, e.yMaximum() + dy])

    def zoom(self, key, factor):
        from .editor_model import finite_number
        factor = finite_number(factor, strictly_positive=True)
        e = self._map(key).extent()
        cx, cy = (e.xMinimum() + e.xMaximum()) / 2, (e.yMinimum() + e.yMaximum()) / 2
        w, h = e.width() * factor / 2, e.height() * factor / 2
        self.set_extent(key, [cx - w, cy - h, cx + w, cy + h])

    def set_scale(self, key, scale):
        from .editor_model import finite_number
        self._map(key).setScale(finite_number(scale, strictly_positive=True))
        self.changed_map(key)

    def set_bounds(self, key, value):
        bounds = parse_bounds(value)
        if bounds is None:
            raise ValueError('Enter four WGS 84 bounds')
        extent = self.renderer._prepare_fixed_extent(
            project=self.ctx.project, wgs84_bounds=bounds, target_crs=self._map(key).crs(),
            QgsCoordinateReferenceSystem=self.ctx.qgis.QgsCoordinateReferenceSystem,
            frame_rect=self._map(key).rect(),
        )
        self._map(key).zoomToExtent(extent)
        self.changed_map(key)

    def geographic_bounds(self, key):
        q = self.ctx.qgis
        transform = q.QgsCoordinateTransform(
            self._map(key).crs(), q.QgsCoordinateReferenceSystem('EPSG:4326'), self.ctx.project,
        )
        e = transform.transformBoundingBox(self._map(key).extent())
        return ','.join(f'{number:.7f}' for number in
                        (e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum()))

    def reset_map(self, key):
        state = self.original_maps[key]
        item = self._map(key)
        if state['crs'] == item.crs().authid():
            self.set_extent(key, state['extent'])
        else:
            extent = self.renderer._saved_map_extent(self.ctx, key, target_crs=item.crs(), state=state)
            item.zoomToExtent(extent)
            self.changed_map(key)

    def auto_fit(self):
        self.ctx.map_states = {}
        self.ctx.map_scale = None
        self.ctx.render_options['manual_scale'] = None
        self.design.options['manual_scale'] = None
        self.renderer._configure_main_map(self.ctx)
        self.renderer.refresh_main_map_furniture(self.ctx)

    def set_grid_font_size(self, size):
        from .editor_model import validate_options
        options = validate_options(dict(self.design.options, grid_font_size=size))
        self.design.options = options
        self.ctx.render_options['grid_font_size'] = options['grid_font_size']
        self.renderer.refresh_main_map_furniture(self.ctx)

    def set_text(self, key, value, style):
        trial = copy.deepcopy(self.design)
        trial.texts[key], trial.text_styles[key] = value, dict(style)
        trial.validate()
        self.renderer.apply_editor_text(self.ctx, {key: value}, {key: style})
        self.design = trial

    def set_overlay(self, key, mode):
        if key not in ('map_inset', 'map_inset_2') or mode not in ('boundary', 'pin'):
            raise ValueError('Choose an inset and boundary or pin')
        item = self._map(key)
        basemap = (self.ctx.frame_basemaps[key] if hasattr(self.ctx, 'frame_basemaps')
                   else self.ctx.basemap_layer)
        previous = [layer for layer in item.layers() if layer is not basemap]
        layer = self.renderer._configure_inset_boundary_overlay(
            project=self.ctx.project, inset_item=item, boundary_layer=self.ctx.merged_layer,
            target_crs=item.crs(), extent=item.extent(), overlay_mode=mode,
            style=self.design.map_styles[key],
        )
        item.setLayers([layer] + ([basemap] if basemap is not None else []))
        if hasattr(self.ctx, 'frame_overlays'):
            self.ctx.frame_overlays[key] = layer
        item.setBackgroundEnabled(True)
        for old in previous:
            if old is not self.ctx.merged_layer:
                self.ctx.project.removeMapLayer(old.id())
        prefix = 'boundary' if key == 'map_inset' else 'overview'
        self.design.options[prefix + '_overlay'] = mode
        self.ctx.render_options[prefix + '_overlay'] = mode
        item.refresh()

    def set_paper(self, paper):
        trial = self.snapshot()
        trial.options['paper_size'] = paper
        self.design = trial.validate()
        self.rebuild()

    def set_overview_crs(self, crs):
        self.set_inset_crs('map_inset_2', crs)

    def set_inset_crs(self, key, crs):
        if key not in ('map_inset', 'map_inset_2'):
            raise ValueError('Only inset CRS can be changed')
        trial = self.snapshot()
        trial.options['boundary_crs' if key == 'map_inset' else 'overview_crs'] = crs
        self.design = trial.validate()
        self.rebuild()

    def set_map_style(self, key, style):
        from .editor_model import validate_style
        self._map(key)
        style = validate_style(style)
        self.renderer.apply_map_style(self.ctx, key, style)
        self.design.map_styles[key] = style

    def set_grid_style(self, style):
        from .editor_model import validate_style
        style = validate_style(style, grid=True)
        self.ctx.grid_style = style
        self.renderer.refresh_main_map_furniture(self.ctx)
        self.design.grid_style = style

    def _apply_design(self, design):
        trial = copy.deepcopy(design).validate()
        if trial.package_dir != self.design.package_dir:
            raise ValueError('Cannot restore a different map package')
        old = dict(self.ctx.render_options)
        if any(old[key] != trial.options[key] for key in ('paper_size', 'boundary_crs', 'overview_crs')):
            self.design = trial
            self.rebuild()
            return
        current = self.snapshot()
        self.design = trial
        for key, prefix in (('map_inset', 'boundary'), ('map_inset_2', 'overview')):
            if old[prefix + '_overlay'] != trial.options[prefix + '_overlay']:
                self.set_overlay(key, trial.options[prefix + '_overlay'])
        self.ctx.render_options = copy.deepcopy(trial.options)
        self.ctx.grid_style = copy.deepcopy(trial.grid_style)
        for key in MAP_IDS:
            self.set_map_style(key, trial.map_styles[key])
            if key in trial.maps and current.maps.get(key) != trial.maps[key]:
                self.set_extent(key, trial.maps[key]['extent'])
        self.renderer.apply_editor_text(self.ctx, trial.texts, trial.text_styles)
        self.renderer.refresh_main_map_furniture(self.ctx)
        self.design = trial

    def restore(self, design, *, force=False):
        """Restore metadata incrementally, rolling a failed restore back to its prior state."""
        before = self.snapshot()
        try:
            if force:
                self.replace_design(design)
            else:
                self._apply_design(design)
        except Exception as exc:
            try:
                self.replace_design(before)
            except Exception as rollback_exc:
                raise RuntimeError(f'Design restore failed ({exc}); rollback failed: {rollback_exc}') from exc
            raise

    def replace_design(self, design):
        if design.package_dir != self.design.package_dir:
            raise ValueError('Cannot replace a different map package')
        self.design = copy.deepcopy(design).validate()
        self.rebuild()

    def apply_preset(self, preset, key):
        from .editor_workflow import validate_preset
        preset = validate_preset(preset)
        if preset['kind'] == 'location':
            if key not in ('map_inset', 'map_inset_2'):
                raise ValueError('Choose an inset for a location preset')
            if self.design.options['boundary_crs' if key == 'map_inset' else 'overview_crs'] != preset['crs']:
                self.set_inset_crs(key, preset['crs'])
            self.set_bounds(key, preset['bounds'])
            self.set_overlay(key, preset['mode'])
            prefix, label = ('boundary', 'label_inset') if key == 'map_inset' else ('overview', 'label_inset_2')
            self.design.options[prefix + '_label'] = preset['label']
            self.design.options[prefix + '_bounds'] = dict(preset['bounds'])
            item = self.ctx.layout.itemById(label)
            item.setText(preset['label'])
            item.refresh()
        else:
            trial = self.snapshot()
            trial.options.update(preset['options'])
            trial.map_styles, trial.grid_style = preset['map_styles'], preset['grid_style']
            trial.text_styles = preset['text_styles']
            self.restore(trial)


def run_snapshot_export(snapshot_path: Path, renderer=None):
    """Called only in the editor-owned helper process; source package stays read-only."""
    from .editor_model import atomic_json, load_design, require_separate_workspace
    import json
    snapshot = Path(snapshot_path).resolve()
    attempt = snapshot.parent
    marker = attempt / 'attempt.json'
    receipt = json.loads(marker.read_text(encoding='utf-8'))
    if snapshot.name != 'snapshot.mapedit.json' or receipt != {'version': 1, 'status': 'incomplete'}:
        raise ValueError('Export requires a new editor-owned incomplete attempt')
    design = load_design(snapshot)
    require_separate_workspace(design.package_dir, attempt)
    if any((attempt / name).exists() for name in ('print_map.pdf', 'preview.png', 'map_metadata.json', 'map_inset.png', 'map_inset_2.png')):
        raise FileExistsError('Export attempt already contains outputs; create a new attempt')
    try:
        if renderer is None:
            from . import qgis_print_exporter as renderer
        print('Building final layout from design snapshot...', flush=True)
        ctx = renderer.build_qgis_print_layout(
            package_dir=design.package_dir, render_options=design.options, live_insets=False,
            map_states=design.maps, text_values=design.texts, text_styles=design.text_styles,
            output_dir=attempt,
            map_styles=design.map_styles, grid_style=design.grid_style,
        )
        ctx.metadata_path = attempt / 'map_metadata.json'
        ctx.editor_state = {'maps': design.maps, 'texts': design.texts, 'text_styles': design.text_styles,
                            'map_styles': design.map_styles, 'grid_style': design.grid_style}
        result = renderer._export_print_outputs(ctx)
        atomic_json(marker, {'version': 1, 'status': 'complete', 'outputs': result})
        print(f'Export complete: {attempt}', flush=True)
        return result
    except Exception as exc:
        atomic_json(marker, {'version': 1, 'status': 'failed', 'error': str(exc)})
        raise


class ExportJob:
    """Own one injected process; never terminate another application's processes."""
    def __init__(self, process_factory, gate=None):
        self.process_factory = process_factory
        self.gate = gate
        self.process = None
        self.attempt = None
        self.cancel_requested = False

    @property
    def busy(self):
        return self.process is not None

    def start(self, design, workspace, program, entrypoint):
        from .editor_model import create_export_attempt
        if self.busy:
            raise RuntimeError('An export is already running')
        if self.gate is not None:
            self.gate.acquire(self)
        try:
            self.attempt = create_export_attempt(copy.deepcopy(design), workspace)
            self.cancel_requested = False
            self.process = self.process_factory()
            self.process.setProgram(str(program))
            self.process.setArguments([str(entrypoint), '--export-snapshot', str(self.attempt / 'snapshot.mapedit.json')])
            self.process.start()
        except Exception:
            if self.process is not None:
                self.finish(-1)
            elif self.gate is not None:
                self.gate.release(self)
            raise
        return self.attempt

    def cancel(self):
        if self.process is not None:
            self.cancel_requested = True
            self.process.terminate()

    def finish(self, exit_code):
        import json
        from .editor_model import atomic_json
        if self.process is None:
            return
        receipt = self.attempt / 'attempt.json'
        try:
            state = json.loads(receipt.read_text(encoding='utf-8'))
            if state['status'] != 'complete':
                status = 'cancelled' if self.cancel_requested else 'failed'
                state.update(status=status, exit_code=exit_code)
                atomic_json(receipt, state)
            return state
        finally:
            self.process = None
            if self.gate is not None:
                self.gate.release(self)
