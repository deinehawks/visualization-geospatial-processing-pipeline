from __future__ import annotations

import time
from typing import Callable, Any, Optional, Dict
import logging

from shared.db.repo import PipelineRepo


class StageRunner:
    """
    Wraps stage execution with DB + logging + resume support.

    Resume rules:
    - If latest stage status is 'completed' => skip (unless force=True)
    - If latest stage status is 'running'  => mark stale/failed, then rerun
    - If latest stage status is 'failed'   => rerun (unless you decide otherwise)

    Also:
    - Optionally loads output_json into state when skipping
    """

    def __init__(self, repo: PipelineRepo, run_id: str, logger: logging.Logger):
        self.repo = repo
        self.run_id = run_id
        self.logger = logger

        # FK safety
        self.repo.create_run(self.run_id)

    def run(
        self,
        stage_name: str,
        fn: Callable[[], Any],
        *,
        output_key: Optional[str] = None,
        state: Optional[Dict[str, Any]] = None,
        force: bool = False,
        load_output_on_skip: bool = True,
        stale_running_policy: str = "fail_then_rerun",  # or "rerun_anyway"
    ) -> Any:
        latest = self.repo.get_latest_stage(self.run_id, stage_name)

        # ---- STALE RUNNING STAGE HANDLING ----
        if latest and latest.get("status") == "running":
            msg = "Found previous 'running' stage record (stale)."
            if stale_running_policy == "fail_then_rerun":
                self.logger.warning(f"StageRunner: {stage_name} | {msg} Marking as failed then rerunning.")
                try:
                    # Mark the old stage as failed with 0 runtime (or keep None, your choice)
                    self.repo.finish_stage(
                        stage_id=int(latest["id"]),
                        success=False,
                        runtime_seconds=float(latest.get("runtime_seconds") or 0.0),
                        output=None,
                        error_message="Stale running stage detected (previous process likely crashed).",
                    )
                except Exception:
                    # Don't block rerun if this update fails
                    self.logger.exception(f"StageRunner: {stage_name} | Failed to mark stale stage as failed. Rerunning anyway.")
            else:
                self.logger.warning(f"StageRunner: {stage_name} | {msg} Rerunning anyway.")

            # refresh latest after cleanup attempt
            latest = self.repo.get_latest_stage(self.run_id, stage_name)

        # ---- RESUME SKIP ----
        if not force and latest and latest.get("status") == "completed":
            self.logger.info(f"StageRunner: skip {stage_name} (completed)")

            if load_output_on_skip and state is not None and output_key:
                output = self.repo.get_latest_stage_output(self.run_id, stage_name)
                if output is not None:
                    state[output_key] = output
                    self.logger.info(f"StageRunner: loaded saved output for {stage_name} -> state['{output_key}']")
                else:
                    self.logger.info(f"StageRunner: no saved output_json for {stage_name}")

            return self.repo.get_latest_stage_output(self.run_id, stage_name)

        # ---- RUN STAGE ----
        self.logger.info(f"{'='*60}")
        self.logger.info(f"StageRunner: start {stage_name}")
        self.logger.info(f"{'='*60}")

        stage_id = self.repo.start_stage(self.run_id, stage_name)
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
