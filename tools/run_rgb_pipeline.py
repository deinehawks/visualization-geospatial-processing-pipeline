from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence
import uuid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the RGB pipeline against explicitly supplied external paths.")
    parser.add_argument("--allow-external-run", action="store_true")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--surveys-root", type=Path, required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--base-dir", type=Path, default=Path("."))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.allow_external_run:
        raise SystemExit("Refusing to run the real pipeline without --allow-external-run.")

    from pipelines.rgb_pipeline import RGBPipeline
    from shared.config import load_pipeline_config

    config = load_pipeline_config()
    from operator_ui.storage import cli_execution_guard
    run_id = str(uuid.uuid4())
    # This low-level legacy launcher has no structured preflight. It must never
    # bypass admission, and two-run policy deliberately rejects it; use main.py.
    with cli_execution_guard(args.base_dir, run_id, exclusive=True):
        pipeline = RGBPipeline(base_dir=args.base_dir, config=config, source_dir=args.source_dir, surveys_root=args.surveys_root, year=args.year, run_id=run_id)
        result = pipeline.run(resume=False)
    print("\n===== FINAL RESULT =====")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
