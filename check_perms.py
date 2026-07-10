from modules.webodm.webodm_processor import WebODMProcessor
from shared.config import load_pipeline_config
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check_perms")

config = load_pipeline_config()

processor = WebODMProcessor(
    url=config["webodm"]["url"],
    username=config["webodm"]["username"],
    password=config["webodm"]["password"],
    logger=logger,
)

resp = processor.session.get(f"{processor.base_url}/api/projects/363/", timeout=30)
resp.raise_for_status()
data = resp.json()
print("Project name:", data.get("name"))
print("Your permissions:", data.get("permissions"))