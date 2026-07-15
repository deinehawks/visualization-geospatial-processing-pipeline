# pyright: reportMissingImports=false

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
import json, math, os, time
from osgeo import gdal


def _quiet_gdal_error_handler(err_class, err_num, err_msg):
    if "PNG driver does not support update access" in err_msg:
        return
    gdal.CPLDefaultErrorHandler(err_class, err_num, err_msg)


gdal.PushErrorHandler(_quiet_gdal_error_handler)


# Fixed reference extent for the "Davao City" overview inset (map_inset_2).
# This is intentionally static — it always shows the same city-wide context,
# regardless of where the current survey sits within it. Adjust if the
# framing needs to be tighter/looser.
DAVAO_CITY_BOUNDS_WGS84 = {
    "min_lon": 125.3063,
    "min_lat": 6.8710,
    "max_lon": 125.9308,
    "max_lat": 7.3282,
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
    layout_template_path: Path | None
    map_disclaimer: str
    grid_label: str

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
    ) -> None:
        self.package_dir = package_dir
        self.title = title
        self.logo_path = logo_path
        self.dpi = dpi
        self.qgis = qgis
        self.layout_variables = layout_variables or {}


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
        "label_inset": ctx.map_location,
        "label_inset_2": "Davao City",
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

    _apply_layout_variables(ctx)


def _configure_main_map(ctx: ExportContext) -> Any:
    """Fit the main map frame's extent to the boundary and assign its layers."""
    q = ctx.qgis

    map_item = _get_layout_item(ctx.layout, "map_frame", q.QgsLayoutItemMap)

    map_extent = _prepare_fitted_extent(
        project=ctx.project,
        layers=[ctx.merged_layer],
        target_crs=ctx.target_crs,
        frame_rect=map_item.rect(),
        buffer_factor=0.12,
    )

    map_item.setExtent(map_extent)
    map_item.setLayers(ctx.main_map_layers)
    map_item.setKeepLayerSet(True)

    if ctx.map_scale:
        map_item.setScale(float(ctx.map_scale))

    map_item.refresh()

    return map_item


def _configure_insets(ctx: ExportContext) -> None:
    """Configure the location and extent inset maps on the layout."""
    q = ctx.qgis

    # Inset 1: zoomed to the survey boundary itself. Label reflects the
    # barangay/district passed in via --location.
    _configure_inset_map(
        layout=ctx.layout,
        project=ctx.project,
        package_dir=ctx.package_dir,
        basemap_layer=ctx.basemap_layer,
        boundary_layer=ctx.merged_layer,
        target_crs=ctx.target_crs,
        QgsLayoutItemPicture=q.QgsLayoutItemPicture,
        QgsCoordinateReferenceSystem=q.QgsCoordinateReferenceSystem,
        map_item_id="map_inset",
    )

    # Inset 2: always a fixed Davao City overview, regardless of survey
    # location. The survey boundary is still overlaid so users can see
    # where within the city it sits.
    _configure_inset_map(
        layout=ctx.layout,
        project=ctx.project,
        package_dir=ctx.package_dir,
        basemap_layer=ctx.basemap_layer,
        boundary_layer=ctx.merged_layer,
        target_crs=ctx.target_crs,
        QgsLayoutItemPicture=q.QgsLayoutItemPicture,
        QgsCoordinateReferenceSystem=q.QgsCoordinateReferenceSystem,
        map_item_id="map_inset_2",
        fixed_extent_wgs84=DAVAO_CITY_BOUNDS_WGS84,
    )


# Pipeline stage 4: export

