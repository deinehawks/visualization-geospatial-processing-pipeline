from pathlib import Path
import os
from dotenv import load_dotenv

from pipelines.rgb_pipeline import RGBPipeline

# --------------------------------------------------
# Load environment variables
# --------------------------------------------------

load_dotenv()  # loads from project root .env

# --------------------------------------------------
# Read values from .env
# --------------------------------------------------

WEBODM_URL = os.getenv("WEBODM_URL")
WEBODM_USERNAME = os.getenv("WEBODM_USERNAME")
WEBODM_PASSWORD = os.getenv("WEBODM_PASSWORD")

SURVEYS_ROOT = Path(os.getenv("SURVEYS_ROOT"))
FIELD_DATA_ROOT = Path(os.getenv("FIELD_DATA_ROOT"))

SOURCE_DIR = FIELD_DATA_ROOT / "BLC_A2S_2Ha_60m"
YEAR = 2025
BASE_DIR = Path(".")

# --------------------------------------------------
# Config
# --------------------------------------------------

config = {
    "cross_run_filter": {
        "max_gap": 10,
        "window": 3,
    },
    "webodm": {
        "url": WEBODM_URL,
        "username": WEBODM_USERNAME,
        "password": WEBODM_PASSWORD,
        "task1_options": {},
        "task2_options": {},
    },
}

# --------------------------------------------------
# Run pipeline
# --------------------------------------------------

pipeline = RGBPipeline(
    base_dir=BASE_DIR,
    config=config,
    source_dir=SOURCE_DIR,
    surveys_root=SURVEYS_ROOT,
    year=YEAR,
)

result = pipeline.run(resume=False)
print(result)
