import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.webodm.webodm_processor import WebODMProcessor
from shared.config import load_pipeline_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cleanup")

config = load_pipeline_config()

processor = WebODMProcessor(
    url=config["webodm"]["url"],
    username=config["webodm"]["username"],
    password=config["webodm"]["password"],
    logger=logger,
)

processor.delete_task(363, "061fbc4d-8bf3-4e00-a8fb-7f910c3fc6eb")
print("Task4 deleted.")
