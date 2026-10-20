from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


def _load_exporter_module(monkeypatch):
    fake_osgeo = ModuleType('osgeo')
    fake_osgeo.gdal = SimpleNamespace(
        PushErrorHandler=lambda *_args: None,
        CPLDefaultErrorHandler=lambda *_args: None,
    )
    monkeypatch.setitem(sys.modules, 'osgeo', fake_osgeo)

    module_name = 'test_qgis_print_exporter_module'
    module_path = (
        Path(__file__).resolve().parents[1]
        / 'modules'
        / 'map_export'
        / 'qgis_print_exporter.py'
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    spec.loader.exec_module(module)
    return module


def test_export_builds_crs_dependent_layout_text_after_crs_is_computed(
    monkeypatch,
    tmp_path,
):
    exporter = _load_exporter_module(monkeypatch)
    metadata_path = tmp_path / 'map_metadata.json'
    metadata_path.write_text(
        json.dumps(
            {
                'bounds': {
                    'min_lon': 125.0,
                    'min_lat': 7.0,
                    'max_lon': 125.2,
                    'max_lat': 7.2,
                },
                'center': {'lon': 125.1, 'lat': 7.1},
                'layout': {
                    'title': 'Test map',
                    'location': 'Test location',
                    'map_scale': 15000,
                    'grid_label': 'WGS 84',
                },
            }
        ),
        encoding='utf-8',
    )

    qgis = SimpleNamespace(
        QgsApplication=object,
        QgsCoordinateReferenceSystem=lambda auth_id: auth_id,
    )
    monkeypatch.setattr(exporter, '_load_qgis_classes', lambda: qgis)
    monkeypatch.setattr(exporter, '_init_qgis_application', lambda _app: None)

    def validate_paths(ctx):
        ctx.metadata_path = metadata_path

    def assert_project_ready(ctx):
        assert ctx.target_epsg == 32651

    def assert_layout_variables(ctx):
        assert 'Projection: WGS 84 / UTM Zone 51N (EPSG:32651)' in (
            ctx.layout_variables['map_info']
        )
        assert ctx.layout_variables['label_inset'] == 'Local area'
        assert ctx.boundary_inset_bounds_wgs84['min_lon'] == 125.49

    monkeypatch.setattr(exporter, '_validate_package_paths', validate_paths)
    monkeypatch.setenv('MAP_EXPORT_BOUNDARY_INSET_LABEL', 'Local area')
    monkeypatch.setenv(
        'MAP_EXPORT_BOUNDARY_INSET_BOUNDS', '125.49,7.07,125.51,7.08',
    )
    monkeypatch.setattr(exporter, '_prepare_project', assert_project_ready)
    monkeypatch.setattr(
        exporter,
        '_apply_layout_variables',
        assert_layout_variables,
    )
    for name in (
        '_load_boundary_layers',
        '_load_orthomosaic_layers',
        '_assemble_map_layers',
        '_build_print_layout',
        '_configure_main_map',
        '_configure_insets',
    ):
        monkeypatch.setattr(exporter, name, lambda _ctx: None)
    monkeypatch.setattr(
        exporter,
        '_export_print_outputs',
        lambda _ctx: {'pdf': 'print_map.pdf'},
    )

    result = exporter.export_qgis_print_layout(package_dir=tmp_path)

    assert result == {'pdf': 'print_map.pdf'}


@pytest.mark.parametrize('fixed', (False, True))
@pytest.mark.parametrize('zoom_out', (1.0, 1.5))
@pytest.mark.parametrize('overlay_mode', ('boundary', 'pin'))
def test_boundary_inset_uses_buffer_or_fixed_bounds(
    monkeypatch, tmp_path, fixed, zoom_out, overlay_mode,
):
    exporter = _load_exporter_module(monkeypatch)

    class FakeMapItem:
        def rect(self):
            return SimpleNamespace(width=lambda: 100, height=lambda: 60)

    fake_qgis = ModuleType('qgis')
    fake_qgis_core = ModuleType('qgis.core')
    fake_qgis_core.QgsLayoutItemMap = FakeMapItem
    fake_qgis.core = fake_qgis_core
    monkeypatch.setitem(sys.modules, 'qgis', fake_qgis)
    monkeypatch.setitem(sys.modules, 'qgis.core', fake_qgis_core)

    observed = {}
    fixed_bounds = {
        'min_lon': 125.49, 'min_lat': 7.07,
        'max_lon': 125.51, 'max_lat': 7.08,
    } if fixed else None
    class FakeExtent:
        def __init__(self):
            self.center = (125, 7)
            self.width = 100
            self.height = 60

        def scale(self, factor):
            self.width *= factor
            self.height *= factor

    expected_extent = FakeExtent()

    def prepare_fitted_extent(**kwargs):
        observed['buffer_factor'] = kwargs['buffer_factor']
        return expected_extent

    def prepare_fixed_extent(**kwargs):
        observed['bounds'] = kwargs['wgs84_bounds']
        return expected_extent

    monkeypatch.setenv('MAP_EXPORT_BOUNDARY_INSET_BUFFER', '0.35')
    monkeypatch.setattr(exporter, '_prepare_fitted_extent', prepare_fitted_extent)
    monkeypatch.setattr(exporter, '_prepare_fixed_extent', prepare_fixed_extent)
    monkeypatch.setattr(
        exporter,
        '_render_inset_basemap_image',
        lambda **kwargs: observed.update(render_extent=kwargs['extent']),
    )
    monkeypatch.setattr(
        exporter,
        '_add_inset_basemap_picture',
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        exporter,
        '_configure_inset_boundary_overlay',
        lambda **kwargs: observed.update(
            overlay_extent=kwargs['extent'], overlay_mode=kwargs['overlay_mode'],
        ),
    )

    map_item = FakeMapItem()
    layout = SimpleNamespace(
        itemById=lambda _item_id: map_item,
        refresh=lambda: None,
    )
    exporter._configure_inset_map(
        layout=layout,
        project=object(),
        package_dir=tmp_path,
        basemap_layer=object(),
        boundary_layer=object(),
        target_crs=object(),
        QgsLayoutItemPicture=object,
        map_item_id='map_inset',
        fixed_extent_wgs84=fixed_bounds,
        QgsCoordinateReferenceSystem=object,
        zoom_out_factor=zoom_out,
        overlay_mode=overlay_mode,
    )

    if fixed:
        assert observed['bounds'] is fixed_bounds
        assert 'buffer_factor' not in observed
    else:
        assert observed['buffer_factor'] == 0.35
        assert 'bounds' not in observed
    assert observed['render_extent'] is expected_extent
    assert observed['overlay_extent'] is expected_extent
    assert expected_extent.width == pytest.approx(100 * zoom_out)
    assert expected_extent.height == pytest.approx(60 * zoom_out)
    assert expected_extent.center == (125, 7)
    assert observed['overlay_mode'] == overlay_mode


def test_boundary_inset_buffer_rejects_negative_values(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.setenv('MAP_EXPORT_BOUNDARY_INSET_BUFFER', '-1')

    assert exporter._read_nonnegative_float_env(
        'MAP_EXPORT_BOUNDARY_INSET_BUFFER',
        4.0,
    ) == 4.0


def test_overview_inset_config_reads_valid_label_and_bounds(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.setenv(
        'MAP_EXPORT_OVERVIEW_LABEL',
        'Kapalong, Davao del Norte',
    )
    monkeypatch.setenv(
        'MAP_EXPORT_OVERVIEW_BOUNDS',
        '125.305271,7.564435,125.736933,8.000306',
    )

    label, bounds = exporter._read_overview_inset_config()

    assert label == 'Kapalong, Davao del Norte'
    assert bounds == {
        'min_lon': 125.305271,
        'min_lat': 7.564435,
        'max_lon': 125.736933,
        'max_lat': 8.000306,
    }


def test_overview_inset_config_uses_default_when_unset(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.delenv('MAP_EXPORT_OVERVIEW_LABEL', raising=False)
    monkeypatch.delenv('MAP_EXPORT_OVERVIEW_BOUNDS', raising=False)

    label, bounds = exporter._read_overview_inset_config()

    assert label == 'Davao City'
    assert bounds == exporter.DAVAO_CITY_BOUNDS_WGS84
    assert bounds is not exporter.DAVAO_CITY_BOUNDS_WGS84


@pytest.mark.parametrize(
    ('label', 'bounds_value'),
    (
        ('Kapalong, Davao del Norte', ''),
        ('', '125.305271,7.564435,125.736933,8.000306'),
        ('Kapalong, Davao del Norte', 'not,valid,bounds'),
        ('Kapalong, Davao del Norte', '125.7,7.5,125.3,8.0'),
        ('Kapalong, Davao del Norte', '125.3,7.5,inf,8.0'),
        ('Kapalong, Davao del Norte', '181,7.5,182,8.0'),
    ),
)
def test_overview_inset_config_falls_back_atomically_for_invalid_pair(
    monkeypatch,
    label,
    bounds_value,
):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.setenv('MAP_EXPORT_OVERVIEW_LABEL', label)
    monkeypatch.setenv('MAP_EXPORT_OVERVIEW_BOUNDS', bounds_value)

    configured_label, configured_bounds = exporter._read_overview_inset_config()

    assert configured_label == 'Davao City'
    assert configured_bounds == exporter.DAVAO_CITY_BOUNDS_WGS84


def test_layout_variables_use_configured_overview_label(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    ctx = SimpleNamespace(
        map_title='Test map',
        map_location='Survey location',
        map_disclaimer='Test disclaimer',
        map_scale=2500,
        target_epsg=32651,
        grid_label='WGS 84',
        overview_label='Kapalong, Davao del Norte',
        boundary_inset_label='Local survey area',
    )

    exporter._configure_layout_variables(ctx)

    assert ctx.layout_variables['label_inset'] == 'Local survey area'
    assert ctx.layout_variables['map_location'] == 'Survey location'
    assert ctx.layout_variables['label_inset_2'] == (
        'Kapalong, Davao del Norte'
    )


@pytest.mark.parametrize('fixed_boundary', (False, True))
@pytest.mark.parametrize(
    ('zoom_out', 'expected'),
    (
        ('', 1.0), ('1', 1.0), ('1.5', 1.5), ('2', 2.0),
        ('0.5', 1.0), ('0', 1.0), ('-1', 1.0),
        ('nan', 1.0), ('inf', 1.0), ('bad', 1.0),
    ),
)
def test_configure_insets_uses_independent_bounds_and_zoom(
    monkeypatch, fixed_boundary, zoom_out, expected,
):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.setenv('MAP_EXPORT_BOUNDARY_INSET_ZOOM_OUT', zoom_out)
    monkeypatch.setenv('MAP_EXPORT_OVERVIEW_ZOOM_OUT', '2.5')
    monkeypatch.setenv('MAP_EXPORT_OVERVIEW_OVERLAY', 'pin')
    calls = []
    monkeypatch.setattr(
        exporter,
        '_configure_inset_map',
        lambda **kwargs: calls.append(kwargs),
    )
    configured_bounds = {
        'min_lon': 125.305271,
        'min_lat': 7.564435,
        'max_lon': 125.736933,
        'max_lat': 8.000306,
    }
    ctx = SimpleNamespace(
        qgis=SimpleNamespace(
            QgsLayoutItemPicture=object,
            QgsCoordinateReferenceSystem=object,
        ),
        layout=object(),
        project=object(),
        package_dir=Path('unused'),
        basemap_layer=object(),
        merged_layer=object(),
        target_crs=object(),
        overview_bounds_wgs84=configured_bounds,
        boundary_inset_bounds_wgs84=(
            {'min_lon': 125.49, 'min_lat': 7.07,
             'max_lon': 125.51, 'max_lat': 7.08}
            if fixed_boundary else None
        ),
    )

    exporter._configure_insets(ctx)

    assert len(calls) == 2
    assert calls[0]['map_item_id'] == 'map_inset'
    assert calls[0]['fixed_extent_wgs84'] is ctx.boundary_inset_bounds_wgs84
    assert calls[1]['map_item_id'] == 'map_inset_2'
    assert calls[1]['fixed_extent_wgs84'] is configured_bounds
    assert calls[0]['zoom_out_factor'] == expected
    assert calls[1]['zoom_out_factor'] == 2.5
    assert calls[0].get('overlay_mode', 'boundary') == 'boundary'
    assert calls[1]['overlay_mode'] == 'pin'


@pytest.mark.parametrize(
    ('configured', 'expected'),
    ((None, 'boundary'), ('', 'boundary'), ('boundary', 'boundary'),
     ('PIN', 'pin'), ('pin', 'pin'), ('bad', 'boundary')),
)
def test_overview_overlay_is_opt_in(monkeypatch, configured, expected):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.delenv('MAP_EXPORT_OVERVIEW_OVERLAY', raising=False)
    if configured is not None:
        monkeypatch.setenv('MAP_EXPORT_OVERVIEW_OVERLAY', configured)
    assert exporter._read_overview_overlay_mode() == expected


@pytest.mark.parametrize(('configured', 'expected'), (
    (None, 'boundary'), ('', 'boundary'), ('boundary', 'boundary'),
    ('PIN', 'pin'), ('pin', 'pin'), ('bad', 'boundary'),
))
@pytest.mark.parametrize('live', (False, True))
def test_upper_inset_batch_marker_is_configurable_and_recorded(monkeypatch, configured, expected, live):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.delenv('MAP_EXPORT_BOUNDARY_INSET_OVERLAY', raising=False)
    if configured is not None:
        monkeypatch.setenv('MAP_EXPORT_BOUNDARY_INSET_OVERLAY', configured)
    monkeypatch.setenv('MAP_EXPORT_OVERVIEW_OVERLAY', 'pin')
    monkeypatch.delenv('MAP_EXPORT_OVERVIEW_CRS', raising=False)
    configure = Mock()
    monkeypatch.setattr(exporter, '_configure_inset_map', configure)
    ctx = SimpleNamespace(
        qgis=SimpleNamespace(QgsLayoutItemPicture=object, QgsCoordinateReferenceSystem=object),
        layout=object(), project=object(), package_dir=Path('unused'),
        basemap_layer=object(), merged_layer=object(), target_crs=object(),
        overview_bounds_wgs84={}, boundary_inset_bounds_wgs84=None, live_insets=live,
        paper_size='A3', selected_map_scale=2500, scale_mode='manual',
    )
    exporter._configure_insets(ctx)
    upper, lower = [call.kwargs for call in configure.call_args_list]
    assert upper['overlay_mode'] == expected
    assert lower['overlay_mode'] == 'pin'
    assert upper['live'] == lower['live'] == live
    assert exporter._resolved_render_options(ctx)['boundary_overlay'] == expected


@pytest.mark.parametrize(('value', 'expected'), (
    (None, 'project'), ('', 'project'), ('PROJECT', 'project'),
    ('epsg:4326', 'EPSG:4326'), ('EPSG:3857', 'project'), ('bad', 'project'),
))
def test_batch_overview_crs_preserves_legacy_default(monkeypatch, value, expected):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.delenv('MAP_EXPORT_OVERVIEW_CRS', raising=False)
    if value is not None:
        monkeypatch.setenv('MAP_EXPORT_OVERVIEW_CRS', value)
    assert exporter._read_overview_crs() == expected


@pytest.mark.parametrize('live', (False, True))
@pytest.mark.parametrize('configured', ('project', 'EPSG:4326'))
def test_only_lower_inset_receives_selected_crs(monkeypatch, live, configured):
    from modules.map_export.editor_model import validate_options
    exporter = _load_exporter_module(monkeypatch)
    calls = Mock()
    monkeypatch.setattr(exporter, '_configure_inset_map', calls)
    make_crs = lambda name: SimpleNamespace(authid=lambda: name)
    target = make_crs('EPSG:32651')
    ctx = SimpleNamespace(
        qgis=SimpleNamespace(QgsLayoutItemPicture=object, QgsCoordinateReferenceSystem=make_crs),
        layout=object(), project=object(), package_dir=Path('unused'),
        basemap_layer=object(), merged_layer=object(), target_crs=target,
        overview_bounds_wgs84={}, boundary_inset_bounds_wgs84=None,
        render_options=validate_options({'overview_crs': configured}), live_insets=live,
    )
    exporter._configure_insets(ctx)
    upper, lower = [call.kwargs for call in calls.call_args_list]
    assert upper['target_crs'] is target
    assert lower['target_crs'].authid() == ('EPSG:32651' if configured == 'project' else 'EPSG:4326')
    assert upper['live'] == lower['live'] == live
    assert ctx.target_crs is target


def _install_locator_fakes(monkeypatch):
    core = ModuleType('qgis.core')
    qgis = ModuleType('qgis')
    qgis.core = core
    layer = Mock()
    layer.isValid.return_value = True
    layer.dataProvider.return_value.addFeatures.return_value = (True, [])
    point = Mock()
    point.isNull.return_value = False
    point.isEmpty.return_value = False
    geometry = Mock()
    geometry.isEmpty.return_value = False
    geometry.pointOnSurface.return_value = point
    source_feature = Mock()
    source_feature.hasGeometry.return_value = True
    source_feature.geometry.return_value = geometry
    boundary = Mock()
    boundary.getFeatures.return_value = [source_feature]
    feature = Mock()
    pin = Mock()
    symbol = Mock()
    core.QgsFeature = Mock(return_value=feature)
    core.QgsVectorLayer = Mock(return_value=layer)
    core.QgsMarkerSymbol = SimpleNamespace(createSimple=Mock(return_value=symbol))
    core.QgsSvgMarkerSymbolLayer = Mock(return_value=pin)
    core.QgsMarkerSymbolLayer = SimpleNamespace(HCenter='center', Bottom='bottom')
    core.QgsUnitTypes = SimpleNamespace(RenderMillimeters='mm')
    core.QgsSingleSymbolRenderer = Mock()
    monkeypatch.setitem(sys.modules, 'qgis', qgis)
    monkeypatch.setitem(sys.modules, 'qgis.core', core)
    return SimpleNamespace(
        core=core, layer=layer, point=point, geometry=geometry,
        source_feature=source_feature, boundary=boundary,
        feature=feature, pin=pin, symbol=symbol,
    )


def test_locator_pin_uses_survey_geometry_crs_and_fixed_print_size(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    fake = _install_locator_fakes(monkeypatch)

    assert exporter._create_inset_locator_layer(fake.boundary) is fake.layer
    fake.layer.setCrs.assert_called_once_with(fake.boundary.crs())
    fake.feature.setGeometry.assert_called_once_with(fake.point)
    fake.geometry.pointOnSurface.assert_called_once_with()
    fake.layer.dataProvider().addFeatures.assert_called_once_with([fake.feature])
    fake.layer.updateExtents.assert_called_once_with()
    fake.pin.setSize.assert_called_once_with(4.0)
    fake.pin.setSizeUnit.assert_called_once_with('mm')
    fake.pin.setHorizontalAnchorPoint.assert_called_once_with('center')
    fake.pin.setVerticalAnchorPoint.assert_called_once_with('bottom')
    fake.symbol.changeSymbolLayer.assert_called_once_with(0, fake.pin)
    fake.layer.setRenderer.assert_called_once_with(
        fake.core.QgsSingleSymbolRenderer.return_value,
    )
    assert Path(fake.core.QgsSvgMarkerSymbolLayer.call_args.args[0]).is_file()
    fake.boundary.clone.assert_not_called()


@pytest.mark.parametrize(
    ('failure', 'message'),
    (
        ('invalid_layer', 'Failed to create'),
        ('no_geometry', 'No valid survey'),
        ('empty_geometry', 'No valid survey'),
        ('empty_point', 'Unable to determine'),
        ('null_point', 'Unable to determine'),
        ('add_failed', 'Failed to add'),
    ),
)
def test_locator_pin_refuses_invalid_or_unpersisted_geometry(
    monkeypatch, failure, message,
):
    exporter = _load_exporter_module(monkeypatch)
    fake = _install_locator_fakes(monkeypatch)
    if failure == 'invalid_layer':
        fake.layer.isValid.return_value = False
    elif failure == 'no_geometry':
        fake.source_feature.hasGeometry.return_value = False
    elif failure == 'empty_geometry':
        fake.geometry.isEmpty.return_value = True
    elif failure == 'empty_point':
        fake.point.isEmpty.return_value = True
    elif failure == 'null_point':
        fake.point.isNull.return_value = True
    else:
        fake.layer.dataProvider().addFeatures.return_value = (False, [])
    with pytest.raises(RuntimeError, match=message):
        exporter._create_inset_locator_layer(fake.boundary)
    fake.layer.setRenderer.assert_not_called()


@pytest.mark.parametrize('mode', ('boundary', 'pin'))
def test_inset_overlay_selects_pin_or_cloned_boundary(monkeypatch, mode):
    exporter = _load_exporter_module(monkeypatch)
    boundary, pin_layer, project, item = Mock(), Mock(), Mock(), Mock()
    create_pin = Mock(return_value=pin_layer)
    monkeypatch.setattr(exporter, '_create_inset_locator_layer', create_pin)
    target_crs, extent = object(), object()

    exporter._configure_inset_boundary_overlay(
        project=project, inset_item=item, boundary_layer=boundary,
        target_crs=target_crs, extent=extent, overlay_mode=mode,
    )

    if mode == 'pin':
        create_pin.assert_called_once_with(boundary)
        boundary.clone.assert_not_called()
        overlay = pin_layer
    else:
        create_pin.assert_not_called()
        boundary.clone.assert_called_once_with()
        overlay = boundary.clone.return_value
        overlay.setRenderer.assert_called_once_with(
            boundary.renderer().clone(),
        )
    project.addMapLayer.assert_called_once_with(overlay, False)
    item.setLayers.assert_called_once_with([overlay])
    item.setCrs.assert_called_once_with(target_crs)
    item.zoomToExtent.assert_called_once_with(extent)
    item.setBackgroundEnabled.assert_called_once_with(False)
    item.refresh.assert_called_once_with()


@pytest.mark.parametrize(
    ('label', 'bounds_value'),
    (
        ('', ''),
        ('Local area', ''),
        ('', '125.49,7.07,125.51,7.08'),
        ('Local area', 'bad,7.07,125.51,7.08'),
        ('Local area', '125.51,7.07,125.49,7.08'),
        ('Local area', '125.49,7.08,125.51,7.07'),
        ('Local area', '125.49,7.07,nan,7.08'),
        ('Local area', '125.49,7.07,inf,7.08'),
        ('Local area', '181,7.07,182,7.08'),
        ('Local area', '125.49,-91,125.51,7.08'),
        ('Local area', '125.49,7.07,125.51'),
    ),
)
def test_boundary_inset_config_preserves_auto_fit_for_invalid_or_empty_pair(
    monkeypatch, label, bounds_value,
):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.setenv('MAP_EXPORT_BOUNDARY_INSET_LABEL', label)
    monkeypatch.setenv('MAP_EXPORT_BOUNDARY_INSET_BOUNDS', bounds_value)

    assert exporter._read_inset_config(
        'MAP_EXPORT_BOUNDARY_INSET', 'Survey location', None,
    ) == ('Survey location', None)


def test_boundary_inset_config_defaults_when_unset(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.delenv('MAP_EXPORT_BOUNDARY_INSET_LABEL', raising=False)
    monkeypatch.delenv('MAP_EXPORT_BOUNDARY_INSET_BOUNDS', raising=False)

    assert exporter._read_inset_config(
        'MAP_EXPORT_BOUNDARY_INSET', 'Survey location', None,
    ) == ('Survey location', None)


@pytest.mark.parametrize(
    ('configured', 'expected'),
    (
        ('A4', 'A4'),
        ('a3', 'A3'),
        ('', 'A4'),
        ('letter', 'A4'),
    ),
)
def test_paper_size_config_accepts_a4_or_a3_and_falls_back(
    monkeypatch,
    configured,
    expected,
):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.setenv('MAP_EXPORT_PAPER_SIZE', configured)

    assert exporter._read_paper_size_env() == expected


def test_a3_layout_proportionally_resizes_page_and_items(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)

    class FakeMeasurement:
        def __init__(self, first, second, units='mm'):
            self.first = float(first)
            self.second = float(second)
            self._units = units

        def x(self):
            return self.first

        def y(self):
            return self.second

        def width(self):
            return self.first

        def height(self):
            return self.second

        def units(self):
            return self._units

    class FakePage:
        def __init__(self):
            self.size = FakeMeasurement(297, 210)

        def pageSize(self):
            return self.size

        def setPageSize(self, size):
            self.size = size

    class FakeItem:
        def __init__(self):
            self.position = FakeMeasurement(10, 20)
            self.size = FakeMeasurement(100, 50)

        def positionWithUnits(self):
            return self.position

        def sizeWithUnits(self):
            return self.size

        def attemptMove(self, position):
            self.position = position

        def attemptResize(self, size):
            self.size = size

    page = FakePage()
    item = FakeItem()
    refreshed = []
    layout = SimpleNamespace(
        pageCollection=lambda: SimpleNamespace(
            pageCount=lambda: 1,
            page=lambda _index: page,
        ),
        items=lambda: [page, item],
        refresh=lambda: refreshed.append(True),
    )
    qgis = SimpleNamespace(
        QgsLayoutItemPage=FakePage,
        QgsLayoutPoint=FakeMeasurement,
        QgsLayoutSize=FakeMeasurement,
        QgsUnitTypes=SimpleNamespace(LayoutMillimeters='mm'),
    )
    ctx = SimpleNamespace(
        qgis=qgis,
        layout=layout,
        paper_size='A3',
    )

    exporter._configure_layout_paper_size(ctx)

    assert page.size.width() == 420
    assert page.size.height() == 297
    assert item.position.x() == pytest.approx(10 * 420 / 297)
    assert item.position.y() == pytest.approx(20 * 297 / 210)
    assert item.size.width() == pytest.approx(100 * 420 / 297)
    assert item.size.height() == pytest.approx(50 * 297 / 210)
    assert refreshed == [True]


@pytest.mark.parametrize(
    ('has_orthomosaic', 'expected_buffer', 'expected_source'),
    (
        (True, 0.08, 'orthomosaic'),
        (False, 0.12, 'boundary'),
    ),
)
def test_main_map_uses_standard_auto_scale_for_orthomosaic_or_boundary(
    monkeypatch,
    has_orthomosaic,
    expected_buffer,
    expected_source,
):
    exporter = _load_exporter_module(monkeypatch)
    fitted_extent = object()
    geographic_extent = SimpleNamespace(
        width=lambda: 0.0042,
        height=lambda: 0.0031,
    )
    ortho_layers = [object(), object()] if has_orthomosaic else []
    merged_layer = object()
    observed = {}

    class FakeMapItem:
        def __init__(self):
            self.forced_scales = []
            self.current_scale = 961.0

        def rect(self):
            return SimpleNamespace(width=lambda: 187, height=lambda: 132)

        def setCrs(self, crs):
            observed['map_crs'] = crs

        def setExtent(self, extent):
            observed['map_extent'] = extent

        def setLayers(self, layers):
            observed['map_layers'] = layers

        def setKeepLayerSet(self, enabled):
            observed['keep_layers'] = enabled

        def setScale(self, scale):
            self.forced_scales.append(scale)
            self.current_scale = float(scale)

        def extent(self):
            return fitted_extent

        def scale(self):
            return self.current_scale

        def refresh(self):
            pass

    class FakeTransform:
        def __init__(self, *_args):
            pass

        def transformBoundingBox(self, extent):
            assert extent is fitted_extent
            return geographic_extent

    map_item = FakeMapItem()
    qgis = SimpleNamespace(
        QgsLayoutItemMap=object,
        QgsLayoutItemMapGrid=object,
        QgsCoordinateReferenceSystem=lambda auth_id: auth_id,
        QgsCoordinateTransform=FakeTransform,
        QColor=object,
        QFont=object,
    )
    ctx = SimpleNamespace(
        qgis=qgis,
        layout=object(),
        project=object(),
        target_crs='EPSG:32651',
        merged_layer=merged_layer,
        orthomosaic_layers=ortho_layers,
        main_map_layers=[object()],
        map_scale=None,
    )

    monkeypatch.setattr(
        exporter,
        '_get_layout_item',
        lambda *_args, **_kwargs: map_item,
    )

    def prepare_fitted_extent(**kwargs):
        observed['fit_layers'] = kwargs['layers']
        observed['buffer_factor'] = kwargs['buffer_factor']
        return fitted_extent

    monkeypatch.setattr(exporter, '_prepare_fitted_extent', prepare_fitted_extent)

    def add_coordinate_grid(**kwargs):
        observed['grid_extent'] = kwargs['extent']
        return 0.001

    monkeypatch.setattr(exporter, '_add_coordinate_grid', add_coordinate_grid)
    monkeypatch.setattr(
        exporter,
        '_configure_scale_bar',
        lambda **_kwargs: None,
    )

    exporter._configure_main_map(ctx)

    expected_layers = ortho_layers if has_orthomosaic else [merged_layer]
    assert observed['fit_layers'] == expected_layers
    assert observed['buffer_factor'] == expected_buffer
    assert observed['grid_extent'] is geographic_extent
    assert map_item.forced_scales == [2500]
    assert ctx.scale_mode == 'automatic'
    assert ctx.fitted_map_scale == 961
    assert ctx.auto_scale_multiplier == 2.5
    assert ctx.selected_map_scale == 2500
    assert ctx.effective_map_scale == 2500
    assert ctx.extent_source == expected_source
    assert ctx.grid_interval_degrees == 0.001


@pytest.mark.parametrize(
    ('scale', 'expected'),
    (
        (100, 500),
        (500, 500),
        (500.001, 750),
        (750, 750),
        (750.001, 1000),
        (1000, 1000),
        (1000.001, 1250),
        (1250, 1250),
        (1250.001, 1500),
        (1500, 1500),
        (1500.001, 2000),
        (2000, 2000),
        (2000.001, 2500),
        (2500, 2500),
        (2500.001, 3000),
        (3000, 3000),
        (3000.001, 4000),
        (4000, 4000),
        (4000.001, 5000),
        (5000, 5000),
        (5000.001, 7500),
        (7500, 7500),
        (7500.001, 10000),
        (10000, 10000),
        (10000.001, 12500),
        (75000001, 100000000),
    ),
)
def test_standard_scale_rounds_outward_at_every_boundary(
    monkeypatch,
    scale,
    expected,
):
    exporter = _load_exporter_module(monkeypatch)

    assert exporter._round_up_standard_scale(scale) == expected


def test_current_tight_fit_selects_one_to_2500(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)

    decision = exporter._select_main_map_scale(
        fitted_scale=961,
        manual_scale=None,
        auto_multiplier=2.5,
    )

    assert decision == {
        'scale_mode': 'automatic',
        'fitted_scale': 961.0,
        'auto_scale_multiplier': 2.5,
        'selected_scale': 2500,
    }


def test_same_footprint_selects_same_scale_regardless_of_pixel_resolution(
    monkeypatch,
):
    exporter = _load_exporter_module(monkeypatch)
    shared_geographic_footprint_fit = 961
    footprint_width_degrees = 0.0014007
    raster_widths = (6605, 12847)
    pixel_sizes = tuple(
        footprint_width_degrees / width
        for width in raster_widths
    )
    decisions = tuple(
        exporter._select_main_map_scale(
            fitted_scale=shared_geographic_footprint_fit,
            manual_scale=None,
            auto_multiplier=2.5,
        )
        for _pixel_size in pixel_sizes
    )

    assert pixel_sizes[0] != pixel_sizes[1]
    assert [decision['selected_scale'] for decision in decisions] == [2500, 2500]


def test_manual_scale_remains_exact(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)

    decision = exporter._select_main_map_scale(
        fitted_scale=961,
        manual_scale=1234.5,
        auto_multiplier=2.5,
    )

    assert decision == {
        'scale_mode': 'manual',
        'fitted_scale': 961.0,
        'auto_scale_multiplier': None,
        'selected_scale': 1234.5,
    }


@pytest.mark.parametrize(
    'configured_value',
    ('not-a-number', 'nan', 'inf', '-inf', '0', '0.99', '-2'),
)
def test_invalid_auto_scale_multiplier_uses_default(
    monkeypatch,
    configured_value,
):
    exporter = _load_exporter_module(monkeypatch)
    monkeypatch.setenv('MAP_EXPORT_AUTO_SCALE_MULTIPLIER', configured_value)

    assert exporter._read_float_env_at_least(
        'MAP_EXPORT_AUTO_SCALE_MULTIPLIER',
        2.5,
        minimum=1.0,
    ) == 2.5


def test_grid_interval_and_precision_adapt_to_geographic_span(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)

    close_extent = SimpleNamespace(width=lambda: 0.0042, height=lambda: 0.0031)
    wide_extent = SimpleNamespace(width=lambda: 0.035, height=lambda: 0.025)

    assert exporter._nice_grid_interval(close_extent) == 0.001
    assert exporter._grid_annotation_precision(0.001) == 3
    assert exporter._nice_grid_interval(wide_extent) == 0.01
    assert exporter._grid_annotation_precision(0.01) == 2


@pytest.mark.parametrize('font_size', (6.0, 8.5, 12.0))
def test_coordinate_grid_applies_adaptive_interval_and_precision(monkeypatch, font_size):
    exporter = _load_exporter_module(monkeypatch)
    observed = {}

    class FakeLineSymbol:
        def clone(self):
            observed['cloned'] = True
            return FakeLineSymbol()

        def setWidth(self, value):
            observed['line_width'] = value

        def setColor(self, value):
            observed['line_color'] = value

        def setOpacity(self, value):
            observed['line_opacity'] = value

    class FakeGrid:
        def setEnabled(self, value):
            observed['enabled'] = value

        def setCrs(self, value):
            observed['crs'] = value

        def setStyle(self, value):
            observed['style'] = value

        def setIntervalX(self, value):
            observed['interval_x'] = value

        def setIntervalY(self, value):
            observed['interval_y'] = value

        def lineSymbol(self):
            return FakeLineSymbol()

        def setLineSymbol(self, value):
            observed['line_symbol'] = value

        def setAnnotationEnabled(self, _value):
            observed['annotations'] = _value

        def setAnnotationPrecision(self, value):
            observed['precision'] = value

        def setAnnotationFont(self, value):
            observed['font'] = value

        def setAnnotationFrameDistance(self, _value):
            pass

    map_item = SimpleNamespace(grid=lambda: FakeGrid())
    extent = SimpleNamespace(width=lambda: 0.0042, height=lambda: 0.0031)

    interval = exporter._add_coordinate_grid(
        map_item=map_item,
        extent=extent,
        grid_crs='EPSG:4326',
        QgsLayoutItemMapGrid=SimpleNamespace(Solid='solid'),
        QColor=lambda *args: args,
        QFont=lambda *_args: SimpleNamespace(setPointSizeF=lambda size: observed.update(font_size=size)),
        annotation_font_size=font_size,
    )

    assert interval == 0.001
    assert observed['enabled'] is True
    assert observed['crs'] == 'EPSG:4326'
    assert observed['style'] == 'solid'
    assert observed['interval_x'] == 0.001
    assert observed['interval_y'] == 0.001
    assert observed['precision'] == 3
    assert observed['cloned'] is True
    assert observed['line_width'] == 0.25
    assert observed['line_color'] == (90, 90, 90, 153)
    assert observed['line_opacity'] == 1.0
    assert isinstance(observed['line_symbol'], FakeLineSymbol)
    assert observed['annotations'] is True
    assert observed['font_size'] == font_size


def test_scale_bar_segment_adapts_to_projected_extent(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    observed = {}

    class FakeScaleBar:
        def setLinkedMap(self, value):
            observed['map'] = value

        def setUnits(self, value):
            observed['units'] = value

        def setUnitLabel(self, value):
            observed['label'] = value

        def setUnitsPerSegment(self, value):
            observed['segment'] = value

        def setNumberOfSegments(self, value):
            observed['segments'] = value

        def setNumberOfSegmentsLeft(self, value):
            observed['left_segments'] = value

        def update(self):
            observed['updated'] = True

    scale_bar = FakeScaleBar()
    map_item = object()
    ctx = SimpleNamespace(
        layout=object(),
        qgis=SimpleNamespace(
            QgsLayoutItemScaleBar=FakeScaleBar,
            QgsUnitTypes=SimpleNamespace(DistanceKilometers='km'),
        ),
    )
    monkeypatch.setattr(
        exporter,
        '_get_layout_item',
        lambda *_args, **_kwargs: scale_bar,
    )

    exporter._configure_scale_bar(
        ctx=ctx,
        map_item=map_item,
        extent=SimpleNamespace(width=lambda: 620),
    )

    assert observed == {
        'map': map_item,
        'units': 'km',
        'label': 'km',
        'segment': 0.1,
        'segments': 3,
        'left_segments': 0,
        'updated': True,
    }


def test_print_metadata_records_scale_decision_and_grid_interval(monkeypatch):
    exporter = _load_exporter_module(monkeypatch)
    ctx = SimpleNamespace(
        target_epsg=32651,
        effective_map_scale=2500,
        scale_mode='automatic',
        fitted_map_scale=961.0,
        auto_scale_multiplier=2.5,
        selected_map_scale=2500,
        extent_source='orthomosaic',
        grid_interval_degrees=0.001,
        paper_size='A3',
        dpi=400,
        logo_path=None,
    )

    metadata = exporter._build_print_layout_metadata(
        ctx,
        exported_at='2026-10-16 12:00:00',
    )

    assert metadata['scale'] == 2500
    assert metadata['scale_mode'] == 'automatic'
    assert metadata['fitted_scale'] == 961.0
    assert metadata['auto_scale_multiplier'] == 2.5
    assert metadata['selected_scale'] == 2500
    assert metadata['extent_source'] == 'orthomosaic'
    assert metadata['grid_interval_degrees'] == 0.001
    assert metadata['paper_size'] == 'A3'
