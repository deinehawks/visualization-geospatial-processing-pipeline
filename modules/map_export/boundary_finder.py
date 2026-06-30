from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


BOUNDARY_EXTENSIONS = {".kmz", ".kml"}

DATASET_FOLDER_PATTERN = re.compile(
    r"^[A-Za-z]{2,5}[-_]\d{3}[_-].+"
)

DATE_FOLDER_PATTERN = re.compile(r"^\d{8}$")


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
    survey_dir = _resolve_survey_dir(
        source_root=source_root,
        survey_name=survey_name,
    )

    candidates = [
        path
        for path in survey_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in BOUNDARY_EXTENSIONS
    ]

    if not candidates:
        found_extensions = sorted(
            {
                path.suffix.lower() or "[no extension]"
                for path in survey_dir.rglob("*")
                if path.is_file()
            }
        )

        raise FileNotFoundError(
            "Boundary file is missing for the resolved survey folder.\n"
            f"Survey name: {survey_name}\n"
            f"Resolved folder: {survey_dir}\n\n"
            "Expected at least one boundary file:\n"
            "- .kmz\n"
            "- .kml\n\n"
            "Action needed:\n"
            "Copy the mission boundary KMZ/KML into the survey folder, then rerun map export.\n\n"
            f"Detected file extensions: {found_extensions}"
        )

    # Prefer KMZ first, then KML.
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
    boundary_files: list[BoundaryFile] = []

    for survey_name in survey_names:
        boundary_files.append(
            find_boundary_file(source_root, survey_name)
        )

    return boundary_files


def _resolve_survey_dir(
    *,
    source_root: Path,
    survey_name: str,
) -> Path:
    source_root = Path(source_root)
    survey_name = survey_name.strip()

    if not source_root.exists():
        raise FileNotFoundError(f"Source root does not exist: {source_root}")

    if not source_root.is_dir():
        raise NotADirectoryError(f"Source root is not a folder: {source_root}")

    # Fast path: old behavior still works.
    direct = source_root / survey_name

    if direct.exists() and direct.is_dir():
        return direct

    candidates = _find_survey_candidates(
        search_root=source_root,
        survey_name=survey_name,
    )

    if not candidates:
        sample_folders = _sample_dataset_folders(source_root)

        hint = ""
        if sample_folders:
            hint = "\n\nSample detected dataset folders:\n" + "\n".join(
                f"  - {path}" for path in sample_folders[:10]
            )

        raise FileNotFoundError(
            "Survey folder was not found under source root.\n"
            f"Survey query: {survey_name}\n"
            f"Search root: {source_root}"
            f"{hint}"
        )

    candidates.sort(key=_survey_candidate_sort_key, reverse=True)

    selected = candidates[0]

    if len(candidates) > 1:
        print(
            "Warning: Multiple matching survey folders found. "
            "Using the latest/first match:\n"
            + "\n".join(f"  - {path}" for path in candidates[:10]),
            flush=True,
        )

    return selected


def _find_survey_candidates(
    *,
    search_root: Path,
    survey_name: str,
) -> list[Path]:
    target = _normalize_folder_name(survey_name)

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


def _normalize_folder_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _survey_candidate_sort_key(path: Path) -> tuple[str, str]:
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