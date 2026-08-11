from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from shared.config import load_pipeline_config
from shared.db.repo import PipelineRepo
from shared.paths import db_path
from modules.data_segregation.data_segregation import resolve_source_dataset_dir


def resolve_cli_source_dir(
    *,
    survey: str,
    resume: bool,
    run_id: str | None,
    logger: logging.Logger,
    date_hint: str | None = None,
    repository: PipelineRepo | None = None,
) -> Path:
    if resume:
        if not run_id:
            raise ValueError("run_id is required when resume=True")
        repo = repository or PipelineRepo(db_path(Path(".")))
        run = repo.get_run(run_id)
        if not run:
            raise ValueError(
                f"Cannot resume run {run_id!r}: no run record was found."
            )
        source_dir = run.get("source_dir")
        if not source_dir:
            raise ValueError(
                f"Cannot resume run {run_id!r}: run record has no source_dir."
            )
        resolved = Path(source_dir)
        logger.info("Resume source dataset restored from run state: %s", resolved)
        return resolved

    survey_arg = Path(survey)

    return resolve_source_dataset_dir(
        survey_arg,
        logger,
        date_hint=date_hint,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run production RGB pipeline")
    parser.add_argument("--survey", required=True, help="Survey folder inside FIELD_DATA_ROOT")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--survey-id", default=None, help="Optional fixed survey ID, e.g. AH-026002")
    parser.add_argument("--node-id", type=int, default=None)
    parser.add_argument(
        "--disable-cross-run",
        action="store_true",
        help="Disable the cross-run filter stage and copy raw images directly into path output.",
    )
    parser.add_argument("--force-stage", action="append", default=[], help="Force a completed stage to rerun during resume. Can be used multiple times.",)
    parser.add_argument(
        "--activate-publication",
        action="store_true",
        help="Activate publication after quality approval when paired with the exact confirmation phrase.",
    )
    parser.add_argument(
        "--publication-confirmation",
        default=None,
        help='Exact confirmation phrase required with --activate-publication, e.g. "PUBLISH AH-026019 run-001".',
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYYMMDD",
        help=(
            "Date folder to use when multiple matches exist for --survey "
            "(e.g. 20260414). Skips the interactive prompt."
        ),
    )

    # WebODM task mode: mutually exclusive flags
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument(
        "--task2",
        action="store_true",
        help="Run WebODM Task 2 only (3d , legacy mode)",
    )
    task_group.add_argument(
        "--task4",
        action="store_true",
        help="Run WebODM Task 4 only (bounded orthomosaic, default)",
    )
    task_group.add_argument(
        "--both-tasks",
        action="store_true",
        help="Run WebODM Task 4 then Task 2 (Orthomosaic + 3D).",
    )

    args = parser.parse_args()

    if args.resume and not args.run_id:
        parser.error("--run-id is required when using --resume")
    if args.activate_publication and not args.publication_confirmation:
        parser.error("--publication-confirmation is required when using --activate-publication")
    if args.publication_confirmation and not args.activate_publication:
        parser.error("--publication-confirmation requires --activate-publication")

    config: dict[str, Any] = load_pipeline_config()

    if args.node_id is not None:
        config["webodm"]["node_id"] = args.node_id

    surveys_root = Path(config["paths"]["surveys_root"])

    resolver_logger = logging.getLogger("rgb.source_resolver")

    source_dir = resolve_cli_source_dir(
        survey=args.survey,
        resume=args.resume,
        run_id=args.run_id,
        logger=resolver_logger,
        date_hint=args.date,
    )

    # Determine webodm_mode from CLI flags
    if args.task2:
        webodm_mode = "task2"
        mode_display = "Task 2 only (3d)"
    elif args.both_tasks:
        webodm_mode = "both"
        mode_display = "Orthomosaic + 3D (Task 4 then Task 2)"
    elif args.task4:
        webodm_mode = "task4"
        mode_display = "Task 4 only (bounded orthomosaic)"
    else:
        # Default to task4
        webodm_mode = "task4"
        mode_display = "Task 4 only (bounded orthomosaic, default)"

    print("\n===== PRODUCTION RGB PIPELINE =====")
    print(f"Source dir   : {source_dir}")
    print(f"Surveys root : {surveys_root}")
    print(f"Year         : {args.year}")
    print(f"Survey ID    : {args.survey_id or 'auto-generate'}")
    print(f"WebODM mode  : {mode_display}")
    print(f"Cross-run    : {'disabled' if args.disable_cross_run else 'enabled'}")
    print("===================================\n")

    from pipelines.rgb_pipeline import RGBPipeline

    pipeline = RGBPipeline(
        base_dir=Path("."),
        config=config,
        source_dir=source_dir,
        surveys_root=surveys_root,
        year=args.year,
        run_id=args.run_id,
        survey_id_override=args.survey_id,

        # production mode
        use_year_subdir_override=True,
        export_name_overrides=None, # using naming templates
        crossrun_enabled_override=False if args.disable_cross_run else None,

        # WebODM task mode
        skip_task1_webodm=True,
        webodm_mode=webodm_mode,
        task1_bounded=False,
        force_segregation=False,
    )

    result = pipeline.run(
        resume=args.resume,
        force_stages=set(args.force_stage or []),
        publication_confirmation=(
            args.publication_confirmation if args.activate_publication else None
        ),
    )

    print("\n===== PIPELINE RESULT =====")
    print(f"Success : {result.get('success')}")
    print(f"Run ID  : {result.get('run_id')}")
    print(f"Error   : {result.get('error', '—')}")
    print("===========================\n")


if __name__ == "__main__":
    main()

