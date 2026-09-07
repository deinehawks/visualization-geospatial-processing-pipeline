import json
import shutil
from pathlib import Path

import pytest

import shared.artifacts as artifacts_module
from shared.artifacts import (
    ARTIFACT_CLEANUP_AUDIT_DIR,
    ARTIFACT_CLEANUP_SENTINEL_NAME,
    RUN_WORKSPACE_OWNERSHIP_NAME,
    PublicationArtifact,
    activate_publication,
    activate_publication_with_lock,
    activate_publication_set_with_lock,
    create_run_workspace,
    cleanup_completed_run_workspace,
    describe_published_survey,
    describe_run_workspace,
    execute_artifact_cleanup,
    plan_published_survey,
    plan_published_survey_from_rgb_path,
    plan_run_workspace,
    plan_artifact_cleanup,
    publication_activation_path,
    reconcile_directory_publication,
    reconcile_publication_set,
    prepare_publication,
)
from tools.artifact_cleanup import main as cleanup_cli_main
from tools.publication_activate import main as publication_activate_cli_main

from shared.publication_lock import (
    PUBLICATION_LOCK_NAME,
    PublicationLockedError,
    acquire_publication_lock,
    release_publication_lock,
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
    ownership = json.loads(
        (first.root / RUN_WORKSPACE_OWNERSHIP_NAME).read_text(encoding="utf-8")
    )
    assert ownership["run_id"] == "run-001"
    assert Path(ownership["workspace_root"]) == workspace_root


def _completed_run_cleanup_fixture(tmp_path):
    workspace_root = tmp_path / "workspaces"
    workspace = plan_run_workspace(workspace_root, "run-001")
    create_run_workspace(workspace)
    workspace_output = workspace.images_path / "kept.jpg"
    workspace_output.write_bytes(b"kept image")

    other_workspace = plan_run_workspace(workspace_root, "run-002")
    create_run_workspace(other_workspace)
    (other_workspace.images_path / "other.jpg").write_bytes(b"other run")

    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    published_output = published.root / "images" / "path" / "kept.jpg"
    published_output.parent.mkdir(parents=True)
    published_output.write_bytes(b"kept image")
    return (
        workspace_root,
        workspace,
        workspace_output,
        other_workspace,
        published,
        published_output,
    )


def test_completed_run_cleanup_archives_evidence_then_deletes_only_owned_run(tmp_path):
    (
        workspace_root,
        workspace,
        workspace_output,
        other_workspace,
        published,
        published_output,
    ) = _completed_run_cleanup_fixture(tmp_path)

    result = cleanup_completed_run_workspace(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        workspace_root=workspace_root,
        published=published,
        output_pairs=[(workspace_output, published_output)],
        stage_summaries={"cross_run_filter": {"total_kept": 1}},
        image_classifications=[
            {
                "relative_path": "kept.jpg",
                "disposition": "kept",
                "reasons": [],
            }
        ],
    )

    assert result["status"] == "completed"
    assert not workspace.root.exists()
    assert other_workspace.root.is_dir()
    assert published_output.is_file()
    audit = json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))
    assert audit["status"] == "completed"
    assert audit["run_id"] == "run-001"
    assert audit["file_count"] >= 2
    assert audit["total_bytes"] >= len(b"kept image")
    assert audit["image_classifications"][0]["relative_path"] == "kept.jpg"
    assert audit["verified_outputs"][0]["published_relative_path"] == (
        "images/path/kept.jpg"
    )


def test_completed_run_cleanup_rejects_preexisting_workspace_without_ownership(tmp_path):
    workspace_root = tmp_path / "workspaces"
    workspace = plan_run_workspace(workspace_root, "run-001")
    workspace.root.mkdir(parents=True)
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    published.root.mkdir(parents=True)

    with pytest.raises(ValueError, match="ownership evidence is missing"):
        cleanup_completed_run_workspace(
            run_id="run-001",
            survey_id="AH-026019",
            workspace=workspace,
            workspace_root=workspace_root,
            published=published,
            output_pairs=[],
            stage_summaries={},
        )

    assert workspace.root.is_dir()


def test_completed_run_cleanup_requires_verified_published_output(tmp_path):
    (
        workspace_root,
        workspace,
        workspace_output,
        _,
        published,
        published_output,
    ) = _completed_run_cleanup_fixture(tmp_path)
    published_output.unlink()

    with pytest.raises(ValueError, match="Required published file is missing"):
        cleanup_completed_run_workspace(
            run_id="run-001",
            survey_id="AH-026019",
            workspace=workspace,
            workspace_root=workspace_root,
            published=published,
            output_pairs=[(workspace_output, published_output)],
            stage_summaries={},
        )

    assert workspace.root.is_dir()


def test_completed_run_cleanup_audit_failure_preserves_workspace(tmp_path):
    (
        workspace_root,
        workspace,
        workspace_output,
        _,
        published,
        published_output,
    ) = _completed_run_cleanup_fixture(tmp_path)

    def fail_audit(path, payload):
        raise OSError("controlled audit failure")

    with pytest.raises(OSError, match="controlled audit failure"):
        cleanup_completed_run_workspace(
            run_id="run-001",
            survey_id="AH-026019",
            workspace=workspace,
            workspace_root=workspace_root,
            published=published,
            output_pairs=[(workspace_output, published_output)],
            stage_summaries={},
            write_json=fail_audit,
        )

    assert workspace.root.is_dir()


def test_completed_run_cleanup_delete_failure_keeps_audit_and_reports_failure(tmp_path):
    (
        workspace_root,
        workspace,
        workspace_output,
        _,
        published,
        published_output,
    ) = _completed_run_cleanup_fixture(tmp_path)

    def fail_delete(path):
        raise OSError("controlled delete failure")

    result = cleanup_completed_run_workspace(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        workspace_root=workspace_root,
        published=published,
        output_pairs=[(workspace_output, published_output)],
        stage_summaries={},
        remove_tree=fail_delete,
    )

    assert result["status"] == "failed"
    assert workspace.root.is_dir()
    audit = json.loads(Path(result["audit_path"]).read_text(encoding="utf-8"))
    assert audit["status"] == "failed"
    assert audit["error_type"] == "OSError"


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


def test_activate_publication_writes_files_then_published_manifest(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    target = published.ortho / "orthomosaic--xcb-t4.tif"
    target.parent.mkdir(parents=True)
    target.write_text("previous active artifact", encoding="utf-8")
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "previous"}\n', encoding="utf-8"
    )

    source = workspace.webodm_ortho / target.name
    source.write_text("new active artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                logical_name="orthomosaic",
                source_path=source,
                published_relative_path=Path("ortho") / target.name,
                kind="file",
            )
        ],
    )

    replacements = []

    def recording_replace(source_path, destination_path):
        replacements.append((source_path, destination_path))
        source_path.replace(destination_path)

    manifest_path = activate_publication(
        workspace=workspace,
        published=published,
        replace_path=recording_replace,
    )

    assert manifest_path == published.publication_manifest.resolve(strict=False)
    assert target.read_text(encoding="utf-8") == "new active artifact"
    previous = target.with_name(f".{target.name}.run-001.previous")
    assert previous.read_text(encoding="utf-8") == "previous active artifact"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "published"
    assert manifest["run_id"] == "run-001"
    assert manifest["artifacts"][0]["published_path"] == str(target.resolve())
    assert manifest["artifacts"][0]["previous_published_path"] == str(previous.resolve())
    previous_manifest = published.publication_manifest.with_name(
        ".publication.json.run-001.previous"
    )
    assert json.loads(previous_manifest.read_text())["run_id"] == "previous"
    assert manifest["previous_publication_manifest"] == str(previous_manifest.resolve())
    assert replacements[-1][1] == published.publication_manifest.resolve(strict=False)
    assert not target.with_name(f".{target.name}.run-001.publishing").exists()


