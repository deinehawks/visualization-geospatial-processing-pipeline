import sys
from pathlib import Path
from shared.pipeline_control import PipelineControl
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("manual_pause")

if len(sys.argv) < 2:
    print("Usage: python pause_run.py <run_id> [abort]")
    sys.exit(1)

run_id = sys.argv[1]
action = sys.argv[2] if len(sys.argv) > 2 else "pause"

control = PipelineControl(Path("."), run_id)

if action == "abort":
    control.request_abort(logger)
else:
    control.request_pause(logger)