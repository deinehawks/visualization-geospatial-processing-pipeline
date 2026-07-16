import builtins
import os
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest
import dotenv


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_outbound_network_is_blocked():
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("example.invalid", 443))


def test_external_subprocess_is_blocked():
    with pytest.raises(RuntimeError, match="subprocess"):
        subprocess.run(["definitely-not-a-real-command"], check=True)


def test_interactive_input_is_blocked():
    with pytest.raises(RuntimeError, match="interactive input"):
        builtins.input("unsafe prompt")


def test_dotenv_loading_is_blocked():
    with pytest.raises(RuntimeError, match=r"load \.env"):
        dotenv.load_dotenv(REPOSITORY_ROOT / ".env")


def test_keyboard_hotkey_registration_is_blocked():
    import keyboard

    with pytest.raises(RuntimeError, match="keyboard hotkeys"):
        keyboard.add_hotkey("ctrl+shift+p", lambda: None)


def test_sensitive_configuration_paths_are_test_owned(
    tmp_path,
    temporary_surveys_dir,
    temporary_field_data_dir,
    temporary_upload_cache_dir,
):
    assert Path(os.environ["SURVEYS_ROOT"]) == temporary_surveys_dir
    assert Path(os.environ["FIELD_DATA_ROOT"]) == temporary_field_data_dir
    assert Path(os.environ["UPLOAD_CACHE_ROOT"]) == temporary_upload_cache_dir
    assert all(
        tmp_path in path.parents
        for path in (
            temporary_surveys_dir,
            temporary_field_data_dir,
            temporary_upload_cache_dir,
        )
    )


def test_known_production_data_path_is_blocked():
    production_probe = REPOSITORY_ROOT / "data" / "pytest-guard-probe.txt"
    existed_before = production_probe.exists()

    with pytest.raises(RuntimeError, match="production path"):
        production_probe.write_text("unsafe", encoding="utf-8")

    assert production_probe.exists() is existed_before


def test_temporary_sqlite_and_filesystem_operations_remain_available(tmp_path):
    database_path = tmp_path / "safe.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE example (value TEXT)")
        connection.execute("INSERT INTO example VALUES ('safe')")
        connection.commit()

    text_path = tmp_path / "safe.txt"
    text_path.write_text("safe", encoding="utf-8")

    assert text_path.read_text(encoding="utf-8") == "safe"
    assert database_path.is_file()