def test_activate_publication_copy_failure_leaves_published_state_untouched(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    first_target = published.ortho / "first.tif"
    second_target = published.ortho / "second.tif"
    first_target.parent.mkdir(parents=True)
    first_target.write_text("old first", encoding="utf-8")
    second_target.write_text("old second", encoding="utf-8")
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "old"}\n', encoding="utf-8"
    )

    first_source = workspace.webodm_ortho / first_target.name
    second_source = workspace.webodm_ortho / second_target.name
    first_source.write_text("new first", encoding="utf-8")
    second_source.write_text("new second", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact("first", first_source, Path("ortho/first.tif"), "file"),
            PublicationArtifact("second", second_source, Path("ortho/second.tif"), "file"),
        ],
    )

    copy_count = 0

    def fail_second_copy(source, destination):
        nonlocal copy_count
        copy_count += 1
        if copy_count == 2:
            raise OSError("simulated activation copy failure")
        destination.write_bytes(source.read_bytes())

    with pytest.raises(OSError, match="simulated activation copy failure"):
        activate_publication(
            workspace=workspace,
            published=published,
            copy_file=fail_second_copy,
        )

    assert first_target.read_text(encoding="utf-8") == "old first"
    assert second_target.read_text(encoding="utf-8") == "old second"
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "old"
    assert not first_target.with_name(".first.tif.run-001.publishing").exists()


def test_activate_publication_replace_failure_rolls_back_published_files(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    first_target = published.ortho / "first.tif"
    second_target = published.ortho / "second.tif"
    first_target.parent.mkdir(parents=True)
    first_target.write_text("old first", encoding="utf-8")
    second_target.write_text("old second", encoding="utf-8")

    first_source = workspace.webodm_ortho / first_target.name
    second_source = workspace.webodm_ortho / second_target.name
    first_source.write_text("new first", encoding="utf-8")
    second_source.write_text("new second", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact("first", first_source, Path("ortho/first.tif"), "file"),
            PublicationArtifact("second", second_source, Path("ortho/second.tif"), "file"),
        ],
    )

    replace_count = 0

    def fail_fourth_replace(source, destination):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 4:
            raise OSError("simulated activation replace failure")
        source.replace(destination)

    with pytest.raises(OSError, match="simulated activation replace failure"):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=fail_fourth_replace,
        )

    assert first_target.read_text(encoding="utf-8") == "old first"
    assert second_target.read_text(encoding="utf-8") == "old second"
    assert not published.publication_manifest.exists()

    manifest_path = activate_publication(workspace=workspace, published=published)

    assert json.loads(manifest_path.read_text())["run_id"] == "run-001"
    assert first_target.read_text(encoding="utf-8") == "new first"
    assert second_target.read_text(encoding="utf-8") == "new second"


def test_activate_publication_activates_external_staged_directory(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    (workspace.qgis_tiles_round / "metadata.json").write_text("metadata")
    (workspace.qgis_tiles_round / "11").mkdir()
    (workspace.qgis_tiles_round / "11" / "tile.png").write_bytes(b"tile")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "round_tiles",
                workspace.qgis_tiles_round,
                Path("tiles/ortho/round-corners"),
                "directory",
                required_paths=("metadata.json", "11"),
            )
        ],
    )
    copy_calls = []

    def recording_copytree(source, destination):
        copy_calls.append((source, destination))
        return shutil.copytree(source, destination)

    manifest_path = activate_publication(
        workspace=workspace,
        published=published,
        copy_tree=recording_copytree,
    )

    assert len(copy_calls) == 1
    assert (published.tiles_ortho_round / "11" / "tile.png").read_bytes() == b"tile"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["artifacts"][0]["zero_copy_activation"] is False
    journal = json.loads(
        (
            published.tiles_ortho_round.parent
            / ".activation"
            / "run-001"
            / "activation.json"
        ).read_text()
    )
    assert journal["status"] == "committed"
    assert journal["expected_file_count"] == 2
    assert journal["expected_total_bytes"] == 12

def test_activate_publication_rejects_mixed_set_without_changing_active_manifest(
    tmp_path,
):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source_file = tmp_path / "source" / "orthomosaic.tif"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"new-ortho")
    source_tiles = tmp_path / "source" / "tiles"
    (source_tiles / "11" / "0").mkdir(parents=True)
    (source_tiles / "11" / "0" / "tile.png").write_bytes(b"new-tile")
    published.root.mkdir(parents=True)
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "old-run"}\n',
        encoding="utf-8",
    )
    original_manifest = published.publication_manifest.read_bytes()

    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source_file,
                Path("ortho/orthomosaic.tif"),
                "file",
            ),
            PublicationArtifact(
                "tiles",
                source_tiles,
                Path("tiles/ortho/round-corners"),
                "directory",
            ),
        ],
    )

    with pytest.raises(ValueError, match="no mixed artifacts"):
        activate_publication(workspace=workspace, published=published)

    assert published.publication_manifest.read_bytes() == original_manifest
    assert not (published.root / "ortho" / "orthomosaic.tif").exists()
    assert not published.tiles_ortho_round.exists()

def test_plan_artifact_cleanup_reports_only_terminal_unprotected_evidence(tmp_path):
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    published.root.mkdir(parents=True)
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "active-run"}\n',
        encoding="utf-8",
    )
    old_run = "old-run"
    old_activation = published.root / ".activation" / old_run
    old_activation.mkdir(parents=True)
    previous_manifest = published.root / f".publication.json.{old_run}.previous"
    previous_manifest.write_text("old manifest", encoding="utf-8")
    previous_file = published.ortho / f".orthomosaic.tif.{old_run}.previous"
    previous_file.parent.mkdir(parents=True)
    previous_file.write_text("old file", encoding="utf-8")
    previous_tiles = published.tiles_ortho_round.parent / ".previous" / f"round-corners.{old_run}"
    (previous_tiles / "11").mkdir(parents=True)
    (previous_tiles / "11" / "tile.png").write_bytes(b"old tile")
    journal = {
        "journal_version": 1,
        "run_id": old_run,
        "survey_id": "AH-026019",
        "output_type": "publication_set",
        "status": "committed",
        "previous_publication_manifest": str(previous_manifest),
        "artifacts": [
            {
                "kind": "file",
                "previous": str(previous_file),
            },
            {
                "kind": "directory",
                "previous": str(previous_tiles),
            },
        ],
    }
    (old_activation / "publication-set.json").write_text(
        json.dumps(journal),
        encoding="utf-8",
    )
    workspace_root = tmp_path / "workspaces"
    old_workspace = workspace_root / old_run
    active_workspace = workspace_root / "active-run"
    keep_workspace = workspace_root / "keep-run"
    old_workspace.mkdir(parents=True)
    active_workspace.mkdir()
    keep_workspace.mkdir()
    for path in [old_activation, previous_manifest, previous_file, previous_tiles, old_workspace]:
        artifacts_module.os.utime(path, (100.0, 100.0))

    plan = plan_artifact_cleanup(
        published=published,
        workspace_root=workspace_root,
        preserve_run_ids=("keep-run",),
        min_age_seconds=50,
        now=200,
    )

    candidate_paths = {candidate.path for candidate in plan.candidates}
    assert old_activation.resolve() in candidate_paths
    assert previous_manifest.resolve() in candidate_paths
    assert previous_file.resolve() in candidate_paths
    assert previous_tiles.resolve() in candidate_paths
    assert old_workspace.resolve() in candidate_paths
    assert active_workspace.resolve() not in candidate_paths
    assert keep_workspace.resolve() not in candidate_paths
    assert plan.protected_run_ids == ("active-run", "keep-run")
    assert plan.blocked_reasons == ()


def test_plan_artifact_cleanup_blocks_when_publication_lock_exists(tmp_path):
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    published.root.mkdir(parents=True)
    (published.root / PUBLICATION_LOCK_NAME).write_text("locked", encoding="utf-8")
    old_activation = published.root / ".activation" / "old-run"
    old_activation.mkdir(parents=True)

    plan = plan_artifact_cleanup(published=published)

    assert plan.candidates == ()
    assert len(plan.blocked_reasons) == 1
    assert "Publication lock exists" in plan.blocked_reasons[0]


