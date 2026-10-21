# pyright: reportMissingImports=false

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlsplit
import json, math, os, time
from osgeo import gdal


def _quiet_gdal_error_handler(err_class, err_num, err_msg):
    if "PNG driver does not support update access" in err_msg:
        return
    gdal.CPLDefaultErrorHandler(err_class, err_num, err_msg)


gdal.PushErrorHandler(_quiet_gdal_error_handler)


# Default reference extent for the overview inset (map_inset_2). Operators can
# replace the label and extent using the paired MAP_EXPORT_OVERVIEW variables.
DEFAULT_OVERVIEW_LABEL = "Davao City"
DAVAO_CITY_BOUNDS_WGS84 = {
    "min_lon": 125.3063,
    "min_lat": 6.8710,
    "max_lon": 125.9308,
    "max_lat": 7.3282,
}

DEFAULT_AUTO_SCALE_MULTIPLIER = 2.5
MIN_AUTO_MAP_SCALE = 500.0
STANDARD_SCALE_MANTISSAS = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.5, 10.0)
GRID_LINE_WIDTH_MM = 0.25
GRID_LINE_COLOR_RGBA = (90, 90, 90, 153)
DEFAULT_PAPER_SIZE = 'A4'
PAPER_SIZES_MM = {
    'A4': (297.0, 210.0),
    'A3': (420.0, 297.0),
}


# Shared pipeline state
class ExportContext:
    """
    Mutable state shared across the export pipeline stages.

    Each `_*` stage function below reads whatever it needs from the context
    and writes back the values it produces, so stage functions don't need to
    pass a long, growing list of individual parameters to each other.

    Populated progressively by the pipeline:
      _validate_package_paths -> boundaries_path, merged_path, metadata_path
      _load_metadata_config   -> metadata, layout_cfg, map_title, map_location,
                                  map_scale, layout_template_path, map_disclaimer,
                                  grid_label, orthomosaic_records, include_orthomosaic
      _compute_target_crs     -> center_lon, center_lat, target_epsg, target_crs
      _prepare_project        -> project
      _load_boundary_layers   -> boundaries_layer, merged_layer
      _load_orthomosaic_layers-> orthomosaic_layers
      _assemble_map_layers    -> basemap_layer, basemap_attribution, main_map_layers
      _build_print_layout     -> layout
    """

    # Set by _validate_package_paths
    boundaries_path: Path
    merged_path: Path
    metadata_path: Path

    # Set by _load_metadata_config
    metadata: dict[str, Any]
    layout_cfg: dict[str, Any]
    orthomosaic_records: list[Any]
    include_orthomosaic: bool
    map_title: str
    map_location: str
    map_scale: Any
    scale_mode: str
    fitted_map_scale: float
    auto_scale_multiplier: float | None
    selected_map_scale: int | float
    effective_map_scale: int | float
    extent_source: str
    grid_interval_degrees: float | None
    layout_template_path: Path | None
    map_disclaimer: str
    grid_label: str
    paper_size: str
    overview_label: str
    overview_bounds_wgs84: dict[str, float]
    boundary_inset_label: str
    boundary_inset_bounds_wgs84: dict[str, float] | None

    # Set by _compute_target_crs
    center_lon: float
    center_lat: float
    target_epsg: int
    target_crs: Any

    # Set by _prepare_project
    project: Any

    # Set by _load_boundary_layers
    boundaries_layer: Any
    merged_layer: Any

    # Set by _load_orthomosaic_layers
    orthomosaic_layers: list[Any]

    # Set by _assemble_map_layers
    basemap_layer: Any
    basemap_attribution: str
    main_map_layers: list[Any]
    layout_variables: dict[str, str]

    # Set by _build_print_layout
    layout: Any

    def __init__(
        self,
        *,
        package_dir: Path,
        title: str | None,
        logo_path: Path | None,
        dpi: int,
        qgis: SimpleNamespace,
        layout_variables: dict[str, str] | None = None,
        render_options: dict[str, Any] | None = None,
    ) -> None:
        self.package_dir = package_dir
        self.title = title
        self.logo_path = logo_path
        self.dpi = dpi
        self.qgis = qgis
        self.layout_variables = layout_variables or {}
        self.render_options = render_options
        self.map_states = {}
        self.live_insets = False


# QGIS import bootstrap

def _load_qgis_classes() -> SimpleNamespace:
    """
    Import all PyQGIS/Qt classes used throughout this module and return them
    as a single namespace, so callers only need to thread one object around
    instead of a long list of class references.
    """
    try:
        from qgis.core import (  # type: ignore[reportMissingImports]
            QgsApplication,
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsFillSymbol,
            QgsLayoutExporter,
            QgsLayoutItemLabel,
            QgsLayoutItemLegend,
            QgsLayoutItemMap,
            QgsLayoutItemMapGrid,
            QgsLayoutItemPage,
            QgsLayoutItemPicture,
            QgsLayoutItemScaleBar,
            QgsLayoutPoint,
            QgsLayoutSize,
            QgsPrintLayout,
            QgsProject,
            QgsRectangle,
            QgsUnitTypes,
            QgsVectorLayer,
            QgsFeature,
            QgsGeometry,
            QgsReadWriteContext,
            QgsRasterLayer,
        )
        from qgis.PyQt.QtGui import QColor, QFont  # type: ignore[reportMissingImports]
        from qgis.PyQt.QtXml import QDomDocument  # type: ignore[reportMissingImports]
    except ImportError as e:
        raise RuntimeError(
            "QGIS Python libraries are not available. "
            "Run this command inside the QGIS/OSGeo4W Python environment."
        ) from e

    return SimpleNamespace(
        QgsApplication=QgsApplication,
        QgsCoordinateReferenceSystem=QgsCoordinateReferenceSystem,
        QgsCoordinateTransform=QgsCoordinateTransform,
        QgsFillSymbol=QgsFillSymbol,
        QgsLayoutExporter=QgsLayoutExporter,
        QgsLayoutItemLabel=QgsLayoutItemLabel,
        QgsLayoutItemLegend=QgsLayoutItemLegend,
        QgsLayoutItemMap=QgsLayoutItemMap,
        QgsLayoutItemMapGrid=QgsLayoutItemMapGrid,
        QgsLayoutItemPage=QgsLayoutItemPage,
        QgsLayoutItemPicture=QgsLayoutItemPicture,
        QgsLayoutItemScaleBar=QgsLayoutItemScaleBar,
        QgsLayoutPoint=QgsLayoutPoint,
        QgsLayoutSize=QgsLayoutSize,
        QgsPrintLayout=QgsPrintLayout,
        QgsProject=QgsProject,
        QgsRectangle=QgsRectangle,
        QgsUnitTypes=QgsUnitTypes,
        QgsVectorLayer=QgsVectorLayer,
        QgsFeature=QgsFeature,
        QgsGeometry=QgsGeometry,
        QgsReadWriteContext=QgsReadWriteContext,
        QgsRasterLayer=QgsRasterLayer,
        QColor=QColor,
        QFont=QFont,
        QDomDocument=QDomDocument,
    )


# Pipeline stage 1: metadata & config loading

def _validate_package_paths(ctx: ExportContext) -> None:
    """Resolve and validate the required input files inside the package directory."""
    boundaries_path = ctx.package_dir / "boundaries.geojson"
    merged_path = ctx.package_dir / "merged_boundary.geojson"
    metadata_path = ctx.package_dir / "map_metadata.json"

    if not boundaries_path.exists():
        raise FileNotFoundError(f"Missing boundaries GeoJSON: {boundaries_path}")

    if not merged_path.exists():
        raise FileNotFoundError(f"Missing merged boundary GeoJSON: {merged_path}")

    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing map metadata JSON: {metadata_path}")

    ctx.boundaries_path = boundaries_path
    ctx.merged_path = merged_path
    ctx.metadata_path = metadata_path


def _load_metadata_config(ctx: ExportContext) -> None:
    """Load map_metadata.json and derive the layout configuration values."""
    metadata = json.loads(ctx.metadata_path.read_text(encoding="utf-8"))
    layout_cfg = metadata.get("layout") or {}

    orthomosaic_records = metadata.get("orthomosaics") or []
    include_orthomosaic = bool(metadata.get("include_orthomosaic")) and bool(orthomosaic_records)

    map_title = (
        ctx.title
        or layout_cfg.get("title")
        or metadata.get("title")
        or metadata.get("map_name")
        or "Survey Boundary Map"
    )

    map_location = (
        layout_cfg.get("location")
        or metadata.get("location")
        or ""
    )

    map_scale = layout_cfg.get("map_scale")

    layout_template_value = layout_cfg.get("layout_template")
    layout_template_path = (
        Path(layout_template_value)
        if layout_template_value
        else None
    )

    map_disclaimer = (
        layout_cfg.get("disclaimer")
        or "Disclaimer: This map is intended for visualization and reference purposes only. Not for legal boundary determination or survey-grade use."
    )

    grid_label = (
        layout_cfg.get("grid_label")
        or "WGS 84 Geographic Coordinates (EPSG:4326)"
    )

    overview_label, overview_bounds_wgs84 = _read_overview_inset_config()
    boundary_inset_label, boundary_inset_bounds_wgs84 = _read_inset_config(
        'MAP_EXPORT_BOUNDARY_INSET', map_location, None,
    )
    paper_size = _read_paper_size_env()

    ctx.metadata = metadata
    ctx.layout_cfg = layout_cfg
    ctx.orthomosaic_records = orthomosaic_records
    ctx.include_orthomosaic = include_orthomosaic
    ctx.map_title = map_title
    ctx.map_location = map_location
    ctx.map_scale = map_scale
    ctx.layout_template_path = layout_template_path
    ctx.map_disclaimer = map_disclaimer
    ctx.grid_label = grid_label
    ctx.paper_size = paper_size
    ctx.overview_label = overview_label
    ctx.overview_bounds_wgs84 = overview_bounds_wgs84
    ctx.boundary_inset_label = boundary_inset_label
    ctx.boundary_inset_bounds_wgs84 = boundary_inset_bounds_wgs84
    if ctx.render_options is not None:
        options = ctx.render_options
        ctx.paper_size = options['paper_size']
        ctx.map_scale = options['manual_scale']
        ctx.boundary_inset_label = options['boundary_label'] or ctx.map_location
        ctx.boundary_inset_bounds_wgs84 = options['boundary_bounds']
        ctx.overview_label = options['overview_label']
        ctx.overview_bounds_wgs84 = options['overview_bounds']
    if not ctx.layout_template_path:
        ctx.layout_template_path = Path(__file__).resolve().parents[2] / 'assets/qgis_layouts/client_boundary_map-v6.qpt'
    elif not ctx.layout_template_path.is_absolute():
        ctx.layout_template_path = Path(__file__).resolve().parents[2] / ctx.layout_template_path


