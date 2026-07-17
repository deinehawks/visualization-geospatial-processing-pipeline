import importlib.util
from importlib.machinery import ModuleSpec
import logging
import sys
import types

import pytest

if "exifread" not in sys.modules and importlib.util.find_spec("exifread") is None:
    exifread_stub = types.ModuleType("exifread")

    def reject_exif_processing(*args, **kwargs):
        pytest.fail("logging characterization test attempted EXIF processing")

    exifread_stub.process_file = reject_exif_processing
    exifread_stub.__spec__ = ModuleSpec("exifread", loader=None)
    sys.modules["exifread"] = exifread_stub
if "rich" not in sys.modules and importlib.util.find_spec("rich") is None:
    rich_stub = types.ModuleType("rich")
    rich_console_stub = types.ModuleType("rich.console")
    rich_panel_stub = types.ModuleType("rich.panel")
    rich_rule_stub = types.ModuleType("rich.rule")
    rich_table_stub = types.ModuleType("rich.table")
    rich_box_stub = types.ModuleType("rich.box")

    class RejectRichRendering:
        def __init__(self, *args, **kwargs):
            pass

        def print(self, *args, **kwargs):
            pytest.fail("logging parser compatibility test attempted rich rendering")

        def add_column(self, *args, **kwargs):
            pytest.fail("logging parser compatibility test attempted rich table rendering")

        def add_row(self, *args, **kwargs):
            pytest.fail("logging parser compatibility test attempted rich table rendering")

    rich_console_stub.Console = RejectRichRendering
    rich_panel_stub.Panel = RejectRichRendering
    rich_rule_stub.Rule = RejectRichRendering
    rich_table_stub.Table = RejectRichRendering
    rich_box_stub.SIMPLE = object()
    rich_box_stub.ROUNDED = object()
    rich_box_stub.MINIMAL = object()
    rich_box_stub.MINIMAL_DOUBLE_HEAD = object()
    rich_stub.box = rich_box_stub
    for module in (
        rich_stub,
        rich_console_stub,
        rich_panel_stub,
        rich_rule_stub,
        rich_table_stub,
        rich_box_stub,
    ):
        module.__spec__ = ModuleSpec(module.__name__, loader=None)
    sys.modules["rich"] = rich_stub
    sys.modules["rich.console"] = rich_console_stub
    sys.modules["rich.panel"] = rich_panel_stub
    sys.modules["rich.rule"] = rich_rule_stub
    sys.modules["rich.table"] = rich_table_stub
    sys.modules["rich.box"] = rich_box_stub

from pipelines.rgb_pipeline import RGBPipeline
from query_survey_stats import parse_log_events
from shared.logging import get_context_filter, get_logger, set_stage_context
from tests.fakes import FakeWebODM


RGB_LOGGER_NAMES = (
    "rgb.pipeline",
    "rgb.data_segregation",
    "rgb.cross_run_filter",
    "rgb.kml",
    "rgb.webodm",
    "rgb.qgis",
)


def cleanup_logger(logger):
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.filters.clear()
    if hasattr(logger, "_configured"):
        delattr(logger, "_configured")


def cleanup_named_loggers(names):
    for name in names:
        cleanup_logger(logging.getLogger(name))


def explicit_config(root):
    return {
        "paths": {
            "surveys_root": root / "surveys",
            "field_data_root": root / "field-data",
            "upload_cache_root": root / "upload-cache",
        },
        "webodm": {
            "url": "https://webodm.invalid",
            "username": "test-user",
            "password": "test-password",
        },
    }


def build_default_logger_pipeline(root, run_id):
    source_dir = root / "source"
    surveys_root = root / "surveys"
    source_dir.mkdir(parents=True)
    surveys_root.mkdir(parents=True)

    return RGBPipeline(
        root,
        explicit_config(root),
        source_dir=source_dir,
        surveys_root=surveys_root,
        year=2026,
        run_id=run_id,
        db_file=root / "data" / "pipeline.db",
        webodm_processor=FakeWebODM(),
    )