def test_plan_artifact_cleanup_blocks_nonterminal_journal_without_candidates(tmp_path):
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    run_id = "old-run"
    activation = published.root / ".activation" / run_id
    activation.mkdir(parents=True)
    (activation / "publication-set.json").write_text(
        json.dumps(
            {
                "journal_version": 1,
                "run_id": run_id,
                "survey_id": "AH-026019",
                "output_type": "publication_set",
                "status": "activated",
            }
        ),
        encoding="utf-8",
    )
    artifacts_module.os.utime(activation, (100.0, 100.0))

    plan = plan_artifact_cleanup(published=published, min_age_seconds=50, now=200)

    assert plan.candidates == ()
    assert len(plan.blocked_reasons) == 1
    assert "not terminal" in plan.blocked_reasons[0]


def test_plan_artifact_cleanup_blocks_mixed_terminal_and_nonterminal_evidence(tmp_path):
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    run_id = "old-run"
    activation = published.root / ".activation" / run_id
    activation.mkdir(parents=True)
    previous_manifest = published.root / f".publication.json.{run_id}.previous"
    previous_manifest.write_text("old manifest", encoding="utf-8")
    (activation / "publication-set.json").write_text(
        json.dumps(
            {
                "journal_version": 1,
                "run_id": run_id,
                "survey_id": "AH-026019",
                "output_type": "publication_set",
                "status": "committed",
                "previous_publication_manifest": str(previous_manifest),
            }
        ),
        encoding="utf-8",
    )
    (activation / "activation.json").write_text(
        json.dumps(
            {
                "journal_version": 1,
                "run_id": run_id,
                "survey_id": "AH-026019",
                "output_type": "tiles",
                "status": "activated",
            }
        ),
        encoding="utf-8",
    )
    artifacts_module.os.utime(activation, (100.0, 100.0))
    artifacts_module.os.utime(previous_manifest, (100.0, 100.0))

    plan = plan_artifact_cleanup(published=published, min_age_seconds=50, now=200)

    assert plan.candidates == ()
    assert len(plan.blocked_reasons) == 1
    assert "not terminal" in plan.blocked_reasons[0]

def test_plan_artifact_cleanup_respects_minimum_age(tmp_path):
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    run_id = "old-run"
    activation = published.root / ".activation" / run_id
    activation.mkdir(parents=True)
    (activation / "publication-set.json").write_text(
        json.dumps(
            {
                "journal_version": 1,
                "run_id": run_id,
                "survey_id": "AH-026019",
                "output_type": "publication_set",
                "status": "committed",
            }
        ),
        encoding="utf-8",
    )
    artifacts_module.os.utime(activation, (190.0, 190.0))

    plan = plan_artifact_cleanup(published=published, min_age_seconds=50, now=200)

    assert plan.candidates == ()
    assert plan.blocked_reasons == ()