def _configure_layout_variables(ctx: ExportContext) -> None:
    ctx.layout_variables = {
        "map_title": ctx.map_title,
        "map_location": ctx.map_location,
        "map_disclaimer": ctx.map_disclaimer,

        "osm_attribution": "",

        "map_info":
            f"Scale 1:{ctx.map_scale:,}\n"
            f"Projection: {_utm_label_from_epsg(ctx.target_epsg)} "
            f"(EPSG:{ctx.target_epsg})\n"
            f"Grid: {ctx.grid_label}",

        # inset labels
        "label_inset": ctx.boundary_inset_label,
        "label_inset_2": ctx.overview_label,
    }


def _compute_target_crs(ctx: ExportContext) -> None:
    """Determine the UTM CRS to use for the export based on the package bounds."""
    bounds = ctx.metadata.get("bounds") or {}
    center = ctx.metadata.get("center") or {}

    if not bounds:
        raise RuntimeError("Missing bounds in map_metadata.json. Cannot calculate map CRS.")

    center_lon = float(center.get("lon") or ((bounds["min_lon"] + bounds["max_lon"]) / 2))
    center_lat = float(center.get("lat") or ((bounds["min_lat"] + bounds["max_lat"]) / 2))

    target_epsg = _utm_epsg_from_lonlat(center_lon, center_lat)
    target_crs = ctx.qgis.QgsCoordinateReferenceSystem(f"EPSG:{target_epsg}")

    ctx.center_lon = center_lon
    ctx.center_lat = center_lat
    ctx.target_epsg = target_epsg
    ctx.target_crs = target_crs


# Pipeline stage 2: project & layer preparation

def _prepare_project(ctx: ExportContext) -> None:
    """Reset the QGIS project instance and set its working CRS."""
    if getattr(ctx, 'render_options', None) is not None:
        project = ctx.qgis.QgsProject()
    else:
        project = ctx.qgis.QgsProject.instance()
        project.clear()
    project.setCrs(ctx.target_crs)

    ctx.project = project


def _load_boundary_layers(ctx: ExportContext) -> None:
    """Load the raw survey boundaries and derive the dissolved overall boundary."""
    q = ctx.qgis

    boundaries_layer = q.QgsVectorLayer(str(ctx.boundaries_path), "Survey Boundaries", "ogr")
    if not boundaries_layer.isValid():
        raise RuntimeError(f"Failed to load layer: {ctx.boundaries_path}")

    source_crs = q.QgsCoordinateReferenceSystem("EPSG:4326")
    boundaries_layer.setCrs(source_crs)

    # Create a true dissolved boundary for print output.
    # This removes internal lines between adjacent/overlapping survey boundaries.
    merged_layer = _create_dissolved_boundary_layer(
        source_layer=boundaries_layer,
        layer_name="Overall Boundary",
        project=ctx.project,
        target_crs=ctx.target_crs,
        dissolve_tolerance_m=3.0,
    )

    _style_polygon_layer(
        merged_layer,
        stroke="#991b1b",
        fill="#fca5a5",
        opacity=0.32,
        width=0.55,
    )

    ctx.boundaries_layer = boundaries_layer
    ctx.merged_layer = merged_layer


def _load_orthomosaic_layers(ctx: ExportContext) -> None:
    """Load orthomosaic raster layers referenced in the metadata, if requested."""
    orthomosaic_layers: list[Any] = []

    if ctx.include_orthomosaic:
        for record in ctx.orthomosaic_records:
            ortho_path_value = (
                record.get("relative_orthomosaic")
                or record.get("packaged_orthomosaic")
                or record.get("source_orthomosaic")
            )

            if not ortho_path_value:
                raise RuntimeError(
                    f"Missing source_orthomosaic value in metadata record: {record}"
                )

            ortho_path = Path(ortho_path_value)

            if not ortho_path.is_absolute():
                ortho_path = ctx.package_dir / ortho_path

            if not ortho_path.exists():
                raise FileNotFoundError(f"Orthomosaic not found: {ortho_path}")

            survey_name = record.get("survey_name") or ortho_path.stem

            ortho_layer = ctx.qgis.QgsRasterLayer(
                str(ortho_path),
                f"Orthomosaic - {survey_name}",
            )

            if not ortho_layer.isValid():
                raise RuntimeError(f"Failed to load orthomosaic raster: {ortho_path}")

            orthomosaic_layers.append(ortho_layer)

        if not orthomosaic_layers:
            raise RuntimeError("Orthomosaic export was requested, but no raster layers were loaded.")

    ctx.orthomosaic_layers = orthomosaic_layers


def _assemble_map_layers(ctx: ExportContext) -> None:
    """
    Pick a basemap and register all layers with the project in the correct
    draw order: boundary on top, then orthomosaics, then basemap at the
    bottom (QgsLayoutItemMap layer order is top-first).
    """
    basemap_layer, basemap_attribution = _create_preferred_basemap_layer(ctx.qgis.QgsRasterLayer)

    if basemap_layer is not None:
        ctx.project.addMapLayer(basemap_layer)

    if ctx.orthomosaic_layers:
        for ortho_layer in ctx.orthomosaic_layers:
            ctx.project.addMapLayer(ortho_layer)

    # Add boundary layer after background layers.
    ctx.project.addMapLayer(ctx.merged_layer)

    main_map_layers: list[Any] = [ctx.merged_layer]

    if ctx.orthomosaic_layers:
        main_map_layers.extend(ctx.orthomosaic_layers)

    if basemap_layer is not None:
        main_map_layers.append(basemap_layer)

    ctx.basemap_layer = basemap_layer
    ctx.basemap_attribution = basemap_attribution
    ctx.main_map_layers = main_map_layers
    if getattr(ctx, 'map_styles', None) is not None:
        ctx.frame_basemaps = {}
        ctx.frame_overlays = {'map_frame': ctx.merged_layer}
        for key, style in ctx.map_styles.items():
            layer = basemap_layer.clone() if basemap_layer is not None else None
            if layer is not None:
                layer.setOpacity(style['basemap_opacity'])
                ctx.project.addMapLayer(layer, False)
            ctx.frame_basemaps[key] = layer
        ctx.main_map_layers = [ctx.merged_layer] + ctx.orthomosaic_layers + (
            [ctx.frame_basemaps['map_frame']] if basemap_layer is not None else [])
        _apply_boundary_style(ctx.merged_layer, ctx.map_styles['map_frame'])


# Pipeline stage 3: layout configuration

def _build_print_layout(ctx: ExportContext) -> None:
    """Create the print layout from the configured QPT template."""
    q = ctx.qgis

    if not ctx.layout_template_path or not ctx.layout_template_path.exists():
        raise RuntimeError(
            "Missing QGIS layout template. Expected: "
            f"{ctx.layout_template_path or 'assets/qgis_layouts/client_boundary_map.qpt'}"
        )

    layout = q.QgsPrintLayout(ctx.project)
    layout.initializeDefaults()
    layout.setName("print_map_layout")

    doc = q.QDomDocument()
    template_text = ctx.layout_template_path.read_text(encoding="utf-8")

    result = doc.setContent(template_text)

    if isinstance(result, tuple):
        ok = result[0]
        error_message = result[1] if len(result) > 1 else ""
    else:
        ok = bool(result)
        error_message = ""

    if not ok:
        raise RuntimeError(
            f"Failed to load QPT template: {ctx.layout_template_path}. {error_message}"
        )

    layout.loadFromTemplate(doc, q.QgsReadWriteContext())

    ctx.layout = layout
    _configure_layout_paper_size(ctx)


