from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from shared.logging import log_ok, log_section, log_step, log_warn


class QGISTools:
    """
    Headless QGIS-like processing using GDAL CLIs:
    - Clip raster by mask layer (GeoJSON boundary) via gdalwarp
    - Generate map tiles via gdal2tiles
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        qgis_root: str = "",
        gdalwarp_path: str = "gdalwarp",
        gdal2tiles_path: str = "gdal2tiles.py",
    ) -> None:
        self.logger = logger
        self.qgis_root = qgis_root
        self.gdalwarp_path = gdalwarp_path
        self.gdal2tiles_path = gdal2tiles_path


    # CLIP RASTER BY MASK
    def clip_raster_by_mask(
        self,
        *,
        input_tif: Path,
        mask_geojson: Path,
        output_tif: Path,
        dst_nodata: Optional[float] = None,
    ) -> Path:
        """
        Clip a GeoTIFF by a GeoJSON mask polygon (equivalent to QGIS
        Raster → Extraction → Clip raster by mask layer).
        """
        input_tif = Path(input_tif)
        mask_geojson = Path(mask_geojson)
        output_tif = Path(output_tif)
        output_tif.parent.mkdir(parents=True, exist_ok=True)

        if not input_tif.exists():
            raise FileNotFoundError(f"Input raster not found: {input_tif}")
        if not mask_geojson.exists():
            raise FileNotFoundError(f"Mask GeoJSON not found: {mask_geojson}")

        # Preflight: make sure the destination has room for at least the
        # source raster size again (clip output is <= input size, but we
        # want headroom for a network share that may be near-full).
        input_size = input_tif.stat().st_size
        try:
            free_bytes = shutil.disk_usage(output_tif.parent).free
        except OSError:
            free_bytes = None
        if free_bytes is not None and free_bytes < input_size:
            raise RuntimeError(
                f"Insufficient free space at {output_tif.parent}: "
                f"{free_bytes / (1024 ** 3):.2f} GB free, "
                f"input raster is {input_size / (1024 ** 3):.2f} GB. "
                "Free up space before re-running the clip."
            )

        # If a previous run died mid-write, a stale/corrupt output file
        # can trip libtiff on the next attempt. -overwrite tells gdalwarp
        # to replace it, but we remove it ourselves first to avoid any
        # partial-file edge cases on network shares (Z:\ etc.).
        if output_tif.exists():
            output_tif.unlink()

        log_step(self.logger, 1,
                 f"Clip raster by mask: {input_tif.name} → {output_tif.name}")

        cmd = [
            str(self.gdalwarp_path),
            "-overwrite",
            "-cutline",    str(mask_geojson),
            "-crop_to_cutline",
            "-of",         "GTiff",
            "-co", "BIGTIFF=IF_SAFER",
            "-co", "TILED=YES",
            "-co", "COMPRESS=LZW",
        ]
        if dst_nodata is not None:
            cmd += ["-dstnodata", str(dst_nodata)]
        cmd += [str(input_tif), str(output_tif)]

        t0 = time.perf_counter()
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            raise RuntimeError(f"gdalwarp not found: {self.gdalwarp_path}")
        except subprocess.CalledProcessError as e:
            # Clean up whatever partial file gdalwarp left behind so the
            # next attempt doesn't start from a corrupt TIFF.
            if output_tif.exists():
                output_tif.unlink(missing_ok=True)
            raise RuntimeError(
                f"gdalwarp clip failed: {(e.stderr or '')[:1000]}")

        elapsed = time.perf_counter() - t0
        size_mb = output_tif.stat().st_size / (1024 ** 2) if output_tif.exists() else 0
        log_ok(
            self.logger, f"Clip complete | output={output_tif.name} | size={size_mb:.1f} MB | elapsed={elapsed:.1f}s")

        return output_tif


    # GENERATE TILES
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
        Generate map tiles from a GeoTIFF using gdal2tiles.

        Resume behavior:
        - resume=True  -> keep existing output folder and pass --resume
        - resume=False -> remove existing output folder before fresh tiling
        - clean=True   -> force clean, unless resume=True
        """
        input_tif = Path(input_tif)
        output_dir = Path(output_dir)

        if not input_tif.exists():
            raise FileNotFoundError(f"Input raster not found: {input_tif}")

        if resume and clean:
            log_warn(
                self.logger,
                "Tile clean was requested together with resume=True; "
                "ignoring clean so existing tiles can be reused.",
            )
            clean = False

        if output_dir.exists() and clean:
            log_warn(self.logger, f"Cleaning existing tile output folder: {output_dir}")
            shutil.rmtree(output_dir, ignore_errors=True)

        elif output_dir.exists() and not resume:
            log_warn(
                self.logger,
                f"Removing existing tile output folder before fresh tiling: {output_dir}"
            )
            shutil.rmtree(output_dir, ignore_errors=True)

        output_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale GDAL auxiliary metadata files only.
        # These are not map tiles and can cause Windows/GDAL unlink issues.
        aux_count = 0
        for aux_file in output_dir.rglob("*.aux.xml"):
            try:
                aux_file.unlink(missing_ok=True)
                aux_count += 1
            except Exception:
                pass

        if aux_count:
            log_warn(self.logger, f"Removed {aux_count} stale GDAL aux files before tiling.")

        existing_tile_count = (
            sum(1 for _ in output_dir.rglob("*.png"))
            + sum(1 for _ in output_dir.rglob("*.jpg"))
            + sum(1 for _ in output_dir.rglob("*.jpeg"))
            + sum(1 for _ in output_dir.rglob("*.webp"))
        )

        log_step(
            self.logger,
            1,
            f"Generate tiles: {input_tif.name} → {output_dir.name}/",
        )

        self.logger.info(
            f"  zoom={zoom} | profile={profile} | webviewer={webviewer} | "
            f"copyright={copyright_text!r} | resume={resume} | "
            f"existing_tiles={existing_tile_count}"
        )

        if resume and existing_tile_count:
            self.logger.info(
                "  Resume mode enabled: existing tiles will be reused when possible."
            )

        gdal2tiles_path = Path(self.gdal2tiles_path)
        bat_path: Optional[Path] = None

        env = os.environ.copy()

        # Prevent GDAL from creating .aux.xml sidecar files.
        # This helps avoid Windows unlink errors during gdal2tiles.
        env["GDAL_PAM_ENABLED"] = "NO"

        if os.name == "nt" and gdal2tiles_path.suffix.lower() == ".py":
            if self.qgis_root:
                qgis_root = Path(self.qgis_root)
            else:
                try:
                    qgis_root = gdal2tiles_path.resolve().parents[3]
                except IndexError:
                    qgis_root = Path(env.get("OSGEO4W_ROOT", ""))

            qgis_python = qgis_root / "apps" / "Python312" / "python.exe"

            # QGIS standalone installs usually store env scripts in etc/ini.
            env_scripts = [
                qgis_root / "bin" / "o4w_env.bat",
                qgis_root / "etc" / "ini" / "python3.bat",
                qgis_root / "etc" / "ini" / "qt5.bat",
                qgis_root / "etc" / "ini" / "gdal.bat",
                qgis_root / "etc" / "ini" / "proj-runtime-data.bat",
            ]

            for required in (qgis_python, gdal2tiles_path):
                if not required.exists():
                    raise RuntimeError(f"Required QGIS file not found: {required}")

            bat_lines = ["@echo off"]
            bat_lines.append("set GDAL_PAM_ENABLED=NO")

            for env_bat in env_scripts:
                if env_bat.exists():
                    bat_lines.append(f'call "{env_bat}"')

            gdal2tiles_cmd = (
                f'"{qgis_python}" "{gdal2tiles_path}"'
                f' -p {profile} -z {zoom} -w {webviewer}'
                f' --copyright "{copyright_text}"'
            )

            if resume:
                gdal2tiles_cmd += " --resume"

            gdal2tiles_cmd += f' "{input_tif}" "{output_dir}"'
            bat_lines.append(gdal2tiles_cmd)

            bat_path = output_dir.parent / "_gdal2tiles_run.bat"
            bat_path.write_text("\r\n".join(bat_lines), encoding="utf-8")
            cmd = ["cmd.exe", "/c", str(bat_path)]

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

        t0 = time.perf_counter()
        self.logger.info("  Tiling in progress… (this may take several minutes)")

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

        except FileNotFoundError:
            raise RuntimeError(f"gdal2tiles not found: {self.gdal2tiles_path}")

        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"gdal2tiles failed.\n"
                f"STDERR:\n{(e.stderr or '')[:4000]}\n"
                f"STDOUT:\n{(e.stdout or '')[:4000]}"
            )

        finally:
            if bat_path is not None:
                try:
                    bat_path.unlink(missing_ok=True)
                except Exception:
                    pass

        elapsed = time.perf_counter() - t0

        tile_count = (
            sum(1 for _ in output_dir.rglob("*.png"))
            + sum(1 for _ in output_dir.rglob("*.jpg"))
            + sum(1 for _ in output_dir.rglob("*.jpeg"))
            + sum(1 for _ in output_dir.rglob("*.webp"))
        )

        log_ok(
            self.logger,
            f"Tiles generated | dir={output_dir.name}/ | "
            f"~{tile_count} tiles | elapsed={elapsed:.1f}s",
        )

        return output_dir