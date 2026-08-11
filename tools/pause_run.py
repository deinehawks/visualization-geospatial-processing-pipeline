import sys
from pathlib import Path
import logging

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.pipeline_control import PipelineControl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("manual_pause")

if len(sys.argv) < 2:
    print("Usage: python pause_run.py <run_id> [abort]")
    sys.exit(1)

run_id = sys.argv[1]
action = sys.argv[2] if len(sys.argv) > 2 else "pause"

control = PipelineControl(REPO_ROOT, run_id)

if action == "abort":
    control.request_abort(logger)
else:
    control.request_pause(logger)
