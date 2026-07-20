import json
from pathlib import Path

import pytest

from shared.artifacts import (
    PublicationArtifact,
    activate_publication,
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


def test_activate_publication_rejects_directory_artifacts_before_publishing(tmp_path):
    workspace = plan_run_workspace(tmp_path / "workspaces", "run-001")
    create_run_workspace(workspace)
    published = plan_published_survey(tmp_path / "surveys", 2026, "AH-026019")
    (workspace.qgis_tiles_round / "tile.png").write_bytes(b"tile")
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
            )
        ],
    )

    with pytest.raises(ValueError, match="file artifacts only"):
        activate_publication(workspace=workspace, published=published)

    assert not published.root.exists()


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
