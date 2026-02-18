from __future__ import annotations

import time
from typing import Callable, Any, Optional, Dict
import logging

from shared import PipelineRepo


class StageRunner:
    """
    Wraps stage execution with DB + logging + resume support.

    Resume logic:
    - If latest stage for (survey_id, stage_name) is 'completed', skip by default
    - Optionally loads output_json into state
    - force=True will rerun even if completed
    """

    def __init__(self, repo: PipelineRepo, survey_id: str, logger: logging.Logger):
        self.repo = repo
        self.survey_id = survey_id
        self.logger = logger

    def run(
        self,
        stage_name: str,
        fn: Callable[[], Any],
        *,
        output_key: Optional[str] = None,
        state: Optional[Dict[str, Any]] = None,
        force: bool = False,
        load_output_on_skip: bool = True,
    ) -> Any:
        # ---- RESUME CHECK ----
        latest = self.repo.get_latest_stage(self.survey_id, stage_name)
        if not force and latest and latest.get("status") == "completed":
            self.logger.info(f"StageRunner: skip {stage_name} (already completed in DB)")

            if load_output_on_skip and state is not None and output_key:
                output = self.repo.get_latest_stage_output(self.survey_id, stage_name)
                if output is not None:
                    state[output_key] = output
                    self.logger.info(f"StageRunner: loaded saved output for {stage_name} into state['{output_key}']")
                else:
                    self.logger.info(f"StageRunner: no saved output_json for {stage_name} to load")

            # Return saved output if available, else None
            return self.repo.get_latest_stage_output(self.survey_id, stage_name)

        # ---- RUN STAGE ----
        self.logger.info(f"StageRunner: start {stage_name}")
        stage_id = self.repo.start_stage(self.survey_id, stage_name)
        start = time.perf_counter()

        try:
            result = fn()
            runtime = time.perf_counter() - start

            if state is not None and output_key:
                state[output_key] = result

            output_payload = result if isinstance(result, dict) else None

            self.repo.finish_stage(
                stage_id=stage_id,
                success=True,
                runtime_seconds=runtime,
                output=output_payload,
                error_message=None,
            )
            self.logger.info(f"StageRunner: done {stage_name} ({runtime:.2f}s)")
            return result

        except Exception as e:
            runtime = time.perf_counter() - start
            self.repo.finish_stage(
                stage_id=stage_id,
                success=False,
                runtime_seconds=runtime,
                output=None,
                error_message=str(e),
            )
            self.logger.exception(f"StageRunner: failed {stage_name} ({runtime:.2f}s)")
            raise