def _prepared_cleanup_plan(tmp_path):
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    published.root.mkdir(parents=True)
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "active-run"}\n',
        encoding="utf-8",
    )
    run_id = "old-run"
    activation = published.root / ".activation" / run_id
    activation.mkdir(parents=True)
    previous_manifest = published.root / f".publication.json.{run_id}.previous"
    previous_manifest.write_text("old manifest", encoding="utf-8")
    previous_file = published.ortho / f".orthomosaic.tif.{run_id}.previous"
    previous_file.parent.mkdir(parents=True)
    previous_file.write_text("old file", encoding="utf-8")
    (activation / "publication-set.json").write_text(
        json.dumps(
            {
                "journal_version": 1,
                "run_id": run_id,
                "survey_id": "AH-026019",
                "output_type": "publication_set",
                "status": "committed",
                "previous_publication_manifest": str(previous_manifest),
                "artifacts": [
                    {
                        "kind": "file",
                        "previous": str(previous_file),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    workspace_root = tmp_path / "workspaces"
    old_workspace = workspace_root / run_id
    old_workspace.mkdir(parents=True)
    for path in [activation, previous_manifest, previous_file, old_workspace]:
        artifacts_module.os.utime(path, (100.0, 100.0))
    plan = plan_artifact_cleanup(
        published=published,
        workspace_root=workspace_root,
        min_age_seconds=50,
        now=200,
    )
    assert plan.blocked_reasons == ()
    assert len(plan.candidates) == 4
    return published, workspace_root, plan


def test_execute_artifact_cleanup_dry_run_deletes_nothing_without_sentinels(tmp_path):
    published, workspace_root, plan = _prepared_cleanup_plan(tmp_path)

    result = execute_artifact_cleanup(
        plan=plan,
        published=published,
        workspace_root=workspace_root,
        min_age_seconds=50,
        now=200,
    )

    assert result.dry_run is True
    assert result.deleted == ()
    assert result.skipped == plan.candidates
    assert result.blocked_reasons == ()
    assert not (published.root / ARTIFACT_CLEANUP_AUDIT_DIR).exists()
    assert all(candidate.path.exists() for candidate in plan.candidates)


def test_execute_artifact_cleanup_requires_owner_sentinels_before_delete(tmp_path):
    published, workspace_root, plan = _prepared_cleanup_plan(tmp_path)

    result = execute_artifact_cleanup(
        plan=plan,
        published=published,
        workspace_root=workspace_root,
        min_age_seconds=50,
        now=200,
        allow_delete=True,
    )

    assert result.dry_run is False
    assert result.deleted == ()
    assert len(result.blocked_reasons) == 2
    assert all("missing sentinel" in reason for reason in result.blocked_reasons)
    assert all(candidate.path.exists() for candidate in plan.candidates)
    assert not (published.root / ARTIFACT_CLEANUP_AUDIT_DIR).exists()


def test_execute_artifact_cleanup_blocks_when_fresh_plan_changes(tmp_path):
    published, workspace_root, plan = _prepared_cleanup_plan(tmp_path)
    (published.root / ARTIFACT_CLEANUP_SENTINEL_NAME).write_text(
        "owned by test", encoding="utf-8"
    )
    (workspace_root / ARTIFACT_CLEANUP_SENTINEL_NAME).write_text(
        "owned by test", encoding="utf-8"
    )
    (published.root / PUBLICATION_LOCK_NAME).write_text("locked", encoding="utf-8")

    result = execute_artifact_cleanup(
        plan=plan,
        published=published,
        workspace_root=workspace_root,
        min_age_seconds=50,
        now=200,
        allow_delete=True,
    )

    assert result.deleted == ()
    assert any("Publication lock exists" in reason for reason in result.blocked_reasons)
    assert any("Cleanup plan changed" in reason for reason in result.blocked_reasons)
    assert all(candidate.path.exists() for candidate in plan.candidates)


def test_execute_artifact_cleanup_deletes_candidates_and_writes_audit(tmp_path):
    published, workspace_root, plan = _prepared_cleanup_plan(tmp_path)
    published_sentinel = published.root / ARTIFACT_CLEANUP_SENTINEL_NAME
    workspace_sentinel = workspace_root / ARTIFACT_CLEANUP_SENTINEL_NAME
    published_sentinel.write_text("owned by test", encoding="utf-8")
    workspace_sentinel.write_text("owned by test", encoding="utf-8")

    result = execute_artifact_cleanup(
        plan=plan,
        published=published,
        workspace_root=workspace_root,
        min_age_seconds=50,
        now=200,
        allow_delete=True,
        cleanup_id="cleanup-001",
    )

    assert result.dry_run is False
    assert len(result.deleted) == len(plan.candidates)
    assert result.skipped == ()
    assert result.blocked_reasons == ()
    assert result.audit_path == published.root / ARTIFACT_CLEANUP_AUDIT_DIR / "cleanup-001.json"
    assert not any(candidate.path.exists() for candidate in plan.candidates)
    assert published_sentinel.is_file()
    assert workspace_sentinel.is_file()
    audit = json.loads(result.audit_path.read_text(encoding="utf-8"))
    assert audit["status"] == "completed"
    assert len(audit["candidates"]) == len(plan.candidates)
    assert len(audit["deleted"]) == len(plan.candidates)
    assert all(attempt["status"] == "deleted" for attempt in audit["attempts"])

def test_artifact_cleanup_cli_plans_requires_ack_and_executes(tmp_path, capsys):
    published, workspace_root, plan = _prepared_cleanup_plan(tmp_path)

    assert cleanup_cli_main(
        [
            "plan",
            "--published-root", str(published.root),
            "--workspace-root", str(workspace_root),
            "--min-age-seconds", "50",
        ]
    ) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "planned"
    assert len(planned["candidates"]) == len(plan.candidates)
    assert all(candidate.path.exists() for candidate in plan.candidates)

    execute_args = [
        "execute",
        "--published-root", str(published.root),
        "--workspace-root", str(workspace_root),
        "--min-age-seconds", "50",
        "--cleanup-id", "cli-cleanup-001",
    ]
    assert cleanup_cli_main(execute_args) == 2
    missing_ack = json.loads(capsys.readouterr().out)
    assert "--allow-delete" in missing_ack["error"]
    assert all(candidate.path.exists() for candidate in plan.candidates)

    (published.root / ARTIFACT_CLEANUP_SENTINEL_NAME).write_text(
        "owned by test", encoding="utf-8"
    )
    (workspace_root / ARTIFACT_CLEANUP_SENTINEL_NAME).write_text(
        "owned by test", encoding="utf-8"
    )
    assert cleanup_cli_main(execute_args + ["--allow-delete"]) == 0
    executed = json.loads(capsys.readouterr().out)
    assert executed["status"] == "deleted"
    assert len(executed["deleted"]) == len(plan.candidates)
    assert Path(executed["audit_path"]).is_file()
    assert not any(candidate.path.exists() for candidate in plan.candidates)


def test_publication_activate_cli_requires_ack_and_confirmation(tmp_path, capsys):
    workspace, published = _prepare_mixed_publication_set(tmp_path)
    args = [
        "--workspace-root", str(workspace.root),
        "--published-root", str(published.root),
        "--confirmation", "PUBLISH AH-026019 run-001",
    ]

    assert publication_activate_cli_main(args) == 2
    missing_ack = json.loads(capsys.readouterr().out)
    assert "--allow-activation" in missing_ack["error"]
    assert not published.publication_manifest.exists()

    assert publication_activate_cli_main(
        args[:-1] + ["wrong", "--allow-activation"]
    ) == 2
    wrong_confirmation = json.loads(capsys.readouterr().out)
    assert "confirmation must exactly match" in wrong_confirmation["error"]
    assert not published.publication_manifest.exists()


def test_publication_activate_cli_activates_staged_mixed_set(tmp_path, capsys):
    workspace, published = _prepare_mixed_publication_set(tmp_path)

    assert publication_activate_cli_main(
        [
            "--workspace-root", str(workspace.root),
            "--published-root", str(published.root),
            "--confirmation", "PUBLISH AH-026019 run-001",
            "--allow-activation",
        ]
    ) == 0

    activated = json.loads(capsys.readouterr().out)
    assert activated["status"] == "activated"
    assert activated["run_id"] == "run-001"
    assert activated["survey_id"] == "AH-026019"
    assert activated["publication_manifest"] == str(published.publication_manifest)
    assert published.publication_manifest.exists()
    assert (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8") == "new-ortho"
    assert (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes() == b"new-tile"
    assert not (published.root / PUBLICATION_LOCK_NAME).exists()

def _prepare_mixed_publication_set(tmp_path, *, existing_final=False):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    ortho_source = workspace.webodm_ortho / "orthomosaic.tif"
    ortho_source.write_text("new-ortho", encoding="utf-8")
    tiles_source = workspace.qgis_tiles_round
    (tiles_source / "metadata.json").write_text("meta", encoding="utf-8")
    (tiles_source / "11" / "0").mkdir(parents=True)
    (tiles_source / "11" / "0" / "tile.png").write_bytes(b"new-tile")
    if existing_final:
        (published.ortho).mkdir(parents=True)
        (published.ortho / "orthomosaic.tif").write_text("old-ortho", encoding="utf-8")
        (published.tiles_ortho_round / "11" / "0").mkdir(parents=True)
        (published.tiles_ortho_round / "metadata.json").write_text(
            "old-meta", encoding="utf-8"
        )
        (published.tiles_ortho_round / "11" / "0" / "tile.png").write_bytes(
            b"old-tile"
        )
        published.publication_manifest.write_text(
            '{"status": "published", "run_id": "old-run"}\n',
            encoding="utf-8",
        )
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                ortho_source,
                Path("ortho/orthomosaic.tif"),
                "file",
            ),
            PublicationArtifact(
                "round_tiles",
                tiles_source,
                Path("tiles/ortho/round-corners"),
                "directory",
                required_paths=("metadata.json", "11"),
            ),
        ],
    )
    return workspace, published


def test_activate_publication_set_with_lock_publishes_mixed_artifacts_once(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path)

    manifest_path = activate_publication_set_with_lock(
        workspace=workspace,
        published=published,
        owner_token="owner-001",
        created_at="2026-07-27T10:00:00+00:00",
    )

    assert manifest_path == published.publication_manifest.resolve(strict=False)
    assert (
        (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8")
        == "new-ortho"
    )
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"new-tile"
    )
    assert not (published.root / PUBLICATION_LOCK_NAME).exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "published"
    assert manifest["run_id"] == "run-001"
    assert [record["kind"] for record in manifest["artifacts"]] == [
        "file",
        "directory",
    ]
    assert Path(manifest["activation_journal"]).is_file()
    journal = json.loads(
        Path(manifest["activation_journal"]).read_text(encoding="utf-8")
    )
    assert journal["status"] == "committed"
    assert journal["output_type"] == "publication_set"


def test_activate_publication_set_with_lock_rolls_back_mixed_targets_before_manifest_commit(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path, existing_final=True)
    replace_calls = 0

    def fail_before_manifest_commit(source_path, destination_path):
        nonlocal replace_calls
        replace_calls += 1
        if destination_path == published.publication_manifest.resolve(strict=False):
            raise OSError("simulated manifest switch failure")
        source_path.replace(destination_path)

    with pytest.raises(OSError, match="simulated manifest switch failure"):
        activate_publication_set_with_lock(
            workspace=workspace,
            published=published,
            replace_path=fail_before_manifest_commit,
            owner_token="owner-001",
        )

    assert replace_calls > 0
    assert (
        (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8")
        == "old-ortho"
    )
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert (
        json.loads(published.publication_manifest.read_text(encoding="utf-8"))["run_id"]
        == "old-run"
    )
    assert not (published.root / PUBLICATION_LOCK_NAME).exists()
    journal = json.loads(
        (published.root / ".activation" / "run-001" / "publication-set.json").read_text(
            encoding="utf-8"
        )
    )
    assert journal["status"] == "rolled_back"


def test_activate_publication_set_with_lock_is_idempotent_after_commit(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path)
    manifest_path = activate_publication_set_with_lock(
        workspace=workspace,
        published=published,
        owner_token="owner-001",
    )
    original_manifest = manifest_path.read_bytes()

    def unexpected_copy(source_path, destination_path):
        pytest.fail(f"Idempotent mixed publication attempted copy: {source_path}")

    repeated = activate_publication_set_with_lock(
        workspace=workspace,
        published=published,
        copy_file=unexpected_copy,
        copy_tree=unexpected_copy,
        replace_path=unexpected_copy,
        owner_token="owner-002",
    )

    assert repeated == manifest_path
    assert repeated.read_bytes() == original_manifest
    assert not (published.root / PUBLICATION_LOCK_NAME).exists()


def test_activate_publication_set_with_lock_fails_before_mutation_when_locked(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path)
    competing_lock = acquire_publication_lock(
        published_root=published.root,
        run_id="run-002",
        survey_id="AH-026019",
        owner_token="owner-002",
    )

    def unexpected_operation(source_path, destination_path):
        pytest.fail("mixed activation should not start while another owner holds the lock")

    try:
        with pytest.raises(PublicationLockedError, match="already locked"):
            activate_publication_set_with_lock(
                workspace=workspace,
                published=published,
                copy_file=unexpected_operation,
                copy_tree=unexpected_operation,
                replace_path=unexpected_operation,
                owner_token="owner-001",
            )
    finally:
        release_publication_lock(competing_lock)

    assert not published.publication_manifest.exists()
    assert not (published.ortho / "orthomosaic.tif").exists()
    assert not published.tiles_ortho_round.exists()


def test_reconcile_publication_set_rolls_back_prepared_partial_activation(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path, existing_final=True)

    class SimulatedCrash(BaseException):
        pass

    replace_calls = 0

    def crash_after_first_candidate_install(source_path, destination_path):
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 3:
            source_path.replace(destination_path)
            raise SimulatedCrash("simulated crash after first mixed artifact install")
        source_path.replace(destination_path)

    with pytest.raises(SimulatedCrash, match="first mixed artifact"):
        activate_publication_set_with_lock(
            workspace=workspace,
            published=published,
            replace_path=crash_after_first_candidate_install,
            owner_token="owner-001",
        )

    assert json.loads(published.publication_manifest.read_text(encoding="utf-8"))["run_id"] == "old-run"
    assert (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8") == "new-ortho"

    journal_path = reconcile_publication_set(workspace=workspace, published=published)

    assert (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8") == "old-ortho"
    assert (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes() == b"old-tile"
    assert json.loads(published.publication_manifest.read_text(encoding="utf-8"))["run_id"] == "old-run"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"
    assert journal["recovered_from_status"] == "prepared"


def test_reconcile_publication_set_rolls_back_activated_without_manifest_commit(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path, existing_final=True)

    class SimulatedCrash(BaseException):
        pass

    def crash_before_manifest_switch(source_path, destination_path):
        if destination_path == published.publication_manifest.resolve(strict=False):
            raise SimulatedCrash("simulated crash before mixed manifest switch")
        source_path.replace(destination_path)

    with pytest.raises(SimulatedCrash, match="mixed manifest switch"):
        activate_publication_set_with_lock(
            workspace=workspace,
            published=published,
            replace_path=crash_before_manifest_switch,
            owner_token="owner-001",
        )

    assert json.loads(published.publication_manifest.read_text(encoding="utf-8"))["run_id"] == "old-run"
    assert (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8") == "new-ortho"
    assert (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes() == b"new-tile"

    journal_path = reconcile_publication_set(workspace=workspace, published=published)

    assert (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8") == "old-ortho"
    assert (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes() == b"old-tile"
    assert json.loads(published.publication_manifest.read_text(encoding="utf-8"))["run_id"] == "old-run"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"
    assert journal["recovered_from_status"] == "activated"


def test_reconcile_publication_set_finalizes_manifest_committed_before_journal(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path, existing_final=True)

    class SimulatedCrash(BaseException):
        pass

    def crash_after_manifest_switch(source_path, destination_path):
        source_path.replace(destination_path)
        if destination_path == published.publication_manifest.resolve(strict=False):
            raise SimulatedCrash("simulated crash after mixed manifest switch")

    with pytest.raises(SimulatedCrash, match="after mixed manifest switch"):
        activate_publication_set_with_lock(
            workspace=workspace,
            published=published,
            replace_path=crash_after_manifest_switch,
            owner_token="owner-001",
        )

    assert json.loads(published.publication_manifest.read_text(encoding="utf-8"))["run_id"] == "run-001"
    assert (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8") == "new-ortho"

    journal_path = reconcile_publication_set(workspace=workspace, published=published)

    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["status"] == "committed"
    assert journal["recovered_from_status"] == "activated"
    assert (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes() == b"new-tile"


def test_reconcile_publication_set_fails_closed_when_active_manifest_changed(tmp_path):
    workspace, published = _prepare_mixed_publication_set(tmp_path, existing_final=True)

    class SimulatedCrash(BaseException):
        pass

    def crash_before_manifest_switch(source_path, destination_path):
        if destination_path == published.publication_manifest.resolve(strict=False):
            raise SimulatedCrash("simulated crash before mixed manifest switch")
        source_path.replace(destination_path)

    with pytest.raises(SimulatedCrash):
        activate_publication_set_with_lock(
            workspace=workspace,
            published=published,
            replace_path=crash_before_manifest_switch,
            owner_token="owner-001",
        )
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "another-run"}\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Active publication changed"):
        reconcile_publication_set(workspace=workspace, published=published)

    assert json.loads(published.publication_manifest.read_text(encoding="utf-8"))["run_id"] == "another-run"
    assert (published.ortho / "orthomosaic.tif").read_text(encoding="utf-8") == "new-ortho"


def test_activate_publication_rejects_tampered_staged_artifact(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.webodm_ortho / "orthomosaic.tif"
    source.write_text("original", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )
    staged = workspace.publish / "staged" / "ortho" / "orthomosaic.tif"
    staged.write_text("tampered and larger", encoding="utf-8")

    with pytest.raises(ValueError, match="size mismatch"):
        activate_publication(workspace=workspace, published=published)

    assert not published.root.exists()


def test_activate_publication_rejects_tampered_escaping_manifest_path(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.webodm_ortho / "orthomosaic.tif"
    source.write_text("safe staged artifact", encoding="utf-8")
    manifest_path = prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["published_relative_path"] = "../escape.tif"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="must not escape"):
        activate_publication(workspace=workspace, published=published)

    assert not published.root.exists()
    assert not (tmp_path / "surveys" / "2026" / "AH-026019" / "escape.tif").exists()

def test_activate_publication_is_idempotent_for_an_already_published_run(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.webodm_ortho / "orthomosaic.tif"
    source.write_text("published artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )
    manifest_path = activate_publication(workspace=workspace, published=published)
    original_manifest = manifest_path.read_bytes()

    def unexpected_operation(source_path, destination_path):
        pytest.fail(f"Idempotent activation attempted filesystem work: {source_path}")

    repeated_manifest = activate_publication(
        workspace=workspace,
        published=published,
        copy_file=unexpected_operation,
        replace_path=unexpected_operation,
    )

    assert repeated_manifest == manifest_path
    assert repeated_manifest.read_bytes() == original_manifest
    assert (published.ortho / "orthomosaic.tif").read_text() == "published artifact"


def test_activate_publication_recovers_precommit_copy_interruption(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.webodm_ortho / "orthomosaic.tif"
    source.write_text("new artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )

    class SimulatedCrash(BaseException):
        pass

    def interrupted_copy(source_path, destination_path):
        destination_path.write_bytes(source_path.read_bytes())
        raise SimulatedCrash("simulated crash during precommit copy")

    with pytest.raises(SimulatedCrash, match="precommit copy"):
        activate_publication(
            workspace=workspace,
            published=published,
            copy_file=interrupted_copy,
        )

    temporary = published.ortho / ".orthomosaic.tif.run-001.publishing"
    assert temporary.is_file()
    assert not published.publication_manifest.exists()

    manifest_path = activate_publication(workspace=workspace, published=published)
    manifest = json.loads(manifest_path.read_text())

    assert manifest["recovered_interrupted_activation"] is True
    assert not temporary.exists()
    assert (published.ortho / "orthomosaic.tif").read_text() == "new artifact"


def test_activate_publication_recovers_interrupted_manifest_switch(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    target = published.ortho / "orthomosaic.tif"
    target.parent.mkdir(parents=True)
    target.write_text("previous artifact", encoding="utf-8")
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "previous"}\n', encoding="utf-8"
    )
    source = workspace.webodm_ortho / target.name
    source.write_text("new artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )

    class SimulatedCrash(BaseException):
        pass

    replace_count = 0

    def crash_before_manifest_switch(source_path, destination_path):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 3:
            raise SimulatedCrash("simulated crash before manifest switch")
        source_path.replace(destination_path)

    with pytest.raises(SimulatedCrash, match="manifest switch"):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=crash_before_manifest_switch,
        )

    backup = target.with_name(".orthomosaic.tif.run-001.previous")
    candidate = published.publication_manifest.with_name(
        ".publication.json.run-001.publishing"
    )
    assert target.read_text() == "new artifact"
    assert backup.read_text() == "previous artifact"
    assert candidate.is_file()
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "previous"

    manifest_path = activate_publication(workspace=workspace, published=published)
    manifest = json.loads(manifest_path.read_text())

    assert manifest["run_id"] == "run-001"
    assert manifest["recovered_interrupted_activation"] is True
    assert target.read_text() == "new artifact"
    assert backup.read_text() == "previous artifact"
    assert not candidate.exists()


def test_activate_publication_refuses_recovery_after_active_manifest_changes(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    target = published.ortho / "orthomosaic.tif"
    target.parent.mkdir(parents=True)
    target.write_text("previous artifact", encoding="utf-8")
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "previous"}\n', encoding="utf-8"
    )
    source = workspace.webodm_ortho / target.name
    source.write_text("interrupted artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )

    class SimulatedCrash(BaseException):
        pass

    replace_count = 0

    def crash_before_manifest_switch(source_path, destination_path):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 3:
            raise SimulatedCrash("simulated crash before manifest switch")
        source_path.replace(destination_path)

    with pytest.raises(SimulatedCrash):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=crash_before_manifest_switch,
        )

    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "another-run"}\n', encoding="utf-8"
    )
    backup = target.with_name(".orthomosaic.tif.run-001.previous")

    with pytest.raises(RuntimeError, match="manifest changed"):
        activate_publication(workspace=workspace, published=published)

    assert json.loads(published.publication_manifest.read_text())["run_id"] == "another-run"
    assert target.read_text() == "interrupted artifact"
    assert backup.read_text() == "previous artifact"


def test_activate_publication_with_lock_releases_lock_after_success(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.webodm_ortho / "orthomosaic.tif"
    source.write_text("new artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )

    manifest_path = activate_publication_with_lock(
        workspace=workspace,
        published=published,
        owner_token="owner-001",
        created_at="2026-07-20T10:00:00+00:00",
    )

    assert json.loads(manifest_path.read_text())["run_id"] == "run-001"
    assert (published.ortho / "orthomosaic.tif").read_text() == "new artifact"
    assert not (published.root / PUBLICATION_LOCK_NAME).exists()


def test_activate_publication_with_lock_releases_lock_after_activation_failure(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    target = published.ortho / "orthomosaic.tif"
    target.parent.mkdir(parents=True)
    target.write_text("previous artifact", encoding="utf-8")
    source = workspace.webodm_ortho / target.name
    source.write_text("new artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )

    def failing_copy(source_path, destination_path):
        raise OSError("simulated activation failure")

    with pytest.raises(OSError, match="simulated activation failure"):
        activate_publication_with_lock(
            workspace=workspace,
            published=published,
            copy_file=failing_copy,
            owner_token="owner-001",
        )

    assert target.read_text() == "previous artifact"
    assert not (published.root / PUBLICATION_LOCK_NAME).exists()


def test_activate_publication_with_lock_fails_before_activation_when_locked(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.webodm_ortho / "orthomosaic.tif"
    source.write_text("new artifact", encoding="utf-8")
    prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "orthomosaic",
                source,
                Path("ortho/orthomosaic.tif"),
                "file",
            )
        ],
    )
    competing_lock = acquire_publication_lock(
        published_root=published.root,
        run_id="run-002",
        survey_id="AH-026019",
        owner_token="owner-002",
    )

    def unexpected_operation(source_path, destination_path):
        pytest.fail("activation should not start while another owner holds the lock")

    try:
        with pytest.raises(PublicationLockedError, match="already locked"):
            activate_publication_with_lock(
                workspace=workspace,
                published=published,
                copy_file=unexpected_operation,
                replace_path=unexpected_operation,
                owner_token="owner-001",
            )
    finally:
        release_publication_lock(competing_lock)

    assert not published.publication_manifest.exists()
    assert not (published.ortho / "orthomosaic.tif").exists()



def _prepare_tiles_directory_publication(tmp_path, *, existing_final=False):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.qgis_tiles_round
    (source / "metadata.json").write_text("meta", encoding="utf-8")
    (source / "11" / "0").mkdir(parents=True)
    (source / "11" / "0" / "tile.png").write_bytes(b"new-tile")
    if existing_final:
        (published.tiles_ortho_round / "11" / "0").mkdir(parents=True)
        (published.tiles_ortho_round / "11" / "0" / "tile.png").write_bytes(
            b"old-tile"
        )
        published.publication_manifest.write_text(
            '{"status": "published", "run_id": "old-run"}\n',
            encoding="utf-8",
        )
    manifest_path = prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "round_tiles",
                source,
                Path("tiles/ortho/round-corners"),
                "directory",
                required_paths=("metadata.json", "11"),
            )
        ],
    )
    staged = workspace.publish / "staged" / "tiles" / "ortho" / "round-corners"
    return workspace, published, manifest_path, staged



def test_prepare_publication_can_stage_directory_directly_for_activation(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    source = workspace.qgis_tiles_round
    (source / "metadata.json").write_text("meta", encoding="utf-8")
    (source / "11" / "0").mkdir(parents=True)
    (source / "11" / "0" / "tile.png").write_bytes(b"new-tile")
    expected = publication_activation_path(
        published_path=published.tiles_ortho_round,
        run_id="run-001",
    )

    manifest_path = prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "round_tiles",
                source,
                Path("tiles/ortho/round-corners"),
                "directory",
                required_paths=("metadata.json", "11"),
            )
        ],
        stage_directories_for_activation=True,
    )

    staged_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    staged_path = Path(staged_manifest["artifacts"][0]["staged_path"])
    assert staged_path.resolve(strict=False) == expected.resolve(strict=False)
    assert (expected / "11" / "0" / "tile.png").read_bytes() == b"new-tile"
    assert not (workspace.publish / "staged" / "tiles" / "ortho" / "round-corners").exists()

    def unexpected_copytree(source_path, destination_path):
        pytest.fail("activation should rename the prepared directory without copytree")

    result = activate_publication(
        workspace=workspace,
        published=published,
        copy_tree=unexpected_copytree,
    )

    active = json.loads(result.read_text(encoding="utf-8"))
    assert active["artifacts"][0]["zero_copy_activation"] is True
    assert not expected.exists()
    assert (
        published.tiles_ortho_round / "11" / "0" / "tile.png"
    ).read_bytes() == b"new-tile"

def test_activate_publication_zero_copy_reuses_exact_activation_path(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    expected = publication_activation_path(
        published_path=published.tiles_ortho_round,
        run_id="run-001",
    )
    (expected / "11" / "0").mkdir(parents=True)
    (expected / "metadata.json").write_text("meta", encoding="utf-8")
    (expected / "11" / "0" / "tile.png").write_bytes(b"new-tile")

    def unexpected_copytree(source, destination):
        pytest.fail("zero-copy preparation or activation invoked copytree")

    manifest_path = prepare_publication(
        run_id="run-001",
        survey_id="AH-026019",
        workspace=workspace,
        published=published,
        artifacts=[
            PublicationArtifact(
                "round_tiles",
                expected,
                Path("tiles/ortho/round-corners"),
                "directory",
                required_paths=("metadata.json", "11"),
                expected_file_count=2,
                expected_total_bytes=12,
            )
        ],
        copy_tree=unexpected_copytree,
    )
    staged_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert Path(staged_manifest["artifacts"][0]["staged_path"]).resolve() == expected.resolve()

    scan_calls = 0

    def recording_scan(root):
        nonlocal scan_calls
        scan_calls += 1
        return artifacts_module._scan_directory_tree(root)

    result = activate_publication(
        workspace=workspace,
        published=published,
        copy_tree=unexpected_copytree,
        directory_scan=recording_scan,
    )

    assert result == published.publication_manifest.resolve(strict=False)
    assert scan_calls == 1
    assert not expected.exists()
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"new-tile"
    )
    active = json.loads(result.read_text())
    assert active["artifacts"][0]["zero_copy_activation"] is True

def test_arbitrary_hidden_directory_does_not_qualify_for_zero_copy(tmp_path):
    workspace, published, manifest_path, staged = _prepare_tiles_directory_publication(
        tmp_path
    )
    hidden = workspace.root / ".activation" / "run-001" / "round-corners.tmp"
    hidden.parent.mkdir(parents=True)
    staged.replace(hidden)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["staged_path"] = str(hidden)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    copy_calls = []

    def recording_copytree(source, destination):
        copy_calls.append((source, destination))
        return shutil.copytree(source, destination)

    activate_publication(
        workspace=workspace,
        published=published,
        copy_tree=recording_copytree,
    )

    expected = publication_activation_path(
        published_path=published.tiles_ortho_round,
        run_id="run-001",
    )
    assert copy_calls == [(hidden.resolve(), expected.resolve())]
    assert (published.tiles_ortho_round / "metadata.json").is_file()


def test_directory_activation_preserves_existing_final_as_previous(tmp_path):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )

    activate_publication(workspace=workspace, published=published)

    backup = (
        published.tiles_ortho_round.parent
        / ".previous"
        / "round-corners.run-001"
    )
    assert (backup / "11" / "0" / "tile.png").read_bytes() == b"old-tile"
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"new-tile"
    )


def test_directory_copy_failure_leaves_existing_final_untouched(tmp_path):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )

    def failing_copytree(source, destination):
        destination.mkdir()
        (destination / "partial.png").write_bytes(b"partial")
        raise OSError("simulated directory copy failure")

    with pytest.raises(OSError, match="simulated directory copy failure"):
        activate_publication(
            workspace=workspace,
            published=published,
            copy_tree=failing_copytree,
        )

    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "old-run"
    journal = json.loads(
        (
            published.tiles_ortho_round.parent
            / ".activation"
            / "run-001"
            / "activation.json"
        ).read_text()
    )
    assert journal["status"] == "failed"


def test_directory_manifest_validation_failure_leaves_final_untouched(tmp_path):
    workspace, published, manifest_path, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["file_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="file count mismatch"):
        activate_publication(workspace=workspace, published=published)

    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "old-run"


def test_directory_failure_moving_final_records_failure_without_activation(tmp_path):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )

    def failing_replace(source, destination):
        raise OSError("simulated final backup rename failure")

    with pytest.raises(OSError, match="backup rename failure"):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=failing_replace,
            rename_attempts=1,
        )

    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    journal = json.loads(
        (
            published.tiles_ortho_round.parent
            / ".activation"
            / "run-001"
            / "activation.json"
        ).read_text()
    )
    assert journal["status"] == "failed"


def test_directory_failure_moving_temp_restores_previous(tmp_path):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )
    replace_count = 0

    def fail_temp_activation(source, destination):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise OSError("simulated temp activation rename failure")
        source.replace(destination)

    with pytest.raises(OSError, match="temp activation rename failure"):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=fail_temp_activation,
            rename_attempts=1,
        )

    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert not (
        published.tiles_ortho_round.parent
        / ".previous"
        / "round-corners.run-001"
    ).exists()
    journal = json.loads(
        (
            published.tiles_ortho_round.parent
            / ".activation"
            / "run-001"
            / "activation.json"
        ).read_text()
    )
    assert journal["status"] == "rolled_back"
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "old-run"


def test_directory_journal_transitions_and_publication_manifest_is_last(
    tmp_path, monkeypatch
):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )
    writes = []
    original_write_json = artifacts_module._write_json_atomic

    def recording_write(path, payload):
        writes.append((Path(path), payload.get("status")))
        return original_write_json(path, payload)

    monkeypatch.setattr(artifacts_module, "_write_json_atomic", recording_write)

    activate_publication(workspace=workspace, published=published)

    journal_path = (
        published.tiles_ortho_round.parent
        / ".activation"
        / "run-001"
        / "activation.json"
    ).resolve(strict=False)
    relevant = [
        (path.resolve(strict=False), status)
        for path, status in writes
        if path.resolve(strict=False) in {journal_path, published.publication_manifest.resolve(strict=False)}
    ]
    assert relevant == [
        (journal_path, "prepared"),
        (journal_path, "previous_moved"),
        (journal_path, "activated"),
        (published.publication_manifest.resolve(strict=False), "published"),
        (journal_path, "committed"),
    ]


def test_directory_rename_retries_are_bounded_with_backoff(tmp_path):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )
    replace_count = 0
    delays = []

    def transient_replace(source, destination):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 1:
            raise OSError("transient open handle")
        source.replace(destination)

    activate_publication(
        workspace=workspace,
        published=published,
        replace_path=transient_replace,
        rename_attempts=2,
        rename_backoff_seconds=0.01,
        sleep=delays.append,
    )

    assert delays == [0.01]
    assert (published.tiles_ortho_round / "metadata.json").is_file()


def test_directory_activation_rejects_symlink_entries(tmp_path):
    workspace, published, manifest_path, staged = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )
    link = staged / "linked-tile.png"
    try:
        link.symlink_to(staged / "metadata.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this platform")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["file_count"] += 1
    manifest["artifacts"][0]["size_bytes"] += 4
    manifest["artifacts"][0]["total_bytes"] += 4
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="symlinks or reparse points"):
        activate_publication(workspace=workspace, published=published)

    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )



