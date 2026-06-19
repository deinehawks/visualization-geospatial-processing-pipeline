from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile


def extract_kml_from_kmz(
    *,
    kmz_path: Path,
    output_dir: Path,
    survey_name: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(kmz_path, "r") as zf:
        kml_members = [
            name
            for name in zf.namelist()
            if name.lower().endswith(".kml")
        ]

        if not kml_members:
            raise FileNotFoundError(
                f"No KML file found inside KMZ: {kmz_path}"
            )

        # Prefer doc.kml if available.
        kml_members.sort(
            key=lambda name: (
                0 if Path(name).name.lower() == "doc.kml" else 1,
                len(Path(name).parts),
                name.lower(),
            )
        )

        member = kml_members[0]
        output_path = output_dir / f"{_safe_name(survey_name)}.kml"

        with zf.open(member) as src:
            output_path.write_bytes(src.read())

        return output_path


def parse_kml_to_features(
    *,
    kml_path: Path,
    survey_name: str,
    source_boundary: Path,
) -> list[dict[str, Any]]:
    root = ET.parse(kml_path).getroot()

    placemarks = [
        elem
        for elem in root.iter()
        if _local_name(elem.tag) == "Placemark"
    ]

    # Some KMLs may contain polygons without clear Placemark nesting.
    if not placemarks:
        placemarks = [root]

    features: list[dict[str, Any]] = []

    for placemark_index, placemark in enumerate(placemarks, start=1):
        placemark_name = (
            _first_child_text(placemark, "name")
            or f"{survey_name} boundary {placemark_index}"
        )

        polygons = [
            elem
            for elem in placemark.iter()
            if _local_name(elem.tag) == "Polygon"
        ]

        for polygon_index, polygon in enumerate(polygons, start=1):
            outer_ring = _extract_outer_ring(polygon)

            if not outer_ring:
                continue

            inner_rings = _extract_inner_rings(polygon)

            geometry = {
                "type": "Polygon",
                "coordinates": [outer_ring, *inner_rings],
            }

            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "survey_name": survey_name,
                        "placemark_name": placemark_name,
                        "polygon_index": polygon_index,
                        "source_boundary": str(source_boundary),
                        "source_kml": str(kml_path),
                    },
                    "geometry": geometry,
                }
            )

    if not features:
        raise ValueError(
            f"No polygon boundaries found in KML: {kml_path}"
        )

    return features


def _extract_outer_ring(polygon: ET.Element) -> list[list[float]]:
    outer_nodes = [
        elem
        for elem in polygon.iter()
        if _local_name(elem.tag) == "outerBoundaryIs"
    ]

    if not outer_nodes:
        return []

    coordinates_text = _first_descendant_text(
        outer_nodes[0],
        "coordinates",
    )

    return _parse_coordinates(coordinates_text)


def _extract_inner_rings(polygon: ET.Element) -> list[list[list[float]]]:
    inner_nodes = [
        elem
        for elem in polygon.iter()
        if _local_name(elem.tag) == "innerBoundaryIs"
    ]

    rings: list[list[list[float]]] = []

    for node in inner_nodes:
        coordinates_text = _first_descendant_text(node, "coordinates")
        ring = _parse_coordinates(coordinates_text)

        if ring:
            rings.append(ring)

    return rings


def _parse_coordinates(raw: str | None) -> list[list[float]]:
    if not raw:
        return []

    coords: list[list[float]] = []

    for token in raw.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")

        if len(parts) < 2:
            continue

        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue

        coords.append([lon, lat])

    if len(coords) < 3:
        return []

    # GeoJSON polygon rings should be closed.
    if coords[0] != coords[-1]:
        coords.append(coords[0])

    if len(coords) < 4:
        return []

    return coords


def _first_child_text(elem: ET.Element, child_name: str) -> str | None:
    for child in list(elem):
        if _local_name(child.tag) == child_name and child.text:
            return child.text.strip()

    return None


def _first_descendant_text(elem: ET.Element, descendant_name: str) -> str | None:
    for child in elem.iter():
        if _local_name(child.tag) == descendant_name and child.text:
            return child.text.strip()

    return None


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]

    return tag


def _safe_name(value: str) -> str:
    keep = []

    for char in value.strip():
        if char.isalnum() or char in {"-", "_"}:
            keep.append(char)
        else:
            keep.append("_")

    result = "".join(keep).strip("_")
    return result or "boundary"