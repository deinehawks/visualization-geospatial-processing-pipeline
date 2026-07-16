from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Sequence


SENTINEL_NAME = ".pipeline-reset-allowed"
SQLITE_NAMES = ("pipeline.db", "pipeline.db-wal", "pipeline.db-shm")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _require_within(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes approved target root: {path}")
    return resolved


def validate_target_root(target_root: Path) -> Path:
    root = target_root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Target root is not a directory: {root}")
    if root == Path(root.anchor):
        raise ValueError("Filesystem roots cannot be reset.")
    if root == REPOSITORY_ROOT or (root / ".git").exists():
        raise ValueError("Repository roots cannot be reset.")
    sentinel = _require_within(root / SENTINEL_NAME, root)
    if not sentinel.is_file():
        raise ValueError(f"Required sentinel is missing: {sentinel}")
    _require_within(root / "logs", root)
    for name in SQLITE_NAMES:
        _require_within(root / name, root)
    return root


def reset_environment(target_root: Path) -> list[Path]:
    root = validate_target_root(target_root)
    removed = []
    logs_dir = _require_within(root / "logs", root)
    if logs_dir.exists():
        if not logs_dir.is_dir():
            raise ValueError(f"Expected logs directory: {logs_dir}")
        shutil.rmtree(logs_dir)
        removed.append(logs_dir)
    for name in SQLITE_NAMES:
        database_file = _require_within(root / name, root)
        if database_file.exists():
            if not database_file.is_file():
                raise ValueError(f"Expected SQLite file: {database_file}")
            database_file.unlink()
            removed.append(database_file)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete pipeline logs and SQLite files from an approved target root.")
    parser.add_argument("target_root", type=Path)
    parser.add_argument("--allow-destructive-reset", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.allow_destructive_reset:
        raise SystemExit("Refusing to delete files without --allow-destructive-reset.")
    removed = reset_environment(args.target_root)
    for path in removed:
        print(f"Removed: {path}")
    print("Environment reset complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
