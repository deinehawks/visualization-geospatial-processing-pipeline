from pathlib import Path
import json

from modules.map_export.boundary_finder import find_boundary_file_by_survey_id
from modules.map_export.orthomosaic_finder import collect_orthomosaic_files


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _prepare_published_survey(tmp_path: Path) -> tuple[Path, Path]:
    surveys_root = tmp_path / "surveys"
    rgb_root = surveys_root / "2026" / "AH-026019" / "rgb"
    _write_json(
        rgb_root / "manifest.json",
        {
            "survey_id": "AH-026019",
            "source_context": {"source_dataset_folder": "AH_026_source"},
            "kml_file": "legacy-boundary.kml",
        },
    )
    return surveys_root, rgb_root


def _write_publication_manifest(rgb_root: Path, artifacts: list[dict]) -> None:
    _write_json(
        rgb_root / "publication.json",
        {
            "manifest_version": 1,
            "status": "published",
            "run_id": "run-active",
            "survey_id": "AH-026019",
            "published_root": str(rgb_root),
            "artifacts": artifacts,
        },
    )


def test_collect_orthomosaic_files_prefers_publication_manifest_artifact(tmp_path):
    surveys_root, rgb_root = _prepare_published_survey(tmp_path)
    legacy_ortho = rgb_root / "qgis" / "clipped" / "ortho" / "orthomosaic-clipped--legacy-t2.tif"
    legacy_ortho.parent.mkdir(parents=True)
    legacy_ortho.write_text("legacy", encoding="utf-8")
    published_ortho = rgb_root / "qgis" / "clipped" / "ortho" / "orthomosaic-clipped--active.tif"
    published_ortho.write_text("active", encoding="utf-8")
    _write_publication_manifest(
        rgb_root,
        [
            {
                "logical_name": "qgis_clipped_orthomosaic",
                "kind": "file",
                "published_relative_path": "qgis/clipped/ortho/orthomosaic-clipped--active.tif",
            }
        ],
    )

    selected = collect_orthomosaic_files(
        surveys_root=surveys_root,
        db_path=None,
        survey_names=["AH_026_source"],
    )

    assert selected == [published_ortho]


def test_collect_orthomosaic_files_falls_back_to_legacy_scan_without_publication_manifest(tmp_path):
    surveys_root, rgb_root = _prepare_published_survey(tmp_path)
    legacy_ortho = rgb_root / "qgis" / "clipped" / "ortho" / "orthomosaic-clipped--legacy-t2.tif"
    legacy_ortho.parent.mkdir(parents=True)
    legacy_ortho.write_text("legacy", encoding="utf-8")

    selected = collect_orthomosaic_files(
        surveys_root=surveys_root,
        db_path=None,
        survey_names=["AH_026_source"],
    )

    assert selected == [legacy_ortho]


def test_collect_orthomosaic_files_falls_back_when_publication_artifact_is_missing(tmp_path):
    surveys_root, rgb_root = _prepare_published_survey(tmp_path)
    legacy_ortho = rgb_root / "qgis" / "clipped" / "ortho" / "orthomosaic-clipped--legacy-t2.tif"
    legacy_ortho.parent.mkdir(parents=True)
    legacy_ortho.write_text("legacy", encoding="utf-8")
    _write_publication_manifest(
        rgb_root,
        [
            {
                "logical_name": "qgis_clipped_orthomosaic",
                "kind": "file",
                "published_relative_path": "qgis/clipped/ortho/missing.tif",
            }
        ],
    )

    selected = collect_orthomosaic_files(
        surveys_root=surveys_root,
        db_path=None,
        survey_names=["AH_026_source"],
    )

    assert selected == [legacy_ortho]


def test_find_boundary_file_by_survey_id_prefers_publication_manifest_kml(tmp_path):
    surveys_root, rgb_root = _prepare_published_survey(tmp_path)
    legacy_boundary = rgb_root / "boundary" / "legacy-boundary.kml"
    legacy_boundary.parent.mkdir(parents=True)
    legacy_boundary.write_text("<kml>legacy</kml>", encoding="utf-8")
    published_boundary = rgb_root / "boundary" / "active-boundary.kml"
    published_boundary.write_text("<kml>active</kml>", encoding="utf-8")
    _write_publication_manifest(
        rgb_root,
        [
            {
                "logical_name": "boundary_kml",
                "kind": "file",
                "published_relative_path": "boundary/active-boundary.kml",
            }
        ],
    )

    selected = find_boundary_file_by_survey_id(
        surveys_root=surveys_root,
        db_path=None,
        survey_name="AH_026_source",
    )

    assert selected.boundary_path == published_boundary
    assert selected.survey_name == "AH-026019"
