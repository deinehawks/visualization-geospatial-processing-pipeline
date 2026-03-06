from __future__ import annotations
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import subprocess
import shutil
import logging


class QGISTools:
    """
    Headless QGIS-like processing using GDAL CLIs:
    - Clip raster by mask layer (GeoJSON boundary) via gdalwarp
    - Generate tiles via gdal2tiles
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        gdalwarp_path: str = "gdalwarp",
        gdal2tiles_path: str = "gdal2tiles.py",
    ):
        self.logger = logger
        self.gdalwarp_path = gdalwarp_path
        self.gdal2tiles_path = gdal2tiles_path

    # -------------------------
    # Clip raster by mask layer
    # -------------------------
    def clip_raster_by_mask(
        self,
        *,
        input_tif: Path,
        mask_geojson: Path,
        output_tif: Path,
        dst_nodata: Optional[float] = None,
    ) -> Path:
        """
        Equivalent to QGIS:
        GDAL -> Raster extraction -> Clip raster by mask layer
        """
        input_tif = Path(input_tif)
        mask_geojson = Path(mask_geojson)
        output_tif = Path(output_tif)
        output_tif.parent.mkdir(parents=True, exist_ok=True)

        if not input_tif.exists():
            raise FileNotFoundError(f"Input raster not found: {input_tif}")
        if not mask_geojson.exists():
            raise FileNotFoundError(f"Mask GeoJSON not found: {mask_geojson}")

        cmd = [
            str(self.gdalwarp_path),
            "-overwrite",
            "-cutline",
            str(mask_geojson),
            "-crop_to_cutline",
            "-of",
            "GTiff",
        ]

        # optional nodata handling
        if dst_nodata is not None:
            cmd += ["-dstnodata", str(dst_nodata)]

        cmd += [str(input_tif), str(output_tif)]

        self.logger.info(f"Clipping raster by mask: {input_tif.name} -> {output_tif.name}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            raise RuntimeError(f"gdalwarp not found: {self.gdalwarp_path}")
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "")[:1000]
            raise RuntimeError(f"gdalwarp clip failed: {stderr}")

        return output_tif

    # -------------------------
    # gdal2tiles
    # -------------------------
    def generate_tiles(
        self,
        *,
        input_tif: Path,
        output_dir: Path,
        zoom: str = "11-24",
        profile: str = "mercator",
        webviewer: str = "none",
        copyright_text: str = "ASIMOV-HAWKS",
        resume: bool = False,
        clean: bool = False,
    ) -> Path:
        """
        Equivalent to QGIS:
        GDAL -> Raster miscellaneous -> gdal2tiles

        On Windows/QGIS, gdal2tiles.py should be run with the QGIS-bundled
        Python interpreter, not the project venv Python.
        """
        input_tif = Path(input_tif)
        output_dir = Path(output_dir)

        if not input_tif.exists():
            raise FileNotFoundError(f"Input raster not found: {input_tif}")

        if clean and output_dir.exists():
            shutil.rmtree(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        gdal2tiles_path = Path(self.gdal2tiles_path)

        # If path points to a Python script, use QGIS Python beside it
        if gdal2tiles_path.suffix.lower() == ".py":
            # Example:
            # C:/Program Files/QGIS 3.40.7/apps/Python312/Scripts/gdal2tiles.py
            qgis_python = gdal2tiles_path.parent.parent / "python.exe"

            if not qgis_python.exists():
                raise RuntimeError(
                    f"QGIS Python interpreter not found beside gdal2tiles: {qgis_python}"
                )

            cmd = [
                str(qgis_python),
                str(gdal2tiles_path),
                "-p", profile,
                "-z", zoom,
                "-w", webviewer,
                "--copyright", copyright_text,
            ]
        else:
            cmd = [
                str(gdal2tiles_path),
                "-p", profile,
                "-z", zoom,
                "-w", webviewer,
                "--copyright", copyright_text,
            ]

        if resume:
            cmd.append("--resume")

        cmd += [str(input_tif), str(output_dir)]

        self.logger.info(f"Generating tiles: {input_tif.name} -> {output_dir}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            raise RuntimeError(f"gdal2tiles not found: {self.gdal2tiles_path}")
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "")[:2000]
            stdout = (e.stdout or "")[:2000]
            raise RuntimeError(
                f"gdal2tiles failed.\nSTDERR:\n{stderr}\nSTDOUT:\n{stdout}"
            )

        return output_dir