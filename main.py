from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pipelines.rgb_pipeline import RGBPipeline
from shared.config import load_pipeline_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run production RGB pipeline")
    parser.add_argument("--survey", required=True, help="Survey folder inside FIELD_DATA_ROOT")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--survey-id", default=None, help="Optional fixed survey ID, e.g. AH-026002")
    parser.add_argument("--node-id", type=int, default=None)

    args = parser.parse_args()

    if args.resume and not args.run_id:
        parser.error("--run-id is required when using --resume")

    config: dict[str, Any] = load_pipeline_config()

    if args.node_id is not None:
        config["webodm"]["node_id"] = args.node_id

    field_data_root = Path(config["paths"]["field_data_root"])
    surveys_root = Path(config["paths"]["surveys_root"])
    source_dir = field_data_root / args.survey

    if not source_dir.exists():
        raise FileNotFoundError(f"Survey folder not found: {source_dir}")

    print("\n===== PRODUCTION RGB PIPELINE =====")
    print(f"Source dir   : {source_dir}")
    print(f"Surveys root : {surveys_root}")
    print(f"Year         : {args.year}")
    print(f"Survey ID    : {args.survey_id or 'auto-generate'}")
    print("WebODM mode  : Task 2 only")
    print("===================================\n")

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
        export_name_overrides={
            "task1": "",
            "task2": "",
        },

        # run Task 2 only
        skip_task1_webodm=True,
        skip_task2_webodm=False,
        task1_bounded=False,
        force_segregation=False,
    )

    result = pipeline.run(resume=args.resume)

    print("\n===== PIPELINE RESULT =====")
    print(f"Success : {result.get('success')}")
    print(f"Run ID  : {result.get('run_id')}")
    print(f"Error   : {result.get('error', '—')}")
    print("===========================\n")


if __name__ == "__main__":
    main()