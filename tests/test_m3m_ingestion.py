from __future__ import annotations

import builtins
import json
import logging
from pathlib import Path

import pytest

from modules.data_segregation.data_segregation import (
    resolve_source_dataset_dir,
    run,
)
from shared.preflight_checks import PipelinePreflight


DATASET_NAME = "BCO-121_11Ha_M3M_70m_85f75s_5mps"


def _dataset(root: Path, date: str, uav: str) -> Path:
    path = root / date / uav / DATASET_NAME
    path.mkdir(parents=True)
    return path


def _logger(name: str) -> logging.Logger:
    return logging.getLogger(f"tests.m3m.{name}")


def test_uav_filter_selects_exact_case_insensitive_parent(monkeypatch, tmp_path):
    field_root = tmp_path / "field-data"
    expected = _dataset(field_root, "20260826-BARBCO", "M3M_A")
    _dataset(field_root, "20260826-BARBCO", "M3M_B")
    monkeypatch.setenv("FIELD_DATA_ROOT", str(field_root))

    resolved = resolve_source_dataset_dir(
        Path(DATASET_NAME),
        _logger("select"),
        uav_folder="m3m_a",
    )

    assert resolved == expected


def test_uav_filter_accepts_future_folder_names(monkeypatch, tmp_path):
    field_root = tmp_path / "field-data"
    expected = _dataset(field_root, "20260826-BARBCO", "M3M_E")
    monkeypatch.setenv("FIELD_DATA_ROOT", str(field_root))

    assert resolve_source_dataset_dir(
        Path(DATASET_NAME),
        _logger("future"),
        uav_folder="M3M_E",
    ) == expected


def test_uav_filter_reports_matching_dataset_elsewhere(monkeypatch, tmp_path):
    field_root = tmp_path / "field-data"
    elsewhere = _dataset(field_root, "20260826-BARBCO", "M3M_B")
    monkeypatch.setenv("FIELD_DATA_ROOT", str(field_root))

    with pytest.raises(FileNotFoundError) as exc_info:
        resolve_source_dataset_dir(
            Path(DATASET_NAME),
            _logger("elsewhere"),
            uav_folder="M3M_A",
        )

    message = str(exc_info.value)
    assert "UAV folder filter: M3M_A" in message
    assert str(elsewhere) in message


def test_direct_dataset_path_rejects_conflicting_uav(tmp_path):
    source = _dataset(tmp_path, "20260826-BARBCO", "M3M_A")

    with pytest.raises(ValueError, match="not beneath UAV folder 'M3M_B'"):
        resolve_source_dataset_dir(
            source,
            _logger("direct-mismatch"),
            uav_folder="M3M_B",
        )


def test_date_hint_runs_after_uav_filter(monkeypatch, tmp_path):
    field_root = tmp_path / "field-data"
    _dataset(field_root, "20260826-BARBCO", "M3M_A")
    expected = _dataset(field_root, "20260827-BARBCO", "M3M_A")
    _dataset(field_root, "20260827-BARBCO", "M3M_B")
    monkeypatch.setenv("FIELD_DATA_ROOT", str(field_root))

    resolved = resolve_source_dataset_dir(
        Path(DATASET_NAME),
        _logger("date"),
        date_hint="20260827",
        uav_folder="M3M_A",
    )

    assert resolved == expected


def test_duplicate_prompt_shows_only_selected_uav_candidates(
    monkeypatch,
    capsys,
    tmp_path,
):
    field_root = tmp_path / "field-data"
    _dataset(field_root, "20260826-BARBCO", "M3M_A")
    _dataset(field_root, "20260827-BARBCO", "M3M_A")
    excluded = _dataset(field_root, "20260828-BARBCO", "M3M_B")
    monkeypatch.setenv("FIELD_DATA_ROOT", str(field_root))
    monkeypatch.setattr(builtins, "input", lambda _prompt: "1")

    selected = resolve_source_dataset_dir(
        Path(DATASET_NAME),
        _logger("prompt"),
        uav_folder="M3M_A",
    )

    output = capsys.readouterr().out
    assert selected.parent.name == "M3M_A"
    assert "M3M_A" in output
    assert str(excluded) not in output


