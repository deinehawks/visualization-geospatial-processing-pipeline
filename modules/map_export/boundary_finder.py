from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BOUNDARY_EXTENSIONS = {".kmz", ".kml"}


@dataclass(frozen=True)
class BoundaryFile:
    survey_name: str
    survey_dir: Path
    boundary_path: Path


def parse_survey_list(raw: str) -> list[str]:
    surveys = [item.strip() for item in raw.split(",") if item.strip()]

    if not surveys:
        raise ValueError("No survey folders provided.")

    return surveys


def find_boundary_file(source_root: Path, survey_name: str) -> BoundaryFile:
    survey_dir = source_root / survey_name

    if not survey_dir.exists():
        raise FileNotFoundError(
            f"Survey folder not found: {survey_dir}"
        )

    if not survey_dir.is_dir():
        raise NotADirectoryError(
            f"Survey path is not a folder: {survey_dir}"
        )

    candidates = [
        path
        for path in survey_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in BOUNDARY_EXTENSIONS
    ]

    if not candidates:
        raise FileNotFoundError(
            f"No KMZ/KML boundary found inside survey folder: {survey_dir}"
        )

    # Prefer KMZ first, then KML, because field folders usually contain KMZ exports.
    candidates.sort(
        key=lambda p: (
            0 if p.suffix.lower() == ".kmz" else 1,
            len(p.parts),
            p.name.lower(),
        )
    )

    return BoundaryFile(
        survey_name=survey_name,
        survey_dir=survey_dir,
        boundary_path=candidates[0],
    )


def collect_boundary_files(
    *,
    source_root: Path,
    survey_names: list[str],
) -> list[BoundaryFile]:
    return [
        find_boundary_file(source_root, survey_name)
        for survey_name in survey_names
    ]