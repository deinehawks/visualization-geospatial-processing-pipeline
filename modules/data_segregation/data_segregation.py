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
from shared.source_images import (
    JPEG_SUFFIXES,
    count_nonmatching_jpegs,
    discover_source_images,
    image_selection_mode,
    is_selected_source_image,
)

IMAGE_EXTENSIONS = JPEG_SUFFIXES
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
    date_hint: Optional[str] = None,
    uav_folder: Optional[str] = None,
) -> Path:
    """
    Resolve the actual dataset folder.

    Supports:
    - Direct full dataset path (absolute path that already exists)
    - Dataset folder name searched across all FIELD_DATA_ROOT entries

    FIELD_DATA_ROOT supports multiple roots via comma separation:
        FIELD_DATA_ROOT=Y:/field-data/2026/sorted, Z:/field-data-2026/sorted
    """
    source_dir = Path(str(source_dir).strip()).expanduser()
    selected_uav = _normalize_uav_folder_name(uav_folder)

    # Direct path — no search needed
    if source_dir.exists() and source_dir.is_dir():
        if _is_probable_dataset_folder(source_dir):
            validate_source_uav_folder(source_dir, selected_uav)
            log_ok(logger, f"Source dataset resolved directly: {source_dir}")
            return source_dir

        raise ValueError(
            "The provided source path exists, but its folder name does not match "
            "the expected dataset naming pattern:\n"
            f"{source_dir}\n\n"
            "Expected example:\n"
            "DNG_001_36.4Ha_M3C_70m_85f75s_6mps"
        )

    # Search across all configured roots
    field_data_roots = _get_field_data_roots()
    dataset_query = source_dir.name or str(source_dir)

    all_candidates: list[Path] = []
    missing_roots: list[Path] = []
    searched_roots: list[Path] = []

    for root in field_data_roots:
        if not root.exists():
            missing_roots.append(root)
            continue
        if not root.is_dir():
            missing_roots.append(root)
            continue

        searched_roots.append(root)
        log_step(logger, 0, f"Searching '{dataset_query}' in: {root}")

        candidates = _find_dataset_candidates(
            search_root=root,
            dataset_query=dataset_query,
            uav_folder=selected_uav,
        )
        all_candidates.extend(candidates)

    if missing_roots:
        logger.warning(
            f"Some FIELD_DATA_ROOT entries do not exist and were skipped:\n"
            + "\n".join(f"  - {r}" for r in missing_roots)
        )

    if not searched_roots:
        raise FileNotFoundError(
            "None of the configured FIELD_DATA_ROOT entries exist.\n"
            + "\n".join(f"  - {r}" for r in field_data_roots)
        )

    if not all_candidates:
        matching_elsewhere: list[Path] = []
        if selected_uav:
            for root in searched_roots:
                matching_elsewhere.extend(
                    _find_dataset_candidates(
                        search_root=root,
                        dataset_query=dataset_query,
                    )
                )

        sample_folders: list[Path] = []
        for root in searched_roots[:2]:  # sample from first two roots only
            sample_folders.extend(_sample_dataset_folders(root))
            if len(sample_folders) >= 10:
                break

        hint = ""
        if sample_folders:
            hint = "\n\nSample detected dataset folders:\n" + "\n".join(
                f"  - {path}" for path in sample_folders[:10]
            )

        roots_str = "\n".join(f"  - {r}" for r in searched_roots)
        uav_detail = (
            f"UAV folder filter: {selected_uav}\n"
            if selected_uav
            else ""
        )
        elsewhere_hint = ""
        if matching_elsewhere:
            elsewhere_hint = (
                "\n\nMatching dataset locations outside the requested UAV folder:\n"
                + "\n".join(f"  - {path}" for path in matching_elsewhere[:10])
            )
        raise FileNotFoundError(
            f"Dataset folder was not found.\n"
            f"Dataset query: {dataset_query}\n"
            f"{uav_detail}"
            f"Searched roots:\n{roots_str}"
            f"{elsewhere_hint or hint}"
        )

    all_candidates.sort(key=_dataset_candidate_sort_key, reverse=True)

    if len(all_candidates) > 1:
        if date_hint:
            matched = [c for c in all_candidates if date_hint in str(c)]
            if len(matched) == 1:
                selected = matched[0]
                log_ok(logger, f"Source dataset resolved via --date hint: {selected}")
                return selected
            elif len(matched) == 0:
                raise FileNotFoundError(
                    f"--date {date_hint!r} provided but no matching folder found.\n"
                    + "\n".join(f"  - {c}" for c in all_candidates)
                )
            # Multiple matches even with date hint — fall through to prompt

        print("\n[!] Multiple matching dataset folders found:")
        for i, path in enumerate(all_candidates[:10], 1):
            print(f"    {i}) {path}")
        print()

        while True:
            try:
                raw = input(
                    f"    Select folder [1–{len(all_candidates[:10])}] "
                    f"or press Ctrl+C to cancel: "
                ).strip()
            except (KeyboardInterrupt, EOFError):
                raise RuntimeError("Dataset folder selection cancelled by user.")
            if raw.isdigit() and 1 <= int(raw) <= len(all_candidates[:10]):
                selected = all_candidates[int(raw) - 1]
                break
            print(f"    Invalid input. Enter a number between 1 and {len(all_candidates[:10])}.")

        log_ok(logger, f"Source dataset selected by user: {selected}")
        return selected

    selected = all_candidates[0]
    log_ok(logger, f"Source dataset resolved: {selected}")
    return selected