def test_rgb_mode_combines_arbitrary_nested_split_folders(tmp_path):
    source = _dataset(tmp_path / "field-data", "20260826-BARBCO", "M3M_A")
    for folder in ("1of2", "2of2", "1of10", "10of10", "1of15", "15of15"):
        (source / folder).mkdir()
    selected_names = {
        "DJI_20260826103108_0001_D.JPG",
        "DJI_20260826105139_0001_D.JPG",
        "DJI_20260826110139_0002_d.jpg",
    }
    (source / "1of2" / "DJI_20260826103108_0001_D.JPG").write_bytes(b"rgb-1")
    (source / "2of2" / "DJI_20260826105139_0001_D.JPG").write_bytes(b"rgb-2")
    (source / "15of15" / "DJI_20260826110139_0002_d.jpg").write_bytes(b"rgb-3")
    (source / "1of10" / "ordinary.JPG").write_bytes(b"not-selected")
    (source / "10of10" / "DJI_0003_D.JPEG").write_bytes(b"not-selected")
    (source / "1of15" / "DJI_0004_MS_G.TIF").write_bytes(b"green")
    (source / "15of15" / "DJI_0004_MS_NIR.TIF").write_bytes(b"nir")
    (source / "boundary.kml").write_text("<kml />", encoding="utf-8")

    summary = run(
        source,
        tmp_path / "surveys",
        2026,
        _logger("segregation"),
        survey_id_override="AH-026121",
        rgb_only=True,
    )

    raw = Path(summary["dirs"]["raw"])
    assert {path.name for path in raw.iterdir()} == selected_names
    manifest = json.loads(Path(summary["manifest"]).read_text(encoding="utf-8"))
    assert manifest["image_count"] == 3
    assert manifest["image_selection_mode"] == "dji_rgb_d_jpg"
    assert manifest["excluded_nonmatching_jpeg_count"] == 2
    assert manifest["ignored_files"] == {".jpg": 1, ".jpeg": 1, ".tif": 2}


def test_legacy_mode_keeps_all_jpg_and_jpeg_images(tmp_path):
    source = _dataset(tmp_path / "field-data", "20260826-BARBCO", "M3M_A")
    (source / "ordinary.jpg").write_bytes(b"ordinary")
    (source / "another.JPEG").write_bytes(b"another")
    (source / "DJI_0001_MS_NIR.TIF").write_bytes(b"nir")
    (source / "boundary.kml").write_text("<kml />", encoding="utf-8")

    summary = run(
        source,
        tmp_path / "surveys",
        2026,
        _logger("legacy"),
        survey_id_override="AH-026122",
    )

    raw = Path(summary["dirs"]["raw"])
    assert {path.name for path in raw.iterdir()} == {
        "ordinary.jpg",
        "another.JPEG",
    }
    assert summary["image_selection_mode"] == "all_jpeg"


def test_rgb_mode_rejects_duplicate_flattened_names_before_copy(tmp_path):
    source = _dataset(tmp_path / "field-data", "20260826-BARBCO", "M3M_A")
    (source / "1of2").mkdir()
    (source / "2of2").mkdir()
    (source / "1of2" / "DJI_0001_D.JPG").write_bytes(b"first")
    (source / "2of2" / "dji_0001_d.jpg").write_bytes(b"second")
    (source / "boundary.kml").write_text("<kml />", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate destination filenames"):
        run(
            source,
            tmp_path / "surveys",
            2026,
            _logger("collision"),
            survey_id_override="AH-026123",
            rgb_only=True,
        )

    raw = tmp_path / "surveys" / "2026" / "AH-026123" / "rgb" / "images" / "raw"
    assert list(raw.iterdir()) == []


def test_rgb_mode_requires_matching_d_jpg_image(tmp_path):
    source = _dataset(tmp_path / "field-data", "20260826-BARBCO", "M3M_A")
    (source / "DJI_0001_D.JPEG").write_bytes(b"jpeg")
    (source / "DJI_0001_MS_NIR.TIF").write_bytes(b"nir")
    (source / "boundary.kml").write_text("<kml />", encoding="utf-8")

    with pytest.raises(ValueError, match="ending in _D.JPG"):
        run(
            source,
            tmp_path / "surveys",
            2026,
            _logger("empty"),
            survey_id_override="AH-026124",
            rgb_only=True,
        )


def test_stage_preflight_uses_same_rgb_selection(tmp_path):
    source = _dataset(tmp_path / "field-data", "20260826-BARBCO", "M3M_A")
    (source / "1of15").mkdir()
    (source / "15of15").mkdir()
    (source / "1of15" / "DJI_0001_D.JPG").write_bytes(b"rgb")
    (source / "15of15" / "ordinary.jpg").write_bytes(b"ordinary")
    (source / "15of15" / "DJI_0002_MS_NIR.TIF").write_bytes(b"nir")
    (source / "boundary.kml").write_text("<kml />", encoding="utf-8")

    preflight = PipelinePreflight(
        config={},
        source_dir=source,
        surveys_root=tmp_path / "surveys",
        year=2026,
        rgb_only=True,
    )

    result = preflight.check_stage("data_segregation", state={})

    assert result["source_images"] == 1
    assert result["image_selection_mode"] == "dji_rgb_d_jpg"
