from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Iterable, Literal

from shared.publication_lock import acquire_publication_lock, release_publication_lock


ArtifactKind = Literal["file", "directory"]

PUBLICATION_MANIFEST_NAME = "publication.json"
ACTIVATION_JOURNAL_NAME = "activation.json"


@dataclass(frozen=True)
class RunWorkspaceLayout:
    root: Path
    images_raw: Path
    images_path: Path
    images_cross_runs: Path
    boundary: Path
    webodm_ortho: Path
    webodm_odm: Path
    webodm_dem: Path
    webodm_3d: Path
    qgis_clipped_ortho: Path
    qgis_tiles_round: Path
    qgis_tiles_soft: Path
    checkpoints: Path
    publish: Path

    def required_dirs(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.images_raw,
            self.images_path,
            self.images_cross_runs,
            self.boundary,
            self.webodm_ortho,
            self.webodm_odm,
            self.webodm_dem,
            self.webodm_3d,
            self.qgis_clipped_ortho,
            self.qgis_tiles_round,
            self.qgis_tiles_soft,
            self.checkpoints,
            self.publish,
        )


@dataclass(frozen=True)
class PublishedSurveyLayout:
    root: Path
    manifest: Path
    publication_manifest: Path
    boundary: Path
    ortho: Path
    qgis_clipped_ortho: Path
    tiles_ortho_round: Path
    tiles_ortho_soft: Path


@dataclass(frozen=True)
class PublicationArtifact:
    logical_name: str
    source_path: Path
    published_relative_path: Path
    kind: ArtifactKind
    required_paths: tuple[str, ...] = ()
    expected_file_count: int | None = None
    expected_total_bytes: int | None = None


def describe_run_workspace(layout: RunWorkspaceLayout) -> dict[str, str]:
    return _describe_path_dataclass(layout)


def describe_published_survey(layout: PublishedSurveyLayout) -> dict[str, str]:
    return _describe_path_dataclass(layout)


def plan_run_workspace(workspace_root: Path, run_id: str) -> RunWorkspaceLayout:
    run_component = _safe_path_component(run_id, "run_id")
    root = Path(workspace_root) / run_component
    return RunWorkspaceLayout(
        root=root,
        images_raw=root / "images" / "raw",
        images_path=root / "images" / "path",
        images_cross_runs=root / "images" / "cross-runs",
        boundary=root / "boundary",
        webodm_ortho=root / "webodm" / "ortho",
        webodm_odm=root / "webodm" / "odm",
        webodm_dem=root / "webodm" / "dem",
        webodm_3d=root / "webodm" / "3d",
        qgis_clipped_ortho=root / "qgis" / "clipped" / "ortho",
        qgis_tiles_round=root / "qgis" / "tiles" / "round-corners",
        qgis_tiles_soft=root / "qgis" / "tiles" / "soft-corners",
        checkpoints=root / "checkpoints",
        publish=root / "publish",
    )


def plan_published_survey(
    surveys_root: Path,
    year: int | str,
    survey_id: str,
) -> PublishedSurveyLayout:
    year_component = _safe_path_component(str(year), "year")
    survey_component = _safe_path_component(survey_id, "survey_id")
    root = Path(surveys_root) / year_component / survey_component / "rgb"
    return PublishedSurveyLayout(
        root=root,
        manifest=root / "manifest.json",
        publication_manifest=root / PUBLICATION_MANIFEST_NAME,
        boundary=root / "boundary",
        ortho=root / "ortho",
        qgis_clipped_ortho=root / "qgis" / "clipped" / "ortho",
        tiles_ortho_round=root / "tiles" / "ortho" / "round-corners",
        tiles_ortho_soft=root / "tiles" / "ortho" / "soft-corners",
    )


def plan_published_survey_from_rgb_path(rgb_path: Path) -> PublishedSurveyLayout:
    root = Path(rgb_path)
    return PublishedSurveyLayout(
        root=root,
        manifest=root / "manifest.json",
        publication_manifest=root / PUBLICATION_MANIFEST_NAME,
        boundary=root / "boundary",
        ortho=root / "ortho",
        qgis_clipped_ortho=root / "qgis" / "clipped" / "ortho",
        tiles_ortho_round=root / "tiles" / "ortho" / "round-corners",
        tiles_ortho_soft=root / "tiles" / "ortho" / "soft-corners",
    )


def create_run_workspace(layout: RunWorkspaceLayout) -> None:
    root = layout.root.resolve()
    _reject_filesystem_root(root)
    for directory in layout.required_dirs():
        resolved = directory.resolve(strict=False)
        _require_within(resolved, root)
        resolved.mkdir(parents=True, exist_ok=True)


def prepare_publication(
    *,
    run_id: str,
    survey_id: str,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    artifacts: Iterable[PublicationArtifact],
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    copy_tree: Callable[[Path, Path], object] | None = None,
) -> Path:
    """Stage a complete publish set inside the run workspace.

    This function prepares a validated, manifest-backed publish set for later
    activation. Directory output already generated at the exact run-specific
    hidden activation path is recorded in place without copying it again.
    """

    workspace_root = workspace.root.resolve()
    tree_copier = copy_tree or _copy_directory_tree
    staging_dir = workspace.publish / "staged"
    staging_resolved = staging_dir.resolve(strict=False)
    _require_within(staging_resolved, workspace_root)
    if staging_dir.exists():
        raise FileExistsError(f"Publication staging directory already exists: {staging_dir}")

    artifact_records = []
    for artifact in tuple(artifacts):
        relative_path = _validate_relative_path(artifact.published_relative_path)
        source_path = Path(artifact.source_path)
        if artifact.kind == "file":
            if not source_path.is_file():
                raise FileNotFoundError(source_path)
        elif artifact.kind == "directory":
            if not source_path.is_dir():
                raise FileNotFoundError(source_path)
        else:
            raise ValueError(f"Unsupported artifact kind: {artifact.kind}")

        published_path = published.root / relative_path
        direct_activation_source = (
            artifact.kind == "directory"
            and source_path.resolve(strict=False)
            == publication_activation_path(
                published_path=published_path,
                run_id=run_id,
            ).resolve(strict=False)
        )
        if direct_activation_source:
            staged_path = source_path
        else:
            staged_path = staging_dir / relative_path
            _require_within(staged_path.resolve(strict=False), staging_resolved)
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            if artifact.kind == "file":
                copy_file(source_path, staged_path)
            else:
                tree_copier(source_path, staged_path)

        artifact_records.append(
            _artifact_record(
                artifact=artifact,
                source_path=source_path,
                staged_path=staged_path,
                published_path=published_path,
                relative_path=relative_path,
            )
        )

    manifest_path = staging_dir / PUBLICATION_MANIFEST_NAME
    _write_json_atomic(
        manifest_path,
        {
            "manifest_version": 1,
            "status": "staged",
            "run_id": run_id,
            "survey_id": survey_id,
            "workspace_root": str(workspace.root),
            "published_root": str(published.root),
            "artifacts": artifact_records,
        },
    )
    return manifest_path