def test_get_logger_isolates_handler_destinations_and_run_context(tmp_path):
    logger_name = "tests.logging.phase2.isolated"
    first_log = tmp_path / "first-run.log"
    second_log = tmp_path / "second-run.log"
    created_loggers = []

    try:
        first = get_logger(
            logger_name,
            first_log,
            to_console=False,
            run_id="run-a",
        )
        second = get_logger(
            logger_name,
            second_log,
            to_console=False,
            run_id="run-b",
        )
        created_loggers.extend([first, second])

        assert first is not second
        assert len(first.handlers) == 1
        assert len(second.handlers) == 1
        assert first.handlers[0].baseFilename == str(first_log)
        assert second.handlers[0].baseFilename == str(second_log)

        first.info("message from first logger")
        second.info("message from second logger")

        first_content = first_log.read_text(encoding="utf-8")
        second_content = second_log.read_text(encoding="utf-8")
        assert "tests.logging.phase2.isolated | run-a |  | message from first logger" in first_content
        assert "run-b" not in first_content
        assert "tests.logging.phase2.isolated | run-b |  | message from second logger" in second_content
        assert "run-a" not in second_content
    finally:
        for logger in created_loggers:
            cleanup_logger(logger)


def test_stage_context_is_isolated_between_owned_logger_instances(tmp_path):
    logger_name = "tests.logging.phase2.stage-isolated"
    first_log = tmp_path / "first-stage.log"
    second_log = tmp_path / "second-stage.log"
    created_loggers = []

    try:
        first = get_logger(
            logger_name,
            first_log,
            to_console=False,
            run_id="run-a",
        )
        second = get_logger(
            logger_name,
            second_log,
            to_console=False,
            run_id="run-b",
        )
        created_loggers.extend([first, second])

        set_stage_context(first, "stage-a")
        second.info("message before second stage is set")
        set_stage_context(second, "stage-b")
        first.info("message from first stage")
        second.info("message from second stage")

        first_content = first_log.read_text(encoding="utf-8")
        second_content = second_log.read_text(encoding="utf-8")
        assert "run-a | stage-a | message from first stage" in first_content
        assert "stage-b" not in first_content
        assert "run-b |  | message before second stage is set" in second_content
        assert "run-b | stage-b | message from second stage" in second_content
        assert "stage-a" not in second_content
    finally:
        for logger in created_loggers:
            cleanup_logger(logger)


def test_default_rgb_pipeline_loggers_own_handlers_per_instance(tmp_path):
    first_root = tmp_path / "first-application"
    second_root = tmp_path / "second-application"
    first_root.mkdir()
    second_root.mkdir()
    created_loggers = []

    try:
        first_pipeline = build_default_logger_pipeline(first_root, "run-a")
        second_pipeline = build_default_logger_pipeline(second_root, "run-b")
        created_loggers.extend(first_pipeline.loggers.values())
        created_loggers.extend(second_pipeline.loggers.values())

        first_logger = first_pipeline.loggers["pipeline"]
        second_logger = second_pipeline.loggers["pipeline"]
        first_context = get_context_filter(first_logger)
        second_context = get_context_filter(second_logger)
        first_log = first_root / "data" / "logs" / "pipeline.log"
        second_log = second_root / "data" / "logs" / "pipeline.log"

        assert first_logger is not second_logger
        assert first_context is not None
        assert second_context is not None
        assert first_context.run_id == "run-a"
        assert second_context.run_id == "run-b"
        assert any(getattr(handler, "baseFilename", None) == str(first_log) for handler in first_logger.handlers)
        assert any(getattr(handler, "baseFilename", None) == str(second_log) for handler in second_logger.handlers)

        first_logger.info("message from first pipeline")
        second_logger.info("message from second pipeline")

        first_content = first_log.read_text(encoding="utf-8")
        second_content = second_log.read_text(encoding="utf-8")
        assert "rgb.pipeline | run-a |  | message from first pipeline" in first_content
        assert "run-b" not in first_content
        assert "rgb.pipeline | run-b |  | message from second pipeline" in second_content
        assert "run-a" not in second_content
    finally:
        for logger in created_loggers:
            cleanup_logger(logger)

def test_generated_isolated_logger_output_remains_parser_compatible(tmp_path):
    logger = get_logger(
        "rgb.pipeline",
        tmp_path / "pipeline.log",
        to_console=False,
        run_id="run-parser",
    )

    try:
        set_stage_context(logger, "data_segregation")
        logger.info("parser compatibility probe")

        events = parse_log_events(tmp_path, ["run-parser"])

        assert list(events) == ["run-parser"]
        assert events["run-parser"] == [
            {
                "time": events["run-parser"][0]["time"],
                "level": "INFO",
                "logger": "rgb.pipeline",
                "stage": "data_segregation",
                "message": "parser compatibility probe",
                "log_file": "pipeline.log",
            }
        ]
    finally:
        set_stage_context(logger, "")
        cleanup_logger(logger)

