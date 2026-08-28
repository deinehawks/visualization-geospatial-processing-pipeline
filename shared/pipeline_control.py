from __future__ import annotations

import datetime
from pathlib import Path


class PipelineControl:
    def __init__(self, base_dir: Path, run_id: str):
        self.base_dir = Path(base_dir)
        self.run_id = str(run_id)
        self.data_dir = self.base_dir / "data"

        self.pause_flag = self.data_dir / f"pause_{self.run_id}.flag"
        self.abort_flag = self.data_dir / f"abort_{self.run_id}.flag"

    def start_hotkeys(self, logger) -> None:
        try:
            import keyboard
        except Exception as e:
            logger.warning(f"Keyboard hotkeys disabled: {e}")
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)

        keyboard.add_hotkey("ctrl+shift+p", lambda: self.request_pause(logger))
        keyboard.add_hotkey("ctrl+shift+q", lambda: self.request_abort(logger))

        logger.info(
            f"Hotkeys enabled for run {self.run_id}: "
            "CTRL+SHIFT+P = pause, CTRL+SHIFT+Q = abort"
        )

    def request_pause(self, logger) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pause_flag.write_text(
            f"pause requested for run {self.run_id} at {datetime.datetime.now().isoformat()}",
            encoding="utf-8",
        )
        logger.warning(
            f'Pause requested for run {self.run_id}. Local orchestration will '
            'detach safely; any active WebODM task will continue remotely.'
        )

    def request_abort(self, logger) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.abort_flag.write_text(
            f"abort requested for run {self.run_id} at {datetime.datetime.now().isoformat()}",
            encoding="utf-8",
        )
        logger.error(f"Abort requested for run {self.run_id}. Pipeline will stop.")

    def clear_abort(self) -> None:
        self.abort_flag.unlink(missing_ok=True)

    def clear_pause(self) -> None:
        self.pause_flag.unlink(missing_ok=True)

    def cleanup_flags(self) -> None:
        self.pause_flag.unlink(missing_ok=True)
        self.abort_flag.unlink(missing_ok=True)

    def check_or_raise(self) -> None:
        if self.abort_flag.exists():
            raise RuntimeError("__PIPELINE_ABORTED__")

        if self.pause_flag.exists():
            raise RuntimeError("__PIPELINE_PAUSED__")
