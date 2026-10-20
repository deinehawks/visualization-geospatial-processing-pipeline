import json
import subprocess
from types import SimpleNamespace

import pytest

from shared.raster_metadata import (
    RasterMetadataError,
    build_raster_manifest_record,
    inspect_raster_metadata,
    update_raster_manifest,
)


def gdalinfo_payload() -> dict:
    return {
        "driverShortName": "GTiff",
        "size": [32628, 23486],
        "coordinateSystem": {
            "wkt": 'GEOGCRS["WGS 84",ID["EPSG",4326]]',
        },
        "geoTransform": [125.7, 0.0000001827337, 0.0, 7.5, 0.0, -0.0000001827337],
        "metadata": {
            "": {"TIFFTAG_SOFTWARE": "ODM 3.5.6", "AREA_OR_POINT": "Area"},
            "IMAGE_STRUCTURE": {"COMPRESSION": "LZW", "INTERLEAVE": "PIXEL"},
        },
        "bands": [
            {"band": 1, "type": "Byte", "colorInterpretation": "Red"},
            {"band": 2, "type": "Byte", "colorInterpretation": "Green"},
            {"band": 3, "type": "Byte", "colorInterpretation": "Blue"},
            {"band": 4, "type": "Byte", "colorInterpretation": "Alpha"},
        ],
    }


def test_inspect_raster_metadata_maps_required_gdal_fields(tmp_path):
    raster = tmp_path / "orthomosaic.tif"
    raster.write_bytes(b"valid test placeholder")
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(gdalinfo_payload()))

    result = inspect_raster_metadata(
        raster,
        gdalinfo_path="fake-gdalinfo",
        command_runner=fake_runner,
    )

    assert calls == [
        (
            ["fake-gdalinfo", "-json", str(raster)],
            {"check": True, "capture_output": True, "text": True},
        )
    ]
    assert result["program_name"] == "ODM 3.5.6"
    assert result["dimensions"] == "32628 x 23486"
    assert result["width_pixels"] == 32628
    assert result["height_pixels"] == 23486
    assert result["band_count"] == 4
    assert result["bits_per_channel"] == 8
    assert result["bits_per_pixel"] == 32
    assert result["compression"] == "LZW"
    assert result["pixel_size_x"] == pytest.approx(0.0000001827337)
    assert result["pixel_size_y"] == pytest.approx(-0.0000001827337)
    assert result["pixel_size_unit"] == "degree"
    assert result["crs"] == "EPSG:4326"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value["metadata"][""].pop("TIFFTAG_SOFTWARE"), "TIFFTAG_SOFTWARE"),
        (lambda value: value.pop("size"), "dimensions"),
        (lambda value: value.pop("bands"), "no readable bands"),
        (lambda value: value.pop("geoTransform"), "pixel size"),
        (lambda value: value["geoTransform"].__setitem__(1, float("nan")), "pixel size"),
        (lambda value: value.pop("coordinateSystem"), "CRS"),
    ],
)
def test_inspect_raster_metadata_fails_closed_on_required_field_loss(
    tmp_path,
    mutation,
    message,
):
    raster = tmp_path / "orthomosaic.tif"
    raster.write_bytes(b"placeholder")
    payload = gdalinfo_payload()
    mutation(payload)

    with pytest.raises(RasterMetadataError, match=message):
        inspect_raster_metadata(
            raster,
            gdalinfo_path="fake-gdalinfo",
            command_runner=lambda *_args, **_kwargs: SimpleNamespace(
                stdout=json.dumps(payload)
            ),
        )


def test_inspect_raster_metadata_preserves_gdal_failure_cause(tmp_path):
    raster = tmp_path / "orthomosaic.tif"
    raster.write_bytes(b"placeholder")
    failure = subprocess.CalledProcessError(
        1,
        ["fake-gdalinfo"],
        stderr="TIFF directory is unreadable",
    )

    def fail(*_args, **_kwargs):
        raise failure

    with pytest.raises(RasterMetadataError, match="TIFF directory is unreadable") as exc:
        inspect_raster_metadata(
            raster,
            gdalinfo_path="fake-gdalinfo",
            command_runner=fail,
        )

    assert exc.value.__cause__ is failure


def test_manifest_update_is_additive_atomic_and_idempotent(tmp_path):
    manifest = tmp_path / "rgb" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "survey_id": "AH-026019",
                "image_count": 123,
                "ignored_files": {".txt": 1},
            }
        ),
        encoding="utf-8",
    )
    published = manifest.parent / "qgis" / "clipped" / "ortho" / "clip.tif"
    record = build_raster_manifest_record(
        {"width_pixels": 10, "height_pixels": 20},
        published_path=published,
        manifest_root=manifest.parent,
        role="qgis_clipped_orthomosaic",
        task_key="task4",
        run_id="run-001",
    )

    for _ in range(2):
        update_raster_manifest(
            manifest,
            survey_id="AH-026019",
            raster_key="qgis_clipped_task4",
            raster_record=record,
        )

    result = json.loads(manifest.read_text(encoding="utf-8"))
    assert result["image_count"] == 123
    assert result["ignored_files"] == {".txt": 1}
    assert list(result["raster_outputs"]) == ["qgis_clipped_task4"]
    assert result["raster_outputs"]["qgis_clipped_task4"]["relative_path"] == (
        "qgis/clipped/ortho/clip.tif"
    )
    assert result["raster_outputs"]["qgis_clipped_task4"]["run_id"] == "run-001"
    assert list(manifest.parent.glob(".manifest.json.*.tmp")) == []


def test_manifest_identity_failure_preserves_original_bytes(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"survey_id":"AH-OTHER","sentinel":true}\n', encoding="utf-8")
    original = manifest.read_bytes()

    with pytest.raises(RasterMetadataError, match="identity"):
        update_raster_manifest(
            manifest,
            survey_id="AH-026019",
            raster_key="webodm_orthomosaic_task4",
            raster_record={"width_pixels": 1},
        )

    assert manifest.read_bytes() == original


def test_manifest_non_finite_value_fails_before_rewrite(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"survey_id":"AH-026019","invalid":NaN}\n',
        encoding="utf-8",
    )
    original = manifest.read_bytes()

    with pytest.raises(RasterMetadataError, match="strict JSON"):
        update_raster_manifest(
            manifest,
            survey_id="AH-026019",
            raster_key="webodm_orthomosaic_task4",
            raster_record={"width_pixels": 1},
        )

    assert manifest.read_bytes() == original