def test_directory_interruption_after_previous_moved_preserves_journal_evidence(
    tmp_path
):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )

    class SimulatedCrash(BaseException):
        pass

    replace_count = 0

    def crash_before_temp_activation(source, destination):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise SimulatedCrash("simulated crash after previous_moved")
        source.replace(destination)

    with pytest.raises(SimulatedCrash, match="previous_moved"):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=crash_before_temp_activation,
            rename_attempts=1,
        )

    journal = json.loads(
        (
            published.tiles_ortho_round.parent
            / ".activation"
            / "run-001"
            / "activation.json"
        ).read_text()
    )
    backup = (
        published.tiles_ortho_round.parent
        / ".previous"
        / "round-corners.run-001"
    )
    assert journal["status"] == "previous_moved"
    assert backup.is_dir()
    assert not published.tiles_ortho_round.exists()
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "old-run"


def test_directory_interruption_after_activated_does_not_claim_commit(
    tmp_path, monkeypatch
):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path, existing_final=True
    )

    class SimulatedCrash(BaseException):
        pass

    def crash_after_activated(**kwargs):
        raise SimulatedCrash("simulated crash after activated")

    monkeypatch.setattr(
        artifacts_module,
        "_lightweight_directory_check",
        crash_after_activated,
    )

    with pytest.raises(SimulatedCrash, match="after activated"):
        activate_publication(workspace=workspace, published=published)

    journal = json.loads(
        (
            published.tiles_ortho_round.parent
            / ".activation"
            / "run-001"
            / "activation.json"
        ).read_text()
    )
    backup = (
        published.tiles_ortho_round.parent
        / ".previous"
        / "round-corners.run-001"
    )
    assert journal["status"] == "activated"
    assert backup.is_dir()
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"new-tile"
    )
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "old-run"



