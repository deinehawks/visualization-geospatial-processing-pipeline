
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional, List

from shared.db.repo import PipelineRepo
from shared.storage_preflight import StorageCapacityError
from shared.logging import (
    log_output_loaded,
    log_stage_canceled,
    log_stage_done,
    log_stage_fail,
    log_stage_retry,
    log_stage_skip,
    log_stage_start,
    log_stale_stage,
    set_stage_context,
)


_WEBODM_TASK_CANCELED = "WEBODM_TASK_CANCELED"
_PIPELINE_CONTROL_SIGNALS = frozenset(
    {"__PIPELINE_CANCELED__", "__PIPELINE_PAUSED__", "__PIPELINE_ABORTED__"}
)

class StageRequiresRecovery(RuntimeError):
    """Signal that a stage failed after mutation and needs recovery."""

    status = "requires_recovery"

    def __init__(
        self,
        message: str,
        *,
        output: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.output = output


class StageFailedWithOutput(RuntimeError):
    """Signal a normal stage failure while preserving diagnostic output."""

    status = "failed"

    def __init__(
        self,
        message: str,
        *,
        output: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.output = output


class StageRunner:
    """
    Wraps stage execution with DB tracking, structured logging, and
    resume / retry support.

    Every log record emitted through *logger* will automatically carry
    ``run_id`` and ``stage_name`` fields (injected by the
    :class:`~shared.logging.ContextFilter` that
    :func:`~shared.logging.get_logger` attaches).
    """

    def __init__(
        self,
        repo: PipelineRepo,
        run_id: str,
        logger: logging.Logger,
        extra_loggers: Optional[List[logging.Logger]] = None,
    ) -> None:
        self.repo = repo
        self.run_id = run_id
        self.logger = logger
        self.extra_loggers = extra_loggers or []
        self.repo.create_run(self.run_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        retry_attempts: int = 3,
        retry_delay_seconds: int = 5,
        result_status: Optional[Callable[[Any], str]] = None,
    ) -> Any:
        """
        Execute *fn* as a named pipeline stage.

        Args:
            stage_name:           Unique name used for DB tracking and logs.
            fn:                   Zero-argument callable that performs the work.
            output_key:           If set, the return value is stored in *state*
                                  under this key and persisted to the DB.
            state:                Shared pipeline state dict mutated in-place.
            force:                Re-run even if the stage already completed.
            load_output_on_skip:  Reload saved output into *state* on skip.
            stale_running_policy: ``"fail_then_rerun"`` (default) marks a
                                  leftover ``running`` record as failed before
                                  re-executing; ``"rerun"`` skips the update.
            retry_attempts:       Number of attempts before giving up on
                                  transient errors (default 3).
            retry_delay_seconds:  Seconds to wait between retry attempts.
            result_status:        Optional mapping from a successful return
                                  value to its persisted terminal status.
        """
        latest = self.repo.get_latest_stage(self.run_id, stage_name)

        if latest and latest.get("status") == "running":
            log_stale_stage(self.logger, stage_name)

            if stale_running_policy == "fail_then_rerun":
                try:
                    self.repo.finish_stage(
                        stage_id=int(latest["id"]),
                        success=False,
                        runtime_seconds=float(
                            latest.get("runtime_seconds") or 0.0),
                        output=None,
                        error_message="Stale running stage (previous crash).",
                    )
                except Exception:
                    self.logger.exception(
                        "Could not update stale stage record — continuing anyway."
                    )

            latest = self.repo.get_latest_stage(self.run_id, stage_name)

        # ── Resume skip ──────────────────────────────────────────────────
        if not force and latest and latest.get("status") == "completed":
            log_stage_skip(self.logger, stage_name)

            if load_output_on_skip and state is not None and output_key:
                output = self.repo.get_latest_stage_output(
                    self.run_id, stage_name)
                if output is not None:
                    state[output_key] = output
                    set_stage_context(self.logger, stage_name)
                    log_output_loaded(self.logger, output_key)
                    set_stage_context(self.logger, "")

            return self.repo.get_latest_stage_output(self.run_id, stage_name)

        # ── Execute stage ────────────────────────────────────────────────
        log_stage_start(self.logger, stage_name)
        for _lg in self.extra_loggers:
            set_stage_context(_lg, stage_name)

        stage_id = self.repo.start_stage(self.run_id, stage_name)
        wall_start = time.perf_counter()
        attempt = 0

        while True:
            try:
                result = fn()
                runtime = time.perf_counter() - wall_start

                if state is not None and output_key:
                    state[output_key] = result

                terminal_status = result_status(result) if result_status else "completed"
                if not terminal_status:
                    raise ValueError("result_status must return a non-empty status")
                self.repo.finish_stage_with_status(
                    stage_id=stage_id,
                    status=terminal_status,
                    runtime_seconds=runtime,
                    output=result if isinstance(result, dict) else None,
                    error_message=None,
                )

                log_stage_done(self.logger, stage_name, runtime)
                for _lg in self.extra_loggers:
                    set_stage_context(_lg, "")
                return result

            # ── WebODM UI cancel — clean propagation ─────────────────────
            except Exception as exc:
                # Pipeline control currently uses explicit RuntimeError
                # messages rather than typed exceptions. Preserve only those
                # documented signals; every other RuntimeError is a failure.
                message = str(exc)
                if (
                    isinstance(exc, RuntimeError)
                    and message == _WEBODM_TASK_CANCELED
                ):
                    runtime = time.perf_counter() - wall_start
                    self.repo.finish_stage(
                        stage_id=stage_id,
                        success=False,
                        runtime_seconds=runtime,
                        output=None,
                        error_message="Canceled in WebODM UI",
                    )
                    log_stage_canceled(self.logger, stage_name, runtime)
                    for _lg in self.extra_loggers:
                        set_stage_context(_lg, "")
                    raise RuntimeError("__PIPELINE_CANCELED__") from exc

                if (
                    isinstance(exc, RuntimeError)
                    and message in _PIPELINE_CONTROL_SIGNALS
                ):
                    if message == '__PIPELINE_PAUSED__':
                        runtime = time.perf_counter() - wall_start
                        paused_output = None
                        if state is not None and output_key:
                            candidate = state.get(output_key)
                            if isinstance(candidate, dict):
                                paused_output = candidate
                        self.repo.finish_stage_with_status(
                            stage_id=stage_id,
                            status='paused',
                            runtime_seconds=runtime,
                            output=paused_output,
                            error_message='Paused by pipeline control',
                        )
                        for _lg in self.extra_loggers:
                            set_stage_context(_lg, '')
                    raise

                if isinstance(exc, (StageRequiresRecovery, StageFailedWithOutput)):
                    runtime = time.perf_counter() - wall_start
                    failure_output = exc.output
                    if state is not None and output_key and failure_output is not None:
                        state[output_key] = failure_output
                    self.repo.finish_stage_with_status(
                        stage_id=stage_id,
                        status=exc.status,
                        runtime_seconds=runtime,
                        output=failure_output,
                        error_message=str(exc),
                    )
                    log_stage_fail(self.logger, stage_name, runtime)
                    for _lg in self.extra_loggers:
                        set_stage_context(_lg, "")
                    self.logger.exception(exc)
                    raise

                if isinstance(exc, StorageCapacityError):
                    runtime = time.perf_counter() - wall_start
                    self.repo.finish_stage(
                        stage_id=stage_id,
                        success=False,
                        runtime_seconds=runtime,
                        output=None,
                        error_message=str(exc),
                    )
                    log_stage_fail(self.logger, stage_name, runtime)
                    for _lg in self.extra_loggers:
                        set_stage_context(_lg, "")
                    self.logger.exception(exc)
                    raise

                attempt += 1

                if attempt < retry_attempts:
                    log_stage_retry(
                        self.logger,
                        stage_name,
                        attempt=attempt,
                        max_attempts=retry_attempts,
                        delay=retry_delay_seconds,
                        error=exc,
                    )
                    time.sleep(retry_delay_seconds)
                    continue

                runtime = time.perf_counter() - wall_start
                self.repo.finish_stage(
                    stage_id=stage_id,
                    success=False,
                    runtime_seconds=runtime,
                    output=None,
                    error_message=str(exc),
                )
                log_stage_fail(self.logger, stage_name, runtime)
                for _lg in self.extra_loggers:
                    set_stage_context(_lg, "")
                self.logger.exception(exc)
                raise