def publication_activation_path(*, published_path: Path, run_id: str) -> Path:
    """Return the exact hidden same-filesystem directory activation path."""

    run_component = _safe_path_component(run_id, "run_id")
    final_path = Path(published_path).resolve(strict=False)
    _reject_filesystem_root(final_path)
    return (
        final_path.parent
        / ".activation"
        / run_component
        / f"{final_path.name}.tmp"
    )


def activate_publication(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    copy_tree: Callable[[Path, Path], object] | None = None,
    replace_path: Callable[[Path, Path], object] | None = None,
    directory_scan: Callable[[Path], tuple[int, int]] | None = None,
    rename_attempts: int = 3,
    rename_backoff_seconds: float = 0.05,
    sleep: Callable[[float], object] = time.sleep,
) -> Path:
    """Activate a staged file set or one directory and commit metadata last.

    File activation preserves its existing multi-file recovery behavior.
    Directory activation supports a compatibility copy into the exact hidden
    activation path and a zero-copy path when generation already occurred
    there. Mixed file/directory publications remain intentionally unsupported.
    """

    manifest = _read_staged_publication_manifest(
        workspace.publish / "staged" / PUBLICATION_MANIFEST_NAME
    )
    records = manifest.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ValueError("Publication manifest must contain at least one artifact")
    kinds = {
        record.get("kind") if isinstance(record, dict) else None
        for record in records
    }
    if kinds == {"file"}:
        return _activate_file_publication(
            workspace=workspace,
            published=published,
            copy_file=copy_file,
            replace_path=replace_path,
        )
    if kinds == {"directory"} and len(records) == 1:
        return _activate_directory_publication(
            workspace=workspace,
            published=published,
            manifest=manifest,
            record=records[0],
            copy_tree=copy_tree or _copy_directory_tree,
            replace_path=replace_path or _replace_path,
            directory_scan=directory_scan or _scan_directory_tree,
            rename_attempts=rename_attempts,
            rename_backoff_seconds=rename_backoff_seconds,
            sleep=sleep,
        )
    raise ValueError(
        "Directory publication currently supports exactly one directory "
        "artifact and no mixed artifacts"
    )