def _export_print_outputs(ctx: ExportContext) -> dict[str, str]:
    """Export the PNG preview and PDF, then write the updated package metadata."""
    q = ctx.qgis

    pdf_path = ctx.package_dir / "print_map.pdf"
    png_path = ctx.package_dir / "preview.png"
    pdf_temp_path = ctx.package_dir / "print_map_tmp.pdf"
    png_temp_path = ctx.package_dir / "preview_tmp.png"

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

    preview_dpi = _read_int_env("MAP_EXPORT_PREVIEW_DPI", 120)
    pdf_dpi = _read_int_env("MAP_EXPORT_PDF_DPI", 300)

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
    ctx.metadata["print_layout"] = {
        "exported_at": created_at,
        "crs": f"EPSG:{ctx.target_epsg}",
        "paper_size": "A4",
        "orientation": "landscape",
        "dpi": ctx.dpi,
        "logo": str(ctx.logo_path) if ctx.logo_path else None,
    }

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
    """
    Export a print-ready PDF and PNG preview from a Phase 1 map package.

    Requires QGIS Python environment.
    Input package must contain:
    - boundaries.geojson
    - merged_boundary.geojson
    - map_metadata.json
    """
    qgis_classes = _load_qgis_classes()
    _init_qgis_application(qgis_classes.QgsApplication)

    ctx = ExportContext(
        package_dir=Path(package_dir),
        title=title,
        logo_path=logo_path,
        dpi=dpi,
        qgis=qgis_classes,
        layout_variables=layout_variables,
    )

    _validate_package_paths(ctx)
    _load_metadata_config(ctx)
    _compute_target_crs(ctx)
    _prepare_project(ctx)
    _load_boundary_layers(ctx)
    _load_orthomosaic_layers(ctx)
    _assemble_map_layers(ctx)
    _build_print_layout(ctx)
    _configure_main_map(ctx)
    _configure_insets(ctx)

    return _export_print_outputs(ctx)


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
    QgsLayoutItemMapGrid: Any,
    QColor: Any,
    QFont: Any,
) -> None:
    try:
        interval = _nice_grid_interval(extent)

        grid = map_item.grid()
        grid.setEnabled(True)
        grid.setStyle(QgsLayoutItemMapGrid.Solid)
        grid.setIntervalX(interval)
        grid.setIntervalY(interval)
        grid.setGridLineColor(QColor(120, 120, 120, 90))
        grid.setGridLineWidth(0.15)
        grid.setAnnotationEnabled(True)
        grid.setAnnotationPrecision(0)
        grid.setAnnotationFont(QFont("Arial", 6))
        grid.setAnnotationFrameDistance(1)

    except Exception:
        # Grid is a visual enhancement only.
        # Export should continue even if QGIS grid API differs by version.
        return


def _utm_epsg_from_lonlat(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1

    if lat >= 0:
        return 32600 + zone

    return 32700 + zone


def _nice_grid_interval(extent: Any) -> float:
    max_dim = max(extent.width(), extent.height())
    raw = max_dim / 5

    return _nice_number(raw)


def _nice_scale_segment(extent: Any) -> float:
    width = extent.width()
    raw = width / 5

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
    map_item.setExtent(extent)
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


def _configure_inset_boundary_overlay(
    *,
    project: Any,
    inset_item: Any,
    boundary_layer: Any,
    target_crs: Any,
    extent: Any,
) -> None:
    from qgis.core import (  # type: ignore[reportMissingImports]
        QgsFillSymbol,
        QgsSingleSymbolRenderer,
    )

    inset_boundary_layer = boundary_layer.clone()
    inset_boundary_layer.setName("Inset Boundary Overlay")

    source_renderer = boundary_layer.renderer()

    if source_renderer is not None:
        inset_boundary_layer.setRenderer(source_renderer.clone())

    # Add silently to project so QGIS can render it, but do not show in layer tree.
    project.addMapLayer(inset_boundary_layer, False)

    inset_item.setCrs(target_crs)
    inset_item.setExtent(extent)

    # Only boundary here. Basemap is handled by the picture underneath.
    inset_item.setLayers([inset_boundary_layer])
    inset_item.setKeepLayerSet(True)

    # Important: transparent map item so the basemap picture below is visible.
    inset_item.setBackgroundEnabled(False)

    inset_item.refresh()


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
) -> None:
    """
    Configure one inset map.

    Creates:
        basemap picture
        live boundary overlay
        optional label

    By default the extent is fitted to `boundary_layer` (the current survey).
    Pass `fixed_extent_wgs84` (with `QgsCoordinateReferenceSystem`) to instead
    use a static WGS84 bounding box — e.g. a fixed city-wide overview inset
    that doesn't move based on the current survey's location. The boundary
    overlay is still drawn on top either way.
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
            buffer_factor=4.0,
        )

    image_path = package_dir / f"{map_item_id}.png"

    inset_dpi = _read_int_env(
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

    _configure_inset_boundary_overlay(
        project=project,
        inset_item=inset_item,
        boundary_layer=boundary_layer,
        target_crs=target_crs,
        extent=inset_extent,
    )

    layout.refresh()


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