def _get_field_data_roots() -> list[Path]:
    """
    Parse FIELD_DATA_ROOT from env — supports multiple roots via comma separation.

    Single root:
        FIELD_DATA_ROOT=Y:/field-data/2026/sorted

    Multiple roots:
        FIELD_DATA_ROOT=Y:/field-data/2026/sorted, Z:/field-data-2026/sorted
    """
    raw = os.getenv("FIELD_DATA_ROOT", "").strip().strip('"').strip("'")

    if not raw:
        raise ValueError(
            "FIELD_DATA_ROOT is missing. Set it in .env, for example:\n"
            "FIELD_DATA_ROOT=Y:/field-data/2026/sorted\n"
            "Or multiple roots:\n"
            "FIELD_DATA_ROOT=Y:/field-data/2026/sorted, Z:/field-data-2026/sorted"
        )

    roots = [Path(p.strip()).expanduser() for p in raw.split(",") if p.strip()]

    if not roots:
        raise ValueError("FIELD_DATA_ROOT is empty after parsing.")

    return roots

def _get_field_data_root() -> Path:
    """Returns the first configured FIELD_DATA_ROOT. Use _get_field_data_roots() for multi-root search."""
    return _get_field_data_roots()[0]


def _find_dataset_candidates(
    *,
    search_root: Path,
    dataset_query: str,
    uav_folder: Optional[str] = None,
) -> list[Path]:
    target = _normalize_folder_name(dataset_query)

    exact_matches: list[Path] = []
    partial_matches: list[Path] = []

    for path in search_root.rglob("*"):
        if not path.is_dir():
            continue

        if not _is_probable_dataset_folder(path):
            continue
        if uav_folder and not _is_beneath_named_folder(path, uav_folder):
            continue

        normalized_name = _normalize_folder_name(path.name)

        if normalized_name == target:
            exact_matches.append(path)
        elif target in normalized_name:
            partial_matches.append(path)

    return exact_matches or partial_matches


def _normalize_uav_folder_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    name = str(value).strip()
    if not name:
        raise ValueError("--uav requires a non-empty folder name.")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(
            "--uav must be one folder name, for example: --uav M3M_A"
        )
    return name


def _is_beneath_named_folder(path: Path, folder_name: str) -> bool:
    target = folder_name.casefold()
    return any(parent.name.casefold() == target for parent in path.parents)


def validate_source_uav_folder(
    source_dir: Path,
    uav_folder: Optional[str],
) -> Path:
    selected_uav = _normalize_uav_folder_name(uav_folder)
    source = Path(source_dir)
    if selected_uav and not _is_beneath_named_folder(source, selected_uav):
        raise ValueError(
            f"Selected dataset is not beneath UAV folder {selected_uav!r}: "
            f"{source}"
        )
    return source