def _activate_file_publication(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    replace_path: Callable[[Path, Path], object] | None = None,
) -> Path:
    """Activate a staged, file-only publication and write its manifest last.

    All staged artifacts are validated and copied to temporary siblings before
    any legacy-compatible published path is changed. Existing published files
    are retained as run-specific backups so an activation failure can restore
    them and interrupted attempts remain diagnosable. Directory artifacts are
    intentionally deferred until a recoverable directory protocol is defined.
    """

    replace = replace_path or _replace_path
    workspace_root = workspace.root.resolve()
    published_root = published.root.resolve(strict=False)
    _reject_filesystem_root(workspace_root)
    _reject_filesystem_root(published_root)

    staged_root = (workspace.publish / "staged").resolve(strict=False)
    _require_within(staged_root, workspace_root)
    staged_manifest = staged_root / PUBLICATION_MANIFEST_NAME
    manifest = _read_staged_publication_manifest(staged_manifest)

    run_id = _safe_path_component(str(manifest.get("run_id", "")), "run_id")
    if manifest.get("status") != "staged":
        raise ValueError("Publication manifest status must be 'staged'")
    if _manifest_root(manifest, "workspace_root") != workspace_root:
        raise ValueError("Publication manifest workspace_root does not match the workspace")
    if _manifest_root(manifest, "published_root") != published_root:
        raise ValueError("Publication manifest published_root does not match the published layout")

    artifact_records = manifest.get("artifacts")
    if not isinstance(artifact_records, list) or not artifact_records:
        raise ValueError("Publication manifest must contain at least one artifact")

    activation_entries: list[dict[str, object]] = []
    seen_targets: set[Path] = set()
    for record in artifact_records:
        entry = _plan_file_activation(
            record=record,
            run_id=run_id,
            staged_root=staged_root,
            published_root=published_root,
        )
        target = entry["target"]
        if target in seen_targets:
            raise ValueError(f"Duplicate published artifact path: {target}")
        seen_targets.add(target)
        activation_entries.append(entry)

    publication_manifest = published.publication_manifest.resolve(strict=False)
    _require_within(publication_manifest, published_root)
    if publication_manifest.exists() and not publication_manifest.is_file():
        raise ValueError("Published publication manifest path must be a file")
    manifest_candidate = publication_manifest.with_name(
        f".{publication_manifest.name}.{run_id}.publishing"
    )
    manifest_candidate_temp = manifest_candidate.with_name(
        f".{manifest_candidate.name}.tmp"
    )
    previous_manifest = publication_manifest.with_name(
        f".{publication_manifest.name}.{run_id}.previous"
    )

    if _active_publication_matches_staged(
        publication_manifest=publication_manifest,
        staged_manifest=manifest,
        activation_entries=activation_entries,
        published_root=published_root,
    ):
        return publication_manifest

    recovered_interrupted_activation = _reconcile_interrupted_publication(
        publication_manifest=publication_manifest,
        manifest_candidate=manifest_candidate,
        manifest_candidate_temp=manifest_candidate_temp,
        previous_manifest=previous_manifest,
        staged_manifest=manifest,
        activation_entries=activation_entries,
        workspace_root=workspace_root,
        published_root=published_root,
    )

    if previous_manifest.exists():
        _require_previous_manifest_matches_active(
            previous_manifest=previous_manifest,
            publication_manifest=publication_manifest,
        )

    reserved_paths = [manifest_candidate, manifest_candidate_temp]
    for entry in activation_entries:
        reserved_paths.extend([entry["temporary"], entry["backup"]])
    existing_reserved = [path for path in reserved_paths if path.exists()]
    if existing_reserved:
        raise FileExistsError(
            f"Publication activation path already exists: {existing_reserved[0]}"
        )

    prepared_paths: list[Path] = []
    try:
        for entry in activation_entries:
            target = entry["target"]
            temporary = entry["temporary"]
            target.parent.mkdir(parents=True, exist_ok=True)
            prepared_paths.append(temporary)
            copy_file(entry["staged"], temporary)
            if not temporary.is_file():
                raise OSError(f"Publication copy did not create a file: {temporary}")
            if temporary.stat().st_size != entry["size_bytes"]:
                raise OSError(f"Publication copy size mismatch: {temporary}")

        if publication_manifest.is_file() and not previous_manifest.exists():
            prepared_paths.append(previous_manifest)
            copy_file(publication_manifest, previous_manifest)

        active_manifest = dict(manifest)
        active_manifest["status"] = "published"
        active_manifest["staged_manifest"] = str(staged_manifest)
        active_manifest["previous_publication_manifest"] = (
            str(previous_manifest) if previous_manifest.is_file() else None
        )
        active_manifest["recovered_interrupted_activation"] = (
            recovered_interrupted_activation
        )
        active_manifest["artifacts"] = [
            _activated_artifact_record(entry) for entry in activation_entries
        ]
        _write_json_atomic(manifest_candidate, active_manifest)
        prepared_paths.append(manifest_candidate)
    except Exception:
        _unlink_owned_files(
            prepared_paths + [manifest_candidate_temp],
            published_root,
        )
        raise

    activated: list[dict[str, object]] = []
    try:
        for entry in activation_entries:
            target = entry["target"]
            backup = entry["backup"]
            had_previous = target.is_file()
            if had_previous:
                replace(target, backup)
            activation_state = {
                "target": target,
                "backup": backup,
                "had_previous": had_previous,
                "installed": False,
            }
            activated.append(activation_state)
            replace(entry["temporary"], target)
            activation_state["installed"] = True

        replace(manifest_candidate, publication_manifest)
    except Exception as activation_error:
        try:
            _rollback_file_activation(activated, published_root)
        except Exception as rollback_error:
            raise RuntimeError(
                "Publication activation failed and rollback was incomplete: "
                f"{activation_error}"
            ) from rollback_error
        _unlink_owned_files(
            [entry["temporary"] for entry in activation_entries]
            + [manifest_candidate, manifest_candidate_temp],
            published_root,
        )
        raise

    return publication_manifest