def _interrupt_directory_with_previous_moved(tmp_path):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path,
        existing_final=True,
    )

    class SimulatedCrash(BaseException):
        pass

    replace_count = 0

    def crash_before_temp_activation(source, destination):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2:
            raise SimulatedCrash("simulated previous_moved interruption")
        source.replace(destination)

    with pytest.raises(SimulatedCrash, match="previous_moved"):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=crash_before_temp_activation,
            rename_attempts=1,
        )
    return workspace, published


def test_directory_reconciliation_retains_valid_prepared_candidate(tmp_path):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path,
        existing_final=True,
    )

    class SimulatedCrash(BaseException):
        pass

    def crash_before_any_rename(source, destination):
        raise SimulatedCrash("simulated prepared interruption")

    with pytest.raises(SimulatedCrash, match="prepared"):
        activate_publication(
            workspace=workspace,
            published=published,
            replace_path=crash_before_any_rename,
            rename_attempts=1,
        )

    def unexpected_replace(source, destination):
        pytest.fail("prepared reconciliation attempted a rename")

    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
        replace_path=unexpected_replace,
    )

    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "prepared"
    assert journal["had_previous"] is True
    assert journal["previous_publication_run_id"] == "old-run"
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert Path(journal["temporary"]).is_dir()


def test_directory_reconciliation_restores_backup_when_prepared_journal_lags(
    tmp_path, monkeypatch
):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path,
        existing_final=True,
    )

    class SimulatedCrash(BaseException):
        pass

    original_write_journal = artifacts_module._write_activation_journal

    def crash_before_previous_moved_journal(path, journal, status, error=None):
        if status == "previous_moved":
            raise SimulatedCrash("journal still prepared")
        return original_write_journal(path, journal, status, error)

    monkeypatch.setattr(
        artifacts_module,
        "_write_activation_journal",
        crash_before_previous_moved_journal,
    )
    with pytest.raises(SimulatedCrash, match="still prepared"):
        activate_publication(workspace=workspace, published=published)
    monkeypatch.setattr(
        artifacts_module,
        "_write_activation_journal",
        original_write_journal,
    )

    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
    )

    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "rolled_back"
    assert journal["recovered_from_status"] == "prepared"
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert not Path(journal["previous"]).exists()


