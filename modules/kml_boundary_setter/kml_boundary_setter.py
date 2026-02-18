"""
KML Boundary Setter Module (Pipeline-ready)

Extracts boundary coordinates from KML files and converts them to:
- GeoJSON format (for WebODM)
- CSV format with boundary metadata
"""

from __future__ import annotations

import csv
import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any


@dataclass
class ProcessedKMLResult:
    kml: str
    geojson: str
    csv: str


class KMLBoundarySetter:
    """Processes KML files and generates GeoJSON and CSV boundary files."""

    def __init__(
        self,
        kml_dir: Path,
        geojson_dir: Path,
        csv_dir: Path,
        logger: logging.Logger,
    ):
        self.kml_dir = Path(kml_dir)
        self.geojson_dir = Path(geojson_dir)
        self.csv_dir = Path(csv_dir)
        self.logger = logger

        self.processed_files: List[ProcessedKMLResult] = []
        self.failed_files: List[str] = []

    # ---------------- Core parsing ---------------- #

    def extract_coordinates_from_kml(self, kml_path: Path) -> Optional[List[List[str]]]:
        """
        Extracts the coordinate list from a KML file, supporting namespaces.
        Returns list of coordinate triplets [lon, lat, alt?] as strings.
        """
        try:
            tree = ET.parse(kml_path)
            root = tree.getroot()

            # Standard KML namespace
            ns = {"kml": "http://www.opengis.net/kml/2.2"}

            # Find coordinates tag (namespace first, then fallback)
            coords_elem = root.find(".//kml:coordinates", ns)
            if coords_elem is None or not (coords_elem.text and coords_elem.text.strip()):
                coords_elem = root.find(".//coordinates")

            if coords_elem is None or not (coords_elem.text and coords_elem.text.strip()):
                return None

            coords_text = coords_elem.text.strip()
            coords_list = [c for c in coords_text.replace("\n", " ").split(" ") if c]
            xyz_list = [triplet.split(",") for triplet in coords_list if len(triplet.split(",")) >= 2]

            return xyz_list if xyz_list else None

        except Exception:
            self.logger.exception(f"Failed to parse KML: {kml_path}")
            return None

    def format_xyz_to_latlon(self, coords: List[List[str]]) -> List[List[float]]:
        """Formats coordinates as [lat, lon] for Leaflet."""
        return [[float(c[1]), float(c[0])] for c in coords]

    def format_xyz_to_lonlat(self, coords: List[List[str]]) -> List[List[float]]:
        """Formats coordinates as [lon, lat] for GeoJSON/MapLibre."""
        return [[float(c[0]), float(c[1])] for c in coords]

    def get_min_max_xy(self, coords: List[List[str]]) -> Tuple[float, float, float, float]:
        """Returns bounding box from coordinates."""
        longitudes = [float(x[0]) for x in coords]
        latitudes = [float(x[1]) for x in coords]
        return min(longitudes), min(latitudes), max(longitudes), max(latitudes)

    # ---------------- Writers ---------------- #

    def write_geojson(self, filepath: Path, boundary_lonlat: List[List[float]]) -> None:
        """
        Writes a GeoJSON Polygon file.

        Note: GeoJSON polygon coordinates should typically be closed (first==last).
        We'll close it if not closed.
        """
        if boundary_lonlat and boundary_lonlat[0] != boundary_lonlat[-1]:
            boundary_lonlat = boundary_lonlat + [boundary_lonlat[0]]

        geojson_obj = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Polygon", "coordinates": [boundary_lonlat]},
                }
            ],
        }

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(json.dumps(geojson_obj, indent=2), encoding="utf-8")
        self.logger.info(f"GeoJSON saved: {filepath}")

    def write_csv(self, filepath: Path, row: Dict[str, Any]) -> None:
        """Writes a CSV file with boundary metadata."""
        fieldnames = [
            "id",
            "code",
            "area_code",
            "access_code",
            "type",
            "flight_date",
            "location",
            "area",
            "max_x",
            "max_y",
            "min_x",
            "min_y",
            "tags",
            "boundaries",
            "geojson_boundaries",
            "ortho",
            "point_cloud",
        ]

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)

        self.logger.info(f"CSV saved: {filepath}")

    # ---------------- Public API ---------------- #

    def process_kml_file(self, kml_path: Path, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Processes one KML file and writes GeoJSON and CSV outputs.
        Returns True if success.
        """
        kml_path = Path(kml_path)

        self.logger.info(f"Processing KML: {kml_path.name}")

        coords = self.extract_coordinates_from_kml(kml_path)
        if not coords or len(coords) < 3:
            self.logger.warning(f"No valid polygon coordinates found in {kml_path.name}. Skipping.")
            self.failed_files.append(kml_path.name)
            return False

        leaflet_boundary = self.format_xyz_to_latlon(coords)
        geojson_boundary = self.format_xyz_to_lonlat(coords)
        min_x, min_y, max_x, max_y = self.get_min_max_xy(coords)

        base_id = kml_path.stem

        # Output paths
        geojson_path = self.geojson_dir / f"{base_id}.geojson"
        csv_path = self.csv_dir / f"{base_id}.csv"

        # Write GeoJSON
        self.write_geojson(geojson_path, geojson_boundary)

        # Prepare CSV row
        md = metadata or {}
        row = {
            "id": base_id,
            "code": md.get("code"),
            "area_code": md.get("area_code"),
            "access_code": md.get("access_code"),
            "type": md.get("type"),
            "flight_date": md.get("flight_date"),
            "location": md.get("location"),
            "area": md.get("area"),
            "max_x": max_x,
            "max_y": max_y,
            "min_x": min_x,
            "min_y": min_y,
            "tags": md.get("tags"),
            "boundaries": json.dumps(leaflet_boundary),
            "geojson_boundaries": json.dumps(geojson_boundary),
            "ortho": md.get("ortho"),
            "point_cloud": md.get("point_cloud"),
        }

        self.write_csv(csv_path, row)

        self.processed_files.append(
            ProcessedKMLResult(kml=kml_path.name, geojson=str(geojson_path), csv=str(csv_path))
        )
        self.logger.info(f"Processed: {kml_path.name}")
        return True

    def process_all(self, metadata_map: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Processes all .kml files in the kml_dir.

        metadata_map (optional): dict keyed by base_id or filename
            Example:
              {
                "survey_001": {...},
                "survey_002": {...}
              }
        """
        metadata_map = metadata_map or {}

        if not self.kml_dir.exists():
            self.logger.error(f"KML directory does not exist: {self.kml_dir}")
            return {"success": False, "message": "KML directory missing", "processed": 0, "failed": 0}

        kml_files = sorted([p for p in self.kml_dir.iterdir() if p.is_file() and p.suffix.lower() == ".kml"])

        if not kml_files:
            self.logger.error(f"No KML files found in: {self.kml_dir}")
            return {"success": False, "message": "No KML files found", "processed": 0, "failed": 0}

        self.logger.info(f"Found {len(kml_files)} KML file(s) in {self.kml_dir}")

        for kml_path in kml_files:
            key1 = kml_path.stem
            key2 = kml_path.name
            md = metadata_map.get(key1) or metadata_map.get(key2)
            self.process_kml_file(kml_path, metadata=md)

        summary = {
            "success": True,
            "processed": len(self.processed_files),
            "failed": len(self.failed_files),
            "processed_files": [r.__dict__ for r in self.processed_files],
            "failed_files": self.failed_files,
            "geojson_dir": str(self.geojson_dir),
            "csv_dir": str(self.csv_dir),
        }

        self.logger.info("KML processing summary | "
                         f"processed={summary['processed']} failed={summary['failed']}")
        return summary

    def get_geojson_path(self, kml_filename: str) -> Optional[str]:
        """Return GeoJSON path for a given KML filename if it exists."""
        geojson_path = self.geojson_dir / f"{Path(kml_filename).stem}.geojson"
        return str(geojson_path) if geojson_path.exists() else None


# ---------------- Pipeline entry ---------------- #

def run_kml(
    kml_dir: Path,
    geojson_dir: Path,
    csv_dir: Path,
    logger: logging.Logger,
    metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Pipeline-friendly entry point.
    """
    setter = KMLBoundarySetter(
        kml_dir=kml_dir,
        geojson_dir=geojson_dir,
        csv_dir=csv_dir,
        logger=logger,
    )
    return setter.process_all(metadata_map=metadata_map)