def _activate_directory_publication(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    manifest: dict[str, object],
    record: object,
    copy_tree: Callable[[Path, Path], object],
    replace_path: Callable[[Path, Path], object],
    directory_scan: Callable[[Path], tuple[int, int]],
    rename_attempts: int,
    rename_backoff_seconds: float,
    sleep: Callable[[float], object],
) -> Path:
    """Activate one directory through a journaled same-filesystem rename."""

    if rename_attempts < 1:
        raise ValueError("rename_attempts must be at least 1")
    if rename_backoff_seconds < 0:
        raise ValueError("rename_backoff_seconds must be non-negative")

    entry = _plan_directory_activation(
        workspace=workspace,
        published=published,
        manifest=manifest,
        record=record,
    )
    publication_manifest = published.publication_manifest.resolve(strict=False)
    _require_within(publication_manifest, entry["published_root"])
    if publication_manifest.exists() and not publication_manifest.is_file():
        raise ValueError("Published publication manifest path must be a file")

    if _active_directory_publication_matches(
        publication_manifest=publication_manifest,
        manifest=manifest,
        entry=entry,
    ):
        return publication_manifest

    source = entry["staged"]
    source_lexical = entry["staged_lexical"]
    temporary = entry["temporary"]
    target = entry["target"]
    backup = entry["backup"]
    journal_path = entry["journal"]
    activation_root = journal_path.parent
    zero_copy = source == temporary

    activation_root.mkdir(parents=True, exist_ok=True)
    journal = {
        "journal_version": 1,
        "run_id": entry["run_id"],
        "output_type": "directory",
        "source": str(source),
        "temporary": str(temporary),
        "final": str(target),
        "previous": str(backup),
        "zero_copy": zero_copy,
        "expected_file_count": entry["file_count"],
        "expected_total_bytes": entry["size_bytes"],
    }

    try:
        _reject_reparse_path(source_lexical, "Directory publication source")
        if not source.is_dir():
            raise FileNotFoundError(source)
        if not zero_copy:
            if temporary.exists():
                raise FileExistsError(
                    f"Directory activation temporary path already exists: {temporary}"
                )
            copy_tree(source, temporary)
        if not temporary.is_dir():
            raise OSError(
                f"Directory activation copy did not create a directory: {temporary}"
            )

        actual_file_count, actual_total_bytes = directory_scan(temporary)
        if actual_file_count != entry["file_count"]:
            raise ValueError(
                "Staged directory file count mismatch: "
                f"expected {entry['file_count']}, got {actual_file_count}"
            )
        if actual_total_bytes != entry["size_bytes"]:
            raise ValueError(
                "Staged directory total size mismatch: "
                f"expected {entry['size_bytes']}, got {actual_total_bytes}"
            )
        _validate_required_paths(temporary, entry["required_paths"])
        _write_activation_journal(journal_path, journal, "prepared")
    except Exception as exc:
        _write_activation_journal(journal_path, journal, "failed", exc)
        raise

    had_previous = target.is_dir()
    if backup.exists():
        error = FileExistsError(
            f"Directory activation backup path already exists: {backup}"
        )
        _write_activation_journal(journal_path, journal, "failed", error)
        raise error

    if had_previous:
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            _replace_with_retry(
                target,
                backup,
                replace_path=replace_path,
                attempts=rename_attempts,
                backoff_seconds=rename_backoff_seconds,
                sleep=sleep,
            )
        except Exception as exc:
            _write_activation_journal(journal_path, journal, "failed", exc)
            raise

    try:
        _write_activation_journal(journal_path, journal, "previous_moved")
        _replace_with_retry(
            temporary,
            target,
            replace_path=replace_path,
            attempts=rename_attempts,
            backoff_seconds=rename_backoff_seconds,
            sleep=sleep,
        )
        _write_activation_journal(journal_path, journal, "activated")
        _lightweight_directory_check(
            final_path=target,
            temporary_path=temporary,
            required_paths=entry["required_paths"],
            publication_manifest=publication_manifest,
            expected_run_id=entry["run_id"],
        )

        active_manifest = dict(manifest)
        active_manifest["status"] = "published"
        active_manifest["staged_manifest"] = str(entry["staged_manifest"])
        active_manifest["previous_publication_manifest"] = None
        active_record = dict(entry["record"])
        active_record["published_path"] = str(target)
        active_record["previous_published_path"] = (
            str(backup) if had_previous else None
        )
        active_record["zero_copy_activation"] = zero_copy
        active_manifest["artifacts"] = [active_record]
        _write_json_atomic(publication_manifest, active_manifest)
    except Exception as activation_error:
        try:
            _rollback_directory_activation(
                target=target,
                temporary=temporary,
                backup=backup,
                had_previous=had_previous,
                replace_path=replace_path,
                attempts=rename_attempts,
                backoff_seconds=rename_backoff_seconds,
                sleep=sleep,
            )
            _unlink_owned_files(
                [publication_manifest.with_name(f".{publication_manifest.name}.tmp")],
                entry["published_root"],
            )
            _write_activation_journal(
                journal_path,
                journal,
                "rolled_back",
                activation_error,
            )
        except Exception as rollback_error:
            _write_activation_journal(
                journal_path,
                journal,
                "failed",
                rollback_error,
            )
            raise RuntimeError(
                "Directory publication activation failed and rollback was incomplete: "
                f"{activation_error}"
            ) from rollback_error
        raise

    try:
        _write_activation_journal(journal_path, journal, "committed")
    except Exception as exc:
        raise RuntimeError(
            "Directory publication committed but its activation journal could not "
            "be marked committed"
        ) from exc
    return publication_manifest


def _plan_directory_activation(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    manifest: dict[str, object],
    record: object,
) -> dict[str, object]:
    if not isinstance(record, dict):
        raise ValueError("Publication artifact record must be a JSON object")
    if record.get("kind") != "directory":
        raise ValueError("Directory activation requires a directory artifact")
    if manifest.get("status") != "staged":
        raise ValueError("Publication manifest status must be 'staged'")

    workspace_root = workspace.root.resolve()
    published_root = published.root.resolve(strict=False)
    _reject_filesystem_root(workspace_root)
    _reject_filesystem_root(published_root)
    if _manifest_root(manifest, "workspace_root") != workspace_root:
        raise ValueError("Publication manifest workspace_root does not match the workspace")
    if _manifest_root(manifest, "published_root") != published_root:
        raise ValueError("Publication manifest published_root does not match the published layout")

    run_id = _safe_path_component(str(manifest.get("run_id", "")), "run_id")
    relative_value = record.get("published_relative_path")
    if not isinstance(relative_value, str) or not relative_value:
        raise ValueError("Publication artifact published_relative_path is required")
    relative_path = _validate_relative_path(Path(relative_value))
    target = (published_root / relative_path).resolve(strict=False)
    _require_within(target, published_root)
    if target.exists() and not target.is_dir():
        raise ValueError(f"Published directory target must be a directory: {target}")

    staged_value = record.get("staged_path")
    if not isinstance(staged_value, str) or not staged_value:
        raise ValueError("Publication artifact staged_path is required")
    staged_lexical = Path(os.path.abspath(staged_value))
    staged = staged_lexical.resolve(strict=False)
    temporary = publication_activation_path(published_path=target, run_id=run_id)
    temporary = temporary.resolve(strict=False)
    backup = (
        target.parent / ".previous" / f"{target.name}.{run_id}"
    ).resolve(strict=False)
    journal = temporary.parent / ACTIVATION_JOURNAL_NAME

    _require_within(temporary, published_root)
    _require_within(backup, published_root)
    _require_within(journal.resolve(strict=False), published_root)
    if staged != temporary:
        _require_within(staged, workspace_root)
    _reject_directory_overlap(staged, target)
    if staged != temporary:
        _reject_directory_overlap(staged, temporary)

    if _required_manifest_path(record, "published_path") != target:
        raise ValueError("Publication artifact published_path does not match its relative path")
    file_count = _required_non_negative_int(record, "file_count", "directory")
    size_bytes = _required_non_negative_int(record, "size_bytes", "directory")
    if "total_bytes" in record:
        total_bytes = _required_non_negative_int(record, "total_bytes", "directory")
        if total_bytes != size_bytes:
            raise ValueError("Directory artifact total_bytes must match size_bytes")
    required_paths = _manifest_required_paths(record)

    _reject_reparse_components(
        target.parent,
        Path(os.path.abspath(published.root)),
        "Published directory path",
    )
    _reject_reparse_components(
        Path(os.path.abspath(temporary)),
        Path(os.path.abspath(published.root)),
        "Directory activation temporary path",
    )
    _reject_reparse_components(
        Path(os.path.abspath(backup)),
        Path(os.path.abspath(published.root)),
        "Directory activation backup path",
    )
    _reject_reparse_components(
        staged_lexical,
        (
            Path(os.path.abspath(published.root))
            if staged == temporary
            else Path(os.path.abspath(workspace.root))
        ),
        "Directory publication source",
    )

    return {
        "record": dict(record),
        "run_id": run_id,
        "workspace_root": workspace_root,
        "published_root": published_root,
        "staged_manifest": (workspace.publish / "staged" / PUBLICATION_MANIFEST_NAME).resolve(strict=False),
        "staged": staged,
        "staged_lexical": staged_lexical,
        "target": target,
        "temporary": temporary,
        "backup": backup,
        "journal": journal,
        "file_count": file_count,
        "size_bytes": size_bytes,
        "required_paths": required_paths,
    }


