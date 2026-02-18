from __future__ import annotations

import time
from typing import Callable, Dict, Any, Optional
import logging


class StageRunner:
    """
    Wraps stage execution with:
    - logging
    - SQLite stage tracking (start/finish + runtime + output_json + error_message)
    """

    def __init__(self, survey_id: str, repo, logger: logging.Logger):
        """
        repo should be shared.db.PipelineRepo (or compatible interface):
          - start_stage(survey_id, stage_name) -> stage_id
          - finish_stage(stage_id, success, runtime_seconds, output=None, error_message=None)
        """
        self.survey_id = survey_id
        self.repo = repo
        self.logger = logger

    def run(
        self,
        stage_name: str,
        fn: Callable[[], Dict[str, Any]],
        *,
        store_output: bool = True,
    ) -> Dict[str, Any]:
        self.logger.info(f"Starting stage: {stage_name}")

        stage_id = self.repo.start_stage(self.survey_id, stage_name)
        start = time.time()

        try:
            output = fn() or {}
            runtime = time.time() - start

            self.repo.finish_stage(
                stage_id=stage_id,
                success=True,
                runtime_seconds=runtime,
                output=output if store_output else None,
                error_message=None,
            )

            self.logger.info(f"Completed stage: {stage_name} ({runtime:.2f}s)")
            return output

        except Exception as e:
            runtime = time.time() - start
            msg = str(e)

            self.repo.finish_stage(
                stage_id=stage_id,
                success=False,
                runtime_seconds=runtime,
                output=None,
                error_message=msg,
            )

            self.logger.exception(f"Failed stage: {stage_name} ({runtime:.2f}s)")
            raise
