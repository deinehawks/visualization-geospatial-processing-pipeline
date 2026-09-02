from __future__ import annotations

import builtins
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests
from dotenv import main as dotenv_main

from shared.db.connection import connect
from shared.db.migrations.m001_add_run_pause_columns import MIGRATION_ID, apply
from shared.db.migrations.m002_webodm_task_bindings import (
    MIGRATION_ID as WEBODM_BINDING_MIGRATION_ID,
    apply as apply_webodm_binding_migration,
)
from shared.db.migrations.m003_run_workspace_root import (
    MIGRATION_ID as WORKSPACE_ROOT_MIGRATION_ID,
    apply as apply_workspace_root_migration,
)
from shared.db.repo import utc_now_iso
from shared.db.schema import SCHEMA_SQL


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DATABASE_PATH = REPOSITORY_ROOT / "data" / "pipeline.db"


class UnsafeTestOperation(RuntimeError):
    """Raised when a default-suite test crosses a protected boundary."""


@dataclass(frozen=True)
class TemporaryPathLayout:
    application_root: Path
    data_dir: Path
    logs_dir: Path
    surveys_dir: Path
    field_data_dir: Path
    upload_cache_dir: Path
    checkpoint_dir: Path
    database_path: Path


def initialize_temporary_pipeline_database(database_path: Path) -> Path:
    """Initialize the current schema and always release the SQLite handle."""
    connection = connect(database_path)
    try:
        connection.executescript(SCHEMA_SQL)
        apply(connection)
        apply_webodm_binding_migration(connection)
        apply_workspace_root_migration(connection)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)",
            (MIGRATION_ID, utc_now_iso()),
        )
        connection.execute(
            'INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)',
            (WEBODM_BINDING_MIGRATION_ID, utc_now_iso()),
        )
        connection.execute(
            'INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)',
            (WORKSPACE_ROOT_MIGRATION_ID, utc_now_iso()),
        )
        connection.commit()
    finally:
        connection.close()
    return database_path


def _captured_production_roots() -> tuple[Path, ...]:
    roots = [REPOSITORY_ROOT / "data"]
    for name in ("SURVEYS_ROOT", "FIELD_DATA_ROOT", "UPLOAD_CACHE_ROOT"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value))
    return tuple(roots)


CAPTURED_PRODUCTION_ROOTS = _captured_production_roots()


def _normalized_path(value: object) -> str | None:
    if isinstance(value, int):
        return None
    try:
        raw = os.fspath(value)
    except TypeError:
        return None
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    return os.path.normcase(os.path.abspath(raw))


def _is_within(path: object, root: Path) -> bool:
    candidate = _normalized_path(path)
    normalized_root = _normalized_path(root)
    if candidate is None or normalized_root is None:
        return False
    try:
        return os.path.commonpath((candidate, normalized_root)) == normalized_root
    except ValueError:
        return False


def _reject_production_path(path: object) -> None:
    for root in CAPTURED_PRODUCTION_ROOTS:
        if _is_within(path, root):
            raise UnsafeTestOperation(
                f"Default tests may not access production path: {path}"
            )


def _blocked(action: str):
    def reject(*args, **kwargs):
        raise UnsafeTestOperation(f"Default tests may not {action}")

    return reject


@pytest.fixture
def temporary_application_root(tmp_path: Path) -> Path:
    root = tmp_path / "application"
    root.mkdir()
    return root


@pytest.fixture
def temporary_data_dir(temporary_application_root: Path) -> Path:
    path = temporary_application_root / "data"
    path.mkdir()
    return path


@pytest.fixture
def temporary_logs_dir(temporary_data_dir: Path) -> Path:
    path = temporary_data_dir / "logs"
    path.mkdir()
    return path


@pytest.fixture
def temporary_surveys_dir(temporary_data_dir: Path) -> Path:
    path = temporary_data_dir / "surveys"
    path.mkdir()
    return path


@pytest.fixture
def temporary_field_data_dir(temporary_data_dir: Path) -> Path:
    path = temporary_data_dir / "field-data"
    path.mkdir()
    return path


@pytest.fixture
def temporary_upload_cache_dir(temporary_data_dir: Path) -> Path:
    path = temporary_data_dir / "upload-cache"
    path.mkdir()
    return path


@pytest.fixture
def temporary_checkpoint_dir(temporary_data_dir: Path) -> Path:
    path = temporary_data_dir / "checkpoints"
    path.mkdir()
    return path


@pytest.fixture
def temporary_sqlite_db_path(temporary_data_dir: Path) -> Path:
    return initialize_temporary_pipeline_database(
        temporary_data_dir / "test-pipeline.db"
    )


@pytest.fixture
def temporary_path_layout(
    temporary_application_root: Path,
    temporary_data_dir: Path,
    temporary_logs_dir: Path,
    temporary_surveys_dir: Path,
    temporary_field_data_dir: Path,
    temporary_upload_cache_dir: Path,
    temporary_checkpoint_dir: Path,
    temporary_sqlite_db_path: Path,
) -> TemporaryPathLayout:
    return TemporaryPathLayout(
        application_root=temporary_application_root,
        data_dir=temporary_data_dir,
        logs_dir=temporary_logs_dir,
        surveys_dir=temporary_surveys_dir,
        field_data_dir=temporary_field_data_dir,
        upload_cache_dir=temporary_upload_cache_dir,
        checkpoint_dir=temporary_checkpoint_dir,
        database_path=temporary_sqlite_db_path,
    )