def _active_directory_publication_matches(
    *,
    publication_manifest: Path,
    manifest: dict[str, object],
    entry: dict[str, object],
) -> bool:
    if not publication_manifest.is_file():
        return False
    active = _read_publication_manifest(
        publication_manifest,
        "published publication manifest",
    )
    if active.get("run_id") != manifest.get("run_id"):
        return False
    if active.get("status") != "published":
        raise ValueError("Existing publication for this run is not marked published")
    if active.get("survey_id") != manifest.get("survey_id"):
        raise ValueError("Existing publication survey_id does not match the staged manifest")
    if _manifest_root(active, "published_root") != entry["published_root"]:
        raise ValueError("Existing publication root does not match the published layout")
    records = active.get("artifacts")
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
        raise ValueError("Existing directory publication artifact set does not match")
    active_record = records[0]
    if active_record.get("kind") != "directory":
        raise ValueError("Existing publication artifact is not a directory")
    if _required_manifest_path(active_record, "published_path") != entry["target"]:
        raise ValueError("Existing publication directory target does not match")
    if active_record.get("file_count") != entry["file_count"]:
        raise ValueError("Existing publication directory file count record does not match")
    if active_record.get("size_bytes") != entry["size_bytes"]:
        raise ValueError("Existing publication directory size record does not match")
    if not entry["target"].is_dir():
        raise ValueError("Existing publication directory is missing")
    _validate_required_paths(entry["target"], entry["required_paths"])
    return True


def _rollback_directory_activation(
    *,
    target: Path,
    temporary: Path,
    backup: Path,
    had_previous: bool,
    replace_path: Callable[[Path, Path], object],
    attempts: int,
    backoff_seconds: float,
    sleep: Callable[[float], object],
) -> None:
    if target.is_dir():
        if temporary.exists():
            raise RuntimeError(
                "Directory rollback temporary path is already occupied"
            )
        _replace_with_retry(
            target,
            temporary,
            replace_path=replace_path,
            attempts=attempts,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )
    elif target.exists():
        raise RuntimeError("Directory rollback target is not a directory")
    if had_previous:
        if not backup.is_dir():
            raise RuntimeError("Directory rollback lost the previous publication")
        _replace_with_retry(
            backup,
            target,
            replace_path=replace_path,
            attempts=attempts,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )


def _replace_with_retry(
    source: Path,
    destination: Path,
    *,
    replace_path: Callable[[Path, Path], object],
    attempts: int,
    backoff_seconds: float,
    sleep: Callable[[float], object],
) -> None:
    for attempt in range(attempts):
        try:
            replace_path(source, destination)
            return
        except OSError:
            if attempt + 1 >= attempts:
                raise
            sleep(backoff_seconds * (2 ** attempt))


def _write_activation_journal(
    journal_path: Path,
    journal: dict[str, object],
    status: str,
    error: Exception | None = None,
) -> None:
    payload = dict(journal)
    payload["status"] = status
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["error_message"] = str(error)
    _write_json_atomic(journal_path, payload)


def _lightweight_directory_check(
    *,
    final_path: Path,
    temporary_path: Path,
    required_paths: tuple[Path, ...],
    publication_manifest: Path,
    expected_run_id: str,
) -> None:
    if not final_path.is_dir():
        raise OSError(f"Activated directory is missing: {final_path}")
    if temporary_path.exists():
        raise OSError(
            f"Directory activation temporary path still exists: {temporary_path}"
        )
    _validate_required_paths(final_path, required_paths)
    if publication_manifest.is_file():
        active = _read_publication_manifest(
            publication_manifest,
            "published publication manifest",
        )
        if active.get("run_id") == expected_run_id:
            raise RuntimeError("Publication manifest was committed before validation")


def _manifest_required_paths(record: dict[str, object]) -> tuple[Path, ...]:
    value = record.get("required_paths", [])
    if not isinstance(value, list):
        raise ValueError("Directory artifact required_paths must be a list")
    required: list[Path] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError("Directory artifact required_paths entries must be strings")
        required.append(_validate_relative_path(Path(item)))
    return tuple(required)


def _validate_required_paths(root: Path, required_paths: tuple[Path, ...]) -> None:
    for relative_path in required_paths:
        candidate = root / relative_path
        _require_within(candidate.resolve(strict=False), root.resolve(strict=False))
        _reject_reparse_components(
            Path(os.path.abspath(candidate)),
            Path(os.path.abspath(root)),
            "Required directory publication path",
        )
        if not candidate.exists():
            raise ValueError(
                f"Required directory publication path is missing: {relative_path.as_posix()}"
            )


def _required_non_negative_int(
    record: dict[str, object],
    field_name: str,
    artifact_label: str,
) -> int:
    value = record.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"Publication {artifact_label} artifact {field_name} must be a non-negative integer"
        )
    return value


