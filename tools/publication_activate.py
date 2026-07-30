from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared.artifacts import (
    PUBLICATION_MANIFEST_NAME,
    activate_publication_set_with_lock,
    plan_published_survey_from_rgb_path,
    plan_run_workspace,
)
from shared.publication_lock import PublicationLockError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicitly activate an already staged Phase 3 publication.",
    )
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--published-root", type=Path, required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument(
        "--allow-activation",
        action="store_true",
        help="Required acknowledgement that this command mutates published artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.allow_activation:
            raise ValueError("activation requires --allow-activation")

        workspace_root = args.workspace_root.resolve(strict=False)
        published_root = args.published_root.resolve(strict=False)
        staged_manifest_path = (
            workspace_root / "publish" / "staged" / PUBLICATION_MANIFEST_NAME
        )
        staged_manifest = _read_staged_manifest(staged_manifest_path)
        run_id = _required_text(staged_manifest, "run_id")
        survey_id = _required_text(staged_manifest, "survey_id")
        expected_confirmation = f"PUBLISH {survey_id} {run_id}"
        if args.confirmation != expected_confirmation:
            raise ValueError(
                f"confirmation must exactly match: {expected_confirmation}"
            )

        manifest_workspace_root = Path(
            _required_text(staged_manifest, "workspace_root")
        ).resolve(strict=False)
        if manifest_workspace_root != workspace_root:
            raise ValueError("staged manifest workspace_root does not match argument")

        manifest_published_root = Path(
            _required_text(staged_manifest, "published_root")
        ).resolve(strict=False)
        if manifest_published_root != published_root:
            raise ValueError("staged manifest published_root does not match argument")

        workspace = plan_run_workspace(workspace_root.parent, run_id)
        if workspace.root.resolve(strict=False) != workspace_root:
            raise ValueError("workspace-root must be the run workspace directory")
        published = plan_published_survey_from_rgb_path(published_root)

        publication_manifest = activate_publication_set_with_lock(
            workspace=workspace,
            published=published,
        )
        print(
            json.dumps(
                {
                    "status": "activated",
                    "run_id": run_id,
                    "survey_id": survey_id,
                    "workspace_root": str(workspace.root),
                    "published_root": str(published.root),
                    "publication_manifest": str(publication_manifest),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, PublicationLockError) as exc:
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


def _read_staged_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(f"staged publication manifest is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("staged publication manifest must contain a JSON object")
    if payload.get("status") != "staged":
        raise ValueError("staged publication manifest status must be 'staged'")
    return payload


def _required_text(payload: dict[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"staged publication manifest {field_name} is required")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
