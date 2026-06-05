from __future__ import annotations

from pathlib import Path
import datetime


class PipelineControl:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.data_dir = self.base_dir / "data"
        self.pause_flag = self.data_dir / "pause.flag"
        self.abort_flag = self.data_dir / "abort.flag"

    def start_hotkeys(self, logger) -> None:
        try:
            import keyboard
        except Exception as e:
            logger.warning(f"Keyboard hotkeys disabled: {e}")
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)

        keyboard.add_hotkey("ctrl+shift+p", lambda: self.request_pause(logger))
        keyboard.add_hotkey("ctrl+shift+q", lambda: self.request_abort(logger))

        logger.info("Hotkeys enabled: CTRL+SHIFT+P = pause, CTRL+SHIFT+Q = abort")

    def request_pause(self, logger) -> None:
        self.pause_flag.write_text(
            f"pause requested at {datetime.datetime.now().isoformat()}",
            encoding="utf-8",
        )
        logger.warning("Pause requested. Pipeline will pause safely.")

    def request_abort(self, logger) -> None:
        self.abort_flag.write_text(
            f"abort requested at {datetime.datetime.now().isoformat()}",
            encoding="utf-8",
        )
        logger.error("Abort requested. Pipeline will stop.")

    def clear_abort(self) -> None:
        if self.abort_flag.exists():
            self.abort_flag.unlink()

    def check_or_raise(self) -> None:
        if self.abort_flag.exists():
            raise RuntimeError("__PIPELINE_ABORTED__")

        if self.pause_flag.exists():
            raise RuntimeError("__PIPELINE_PAUSED__")