def _reject_directory_overlap(source: Path, destination: Path) -> None:
    if source == destination:
        raise ValueError("Directory publication source and destination must differ")
    if _path_is_within(source, destination) or _path_is_within(destination, source):
        raise ValueError("Directory publication source and destination must not be nested")


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def activate_publication_with_lock(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    replace_path: Callable[[Path, Path], object] | None = None,
    owner_token: str | None = None,
    created_at: str | None = None,
) -> Path:
    """Activate a staged publication while holding survey publication ownership.

    This helper composes the existing dormant file activation and fail-closed
    publication lock. It remains dormant until a later RGBPipeline integration
    slice calls it.
    """

    staged_manifest = _read_staged_publication_manifest(
        workspace.publish / "staged" / PUBLICATION_MANIFEST_NAME
    )
    run_id = _required_manifest_text(staged_manifest, "run_id")
    survey_id = _required_manifest_text(staged_manifest, "survey_id")
    lock = acquire_publication_lock(
        published_root=published.root,
        run_id=run_id,
        survey_id=survey_id,
        owner_token=owner_token,
        created_at=created_at,
    )
    try:
        return activate_publication(
            workspace=workspace,
            published=published,
            copy_file=copy_file,
            replace_path=replace_path,
        )
    finally:
        release_publication_lock(lock)


def _active_publication_matches_staged(
    *,
    publication_manifest: Path,
    staged_manifest: dict[str, object],
    activation_entries: list[dict[str, object]],
    published_root: Path,
) -> bool:
    if not publication_manifest.is_file():
        return False

    active_manifest = _read_publication_manifest(
        publication_manifest,
        "published publication manifest",
    )
    if active_manifest.get("run_id") != staged_manifest.get("run_id"):
        return False
    if active_manifest.get("status") != "published":
        raise ValueError("Existing publication for this run is not marked published")
    if active_manifest.get("survey_id") != staged_manifest.get("survey_id"):
        raise ValueError("Existing publication survey_id does not match the staged manifest")
    if _manifest_root(active_manifest, "published_root") != published_root:
        raise ValueError("Existing publication root does not match the published layout")

    active_records = _publication_records_by_relative(active_manifest)
    expected_paths = {
        str(entry["record"]["published_relative_path"])
        for entry in activation_entries
    }
    if set(active_records) != expected_paths:
        raise ValueError("Existing publication artifact set does not match the staged manifest")

    for entry in activation_entries:
        relative_path = str(entry["record"]["published_relative_path"])
        active_record = active_records[relative_path]
        if active_record.get("kind") != "file":
            raise ValueError("Existing publication contains a non-file artifact")
        if _required_manifest_path(active_record, "published_path") != entry["target"]:
            raise ValueError("Existing publication artifact target does not match")
        if active_record.get("size_bytes") != entry["size_bytes"]:
            raise ValueError("Existing publication artifact size record does not match")
        target = entry["target"]
        if not target.is_file() or target.stat().st_size != entry["size_bytes"]:
            raise ValueError("Existing publication artifact is missing or has changed size")

    return True


def _reconcile_interrupted_publication(
    *,
    publication_manifest: Path,
    manifest_candidate: Path,
    manifest_candidate_temp: Path,
    previous_manifest: Path,
    staged_manifest: dict[str, object],
    activation_entries: list[dict[str, object]],
    workspace_root: Path,
    published_root: Path,
) -> bool:
    artifact_temporaries = [entry["temporary"] for entry in activation_entries]
    artifact_backups = [entry["backup"] for entry in activation_entries]

    if manifest_candidate.is_file():
        candidate = _read_publication_manifest(
            manifest_candidate,
            "publication activation candidate",
        )
        _validate_activation_candidate(
            candidate=candidate,
            staged_manifest=staged_manifest,
            activation_entries=activation_entries,
            previous_manifest=previous_manifest,
            publication_manifest=publication_manifest,
            workspace_root=workspace_root,
            published_root=published_root,
        )
        candidate_records = _publication_records_by_relative(candidate)

        for entry in reversed(activation_entries):
            relative_path = str(entry["record"]["published_relative_path"])
            candidate_record = candidate_records[relative_path]
            previous_path = candidate_record.get("previous_published_path")
            target = entry["target"]
            temporary = entry["temporary"]
            backup = entry["backup"]

            if previous_path is not None:
                if not isinstance(previous_path, str) or (
                    Path(previous_path).resolve(strict=False) != backup
                ):
                    raise ValueError(
                        "Publication candidate previous artifact path does not match"
                    )
                if backup.is_file():
                    if target.exists() and not target.is_file():
                        raise ValueError(
                            f"Interrupted publication target is not a file: {target}"
                        )
                    if target.is_file():
                        target.unlink()
                    backup.replace(target)
                elif temporary.is_file():
                    if not target.is_file():
                        raise RuntimeError(
                            "Interrupted publication lost its previous artifact"
                        )
                else:
                    raise RuntimeError(
                        "Interrupted publication previous artifact state is ambiguous"
                    )
            else:
                if backup.exists():
                    raise RuntimeError(
                        "Interrupted publication has an unexpected artifact backup"
                    )
                if temporary.is_file():
                    if target.exists():
                        raise RuntimeError(
                            "Interrupted publication target changed before activation"
                        )
                elif target.is_file():
                    target.unlink()
                elif target.exists():
                    raise ValueError(
                        f"Interrupted publication target is not a file: {target}"
                    )

        _unlink_owned_files(
            artifact_temporaries + [manifest_candidate, manifest_candidate_temp],
            published_root,
        )
        return True

    if any(path.exists() for path in artifact_backups):
        raise RuntimeError(
            "Publication backup exists without an activation candidate; "
            "automatic recovery is unsafe"
        )

    if manifest_candidate_temp.exists() or any(
        path.exists() for path in artifact_temporaries
    ):
        if previous_manifest.exists():
            _require_previous_manifest_matches_active(
                previous_manifest=previous_manifest,
                publication_manifest=publication_manifest,
            )
        _unlink_owned_files(
            artifact_temporaries + [manifest_candidate_temp],
            published_root,
        )
        return True

    return False