def _configure_layout_paper_size(ctx: ExportContext) -> None:
    """Resize the template page and proportionally transform its items."""
    q = ctx.qgis
    page_collection = ctx.layout.pageCollection()
    if page_collection.pageCount() < 1:
        raise RuntimeError('QGIS layout template contains no pages')

    page = page_collection.page(0)
    current_size = page.pageSize()
    current_width = float(current_size.width())
    current_height = float(current_size.height())
    target_width, target_height = PAPER_SIZES_MM[ctx.paper_size]

    if current_width <= 0 or current_height <= 0:
        raise RuntimeError(
            'QGIS layout template returned an invalid page size: '
            f'{current_width} x {current_height} mm'
        )

    scale_x = target_width / current_width
    scale_y = target_height / current_height
    unchanged = (
        math.isclose(scale_x, 1.0, rel_tol=0.0, abs_tol=1e-9)
        and math.isclose(scale_y, 1.0, rel_tol=0.0, abs_tol=1e-9)
    )

    if not unchanged:
        page.setPageSize(
            q.QgsLayoutSize(
                target_width,
                target_height,
                q.QgsUnitTypes.LayoutMillimeters,
            )
        )

        for item in ctx.layout.items():
            if item is page or isinstance(item, q.QgsLayoutItemPage):
                continue
            if not all(
                hasattr(item, name)
                for name in (
                    'positionWithUnits',
                    'sizeWithUnits',
                    'attemptMove',
                    'attemptResize',
                )
            ):
                continue

            position = item.positionWithUnits()
            size = item.sizeWithUnits()
            item.attemptMove(
                q.QgsLayoutPoint(
                    position.x() * scale_x,
                    position.y() * scale_y,
                    position.units(),
                )
            )
            item.attemptResize(
                q.QgsLayoutSize(
                    size.width() * scale_x,
                    size.height() * scale_y,
                    size.units(),
                )
            )

        ctx.layout.refresh()

    print(
        f'Layout paper size: {ctx.paper_size} landscape '
        f'({target_width:g} x {target_height:g} mm)',
        flush=True,
    )


def _configure_main_map(ctx: ExportContext) -> Any:
    """Fit the main map to orthomosaic extents and adapt its map furniture."""
    q = ctx.qgis

    map_item = _get_layout_item(ctx.layout, "map_frame", q.QgsLayoutItemMap)

    fit_layers = ctx.orthomosaic_layers or [ctx.merged_layer]
    default_buffer = 0.08 if ctx.orthomosaic_layers else 0.12

    map_extent = _prepare_fitted_extent(
        project=ctx.project,
        layers=fit_layers,
        target_crs=ctx.target_crs,
        frame_rect=map_item.rect(),
        buffer_factor=_render_option(ctx, 'main_buffer', lambda: _read_nonnegative_float_env(
            'MAP_EXPORT_MAIN_BUFFER',
            default_buffer,
        )),
    )

    map_item.setCrs(ctx.target_crs)
    set_extent = (map_item.zoomToExtent if getattr(ctx, 'render_options', None) is not None
                  else map_item.setExtent)
    set_extent(map_extent)
    map_item.setLayers(ctx.main_map_layers)
    map_item.setKeepLayerSet(True)
    map_item.refresh()

    fitted_scale = float(map_item.scale())
    if not math.isfinite(fitted_scale) or fitted_scale <= 0:
        raise RuntimeError(
            f'QGIS returned an invalid fitted main-map scale: {fitted_scale!r}'
        )

    multiplier = DEFAULT_AUTO_SCALE_MULTIPLIER
    if ctx.map_scale is None:
        multiplier = _render_option(ctx, 'auto_scale_multiplier', lambda: _read_float_env_at_least(
            'MAP_EXPORT_AUTO_SCALE_MULTIPLIER',
            DEFAULT_AUTO_SCALE_MULTIPLIER,
            minimum=1.0,
        ))
    scale_decision = _select_main_map_scale(
        fitted_scale=fitted_scale,
        manual_scale=ctx.map_scale,
        auto_multiplier=multiplier,
    )

    map_item.setScale(scale_decision['selected_scale'])
    saved_extent = _saved_map_extent(ctx, 'map_frame')
    if saved_extent is not None:
        set_extent(saved_extent)
        scale_decision.update(scale_mode='manual', auto_scale_multiplier=None,
                              selected_scale=float(map_item.scale()))
    map_item.refresh()

    effective_scale = float(map_item.scale())
    if not math.isfinite(effective_scale) or effective_scale <= 0:
        raise RuntimeError(
            f'QGIS returned an invalid final main-map scale: {effective_scale!r}'
        )

    ctx.scale_mode = scale_decision['scale_mode']
    ctx.fitted_map_scale = scale_decision['fitted_scale']
    ctx.auto_scale_multiplier = scale_decision['auto_scale_multiplier']
    ctx.selected_map_scale = scale_decision['selected_scale']
    ctx.effective_map_scale = _clean_scale_value(effective_scale)
    ctx.map_scale = ctx.effective_map_scale
    ctx.extent_source = (
        'orthomosaic'
        if ctx.orthomosaic_layers
        else 'boundary'
    )

    displayed_extent = map_item.extent()
    grid_crs = q.QgsCoordinateReferenceSystem('EPSG:4326')
    to_grid_crs = q.QgsCoordinateTransform(
        ctx.target_crs,
        grid_crs,
        ctx.project,
    )
    geographic_extent = to_grid_crs.transformBoundingBox(displayed_extent)

    ctx.grid_interval_degrees = _add_coordinate_grid(
        map_item=map_item,
        extent=geographic_extent,
        grid_crs=grid_crs,
        QgsLayoutItemMapGrid=q.QgsLayoutItemMapGrid,
        QColor=q.QColor,
        QFont=q.QFont,
        annotation_font_size=_render_option(ctx, 'grid_font_size', lambda: 6.0),
        grid_style=getattr(ctx, 'grid_style', None),
    )
    _configure_scale_bar(
        ctx=ctx,
        map_item=map_item,
        extent=displayed_extent,
    )
    map_item.refresh()

    return map_item


def _configure_insets(ctx: ExportContext) -> None:
    """Configure the location and extent inset maps on the layout."""
    q = ctx.qgis

    # Inset 1: automatically fit the survey boundary unless fixed bounds
    # and a matching label have been configured by the operator.
    upper_crs_name = _render_option(ctx, 'boundary_crs', lambda: 'project')
    upper_crs = ctx.target_crs if upper_crs_name == 'project' else q.QgsCoordinateReferenceSystem(upper_crs_name)
    upper_overlay = _configure_inset_map(
        layout=ctx.layout,
        project=ctx.project,
        package_dir=getattr(ctx, 'output_dir', ctx.package_dir),
        basemap_layer=_inset_basemap_with_native_dpi(ctx, 'map_inset'),
        boundary_layer=ctx.merged_layer,
        target_crs=upper_crs,
        QgsLayoutItemPicture=q.QgsLayoutItemPicture,
        QgsCoordinateReferenceSystem=q.QgsCoordinateReferenceSystem,
        map_item_id="map_inset",
        fixed_extent_wgs84=ctx.boundary_inset_bounds_wgs84,
        zoom_out_factor=_render_option(ctx, 'boundary_zoom', lambda: _read_float_env_at_least(
            'MAP_EXPORT_BOUNDARY_INSET_ZOOM_OUT', 1.0, minimum=1.0,
        )),
        overlay_mode=_render_option(ctx, 'boundary_overlay', lambda: _read_inset_overlay_mode(
            'MAP_EXPORT_BOUNDARY_INSET_OVERLAY',
        )),
        live=getattr(ctx, 'live_insets', False),
        extent_override=_saved_map_extent(ctx, 'map_inset', target_crs=upper_crs),
        overlay_style=_frame_style(ctx, 'map_inset'),
        buffer_factor=_render_option(ctx, 'boundary_buffer', lambda: _read_nonnegative_float_env('MAP_EXPORT_BOUNDARY_INSET_BUFFER', 4.0)),
        inset_dpi=_render_option(ctx, 'inset_dpi', lambda: _read_int_env('MAP_EXPORT_INSET_DPI', 800)),
    )

    # Inset 2: fixed configurable overview, with a boundary or locator pin.
    overview_crs_name = _render_option(ctx, 'overview_crs', _read_overview_crs)
    overview_crs = (ctx.target_crs if overview_crs_name == 'project'
                    else q.QgsCoordinateReferenceSystem(overview_crs_name))
    lower_overlay = _configure_inset_map(
        layout=ctx.layout,
        project=ctx.project,
        package_dir=getattr(ctx, 'output_dir', ctx.package_dir),
        basemap_layer=_inset_basemap_with_native_dpi(ctx, 'map_inset_2'),
        boundary_layer=ctx.merged_layer,
        target_crs=overview_crs,
        QgsLayoutItemPicture=q.QgsLayoutItemPicture,
        QgsCoordinateReferenceSystem=q.QgsCoordinateReferenceSystem,
        map_item_id="map_inset_2",
        overlay_mode=_render_option(ctx, 'overview_overlay', _read_overview_overlay_mode),
        fixed_extent_wgs84=ctx.overview_bounds_wgs84,
        zoom_out_factor=_render_option(ctx, 'overview_zoom', lambda: _read_float_env_at_least(
            'MAP_EXPORT_OVERVIEW_ZOOM_OUT', 1.0, minimum=1.0,
        )),
        live=getattr(ctx, 'live_insets', False),
        extent_override=_saved_map_extent(ctx, 'map_inset_2', target_crs=overview_crs),
        overlay_style=_frame_style(ctx, 'map_inset_2'),
        inset_dpi=_render_option(ctx, 'inset_dpi', lambda: _read_int_env('MAP_EXPORT_INSET_DPI', 800)),
    )
    if getattr(ctx, 'map_styles', None) is not None:
        ctx.frame_overlays.update(map_inset=upper_overlay, map_inset_2=lower_overlay)


# Pipeline stage 4: export

