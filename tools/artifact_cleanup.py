from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared.artifacts import (
    CleanupCandidate,
    CleanupExecutionResult,
    CleanupPlan,
    execute_artifact_cleanup,
    plan_artifact_cleanup,
    plan_published_survey_from_rgb_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or explicitly execute Phase 3 artifact cleanup.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Read cleanup candidates only.")
    _add_common_arguments(plan)

    execute = subparsers.add_parser(
        "execute",
        help="Delete unchanged cleanup candidates after explicit approval.",
    )
    _add_common_arguments(execute)
    execute.add_argument("--cleanup-id", required=True)
    execute.add_argument(
        "--allow-delete",
        action="store_true",
        help="Required acknowledgement that this command deletes planner-approved artifacts.",
    )
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--published-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--preserve-run-id", action="append", default=[])
    parser.add_argument("--min-age-seconds", type=float, default=0)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        published = plan_published_survey_from_rgb_path(args.published_root)
        plan = plan_artifact_cleanup(
            published=published,
            workspace_root=args.workspace_root,
            preserve_run_ids=args.preserve_run_id,
            min_age_seconds=args.min_age_seconds,
        )
        if args.command == "plan":
            print(json.dumps(_plan_payload(plan), indent=2, sort_keys=True))
            return 2 if plan.blocked_reasons else 0

        if not args.allow_delete:
            raise ValueError("execute requires --allow-delete")
        result = execute_artifact_cleanup(
            plan=plan,
            published=published,
            workspace_root=args.workspace_root,
            preserve_run_ids=args.preserve_run_id,
            min_age_seconds=args.min_age_seconds,
            allow_delete=True,
            cleanup_id=args.cleanup_id,
        )
        print(json.dumps(_execution_payload(result), indent=2, sort_keys=True))
        return 2 if result.blocked_reasons else 0
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2


def _plan_payload(plan: CleanupPlan) -> dict[str, object]:
    return {
        "status": "blocked" if plan.blocked_reasons else "planned",
        "candidates": [_candidate_payload(candidate) for candidate in plan.candidates],
        "protected_run_ids": list(plan.protected_run_ids),
        "blocked_reasons": list(plan.blocked_reasons),
        "warning": "Plan output is read-only. Use execute --allow-delete only after review.",
    }


def _execution_payload(result: CleanupExecutionResult) -> dict[str, object]:
    return {
        "status": "blocked" if result.blocked_reasons else "deleted",
        "dry_run": result.dry_run,
        "deleted": [
            {
                "path": str(deletion.path),
                "kind": deletion.kind,
                "run_id": deletion.run_id,
                "owner_root": str(deletion.owner_root),
            }
            for deletion in result.deleted
        ],
        "skipped": [_candidate_payload(candidate) for candidate in result.skipped],
        "blocked_reasons": list(result.blocked_reasons),
        "audit_path": str(result.audit_path) if result.audit_path else None,
    }


def _candidate_payload(candidate: CleanupCandidate) -> dict[str, object]:
    return {
        "path": str(candidate.path),
        "kind": candidate.kind,
        "reason": candidate.reason,
        "run_id": candidate.run_id,
        "owner_root": str(candidate.owner_root),
    }


if __name__ == "__main__":
    raise SystemExit(main())