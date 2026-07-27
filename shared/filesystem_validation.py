from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FILESYSTEM_VALIDATION_SENTINEL = ".filesystem-validation-root"
FILESYSTEM_VALIDATION_RUNS_DIR = ".filesystem-validation-runs"


@dataclass(frozen=True)
class FilesystemValidationCheck:
    name: str
    status: str
    details: dict[str, object]
    error: str | None = None


@dataclass(frozen=True)
class FilesystemValidationResult:
    root: Path
    run_root: Path
    destructive: bool
    checks: tuple[FilesystemValidationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.status == "passed" for check in self.checks)


def validate_publication_filesystem(
    root: Path,
    *,
    validation_id: str | None = None,
    allow_destructive_validation: bool = False,
    keep_workdir: bool = False,
    sentinel_name: str = FILESYSTEM_VALIDATION_SENTINEL,
    replace_path: Callable[[Path, Path], object] = os.replace,
    rename_path: Callable[[Path, Path], object] = os.rename,
    remove_tree: Callable[[Path], object] = shutil.rmtree,
) -> FilesystemValidationResult:
    """Validate publication filesystem primitives under an explicit disposable root.

    The validator creates, renames, replaces, and deletes tiny disposable files
    beneath ``root/.filesystem-validation-runs/<validation-id>``. It refuses to
    run unless the caller provides explicit destructive authorization and the
    root contains the validation sentinel.
    """

    resolved_root = Path(root).resolve(strict=False)
    _reject_filesystem_root(resolved_root)
    if not allow_destructive_validation:
        raise ValueError("filesystem validation requires allow_destructive_validation=True")
    sentinel_component = _safe_path_component(sentinel_name, "sentinel_name")
    sentinel = resolved_root / sentinel_component
    if not sentinel.is_file():
        raise ValueError(f"filesystem validation root is missing sentinel {sentinel}")

    run_id = _safe_path_component(validation_id or str(int(time.time() * 1000)), "validation_id")
    run_root = resolved_root / FILESYSTEM_VALIDATION_RUNS_DIR / run_id
    _require_within(run_root, resolved_root)
    if run_root.exists():
        raise FileExistsError(f"validation run root already exists: {run_root}")
    run_root.mkdir(parents=True)

    checks: list[FilesystemValidationCheck] = []
    try:
        checks.append(_check_exclusive_create(run_root))
        checks.append(_check_file_replace(run_root, replace_path=replace_path))
        checks.append(_check_directory_rename(run_root, rename_path=rename_path))
        checks.append(_check_json_visibility(run_root))
    finally:
        if not keep_workdir:
            remove_tree(run_root)

    return FilesystemValidationResult(
        root=resolved_root,
        run_root=run_root,
        destructive=True,
        checks=tuple(checks),
    )


def filesystem_validation_payload(result: FilesystemValidationResult) -> dict[str, object]:
    return {
        "status": "passed" if result.passed else "failed",
        "root": str(result.root),
        "run_root": str(result.run_root),
        "destructive": result.destructive,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "details": check.details,
                "error": check.error,
            }
            for check in result.checks
        ],
    }


