# pyright: reportMissingImports=false

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json
import math
import os


def export_qgis_print_layout(
    *,
    package_dir: Path,
    title: str | None = None,
    logo_path: Path | None = None,
    dpi: int = 200,
) -> dict[str, str]:
    """
    Export a print-ready PDF and PNG preview from a Phase 1 map package.

    Requires QGIS Python environment.
    Input package must contain:
    - boundaries.geojson
    - merged_boundary.geojson
    - map_metadata.json
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

    _init_qgis_application(QgsApplication)

    package_dir = Path(package_dir)

    boundaries_path = package_dir / "boundaries.geojson"
    merged_path = package_dir / "merged_boundary.geojson"
    metadata_path = package_dir / "map_metadata.json"

    if not boundaries_path.exists():
        raise FileNotFoundError(f"Missing boundaries GeoJSON: {boundaries_path}")

    if not merged_path.exists():
        raise FileNotFoundError(f"Missing merged boundary GeoJSON: {merged_path}")

    if not metadata_path.exists():
        raise FileNotFoundError(f"Missing map metadata JSON: {metadata_path}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    layout_cfg = metadata.get("layout") or {}

    map_title = (
        title
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

    osm_attribution = (
        layout_cfg.get("basemap_attribution")
        or "Basemap: © OpenStreetMap contributors"
    )

    map_disclaimer = (
        layout_cfg.get("disclaimer")
        or "Disclaimer: This map is intended for visualization and reference purposes only. Not for legal boundary determination or survey-grade use."
    )

    grid_label = (
        layout_cfg.get("grid_label")
        or "WGS 84 Geographic Coordinates (EPSG:4326)"
    )

    bounds = metadata.get("bounds") or {}
    center = metadata.get("center") or {}

    if not bounds:
        raise RuntimeError("Missing bounds in map_metadata.json. Cannot calculate map CRS.")

    center_lon = float(center.get("lon") or ((bounds["min_lon"] + bounds["max_lon"]) / 2))
    center_lat = float(center.get("lat") or ((bounds["min_lat"] + bounds["max_lat"]) / 2))

    target_epsg = _utm_epsg_from_lonlat(center_lon, center_lat)
    target_crs = QgsCoordinateReferenceSystem(f"EPSG:{target_epsg}")

    project = QgsProject.instance()
    project.clear()
    project.setCrs(target_crs)

    boundaries_layer = QgsVectorLayer(str(boundaries_path), "Survey Boundaries", "ogr")
    if not boundaries_layer.isValid():
        raise RuntimeError(f"Failed to load layer: {boundaries_path}")

    source_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    boundaries_layer.setCrs(source_crs)

    # Create a true dissolved boundary for print output.
    # This removes internal lines between adjacent/overlapping survey boundaries.
    merged_layer = _create_dissolved_boundary_layer(
        source_layer=boundaries_layer,
        layer_name="Overall Boundary",
        project=project,
        target_crs=target_crs,
        dissolve_tolerance_m=3.0,
    )

    _style_polygon_layer(
        merged_layer,
        stroke="#991b1b",
        fill="#fca5a5",
        opacity=0.32,
        width=0.55,
    )

    osm_layer = _create_osm_basemap_layer(QgsRasterLayer)

    if osm_layer is not None:
        project.addMapLayer(osm_layer)

    project.addMapLayer(merged_layer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("print_map_layout")

    if not layout_template_path or not layout_template_path.exists():
        raise RuntimeError(
            "Missing QGIS layout template. Expected: "
            f"{layout_template_path or 'assets/qgis_layouts/client_boundary_map.qpt'}"
        )

    doc = QDomDocument()
    template_text = layout_template_path.read_text(encoding="utf-8")

    result = doc.setContent(template_text)

    if isinstance(result, tuple):
        ok = result[0]
        error_message = result[1] if len(result) > 1 else ""
    else:
        ok = bool(result)
        error_message = ""

    if not ok:
        raise RuntimeError(
            f"Failed to load QPT template: {layout_template_path}. {error_message}"
        )

    layout.loadFromTemplate(doc, QgsReadWriteContext())

    map_item = _get_layout_item(layout, "map_frame", QgsLayoutItemMap)

    map_extent = _combined_projected_extent(
        project=project,
        layers=[merged_layer],
        target_crs=target_crs,
    )

    frame_rect = map_item.rect()

    map_extent = _fit_extent_to_frame(
        map_extent,
        frame_width=frame_rect.width(),
        frame_height=frame_rect.height(),
    )

    map_extent = _buffer_extent(map_extent, factor=0.12)

    map_item.setExtent(map_extent)

    if map_scale:
        map_item.setScale(float(map_scale))

    map_item.refresh()

    _set_label_text(layout, "map_title", map_title, QgsLayoutItemLabel)
    _set_label_text(layout, "map_location", map_location, QgsLayoutItemLabel)

    scale_value = int(round(float(map_scale or map_item.scale())))

    projection_label = _utm_label_from_epsg(target_epsg)
    
    map_info = (
        f"Scale 1:{scale_value:,}\n"
        f"Projection: {projection_label} (EPSG:{target_epsg})\n"
        f"Grid: {grid_label}"
    )

    _set_label_text(layout, "map_info", map_info, QgsLayoutItemLabel)
    _set_label_text(layout, "osm_attribution", osm_attribution, QgsLayoutItemLabel)
    _set_label_text(layout, "map_disclaimer", map_disclaimer, QgsLayoutItemLabel)

    scale_bar = layout.itemById("scale_bar")
    if scale_bar:
        scale_bar.setLinkedMap(map_item)

    logo_path = Path(logo_path) if logo_path else None
    logo_item = layout.itemById("map_logo")

    if logo_item and logo_path and logo_path.exists():
        logo_item.setPicturePath(str(logo_path))

    pdf_path = package_dir / "print_map.pdf"
    png_path = package_dir / "preview.png"

    pdf_path.unlink(missing_ok=True)
    png_path.unlink(missing_ok=True)

    exporter = QgsLayoutExporter(layout)

    pdf_settings = QgsLayoutExporter.PdfExportSettings()
    pdf_result = exporter.exportToPdf(str(pdf_path), pdf_settings)

    if pdf_result != QgsLayoutExporter.Success:
        raise RuntimeError(f"Failed to export PDF: {pdf_path}")

    image_settings = QgsLayoutExporter.ImageExportSettings()
    image_settings.dpi = dpi

    image_result = exporter.exportToImage(str(png_path), image_settings)

    if image_result != QgsLayoutExporter.Success:
        raise RuntimeError(f"Failed to export PNG preview: {png_path}")
    
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    metadata.setdefault("outputs", {})
    metadata["outputs"]["print_map_pdf"] = "print_map.pdf"
    metadata["outputs"]["preview_png"] = "preview.png"
    metadata["print_layout"] = {
        "exported_at": created_at,
        "crs": f"EPSG:{target_epsg}",
        "paper_size": "A4",
        "orientation": "landscape",
        "dpi": dpi,
        "logo": str(logo_path) if logo_path else None,
    }

    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "pdf": str(pdf_path),
        "preview": str(png_path),
        "metadata": str(metadata_path),
        "crs": f"EPSG:{target_epsg}",
    }

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


def _set_label_text(
    layout: Any,
    item_id: str,
    text: str,
    QgsLayoutItemLabel: Any,
    *,
    adjust_to_text: bool = False,
) -> None:
    item = _get_layout_item(layout, item_id, QgsLayoutItemLabel)
    item.setText(text)

    if adjust_to_text:
        item.adjustSizeToText()

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