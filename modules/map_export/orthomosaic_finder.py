from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .boundary_finder import _resolve_survey_dir

ORTHOMOSAIC_EXTENSIONS = {".tif", ".tiff"}

PREFERRED_ORTHO_SUBDIRS = [
    Path("rgb") / "qgis" / "clipped" / "ortho",
    Path("qgis") / "clipped" / "ortho",
    Path("clipped") / "ortho",
    Path("ortho"),
]


@dataclass(frozen=True)
class OrthomosaicFile:
    survey_name: str
    survey_dir: Path
    orthomosaic_path: Path


def find_orthomosaic_file(source_root: Path, survey_name: str) -> OrthomosaicFile:
    survey_dir = _resolve_survey_dir(
        source_root=source_root,
        survey_name=survey_name,
    )

    candidates: list[Path] = []

    for relative_dir in PREFERRED_ORTHO_SUBDIRS:
        search_dir = survey_dir / relative_dir

        if search_dir.exists() and search_dir.is_dir():
            candidates.extend(_find_rasters(search_dir))

    if not candidates:
        candidates.extend(_find_rasters(survey_dir))

    if not candidates:
        raise FileNotFoundError(
            f"No orthomosaic TIFF found inside survey folder: {survey_dir}"
        )

    candidates.sort(key=_orthomosaic_sort_key)

    return OrthomosaicFile(
        survey_name=survey_dir.name,
        survey_dir=survey_dir,
        orthomosaic_path=candidates[0],
    )


def collect_orthomosaic_files(
    *,
    source_root: Path,
    survey_names: list[str],
) -> list[OrthomosaicFile]:
    return [
        find_orthomosaic_file(source_root, survey_name)
        for survey_name in survey_names
    ]


def _find_rasters(folder: Path) -> list[Path]:
    return [
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in ORTHOMOSAIC_EXTENSIONS
    ]


def _orthomosaic_sort_key(path: Path) -> tuple[int, int, int, str]:
    name = path.name.lower()
    full_path = str(path).lower()

    # Prefer clipped orthomosaic output.
    preferred_dir_score = 0 if "qgis\\clipped\\ortho" in full_path or "qgis/clipped/ortho" in full_path else 1

    # Prefer orthomosaic-looking filenames.
    name_score = 0 if any(
        token in name
        for token in ("ortho", "orthomosaic", "orthophoto", "odm_orthophoto")
    ) else 1

    return (
        preferred_dir_score,
        name_score,
        len(path.parts),
        name,
    )