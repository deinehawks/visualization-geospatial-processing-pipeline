"""Optional page-furniture overrides; imports never initialize Qt or GIS."""
from __future__ import annotations

import copy
import math

from .editor_model import MAP_IDS, TEXT_IDS


FURNITURE_IDS = ('map_info', 'scale_bar', 'map_logo')
DECORATION_IDS = ('north_arrow', 'osm_attribution')
MOVABLE_IDS = MAP_IDS + TEXT_IDS + FURNITURE_IDS + DECORATION_IDS


def validate_layout_settings(settings):
    if not isinstance(settings, dict) or set(settings) - {
        'info_font_size', 'scale_font_size', 'scale_height_mm', 'logo_position', 'object_positions',
    }:
        raise ValueError('Invalid layout settings')
    result = copy.deepcopy(settings)
    for key, value in result.items():
        if key == 'object_positions':
            if not isinstance(value, dict) or set(value) - set(MOVABLE_IDS):
                raise ValueError('Unknown movable page object')
            if 'map_logo' in value and 'logo_position' in settings:
                raise ValueError('Use only one saved position for the logo')
            for position in value.values():
                _validate_position(position)
        elif key == 'logo_position':
            _validate_position(value)
        else:
            _number(value, 0.5 if key == 'scale_height_mm' else 1,
                    100 if key == 'scale_height_mm' else 1000)
    return result


def _validate_position(position):
    if not isinstance(position, dict) or set(position) != {'x_fraction', 'y_fraction'}:
        raise ValueError('Object position requires page fractions')
    for coordinate in position.values():
        _number(coordinate, 0, 1)


def _number(value, minimum, maximum):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not minimum <= value <= maximum):
        raise ValueError(f'Layout value must be a finite number from {minimum} to {maximum}')


def apply_layout_settings(ctx, settings):
    """Apply sparse overrides, remembering template values for incremental Undo.

    Check requested items/geometry before mutating. Callers own transaction rollback
    if a Qt setter fails. Empty legacy settings leave the template untouched.
    """
    settings = validate_layout_settings(settings)
    defaults = getattr(ctx, 'furniture_defaults', {})
    keys = (set(settings) - {'object_positions'}) | set(defaults)
    positions = settings.get('object_positions', {})
    position_defaults = dict(getattr(ctx, 'object_position_defaults', {}))
    position_items = {key: _object(ctx, key) for key in set(positions) | set(position_defaults)}
    if not keys and not position_items:
        ctx.layout_settings = {}
        return
    ids = {'info_font_size': 'map_info', 'scale_font_size': 'scale_bar',
           'scale_height_mm': 'scale_bar', 'logo_position': 'map_logo'}
    items = {key: ctx.layout.itemById(ids[key]) for key in keys}
    if any(item is None for item in items.values()):
        raise ValueError('The template is missing requested page furniture')
    q = ctx.qgis
    position = settings.get('logo_position')
    if position is not None:
        width, height = page_size(ctx)
        logo = items['logo_position']
        x, y = position['x_fraction'] * width, position['y_fraction'] * height
        if x + logo.rect().width() > width + 1e-6 or y + logo.rect().height() > height + 1e-6:
            raise ValueError('Keep the entire logo inside the page')
    # Capture original values only once, before the first override for each setting.
    defaults = dict(defaults)
    for key, item in items.items():
        if key not in defaults:
            if key == 'info_font_size':
                defaults[key] = (q.QFont(item.font()), item.sizeWithUnits())
            elif key == 'scale_font_size':
                defaults[key] = item.textFormat()
            elif key == 'scale_height_mm':
                defaults[key] = item.height()
            else:
                defaults[key] = item.positionWithUnits()
    ctx.furniture_defaults = defaults
    for key, item in position_items.items():
        if key not in position_defaults:
            # A legacy logo override must not become the template's Undo origin.
            position_defaults[key] = (defaults['logo_position'] if key == 'map_logo'
                                      and 'logo_position' in defaults else item.positionWithUnits())
    ctx.object_position_defaults = position_defaults
    for key, item in items.items():
        if key == 'info_font_size':
            font, size = defaults[key]
            font = q.QFont(font)
            if key in settings:
                font.setPointSizeF(settings[key])
            item.setFont(font)
            if key in settings:
                item.adjustSizeToText()
            else:
                item.attemptResize(size)
        elif key == 'scale_font_size':
            # textFormat() returns a value copy, preserving color/family/buffers.
            text_format = item.textFormat() if key in settings else defaults[key]
            if key in settings:
                text_format.setSize(settings[key])
                text_format.setSizeUnit(q.QgsUnitTypes.RenderPoints)
            item.setTextFormat(text_format)
        elif key == 'scale_height_mm':
            item.setHeight(settings.get(key, defaults[key]))
        else:
            point = (q.QgsLayoutPoint(x, y, q.QgsUnitTypes.LayoutMillimeters)
                     if position is not None else defaults[key])
            item.attemptMove(point, False)
        item.refresh()
    width, height = page_size(ctx)
    for key, item in position_items.items():
        if key in positions:
            value = positions[key]
            point = q.QgsLayoutPoint(value['x_fraction'] * width, value['y_fraction'] * height,
                                     q.QgsUnitTypes.LayoutMillimeters)
        elif key == 'map_logo' and position is not None:
            # An older saved design may use the original logo-only field.
            point = q.QgsLayoutPoint(x, y, q.QgsUnitTypes.LayoutMillimeters)
        else:
            point = position_defaults[key]
        item.attemptMove(point, False)
        if key in ('map_inset', 'map_inset_2'):
            background = ctx.layout.itemById(key + '_basemap')
            if background is not None:
                background.attemptMove(item.positionWithUnits(), False)
                background.refresh()
        item.refresh()
    ctx.layout_settings = copy.deepcopy(settings)


def page_size(ctx):
    from .editor_model import PAPER_SIZES_MM
    options = getattr(ctx, 'render_options', None)
    return PAPER_SIZES_MM[options['paper_size'] if options is not None else ctx.paper_size]


def _object(ctx, key):
    if key not in MOVABLE_IDS:
        raise ValueError('Unknown movable page object')
    item = ctx.layout.itemById(key)
    if item is None:
        raise ValueError(f'The template is missing page object: {key}')
    return item


def object_position_values(ctx, key):
    item = _object(ctx, key)
    width, height = page_size(ctx)
    position = item.pos()
    return {'x_mm': position.x(), 'y_mm': position.y(),
            'max_x_mm': max(0, width - item.rect().width()),
            'max_y_mm': max(0, height - item.rect().height())}


def furniture_values(ctx):
    """Read physical values for the controls without recording design edits."""
    info, bar, logo = (ctx.layout.itemById(key) for key in FURNITURE_IDS)
    if info is None or bar is None or logo is None:
        raise ValueError('The template is missing page furniture')
    position = logo.pos()  # Shared layout uses millimeters and top-left anchors.
    width, height = page_size(ctx)
    return {'info_font_size': info.font().pointSizeF(),
            'scale_font_size': bar.textFormat().size(),
            'scale_height_mm': bar.height(), 'logo_x_mm': position.x(),
            'logo_y_mm': position.y(),
            'logo_max_x_mm': max(0, width - logo.rect().width()),
            'logo_max_y_mm': max(0, height - logo.rect().height())}
