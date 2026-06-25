from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import shutil

from .boundary_finder import collect_boundary_files
from .kml_to_geojson import extract_kml_from_kmz, parse_kml_to_features


@dataclass(frozen=True)
class MapPackageResult:
    map_name: str
    output_dir: Path
    boundaries_geojson: Path
    merged_boundary_geojson: Path
    metadata_json: Path
    style_json: Path


def export_map_package(
    *,
    survey_names: list[str],
    source_root: Path,
    output_root: Path,
    map_name: str,
    title: str | None = None,
    location: str | None = None,
    map_scale: int | None = None,
    layout_template: Path | None = None,
    disclaimer: str | None = None,
) -> MapPackageResult:
    map_slug = _slugify(map_name)
    output_dir = output_root / map_slug

    source_boundary_dir = output_dir / "source_boundaries"
    extracted_kml_dir = output_dir / "extracted_kml"

    output_dir.mkdir(parents=True, exist_ok=True)
    source_boundary_dir.mkdir(parents=True, exist_ok=True)
    extracted_kml_dir.mkdir(parents=True, exist_ok=True)

    boundary_files = collect_boundary_files(
        source_root=source_root,
        survey_names=survey_names,
    )

    all_features: list[dict[str, Any]] = []
    boundary_records: list[dict[str, Any]] = []

    for item in boundary_files:
        copied_boundary = source_boundary_dir / (
            f"{_slugify(item.survey_name)}{item.boundary_path.suffix.lower()}"
        )

        shutil.copy2(item.boundary_path, copied_boundary)

        if item.boundary_path.suffix.lower() == ".kmz":
            kml_path = extract_kml_from_kmz(
                kmz_path=item.boundary_path,
                output_dir=extracted_kml_dir,
                survey_name=item.survey_name,
            )
        else:
            kml_path = extracted_kml_dir / f"{_slugify(item.survey_name)}.kml"
            shutil.copy2(item.boundary_path, kml_path)

        features = parse_kml_to_features(
            kml_path=kml_path,
            survey_name=item.survey_name,
            source_boundary=item.boundary_path,
        )

        all_features.extend(features)

        boundary_records.append(
            {
                "survey_name": item.survey_name,
                "survey_dir": str(item.survey_dir),
                "source_boundary": str(item.boundary_path),
                "copied_boundary": str(copied_boundary),
                "extracted_kml": str(kml_path),
                "polygon_count": len(features),
            }
        )

    boundaries_collection = {
        "type": "FeatureCollection",
        "name": f"{map_slug}_boundaries",
        "features": all_features,
    }

    merged_feature, merge_method = _build_merged_boundary_feature(
        features=all_features,
        map_name=map_slug,
        survey_names=survey_names,
    )

    merged_collection = {
        "type": "FeatureCollection",
        "name": f"{map_slug}_merged_boundary",
        "features": [merged_feature],
    }

    bounds = _compute_feature_collection_bounds(boundaries_collection)

    boundaries_geojson = output_dir / "boundaries.geojson"
    merged_boundary_geojson = output_dir / "merged_boundary.geojson"
    metadata_json = output_dir / "map_metadata.json"
    style_json = output_dir / "style.json"

    _write_json(boundaries_geojson, boundaries_collection)
    _write_json(merged_boundary_geojson, merged_collection)

    default_disclaimer = (
        "Disclaimer: This map is intended for visualization and reference purposes only. "
        "Not for legal boundary determination or survey-grade use."
    )

    metadata = {
        "map_name": map_slug,
        "title": title or map_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "output_dir": str(output_dir),
        "survey_count": len(survey_names),
        "polygon_count": len(all_features),
        "surveys": survey_names,
        "boundaries": boundary_records,
        "bounds": bounds,
        "center": _bounds_center(bounds),
        "merge_method": merge_method,
        "outputs": {
            "boundaries_geojson": "boundaries.geojson",
            "merged_boundary_geojson": "merged_boundary.geojson",
            "metadata_json": "map_metadata.json",
            "style_json": "style.json",
        },
        "location": location,
        "layout": {
            "title": title or map_name,
            "location": location or "",
            "map_scale": map_scale,
            "layout_template": str(layout_template) if layout_template else None,
            "basemap_attribution": "Basemap: © OpenStreetMap contributors",
            "disclaimer": disclaimer or default_disclaimer,
            "projection_label": "WGS 84 / UTM Zone 51N",
            "grid_label": "WGS 84 Geographic Coordinates (EPSG:4326)",
        },
    }

    style = {
        "version": 1,
        "crs": "EPSG:4326",
        "layers": {
            "boundaries": {
                "label": "Survey boundaries",
                "stroke": "#2563eb",
                "stroke_width": 2,
                "fill": "#3b82f6",
                "fill_opacity": 0.15,
            },
            "merged_boundary": {
                "label": "Overall boundary",
                "stroke": "#dc2626",
                "stroke_width": 3,
                "fill": "#ef4444",
                "fill_opacity": 0.08,
            },
        },
    }

    _write_json(metadata_json, metadata)
    _write_json(style_json, style)

    return MapPackageResult(
        map_name=map_slug,
        output_dir=output_dir,
        boundaries_geojson=boundaries_geojson,
        merged_boundary_geojson=merged_boundary_geojson,
        metadata_json=metadata_json,
        style_json=style_json,
    )


