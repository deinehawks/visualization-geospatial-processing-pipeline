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
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

import re
import shutil
import json
import logging
import zipfile
import tempfile
import time

# ============================================================
# SURVEY ID GENERATOR
# ============================================================

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
            number = int(match.group(1))
            max_number = max(max_number, number)

    next_number = max_number + 1
    survey_id = f"{prefix}{next_number:03d}"

    logger.info(f"Generated new survey ID: {survey_id}")
    return survey_id


# ============================================================
# MAIN RUN FUNCTION
# ============================================================

def run(
    source_dir: Path,
    surveys_root: Path,
    year: int,
    logger: logging.Logger,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    # ... keep everything you already have above ...

    survey_id = generate_next_survey_id(surveys_root, year, logger)
    survey_path = surveys_root / str(year) / survey_id / "rgb"

    if survey_path.exists() and not force:
        raise FileExistsError(f"Survey path already exists: {survey_path}")

    logger.info(f"Creating survey directory: {survey_path}")

    # --------------------------------------------------------
    # Create standard folder structure
    # --------------------------------------------------------
    dirs = {
        "rgb_root": survey_path,
        "boundary": survey_path / "boundary",

        # images
        "raw": survey_path / "images" / "raw",
        "path": survey_path / "images" / "path",
        "cross_runs": survey_path / "images" / "cross-runs",

        # Ortho
        "ortho": survey_path / "ortho",

        #DEM
        "odm": survey_path / "odm",
        "dem_odm": survey_path / "dem" / "odm",
        "dem_dtm": survey_path / "dem" / "odm" / "dtm",
        "dem_dsm": survey_path / "dem" / "odm" / "dsm",

        # 3D
        "3d": survey_path / "3d",
    }

    extra_folders = [
        survey_path / "3d",
        survey_path / "dem" / "lidar",
        survey_path / "object-detection",
        survey_path / "qgis" / "clipped",
        survey_path / "tiles",
    ]

    for p in list(dirs.values()) + extra_folders:
        p.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # Validate + Copy Images
    # --------------------------------------------------------
    image_extensions = (".jpg", ".jpeg", ".JPG", ".JPEG")
    images = [f for f in source_dir.rglob("*") if f.suffix in image_extensions]

    if len(images) == 0:
        raise ValueError("No JPG/JPEG images found in source directory.")

    logger.info(f"Found {len(images)} images")

    # Use images/raw as canonical input folder
    raw_dir = dirs["raw"]

    logger.info(f"Copying images -> {raw_dir}")

    copied = 0
    total = len(images)
    t0 = time.perf_counter()

    for img in images:
        shutil.copy2(img, raw_dir / img.name)
        copied += 1

        if copied % 50 == 0 or copied == total:
            elapsed = time.perf_counter() - t0
            msg = f"Copying images: {copied}/{total} | elapsed={elapsed:.1f}s"
            print(msg.ljust(80), end="\r", flush=True)  # one-line update

    # finish the line cleanly
    print("".ljust(80), end="\r", flush=True)

    elapsed = time.perf_counter() - t0
    logger.info(f"Images copied to raw | total={total} | time={elapsed:.1f}s")
    
    # (optional) also mirror to images/raw if you still want it
    # raw_dir = dirs["raw"]
    # for img in images:
    #     shutil.copy2(img, raw_dir / img.name)

    # --------------------------------------------------------
    # Validate + Copy KML/KMZ -> boundary/<survey_id>.kml
    # --------------------------------------------------------
    kml_files = list(source_dir.rglob("*.kml"))
    kmz_files = list(source_dir.rglob("*.kmz"))

    boundary_dir = dirs["boundary"]
    selected_kml_path: Optional[Path] = None

    if kml_files:
        src_kml = kml_files[0]
        selected_kml_path = boundary_dir / f"{survey_id}.kml"
        shutil.copy2(src_kml, selected_kml_path)

    elif kmz_files:
        kmz_path = kmz_files[0]
        logger.info(f"KMZ detected: {kmz_path.name} — extracting KML")

        with zipfile.ZipFile(kmz_path, "r") as zf:
            kml_members = [m for m in zf.namelist() if m.lower().endswith(".kml")]
            if not kml_members:
                raise ValueError("KMZ file does not contain any KML file.")

            member = kml_members[0]

            with tempfile.TemporaryDirectory() as tmpdir:
                zf.extract(member, path=tmpdir)
                extracted_path = Path(tmpdir) / member

                if not extracted_path.exists():
                    extracted_path = next(Path(tmpdir).rglob("*.kml"), None)

                if not extracted_path:
                    raise ValueError("Failed to extract KML from KMZ.")

                selected_kml_path = boundary_dir / f"{survey_id}.kml"
                shutil.copy2(extracted_path, selected_kml_path)

    else:
        raise ValueError("No KML or KMZ file found in source directory.")

    logger.info(f"KML saved as: {selected_kml_path.name}")

    # --------------------------------------------------------
    # Ignored Files
    # --------------------------------------------------------
    ignored_counts: Dict[str, int] = {}
    for file in source_dir.rglob("*"):
        if file.is_file():
            ext = file.suffix.lower()
            if ext not in (".kml", ".kmz") and ext not in (e.lower() for e in image_extensions):
                ignored_counts[ext] = ignored_counts.get(ext, 0) + 1

    # --------------------------------------------------------
    # Manifest
    # --------------------------------------------------------
    manifest = {
        "survey_id": survey_id,
        "year": year,
        "source_folder": str(source_dir),
        "created_at": datetime.now().isoformat(),
        "image_count": len(images),
        "kml_file": f"{survey_id}.kml",
        "ignored_files": ignored_counts,
    }

    manifest_path = survey_path / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info("Manifest written")

    summary = {
        "survey_id": survey_id,
        "survey_path": str(survey_path),
        "image_count": len(images),
        "kml_file": f"{survey_id}.kml",
        "manifest": str(manifest_path),
        "dirs": {k: str(v) for k, v in dirs.items()},  
    }

    logger.info("Data segregation stage completed successfully")
    return summary