def test_directory_reconciliation_rolls_back_previous_moved(tmp_path):
    workspace, published = _interrupt_directory_with_previous_moved(tmp_path)

    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
    )

    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "rolled_back"
    assert journal["recovered_interrupted_activation"] is True
    assert journal["recovered_from_status"] == "previous_moved"
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert Path(journal["temporary"]).is_dir()
    assert not Path(journal["previous"]).exists()
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "old-run"


def test_directory_reconciliation_handles_rename_ahead_of_previous_moved_journal(
    tmp_path, monkeypatch
):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path,
        existing_final=True,
    )

    class SimulatedCrash(BaseException):
        pass

    original_write_journal = artifacts_module._write_activation_journal

    def crash_before_activated_journal(path, journal, status, error=None):
        if status == "activated":
            raise SimulatedCrash("journal still previous_moved")
        return original_write_journal(path, journal, status, error)

    monkeypatch.setattr(
        artifacts_module,
        "_write_activation_journal",
        crash_before_activated_journal,
    )
    with pytest.raises(SimulatedCrash, match="previous_moved"):
        activate_publication(workspace=workspace, published=published)
    monkeypatch.setattr(
        artifacts_module,
        "_write_activation_journal",
        original_write_journal,
    )

    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
    )

    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "rolled_back"
    assert journal["recovered_from_status"] == "previous_moved"
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert Path(journal["temporary"]).is_dir()


