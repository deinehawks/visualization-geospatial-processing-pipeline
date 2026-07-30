from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared.publication_lock import (
    PublicationLockError,
    diagnose_publication_lock,
    recover_publication_lock,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose or explicitly recover a survey publication lock.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    diagnose = subparsers.add_parser("diagnose", help="Read lock evidence only.")
    diagnose.add_argument("--published-root", type=Path, required=True)

    recover = subparsers.add_parser(
        "recover",
        help="Archive an unchanged diagnosed lock after explicit approval.",
    )
    recover.add_argument("--published-root", type=Path, required=True)
    recover.add_argument("--expected-digest", required=True)
    recover.add_argument("--confirmation", required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument("--recovery-id")
    recover.add_argument(
        "--allow-recovery",
        action="store_true",
        help="Required acknowledgement that this operation unlocks publication.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        diagnosis = diagnose_publication_lock(args.published_root)
        if args.command == "diagnose":
            print(json.dumps(_diagnosis_payload(diagnosis), indent=2, sort_keys=True))
            return 0

        if not args.allow_recovery:
            raise ValueError("recover requires --allow-recovery")
        if args.expected_digest != diagnosis.raw_sha256:
            raise ValueError("lock digest changed or does not match diagnosis")
        recovery = recover_publication_lock(
            diagnosis=diagnosis,
            confirmation=args.confirmation,
            reason=args.reason,
            recovery_id=args.recovery_id,
        )
        print(
            json.dumps(
                {
                    "status": "recovered",
                    "recovery_id": recovery.recovery_id,
                    "evidence_path": str(recovery.evidence_path),
                    "record_path": str(recovery.record_path),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (PublicationLockError, OSError, ValueError) as exc:
        parser_error = {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(parser_error, indent=2, sort_keys=True))
        return 2


def _diagnosis_payload(diagnosis) -> dict[str, object]:
    lock = diagnosis.lock
    return {
        "status": diagnosis.status,
        "published_root": str(diagnosis.published_root),
        "lock_path": str(diagnosis.path),
        "lock_sha256": diagnosis.raw_sha256,
        "observed_at": diagnosis.observed_at,
        "created_at_valid": diagnosis.created_at_valid,
        "age_seconds": diagnosis.age_seconds,
        "format_error": diagnosis.format_error,
        "run_id": lock.run_id if lock else None,
        "survey_id": lock.survey_id if lock else None,
        "created_at": lock.created_at if lock else None,
        "recovery_confirmation": diagnosis.recovery_confirmation,
        "warning": (
            "Age is diagnostic only. Verify the owner is no longer active before "
            "using the explicit recover command."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
