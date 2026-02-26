from __future__ import annotations

import argparse
from pathlib import Path

from shared.config import load_pipeline_config
from pipelines.rgb_pipeline import RGBPipeline


# --------------------------------------------------
# MAIN PC running script, but DATA + OUTPUTS live on worker via SMB drive
# --------------------------------------------------
WORKER_DRIVE = Path(r"Z:") 
FIELD_DATA_ROOT = WORKER_DRIVE / "field data"
SURVEYS_ROOT = WORKER_DRIVE / "surveys"

WEBODM_NODE_ID = 2 


def force_webodm_node2():
    """
    Temporary monkey patch:
    Injects processing_node=WEBODM_NODE_ID into ONLY the task-create request.
    """
    from modules import WebODMProcessor

    original_create = WebODMProcessor.create_task_with_images

    def patched_create_task_with_images(self, project_id, name, image_folder, options=None):
        original_post = self.session.post

        def patched_post(url, *args, **kwargs):
            if f"/api/projects/{project_id}/tasks/" in url:
                data = kwargs.get("data") or {}
                if not isinstance(data, dict):
                    data = dict(data)
                data["processing_node"] = str(WEBODM_NODE_ID)
                kwargs["data"] = data
            return original_post(url, *args, **kwargs)

        self.session.post = patched_post
        try:
            return original_create(self, project_id, name, image_folder, options)
        finally:
            self.session.post = original_post

    WebODMProcessor.create_task_with_images = patched_create_task_with_images


def main():
    parser = argparse.ArgumentParser(description="Run RGB experiment from MAIN PC using worker SMB paths.")
    parser.add_argument("--survey", required=True, help="Survey folder name inside FIELD_DATA_ROOT")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    config = load_pipeline_config()
    force_webodm_node2()

    source_dir = FIELD_DATA_ROOT / args.survey
    if not source_dir.exists():
        raise FileNotFoundError(f"Survey folder not found: {source_dir}")

    print("\n===== EXPERIMENT SETTINGS =====")
    print(f"Running on: MAIN PC")
    print(f"Worker paths via SMB drive: {WORKER_DRIVE}")
    print(f"Source dir: {source_dir}")
    print(f"Outputs (SURVEYS_ROOT): {SURVEYS_ROOT}")
    print(f"Forced Node ID: {WEBODM_NODE_ID}")
    print("===============================\n")

    pipeline = RGBPipeline(
        base_dir=Path("."),
        config=config,
        source_dir=source_dir,
        surveys_root=SURVEYS_ROOT,
        year=args.year,
    )

    result = pipeline.run(resume=args.resume)

    print("\n===== FINAL RESULT =====")
    print(result)


if __name__ == "__main__":
    main()