def test_directory_reconciliation_rolls_back_activated_state(tmp_path, monkeypatch):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path,
        existing_final=True,
    )

    class SimulatedCrash(BaseException):
        pass

    def crash_after_activated(**kwargs):
        raise SimulatedCrash("simulated activated interruption")

    monkeypatch.setattr(
        artifacts_module,
        "_lightweight_directory_check",
        crash_after_activated,
    )
    with pytest.raises(SimulatedCrash, match="activated"):
        activate_publication(workspace=workspace, published=published)
    monkeypatch.undo()

    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
    )

    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "rolled_back"
    assert journal["recovered_from_status"] == "activated"
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )
    assert Path(journal["temporary"]).is_dir()


def test_directory_reconciliation_rolls_back_activation_without_previous(tmp_path, monkeypatch):
    workspace, published, _, _ = _prepare_tiles_directory_publication(tmp_path)

    class SimulatedCrash(BaseException):
        pass

    def crash_after_activated(**kwargs):
        raise SimulatedCrash("simulated first activation interruption")

    monkeypatch.setattr(
        artifacts_module,
        "_lightweight_directory_check",
        crash_after_activated,
    )
    with pytest.raises(SimulatedCrash, match="first activation"):
        activate_publication(workspace=workspace, published=published)
    monkeypatch.undo()

    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
    )

    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "rolled_back"
    assert journal["had_previous"] is False
    assert not published.tiles_ortho_round.exists()
    assert Path(journal["temporary"]).is_dir()
    assert not published.publication_manifest.exists()


def test_directory_reconciliation_finalizes_manifest_committed_before_journal(
    tmp_path, monkeypatch
):
    workspace, published, _, _ = _prepare_tiles_directory_publication(
        tmp_path,
        existing_final=True,
    )

    class SimulatedCrash(BaseException):
        pass

    original_write_journal = artifacts_module._write_activation_journal

    def crash_before_committed_journal(path, journal, status, error=None):
        if status == "committed":
            raise SimulatedCrash("publication committed before journal")
        return original_write_journal(path, journal, status, error)

    monkeypatch.setattr(
        artifacts_module,
        "_write_activation_journal",
        crash_before_committed_journal,
    )
    with pytest.raises(SimulatedCrash, match="committed before journal"):
        activate_publication(workspace=workspace, published=published)
    monkeypatch.setattr(
        artifacts_module,
        "_write_activation_journal",
        original_write_journal,
    )

    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
    )
    first_result = journal_path.read_bytes()

    def unexpected_operation(*args, **kwargs):
        pytest.fail("committed reconciliation attempted filesystem work")

    repeated = reconcile_directory_publication(
        workspace=workspace,
        published=published,
        replace_path=unexpected_operation,
        directory_scan=unexpected_operation,
    )

    journal = json.loads(first_result)
    assert repeated == journal_path
    assert repeated.read_bytes() == first_result
    assert journal["status"] == "committed"
    assert journal["recovered_from_status"] == "activated"
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "run-001"
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"new-tile"
    )


def test_directory_reconciliation_is_idempotent_after_rollback(tmp_path):
    workspace, published = _interrupt_directory_with_previous_moved(tmp_path)
    journal_path = reconcile_directory_publication(
        workspace=workspace,
        published=published,
    )
    first_result = journal_path.read_bytes()

    def unexpected_replace(source, destination):
        pytest.fail("rolled-back reconciliation attempted another rename")

    repeated = reconcile_directory_publication(
        workspace=workspace,
        published=published,
        replace_path=unexpected_replace,
    )

    assert repeated == journal_path
    assert repeated.read_bytes() == first_result
    assert (
        (published.tiles_ortho_round / "11" / "0" / "tile.png").read_bytes()
        == b"old-tile"
    )


def test_directory_reconciliation_fails_closed_when_active_manifest_changed(tmp_path):
    workspace, published = _interrupt_directory_with_previous_moved(tmp_path)
    published.publication_manifest.write_text(
        '{"status": "published", "run_id": "another-run"}\n',
        encoding="utf-8",
    )
    backup = (
        published.tiles_ortho_round.parent
        / ".previous"
        / "round-corners.run-001"
    )

    with pytest.raises(RuntimeError, match="Active publication changed"):
        reconcile_directory_publication(
            workspace=workspace,
            published=published,
        )

    assert backup.is_dir()
    assert not published.tiles_ortho_round.exists()
    assert json.loads(published.publication_manifest.read_text())["run_id"] == "another-run"


def test_directory_reconciliation_rejects_tampered_journal_without_moving(tmp_path):
    workspace, published = _interrupt_directory_with_previous_moved(tmp_path)
    journal_path = (
        published.tiles_ortho_round.parent
        / ".activation"
        / "run-001"
        / "activation.json"
    )
    journal = json.loads(journal_path.read_text())
    journal["temporary"] = str(journal_path.parent / "other.tmp")
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    backup = Path(journal["previous"])

    with pytest.raises(ValueError, match="temporary path does not match"):
        reconcile_directory_publication(
            workspace=workspace,
            published=published,
        )

    assert backup.is_dir()
    assert not published.tiles_ortho_round.exists()


def test_directory_reconciliation_fails_closed_when_previous_backup_is_missing(tmp_path):
    workspace, published = _interrupt_directory_with_previous_moved(tmp_path)
    backup = (
        published.tiles_ortho_round.parent
        / ".previous"
        / "round-corners.run-001"
    )
    displaced_backup = backup.with_name("round-corners.displaced")
    backup.replace(displaced_backup)

    with pytest.raises(RuntimeError, match="lost its previous backup"):
        reconcile_directory_publication(
            workspace=workspace,
            published=published,
        )

    assert displaced_backup.is_dir()
    assert not published.tiles_ortho_round.exists()


def test_directory_reconciliation_rename_failure_preserves_retryable_evidence(tmp_path):
    workspace, published = _interrupt_directory_with_previous_moved(tmp_path)

    def failing_replace(source, destination):
        raise OSError("simulated reconciliation rename failure")

    with pytest.raises(OSError, match="reconciliation rename failure"):
        reconcile_directory_publication(
            workspace=workspace,
            published=published,
            replace_path=failing_replace,
            rename_attempts=1,
        )

    journal_path = (
        published.tiles_ortho_round.parent
        / ".activation"
        / "run-001"
        / "activation.json"
    )
    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "previous_moved"
    assert journal["reconciliation_status"] == "failed"
    assert journal["reconciliation_error_type"] == "OSError"
    assert Path(journal["previous"]).is_dir()
    assert Path(journal["temporary"]).is_dir()
    assert not published.tiles_ortho_round.exists()
