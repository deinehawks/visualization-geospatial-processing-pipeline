from pathlib import Path
from shared.config import load_pipeline_config
from pipelines.rgb_pipeline import RGBPipeline

# --------------------------------------------------
# Load centralized config
# --------------------------------------------------

config = load_pipeline_config()

# --------------------------------------------------
# Extract needed paths
# --------------------------------------------------

SURVEYS_ROOT = config["paths"]["surveys_root"]
FIELD_DATA_ROOT = config["paths"]["field_data_root"]

SOURCE_DIR = FIELD_DATA_ROOT / "BLC_A2S_2Ha_60m"
YEAR = 2026
BASE_DIR = Path(".")

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

print("\n===== FINAL RESULT =====")
print(result)
