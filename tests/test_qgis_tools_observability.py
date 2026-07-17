from __future__ import annotations

import subprocess

import pytest

from modules.qgis import qgis_tools as qgis_module
from modules.qgis.qgis_tools import QGISTools
from shared.logging import get_logger


RUN_ID = "qgis-observability-run"


def cleanup_logger(logger):
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.filters.clear()
    if hasattr(logger, "_configured"):
        delattr(logger, "_configured")


def test_qgis_tools_emits_command_started_and_completed_events(monkeypatch, tmp_path):
    log_path = tmp_path / "qgis-command-events.log"
    logger = get_logger("tests.qgis.tools", log_path, to_console=False, run_id=RUN_ID)
    input_tif = tmp_path / "input.tif"
    output_dir = tmp_path / "tiles"
    input_tif.write_bytes(b"fake tif")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(qgis_module.subprocess, "run", fake_run)
    tools = QGISTools(logger, gdal2tiles_path="gdal2tiles")

    try:
        result = tools.generate_tiles(
            input_tif=input_tif,
            output_dir=output_dir,
            zoom="1-2",
            clean=False,
        )

        assert result == output_dir
        assert len(calls) == 1
        assert calls[0][0][:7] == [
            "gdal2tiles",
            "-p",
            "mercator",
            "-z",
            "1-2",
            "-w",
            "none",
        ]
        content = log_path.read_text(encoding="utf-8")
        assert "tests.qgis.tools | qgis-observability-run |  | event=qgis_command_started" in content
        assert "tool=gdal2tiles" in content
        assert "argv0=gdal2tiles" in content
        assert "arg_count=11" in content
        assert "event=qgis_command_completed tool=gdal2tiles" in content
        assert "returncode=0" in content
    finally:
        cleanup_logger(logger)


def test_qgis_tools_emits_command_failed_event_without_full_paths(monkeypatch, tmp_path):
    log_path = tmp_path / "qgis-command-failure.log"
    logger = get_logger("tests.qgis.tools", log_path, to_console=False, run_id=RUN_ID)
    input_tif = tmp_path / "input.tif"
    output_dir = tmp_path / "tiles"
    input_tif.write_bytes(b"fake tif")

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            7,
            cmd,
            output="fake stdout",
            stderr="fake stderr",
        )

    monkeypatch.setattr(qgis_module.subprocess, "run", fake_run)
    tools = QGISTools(logger, gdal2tiles_path="gdal2tiles")

    try:
        with pytest.raises(RuntimeError, match="gdal2tiles failed"):
            tools.generate_tiles(
                input_tif=input_tif,
                output_dir=output_dir,
                zoom="1-2",
                clean=False,
            )

        content = log_path.read_text(encoding="utf-8")
        assert "event=qgis_command_started tool=gdal2tiles" in content
        assert "event=qgis_command_failed tool=gdal2tiles" in content
        assert "error_type=CalledProcessError" in content
        assert 'error_message="command returned non-zero exit status 7"' in content
        assert "returncode=7" in content
        assert str(input_tif) not in content
        assert str(output_dir) not in content
    finally:
        cleanup_logger(logger)