def _validate_flattened_image_names(images: list[Path]) -> None:
    by_name: dict[str, list[Path]] = {}
    for image in images:
        by_name.setdefault(image.name.casefold(), []).append(image)

    conflicts = [paths for paths in by_name.values() if len(paths) > 1]
    if not conflicts:
        return

    details = "\n".join(
        f"  - {path}"
        for paths in conflicts[:10]
        for path in paths
    )
    raise ValueError(
        "Selected source images contain duplicate destination filenames. "
        "The flat images/raw output would overwrite data:\n"
        f"{details}"
    )


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
    try:
        roots = _get_field_data_roots()
    except ValueError:
        roots = []

    for field_data_root in roots:
        try:
            relative = source_dir.relative_to(field_data_root)
            parts = relative.parts
            return {
                "source_date_folder":   parts[0] if len(parts) >= 1 else None,
                "source_client_folder": parts[1] if len(parts) >= 2 else None,
                "source_dataset_folder": source_dir.name,
            }
        except ValueError:
            continue

    return {
        "source_date_folder": None,
        "source_client_folder": None,
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
    rgb_only: bool = False,
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

    source_files = [path for path in source_dir.rglob("*") if path.is_file()]
    images = discover_source_images(source_dir, rgb_only=rgb_only)

    if not images:
        expected = "images ending in _D.JPG" if rgb_only else "JPG/JPEG images"
        raise ValueError(f"No {expected} found in source directory.")

    _validate_flattened_image_names(images)
    excluded_nonmatching_jpegs = count_nonmatching_jpegs(
        source_files,
        rgb_only=rgb_only,
    )

    total_bytes = sum(p.stat().st_size for p in images)
    gb = total_bytes / (1024 ** 3)
    log_ok(logger, f"Found {len(images)} images ({gb:.2f} GB)")

    log_step(logger, 4, "Copy images → raw")

    raw_dir = dirs["raw"]
    total = len(images)
    t0 = time.perf_counter()

    for idx, img in enumerate(images, 1):
            dst = raw_dir / img.name
            # Per-file retry handles transient SMB/network glitches
            # (WinError 2, WinError 64) without restarting the whole stage.
            for attempt in range(1, 4):
                try:
                    shutil.copy2(img, dst)
                    break
                except OSError as e:
                    if attempt == 3:
                        raise RuntimeError(
                            f"Failed to copy image after 3 attempts: {img}\n"
                            f"Destination: {dst}\n"
                            f"Error: {e}\n\n"
                            "Check that the source network drive (Y:\\) is still "
                            "connected and the file is accessible."
                        ) from e
                    logger.warning(
                        f"Copy failed for {img.name} (attempt {attempt}/3): {e} "
                        f"— retrying in 3s"
                    )
                    time.sleep(3)

    log_progress_done(logger, "Copying images", total=total,
                      elapsed=time.perf_counter() - t0)

    log_step(logger, 5, "Locate and copy boundary file (KML/KMZ)")

    kml_files = [f for f in source_files if f.suffix.lower() == ".kml"]

    kmz_files = [f for f in source_files if f.suffix.lower() == ".kmz"]

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
        found_files = sorted(
            {
                file.suffix.lower() or "[no extension]"
                for file in source_files
            }
        )
        raise ValueError(
            "Boundary file is missing for the resolved dataset.\n"
            f"Resolved dataset folder: {source_dir}\n\n"
            "Expected at least one boundary file:\n"
            "- .kml\n"
            "- .kmz\n\n"
            "Action needed:\n"
            "Copy the mission boundary KML/KMZ into the dataset folder, then rerun the pipeline.\n\n"
            f"Detected file extensions in this dataset: {found_files}"
        )

    log_step(logger, 6, "Audit ignored files")

    ignored_counts: Dict[str, int] = {}
    for file in source_files:
        ext = file.suffix.lower()
        if ext in BOUNDARY_EXTENSIONS:
            continue
        if is_selected_source_image(file, rgb_only=rgb_only):
            continue
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
        "image_selection_mode": image_selection_mode(rgb_only=rgb_only),
        "excluded_nonmatching_jpeg_count": excluded_nonmatching_jpegs,
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
        "image_selection_mode": image_selection_mode(rgb_only=rgb_only),
        "excluded_nonmatching_jpeg_count": excluded_nonmatching_jpegs,
        "kml_file":    f"{survey_id}.kml",
        "manifest":    str(manifest_path),
        "dirs":        {k: str(v) for k, v in dirs.items()},
    }


run_data_segregation = run
