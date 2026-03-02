from __future__ import annotations
import os
import json
from pathlib import Path
from dotenv import load_dotenv


def load_pipeline_config() -> dict:
    """
    Load all pipeline configuration from .env once.
    Returns structured dictionary used by RGBPipeline.
    """

    load_dotenv()

    def read_json_env(name: str, default: dict) -> dict:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {name}: {e}")

    config = {
        "paths": {
            "surveys_root": Path(os.getenv("SURVEYS_ROOT")),
            "field_data_root": Path(os.getenv("FIELD_DATA_ROOT")),
            "upload_cache_root": os.getenv("UPLOAD_CACHE_ROOT"),
        },
        "cross_run_filter": {
            "max_gap": int(os.getenv("CROSSRUN_MAX_GAP", 10)),
            "window": int(os.getenv("CROSSRUN_WINDOW", 3)),
            "delete_raw_after_success": os.getenv("DELETE_RAW", "false").lower() == "true",
        },
        "webodm": {
            "url": os.getenv("WEBODM_URL"),
            "username": os.getenv("WEBODM_USERNAME"),
            "password": os.getenv("WEBODM_PASSWORD"),
            "node_id": int(os.getenv("WEBODM_NODE_ID", 0)), 
            "task1_options": read_json_env("WEBODM_TASK1_OPTIONS_JSON", {}),
            "task2_options": read_json_env("WEBODM_TASK2_OPTIONS_JSON", {}),
        }
    }

    # Basic validation
    if not config["webodm"]["url"]:
        raise ValueError("WEBODM_URL missing in .env")

    if not config["webodm"]["username"]:
        raise ValueError("WEBODM_USERNAME missing in .env")

    if not config["webodm"]["password"]:
        raise ValueError("WEBODM_PASSWORD missing in .env")
    
    if not config["webodm"]["node_id"]:
        raise ValueError("WEBODM_NODE_ID missing in .env")

    return config
