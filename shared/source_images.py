from __future__ import annotations

from pathlib import Path
from typing import Iterable


JPEG_SUFFIXES = frozenset({".jpg", ".jpeg"})
ALL_JPEG_SELECTION_MODE = "all_jpeg"
DJI_RGB_SELECTION_MODE = "dji_rgb_d_jpg"


def image_selection_mode(*, rgb_only: bool) -> str:
    return DJI_RGB_SELECTION_MODE if rgb_only else ALL_JPEG_SELECTION_MODE


def is_selected_source_image(path: Path, *, rgb_only: bool) -> bool:
    candidate = Path(path)
    if rgb_only:
        return candidate.name.casefold().endswith("_d.jpg")
    return candidate.suffix.casefold() in JPEG_SUFFIXES


def discover_source_images(
    source_dir: Path,
    *,
    rgb_only: bool = False,
) -> list[Path]:
    source = Path(source_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source}")

    return sorted(
        (
            path
            for path in source.rglob("*")
            if path.is_file()
            and is_selected_source_image(path, rgb_only=rgb_only)
        ),
        key=lambda path: str(path).casefold(),
    )


def count_nonmatching_jpegs(
    files: Iterable[Path],
    *,
    rgb_only: bool,
) -> int:
    if not rgb_only:
        return 0
    return sum(
        1
        for path in files
        if Path(path).suffix.casefold() in JPEG_SUFFIXES
        and not is_selected_source_image(Path(path), rgb_only=True)
    )
