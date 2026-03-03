from __future__ import annotations
from pathlib import Path
from shared.config import load_pipeline_config
from pipelines.rgb_pipeline import RGBPipeline
import argparse

def main():
    parser = argparse.ArgumentParser(description="Run RGB experiment")
    parser.add_argument("--survey", required=True, help="Survey folder name inside FIELD_DATA_ROOT")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    config = load_pipeline_config()

    # Use .env paths
    field_data_root = Path(config["paths"]["field_data_root"])
    surveys_root = Path(config["paths"]["surveys_root"])

    # Use .env node id
    node_id = int(config["webodm"].get("node_id") or 0)

    source_dir = field_data_root / args.survey
    if not source_dir.exists():
        raise FileNotFoundError(f"Survey folder not found: {source_dir}")

    print("\n===== EXPERIMENT SETTINGS =====")
    print(f"Source dir: {source_dir}")
    print(f"Outputs (SURVEYS_ROOT): {surveys_root}")
    print(f"WebODM Node ID: {node_id}")
    print("===============================\n")

    pipeline = RGBPipeline(
        base_dir=Path("."),
        config=config,
        source_dir=source_dir,
        surveys_root=surveys_root,
        year=args.year,
    )

    result = pipeline.run(resume=args.resume)
    print("\n===== FINAL RESULT =====")
    print(result)

if __name__ == "__main__":
    main()