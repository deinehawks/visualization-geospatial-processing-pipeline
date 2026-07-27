from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.filesystem_validation import (
    FILESYSTEM_VALIDATION_SENTINEL,
    filesystem_validation_payload,
    validate_publication_filesystem,
    write_filesystem_validation_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate publication filesystem primitives under a disposable root.",
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--validation-id", required=True)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--large-tree-files", type=int, default=0)
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep the disposable validation run directory for inspection.",
    )
    parser.add_argument(
        "--allow-destructive-validation",
        action="store_true",
        help=(
            "Required acknowledgement that validation creates, renames, replaces, "
            "and deletes disposable files under --root."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_publication_filesystem(
            args.root,
            validation_id=args.validation_id,
            allow_destructive_validation=args.allow_destructive_validation,
            keep_workdir=args.keep_workdir,
            large_tree_files=args.large_tree_files,
        )
        if args.report_path is not None:
            write_filesystem_validation_report(result, args.report_path)
        payload = filesystem_validation_payload(result)
        payload["sentinel_name"] = FILESYSTEM_VALIDATION_SENTINEL
        payload["report_path"] = str(args.report_path) if args.report_path else None
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if result.passed else 2
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "sentinel_name": FILESYSTEM_VALIDATION_SENTINEL,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
