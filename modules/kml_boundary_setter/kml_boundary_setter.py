"""
KML Boundary Setter Module (Pipeline-ready)

Extracts boundary coordinates from:
- .kml
- .kmz (auto-extracts embedded KML)

Converts to:
- GeoJSON format (for WebODM)
- CSV format with boundary metadata
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import csv
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
import tempfile
import shutil


@dataclass
class ProcessedKMLResult:
    kml: str
    geojson: str
    csv: str


class KMLBoundarySetter:
    """Processes KML/KMZ files and generates GeoJSON and CSV boundary files."""

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

    # ============================================================
    # KMZ SUPPORT
    # ============================================================

    def _extract_kml_from_kmz(self, kmz_path: Path) -> Optional[Path]:
        """
        Extract first KML inside KMZ to temp folder and return its path.
        """
        try:
            tmp_dir = Path(tempfile.mkdtemp(prefix="kmz_extract_"))
            with zipfile.ZipFile(kmz_path, "r") as zf:
                kml_members = [m for m in zf.namelist() if m.lower().endswith(".kml")]
                if not kml_members:
                    self.logger.warning(f"No .kml inside KMZ: {kmz_path.name}")
                    return None

                member = kml_members[0]
                zf.extract(member, path=tmp_dir)

                extracted = tmp_dir / member
                if not extracted.exists():
                    extracted = next(tmp_dir.rglob("*.kml"), None)

                return extracted

        except Exception:
            self.logger.exception(f"Failed to extract KMZ: {kmz_path}")
            return None

    # ============================================================
    # KML PARSING
    # ============================================================

    def extract_coordinates_from_kml(self, kml_path: Path) -> Optional[List[List[str]]]:
        try:
            tree = ET.parse(kml_path)
            root = tree.getroot()

            ns = {"kml": "http://www.opengis.net/kml/2.2"}

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
        return [[float(c[1]), float(c[0])] for c in coords]

    def format_xyz_to_lonlat(self, coords: List[List[str]]) -> List[List[float]]:
        return [[float(c[0]), float(c[1])] for c in coords]

    def get_min_max_xy(self, coords: List[List[str]]) -> Tuple[float, float, float, float]:
        longitudes = [float(x[0]) for x in coords]
        latitudes = [float(x[1]) for x in coords]
        return min(longitudes), min(latitudes), max(longitudes), max(latitudes)

    # ============================================================
    # WRITERS
    # ============================================================

    def write_geojson(self, filepath: Path, boundary_lonlat: List[List[float]]) -> None:
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
        fieldnames = [
            "id", "code", "area_code", "access_code", "type",
            "flight_date", "location", "area",
            "max_x", "max_y", "min_x", "min_y",
            "tags", "boundaries", "geojson_boundaries",
            "ortho", "point_cloud",
        ]

        filepath.parent.mkdir(parents=True, exist_ok=True)
        with filepath.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)

        self.logger.info(f"CSV saved: {filepath}")

    # ============================================================
    # PROCESSING
    # ============================================================

    def process_kml_file(self, kml_path: Path, metadata: Optional[Dict[str, Any]] = None) -> bool:
        self.logger.info(f"Processing: {kml_path.name}")

        coords = self.extract_coordinates_from_kml(kml_path)
        if not coords or len(coords) < 3:
            self.logger.warning(f"No valid polygon found in {kml_path.name}")
            self.failed_files.append(kml_path.name)
            return False

        leaflet_boundary = self.format_xyz_to_latlon(coords)
        geojson_boundary = self.format_xyz_to_lonlat(coords)
        min_x, min_y, max_x, max_y = self.get_min_max_xy(coords)

        base_id = kml_path.stem
        geojson_path = self.geojson_dir / f"{base_id}.geojson"
        csv_path = self.csv_dir / f"{base_id}.csv"

        self.write_geojson(geojson_path, geojson_boundary)

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

        return True

    # ============================================================
    # MAIN ENTRY
    # ============================================================

    def process_all(self, metadata_map: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        metadata_map = metadata_map or {}

        if not self.kml_dir.exists():
            return {"success": False, "message": "KML directory missing", "processed": 0, "failed": 0}

        all_files = sorted([p for p in self.kml_dir.iterdir() if p.is_file() and p.suffix.lower() in (".kml", ".kmz")])

        if not all_files:
            return {"success": False, "message": "No KML/KMZ files found", "processed": 0, "failed": 0}

        self.logger.info(f"Found {len(all_files)} KML/KMZ file(s)")

        for file_path in all_files:
            working_kml = file_path

            # KMZ handling
            if file_path.suffix.lower() == ".kmz":
                extracted = self._extract_kml_from_kmz(file_path)
                if not extracted:
                    self.failed_files.append(file_path.name)
                    continue
                working_kml = extracted

            key1 = working_kml.stem
            key2 = working_kml.name
            md = metadata_map.get(key1) or metadata_map.get(key2)

            self.process_kml_file(working_kml, metadata=md)

        summary = {
            "success": True,
            "processed": len(self.processed_files),
            "failed": len(self.failed_files),
            "processed_files": [r.__dict__ for r in self.processed_files],
            "failed_files": self.failed_files,
            "geojson_dir": str(self.geojson_dir),
            "csv_dir": str(self.csv_dir),
        }

        self.logger.info(f"KML summary | processed={summary['processed']} failed={summary['failed']}")
        return summary


# ---------------- Pipeline entry ---------------- #

def run_kml(
    kml_dir: Path,
    geojson_dir: Path,
    csv_dir: Path,
    logger: logging.Logger,
    metadata_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:

    setter = KMLBoundarySetter(
        kml_dir=kml_dir,
        geojson_dir=geojson_dir,
        csv_dir=csv_dir,
        logger=logger,
    )
    return setter.process_all(metadata_map=metadata_map)