@pytest.fixture
def sample_survey_dir(temporary_surveys_dir: Path) -> Path:
    path = temporary_surveys_dir / "2026" / "TEST-SURVEY-001" / "rgb"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def sample_dataset_dir(temporary_field_data_dir: Path) -> Path:
    path = temporary_field_data_dir / "TEST-DATASET-001" / "images"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def placeholder_image_files(sample_dataset_dir: Path) -> tuple[Path, ...]:
    paths = (
        sample_dataset_dir / "image-001.jpg",
        sample_dataset_dir / "image-002.jpg",
    )
    for path in paths:
        path.write_bytes(b"test-image-placeholder")
    return paths


@pytest.fixture
def temporary_pipeline_db_factory(tmp_path: Path) -> Callable[[str], Path]:
    database_root = tmp_path / "databases"
    database_root.mkdir()

    def create_database(name: str = "pipeline.db") -> Path:
        database_path = database_root / name
        if database_path.parent != database_root:
            raise ValueError("Temporary database name must not contain path components")
        return initialize_temporary_pipeline_database(database_path)

    return create_database


@pytest.fixture
def temporary_sqlite_connection(
    temporary_sqlite_db_path: Path,
) -> Iterator[sqlite3.Connection]:
    connection = connect(temporary_sqlite_db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def default_suite_safety_guards(
    monkeypatch: pytest.MonkeyPatch,
    temporary_application_root: Path,
    temporary_surveys_dir: Path,
    temporary_field_data_dir: Path,
    temporary_upload_cache_dir: Path,
) -> None:
    """Deny external effects and redirect configuration to test-owned roots."""

    monkeypatch.chdir(temporary_application_root)
    monkeypatch.setenv("SURVEYS_ROOT", str(temporary_surveys_dir))
    monkeypatch.setenv("FIELD_DATA_ROOT", str(temporary_field_data_dir))
    monkeypatch.setenv("UPLOAD_CACHE_ROOT", str(temporary_upload_cache_dir))
    for name in (
        "WEBODM_URL",
        "WEBODM_USERNAME",
        "WEBODM_PASSWORD",
        "WEBODM_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    network_blocker = _blocked("make outbound network requests")
    monkeypatch.setattr(socket, "create_connection", network_blocker)
    monkeypatch.setattr(socket.socket, "connect", network_blocker)
    monkeypatch.setattr(socket.socket, "connect_ex", network_blocker)
    monkeypatch.setattr(requests.sessions.Session, "request", network_blocker)

    subprocess_blocker = _blocked("execute external subprocesses")
    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, subprocess_blocker)

    monkeypatch.setattr(builtins, "input", _blocked("request interactive input"))

    keyboard_module = types.ModuleType("keyboard")
    keyboard_module.add_hotkey = _blocked("register keyboard hotkeys")
    keyboard_module.remove_hotkey = _blocked("manage keyboard hotkeys")
    monkeypatch.setitem(sys.modules, "keyboard", keyboard_module)

    dotenv_blocker = _blocked("load .env files")
    monkeypatch.setattr(dotenv_main, "load_dotenv", dotenv_blocker)
    dotenv_module = sys.modules.get("dotenv")
    if dotenv_module is not None:
        monkeypatch.setattr(dotenv_module, "load_dotenv", dotenv_blocker)
    loaded_config = sys.modules.get("shared.config")
    if loaded_config is not None:
        monkeypatch.setattr(loaded_config, "load_dotenv", dotenv_blocker)

    original_sqlite_connect = sqlite3.connect

    def guarded_sqlite_connect(database, *args, **kwargs):
        if database != ":memory:" and _is_within(database, PRODUCTION_DATABASE_PATH):
            raise UnsafeTestOperation(
                f"Default tests may not open production database: {database}"
            )
        return original_sqlite_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_sqlite_connect)

    original_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        _reject_production_path(file)
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)

    original_path_open = Path.open

    def guarded_path_open(path, *args, **kwargs):
        _reject_production_path(path)
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_path_open)

    for method_name in ("mkdir", "touch", "unlink", "rmdir", "iterdir", "glob", "rglob"):
        original_method = getattr(Path, method_name)

        def guarded_method(path, *args, _original=original_method, **kwargs):
            _reject_production_path(path)
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(Path, method_name, guarded_method)

    for method_name in ("rename", "replace"):
        original_method = getattr(Path, method_name)

        def guarded_two_path_method(
            path, target, *args, _original=original_method, **kwargs
        ):
            _reject_production_path(path)
            _reject_production_path(target)
            return _original(path, target, *args, **kwargs)

        monkeypatch.setattr(Path, method_name, guarded_two_path_method)

    original_rmtree = shutil.rmtree

    def guarded_rmtree(path, *args, **kwargs):
        _reject_production_path(path)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", guarded_rmtree)
