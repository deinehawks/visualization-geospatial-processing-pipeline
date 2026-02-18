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

import re
import shutil
import json
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import logging


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

    source_dir = Path(source_dir)
    surveys_root = Path(surveys_root)

    logger.info("Starting data segregation stage")

    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source_dir}")

    # --------------------------------------------------------
    # Generate survey ID
    # --------------------------------------------------------

    survey_id = generate_next_survey_id(surveys_root, year, logger)

    survey_path = surveys_root / str(year) / survey_id / "rgb"

    if survey_path.exists() and not force:
        raise FileExistsError(f"Survey path already exists: {survey_path}")

    logger.info(f"Creating survey directory: {survey_path}")

    # --------------------------------------------------------
    # Create standard folder structure
    # --------------------------------------------------------

    folders = [
        survey_path / "3d",
        survey_path / "boundary",
        survey_path / "dem" / "lidar",
        survey_path / "dem" / "odm",
        survey_path / "images" / "path_raw",
        survey_path / "images" / "path",
        survey_path / "images" / "cross-runs",
        survey_path / "object-detection",
        survey_path / "odm",
        survey_path / "ortho",
        survey_path / "qgis" / "clipped",
        survey_path / "tiles",
    ]

    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # Validate + Copy Images
    # --------------------------------------------------------

    image_extensions = (".jpg", ".jpeg", ".JPG", ".JPEG")

    images = [
        f for f in source_dir.rglob("*")
        if f.suffix in image_extensions
    ]

    if len(images) == 0:
        raise ValueError("No JPG/JPEG images found in source directory.")

    logger.info(f"Found {len(images)} images")

    path_raw_dir = survey_path / "images" / "path_raw"

    for img in images:
        shutil.copy2(img, path_raw_dir / img.name)

    logger.info("Images copied to path_raw")

    # --------------------------------------------------------
    # Validate + Copy KML
    # --------------------------------------------------------

    kml_files = list(source_dir.rglob("*.kml"))

    if not kml_files:
        raise ValueError("No KML file found in source directory.")

    if len(kml_files) > 1:
        logger.warning("Multiple KML files found — selecting first one.")

    selected_kml = kml_files[0]

    boundary_dir = survey_path / "boundary"
    shutil.copy2(selected_kml, boundary_dir / selected_kml.name)

    logger.info(f"KML copied: {selected_kml.name}")

    # --------------------------------------------------------
    # Count Ignored Files
    # --------------------------------------------------------

    ignored_counts = {}
    for file in source_dir.rglob("*"):
        if file.is_file():
            ext = file.suffix.lower()
            if ext not in image_extensions and ext != ".kml":
                ignored_counts[ext] = ignored_counts.get(ext, 0) + 1

    # --------------------------------------------------------
    # Write Manifest
    # --------------------------------------------------------

    manifest = {
        "survey_id": survey_id,
        "year": year,
        "source_folder": str(source_dir),
        "created_at": datetime.now().isoformat(),
        "image_count": len(images),
        "kml_file": selected_kml.name,
        "ignored_files": ignored_counts,
    }

    manifest_path = survey_path / "manifest.json"

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("Manifest written")

    # --------------------------------------------------------
    # Return Summary (StageRunner will store in DB)
    # --------------------------------------------------------

    summary = {
        "survey_id": survey_id,
        "survey_path": str(survey_path),
        "image_count": len(images),
        "kml_file": selected_kml.name,
        "manifest": str(manifest_path),
    }

    logger.info("Data segregation stage completed successfully")

    return summary
