import builtins
import importlib.util
import socket
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATHS = [
    REPOSITORY_ROOT / "tools" / "run_rgb_pipeline.py",
    REPOSITORY_ROOT / "tools" / "query_pipeline_db.py",
    REPOSITORY_ROOT / "tools" / "reset_environment.py",
]


def fail(action):
    def blocked(*args, **kwargs):
        pytest.fail(f"Import attempted unsafe action: {action}")
    return blocked


class TrapModule(types.ModuleType):
    def __init__(self, name, action):
        super().__init__(name)
        self.action = action
        self.__file__ = f"<synthetic-trap:{name}>"
        self.__path__ = []

    def __getattr__(self, name):
        pytest.fail(f"Import attempted unsafe action: {self.action}")


def test_tool_imports_have_no_runtime_or_external_side_effects(monkeypatch, tmp_path):
    import_observation_dir = tmp_path / "import-observation"
    import_observation_dir.mkdir()
    monkeypatch.setattr(sqlite3, "connect", fail("access SQLite"))
    monkeypatch.setattr(socket, "create_connection", fail("contact network"))
    monkeypatch.setattr(subprocess, "run", fail("call subprocess"))
    monkeypatch.setattr(subprocess, "Popen", fail("call subprocess"))
    monkeypatch.setattr(builtins, "input", fail("request interactive input"))
    import shutil
    monkeypatch.setattr(shutil, "rmtree", fail("delete runtime files"))
    monkeypatch.setattr(Path, "unlink", fail("delete runtime files"))
    monkeypatch.setattr(Path, "write_text", fail("write outside pytest temporary paths"))
    monkeypatch.setattr(Path, "write_bytes", fail("write outside pytest temporary paths"))
    monkeypatch.setattr(Path, "mkdir", fail("write outside pytest temporary paths"))
    monkeypatch.setattr(Path, "touch", fail("write outside pytest temporary paths"))
    monkeypatch.setitem(sys.modules, "dotenv", TrapModule("dotenv", "load production .env"))
    monkeypatch.setitem(sys.modules, "keyboard", TrapModule("keyboard", "register keyboard hotkey"))
    monkeypatch.setitem(sys.modules, "requests", TrapModule("requests", "contact WebODM"))
    monkeypatch.setitem(sys.modules, "pipelines.rgb_pipeline", TrapModule("pipelines.rgb_pipeline", "start RGBPipeline"))
    monkeypatch.setitem(sys.modules, "shared.config", TrapModule("shared.config", "load production .env"))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    before = {path: path.stat().st_mtime_ns for path in TOOL_PATHS}
    for index, path in enumerate(TOOL_PATHS):
        spec = importlib.util.spec_from_file_location(f"safe_tool_{index}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    after = {path: path.stat().st_mtime_ns for path in TOOL_PATHS}

    assert after == before
    assert list(import_observation_dir.iterdir()) == []
