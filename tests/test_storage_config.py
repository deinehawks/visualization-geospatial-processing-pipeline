from pathlib import Path

import pytest

import shared.config as config_module


def _set_required_config(monkeypatch, tmp_path: Path) -> None:
    surveys = tmp_path / "surveys"
    field_data = tmp_path / "field-data"
    surveys.mkdir()
    field_data.mkdir()
    monkeypatch.setattr(config_module, "load_dotenv", lambda: None)
    monkeypatch.setenv("SURVEYS_ROOT", str(surveys))
    monkeypatch.setenv("FIELD_DATA_ROOT", str(field_data))
    monkeypatch.setenv("WEBODM_URL", "https://webodm.invalid")
    monkeypatch.setenv("WEBODM_USERNAME", "test-user")
    monkeypatch.setenv("WEBODM_PASSWORD", "test-password")
    monkeypatch.setenv("WEBODM_NODE_ID", "1")


def test_storage_settings_and_workspace_root_are_loaded(monkeypatch, tmp_path):
    _set_required_config(monkeypatch, tmp_path)
    workspace = tmp_path / "workspaces"
    monkeypatch.setenv("WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("STORAGE_MIN_FREE_GB", "10")
    monkeypatch.setenv("STORAGE_MIN_FREE_PERCENT", "10")

    config = config_module.load_pipeline_config()

    assert config["paths"]["workspace_root"] == workspace
    assert config["storage"] == {
        "min_free_gb": 10,
        "min_free_percent": 10,
    }


def test_storage_percentage_rejects_values_over_100(monkeypatch, tmp_path):
    _set_required_config(monkeypatch, tmp_path)
    monkeypatch.setenv("STORAGE_MIN_FREE_PERCENT", "101")

    with pytest.raises(ValueError, match="must be <= 100"):
        config_module.load_pipeline_config()
