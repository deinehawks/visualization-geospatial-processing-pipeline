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
import os
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

IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
BOUNDARY_EXTENSIONS = {".kml", ".kmz"}

DATASET_FOLDER_PATTERN = re.compile(
    r"^[A-Za-z]{2,5}[-_]\d{3}[_-].+"
)

DATE_FOLDER_PATTERN = re.compile(r"^\d{8}$")

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

def resolve_source_dataset_dir(
    source_dir: Path,
    logger: logging.Logger,
) -> Path:
    """
    Resolve the actual dataset folder.

    Supports:
    - Direct full dataset path
    - Dataset folder name searched under FIELD_DATA_ROOT

    Example FIELD_DATA_ROOT:
    Z:/field-data-2026/sorted

    Example dataset:
    Z:/field-data-2026/sorted/20260318/BARBCO/DNG_001_36.4Ha_M3C_70m_85f75s_6mps
    """

    source_dir = Path(str(source_dir).strip()).expanduser()

    if source_dir.exists() and source_dir.is_dir():
        if _is_probable_dataset_folder(source_dir) and _has_required_dataset_inputs(source_dir):
            log_ok(logger, f"Source dataset resolved directly: {source_dir}")
            return source_dir

        raise ValueError(
            "The provided source path exists, but it does not look like a dataset folder:\n"
            f"{source_dir}\n\n"
            "Pass the actual dataset folder name instead, for example:\n"
            "DNG_001_36.4Ha_M3C_70m_85f75s_6mps"
        )

    field_data_root = _get_field_data_root()

    if not field_data_root.exists():
        raise FileNotFoundError(f"FIELD_DATA_ROOT does not exist: {field_data_root}")

    if not field_data_root.is_dir():
        raise NotADirectoryError(f"FIELD_DATA_ROOT is not a folder: {field_data_root}")

    dataset_query = source_dir.name or str(source_dir)

    log_step(logger, 0, f"Searching dataset under FIELD_DATA_ROOT: {dataset_query}")

    candidates = _find_dataset_candidates(
        search_root=field_data_root,
        dataset_query=dataset_query,
    )

    valid_candidates = [
        candidate
        for candidate in candidates
        if _has_required_dataset_inputs(candidate)
    ]

    if not valid_candidates:
        sample_folders = _sample_dataset_folders(field_data_root)

        hint = ""
        if sample_folders:
            hint = "\n\nSample detected dataset folders:\n" + "\n".join(
                f"  - {path}" for path in sample_folders[:10]
            )

        raise FileNotFoundError(
            "Dataset folder was not found or does not contain both images and KML/KMZ.\n"
            f"Dataset query: {dataset_query}\n"
            f"Search root: {field_data_root}"
            f"{hint}"
        )

    valid_candidates.sort(key=_dataset_candidate_sort_key, reverse=True)

    selected = valid_candidates[0]

    if len(valid_candidates) > 1:
        log_warn(
            logger,
            "Multiple matching dataset folders found. Using the latest/first match:\n"
            + "\n".join(f"  - {path}" for path in valid_candidates[:10]),
        )

    log_ok(logger, f"Source dataset resolved: {selected}")

    return selected


def _get_field_data_root() -> Path:
    raw = os.getenv("FIELD_DATA_ROOT", "").strip().strip('"').strip("'")

    if not raw:
        raise ValueError(
            "FIELD_DATA_ROOT is missing. Set it in .env, for example:\n"
            "FIELD_DATA_ROOT=Z:/field-data-2026/sorted"
        )

    return Path(raw).expanduser()


def _find_dataset_candidates(
    *,
    search_root: Path,
    dataset_query: str,
) -> list[Path]:
    target = _normalize_folder_name(dataset_query)

    exact_matches: list[Path] = []
    partial_matches: list[Path] = []

    for path in search_root.rglob("*"):
        if not path.is_dir():
            continue

        if not _is_probable_dataset_folder(path):
            continue

        normalized_name = _normalize_folder_name(path.name)

        if normalized_name == target:
            exact_matches.append(path)
        elif target in normalized_name:
            partial_matches.append(path)

    return exact_matches or partial_matches


def _is_probable_dataset_folder(path: Path) -> bool:
    return bool(DATASET_FOLDER_PATTERN.match(path.name))


