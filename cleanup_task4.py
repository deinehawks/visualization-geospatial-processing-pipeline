from modules.webodm.webodm_processor import WebODMProcessor
from shared.config import load_pipeline_config
import logging

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