def _validate_activation_candidate(
    *,
    candidate: dict[str, object],
    staged_manifest: dict[str, object],
    activation_entries: list[dict[str, object]],
    previous_manifest: Path,
    publication_manifest: Path,
    workspace_root: Path,
    published_root: Path,
) -> None:
    if candidate.get("status") != "published":
        raise ValueError("Publication activation candidate is not marked published")
    if candidate.get("run_id") != staged_manifest.get("run_id"):
        raise ValueError("Publication activation candidate run_id does not match")
    if candidate.get("survey_id") != staged_manifest.get("survey_id"):
        raise ValueError("Publication activation candidate survey_id does not match")
    if _manifest_root(candidate, "workspace_root") != workspace_root:
        raise ValueError("Publication activation candidate workspace_root does not match")
    if _manifest_root(candidate, "published_root") != published_root:
        raise ValueError("Publication activation candidate published_root does not match")

    candidate_records = _publication_records_by_relative(candidate)
    expected_paths = {
        str(entry["record"]["published_relative_path"])
        for entry in activation_entries
    }
    if set(candidate_records) != expected_paths:
        raise ValueError("Publication activation candidate artifact set does not match")

    previous_manifest_value = candidate.get("previous_publication_manifest")
    if previous_manifest_value is None:
        if publication_manifest.exists() or previous_manifest.exists():
            raise RuntimeError(
                "Active publication changed during an interrupted activation"
            )
    else:
        if not isinstance(previous_manifest_value, str) or (
            Path(previous_manifest_value).resolve(strict=False) != previous_manifest
        ):
            raise ValueError(
                "Publication activation candidate previous manifest path does not match"
            )
        _require_previous_manifest_matches_active(
            previous_manifest=previous_manifest,
            publication_manifest=publication_manifest,
        )

    for entry in activation_entries:
        relative_path = str(entry["record"]["published_relative_path"])
        record = candidate_records[relative_path]
        if record.get("kind") != "file":
            raise ValueError("Publication activation candidate contains a non-file artifact")
        if _required_manifest_path(record, "staged_path") != entry["staged"]:
            raise ValueError("Publication activation candidate staged path does not match")
        if _required_manifest_path(record, "published_path") != entry["target"]:
            raise ValueError("Publication activation candidate target does not match")
        if record.get("size_bytes") != entry["size_bytes"]:
            raise ValueError("Publication activation candidate size does not match")


def _publication_records_by_relative(
    manifest: dict[str, object],
) -> dict[str, dict[str, object]]:
    records = manifest.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ValueError("Publication manifest must contain at least one artifact")

    by_relative: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Publication artifact record must be a JSON object")
        relative_value = record.get("published_relative_path")
        if not isinstance(relative_value, str) or not relative_value:
            raise ValueError("Publication artifact published_relative_path is required")
        relative_path = _validate_relative_path(Path(relative_value)).as_posix()
        if relative_path in by_relative:
            raise ValueError(f"Duplicate published artifact path: {relative_path}")
        by_relative[relative_path] = record
    return by_relative


def _require_previous_manifest_matches_active(
    *,
    previous_manifest: Path,
    publication_manifest: Path,
) -> None:
    if not previous_manifest.is_file() or not publication_manifest.is_file():
        raise RuntimeError(
            "Previous publication manifest cannot be reconciled with the active manifest"
        )
    if previous_manifest.read_bytes() != publication_manifest.read_bytes():
        raise RuntimeError(
            "Active publication manifest changed; automatic recovery is unsafe"
        )


def _read_publication_manifest(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label.capitalize()} must contain a JSON object")
    return payload


def _read_staged_publication_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read staged publication manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Publication manifest must contain a JSON object")
    return payload


def _manifest_root(manifest: dict[str, object], field_name: str) -> Path:
    value = manifest.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Publication manifest {field_name} is required")
    return Path(value).resolve(strict=False)


def _required_manifest_text(manifest: dict[str, object], field_name: str) -> str:
    value = manifest.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Publication manifest {field_name} is required")
    return value


def _plan_file_activation(
    *,
    record: object,
    run_id: str,
    staged_root: Path,
    published_root: Path,
) -> dict[str, object]:
    if not isinstance(record, dict):
        raise ValueError("Publication artifact record must be a JSON object")
    if record.get("kind") != "file":
        raise ValueError("Publish activation currently supports file artifacts only")

    relative_value = record.get("published_relative_path")
    if not isinstance(relative_value, str) or not relative_value:
        raise ValueError("Publication artifact published_relative_path is required")
    relative_path = _validate_relative_path(Path(relative_value))

    staged = (staged_root / relative_path).resolve(strict=False)
    target = (published_root / relative_path).resolve(strict=False)
    _require_within(staged, staged_root)
    _require_within(target, published_root)

    if not staged.is_file():
        raise FileNotFoundError(staged)
    if target.exists() and not target.is_file():
        raise ValueError(f"Published artifact target must be a file: {target}")
    if _required_manifest_path(record, "staged_path") != staged:
        raise ValueError("Publication artifact staged_path does not match its relative path")
    if _required_manifest_path(record, "published_path") != target:
        raise ValueError("Publication artifact published_path does not match its relative path")

    size_bytes = record.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise ValueError("Publication file artifact size_bytes must be a non-negative integer")
    if staged.stat().st_size != size_bytes:
        raise ValueError(f"Staged publication artifact size mismatch: {staged}")

    return {
        "record": dict(record),
        "staged": staged,
        "target": target,
        "temporary": target.with_name(f".{target.name}.{run_id}.publishing"),
        "backup": target.with_name(f".{target.name}.{run_id}.previous"),
        "size_bytes": size_bytes,
    }


def _required_manifest_path(record: dict[str, object], field_name: str) -> Path:
    value = record.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Publication artifact {field_name} is required")
    return Path(value).resolve(strict=False)


def _activated_artifact_record(entry: dict[str, object]) -> dict[str, object]:
    record = dict(entry["record"])
    target = entry["target"]
    backup = entry["backup"]
    record["published_path"] = str(target)
    record["previous_published_path"] = str(backup) if target.is_file() else None
    return record


