from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Mapping

from shared.source_images import discover_source_images, image_selection_mode


RAW_RECOVERY_OWNERSHIP_NAME = ".raw-recovery.json"


def raw_input_recovery_confirmation(run_id: str) -> str:
    return f"RECOVER RAW FOR RUN {run_id}"


def validate_raw_input_recovery_request(
    request: Mapping[str, object],
    run_id: str,
) -> dict[str, str]:
    expected = raw_input_recovery_confirmation(run_id)
    if request.get("confirmation") != expected:
        raise ValueError(
            "Raw-input recovery requires exact confirmation: "
            f"{expected!r}"
        )
    return {"confirmation": expected}


def _inventory(files: list[Path]) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    collisions: list[tuple[Path, str]] = []
    for path in files:
        key = path.name.casefold()
        if key in result:
            collisions.append((path, result[key][0]))
            continue
        result[key] = (path.name, path.stat().st_size)
    if collisions:
        details = ", ".join(
            f"{previous!r} / {current.name!r}"
            for current, previous in collisions[:10]
        )
        raise ValueError(
            "Selected source images contain duplicate destination filenames: "
            f"{details}"
        )
    return result


def _direct_image_files(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.casefold() in {".jpg", ".jpeg"}
        ),
        key=lambda path: path.name.casefold(),
    )


def _inventory_matches(
    directory: Path,
    expected: Mapping[str, tuple[str, int]],
) -> bool:
    actual = _inventory(_direct_image_files(directory))
    return {
        key: size for key, (_name, size) in actual.items()
    } == {
        key: size for key, (_name, size) in expected.items()
    }


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _staging_directory(raw_dir: Path, run_id: str) -> Path:
    suffix = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return raw_dir.with_name(f".raw-recovery-{suffix}.tmp")


def recover_missing_raw_inputs(
    *,
    source_dir: Path,
    raw_dir: Path,
    run_id: str,
    rgb_only: bool = False,
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
) -> dict[str, object]:
    source = Path(source_dir).resolve(strict=True)
    target = Path(raw_dir).resolve(strict=False)
    if target.name.casefold() != "raw" or target.parent.name.casefold() != "images":
        raise ValueError(f"Raw-input recovery target must be images/raw: {target}")
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError(
            "Raw-input recovery requires an existing non-symlink images directory: "
            f"{target.parent}"
        )

    selected = discover_source_images(source, rgb_only=rgb_only)
    if not selected:
        expected = "images ending in _D.JPG" if rgb_only else "JPG/JPEG images"
        raise FileNotFoundError(
            f"No {expected} found for raw-input recovery: {source}"
        )
    expected_inventory = _inventory(selected)

    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise ValueError(
                f"Raw-input recovery target exists but is not a safe directory: {target}"
            )
        entries = list(target.iterdir())
        if entries and _inventory_matches(target, expected_inventory):
            unexpected = [
                entry
                for entry in entries
                if not (
                    entry.is_file()
                    and (
                        entry.suffix.casefold() in {".jpg", ".jpeg"}
                        or entry.name == RAW_RECOVERY_OWNERSHIP_NAME
                    )
                )
            ]
            if unexpected:
                raise ValueError(
                    "Raw-input recovery found unexpected existing content: "
                    f"{unexpected[0]}"
                )
            return {
                "status": "already_present",
                "changed": False,
                "source_dir": str(source),
                "raw_dir": str(target),
                "image_count": len(selected),
                "image_bytes": sum(size for _name, size in expected_inventory.values()),
                "image_selection_mode": image_selection_mode(rgb_only=rgb_only),
            }
        if entries:
            raise ValueError(
                "Refusing raw-input recovery because images/raw contains "
                "partial, mismatched, or unexpected content: "
                f"{target}"
            )

    staging = _staging_directory(target, run_id)
    ownership_path = staging / RAW_RECOVERY_OWNERSHIP_NAME
    ownership = {
        "event": "raw_input_recovery_staging",
        "run_id": run_id,
        "source_dir": str(source),
        "raw_dir": str(target),
        "image_selection_mode": image_selection_mode(rgb_only=rgb_only),
    }
    if staging.exists():
        if staging.is_symlink() or not staging.is_dir() or not ownership_path.is_file():
            raise ValueError(
                "Raw-input recovery staging exists without valid ownership: "
                f"{staging}"
            )
        existing_ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
        if existing_ownership != ownership:
            raise ValueError(
                "Raw-input recovery staging ownership does not match this request: "
                f"{staging}"
            )
    else:
        staging.mkdir()
        _write_json_atomic(ownership_path, ownership)

    allowed_names = {
        name.casefold() for name, _size in expected_inventory.values()
    } | {RAW_RECOVERY_OWNERSHIP_NAME.casefold()}
    for entry in staging.iterdir():
        if entry.name.casefold() not in allowed_names and not entry.name.endswith(".part"):
            raise ValueError(
                f"Raw-input recovery staging contains unexpected content: {entry}"
            )

    for source_image in selected:
        destination = staging / source_image.name
        expected_size = source_image.stat().st_size
        if destination.is_file() and destination.stat().st_size == expected_size:
            continue
        part = destination.with_name(f".{destination.name}.part")
        copy_file(source_image, part)
        if not part.is_file() or part.stat().st_size != expected_size:
            raise RuntimeError(
                "Raw-input recovery copy verification failed: "
                f"{source_image} -> {part}"
            )
        os.replace(part, destination)

    if not _inventory_matches(staging, expected_inventory):
        raise RuntimeError(
            f"Raw-input recovery staging verification failed: {staging}"
        )

    if target.exists():
        target.rmdir()
    os.replace(staging, target)
    return {
        "status": "recovered",
        "changed": True,
        "source_dir": str(source),
        "raw_dir": str(target),
        "image_count": len(selected),
        "image_bytes": sum(size for _name, size in expected_inventory.values()),
        "image_selection_mode": image_selection_mode(rgb_only=rgb_only),
    }
