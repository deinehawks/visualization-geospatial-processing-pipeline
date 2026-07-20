import json
from pathlib import Path

import pytest

from shared.artifacts import (
    PublicationArtifact,
    create_run_workspace,
    describe_published_survey,
    describe_run_workspace,
    plan_published_survey,
    plan_published_survey_from_rgb_path,
    plan_run_workspace,
    prepare_publication,
)


def test_run_workspace_paths_are_unique_for_same_survey(tmp_path):
    workspace_root = tmp_path / "workspaces"

    first = plan_run_workspace(workspace_root, "run-001")
    second = plan_run_workspace(workspace_root, "run-002")

    create_run_workspace(first)
    create_run_workspace(second)

    assert first.root != second.root
    assert first.images_raw == workspace_root / "run-001" / "images" / "raw"
    assert second.images_raw == workspace_root / "run-002" / "images" / "raw"
    assert first.qgis_tiles_round.is_dir()
    assert second.qgis_tiles_round.is_dir()


@pytest.mark.parametrize("unsafe_run_id", ["../escape", "nested/run", "", ".", ".."])
def test_run_workspace_rejects_unsafe_run_id(tmp_path, unsafe_run_id):
    with pytest.raises(ValueError, match="run_id"):
        plan_run_workspace(tmp_path / "workspaces", unsafe_run_id)


def test_published_survey_layout_preserves_legacy_paths(tmp_path):
    layout = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")

    assert layout.root == tmp_path / "surveys" / "2026" / "AH-026019" / "rgb"
    assert layout.manifest == layout.root / "manifest.json"
    assert layout.boundary == layout.root / "boundary"
    assert layout.ortho == layout.root / "ortho"
    assert layout.qgis_clipped_ortho == layout.root / "qgis" / "clipped" / "ortho"
    assert layout.tiles_ortho_round == layout.root / "tiles" / "ortho" / "round-corners"
    assert layout.tiles_ortho_soft == layout.root / "tiles" / "ortho" / "soft-corners"


def test_layout_descriptions_are_string_path_mappings(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")

    workspace_description = describe_run_workspace(workspace)
    published_description = describe_published_survey(published)

    assert workspace_description["root"] == str(workspace.root)
    assert workspace_description["images_raw"] == str(workspace.images_raw)
    assert published_description["root"] == str(published.root)
    assert published_description["qgis_clipped_ortho"] == str(
        published.qgis_clipped_ortho
    )
    assert all(isinstance(value, str) for value in workspace_description.values())
    assert all(isinstance(value, str) for value in published_description.values())


def test_published_survey_layout_can_be_derived_from_actual_rgb_path(tmp_path):
    rgb_path = tmp_path / "surveys" / "AH-026019" / "rgb"

    layout = plan_published_survey_from_rgb_path(rgb_path)

    assert layout.root == rgb_path
    assert layout.manifest == rgb_path / "manifest.json"
    assert layout.tiles_ortho_round == rgb_path / "tiles" / "ortho" / "round-corners"


def test_prepare_publication_stages_artifacts_without_touching_published_tree(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    existing_published = published.root / "ortho" / "orthomosaic--old.tif"
    existing_published.parent.mkdir(parents=True)
    existing_published.write_text("previous active artifact", encoding="utf-8")

    orthomosaic = workspace.webodm_ortho / "orthomosaic--xcb-t4.tif"
    orthomosaic.write_text("new ortho", encoding="utf-8")
    tiles = workspace.qgis_tiles_round
    (tiles / "11" / "1").mkdir(parents=True)
    (tiles / "11" / "1" / "1.png").write_bytes(b"tile-one")
    (tiles / "11" / "1" / "2.png").write_bytes(b"tile-two")

    manifest_path = prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                logical_name="orthomosaic",
                source_path=orthomosaic,
                published_relative_path=Path("ortho/orthomosaic--xcb-t4.tif"),
                kind="file",
            ),
            PublicationArtifact(
                logical_name="round_tiles",
                source_path=tiles,
                published_relative_path=Path("tiles/ortho/round-corners"),
                kind="directory",
            ),
        ],
    )

    assert existing_published.read_text(encoding="utf-8") == "previous active artifact"
    staged_root = workspace.publish / "staged"
    assert (staged_root / "ortho" / "orthomosaic--xcb-t4.tif").read_text(
        encoding="utf-8"
    ) == "new ortho"
    assert (staged_root / "tiles" / "ortho" / "round-corners" / "11" / "1" / "1.png").is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "staged"
    assert manifest["run_id"] == "run-001"
    assert manifest["survey_id"] == "AH-026019"
    assert manifest["published_root"] == str(published.root)
    assert [artifact["logical_name"] for artifact in manifest["artifacts"]] == [
        "orthomosaic",
        "round_tiles",
    ]
    assert manifest["artifacts"][0]["size_bytes"] == len("new ortho")
    assert manifest["artifacts"][1]["file_count"] == 2


def test_prepare_publication_failure_leaves_published_tree_and_complete_manifest_untouched(
    tmp_path,
):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    existing_published = published.root / "ortho" / "orthomosaic--active.tif"
    existing_published.parent.mkdir(parents=True)
    existing_published.write_text("active", encoding="utf-8")

    orthomosaic = workspace.webodm_ortho / "orthomosaic--xcb-t4.tif"
    orthomosaic.write_text("new", encoding="utf-8")

    def failing_copy_file(source, destination):
        raise OSError("simulated copy failure")

    with pytest.raises(OSError, match="simulated copy failure"):
        prepare_publication(
            run_id="run-001",
            survey_id="AH-026019",
            workspace=workspace,
            published=published,
            artifacts=[
                PublicationArtifact(
                    logical_name="orthomosaic",
                    source_path=orthomosaic,
                    published_relative_path=Path("ortho/orthomosaic--xcb-t4.tif"),
                    kind="file",
                )
            ],
            copy_file=failing_copy_file,
        )

    assert existing_published.read_text(encoding="utf-8") == "active"
    assert not (workspace.publish / "staged" / "publication.json").exists()


@pytest.mark.parametrize(
    "published_relative_path",
    [Path("../escape.tif"), Path("ortho/../escape.tif"), Path.cwd() / "absolute.tif"],
)
def test_prepare_publication_rejects_paths_that_escape_published_root(
    tmp_path,
    published_relative_path,
):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    orthomosaic = workspace.webodm_ortho / "orthomosaic--xcb-t4.tif"
    orthomosaic.write_text("new", encoding="utf-8")

    with pytest.raises(ValueError, match="Published artifact path"):
        prepare_publication(
            run_id="run-001",
            survey_id="AH-026019",
            workspace=workspace,
            published=published,
            artifacts=[
                PublicationArtifact(
                    logical_name="orthomosaic",
                    source_path=orthomosaic,
                    published_relative_path=published_relative_path,
                    kind="file",
                )
            ],
        )
