"""
Data Segregation Module

Ingests pilot field folder and converts it into
standardized survey folder structure.

Responsible for:
- Survey ID generation (AH-0YYNNN)
- Folder structure creation
- Copying required files (images + kml)
- Validation
- Manifest generation
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from shared.logging import (
    log_ok,
    log_progress,
    log_progress_done,
    log_section,
    log_step,
    log_warn,
)

# SURVEY ID GENERATOR
def generate_next_survey_id(surveys_root: Path, year: int, logger: logging.Logger) -> str:
    year_dir = surveys_root / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"AH-0{str(year)[-2:]}"
    pattern = re.compile(rf"{prefix}(\d{{3}})")

    max_number = 0
    for folder in year_dir.iterdir():
        if not folder.is_dir():
            continue
        match = pattern.fullmatch(folder.name)
        if match:
            max_number = max(max_number, int(match.group(1)))

    survey_id = f"{prefix}{max_number + 1:03d}"
    log_ok(logger, f"Survey ID generated: {survey_id}")
    return survey_id


# MAIN RUN FUNCTION
def run(
    source_dir: Path,
    surveys_root: Path,
    year: int,
    logger: logging.Logger,
    *,
    force: bool = False,
    survey_id_override: Optional[str] = None,
    use_year_subdir: bool = True,
) -> Dict[str, Any]:

    log_section(logger, "DATA SEGREGATION")

    log_step(logger, 1, "Resolve survey ID")

    if survey_id_override:
        survey_id = survey_id_override
        log_ok(logger, f"Using provided survey ID: {survey_id}")
    else:
        survey_id = generate_next_survey_id(surveys_root, year, logger)

    survey_base = surveys_root / str(year) if use_year_subdir else surveys_root
    survey_path = survey_base / survey_id / "rgb"

    if survey_path.exists() and not force:
        raise FileExistsError(f"Survey path already exists: {survey_path}")

    log_step(logger, 2, "Create folder structure")

    dirs: Dict[str, Path] = {
        "rgb_root":          survey_path,
        "boundary":          survey_path / "boundary",
        "raw":               survey_path / "images" / "raw",
        "path":              survey_path / "images" / "path",
        "cross_runs":        survey_path / "images" / "cross-runs",
        "ortho":             survey_path / "ortho",
        "odm":               survey_path / "odm",
        "dem_odm":           survey_path / "dem" / "odm",
        "dem_dtm":           survey_path / "dem" / "odm" / "dtm",
        "dem_dsm":           survey_path / "dem" / "odm" / "dsm",
        "3d":                survey_path / "3d",
        "qgis_root":         survey_path / "qgis",
        "qgis_clipped":      survey_path / "qgis" / "clipped",
        "qgis_clipped_ortho": survey_path / "qgis" / "clipped" / "ortho",
        "tiles_root":        survey_path / "tiles",
        "tiles_ortho":       survey_path / "tiles" / "ortho",
        "tiles_ortho_sharp": survey_path / "tiles" / "ortho" / "sharp-corners",
        "tiles_ortho_round": survey_path / "tiles" / "ortho" / "round-corners",
    }

    extra_folders = [
        survey_path / "dem" / "lidar",
        survey_path / "object-detection",
    ]

    for p in list(dirs.values()) + extra_folders:
        p.mkdir(parents=True, exist_ok=True)

    log_ok(logger, f"Folder structure created: {survey_path}")

    log_step(logger, 3, "Discover source images")

    image_extensions = {".jpg", ".jpeg", ".JPG", ".JPEG"}
    images = [f for f in source_dir.rglob("*") if f.suffix in image_extensions]

    if not images:
        raise ValueError("No JPG/JPEG images found in source directory.")

    total_bytes = sum(p.stat().st_size for p in images)
    gb = total_bytes / (1024 ** 3)
    log_ok(logger, f"Found {len(images)} images ({gb:.2f} GB)")

    log_step(logger, 4, "Copy images → raw")

    raw_dir = dirs["raw"]
    total = len(images)
    t0 = time.perf_counter()

    for i, img in enumerate(images, 1):
        shutil.copy2(img, raw_dir / img.name)
        if i % 25 == 0 or i == total:
            log_progress(
                logger, "Copying images",
                current=i, total=total,
                elapsed=time.perf_counter() - t0,
            )

    log_progress_done(logger, "Copying images", total=total,
                      elapsed=time.perf_counter() - t0)

    log_step(logger, 5, "Locate and copy boundary file (KML/KMZ)")

    kml_files = list(source_dir.rglob("*.kml"))
    kmz_files = list(source_dir.rglob("*.kmz"))
    boundary_dir = dirs["boundary"]
    selected_kml_path: Optional[Path] = None

    if kml_files:
        src_kml = kml_files[0]
        selected_kml_path = boundary_dir / f"{survey_id}.kml"
        shutil.copy2(src_kml, selected_kml_path)
        log_ok(
            logger, f"KML copied: {src_kml.name} → {selected_kml_path.name}")

    elif kmz_files:
        kmz_path = kmz_files[0]
        log_ok(logger, f"KMZ detected: {kmz_path.name} — extracting KML")

        with zipfile.ZipFile(kmz_path, "r") as zf:
            kml_members = [
                m for m in zf.namelist() if m.lower().endswith(".kml")]
            if not kml_members:
                raise ValueError("KMZ file does not contain any KML file.")

            with tempfile.TemporaryDirectory() as tmpdir:
                zf.extract(kml_members[0], path=tmpdir)
                extracted = Path(tmpdir) / kml_members[0]
                if not extracted.exists():
                    extracted = next(Path(tmpdir).rglob("*.kml"), None)
                if not extracted:
                    raise ValueError("Failed to extract KML from KMZ.")

                selected_kml_path = boundary_dir / f"{survey_id}.kml"
                shutil.copy2(extracted, selected_kml_path)

        log_ok(logger, f"KML extracted and saved: {selected_kml_path.name}")

    else:
        raise ValueError("No KML or KMZ file found in source directory.")

    log_step(logger, 6, "Audit ignored files")

    ignored_counts: Dict[str, int] = {}
    for file in source_dir.rglob("*"):
        if file.is_file():
            ext = file.suffix.lower()
            if ext not in {".kml", ".kmz"} and ext not in {e.lower() for e in image_extensions}:
                ignored_counts[ext] = ignored_counts.get(ext, 0) + 1

    if ignored_counts:
        log_warn(logger, f"Ignored file types: {ignored_counts}")
    else:
        log_ok(logger, "No unexpected file types found")

    log_step(logger, 7, "Write manifest")

    manifest = {
        "survey_id":     survey_id,
        "year":          year,
        "source_folder": str(source_dir),
        "created_at":    datetime.now().isoformat(),
        "image_count":   len(images),
        "kml_file":      f"{survey_id}.kml",
        "ignored_files": ignored_counts,
    }

    manifest_path = survey_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log_ok(logger, f"Manifest written: {manifest_path.name}")

    # ── Summary ───────────────────────────────────────────────────────────
    log_section(logger, "DATA SEGREGATION COMPLETE")
    logger.info(
        f"survey_id={survey_id} | images={len(images)} | "
        f"kml={selected_kml_path.name if selected_kml_path else '—'}"
    )

    return {
        "survey_id":   survey_id,
        "survey_path": str(survey_path),
        "image_count": len(images),
        "kml_file":    f"{survey_id}.kml",
        "manifest":    str(manifest_path),
        "dirs":        {k: str(v) for k, v in dirs.items()},
    }


run_data_segregation = run