def _has_required_dataset_inputs(path: Path) -> bool:
    has_images = False
    has_boundary = False

    for file in path.rglob("*"):
        if not file.is_file():
            continue

        ext = file.suffix.lower()

        if ext in IMAGE_EXTENSIONS:
            has_images = True
        elif ext in BOUNDARY_EXTENSIONS:
            has_boundary = True

        if has_images and has_boundary:
            return True

    return False


def _normalize_folder_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _dataset_candidate_sort_key(path: Path) -> tuple[str, str]:
    date_folder = ""

    for part in path.parts:
        if DATE_FOLDER_PATTERN.fullmatch(part):
            date_folder = part

    return (
        date_folder,
        str(path).lower(),
    )


def _sample_dataset_folders(search_root: Path) -> list[Path]:
    samples: list[Path] = []

    for path in search_root.rglob("*"):
        if path.is_dir() and _is_probable_dataset_folder(path):
            samples.append(path)

        if len(samples) >= 10:
            break

    return samples


def _extract_source_context(source_dir: Path) -> dict[str, Any]:
    field_data_root = _get_field_data_root()

    try:
        relative = source_dir.relative_to(field_data_root)
    except ValueError:
        return {
            "source_date_folder": None,
            "source_client_folder": None,
            "source_dataset_folder": source_dir.name,
        }

    parts = relative.parts

    return {
        "source_date_folder": parts[0] if len(parts) >= 1 else None,
        "source_client_folder": parts[1] if len(parts) >= 2 else None,
        "source_dataset_folder": source_dir.name,
    }

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

    log_step(logger, 0, "Resolve source dataset folder")
    source_dir = resolve_source_dataset_dir(source_dir, logger)
    source_context = _extract_source_context(source_dir)

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
        "rgb_root":           survey_path,
        "boundary":           survey_path / "boundary",
        "raw":                survey_path / "images" / "raw",
        "path":               survey_path / "images" / "path",
        "cross_runs":         survey_path / "images" / "cross-runs",

        # "ortho":              survey_path / "ortho",
        # "odm":                survey_path / "odm",
        # "dem_odm":            survey_path / "dem" / "odm",
        # "dem_dtm":            survey_path / "dem" / "odm" / "dtm",
        # "dem_dsm":            survey_path / "dem" / "odm" / "dsm",
        # "3d":                 survey_path / "3d",

        # "task1_root":         survey_path / "task1",
        # "task1_ortho":        survey_path / "task1" / "ortho",
        # "task1_odm":          survey_path / "task1" / "odm",

        # "task2_root":         survey_path / "task2",
        # "task2_ortho":        survey_path / "task2" / "ortho",
        # "task2_odm":          survey_path / "task2" / "odm",
        # "task2_3d":           survey_path / "task2" / "3d",
        # "task2_dem_odm":      survey_path / "task2" / "dem" / "odm",
        # "task2_dem_dtm":      survey_path / "task2" / "dem" / "odm" / "dtm",
        # "task2_dem_dsm":      survey_path / "task2" / "dem" / "odm" / "dsm",

        "qgis_root":          survey_path / "qgis",
        "qgis_clipped":       survey_path / "qgis" / "clipped",
        "qgis_clipped_ortho": survey_path / "qgis" / "clipped" / "ortho",
        "tiles_root":         survey_path / "tiles",
        "tiles_ortho":        survey_path / "tiles" / "ortho",
        "tiles_ortho_sharp":  survey_path / "tiles" / "ortho" / "sharp-corners",
        "tiles_ortho_round":  survey_path / "tiles" / "ortho" / "round-corners",
    }

    extra_folders = [
        survey_path / "dem" / "lidar",
        survey_path / "object-detection",
    ]

    for p in list(dirs.values()) + extra_folders:
        p.mkdir(parents=True, exist_ok=True)

    log_ok(logger, f"Folder structure created: {survey_path}")

    log_step(logger, 3, "Discover source images")

    images = [
        f
        for f in source_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    ]

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

    kml_files = [
        f for f in source_dir.rglob("*")
        if f.is_file() and f.suffix.lower() == ".kml"
    ]

    kmz_files = [
        f for f in source_dir.rglob("*")
        if f.is_file() and f.suffix.lower() == ".kmz"
    ]

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
            if ext not in BOUNDARY_EXTENSIONS and ext not in IMAGE_EXTENSIONS:
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
        "source_context": source_context,
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
