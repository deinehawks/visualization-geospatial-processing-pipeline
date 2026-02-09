"""
KML Boundary Setter Module

Extracts boundary coordinates from KML files and converts them to:
- GeoJSON format (for WebODM)
- CSV format with boundary metadata
"""

import os
import csv
import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Tuple
from dotenv import load_dotenv


class KMLBoundarySetter:
    """Processes KML files and generates GeoJSON and CSV boundary files."""
    
    def __init__(self, kml_dir: str = None, geojson_dir: str = None, csv_dir: str = None):
        """
        Initialize the KML Boundary Setter.
        
        Args:
            kml_dir: Directory containing KML files
            geojson_dir: Directory to save GeoJSON files
            csv_dir: Directory to save CSV files
        """
        # Load environment variables
        load_dotenv()
        
        # Set directories from parameters or environment variables
        self.kml_dir = kml_dir or os.getenv("KML_DIR", "kml")
        self.geojson_dir = geojson_dir or os.getenv("GEOJSON_DIR", "geojson")
        self.csv_dir = csv_dir or os.getenv("CSV_DIR", "csv")
        
        # Create directories if they don't exist
        os.makedirs(self.kml_dir, exist_ok=True)
        os.makedirs(self.geojson_dir, exist_ok=True)
        os.makedirs(self.csv_dir, exist_ok=True)
        
        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.processed_files = []
        self.failed_files = []
    
    def extract_coordinates_from_kml(self, kml_path: str) -> Optional[List[List[str]]]:
        """
        Extracts the coordinate list from a KML file, supporting namespaces.
        
        Args:
            kml_path: Path to the KML file
            
        Returns:
            List of coordinate triplets [lon, lat, alt] or None if not found
        """
        try:
            tree = ET.parse(kml_path)
            root = tree.getroot()
            ns = {'kml': 'http://www.opengis.net/kml/2.2'}
            
            # Find the <coordinates> tag (with namespace)
            coords_elem = root.find('.//kml:coordinates', ns)
            if coords_elem is None or not coords_elem.text:
                # Try without namespace as fallback
                coords_elem = root.find('.//coordinates')
                if coords_elem is None or not coords_elem.text:
                    return None
            
            coords_text = coords_elem.text.strip()
            coords_list = [c for c in coords_text.replace('\n', ' ').split(' ') if c]
            xyz_list = [triplet.split(',') for triplet in coords_list if len(triplet.split(',')) >= 2]
            
            return xyz_list if xyz_list else None
        except Exception as e:
            self.logger.error(f"Failed to parse {kml_path}: {e}")
            return None
    
    def format_xyz_to_latlon(self, coords: List[List[str]]) -> List[List[float]]:
        """
        Formats coordinates as [lat, lon] for Leaflet.
        
        Args:
            coords: List of [lon, lat, alt] coordinates
            
        Returns:
            List of [lat, lon] coordinates
        """
        return [[float(c[1]), float(c[0])] for c in coords]
    
    def format_xyz_to_lonlat(self, coords: List[List[str]]) -> List[List[float]]:
        """
        Formats coordinates as [lon, lat] for MapLibre/GeoJSON.
        
        Args:
            coords: List of [lon, lat, alt] coordinates
            
        Returns:
            List of [lon, lat] coordinates
        """
        return [[float(c[0]), float(c[1])] for c in coords]
    
    def get_min_max_xy(self, coords: List[List[str]]) -> Tuple[float, float, float, float]:
        """
        Returns bounding box from coordinates.
        
        Args:
            coords: List of [lon, lat, ...] coordinates
            
        Returns:
            Tuple of (min_lon, min_lat, max_lon, max_lat)
        """
        longitudes = [float(x[0]) for x in coords]
        latitudes = [float(x[1]) for x in coords]
        return min(longitudes), min(latitudes), max(longitudes), max(latitudes)
    
    def write_geojson(self, filename: str, boundary: List[List[float]]) -> None:
        """
        Writes a GeoJSON Polygon file.
        
        Args:
            filename: Output filename
            boundary: List of [lon, lat] coordinates
        """
        geojson_obj = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [boundary]
                    }
                }
            ]
        }
        with open(filename, "w") as f:
            json.dump(geojson_obj, f, indent=2)
        
        self.logger.info(f"✓ GeoJSON saved: {filename}")
    
    def write_csv(self, filename: str, row: dict) -> None:
        """
        Writes a CSV file with boundary metadata.
        
        Args:
            filename: Output filename
            row: Dictionary with boundary metadata
        """
        fieldnames = [
            "id", "code", "area_code", "access_code", "type", 
            "flight_date", "location", "area", 
            "max_x", "max_y", "min_x", "min_y", 
            "tags", "boundaries", "geojson_boundaries", 
            "ortho", "point_cloud"
        ]
        with open(filename, "w", newline='') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)
        
        self.logger.info(f"✓ CSV saved: {filename}")
    
    def process_kml_file(self, kml_file: str, metadata: dict = None) -> bool:
        """
        Processes a single KML file and writes GeoJSON and CSV outputs.
        
        Args:
            kml_file: Name of the KML file
            metadata: Optional metadata dictionary to include in CSV
            
        Returns:
            True if successful, False otherwise
        """
        kml_path = os.path.join(self.kml_dir, kml_file)
        
        # Extract coordinates
        coords = self.extract_coordinates_from_kml(kml_path)
        if not coords or len(coords) < 3:
            self.logger.warning(f"⚠ No valid polygon coordinates found in {kml_file}. Skipping.")
            self.failed_files.append(kml_file)
            return False
        
        # Format coordinates
        leaflet_boundary = self.format_xyz_to_latlon(coords)
        maplibre_boundary = self.format_xyz_to_lonlat(coords)
        min_x, min_y, max_x, max_y = self.get_min_max_xy(coords)
        
        # Write GeoJSON
        geojson_filename = os.path.splitext(kml_file)[0] + ".geojson"
        geojson_path = os.path.join(self.geojson_dir, geojson_filename)
        self.write_geojson(geojson_path, maplibre_boundary)
        
        # Prepare CSV data
        base_id = os.path.splitext(kml_file)[0]
        row = {
            "id": base_id,
            "code": metadata.get("code") if metadata else None,
            "area_code": metadata.get("area_code") if metadata else None,
            "access_code": metadata.get("access_code") if metadata else None,
            "type": metadata.get("type") if metadata else None,
            "flight_date": metadata.get("flight_date") if metadata else None,
            "location": metadata.get("location") if metadata else None,
            "area": metadata.get("area") if metadata else None,
            "max_x": max_x,
            "max_y": max_y,
            "min_x": min_x,
            "min_y": min_y,
            "tags": metadata.get("tags") if metadata else None,
            "boundaries": json.dumps(leaflet_boundary),
            "geojson_boundaries": json.dumps(maplibre_boundary),
            "ortho": metadata.get("ortho") if metadata else None,
            "point_cloud": metadata.get("point_cloud") if metadata else None
        }
        
        # Write CSV
        csv_filename = os.path.splitext(kml_file)[0] + ".csv"
        csv_path = os.path.join(self.csv_dir, csv_filename)
        self.write_csv(csv_path, row)
        
        self.processed_files.append({
            "kml": kml_file,
            "geojson": geojson_path,
            "csv": csv_path
        })
        
        self.logger.info(f"✓ Processed {kml_file}")
        return True
    
    def process_all(self) -> dict:
        """
        Processes all KML files in the kml directory.
        
        Returns:
            Dictionary with processing summary
        """
        kml_files = [f for f in os.listdir(self.kml_dir) if f.lower().endswith('.kml')]
        
        if not kml_files:
            self.logger.error("✗ No KML files found in the kml directory.")
            return {
                "success": False,
                "message": "No KML files found",
                "processed": 0,
                "failed": 0
            }
        
        self.logger.info(f"Found {len(kml_files)} KML file(s) to process")
        
        for kml_file in kml_files:
            self.process_kml_file(kml_file)
        
        summary = {
            "success": True,
            "processed": len(self.processed_files),
            "failed": len(self.failed_files),
            "processed_files": self.processed_files,
            "failed_files": self.failed_files
        }
        
        self.logger.info("\n" + "="*60)
        self.logger.info("PROCESSING SUMMARY")
        self.logger.info("="*60)
        self.logger.info(f"✓ Processed: {len(self.processed_files)}")
        self.logger.info(f"✗ Failed: {len(self.failed_files)}")
        self.logger.info(f"GeoJSON files: {self.geojson_dir}")
        self.logger.info(f"CSV files: {self.csv_dir}")
        
        return summary
    
    def get_geojson_path(self, kml_filename: str) -> Optional[str]:
        """
        Get the GeoJSON file path for a given KML filename.
        
        Args:
            kml_filename: Name of the KML file
            
        Returns:
            Path to the corresponding GeoJSON file or None
        """
        geojson_filename = os.path.splitext(kml_filename)[0] + ".geojson"
        geojson_path = os.path.join(self.geojson_dir, geojson_filename)
        
        if os.path.exists(geojson_path):
            return geojson_path
        return None


def main():
    """Main entry point for standalone usage."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO, 
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    # Create processor
    processor = KMLBoundarySetter()
    
    # Process all KML files
    summary = processor.process_all()
    
    return summary


if __name__ == "__main__":
    main()