def _build_print_layout_metadata(
    ctx: ExportContext,
    *,
    exported_at: str,
) -> dict[str, Any]:
    return {
        'exported_at': exported_at,
        'crs': f'EPSG:{ctx.target_epsg}',
        'scale': ctx.effective_map_scale,
        'scale_mode': ctx.scale_mode,
        'fitted_scale': ctx.fitted_map_scale,
        'auto_scale_multiplier': ctx.auto_scale_multiplier,
        'selected_scale': ctx.selected_map_scale,
        'extent_source': ctx.extent_source,
        'grid_interval_degrees': ctx.grid_interval_degrees,
        'paper_size': ctx.paper_size,
        'orientation': 'landscape',
        'dpi': ctx.dpi,
        'logo': str(ctx.logo_path) if ctx.logo_path else None,
        'render_options': _resolved_render_options(ctx),
        'inset_tile_scaling': getattr(ctx, 'inset_tile_scaling', {}),
    }


def _export_print_outputs(ctx: ExportContext) -> dict[str, str]:
    """Export the PNG preview and PDF, then write the updated package metadata."""
    q = ctx.qgis

    output_dir = getattr(ctx, 'output_dir', ctx.package_dir)
    pdf_path = output_dir / "print_map.pdf"
    png_path = output_dir / "preview.png"
    pdf_temp_path = output_dir / "print_map_tmp.pdf"
    png_temp_path = output_dir / "preview_tmp.png"

    for path in (pdf_path, png_path, pdf_temp_path, png_temp_path):
        path.unlink(missing_ok=True)

    # -----------------------------
    # DEBUG
    # -----------------------------
    title_item = ctx.layout.itemById("map_title")

    print("\n===== EXPORT TITLE =====")
    print("Title:", title_item.text())
    print("========================\n")

    # -----------------------------
    # Exporter
    # -----------------------------

    exporter = q.QgsLayoutExporter(ctx.layout)

    preview_dpi = _render_option(ctx, 'preview_dpi', lambda: _read_int_env("MAP_EXPORT_PREVIEW_DPI", 120))
    pdf_dpi = _render_option(ctx, 'pdf_dpi', lambda: _read_int_env("MAP_EXPORT_PDF_DPI", 300))

    # Export preview first so we can verify layout before PDF.
    image_settings = q.QgsLayoutExporter.ImageExportSettings()
    image_settings.dpi = preview_dpi

    _run_layout_export(
        export_callable=lambda p: exporter.exportToImage(p, image_settings),
        output_path=png_temp_path,
        success_code=q.QgsLayoutExporter.Success,
        label=f"PNG preview at {preview_dpi} DPI",
        short_label="PNG",
    )

    png_temp_path.replace(png_path)

    # Export PDF after preview.
    pdf_settings = q.QgsLayoutExporter.PdfExportSettings()
    pdf_settings.dpi = pdf_dpi
    pdf_settings.rasterizeWholeImage = True

    _run_layout_export(
        export_callable=lambda p: exporter.exportToPdf(p, pdf_settings),
        output_path=pdf_temp_path,
        success_code=q.QgsLayoutExporter.Success,
        label=f"PDF at {pdf_dpi} DPI",
        short_label="PDF",
    )

    pdf_temp_path.replace(pdf_path)

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    ctx.metadata.setdefault("outputs", {})
    ctx.metadata["outputs"]["print_map_pdf"] = "print_map.pdf"
    ctx.metadata["print_layout"] = _build_print_layout_metadata(
        ctx,
        exported_at=created_at,
    )
    if getattr(ctx, 'editor_state', None) is not None:
        ctx.metadata['print_layout']['editor_state'] = ctx.editor_state

    ctx.metadata_path.write_text(
        json.dumps(ctx.metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "pdf": str(pdf_path),
        "preview": str(png_path),
        "metadata": str(ctx.metadata_path),
        "crs": f"EPSG:{ctx.target_epsg}",
    }


# Public entry point

def export_qgis_print_layout(
    *,
    package_dir: Path,
    title: str | None = None,
    logo_path: Path | None = None,
    dpi: int = 400,
    layout_variables: dict[str, str] | None = None,
) -> dict[str, str]:
    ctx = build_qgis_print_layout(
        package_dir=package_dir, title=title, logo_path=logo_path,
        dpi=dpi, layout_variables=layout_variables,
    )
    return _export_print_outputs(ctx)


def build_qgis_print_layout(
    *, package_dir: Path, title: str | None = None, logo_path: Path | None = None,
    dpi: int = 400, layout_variables: dict[str, str] | None = None,
    render_options: dict[str, Any] | None = None, live_insets: bool = False,
    map_states: dict[str, Any] | None = None, text_values: dict[str, str] | None = None,
    text_styles: dict[str, Any] | None = None, output_dir: Path | None = None,
    map_styles: dict[str, Any] | None = None, grid_style: dict[str, Any] | None = None,
) -> ExportContext:
    """
    Build a shared layout; live insets do not write images or final outputs.

    Requires QGIS Python environment.
    Input package must contain:
    - boundaries.geojson
    - merged_boundary.geojson
    - map_metadata.json
    """
    if render_options is not None:
        from .editor_model import validate_options
        render_options = validate_options(render_options)
    qgis_classes = _load_qgis_classes()
    _init_qgis_application(qgis_classes.QgsApplication)
    if render_options is not None and not live_insets:
        _require_export_fonts()

    ctx = ExportContext(
        package_dir=Path(package_dir),
        title=title,
        logo_path=logo_path,
        dpi=dpi,
        qgis=qgis_classes,
        layout_variables=layout_variables,
        render_options=render_options,
    )
    ctx.map_states = map_states or {}
    ctx.live_insets = live_insets
    ctx.map_styles, ctx.grid_style = None, None
    if map_styles is not None or grid_style is not None:
        from .editor_model import validate_map_styles, validate_style
        ctx.map_styles = validate_map_styles(map_styles) if map_styles is not None else None
        ctx.grid_style = validate_style(grid_style, grid=True) if grid_style is not None else None
    if output_dir is not None:
        ctx.output_dir = Path(output_dir)

    _validate_package_paths(ctx)
    _load_metadata_config(ctx)
    if logo_path is None and render_options is not None:
        value = (ctx.metadata.get('print_layout') or {}).get('logo')
        ctx.logo_path = Path(value) if value else Path(__file__).resolve().parents[2] / 'assets/logo.png'
    _compute_target_crs(ctx)
    _prepare_project(ctx)
    _load_boundary_layers(ctx)
    _load_orthomosaic_layers(ctx)
    _assemble_map_layers(ctx)
    _build_print_layout(ctx)
    _configure_main_map(ctx)
    _configure_layout_variables(ctx)
    _apply_layout_variables(ctx)
    _configure_insets(ctx)
    apply_editor_text(ctx, text_values or {}, text_styles or {})
    return ctx


def _render_option(ctx, name, legacy_default):
    options = getattr(ctx, 'render_options', None)
    return options[name] if options is not None else legacy_default()


def _require_export_fonts():
    from qgis.PyQt.QtGui import QFontDatabase
    if not QFontDatabase().families():
        raise RuntimeError(
            'Qt export font database is empty; export stopped to avoid unreadable text. '
            'Restart the editor using run_map_editor.bat. Windows exports must use '
            'QT_QPA_PLATFORM=windows, not offscreen.'
        )


def _saved_map_extent(ctx, item_id, *, target_crs=None, state=None):
    """Preserve saved framing; only inset frames support changing map CRS."""
    state = state if state is not None else getattr(ctx, 'map_states', {}).get(item_id)
    if not state:
        return None
    target_crs = target_crs if target_crs is not None else ctx.target_crs
    if state['crs'] != target_crs.authid() and item_id not in ('map_inset', 'map_inset_2'):
        raise ValueError(f'Saved extent CRS does not match package CRS: {item_id}')
    extent = ctx.qgis.QgsRectangle(*state['extent'])
    if state['crs'] != target_crs.authid():
        # Older editor designs stored every frame in the main map's CRS.
        # Transform their overview bounds, never relabel projected coordinates.
        source_crs = ctx.qgis.QgsCoordinateReferenceSystem(state['crs'])
        transform = ctx.qgis.QgsCoordinateTransform(source_crs, target_crs, ctx.project)
        extent = transform.transformBoundingBox(extent)
        frame = ctx.layout.itemById(item_id).rect()
        extent = _fit_extent_to_frame(extent, frame_width=frame.width(), frame_height=frame.height())
    return extent


def _resolved_render_options(ctx):
    if getattr(ctx, 'render_options', None) is not None:
        return dict(ctx.render_options)
    return {
        'paper_size': ctx.paper_size,
        'grid_font_size': 6.0,
        'preview_dpi': _read_int_env('MAP_EXPORT_PREVIEW_DPI', 120),
        'pdf_dpi': _read_int_env('MAP_EXPORT_PDF_DPI', 300),
        'inset_dpi': _read_int_env('MAP_EXPORT_INSET_DPI', 800),
        'main_buffer': _read_nonnegative_float_env('MAP_EXPORT_MAIN_BUFFER', 0.08),
        'auto_scale_multiplier': _read_float_env_at_least('MAP_EXPORT_AUTO_SCALE_MULTIPLIER', 2.5, minimum=1),
        'manual_scale': ctx.selected_map_scale if ctx.scale_mode == 'manual' else None,
        'boundary_buffer': _read_nonnegative_float_env('MAP_EXPORT_BOUNDARY_INSET_BUFFER', 4.0),
        'boundary_label': getattr(ctx, 'boundary_inset_label', None),
        'boundary_bounds': getattr(ctx, 'boundary_inset_bounds_wgs84', None),
        'boundary_zoom': _read_float_env_at_least('MAP_EXPORT_BOUNDARY_INSET_ZOOM_OUT', 1, minimum=1),
        'boundary_overlay': _read_inset_overlay_mode('MAP_EXPORT_BOUNDARY_INSET_OVERLAY'),
        'overview_label': getattr(ctx, 'overview_label', DEFAULT_OVERVIEW_LABEL),
        'overview_bounds': getattr(ctx, 'overview_bounds_wgs84', dict(DAVAO_CITY_BOUNDS_WGS84)),
        'overview_zoom': _read_float_env_at_least('MAP_EXPORT_OVERVIEW_ZOOM_OUT', 1, minimum=1),
        'overview_overlay': _read_overview_overlay_mode(),
        'overview_crs': _read_overview_crs(),
        'boundary_crs': 'project',
    }


def apply_editor_text(ctx, values, styles):
    for item_id in values.keys() | styles.keys():
        item = ctx.layout.itemById(item_id)
        if item is None or not isinstance(item, ctx.qgis.QgsLayoutItemLabel):
            raise ValueError(f'Missing editable layout label: {item_id}')
        if item_id in values:
            item.setText(values[item_id])
        if item_id in styles:
            style = styles[item_id]
            font = ctx.qgis.QFont(item.font())
            font.setFamily(style['family'])
            font.setPointSizeF(style['size'])
            font.setBold(style['bold'])
            item.setFont(font)
            item.setFontColor(ctx.qgis.QColor(style['color']))
        item.refresh()


def refresh_main_map_furniture(ctx):
    """Update furniture from the current extent without fitting or snapping scale."""
    q = ctx.qgis
    item = ctx.layout.itemById('map_frame')
    ctx.map_scale = ctx.effective_map_scale = ctx.selected_map_scale = _clean_scale_value(float(item.scale()))
    ctx.scale_mode, ctx.auto_scale_multiplier = 'manual', None
    grid_crs = q.QgsCoordinateReferenceSystem('EPSG:4326')
    geographic = q.QgsCoordinateTransform(ctx.target_crs, grid_crs, ctx.project).transformBoundingBox(item.extent())
    ctx.grid_interval_degrees = _add_coordinate_grid(
        map_item=item, extent=geographic, grid_crs=grid_crs,
        QgsLayoutItemMapGrid=q.QgsLayoutItemMapGrid, QColor=q.QColor, QFont=q.QFont,
        annotation_font_size=_render_option(ctx, 'grid_font_size', lambda: 6.0),
        grid_style=getattr(ctx, 'grid_style', None),
    )
    _configure_scale_bar(ctx=ctx, map_item=item, extent=item.extent())
    info = ctx.layout.itemById('map_info')
    if info is not None:
        info.setText(f'Scale 1:{ctx.map_scale:,}\nProjection: {_utm_label_from_epsg(ctx.target_epsg)} '
                     f'(EPSG:{ctx.target_epsg})\nGrid: {ctx.grid_label}')
        info.refresh()


_QGIS_APP = None


def _init_qgis_application(QgsApplication):
    """
    Initialize QGIS when running PyQGIS from a standalone Python script.
    This is required when running map.py from QGIS/OSGeo4W Shell.
    """
    global _QGIS_APP

    if QgsApplication.instance() is not None:
        return

    qgis_prefix = os.environ.get("QGIS_PREFIX_PATH")

    if not qgis_prefix:
        osgeo_root = os.environ.get("OSGEO4W_ROOT")

        if osgeo_root:
            qgis_ltr = Path(osgeo_root) / "apps" / "qgis-ltr"
            qgis_default = Path(osgeo_root) / "apps" / "qgis"

            if qgis_ltr.exists():
                qgis_prefix = str(qgis_ltr)
            elif qgis_default.exists():
                qgis_prefix = str(qgis_default)

    if not qgis_prefix:
        raise RuntimeError(
            "QGIS_PREFIX_PATH is not set. Please run this from QGIS/OSGeo4W Shell "
            "or set QGIS_PREFIX_PATH manually."
        )

    QgsApplication.setPrefixPath(qgis_prefix, True)

    _QGIS_APP = QgsApplication([], False)
    _QGIS_APP.initQgis()


def _create_dissolved_boundary_layer(
    *,
    source_layer: Any,
    layer_name: str,
    project: Any,
    target_crs: Any,
    dissolve_tolerance_m: float = 2.0,
) -> Any:
    """
    Create one dissolved overall boundary layer.

    Uses a small buffer-union-negative-buffer workflow in projected CRS.
    This helps remove internal lines caused by tiny gaps, overlaps, or
    imperfect boundary alignment.
    """
    from qgis.core import (
        QgsCoordinateTransform,
        QgsFeature,
        QgsGeometry,
        QgsVectorLayer,
    )

    crs_authid = target_crs.authid() or "EPSG:3857"

    dissolved_layer = QgsVectorLayer(
        f"MultiPolygon?crs={crs_authid}",
        layer_name,
        "memory",
    )

    provider = dissolved_layer.dataProvider()

    transform = QgsCoordinateTransform(
        source_layer.crs(),
        target_crs,
        project,
    )

    geometries = []

    for feature in source_layer.getFeatures():
        if not feature.hasGeometry():
            continue

        geometry = QgsGeometry(feature.geometry())

        if geometry is None or geometry.isEmpty():
            continue

        if not geometry.isGeosValid():
            geometry = geometry.makeValid()

        geometry.transform(transform)

        # Clean small geometry issues after reprojection.
        geometry = geometry.buffer(0, 8)

        # Expand slightly so tiny gaps/edge mismatches touch before dissolve.
        if dissolve_tolerance_m > 0:
            geometry = geometry.buffer(dissolve_tolerance_m, 8)

        if geometry and not geometry.isEmpty():
            geometries.append(geometry)

    if not geometries:
        raise RuntimeError("No valid boundary geometries found for dissolve.")

    dissolved_geometry = QgsGeometry.unaryUnion(geometries)

    if not dissolved_geometry or dissolved_geometry.isEmpty():
        raise RuntimeError("Boundary dissolve produced an empty geometry.")

    if not dissolved_geometry.isGeosValid():
        dissolved_geometry = dissolved_geometry.makeValid()

    # Shrink back after the positive buffer.
    if dissolve_tolerance_m > 0:
        dissolved_geometry = dissolved_geometry.buffer(-dissolve_tolerance_m, 8)

    # Final cleanup.
    if not dissolved_geometry.isGeosValid():
        dissolved_geometry = dissolved_geometry.makeValid()

    dissolved_geometry = dissolved_geometry.buffer(0, 8)

    dissolved_feature = QgsFeature()
    dissolved_feature.setGeometry(dissolved_geometry)

    provider.addFeatures([dissolved_feature])
    dissolved_layer.updateExtents()

    if not dissolved_layer.isValid():
        raise RuntimeError("Failed to create dissolved overall boundary layer.")

    return dissolved_layer


def _fit_extent_to_frame(
    extent: Any,
    *,
    frame_width: float,
    frame_height: float,
) -> Any:
    from qgis.core import QgsRectangle

    width = extent.width()
    height = extent.height()

    if width <= 0:
        width = 100

    if height <= 0:
        height = 100

    target_ratio = frame_width / frame_height
    current_ratio = width / height

    center_x = (extent.xMinimum() + extent.xMaximum()) / 2
    center_y = (extent.yMinimum() + extent.yMaximum()) / 2

    if current_ratio > target_ratio:
        height = width / target_ratio
    else:
        width = height * target_ratio

    return QgsRectangle(
        center_x - width / 2,
        center_y - height / 2,
        center_x + width / 2,
        center_y + height / 2,
    )


def _style_polygon_layer(
    layer: Any,
    *,
    stroke: str,
    fill: str,
    opacity: float,
    width: float,
) -> None:
    from qgis.core import QgsFillSymbol, QgsSingleSymbolRenderer

    symbol = QgsFillSymbol.createSimple(
        {
            "color": fill,
            "outline_color": stroke,
            "outline_width": str(width),
        }
    )

    symbol.setOpacity(opacity)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


def _combined_projected_extent(
    *,
    project: Any,
    layers: list[Any],
    target_crs: Any,
) -> Any:
    from qgis.core import QgsCoordinateTransform

    combined = None

    for layer in layers:
        transform = QgsCoordinateTransform(
            layer.crs(),
            target_crs,
            project,
        )

        extent = transform.transformBoundingBox(layer.extent())

        if combined is None:
            combined = extent
        else:
            combined.combineExtentWith(extent)

    if combined is None:
        raise RuntimeError("Could not calculate map extent.")

    return combined


def _buffer_extent(extent: Any, *, factor: float) -> Any:
    width = extent.width()
    height = extent.height()

    if width <= 0:
        width = 100

    if height <= 0:
        height = 100

    x_buffer = width * factor
    y_buffer = height * factor

    extent.setXMinimum(extent.xMinimum() - x_buffer)
    extent.setXMaximum(extent.xMaximum() + x_buffer)
    extent.setYMinimum(extent.yMinimum() - y_buffer)
    extent.setYMaximum(extent.yMaximum() + y_buffer)

    return extent


def _prepare_fitted_extent(
    *,
    project: Any,
    layers: list[Any],
    target_crs: Any,
    frame_rect: Any,
    buffer_factor: float = 0.0,
) -> Any:
    """
    Shared extent-preparation workflow: combine layer extents in the target
    CRS, fit the result to a layout frame's aspect ratio, then optionally pad
    it by a proportional buffer. Used by both the main map and inset maps.
    """
    extent = _combined_projected_extent(
        project=project,
        layers=layers,
        target_crs=target_crs,
    )

    extent = _fit_extent_to_frame(
        extent,
        frame_width=frame_rect.width(),
        frame_height=frame_rect.height(),
    )

    if buffer_factor:
        extent = _buffer_extent(extent, factor=buffer_factor)

    return extent


def _prepare_fixed_extent(
    *,
    project: Any,
    wgs84_bounds: dict[str, float],
    target_crs: Any,
    QgsCoordinateReferenceSystem: Any,
    frame_rect: Any,
) -> Any:
    """
    Build a fitted extent from a static WGS84 bounding box rather than from
    layer geometry. Used for reference insets (e.g. a fixed "Davao City"
    overview) that should not move based on the current survey's location.
    """
    from qgis.core import QgsCoordinateTransform, QgsRectangle

    source_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    raw_extent = QgsRectangle(
        wgs84_bounds["min_lon"],
        wgs84_bounds["min_lat"],
        wgs84_bounds["max_lon"],
        wgs84_bounds["max_lat"],
    )

    transform = QgsCoordinateTransform(source_crs, target_crs, project)
    extent = transform.transformBoundingBox(raw_extent)

    return _fit_extent_to_frame(
        extent,
        frame_width=frame_rect.width(),
        frame_height=frame_rect.height(),
    )


def _run_layout_export(
    *,
    export_callable: Callable[[str], Any],
    output_path: Path,
    success_code: Any,
    label: str,
    short_label: str | None = None,
) -> None:
    """
    Shared export-and-validate workflow: run a QGIS export callable, log the
    result, and confirm the output file exists and is non-empty. Used by the
    PNG preview, PDF, and inset basemap image exports.
    """
    short_label = short_label or label

    print(f"Exporting {label}: {output_path}", flush=True)

    result = export_callable(str(output_path))

    print(f"{short_label} export result: {result}", flush=True)

    if result != success_code:
        raise RuntimeError(f"Failed to export {short_label}: {output_path}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"{short_label} export produced an empty file: {output_path}")


def _add_coordinate_grid(
    *,
    map_item: Any,
    extent: Any,
    grid_crs: Any,
    QgsLayoutItemMapGrid: Any,
    QColor: Any,
    QFont: Any,
    annotation_font_size: float = 6.0,
    grid_style: dict[str, Any] | None = None,
) -> float | None:
    try:
        interval = _nice_grid_interval(extent)

        grid = map_item.grid()
        grid.setEnabled(True)
        grid.setCrs(grid_crs)
        style = grid_style
        grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly if style and not style['lines_visible']
                      else QgsLayoutItemMapGrid.Solid)
        grid.setIntervalX(interval)
        grid.setIntervalY(interval)

        line_symbol = grid.lineSymbol().clone()
        line_symbol.setWidth(style['width_mm'] if style else GRID_LINE_WIDTH_MM)
        color = QColor(style['color']) if style else QColor(*GRID_LINE_COLOR_RGBA)
        if style:
            color.setAlphaF(style['opacity'])
        line_symbol.setColor(color)
        line_symbol.setOpacity(1.0)
        grid.setLineSymbol(line_symbol)

        grid.setAnnotationEnabled(style['labels_visible'] if style else True)
        grid.setAnnotationPrecision(_grid_annotation_precision(interval))
        font = QFont('Arial', 6)
        font.setPointSizeF(annotation_font_size)
        grid.setAnnotationFont(font)
        grid.setAnnotationFrameDistance(1)
        return interval

    except Exception as exc:
        # Grid is a visual enhancement only.
        # Export should continue even if QGIS grid API differs by version.
        print(
            f'Warning: could not configure adaptive coordinate grid: {exc}',
            flush=True,
        )
        return None


def _configure_scale_bar(
    *,
    ctx: ExportContext,
    map_item: Any,
    extent: Any,
) -> None:
    try:
        q = ctx.qgis
        scale_bar = _get_layout_item(
            ctx.layout,
            'scale_bar',
            q.QgsLayoutItemScaleBar,
        )
        segment_km = _nice_scale_segment(extent) / 1000.0

        scale_bar.setLinkedMap(map_item)
        scale_bar.setUnits(q.QgsUnitTypes.DistanceKilometers)
        scale_bar.setUnitLabel('km')
        scale_bar.setUnitsPerSegment(segment_km)
        scale_bar.setNumberOfSegments(3)
        scale_bar.setNumberOfSegmentsLeft(0)
        scale_bar.update()
    except Exception as exc:
        # Retain the template value if a custom layout or QGIS version uses
        # a different scale-bar API.
        print(
            f'Warning: could not configure adaptive scale bar: {exc}',
            flush=True,
        )


def _utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1

    if lat >= 0:
        return 32600 + zone

    return 32700 + zone


def _nice_grid_interval(extent: Any) -> float:
    max_dim = max(extent.width(), extent.height())
    raw = max_dim / 5

    return _nice_number(raw)


def _round_up_standard_scale(
    scale: float,
    *,
    minimum: float = MIN_AUTO_MAP_SCALE,
) -> int:
    scale = float(scale)
    minimum = float(minimum)

    if not math.isfinite(scale) or scale <= 0:
        raise ValueError(f'Scale must be finite and positive: {scale!r}')
    if not math.isfinite(minimum) or minimum <= 0:
        raise ValueError(f'Minimum scale must be finite and positive: {minimum!r}')

    target = max(scale, minimum)
    exponent = 10 ** math.floor(math.log10(target))
    tolerance = max(1e-9, target * 1e-12)

    for mantissa in STANDARD_SCALE_MANTISSAS:
        candidate = mantissa * exponent
        if candidate + tolerance >= target:
            return int(round(candidate))

    return int(round(10 * exponent))


def _select_main_map_scale(
    *,
    fitted_scale: float,
    manual_scale: Any,
    auto_multiplier: float,
) -> dict[str, Any]:
    fitted_scale = float(fitted_scale)
    if not math.isfinite(fitted_scale) or fitted_scale <= 0:
        raise ValueError(
            f'Fitted scale must be finite and positive: {fitted_scale!r}'
        )

    fitted_value = round(fitted_scale, 3)

    if manual_scale is not None:
        selected_scale = float(manual_scale)
        if not math.isfinite(selected_scale) or selected_scale <= 0:
            raise ValueError(
                f'Manual scale must be finite and positive: {manual_scale!r}'
            )
        return {
            'scale_mode': 'manual',
            'fitted_scale': fitted_value,
            'auto_scale_multiplier': None,
            'selected_scale': _clean_scale_value(selected_scale),
        }

    auto_multiplier = float(auto_multiplier)
    if not math.isfinite(auto_multiplier) or auto_multiplier < 1.0:
        auto_multiplier = DEFAULT_AUTO_SCALE_MULTIPLIER

    selected_scale = _round_up_standard_scale(
        fitted_scale * auto_multiplier,
    )
    return {
        'scale_mode': 'automatic',
        'fitted_scale': fitted_value,
        'auto_scale_multiplier': auto_multiplier,
        'selected_scale': selected_scale,
    }


def _clean_scale_value(scale: float) -> int | float:
    rounded = round(float(scale), 3)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _grid_annotation_precision(interval: float) -> int:
    if interval <= 0 or interval >= 1:
        return 0

    return min(8, max(0, math.ceil(-math.log10(interval))))


def _nice_scale_segment(extent: Any) -> float:
    width = extent.width()
    raw = width / 10

    return _nice_number(raw)


def _nice_number(value: float) -> float:
    if value <= 0:
        return 100

    exponent = math.floor(math.log10(value))
    fraction = value / (10 ** exponent)

    if fraction <= 1:
        nice_fraction = 1
    elif fraction <= 2:
        nice_fraction = 2
    elif fraction <= 5:
        nice_fraction = 5
    else:
        nice_fraction = 10

    return nice_fraction * (10 ** exponent)


def _get_layout_item(layout: Any, item_id: str, item_type: Any | None = None) -> Any:
    item = layout.itemById(item_id)

    if item is None:
        raise RuntimeError(
            f"Missing layout item ID '{item_id}'. "
            "Open the QPT in QGIS Print Layout and set the correct Item ID."
        )

    if item_type is not None and not isinstance(item, item_type):
        raise RuntimeError(
            f"Layout item '{item_id}' has the wrong type."
        )

    return item


def _create_osm_basemap_layer(QgsRasterLayer: Any) -> Any | None:
    url = "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    layer = QgsRasterLayer(url, "OpenStreetMap", "wms")

    if not layer.isValid():
        return None

    return layer


def _utm_label_from_epsg(epsg: int) -> str:
    if 32601 <= epsg <= 32660:
        zone = epsg - 32600
        return f"WGS 84 / UTM Zone {zone}N"

    if 32701 <= epsg <= 32760:
        zone = epsg - 32700
        return f"WGS 84 / UTM Zone {zone}S"

    return "WGS 84 / UTM"


def _create_preferred_basemap_layer(QgsRasterLayer: Any) -> tuple[Any | None, str]:
    stadia_layer = _create_stadia_alidade_smooth_layer(QgsRasterLayer)

    if stadia_layer is not None:
        return (
            stadia_layer,
            "Basemap: © Stadia Maps, © OpenMapTiles, © OpenStreetMap contributors",
        )

    osm_layer = _create_osm_basemap_layer(QgsRasterLayer)

    if osm_layer is not None:
        return (
            osm_layer,
            "Basemap: © OpenStreetMap contributors",
        )

    return None, "Basemap unavailable"


def _create_stadia_alidade_smooth_layer(QgsRasterLayer: Any) -> Any | None:
    api_key = os.getenv("STADIA_MAPS_API_KEY", "").strip()

    if not api_key:
        return None

    url = (
        "type=xyz&url="
        "https://tiles.stadiamaps.com/tiles/alidade_smooth/"
        "{z}/{x}/{y}@2x.png"
        f"?api_key={api_key}"
    )

    layer = QgsRasterLayer(url, "Stadia Alidade Smooth", "wms")

    if not layer.isValid():
        return None

    return layer


def _render_inset_basemap_image(
    *,
    project: Any,
    basemap_layer: Any | None,
    target_crs: Any,
    extent: Any,
    output_path: Path,
    width_mm: float,
    height_mm: float,
    dpi: int = 800,
) -> None:
    from qgis.core import (  # type: ignore[reportMissingImports]
        QgsLayoutExporter,
        QgsLayoutItemMap,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsUnitTypes,
    )

    output_path.unlink(missing_ok=True)

    temp_layout = QgsPrintLayout(project)
    temp_layout.initializeDefaults()
    temp_layout.setName("temporary_inset_basemap_layout")

    page = temp_layout.pageCollection().page(0)
    page.setPageSize(
        QgsLayoutSize(
            width_mm,
            height_mm,
            QgsUnitTypes.LayoutMillimeters,
        )
    )

    map_item = QgsLayoutItemMap(temp_layout)
    map_item.attemptMove(
        QgsLayoutPoint(
            0,
            0,
            QgsUnitTypes.LayoutMillimeters,
        )
    )
    map_item.attemptResize(
        QgsLayoutSize(
            width_mm,
            height_mm,
            QgsUnitTypes.LayoutMillimeters,
        )
    )

    map_item.setCrs(target_crs)
    map_item.zoomToExtent(extent)
    map_item.setBackgroundEnabled(True)

    if basemap_layer is not None:
        map_item.setLayers([basemap_layer])
        map_item.setKeepLayerSet(True)

    temp_layout.addLayoutItem(map_item)
    map_item.refresh()

    exporter = QgsLayoutExporter(temp_layout)

    image_settings = QgsLayoutExporter.ImageExportSettings()
    image_settings.dpi = dpi

    _run_layout_export(
        export_callable=lambda p: exporter.exportToImage(p, image_settings),
        output_path=output_path,
        success_code=QgsLayoutExporter.Success,
        label=f"inset basemap image at {dpi} DPI",
        short_label="Inset basemap",
    )


def _add_inset_basemap_picture(
    *,
    layout: Any,
    inset_item: Any,
    image_path: Path,
    QgsLayoutItemPicture: Any,
) -> None:
    picture = QgsLayoutItemPicture(layout)
    picture.setId("map_inset_basemap")
    picture.setPicturePath(str(image_path))

    try:
        picture.setResizeMode(QgsLayoutItemPicture.Stretch)
    except AttributeError:
        pass

    picture.attemptMove(inset_item.positionWithUnits())
    picture.attemptResize(inset_item.sizeWithUnits())

    # Put image below the live boundary overlay.
    picture.setZValue(inset_item.zValue() - 0.1)

    layout.addLayoutItem(picture)


def _create_inset_locator_layer(boundary_layer: Any, style=None) -> Any:
    """Create fixed-print-size survey pins in the boundary's CRS, in memory."""
    from qgis.core import (
        QgsFeature,
        QgsMarkerSymbol,
        QgsMarkerSymbolLayer,
        QgsSingleSymbolRenderer,
        QgsSvgMarkerSymbolLayer,
        QgsUnitTypes,
        QgsVectorLayer,
    )

    pin_path = Path(__file__).resolve().parents[2] / 'assets' / 'map_locator_pin.svg'
    if not pin_path.is_file():
        raise FileNotFoundError(f'Missing inset locator pin: {pin_path}')

    layer = QgsVectorLayer('Point', 'Survey Location Pin', 'memory')
    if not layer.isValid():
        raise RuntimeError('Failed to create inset locator layer')
    layer.setCrs(boundary_layer.crs())

    points = []
    for source_feature in boundary_layer.getFeatures():
        if not source_feature.hasGeometry():
            continue
        geometry = source_feature.geometry()
        if geometry.isEmpty():
            continue
        # Unlike a centroid, this stays inside concave/multipart boundaries.
        location = geometry.pointOnSurface()
        if location.isNull() or location.isEmpty():
            raise RuntimeError('Unable to determine survey location for inset pin')
        feature = QgsFeature()
        feature.setGeometry(location)
        points.append(feature)

    if not points:
        raise RuntimeError('No valid survey boundary geometry for inset pin')
    added, _features = layer.dataProvider().addFeatures(points)
    if not added:
        raise RuntimeError('Failed to add survey location to inset locator layer')
    layer.updateExtents()

    pin = QgsSvgMarkerSymbolLayer(str(pin_path))
    pin.setSize(4.0)
    if style is not None:
        from qgis.PyQt.QtGui import QColor
        pin.setSize(style['pin_size_mm'])
        pin.setFillColor(QColor(style['pin_color']))
    pin.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    pin.setHorizontalAnchorPoint(QgsMarkerSymbolLayer.HCenter)
    pin.setVerticalAnchorPoint(QgsMarkerSymbolLayer.Bottom)
    symbol = QgsMarkerSymbol.createSimple({'name': 'circle'})
    symbol.changeSymbolLayer(0, pin)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    return layer


def _configure_inset_boundary_overlay(
    *,
    project: Any,
    inset_item: Any,
    boundary_layer: Any,
    target_crs: Any,
    extent: Any,
    overlay_mode: str = 'boundary',
    style: dict[str, Any] | None = None,
) -> Any:
    if overlay_mode == 'pin':
        inset_boundary_layer = (_create_inset_locator_layer(boundary_layer, style=style) if style is not None
                                else _create_inset_locator_layer(boundary_layer))
    else:
        inset_boundary_layer = boundary_layer.clone()
        inset_boundary_layer.setName("Inset Boundary Overlay")
        source_renderer = boundary_layer.renderer()
        if source_renderer is not None:
            inset_boundary_layer.setRenderer(source_renderer.clone())
        if style is not None:
            _apply_boundary_style(inset_boundary_layer, style)

    # Add silently to project so QGIS can render it, but do not show in layer tree.
    project.addMapLayer(inset_boundary_layer, False)

    inset_item.setCrs(target_crs)
    inset_item.zoomToExtent(extent)

    # Only the boundary or pin here. The basemap is the picture underneath.
    inset_item.setLayers([inset_boundary_layer])
    inset_item.setKeepLayerSet(True)

    # Important: transparent map item so the basemap picture below is visible.
    inset_item.setBackgroundEnabled(False)

    inset_item.refresh()
    return inset_boundary_layer


def _frame_style(ctx, key):
    styles = getattr(ctx, 'map_styles', None)
    return styles[key] if styles is not None else None


def _frame_basemap(ctx, key):
    layers = getattr(ctx, 'frame_basemaps', None)
    if layers is not None and key in layers:
        return layers[key]
    return ctx.basemap_layer


def _inset_basemap_with_native_dpi(ctx, key):
    """Normalize private XYZ inset layers without changing geography/output DPI.

    QGIS 3.34 scales tiled requests by output DPI only when tilePixelRatio is
    known. An undefined ratio selects finer zooms at print DPI and shrinks text.
    """
    layer = _frame_basemap(ctx, key)
    if layer is None or layer.providerType() != 'wms':
        return layer

    from qgis.core import QgsDataSourceUri

    uri = QgsDataSourceUri()
    uri.setEncodedUri(layer.source())
    if uri.param('type').lower() != 'xyz':
        return layer
    ratio = uri.param('tilePixelRatio')
    if ratio not in ('1', '2'):
        ratio = '2' if '@2x' in urlsplit(uri.param('url')).path else '1'

    if uri.params('tilePixelRatio') != [ratio]:
        uri.removeParam('tilePixelRatio')  # setParam appends; never leave duplicates.
        uri.setParam('tilePixelRatio', ratio)
        candidate = layer.clone()
        candidate.setDataSource(bytes(uri.encodedUri()).decode('utf-8'), layer.name(), 'wms')
        if not candidate.isValid():
            raise RuntimeError(f'Failed to normalize inset basemap: {key}')
        candidate.setOpacity(layer.opacity())
        ctx.project.addMapLayer(candidate, False)
        ctx.frame_basemaps = dict(getattr(ctx, 'frame_basemaps', None) or {})
        ctx.frame_basemaps[key] = candidate
        layer = candidate

    settings = {'mode': 'native_dpi', 'tile_pixel_ratio': int(ratio), 'source_dpi': 96 * int(ratio)}
    ctx.inset_tile_scaling = dict(getattr(ctx, 'inset_tile_scaling', None) or {})
    ctx.inset_tile_scaling[key] = settings
    print(f'Inset {key}: native XYZ tile scaling ({settings["source_dpi"]} source DPI)', flush=True)
    return layer


def _apply_boundary_style(layer, style):
    _style_polygon_layer(layer, stroke=style['boundary_stroke'], fill=style['boundary_fill'],
                         opacity=style['boundary_opacity'], width=style['boundary_width_mm'])


def apply_map_style(ctx, key, style):
    """Restyle private frame layers without loading TIFFs or changing framing."""
    layer = _frame_basemap(ctx, key)
    if layer is not None:
        layer.setOpacity(style['basemap_opacity'])
        layer.triggerRepaint()
    overlay = ctx.frame_overlays[key]
    prefix = 'boundary' if key == 'map_inset' else 'overview'
    if key != 'map_frame' and ctx.render_options[prefix + '_overlay'] == 'pin':
        symbol = overlay.renderer().symbol().clone()
        marker = symbol.symbolLayer(0)
        marker.setSize(style['pin_size_mm'])
        marker.setFillColor(ctx.qgis.QColor(style['pin_color']))
        overlay.renderer().setSymbol(symbol)
    else:
        _apply_boundary_style(overlay, style)
    overlay.triggerRepaint()
    ctx.map_styles[key] = dict(style)
    ctx.layout.itemById(key).refresh()


def _read_int_env(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()

    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        print(
            f"Invalid {name} value: {value!r}. Using default: {default}",
            flush=True,
        )
        return default


def _read_nonnegative_float_env(name: str, default: float) -> float:
    value = os.getenv(name, '').strip()

    if not value:
        return default

    try:
        parsed = float(value)
    except ValueError:
        parsed = -1.0

    if not math.isfinite(parsed) or parsed < 0:
        print(
            f'Invalid {name} value: {value!r}. Using default: {default}',
            flush=True,
        )
        return default

    return parsed


def _read_float_env_at_least(
    name: str,
    default: float,
    *,
    minimum: float,
) -> float:
    value = os.getenv(name, '').strip()

    if not value:
        return default

    try:
        parsed = float(value)
    except ValueError:
        parsed = float('-inf')

    if not math.isfinite(parsed) or parsed < minimum:
        print(
            f'Invalid {name} value: {value!r}. Using default: {default}',
            flush=True,
        )
        return default

    return parsed


def _read_overview_crs() -> str:
    value = os.getenv('MAP_EXPORT_OVERVIEW_CRS', 'project').strip()
    if value.lower() == 'project' or not value:
        return 'project'
    if value.upper() == 'EPSG:4326':
        return 'EPSG:4326'
    print(f'Invalid MAP_EXPORT_OVERVIEW_CRS: {value!r}. Using project CRS.', flush=True)
    return 'project'


def _read_overview_overlay_mode() -> str:
    return _read_inset_overlay_mode('MAP_EXPORT_OVERVIEW_OVERLAY')


def _read_inset_overlay_mode(env_name: str) -> str:
    value = os.getenv(env_name, 'boundary').strip().lower()
    if value in ('boundary', 'pin'):
        return value
    if value:
        print(
            f'Invalid {env_name}: {value!r}. '
            'Expected boundary or pin. Using boundary.',
            flush=True,
        )
    return 'boundary'


def _read_overview_inset_config() -> tuple[str, dict[str, float]]:
    """Read an atomic overview label/bounds pair, falling back safely."""
    label, bounds = _read_inset_config(
        'MAP_EXPORT_OVERVIEW', DEFAULT_OVERVIEW_LABEL, DAVAO_CITY_BOUNDS_WGS84,
    )
    assert bounds is not None
    return label, bounds


def _read_inset_config(
    prefix: str,
    default_label: str,
    default_bounds: dict[str, float] | None,
) -> tuple[str, dict[str, float] | None]:
    """Read a paired label/extent; reject partial or invalid overrides together."""
    label = os.getenv(f'{prefix}_LABEL', '').strip()
    bounds_value = os.getenv(f'{prefix}_BOUNDS', '').strip()
    fallback_bounds = dict(default_bounds) if default_bounds is not None else None

    if not label and not bounds_value:
        return default_label, fallback_bounds

    if not label or not bounds_value:
        print(
            f'{prefix}_LABEL and {prefix}_BOUNDS must '
            'be set together. Using the default inset label and extent.',
            flush=True,
        )
        return default_label, fallback_bounds

    parts = [part.strip() for part in bounds_value.split(',')]
    try:
        values = [float(part) for part in parts]
    except ValueError:
        values = []

    if len(values) == 4:
        min_lon, min_lat, max_lon, max_lat = values
        is_valid = (
            all(math.isfinite(value) for value in values)
            and -180.0 <= min_lon < max_lon <= 180.0
            and -90.0 <= min_lat < max_lat <= 90.0
        )
    else:
        is_valid = False

    if not is_valid:
        print(
            f'Invalid {prefix}_BOUNDS value: '
            f'{bounds_value!r}. Expected '
            'min_lon,min_lat,max_lon,max_lat. Using the default inset '
            'label and extent.',
            flush=True,
        )
        return default_label, fallback_bounds

    return label, {
        'min_lon': min_lon,
        'min_lat': min_lat,
        'max_lon': max_lon,
        'max_lat': max_lat,
    }


def _read_paper_size_env() -> str:
    value = os.getenv('MAP_EXPORT_PAPER_SIZE', DEFAULT_PAPER_SIZE).strip().upper()
    if value in PAPER_SIZES_MM:
        return value

    print(
        f'Invalid MAP_EXPORT_PAPER_SIZE value: {value!r}. '
        f'Expected one of: {", ".join(PAPER_SIZES_MM)}. '
        f'Using default: {DEFAULT_PAPER_SIZE}',
        flush=True,
    )
    return DEFAULT_PAPER_SIZE


def _configure_inset_map(
    *,
    layout: Any,
    project: Any,
    package_dir: Path,
    basemap_layer: Any,
    boundary_layer: Any,
    target_crs: Any,
    QgsLayoutItemPicture: Any,
    map_item_id: str,
    QgsCoordinateReferenceSystem: Any | None = None,
    fixed_extent_wgs84: dict[str, float] | None = None,
    zoom_out_factor: float = 1.0,
    overlay_mode: str = 'boundary',
    live: bool = False,
    extent_override: Any = None,
    buffer_factor: float | None = None,
    inset_dpi: int | None = None,
    overlay_style: dict[str, Any] | None = None,
) -> Any | None:
    """
    Configure one inset map.

    Creates:
        basemap picture
        live boundary or fixed-size location pin overlay
        optional label

    By default the extent is fitted to `boundary_layer` (the current survey).
    Pass `fixed_extent_wgs84` (with `QgsCoordinateReferenceSystem`) to instead
    use a static WGS84 bounding box — e.g. a fixed city-wide overview inset
    that doesn't move based on the current survey's location. The boundary
    overlay is still drawn on top either way.
    A zoom-out factor greater than 1 expands that extent around its center,
    after automatic padding and frame-aspect adjustment.
    """

    inset_item = layout.itemById(map_item_id)

    if inset_item is None:
        return

    from qgis.core import QgsLayoutItemMap

    if not isinstance(inset_item, QgsLayoutItemMap):
        return

    frame_rect = inset_item.rect()

    if fixed_extent_wgs84 is not None:
        inset_extent = _prepare_fixed_extent(
            project=project,
            wgs84_bounds=fixed_extent_wgs84,
            target_crs=target_crs,
            QgsCoordinateReferenceSystem=QgsCoordinateReferenceSystem,
            frame_rect=frame_rect,
        )
    else:
        inset_extent = _prepare_fitted_extent(
            project=project,
            layers=[boundary_layer],
            target_crs=target_crs,
            frame_rect=frame_rect,
            buffer_factor=buffer_factor if buffer_factor is not None else _read_nonnegative_float_env(
                'MAP_EXPORT_BOUNDARY_INSET_BUFFER',
                4.0,
            ),
        )

    if extent_override is not None:
        inset_extent = extent_override
    elif zoom_out_factor > 1.0:
        inset_extent.scale(zoom_out_factor)
        print(
            f'Inset {map_item_id}: zoom-out factor={zoom_out_factor:g}',
            flush=True,
        )

    if live:
        overlay = _configure_inset_boundary_overlay(
            project=project, inset_item=inset_item, boundary_layer=boundary_layer,
            target_crs=target_crs, extent=inset_extent, overlay_mode=overlay_mode,
            style=overlay_style,
        )
        inset_item.setLayers([overlay] + ([basemap_layer] if basemap_layer is not None else []))
        inset_item.setBackgroundEnabled(True)
        inset_item.refresh()
        return overlay

    image_path = package_dir / f"{map_item_id}.png"

    inset_dpi = inset_dpi if inset_dpi is not None else _read_int_env(
        "MAP_EXPORT_INSET_DPI",
        800,
    )

    _render_inset_basemap_image(
        project=project,
        basemap_layer=basemap_layer,
        target_crs=target_crs,
        extent=inset_extent,
        output_path=image_path,
        width_mm=frame_rect.width(),
        height_mm=frame_rect.height(),
        dpi=inset_dpi,
    )

    _add_inset_basemap_picture(
        layout=layout,
        inset_item=inset_item,
        image_path=image_path,
        QgsLayoutItemPicture=QgsLayoutItemPicture,
    )

    overlay = _configure_inset_boundary_overlay(
        project=project,
        inset_item=inset_item,
        boundary_layer=boundary_layer,
        target_crs=target_crs,
        extent=inset_extent,
        overlay_mode=overlay_mode,
        style=overlay_style,
    )

    layout.refresh()
    return overlay


def _apply_layout_variables(ctx: ExportContext) -> None:
    """
    Populate layout labels using their Layout Item IDs.

    Example:

        {
            "map_title": "...",
            "map_location": "...",
            "map_author": "...",
        }

    """

    from qgis.core import QgsLayoutItemLabel

    if not ctx.layout_variables:
        return

    print("\n===== APPLYING LAYOUT VARIABLES =====")

    for item_id, value in ctx.layout_variables.items():

        item = ctx.layout.itemById(item_id)

        if item is None:
            print(f"[WARN] Layout item '{item_id}' not found.")
            continue

        if not isinstance(item, QgsLayoutItemLabel):
            print(f"[WARN] '{item_id}' is not a label.")
            continue

        old = item.text()

        item.setText(str(value))
        item.refresh()

        print(f"{item_id}")
        print(f"  OLD : {old}")
        print(f"  NEW : {value}")

    print("====================================\n")