def write_filesystem_validation_report(
    result: FilesystemValidationResult,
    report_path: Path,
) -> None:
    report = Path(report_path).resolve(strict=False)
    _require_within(report, result.root)
    report.parent.mkdir(parents=True, exist_ok=True)
    temporary = report.with_name(f".{report.name}.tmp")
    temporary.write_text(
        json.dumps(filesystem_validation_payload(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report)


def _check_exclusive_create(run_root: Path) -> FilesystemValidationCheck:
    lock_path = run_root / "exclusive.lock"
    details = {"path": str(lock_path)}
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("owner-1")
        try:
            second_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return FilesystemValidationCheck("exclusive_create", "passed", details)
        else:
            os.close(second_fd)
            return FilesystemValidationCheck(
                "exclusive_create",
                "failed",
                details,
                "second exclusive create unexpectedly succeeded",
            )
    except Exception as exc:
        return FilesystemValidationCheck(
            "exclusive_create",
            "failed",
            details,
            f"{type(exc).__name__}: {exc}",
        )


def _check_file_replace(
    run_root: Path,
    *,
    replace_path: Callable[[Path, Path], object],
) -> FilesystemValidationCheck:
    final = run_root / "publication.json"
    temporary = run_root / ".publication.json.tmp"
    details = {"final": str(final), "temporary": str(temporary)}
    try:
        final.write_text("old", encoding="utf-8")
        temporary.write_text("new", encoding="utf-8")
        replace_path(temporary, final)
        if final.read_text(encoding="utf-8") != "new":
            return FilesystemValidationCheck(
                "file_replace",
                "failed",
                details,
                "final file did not contain replacement content",
            )
        if temporary.exists():
            return FilesystemValidationCheck(
                "file_replace",
                "failed",
                details,
                "temporary file still exists after replace",
            )
        return FilesystemValidationCheck("file_replace", "passed", details)
    except Exception as exc:
        return FilesystemValidationCheck(
            "file_replace",
            "failed",
            details,
            f"{type(exc).__name__}: {exc}",
        )


def _check_directory_rename(
    run_root: Path,
    *,
    rename_path: Callable[[Path, Path], object],
) -> FilesystemValidationCheck:
    final = run_root / "round-corners"
    backup = run_root / ".previous" / "round-corners.validation"
    candidate = run_root / ".activation" / "validation" / "round-corners.tmp"
    details = {"final": str(final), "backup": str(backup), "candidate": str(candidate)}
    try:
        (final / "11" / "0").mkdir(parents=True)
        (final / "11" / "0" / "old.png").write_bytes(b"old")
        (candidate / "11" / "0").mkdir(parents=True)
        (candidate / "11" / "0" / "new.png").write_bytes(b"new")
        backup.parent.mkdir(parents=True)
        rename_path(final, backup)
        rename_path(candidate, final)
        if not (backup / "11" / "0" / "old.png").is_file():
            return FilesystemValidationCheck(
                "directory_rename",
                "failed",
                details,
                "backup directory did not contain prior content",
            )
        if not (final / "11" / "0" / "new.png").is_file():
            return FilesystemValidationCheck(
                "directory_rename",
                "failed",
                details,
                "final directory did not contain candidate content",
            )
        if candidate.exists():
            return FilesystemValidationCheck(
                "directory_rename",
                "failed",
                details,
                "candidate directory still exists after rename",
            )
        return FilesystemValidationCheck("directory_rename", "passed", details)
    except Exception as exc:
        return FilesystemValidationCheck(
            "directory_rename",
            "failed",
            details,
            f"{type(exc).__name__}: {exc}",
        )


def _check_json_visibility(run_root: Path) -> FilesystemValidationCheck:
    journal = run_root / "activation.json"
    details = {"journal": str(journal)}
    payload = {"status": "committed", "run_id": "validation"}
    try:
        journal.write_text(json.dumps(payload), encoding="utf-8")
        observed = json.loads(journal.read_text(encoding="utf-8"))
        if observed != payload:
            return FilesystemValidationCheck(
                "json_visibility",
                "failed",
                details,
                "read-after-write JSON content mismatch",
            )
        return FilesystemValidationCheck("json_visibility", "passed", details)
    except Exception as exc:
        return FilesystemValidationCheck(
            "json_visibility",
            "failed",
            details,
            f"{type(exc).__name__}: {exc}",
        )


def _safe_path_component(value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} is required")
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or value in {".", ".."}:
        raise ValueError(f"{field_name} must be a single safe path component")
    return value


def _reject_filesystem_root(path: Path) -> None:
    if path.parent == path:
        raise ValueError("validation root must not be a filesystem root")


def _require_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes validation root: {path}") from exc