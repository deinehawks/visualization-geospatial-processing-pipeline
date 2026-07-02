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
        gdalinfo_path: str = "gdalinfo",
    ) -> None:
        self.logger = logger
        self.qgis_root = qgis_root
        self.gdalwarp_path = gdalwarp_path
        self.gdal2tiles_path = gdal2tiles_path
        self.gdalinfo_path = gdalinfo_path


    # STAGE FILE TO LOCAL DISK
    def stage_local_copy(self, src: Path, local_dir: Path) -> Path:
        """
        Copy src (typically on a network share) to local_dir on local disk,
        verifying the copy landed intact. Used to pull a raster off Z:\\
        before handing it to gdal2tiles, which does thousands of small
        reads over the life of a tiling run and is far more exposed to a
        network hiccup than a single sequential clip/checksum pass.
        """
        src = Path(src)
        local_dir = Path(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        dst = local_dir / src.name

        src_size = src.stat().st_size
        if dst.exists() and dst.stat().st_size == src_size:
            log_step(self.logger, 1, f"Local staging copy already present: {dst.name}")
        else:
            log_step(self.logger, 1, f"Staging {src.name} to local disk: {dst}")
            t0 = time.perf_counter()
            shutil.copy2(src, dst)
            elapsed = time.perf_counter() - t0
            log_ok(self.logger, f"Staged locally in {elapsed:.1f}s: {dst}")

        dst_size = dst.stat().st_size
        if dst_size != src_size:
            dst.unlink(missing_ok=True)
            raise RuntimeError(
                f"Local staging copy size mismatch for {src.name}: "
                f"source={src_size} bytes, copy={dst_size} bytes"
            )

        # Full read-back on the local copy too — cheap on local disk, and
        # catches a bad copy (or a source that was itself unreadable)
        # before we sink minutes into tiling it.
        self.verify_raster_readable(dst, retries=1, delay_s=1.0)

        return dst


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
            # BIGTIFF=IF_SAFER: switch to BigTIFF automatically once the
            # output is projected to cross the classic 4GB TIFF ceiling,
            # instead of silently corrupting the write past that point
            # (this is what produced the TIFFAppendToStrip/"No error"
            # failures on the large orthomosaic).
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

        # gdalwarp reporting success does not guarantee every block has
        # actually landed on a network share (Z:\ etc.) — SMB clients can
        # hold recently-written blocks in a write-back cache for a moment
        # after the writing process exits. If we hand the file straight to
        # gdal2tiles, it can hit a block that isn't really there yet and
        # fail with TIFFFillTile/"Read error ... got 0 bytes". So we force
        # a full read-back here (every band, every block) and retry a
        # couple of times with a short wait before giving up, rather than
        # discovering the corruption 10+ minutes into tiling.
        self.verify_raster_readable(output_tif, retries=3, delay_s=5.0)

        log_ok(
            self.logger, f"Clip complete | output={output_tif.name} | size={size_mb:.1f} MB | elapsed={elapsed:.1f}s")

        return output_tif

    # VERIFY RASTER IS FULLY READABLE
    def verify_raster_readable(
        self,
        tif_path: Path,
        *,
        retries: int = 3,
        delay_s: float = 5.0,
    ) -> None:
        """
        Force GDAL to read every block of every band in tif_path (via
        `gdalinfo -checksum`), retrying a few times with a short delay.
        This catches network-share write-back cache races where gdalwarp
        exits successfully but the file isn't fully flushed to the remote
        share yet. Raises RuntimeError if the file still isn't readable
        after all retries.
        """
        last_err = ""
        for attempt in range(1, retries + 1):
            try:
                subprocess.run(
                    [str(self.gdalinfo_path), "-checksum", str(tif_path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return
            except FileNotFoundError:
                # gdalinfo not on PATH — skip verification rather than
                # blocking the pipeline over a missing optional tool.
                log_warn(
                    self.logger,
                    f"gdalinfo not found ({self.gdalinfo_path}); "
                    "skipping post-clip read-back verification.",
                )
                return
            except subprocess.CalledProcessError as e:
                last_err = (e.stderr or "")[:1000]
                if attempt < retries:
                    log_warn(
                        self.logger,
                        f"Read-back check failed on attempt {attempt}/{retries} "
                        f"for {tif_path.name} (likely network share flush delay); "
                        f"retrying in {delay_s:.0f}s...",
                    )
                    time.sleep(delay_s)

        raise RuntimeError(
            f"Clipped output failed read-back verification after {retries} "
            f"attempts: {tif_path.name} — {last_err}"
        )


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