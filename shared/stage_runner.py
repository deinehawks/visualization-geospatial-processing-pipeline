from __future__ import annotations
from shared.db.repo import PipelineRepo
from shared.logging import line
from typing import Callable, Any, Optional, Dict

import time
import logging


class StageRunner:
    """
    Wraps stage execution with DB + logging + resume support.
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
        stale_running_policy: str = "fail_then_rerun",
        retry_attempts: int = 3,          # NEW
        retry_delay_seconds: int = 5,     # NEW
    ) -> Any:

        latest = self.repo.get_latest_stage(self.run_id, stage_name)

        # ----------------------------
        # Handle stale "running"
        # ----------------------------
        if latest and latest.get("status") == "running":
            self.logger.warning(f"⚠ Stale stage detected: {stage_name}")

            if stale_running_policy == "fail_then_rerun":
                try:
                    self.repo.finish_stage(
                        stage_id=int(latest["id"]),
                        success=False,
                        runtime_seconds=float(latest.get("runtime_seconds") or 0.0),
                        output=None,
                        error_message="Stale running stage (previous crash).",
                    )
                    self.logger.warning("Marked stale stage as failed. Rerunning.")
                except Exception:
                    self.logger.exception("Failed to update stale stage. Continuing rerun.")

            latest = self.repo.get_latest_stage(self.run_id, stage_name)

        # ----------------------------
        # Resume skip
        # ----------------------------
        if not force and latest and latest.get("status") == "completed":
            self.logger.info(f"\033[93m⏭ SKIP   | {stage_name} (already completed)\033[0m")

            if load_output_on_skip and state is not None and output_key:
                output = self.repo.get_latest_stage_output(self.run_id, stage_name)
                if output is not None:
                    state[output_key] = output
                    self.logger.info(f"↳ Loaded saved output into state['{output_key}']")

            return self.repo.get_latest_stage_output(self.run_id, stage_name)

        # ----------------------------
        # Run stage
        # ----------------------------
        self.logger.info(line())
        self.logger.info(f"\033[94m▶ START  | {stage_name}\033[0m")
        self.logger.info(line())

        stage_id = self.repo.start_stage(self.run_id, stage_name)
        start = time.perf_counter()

        attempt = 0

        while True:
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

                self.logger.info(f"\033[92m✔ DONE   | {stage_name} | {runtime:.2f}s\033[0m")
                return result

            # ----------------------------
            # WebODM UI Cancel (clean stop)
            # ----------------------------
            except RuntimeError as e:

                if str(e) == "WEBODM_TASK_CANCELED":
                    runtime = time.perf_counter() - start

                    self.repo.finish_stage(
                        stage_id=stage_id,
                        success=False,
                        runtime_seconds=runtime,
                        output=None,
                        error_message="Canceled in WebODM UI",
                    )

                    self.logger.warning(f"\033[93m⏹ CANCELED | {stage_name} | {runtime:.2f}s\033[0m")

                    # propagate special signal
                    raise RuntimeError("__PIPELINE_CANCELED__") from e

                if str(e) == "__PIPELINE_CANCELED__":
                    raise

                raise

            # ----------------------------
            # Retry for transient errors
            # ----------------------------
            except Exception as e:

                attempt += 1

                if attempt < retry_attempts:
                    self.logger.warning(
                        f"⚠ Stage '{stage_name}' failed (attempt {attempt}/{retry_attempts}). "
                        f"Retrying in {retry_delay_seconds}s..."
                    )
                    self.logger.warning(str(e))
                    time.sleep(retry_delay_seconds)
                    continue

                runtime = time.perf_counter() - start

                self.repo.finish_stage(
                    stage_id=stage_id,
                    success=False,
                    runtime_seconds=runtime,
                    output=None,
                    error_message=str(e),
                )

                self.logger.error(f"\033[91m✗ FAILED | {stage_name} | {runtime:.2f}s\033[0m")
                self.logger.exception(e)
                raise