def _rollback_file_activation(
    activated: list[dict[str, object]],
    published_root: Path,
) -> None:
    for state in reversed(activated):
        target = state["target"]
        backup = state["backup"]
        _require_within(target.resolve(strict=False), published_root)
        _require_within(backup.resolve(strict=False), published_root)
        if state["installed"] and target.is_file():
            target.unlink()
        if state["had_previous"] and backup.is_file():
            backup.replace(target)


def _unlink_owned_files(paths: Iterable[Path], owner_root: Path) -> None:
    for path in paths:
        resolved = path.resolve(strict=False)
        _require_within(resolved, owner_root)
        if resolved.is_file():
            resolved.unlink()


def _replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def _copy_directory_tree(source: Path, destination: Path) -> None:
    """Copy a directory without following symlinks or Windows reparse points."""

    source = Path(source)
    destination = Path(destination)
    _reject_reparse_path(source, "Directory publication source")
    if not source.is_dir():
        raise FileNotFoundError(source)
    if destination.exists():
        raise FileExistsError(destination)
    destination.mkdir(parents=False)

    def copy_entries(source_dir: Path, destination_dir: Path) -> None:
        with os.scandir(source_dir) as entries:
            for entry in entries:
                entry_stat = _safe_directory_entry_stat(entry)
                source_entry = Path(entry.path)
                destination_entry = destination_dir / entry.name
                if stat.S_ISDIR(entry_stat.st_mode):
                    destination_entry.mkdir()
                    copy_entries(source_entry, destination_entry)
                elif stat.S_ISREG(entry_stat.st_mode):
                    shutil.copy2(source_entry, destination_entry)
                else:
                    raise ValueError(
                        f"Unsupported directory publication entry: {source_entry}"
                    )

    copy_entries(source, destination)


def _scan_directory_tree(root: Path) -> tuple[int, int]:
    """Perform one independent count/size scan and reject unsafe entries."""

    root = Path(root)
    _reject_reparse_path(root, "Directory publication root")
    if not root.is_dir():
        raise FileNotFoundError(root)
    file_count = 0
    total_bytes = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_stat = _safe_directory_entry_stat(entry)
                if stat.S_ISDIR(entry_stat.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(entry_stat.st_mode):
                    file_count += 1
                    total_bytes += entry_stat.st_size
                else:
                    raise ValueError(
                        f"Unsupported directory publication entry: {entry.path}"
                    )
    return file_count, total_bytes


def _safe_directory_entry_stat(entry: os.DirEntry[str]) -> os.stat_result:
    entry_stat = entry.stat(follow_symlinks=False)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(entry_stat, "st_file_attributes", 0)
    if stat.S_ISLNK(entry_stat.st_mode) or file_attributes & reparse_flag:
        raise ValueError(
            f"Directory publication does not support symlinks or reparse points: {entry.path}"
        )
    return entry_stat


def _reject_reparse_path(path: Path, label: str) -> None:
    try:
        path_stat = Path(path).lstat()
    except FileNotFoundError:
        return
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(path_stat, "st_file_attributes", 0)
    if stat.S_ISLNK(path_stat.st_mode) or file_attributes & reparse_flag:
        raise ValueError(f"{label} must not be a symlink or reparse point: {path}")


def _reject_reparse_components(path: Path, owner_root: Path, label: str) -> None:
    path = Path(os.path.abspath(path))
    owner_root = Path(os.path.abspath(owner_root))
    _require_within(path, owner_root)
    current = owner_root
    _reject_reparse_path(current, label)
    for part in path.relative_to(owner_root).parts:
        current = current / part
        _reject_reparse_path(current, label)


def _artifact_record(
    *,
    artifact: PublicationArtifact,
    source_path: Path,
    staged_path: Path,
    published_path: Path,
    relative_path: Path,
) -> dict[str, object]:
    record: dict[str, object] = {
        "logical_name": artifact.logical_name,
        "kind": artifact.kind,
        "source_path": str(source_path),
        "staged_path": str(staged_path),
        "published_path": str(published_path),
        "published_relative_path": relative_path.as_posix(),
    }
    if artifact.kind == "file":
        record["size_bytes"] = staged_path.stat().st_size
    else:
        expected_count = artifact.expected_file_count
        expected_bytes = artifact.expected_total_bytes
        if (expected_count is None) != (expected_bytes is None):
            raise ValueError(
                "Directory artifacts must provide both expected_file_count and "
                "expected_total_bytes, or neither"
            )
        if expected_count is None:
            expected_count, expected_bytes = _scan_directory_tree(staged_path)
        if (
            isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count < 0
            or isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
        ):
            raise ValueError("Directory artifact expected metrics must be non-negative integers")
        required_paths = tuple(
            _validate_relative_path(Path(value)) for value in artifact.required_paths
        )
        _validate_required_paths(staged_path, required_paths)
        record["file_count"] = expected_count
        record["size_bytes"] = expected_bytes
        record["total_bytes"] = expected_bytes
        record["required_paths"] = [path.as_posix() for path in required_paths]
    return record


def _describe_path_dataclass(layout: object) -> dict[str, str]:
    return {
        field.name: str(getattr(layout, field.name))
        for field in fields(layout)
    }


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _safe_path_component(value: str, field_name: str) -> str:
    if not value:
        raise ValueError(f"{field_name} is required")
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or value in {".", ".."}:
        raise ValueError(f"{field_name} must be a single safe path component")
    return value


def _validate_relative_path(path: Path) -> Path:
    relative_path = Path(path)
    if relative_path.is_absolute() or relative_path.drive:
        raise ValueError("Published artifact path must be relative")
    if not relative_path.parts or any(part in {"", ".", ".."} for part in relative_path.parts):
        raise ValueError("Published artifact path must not escape the published root")
    return relative_path


def _reject_filesystem_root(path: Path) -> None:
    if path.parent == path:
        raise ValueError("Workspace root must not be a filesystem root")


def _require_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes its owner root: {path}") from exc