def _build_merged_boundary_feature(
    *,
    features: list[dict[str, Any]],
    map_name: str,
    survey_names: list[str],
) -> tuple[dict[str, Any], str]:
    try:
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union

        geometries = [
            shape(feature["geometry"])
            for feature in features
            if feature.get("geometry")
        ]

        merged = unary_union(geometries)

        return (
            {
                "type": "Feature",
                "properties": {
                    "map_name": map_name,
                    "survey_count": len(survey_names),
                    "surveys": survey_names,
                    "merge_method": "shapely_unary_union",
                },
                "geometry": mapping(merged),
            },
            "shapely_unary_union",
        )

    except Exception:
        multipolygon_coordinates: list[Any] = []

        for feature in features:
            geometry = feature.get("geometry") or {}
            geometry_type = geometry.get("type")

            if geometry_type == "Polygon":
                multipolygon_coordinates.append(geometry.get("coordinates"))

            elif geometry_type == "MultiPolygon":
                multipolygon_coordinates.extend(
                    geometry.get("coordinates") or []
                )

        return (
            {
                "type": "Feature",
                "properties": {
                    "map_name": map_name,
                    "survey_count": len(survey_names),
                    "surveys": survey_names,
                    "merge_method": "multipolygon_collect_no_dissolve",
                    "note": (
                        "Shapely was not available, so polygons were combined "
                        "as a MultiPolygon without geometric dissolve."
                    ),
                },
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": multipolygon_coordinates,
                },
            },
            "multipolygon_collect_no_dissolve",
        )


def _compute_feature_collection_bounds(
    collection: dict[str, Any],
) -> dict[str, float] | None:
    positions: list[list[float]] = []

    for feature in collection.get("features", []):
        geometry = feature.get("geometry") or {}
        positions.extend(_iter_positions(geometry))

    if not positions:
        return None

    lons = [pos[0] for pos in positions]
    lats = [pos[1] for pos in positions]

    return {
        "min_lon": min(lons),
        "min_lat": min(lats),
        "max_lon": max(lons),
        "max_lat": max(lats),
    }


def _bounds_center(
    bounds: dict[str, float] | None,
) -> dict[str, float] | None:
    if not bounds:
        return None

    return {
        "lon": (bounds["min_lon"] + bounds["max_lon"]) / 2,
        "lat": (bounds["min_lat"] + bounds["max_lat"]) / 2,
    }


def _iter_positions(geometry: dict[str, Any]) -> list[list[float]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    positions: list[list[float]] = []

    if geometry_type == "Point":
        if _is_position(coordinates):
            positions.append(coordinates)

    elif geometry_type == "LineString":
        positions.extend(
            coord for coord in coordinates if _is_position(coord)
        )

    elif geometry_type == "Polygon":
        for ring in coordinates:
            positions.extend(
                coord for coord in ring if _is_position(coord)
            )

    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            for ring in polygon:
                positions.extend(
                    coord for coord in ring if _is_position(coord)
                )

    elif geometry_type == "GeometryCollection":
        for child in geometry.get("geometries") or []:
            positions.extend(_iter_positions(child))

    return positions


def _is_position(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _slugify(value: str) -> str:
    value = value.strip().lower()

    result = []

    for char in value:
        if char.isalnum():
            result.append(char)
        elif char in {"-", "_", " "}:
            result.append("-")

    slug = "".join(result)

    while "--" in slug:
        slug = slug.replace("--", "-")

    return slug.strip("-") or "map-export"