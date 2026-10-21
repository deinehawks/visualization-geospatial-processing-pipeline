"""Qt/QGIS-free map-editor settings, literal BAT import, and design persistence."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAP_IDS = ('map_frame', 'map_inset', 'map_inset_2')
TEXT_IDS = ('map_title', 'map_location', 'label_inset', 'label_inset_2', 'map_disclaimer')
DEFAULT_OPTIONS = {
    'paper_size': 'A3', 'preview_dpi': 300, 'pdf_dpi': 450, 'inset_dpi': 800,
    'grid_font_size': 6.0,
    'main_buffer': 0.08, 'auto_scale_multiplier': 2.5, 'manual_scale': None,
    'boundary_buffer': 0.35, 'boundary_label': None, 'boundary_bounds': None,
    'boundary_zoom': 1.0, 'boundary_overlay': 'pin',
    'overview_label': 'Davao City',
    'overview_bounds': {'min_lon': 125.3063, 'min_lat': 6.8710,
                        'max_lon': 125.9308, 'max_lat': 7.3282},
    'overview_zoom': 1.0, 'overview_overlay': 'boundary',
    'overview_crs': 'EPSG:4326',
    'boundary_crs': 'project',
}
DEFAULT_MAP_STYLE = {
    'basemap_opacity': 1.0, 'boundary_stroke': '#991b1b', 'boundary_fill': '#fca5a5',
    'boundary_width_mm': 0.55, 'boundary_opacity': 0.32,
    'pin_color': '#c62828', 'pin_size_mm': 4.0,
}
DEFAULT_GRID_STYLE = {
    'lines_visible': True, 'labels_visible': True, 'color': '#5a5a5a',
    'opacity': 0.6, 'width_mm': 0.25,
}


def validate_style(values, *, grid=False):
    defaults = DEFAULT_GRID_STYLE if grid else DEFAULT_MAP_STYLE
    if not isinstance(values, dict) or set(values) - set(defaults):
        raise ValueError('Unknown map/grid style setting')
    result = dict(defaults, **values)
    ranges = {'basemap_opacity': (0, 1), 'boundary_opacity': (0, 1),
              'boundary_width_mm': (0.05, 5), 'pin_size_mm': (1, 20),
              'opacity': (0, 1), 'width_mm': (0.05, 2)}
    for key, value in result.items():
        if key.endswith('_visible'):
            if not isinstance(value, bool):
                raise ValueError('Visibility must be true or false')
        elif key in ranges:
            low, high = ranges[key]
            result[key] = finite_number(value, minimum=low)
            if result[key] > high:
                raise ValueError(f'{key} must be from {low} to {high}')
        elif not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            raise ValueError('Style colors must be #RRGGBB')
    return result


def validate_map_styles(values):
    if not isinstance(values, dict) or set(values) - set(MAP_IDS):
        raise ValueError('Invalid style map ID')
    return {key: validate_style(values.get(key, {})) for key in MAP_IDS}
BAT_OPTIONS = {
    'MAP_EXPORT_PAPER_SIZE': 'paper_size', 'MAP_EXPORT_PREVIEW_DPI': 'preview_dpi',
    'MAP_EXPORT_PDF_DPI': 'pdf_dpi', 'MAP_EXPORT_INSET_DPI': 'inset_dpi',
    'MAP_EXPORT_MAIN_BUFFER': 'main_buffer',
    'MAP_EXPORT_AUTO_SCALE_MULTIPLIER': 'auto_scale_multiplier',
    'MAP_EXPORT_BOUNDARY_INSET_BUFFER': 'boundary_buffer',
    'MAP_EXPORT_BOUNDARY_INSET_LABEL': 'boundary_label',
    'MAP_EXPORT_BOUNDARY_INSET_BOUNDS': 'boundary_bounds',
    'MAP_EXPORT_BOUNDARY_INSET_ZOOM_OUT': 'boundary_zoom',
    'MAP_EXPORT_BOUNDARY_INSET_OVERLAY': 'boundary_overlay',
    'MAP_EXPORT_OVERVIEW_LABEL': 'overview_label',
    'MAP_EXPORT_OVERVIEW_BOUNDS': 'overview_bounds',
    'MAP_EXPORT_OVERVIEW_ZOOM_OUT': 'overview_zoom',
    'MAP_EXPORT_OVERVIEW_OVERLAY': 'overview_overlay',
    'MAP_EXPORT_OVERVIEW_CRS': 'overview_crs',
}
BAT_TEXT = {'TITLE': 'map_title', 'LOCATION': 'map_location',
            'DISCLAIMER': 'map_disclaimer'}


def finite_number(value, *, minimum=0.0, strictly_positive=False):
    if isinstance(value, bool):
        raise ValueError('Expected a number')
    result = float(value)
    if not math.isfinite(result) or result < minimum or (strictly_positive and result <= 0):
        raise ValueError(f'Expected a finite number >= {minimum}')
    return result


def parse_bounds(value):
    if value is None or value == '':
        return None
    if isinstance(value, str):
        value = dict(zip(('min_lon', 'min_lat', 'max_lon', 'max_lat'), value.split(','))) if len(value.split(',')) == 4 else {}
    if not isinstance(value, dict) or set(value) != {'min_lon', 'min_lat', 'max_lon', 'max_lat'}:
        raise ValueError('Bounds must be min_lon,min_lat,max_lon,max_lat')
    result = {key: finite_number(number, minimum=-180) for key, number in value.items()}
    if not (-180 <= result['min_lon'] < result['max_lon'] <= 180
            and -90 <= result['min_lat'] < result['max_lat'] <= 90):
        raise ValueError('Invalid longitude/latitude bounds or coordinate order')
    return result


def validate_options(values):
    unknown = set(values) - set(DEFAULT_OPTIONS)
    if unknown:
        raise ValueError(f'Unknown render settings: {sorted(unknown)}')
    result = copy.deepcopy(DEFAULT_OPTIONS)
    result.update(copy.deepcopy(values))
    result['paper_size'] = str(result['paper_size']).upper()
    if result['paper_size'] not in ('A4', 'A3'):
        raise ValueError('Paper size must be A4 or A3')
    for key in ('overview_crs', 'boundary_crs'):
        crs = str(result[key]).strip()
        result[key] = 'project' if crs.lower() == 'project' else crs.upper()
        if result[key] not in ('project', 'EPSG:4326'):
            raise ValueError('Inset CRS must be project or EPSG:4326')
    result['grid_font_size'] = finite_number(result['grid_font_size'], minimum=1)
    if result['grid_font_size'] > 72:
        raise ValueError('Coordinate label font size must be from 1 to 72 pt')
    for key in ('preview_dpi', 'pdf_dpi', 'inset_dpi'):
        number = finite_number(result[key], minimum=1)
        if not number.is_integer() or number > 2400:
            raise ValueError(f'{key} must be a whole number from 1 to 2400')
        result[key] = int(number)
    for key in ('main_buffer', 'boundary_buffer', 'auto_scale_multiplier', 'boundary_zoom', 'overview_zoom'):
        result[key] = finite_number(result[key], minimum=0 if key.endswith('buffer') else 1)
    if result['manual_scale'] is not None:
        result['manual_scale'] = finite_number(result['manual_scale'], strictly_positive=True)
    for prefix in ('boundary', 'overview'):
        result[prefix + '_bounds'] = parse_bounds(result[prefix + '_bounds'])
        mode = str(result[prefix + '_overlay']).lower()
        if mode not in ('boundary', 'pin'):
            raise ValueError('Inset overlay must be boundary or pin')
        result[prefix + '_overlay'] = mode
        label = result[prefix + '_label']
        if label is not None and not isinstance(label, str):
            raise ValueError('Inset labels must be text')
    if result['overview_bounds'] is None:
        raise ValueError('The overview requires bounds')
    return result


@dataclass
class EditorDesign:
    package_dir: Path
    options: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_OPTIONS))
    maps: dict = field(default_factory=dict)
    texts: dict = field(default_factory=dict)
    text_styles: dict = field(default_factory=dict)
    map_styles: dict = field(default_factory=dict)
    grid_style: dict = field(default_factory=dict)
    source_version: int = field(default=2, compare=False)

    def validate(self):
        self.options = validate_options(self.options)
        self.map_styles = validate_map_styles(self.map_styles)
        self.grid_style = validate_style(self.grid_style, grid=True)
        for key, state in self.maps.items():
            if key not in MAP_IDS or not isinstance(state.get('crs'), str) or not re.fullmatch(r'EPSG:\d+', state['crs']):
                raise ValueError('Invalid map ID or extent CRS')
            coordinates = state.get('extent', [])
            if len(coordinates) != 4:
                raise ValueError('A map extent requires four coordinates')
            x1, y1, x2, y2 = (finite_number(x, minimum=-1e15) for x in coordinates)
            if x1 >= x2 or y1 >= y2:
                raise ValueError('Invalid map extent')
            state['extent'] = [x1, y1, x2, y2]
        if set(self.texts) - set(TEXT_IDS) or any(not isinstance(v, str) for v in self.texts.values()):
            raise ValueError('Invalid editable label values')
        for key, style in self.text_styles.items():
            if key not in TEXT_IDS or set(style) != {'family', 'size', 'bold', 'color'}:
                raise ValueError('Invalid text style')
            if not isinstance(style['family'], str) or not isinstance(style['bold'], bool):
                raise ValueError('Invalid font family or bold setting')
            finite_number(style['size'], minimum=1)
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(style['color'])):
                raise ValueError('Font color must be #RRGGBB')
        return self


def validate_package(package_dir):
    package = Path(package_dir).resolve()
    for name in ('boundaries.geojson', 'merged_boundary.geojson', 'map_metadata.json'):
        if not (package / name).is_file():
            raise FileNotFoundError(f'Map package is missing {name}: {package}')
    metadata = json.loads((package / 'map_metadata.json').read_text(encoding='utf-8'))
    for record in metadata.get('orthomosaics', []):
        reference = record.get('relative_orthomosaic') or record.get('packaged_orthomosaic') or record.get('source_orthomosaic')
        if not reference:
            raise ValueError('Orthomosaic metadata has no file reference')
        raster = Path(reference)
        raster = raster if raster.is_absolute() else package / raster
        if not raster.is_file():
            raise FileNotFoundError(f'Orthomosaic unavailable: {raster}')
    return package, metadata


def design_from_package(package_dir):
    package, metadata = validate_package(package_dir)
    layout = metadata.get('layout') or {}
    printed = metadata.get('print_layout') or {}
    options = copy.deepcopy(DEFAULT_OPTIONS)
    options.update(printed.get('render_options') or {})
    options['paper_size'] = printed.get('paper_size', options['paper_size'])
    if 'render_options' not in printed:
        options['manual_scale'] = layout.get('map_scale')
    texts = {
        'map_title': layout.get('title') or metadata.get('title') or metadata.get('map_name', 'Map'),
        'map_location': layout.get('location') or metadata.get('location') or '',
        'map_disclaimer': layout.get('disclaimer') or 'Disclaimer: This map is intended for visualization and reference purposes only.',
    }
    texts['label_inset'] = options['boundary_label'] or texts['map_location']
    texts['label_inset_2'] = options['overview_label']
    state = printed.get('editor_state') or {}
    texts.update(state.get('texts', {}))
    return EditorDesign(package, options, copy.deepcopy(state.get('maps', {})),
                        texts, copy.deepcopy(state.get('text_styles', {})),
                        copy.deepcopy(state.get('map_styles', {})),
                        copy.deepcopy(state.get('grid_style', {}))).validate()


def import_bat_settings(path):
    """Parse whitelisted SET literals only. Never interpret/execute batch syntax."""
    options, texts, warnings = {}, {}, []
    for line_no, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        match = re.fullmatch(r'\s*@?set\s+(?:"([^"\r\n]*)"|([^"\r\n]*))\s*', line, re.I)
        if not match:
            if re.match(r'\s*@?set\s+', line, re.I):
                warnings.append(f'Line {line_no}: unsupported SET syntax')
            continue
        assignment = match.group(1) if match.group(1) is not None else match.group(2)
        name, separator, value = assignment.partition('=')
        name, value = name.strip().upper(), value.strip()
        if not separator or name not in BAT_OPTIONS.keys() | BAT_TEXT.keys():
            continue
        if any(token in value for token in ('&', '|', '<', '>', '^', '!', '`')) or re.search(r'%[^%]*%', value):
            warnings.append(f'Line {line_no}: {name} contains shell syntax or expansion; ignored')
            continue
        if name in BAT_TEXT:
            texts[BAT_TEXT[name]] = value
            continue
        key = BAT_OPTIONS[name]
        try:
            parsed = value
            if key.endswith('_bounds'):
                parsed = parse_bounds(value)
            elif key.endswith('_label'):
                parsed = value or None
            elif key not in ('paper_size', 'boundary_overlay', 'overview_overlay', 'overview_crs'):
                parsed = float(value)
            validate_options({key: parsed})
            options[key] = parsed
        except (ValueError, TypeError) as exc:
            # A blank boundary pair means auto-fit, not a bad setting.
            warnings.append(f'Line {line_no}: {name}: {exc}; ignored')
    for prefix in ('boundary', 'overview'):
        label, bounds = prefix + '_label', prefix + '_bounds'
        if (label in options or bounds in options) and bool(options.get(label)) != bool(options.get(bounds)):
            options.pop(label, None)
            options.pop(bounds, None)
            warnings.append(f'{prefix}: label and bounds must be supplied together; pair ignored')
    return options, texts, warnings


def apply_bat_settings(design, path):
    options, texts, warnings = import_bat_settings(path)
    updated = copy.deepcopy(design)
    updated.options.update(options)
    updated.texts.update(texts)
    updated.texts['label_inset'] = updated.options['boundary_label'] or updated.texts.get('map_location', '')
    updated.texts['label_inset_2'] = updated.options['overview_label']
    updated.maps = {}  # Framing options explicitly imported by the operator take effect.
    return updated.validate(), warnings


def design_payload(design, relative_to):
    design.validate()
    try:
        reference = os.path.relpath(design.package_dir, relative_to)
    except ValueError:  # Windows cross-volume references must remain absolute.
        reference = str(design.package_dir)
    return {'version': 2, 'package': reference, 'options': copy.deepcopy(design.options),
            'maps': copy.deepcopy(design.maps), 'texts': copy.deepcopy(design.texts),
            'text_styles': copy.deepcopy(design.text_styles),
            'map_styles': copy.deepcopy(design.map_styles), 'grid_style': copy.deepcopy(design.grid_style)}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep same-directory atomic replacement without repeating a potentially
    # long destination name (export snapshots can approach Windows MAX_PATH).
    temporary = path.with_name(uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def require_separate_workspace(package, target):
    package, target = Path(package).resolve(), Path(target).resolve()
    if target == Path(target.anchor) or target == REPO_ROOT or target == package or package in target.parents:
        raise ValueError('Choose a separate editor workspace outside the source map package')
    return target


def save_design(design, path):
    path = require_separate_workspace(design.package_dir, path)
    if (path.parent / 'attempt.json').exists():
        raise ValueError('Export snapshots are immutable; save a design in a separate workspace')
    if path.is_file() and json.loads(path.read_text(encoding='utf-8')).get('version') == 1:
        raise ValueError('Save V1 designs as a separate V2 copy')
    atomic_json(path, design_payload(design, path.parent))


def load_design(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    if type(data.get('version')) is not int or data['version'] not in (1, 2):
        raise ValueError('Unsupported map design version')
    package, _ = validate_package(path.parent / data['package'])
    options = dict(data['options'])
    if data['version'] == 1 and 'overview_crs' not in options:
        state = data['maps'].get('map_inset_2', {})
        if state.get('crs'):
            options['overview_crs'] = 'EPSG:4326' if state['crs'] == 'EPSG:4326' else 'project'
    return EditorDesign(package, options, data['maps'], data['texts'], data['text_styles'],
                        data.get('map_styles', {}), data.get('grid_style', {}), data['version']).validate()


def create_export_attempt(design, workspace):
    workspace = require_separate_workspace(design.package_dir, workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    attempt = workspace / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex)
    attempt.mkdir(exist_ok=False)
    save_design(design, attempt / 'snapshot.mapedit.json')
    atomic_json(attempt / 'attempt.json', {'version': 1, 'status': 'incomplete'})
    return attempt
