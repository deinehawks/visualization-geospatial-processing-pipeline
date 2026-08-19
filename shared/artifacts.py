from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping

from shared.publication_lock import (
    PUBLICATION_LOCK_NAME,
    acquire_publication_lock,
    release_publication_lock,
)


ArtifactKind = Literal["file", "directory"]

PUBLICATION_MANIFEST_NAME = "publication.json"
ACTIVATION_JOURNAL_NAME = "activation.json"
ARTIFACT_CLEANUP_SENTINEL_NAME = ".artifact-cleanup-root"
ARTIFACT_CLEANUP_AUDIT_DIR = ".artifact-cleanup-audit"
RUN_WORKSPACE_OWNERSHIP_NAME = ".run-workspace.json"


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


CleanupCandidateKind = Literal["file", "directory"]


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    kind: CleanupCandidateKind
    reason: str
    run_id: str
    owner_root: Path


@dataclass(frozen=True)
class CleanupPlan:
    candidates: tuple[CleanupCandidate, ...]
    protected_run_ids: tuple[str, ...]
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True)
class CleanupDeletion:
    path: Path
    kind: CleanupCandidateKind
    run_id: str
    owner_root: Path


@dataclass(frozen=True)
class CleanupExecutionResult:
    dry_run: bool
    deleted: tuple[CleanupDeletion, ...]
    skipped: tuple[CleanupCandidate, ...]
    blocked_reasons: tuple[str, ...]
    audit_path: Path | None = None


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
    root_existed = root.exists()
    for directory in layout.required_dirs():
        resolved = directory.resolve(strict=False)
        _require_within(resolved, root)
        resolved.mkdir(parents=True, exist_ok=True)

    ownership_path = root / RUN_WORKSPACE_OWNERSHIP_NAME
    if ownership_path.exists():
        ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
        expected_run_id = _safe_path_component(root.name, "workspace run_id")
        if ownership.get("run_id") != expected_run_id:
            raise ValueError(
                f"Run workspace ownership does not match its directory: {ownership_path}"
            )
        return

    # Existing workspaces predate ownership evidence. Leave them usable for
    # resume, but do not retroactively authorize automatic deletion.
    if root_existed:
        return

    _write_json_atomic(
        ownership_path,
        {
            "version": 1,
            "run_id": _safe_path_component(root.name, "workspace run_id"),
            "workspace_root": str(root.parent),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def cleanup_completed_run_workspace(
    *,
    run_id: str,
    survey_id: str,
    workspace: RunWorkspaceLayout,
    workspace_root: Path,
    published: PublishedSurveyLayout,
    output_pairs: Iterable[tuple[Path, Path]],
    stage_summaries: Mapping[str, object],
    image_classifications: Iterable[Mapping[str, object]] = (),
    remove_tree: Callable[[Path], object] = shutil.rmtree,
    write_json: Callable[[Path, dict[str, object]], object] | None = None,
) -> dict[str, object]:
    """Archive evidence and delete exactly one verified completed-run workspace."""

    safe_run_id = _safe_path_component(run_id, "run_id")
    safe_survey_id = _safe_path_component(survey_id, "survey_id")
    owner_root = Path(workspace_root).resolve(strict=False)
    target = workspace.root.resolve(strict=False)
    expected_target = (owner_root / safe_run_id).resolve(strict=False)
    published_root = published.root.resolve(strict=False)
    _reject_filesystem_root(owner_root)
    _reject_filesystem_root(target)
    _reject_filesystem_root(published_root)
    _require_within(target, owner_root)
    if target != expected_target:
        raise ValueError(
            f"Run workspace does not match configured workspace root and run_id: {target}"
        )
    if not target.is_dir():
        raise ValueError(f"Run workspace is missing or is not a directory: {target}")

    ownership_path = target / RUN_WORKSPACE_OWNERSHIP_NAME
    if not ownership_path.is_file():
        raise ValueError(f"Run workspace ownership evidence is missing: {ownership_path}")
    ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
    if ownership.get("run_id") != safe_run_id:
        raise ValueError(f"Run workspace ownership run_id does not match: {ownership_path}")
    recorded_owner_root = Path(str(ownership.get("workspace_root") or "")).resolve(
        strict=False
    )
    if recorded_owner_root != owner_root:
        raise ValueError(
            f"Run workspace ownership root does not match configured root: {ownership_path}"
        )

    verified_outputs: list[dict[str, object]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for source_value, published_value in output_pairs:
        source = Path(source_value).resolve(strict=False)
        destination = Path(published_value).resolve(strict=False)
        _require_within(source, target)
        _require_within(destination, published_root)
        key = (str(source), str(destination))
        if key in seen_pairs or not source.exists():
            continue
        seen_pairs.add(key)
        if source.is_file():
            if not destination.is_file():
                raise ValueError(f"Required published file is missing: {destination}")
            if destination.stat().st_size != source.stat().st_size:
                raise ValueError(
                    f"Required published file size does not match: {destination}"
                )
            kind = "file"
        elif source.is_dir():
            if not destination.is_dir():
                raise ValueError(f"Required published directory is missing: {destination}")
            kind = "directory"
            source_files = [
                path
                for path in source.rglob("*")
                if path.is_file()
            ]
            for source_file in source_files:
                relative_path = source_file.relative_to(source)
                published_file = destination / relative_path
                if not published_file.is_file():
                    raise ValueError(
                        f"Required published file is missing: {published_file}"
                    )
                if published_file.stat().st_size != source_file.stat().st_size:
                    raise ValueError(
                        f"Required published file size does not match: {published_file}"
                    )
        else:
            raise ValueError(f"Workspace output has unsupported type: {source}")
        output_record: dict[str, object] = {
            "workspace_relative_path": source.relative_to(target).as_posix(),
            "published_relative_path": destination.relative_to(published_root).as_posix(),
            "kind": kind,
        }
        if kind == "directory":
            output_record["file_count"] = len(source_files)
        verified_outputs.append(output_record)
    if not verified_outputs:
        raise ValueError("No run workspace outputs were available for publication verification")

    inventory: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(target.rglob("*"), key=lambda item: str(item).lower()):
        if not path.is_file():
            continue
        stat_result = path.stat()
        total_bytes += stat_result.st_size
        inventory.append(
            {
                "relative_path": path.relative_to(target).as_posix(),
                "size_bytes": stat_result.st_size,
                "modified_at_epoch": stat_result.st_mtime,
            }
        )

    audit_path = (
        published_root
        / ARTIFACT_CLEANUP_AUDIT_DIR
        / f"completed-run-{safe_run_id}.json"
    )
    writer = write_json or _write_json_atomic
    payload: dict[str, object] = {
        "version": 1,
        "status": "prepared",
        "run_id": safe_run_id,
        "survey_id": safe_survey_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workspace_root": str(target),
        "file_count": len(inventory),
        "total_bytes": total_bytes,
        "files": inventory,
        "image_classifications": [dict(item) for item in image_classifications],
        "stage_summaries": dict(stage_summaries),
        "verified_outputs": verified_outputs,
    }
    writer(audit_path, payload)

    try:
        remove_tree(target)
    except Exception as exc:
        payload["status"] = "failed"
        payload["error_type"] = type(exc).__name__
        payload["error"] = str(exc)
        payload["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
        )
        writer(audit_path, payload)
        return {
            "status": "failed",
            "workspace": str(target),
            "audit_path": str(audit_path),
            "error_type": type(exc).__name__,
            "error": str(exc),
        }

    payload["status"] = "completed"
    payload["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    writer(audit_path, payload)
    return {
        "status": "completed",
        "workspace": str(target),
        "audit_path": str(audit_path),
        "file_count": len(inventory),
        "total_bytes": total_bytes,
    }


def prepare_publication(
    *,
    run_id: str,
    survey_id: str,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    artifacts: Iterable[PublicationArtifact],
    copy_file: Callable[[Path, Path], object] = shutil.copy2,
    copy_tree: Callable[[Path, Path], object] | None = None,
    stage_directories_for_activation: bool = False,
) -> Path:
    """Stage a complete publish set inside the run workspace.

    This function prepares a validated, manifest-backed publish set for later
    activation. Directory output already generated at the exact run-specific
    hidden activation path is recorded in place without copying it again. When
    stage_directories_for_activation is enabled, directory artifacts are copied
    directly to that hidden same-filesystem activation path so activation can
    rename them into place without a second full directory copy.
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
        elif artifact.kind == "directory" and stage_directories_for_activation:
            staged_path = publication_activation_path(
                published_path=published_path,
                run_id=run_id,
            )
            staged_resolved = staged_path.resolve(strict=False)
            _require_within(staged_resolved, published.root.resolve(strict=False))
            if staged_path.exists():
                raise FileExistsError(staged_path)
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            tree_copier(source_path, staged_path)
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




def reconcile_directory_publication(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    replace_path: Callable[[Path, Path], object] | None = None,
    directory_scan: Callable[[Path], tuple[int, int]] | None = None,
    rename_attempts: int = 3,
    rename_backoff_seconds: float = 0.05,
    sleep: Callable[[float], object] = time.sleep,
) -> Path:
    """Reconcile one interrupted directory activation to committed state.

    ``publication.json`` is authoritative. If it already names this run, the
    journal is finalized as committed. Otherwise incomplete rename states are
    rolled back to the prior committed view. The caller must provide exclusive
    publication ownership; stale-lock recovery remains a separate concern.
    """

    if rename_attempts < 1:
        raise ValueError("rename_attempts must be at least 1")
    if rename_backoff_seconds < 0:
        raise ValueError("rename_backoff_seconds must be non-negative")

    staged_manifest = _read_staged_publication_manifest(
        workspace.publish / "staged" / PUBLICATION_MANIFEST_NAME
    )
    records = staged_manifest.get("artifacts")
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError(
            "Directory reconciliation requires exactly one staged artifact"
        )
    record = records[0]
    if not isinstance(record, dict) or record.get("kind") != "directory":
        raise ValueError(
            "Directory reconciliation requires exactly one directory artifact"
        )

    entry = _plan_directory_activation(
        workspace=workspace,
        published=published,
        manifest=staged_manifest,
        record=record,
    )
    journal_path = entry["journal"]
    journal = _read_activation_journal(journal_path)
    _validate_directory_activation_journal(journal=journal, entry=entry)

    publication_manifest = published.publication_manifest.resolve(strict=False)
    active_run_id = _active_publication_run_id(publication_manifest)
    current_run_id = entry["run_id"]
    status = journal["status"]

    if active_run_id == current_run_id:
        if status not in {"activated", "committed"}:
            raise RuntimeError(
                "Publication manifest names the interrupted run but the activation "
                f"journal status is {status!r}"
            )
        if not _active_directory_publication_matches(
            publication_manifest=publication_manifest,
            manifest=staged_manifest,
            entry=entry,
        ):
            raise RuntimeError("Committed directory publication could not be validated")
        if status == "activated":
            recovered = dict(journal)
            recovered["recovered_interrupted_activation"] = True
            recovered["recovered_from_status"] = "activated"
            _write_activation_journal(
                journal_path,
                recovered,
                "committed",
            )
        return journal_path

    previous_run_id = journal["previous_publication_run_id"]
    if active_run_id != previous_run_id:
        raise RuntimeError(
            "Active publication changed after directory activation evidence was written"
        )
    if status == "committed":
        raise RuntimeError(
            "Activation journal claims committed but publication.json names another run"
        )
    if status == "failed":
        raise RuntimeError(
            "Failed directory activation evidence requires explicit diagnosis"
        )

    temporary = entry["temporary"]
    target = entry["target"]
    backup = entry["backup"]
    had_previous = journal["had_previous"]
    temporary_state = _directory_path_state(
        temporary,
        "Directory activation temporary path",
    )
    target_state = _directory_path_state(
        target,
        "Published directory path",
    )
    backup_state = _directory_path_state(
        backup,
        "Directory activation backup path",
    )
    scan = directory_scan or _scan_directory_tree
    replace = replace_path or _replace_path

    if status == "prepared":
        _validate_reconciliation_candidate(temporary, temporary_state, entry, scan)
        if had_previous and target_state == "directory" and backup_state == "missing":
            return journal_path
        if not had_previous and target_state == "missing" and backup_state == "missing":
            return journal_path
        if had_previous and target_state == "missing" and backup_state == "directory":
            try:
                _replace_with_retry(
                    backup,
                    target,
                    replace_path=replace,
                    attempts=rename_attempts,
                    backoff_seconds=rename_backoff_seconds,
                    sleep=sleep,
                )
            except Exception as exc:
                _record_reconciliation_failure(journal_path, journal, exc)
                raise
            _write_reconciled_rollback(journal_path, journal, "prepared")
            return journal_path
        raise RuntimeError("Prepared directory activation evidence is ambiguous")

    if status == "rolled_back":
        _validate_reconciliation_candidate(temporary, temporary_state, entry, scan)
        if had_previous:
            if target_state != "directory" or backup_state != "missing":
                raise RuntimeError("Rolled-back directory activation evidence is ambiguous")
        elif target_state != "missing" or backup_state != "missing":
            raise RuntimeError("Rolled-back directory activation evidence is ambiguous")
        return journal_path

    if status not in {"previous_moved", "activated"}:
        raise ValueError(f"Unsupported directory activation journal status: {status}")

    if had_previous and backup_state == "missing":
        if temporary_state == "directory" and target_state == "directory":
            _validate_reconciliation_candidate(temporary, temporary_state, entry, scan)
            _write_reconciled_rollback(journal_path, journal, status)
            return journal_path
        raise RuntimeError("Interrupted directory activation lost its previous backup")
    if had_previous and backup_state != "directory":
        raise RuntimeError("Interrupted directory activation backup is ambiguous")
    if not had_previous and backup_state != "missing":
        raise RuntimeError("Interrupted directory activation has an unexpected backup")

    if temporary_state == "directory" and target_state == "missing":
        candidate = temporary
    elif temporary_state == "missing" and target_state == "directory":
        candidate = target
    else:
        raise RuntimeError("Interrupted directory activation rename state is ambiguous")
    _validate_reconciliation_candidate(
        candidate,
        "directory",
        entry,
        scan,
    )

    try:
        if candidate == target:
            _replace_with_retry(
                target,
                temporary,
                replace_path=replace,
                attempts=rename_attempts,
                backoff_seconds=rename_backoff_seconds,
                sleep=sleep,
            )
        if had_previous:
            _replace_with_retry(
                backup,
                target,
                replace_path=replace,
                attempts=rename_attempts,
                backoff_seconds=rename_backoff_seconds,
                sleep=sleep,
            )
    except Exception as exc:
        _record_reconciliation_failure(journal_path, journal, exc)
        raise

    _write_reconciled_rollback(journal_path, journal, status)
    return journal_path


def _read_activation_journal(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read directory activation journal: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Directory activation journal must contain a JSON object")
    return payload


def _validate_directory_activation_journal(
    *,
    journal: dict[str, object],
    entry: dict[str, object],
) -> None:
    if journal.get("journal_version") != 1:
        raise ValueError("Unsupported directory activation journal version")
    if journal.get("output_type") != "directory":
        raise ValueError("Activation journal output_type must be 'directory'")
    if journal.get("run_id") != entry["run_id"]:
        raise ValueError("Activation journal run_id does not match")

    expected_paths = {
        "source": entry["staged"],
        "temporary": entry["temporary"],
        "final": entry["target"],
        "previous": entry["backup"],
    }
    for field_name, expected_path in expected_paths.items():
        value = journal.get(field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Activation journal {field_name} is required")
        if Path(value).resolve(strict=False) != expected_path:
            raise ValueError(f"Activation journal {field_name} path does not match")

    zero_copy = journal.get("zero_copy")
    if not isinstance(zero_copy, bool) or zero_copy != (
        entry["staged"] == entry["temporary"]
    ):
        raise ValueError("Activation journal zero_copy value does not match")
    journal_file_count = journal.get("expected_file_count")
    if (
        isinstance(journal_file_count, bool)
        or not isinstance(journal_file_count, int)
        or journal_file_count != entry["file_count"]
    ):
        raise ValueError("Activation journal expected_file_count does not match")
    journal_total_bytes = journal.get("expected_total_bytes")
    if (
        isinstance(journal_total_bytes, bool)
        or not isinstance(journal_total_bytes, int)
        or journal_total_bytes != entry["size_bytes"]
    ):
        raise ValueError("Activation journal expected_total_bytes does not match")
    if not isinstance(journal.get("had_previous"), bool):
        raise ValueError("Activation journal had_previous must be a boolean")

    previous_run_id = journal.get("previous_publication_run_id")
    if previous_run_id is not None:
        if not isinstance(previous_run_id, str):
            raise ValueError(
                "Activation journal previous_publication_run_id must be a string or null"
            )
        _safe_path_component(previous_run_id, "previous_publication_run_id")

    status = journal.get("status")
    if status not in {
        "prepared",
        "previous_moved",
        "activated",
        "committed",
        "rolled_back",
        "failed",
    }:
        raise ValueError(f"Unsupported directory activation journal status: {status}")


def _active_publication_run_id(publication_manifest: Path) -> str | None:
    if not publication_manifest.exists():
        return None
    if not publication_manifest.is_file():
        raise ValueError("Published publication manifest path must be a file")
    manifest = _read_publication_manifest(
        publication_manifest,
        "published publication manifest",
    )
    if manifest.get("status") != "published":
        raise ValueError("Existing publication manifest is not marked published")
    return _safe_path_component(
        _required_manifest_text(manifest, "run_id"),
        "published run_id",
    )


def _directory_path_state(path: Path, label: str) -> str:
    _reject_reparse_path(path, label)
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    raise ValueError(f"{label} must be a directory when present: {path}")


def _validate_reconciliation_candidate(
    candidate: Path,
    candidate_state: str,
    entry: dict[str, object],
    directory_scan: Callable[[Path], tuple[int, int]],
) -> None:
    if candidate_state != "directory":
        raise RuntimeError("Interrupted directory activation candidate is missing")
    file_count, total_bytes = directory_scan(candidate)
    if file_count != entry["file_count"]:
        raise ValueError("Interrupted directory activation file count does not match")
    if total_bytes != entry["size_bytes"]:
        raise ValueError("Interrupted directory activation total size does not match")
    _validate_required_paths(candidate, entry["required_paths"])


def _write_reconciled_rollback(
    journal_path: Path,
    journal: dict[str, object],
    recovered_from_status: str,
) -> None:
    recovered = dict(journal)
    recovered["recovered_interrupted_activation"] = True
    recovered["recovered_from_status"] = recovered_from_status
    recovered.pop("reconciliation_status", None)
    recovered.pop("reconciliation_error_type", None)
    recovered.pop("reconciliation_error_message", None)
    _write_activation_journal(journal_path, recovered, "rolled_back")


def _record_reconciliation_failure(
    journal_path: Path,
    journal: dict[str, object],
    error: Exception,
) -> None:
    failed = dict(journal)
    failed["reconciliation_status"] = "failed"
    failed["reconciliation_error_type"] = type(error).__name__
    failed["reconciliation_error_message"] = str(error)
    try:
        _write_json_atomic(journal_path, failed)
    except Exception as journal_error:
        raise RuntimeError(
            "Directory reconciliation failed and its journal could not be updated"
        ) from journal_error


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

    previous_publication_run_id = _active_publication_run_id(publication_manifest)
    source = entry["staged"]
    source_lexical = entry["staged_lexical"]
    temporary = entry["temporary"]
    target = entry["target"]
    backup = entry["backup"]
    journal_path = entry["journal"]
    activation_root = journal_path.parent
    zero_copy = source == temporary

    activation_root.mkdir(parents=True, exist_ok=True)
    had_previous = target.is_dir()
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
        "had_previous": had_previous,
        "previous_publication_run_id": previous_publication_run_id,
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


def plan_artifact_cleanup(
    *,
    published: PublishedSurveyLayout,
    workspace_root: Path | None = None,
    preserve_run_ids: Iterable[str] = (),
    min_age_seconds: float = 0,
    now: float | None = None,
) -> CleanupPlan:
    """Build a non-destructive cleanup plan for Phase 3 artifacts.

    The planner never deletes files. It reports terminal publication evidence
    and old run workspaces that are eligible for a later explicit cleanup
    executor. Any active lock, non-terminal journal, malformed run ID, or
    ambiguous evidence becomes a blocked reason instead of a cleanup candidate.
    """

    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be non-negative")
    reference_time = time.time() if now is None else now
    published_root = published.root.resolve(strict=False)
    _reject_filesystem_root(published_root)
    protected = {_safe_path_component(run_id, "preserve_run_id") for run_id in preserve_run_ids}
    active_run_id = _active_publication_run_id(published.publication_manifest.resolve(strict=False))
    if active_run_id is not None:
        protected.add(active_run_id)

    lock_path = published_root / PUBLICATION_LOCK_NAME
    if lock_path.exists():
        return CleanupPlan(
            candidates=(),
            protected_run_ids=tuple(sorted(protected)),
            blocked_reasons=(f"Publication lock exists: {lock_path}",),
        )

    candidates: list[CleanupCandidate] = []
    blocked: list[str] = []
    activation_root = published_root / ".activation"
    if activation_root.exists():
        if not activation_root.is_dir():
            blocked.append(f"Publication activation root is not a directory: {activation_root}")
        else:
            for run_dir in sorted(activation_root.iterdir(), key=lambda item: item.name):
                if not run_dir.is_dir():
                    blocked.append(f"Unexpected publication activation entry: {run_dir}")
                    continue
                try:
                    run_id = _safe_path_component(run_dir.name, "publication cleanup run_id")
                except ValueError as exc:
                    blocked.append(str(exc))
                    continue
                if run_id in protected:
                    continue
                _plan_terminal_publication_evidence(
                    run_id=run_id,
                    run_dir=run_dir,
                    published_root=published_root,
                    min_age_seconds=min_age_seconds,
                    now=reference_time,
                    candidates=candidates,
                    blocked=blocked,
                )

    if workspace_root is not None:
        workspace_resolved = Path(workspace_root).resolve(strict=False)
        _reject_filesystem_root(workspace_resolved)
        if workspace_resolved.exists():
            if not workspace_resolved.is_dir():
                blocked.append(f"Workspace root is not a directory: {workspace_resolved}")
            else:
                for run_dir in sorted(workspace_resolved.iterdir(), key=lambda item: item.name):
                    if run_dir.name == ARTIFACT_CLEANUP_SENTINEL_NAME:
                        continue
                    if not run_dir.is_dir():
                        blocked.append(f"Unexpected workspace entry: {run_dir}")
                        continue
                    try:
                        run_id = _safe_path_component(run_dir.name, "workspace cleanup run_id")
                    except ValueError as exc:
                        blocked.append(str(exc))
                        continue
                    if run_id in protected:
                        continue
                    _add_cleanup_candidate_if_old_enough(
                        path=run_dir,
                        kind="directory",
                        reason="run workspace is not active or protected",
                        run_id=run_id,
                        owner_root=workspace_resolved,
                        min_age_seconds=min_age_seconds,
                        now=reference_time,
                        candidates=candidates,
                        blocked=blocked,
                    )

    return CleanupPlan(
        candidates=tuple(candidates),
        protected_run_ids=tuple(sorted(protected)),
        blocked_reasons=tuple(blocked),
    )


def _plan_terminal_publication_evidence(
    *,
    run_id: str,
    run_dir: Path,
    published_root: Path,
    min_age_seconds: float,
    now: float,
    candidates: list[CleanupCandidate],
    blocked: list[str],
) -> None:
    journals = [
        run_dir / "publication-set.json",
        run_dir / ACTIVATION_JOURNAL_NAME,
    ]
    existing_journals = [journal for journal in journals if journal.exists()]
    if not existing_journals:
        blocked.append(f"Publication activation directory has no known journal: {run_dir}")
        return
    parsed_journals: list[dict[str, object]] = []
    blocked_count = len(blocked)
    for journal_path in existing_journals:
        try:
            journal = _read_activation_journal(journal_path)
        except Exception as exc:
            blocked.append(f"Unable to read cleanup journal {journal_path}: {exc}")
            continue
        journal_run_id = journal.get("run_id")
        if journal_run_id != run_id:
            blocked.append(f"Cleanup journal run_id does not match its directory: {journal_path}")
            continue
        status = journal.get("status")
        if status not in {"committed", "rolled_back"}:
            blocked.append(f"Cleanup journal is not terminal: {journal_path} status={status!r}")
            continue
        parsed_journals.append(journal)

    if len(blocked) != blocked_count:
        return

    _add_cleanup_candidate_if_old_enough(
        path=run_dir,
        kind="directory",
        reason="terminal publication activation evidence",
        run_id=run_id,
        owner_root=published_root,
        min_age_seconds=min_age_seconds,
        now=now,
        candidates=candidates,
        blocked=blocked,
    )
    for journal in parsed_journals:
        _plan_journal_previous_paths(
            journal=journal,
            run_id=run_id,
            published_root=published_root,
            min_age_seconds=min_age_seconds,
            now=now,
            candidates=candidates,
            blocked=blocked,
        )


def _plan_journal_previous_paths(
    *,
    journal: dict[str, object],
    run_id: str,
    published_root: Path,
    min_age_seconds: float,
    now: float,
    candidates: list[CleanupCandidate],
    blocked: list[str],
) -> None:
    previous_manifest = journal.get("previous_publication_manifest")
    if isinstance(previous_manifest, str) and previous_manifest:
        _add_path_value_cleanup_candidate(
            path_value=previous_manifest,
            kind="file",
            reason="previous publication manifest retained after terminal activation",
            run_id=run_id,
            owner_root=published_root,
            min_age_seconds=min_age_seconds,
            now=now,
            candidates=candidates,
            blocked=blocked,
        )
    previous_directory = journal.get("previous")
    if isinstance(previous_directory, str) and previous_directory:
        _add_path_value_cleanup_candidate(
            path_value=previous_directory,
            kind="directory",
            reason="previous directory publication retained after terminal activation",
            run_id=run_id,
            owner_root=published_root,
            min_age_seconds=min_age_seconds,
            now=now,
            candidates=candidates,
            blocked=blocked,
        )
    records = journal.get("artifacts")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                blocked.append("Mixed publication-set cleanup journal contains non-object artifact")
                continue
            path_value = record.get("previous")
            kind_value = record.get("kind")
            if isinstance(path_value, str) and path_value:
                if kind_value not in {"file", "directory"}:
                    blocked.append("Mixed publication-set cleanup artifact has unknown kind")
                    continue
                _add_path_value_cleanup_candidate(
                    path_value=path_value,
                    kind=kind_value,
                    reason="previous mixed publication artifact retained after terminal activation",
                    run_id=run_id,
                    owner_root=published_root,
                    min_age_seconds=min_age_seconds,
                    now=now,
                    candidates=candidates,
                    blocked=blocked,
                )


def _add_path_value_cleanup_candidate(
    *,
    path_value: str,
    kind: CleanupCandidateKind,
    reason: str,
    run_id: str,
    owner_root: Path,
    min_age_seconds: float,
    now: float,
    candidates: list[CleanupCandidate],
    blocked: list[str],
) -> None:
    path = Path(path_value).resolve(strict=False)
    try:
        _require_within(path, owner_root)
    except ValueError as exc:
        blocked.append(str(exc))
        return
    if not path.exists():
        return
    if kind == "file" and not path.is_file():
        blocked.append(f"Cleanup candidate is not a file: {path}")
        return
    if kind == "directory" and not path.is_dir():
        blocked.append(f"Cleanup candidate is not a directory: {path}")
        return
    _add_cleanup_candidate_if_old_enough(
        path=path,
        kind=kind,
        reason=reason,
        run_id=run_id,
        owner_root=owner_root,
        min_age_seconds=min_age_seconds,
        now=now,
        candidates=candidates,
        blocked=blocked,
    )


def _add_cleanup_candidate_if_old_enough(
    *,
    path: Path,
    kind: CleanupCandidateKind,
    reason: str,
    run_id: str,
    owner_root: Path,
    min_age_seconds: float,
    now: float,
    candidates: list[CleanupCandidate],
    blocked: list[str],
) -> None:
    resolved = path.resolve(strict=False)
    root = owner_root.resolve(strict=False)
    try:
        _require_within(resolved, root)
    except ValueError as exc:
        blocked.append(str(exc))
        return
    try:
        modified_at = resolved.stat().st_mtime
    except OSError as exc:
        blocked.append(f"Unable to stat cleanup candidate {resolved}: {exc}")
        return
    if now - modified_at < min_age_seconds:
        return
    candidate = CleanupCandidate(
        path=resolved,
        kind=kind,
        reason=reason,
        run_id=run_id,
        owner_root=root,
    )
    if candidate not in candidates:
        candidates.append(candidate)


def execute_artifact_cleanup(
    *,
    plan: CleanupPlan,
    published: PublishedSurveyLayout,
    workspace_root: Path | None = None,
    preserve_run_ids: Iterable[str] = (),
    min_age_seconds: float = 0,
    now: float | None = None,
    allow_delete: bool = False,
    cleanup_id: str | None = None,
    sentinel_name: str = ARTIFACT_CLEANUP_SENTINEL_NAME,
    audit_root: Path | None = None,
    remove_file: Callable[[Path], object] = os.remove,
    remove_tree: Callable[[Path], object] = shutil.rmtree,
) -> CleanupExecutionResult:
    """Execute a cleanup plan only after revalidation and explicit approval.

    The default mode is a dry run. Deletion requires ``allow_delete=True``, a
    clean caller-supplied plan, an unchanged fresh plan, and an ownership
    sentinel at every candidate owner root.
    """

    fresh_plan = plan_artifact_cleanup(
        published=published,
        workspace_root=workspace_root,
        preserve_run_ids=preserve_run_ids,
        min_age_seconds=min_age_seconds,
        now=now,
    )
    blocked = list(plan.blocked_reasons) + list(fresh_plan.blocked_reasons)
    requested = {_cleanup_candidate_key(candidate): candidate for candidate in plan.candidates}
    fresh = {_cleanup_candidate_key(candidate): candidate for candidate in fresh_plan.candidates}
    stale_keys = sorted(set(requested) - set(fresh))
    if stale_keys:
        blocked.append("Cleanup plan changed before execution; rerun planning")

    if not allow_delete:
        return CleanupExecutionResult(
            dry_run=True,
            deleted=(),
            skipped=tuple(plan.candidates),
            blocked_reasons=tuple(blocked),
        )
    if blocked:
        return CleanupExecutionResult(
            dry_run=False,
            deleted=(),
            skipped=tuple(plan.candidates),
            blocked_reasons=tuple(blocked),
        )

    candidate_list = [fresh[key] for key in sorted(requested)]
    if not candidate_list:
        return CleanupExecutionResult(
            dry_run=False,
            deleted=(),
            skipped=(),
            blocked_reasons=(),
        )
    sentinel_errors = _validate_cleanup_sentinels(
        candidate_list,
        sentinel_name,
        extra_roots=(published.root,),
    )
    if sentinel_errors:
        return CleanupExecutionResult(
            dry_run=False,
            deleted=(),
            skipped=tuple(candidate_list),
            blocked_reasons=tuple(sentinel_errors),
        )

    audit_path = _cleanup_audit_path(
        published=published,
        audit_root=audit_root,
        cleanup_id=cleanup_id,
    )
    started_payload = _cleanup_audit_payload(
        status="started",
        dry_run=False,
        candidates=candidate_list,
        deleted=[],
        blocked=[],
    )
    _write_json_atomic(audit_path, started_payload)

    deleted: list[CleanupDeletion] = []
    remaining: list[CleanupCandidate] = []
    attempts: list[dict[str, object]] = []
    for candidate in candidate_list:
        try:
            _delete_cleanup_candidate(
                candidate,
                remove_file=remove_file,
                remove_tree=remove_tree,
            )
        except Exception as exc:
            blocked.append(f"Unable to delete cleanup candidate {candidate.path}: {exc}")
            remaining.append(candidate)
            attempts.append(
                {
                    "path": str(candidate.path),
                    "kind": candidate.kind,
                    "run_id": candidate.run_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            break
        deletion = CleanupDeletion(
            path=candidate.path,
            kind=candidate.kind,
            run_id=candidate.run_id,
            owner_root=candidate.owner_root,
        )
        deleted.append(deletion)
        attempts.append(
            {
                "path": str(candidate.path),
                "kind": candidate.kind,
                "run_id": candidate.run_id,
                "status": "deleted",
            }
        )
    if blocked:
        deleted_keys = {_cleanup_deletion_key(deletion) for deletion in deleted}
        remaining.extend(
            candidate
            for candidate in candidate_list
            if _cleanup_candidate_key(candidate) not in deleted_keys
            and candidate not in remaining
        )
    final_payload = _cleanup_audit_payload(
        status="failed" if blocked else "completed",
        dry_run=False,
        candidates=candidate_list,
        deleted=deleted,
        blocked=blocked,
    )
    final_payload["attempts"] = attempts
    _write_json_atomic(audit_path, final_payload)
    return CleanupExecutionResult(
        dry_run=False,
        deleted=tuple(deleted),
        skipped=tuple(remaining),
        blocked_reasons=tuple(blocked),
        audit_path=audit_path,
    )


def _cleanup_candidate_key(candidate: CleanupCandidate) -> tuple[str, str, str, str]:
    return (
        str(candidate.path.resolve(strict=False)),
        candidate.kind,
        candidate.run_id,
        str(candidate.owner_root.resolve(strict=False)),
    )


def _cleanup_deletion_key(deletion: CleanupDeletion) -> tuple[str, str, str, str]:
    return (
        str(deletion.path.resolve(strict=False)),
        deletion.kind,
        deletion.run_id,
        str(deletion.owner_root.resolve(strict=False)),
    )


def _validate_cleanup_sentinels(
    candidates: Iterable[CleanupCandidate],
    sentinel_name: str,
    *,
    extra_roots: Iterable[Path] = (),
) -> list[str]:
    if not sentinel_name:
        raise ValueError("sentinel_name is required")
    sentinel_component = _safe_path_component(sentinel_name, "sentinel_name")
    errors: list[str] = []
    checked_roots: set[Path] = set()
    roots = [candidate.owner_root for candidate in candidates]
    roots.extend(Path(root) for root in extra_roots)
    for root_value in roots:
        root = root_value.resolve(strict=False)
        if root in checked_roots:
            continue
        checked_roots.add(root)
        _reject_filesystem_root(root)
        sentinel = root / sentinel_component
        if not sentinel.is_file():
            errors.append(f"Cleanup owner root is missing sentinel {sentinel}")
    return errors


def _delete_cleanup_candidate(
    candidate: CleanupCandidate,
    *,
    remove_file: Callable[[Path], object],
    remove_tree: Callable[[Path], object],
) -> None:
    path = candidate.path.resolve(strict=False)
    owner_root = candidate.owner_root.resolve(strict=False)
    _reject_filesystem_root(path)
    _require_within(path, owner_root)
    if path == owner_root:
        raise ValueError(f"Cleanup candidate must not be its owner root: {path}")
    if candidate.kind == "file":
        if not path.is_file():
            raise ValueError(f"Cleanup candidate is not a file: {path}")
        remove_file(path)
        return
    if candidate.kind == "directory":
        if not path.is_dir():
            raise ValueError(f"Cleanup candidate is not a directory: {path}")
        remove_tree(path)
        return
    raise ValueError(f"Unsupported cleanup candidate kind: {candidate.kind}")


def _cleanup_audit_path(
    *,
    published: PublishedSurveyLayout,
    audit_root: Path | None,
    cleanup_id: str | None,
) -> Path:
    root = published.root.resolve(strict=False) if audit_root is None else Path(audit_root).resolve(strict=False)
    _reject_filesystem_root(root)
    if audit_root is not None:
        _require_within(root, published.root.resolve(strict=False))
    identifier = _safe_path_component(cleanup_id or str(int(time.time() * 1000)), "cleanup_id")
    return root / ARTIFACT_CLEANUP_AUDIT_DIR / f"{identifier}.json"


def _cleanup_audit_payload(
    *,
    status: str,
    dry_run: bool,
    candidates: Iterable[CleanupCandidate],
    deleted: Iterable[CleanupDeletion],
    blocked: Iterable[str],
) -> dict[str, object]:
    return {
        "status": status,
        "dry_run": dry_run,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidates": [
            {
                "path": str(candidate.path),
                "kind": candidate.kind,
                "reason": candidate.reason,
                "run_id": candidate.run_id,
                "owner_root": str(candidate.owner_root),
            }
            for candidate in candidates
        ],
        "deleted": [
            {
                "path": str(deletion.path),
                "kind": deletion.kind,
                "run_id": deletion.run_id,
                "owner_root": str(deletion.owner_root),
            }
            for deletion in deleted
        ],
        "blocked_reasons": list(blocked),
    }
def activate_publication_set_with_lock(
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
    owner_token: str | None = None,
    created_at: str | None = None,
) -> Path:
    """Activate a complete mixed file/directory publication under one lock.

    This dormant Phase 3 coordinator publishes one complete staged manifest as
    a single generation. It supports mixed file and directory artifacts, writes
    one set-level activation journal, replaces all visible artifacts before
    committing one authoritative publication manifest, and releases publication
    ownership in a finally block.
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
        return _activate_publication_set(
            workspace=workspace,
            published=published,
            manifest=staged_manifest,
            copy_file=copy_file,
            copy_tree=copy_tree or _copy_directory_tree,
            replace_path=replace_path or _replace_path,
            directory_scan=directory_scan or _scan_directory_tree,
            rename_attempts=rename_attempts,
            rename_backoff_seconds=rename_backoff_seconds,
            sleep=sleep,
        )
    finally:
        release_publication_lock(lock)


def _activate_publication_set(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    manifest: dict[str, object],
    copy_file: Callable[[Path, Path], object],
    copy_tree: Callable[[Path, Path], object],
    replace_path: Callable[[Path, Path], object],
    directory_scan: Callable[[Path], tuple[int, int]],
    rename_attempts: int,
    rename_backoff_seconds: float,
    sleep: Callable[[float], object],
) -> Path:
    if rename_attempts < 1:
        raise ValueError("rename_attempts must be at least 1")
    if rename_backoff_seconds < 0:
        raise ValueError("rename_backoff_seconds must be non-negative")

    workspace_root = workspace.root.resolve()
    published_root = published.root.resolve(strict=False)
    staged_root = (workspace.publish / "staged").resolve(strict=False)
    _reject_filesystem_root(workspace_root)
    _reject_filesystem_root(published_root)
    _require_within(staged_root, workspace_root)
    if manifest.get("status") != "staged":
        raise ValueError("Publication manifest status must be 'staged'")
    if _manifest_root(manifest, "workspace_root") != workspace_root:
        raise ValueError("Publication manifest workspace_root does not match the workspace")
    if _manifest_root(manifest, "published_root") != published_root:
        raise ValueError("Publication manifest published_root does not match the published layout")

    records = manifest.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ValueError("Publication manifest must contain at least one artifact")

    run_id = _safe_path_component(str(manifest.get("run_id", "")), "run_id")
    entries: list[dict[str, object]] = []
    seen_targets: set[Path] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Publication artifact record must be a JSON object")
        kind = record.get("kind")
        if kind == "file":
            entry = _plan_file_activation(
                record=record,
                run_id=run_id,
                staged_root=staged_root,
                published_root=published_root,
            )
        elif kind == "directory":
            entry = _plan_directory_activation(
                workspace=workspace,
                published=published,
                manifest=manifest,
                record=record,
            )
        else:
            raise ValueError(f"Unsupported publication artifact kind: {kind}")
        entry["kind"] = kind
        target = entry["target"]
        if target in seen_targets:
            raise ValueError(f"Duplicate published artifact path: {target}")
        for existing in seen_targets:
            if _path_is_within(target, existing) or _path_is_within(existing, target):
                raise ValueError("Published artifact targets must not be nested")
        seen_targets.add(target)
        entries.append(entry)

    publication_manifest = published.publication_manifest.resolve(strict=False)
    _require_within(publication_manifest, published_root)
    if publication_manifest.exists() and not publication_manifest.is_file():
        raise ValueError("Published publication manifest path must be a file")

    if _active_publication_set_matches(
        publication_manifest=publication_manifest,
        manifest=manifest,
        entries=entries,
        published_root=published_root,
    ):
        return publication_manifest

    activation_root = published_root / ".activation" / run_id
    journal_path = activation_root / "publication-set.json"
    previous_manifest = publication_manifest.with_name(
        f".{publication_manifest.name}.{run_id}.previous"
    )
    manifest_candidate = publication_manifest.with_name(
        f".{publication_manifest.name}.{run_id}.publishing"
    )
    manifest_candidate_temp = manifest_candidate.with_name(
        f".{manifest_candidate.name}.tmp"
    )
    if journal_path.exists():
        existing_journal = _read_activation_journal(journal_path)
        if existing_journal.get("status") != "committed":
            raise RuntimeError("Mixed publication-set journal requires reconciliation")
    reserved_paths = [previous_manifest, manifest_candidate, manifest_candidate_temp]
    for entry in entries:
        temporary = entry["temporary"]
        backup = entry["backup"]
        if entry["kind"] == "directory" and temporary == entry["staged"]:
            reserved_paths.append(backup)
        else:
            reserved_paths.extend([temporary, backup])
    for path in reserved_paths:
        if path.exists():
            raise FileExistsError(f"Publication set activation path already exists: {path}")

    previous_publication_run_id = _active_publication_run_id(publication_manifest)
    for entry in entries:
        entry["had_previous_at_start"] = (
            entry["target"].is_file()
            if entry["kind"] == "file"
            else entry["target"].is_dir()
        )
    journal = {
        "journal_version": 1,
        "run_id": run_id,
        "survey_id": manifest.get("survey_id"),
        "output_type": "publication_set",
        "previous_publication_run_id": previous_publication_run_id,
        "publication_manifest": str(publication_manifest),
        "previous_publication_manifest": str(previous_manifest),
        "artifacts": [_publication_set_journal_record(entry) for entry in entries],
    }
    activation_root.mkdir(parents=True, exist_ok=True)

    try:
        for entry in entries:
            if entry["kind"] == "file":
                entry["target"].parent.mkdir(parents=True, exist_ok=True)
                copy_file(entry["staged"], entry["temporary"])
                if not entry["temporary"].is_file():
                    raise OSError(
                        f"Publication copy did not create a file: {entry['temporary']}"
                    )
                if entry["temporary"].stat().st_size != entry["size_bytes"]:
                    raise OSError(
                        f"Publication copy size mismatch: {entry['temporary']}"
                    )
            else:
                _prepare_publication_set_directory(
                    entry=entry,
                    copy_tree=copy_tree,
                    directory_scan=directory_scan,
                )
        _write_activation_journal(journal_path, journal, "prepared")
        if publication_manifest.is_file():
            copy_file(publication_manifest, previous_manifest)

        active_manifest = dict(manifest)
        active_manifest["status"] = "published"
        active_manifest["staged_manifest"] = str(
            workspace.publish / "staged" / PUBLICATION_MANIFEST_NAME
        )
        active_manifest["previous_publication_manifest"] = (
            str(previous_manifest) if previous_manifest.is_file() else None
        )
        active_manifest["activation_journal"] = str(journal_path)
        active_manifest["artifacts"] = [
            _publication_set_active_record(entry) for entry in entries
        ]
        _write_json_atomic(manifest_candidate, active_manifest)
    except Exception as exc:
        _write_activation_journal(journal_path, journal, "failed", exc)
        _cleanup_publication_set_prepared_paths(
            entries=entries,
            paths=[previous_manifest, manifest_candidate, manifest_candidate_temp],
            published_root=published_root,
        )
        raise

    activated: list[dict[str, object]] = []
    try:
        for entry in entries:
            target = entry["target"]
            backup = entry["backup"]
            had_previous = bool(entry["had_previous_at_start"])
            if target.exists() and (
                (entry["kind"] == "file" and not target.is_file())
                or (entry["kind"] == "directory" and not target.is_dir())
            ):
                raise ValueError(f"Published artifact target has the wrong type: {target}")
            if had_previous:
                if entry["kind"] == "directory":
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    _replace_with_retry(
                        target,
                        backup,
                        replace_path=replace_path,
                        attempts=rename_attempts,
                        backoff_seconds=rename_backoff_seconds,
                        sleep=sleep,
                    )
                else:
                    replace_path(target, backup)
            state = {
                "entry": entry,
                "had_previous": had_previous,
                "installed": False,
            }
            activated.append(state)
            if entry["kind"] == "directory":
                _replace_with_retry(
                    entry["temporary"],
                    target,
                    replace_path=replace_path,
                    attempts=rename_attempts,
                    backoff_seconds=rename_backoff_seconds,
                    sleep=sleep,
                )
                _validate_required_paths(target, entry["required_paths"])
            else:
                replace_path(entry["temporary"], target)
            state["installed"] = True
        _write_activation_journal(journal_path, journal, "activated")
        replace_path(manifest_candidate, publication_manifest)
    except Exception as activation_error:
        try:
            _rollback_publication_set_activation(
                activated=activated,
                replace_path=replace_path,
                rename_attempts=rename_attempts,
                rename_backoff_seconds=rename_backoff_seconds,
                sleep=sleep,
                published_root=published_root,
            )
            _cleanup_publication_set_prepared_paths(
                entries=entries,
                paths=[manifest_candidate, manifest_candidate_temp],
                published_root=published_root,
            )
            _write_activation_journal(
                journal_path,
                journal,
                "rolled_back",
                activation_error,
            )
        except Exception as rollback_error:
            _write_activation_journal(journal_path, journal, "failed", rollback_error)
            raise RuntimeError(
                "Publication set activation failed and rollback was incomplete: "
                f"{activation_error}"
            ) from rollback_error
        raise

    _write_activation_journal(journal_path, journal, "committed")
    return publication_manifest


def _prepare_publication_set_directory(
    *,
    entry: dict[str, object],
    copy_tree: Callable[[Path, Path], object],
    directory_scan: Callable[[Path], tuple[int, int]],
) -> None:
    source = entry["staged"]
    temporary = entry["temporary"]
    _reject_reparse_path(entry["staged_lexical"], "Directory publication source")
    if not source.is_dir():
        raise FileNotFoundError(source)
    if source != temporary:
        if temporary.exists():
            raise FileExistsError(
                f"Directory activation temporary path already exists: {temporary}"
            )
        temporary.parent.mkdir(parents=True, exist_ok=True)
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


def _publication_set_journal_record(entry: dict[str, object]) -> dict[str, object]:
    return {
        "kind": entry["kind"],
        "published_relative_path": entry["record"]["published_relative_path"],
        "staged": str(entry["staged"]),
        "temporary": str(entry["temporary"]),
        "final": str(entry["target"]),
        "previous": str(entry["backup"]),
        "file_count": entry.get("file_count"),
        "size_bytes": entry["size_bytes"],
        "required_paths": [
            path.as_posix() for path in entry.get("required_paths", ())
        ],
        "zero_copy": entry["kind"] == "directory" and entry["staged"] == entry["temporary"],
        "had_previous": bool(entry.get("had_previous_at_start", False)),
    }


def _publication_set_active_record(entry: dict[str, object]) -> dict[str, object]:
    record = dict(entry["record"])
    record["published_path"] = str(entry["target"])
    record["previous_published_path"] = (
        str(entry["backup"]) if entry["target"].exists() else None
    )
    if entry["kind"] == "directory":
        record["zero_copy_activation"] = entry["staged"] == entry["temporary"]
    return record


def _active_publication_set_matches(
    *,
    publication_manifest: Path,
    manifest: dict[str, object],
    entries: list[dict[str, object]],
    published_root: Path,
) -> bool:
    if not publication_manifest.is_file():
        return False
    active_manifest = _read_publication_manifest(
        publication_manifest,
        "published publication manifest",
    )
    if active_manifest.get("run_id") != manifest.get("run_id"):
        return False
    if active_manifest.get("status") != "published":
        raise ValueError("Existing publication for this run is not marked published")
    if active_manifest.get("survey_id") != manifest.get("survey_id"):
        raise ValueError("Existing publication survey_id does not match the staged manifest")
    if _manifest_root(active_manifest, "published_root") != published_root:
        raise ValueError("Existing publication root does not match the published layout")
    active_records = _publication_records_by_relative(active_manifest)
    expected_paths = {
        str(entry["record"]["published_relative_path"])
        for entry in entries
    }
    if set(active_records) != expected_paths:
        raise ValueError("Existing publication artifact set does not match the staged manifest")
    for entry in entries:
        active_record = active_records[str(entry["record"]["published_relative_path"])]
        if active_record.get("kind") != entry["kind"]:
            raise ValueError("Existing publication artifact kind does not match")
        if _required_manifest_path(active_record, "published_path") != entry["target"]:
            raise ValueError("Existing publication artifact target does not match")
        if active_record.get("size_bytes") != entry["size_bytes"]:
            raise ValueError("Existing publication artifact size record does not match")
        if entry["kind"] == "file":
            if not entry["target"].is_file() or entry["target"].stat().st_size != entry["size_bytes"]:
                raise ValueError("Existing publication artifact is missing or has changed size")
        else:
            if active_record.get("file_count") != entry["file_count"]:
                raise ValueError("Existing publication directory file count record does not match")
            if not entry["target"].is_dir():
                raise ValueError("Existing publication directory is missing")
            _validate_required_paths(entry["target"], entry["required_paths"])
    return True


def _cleanup_publication_set_prepared_paths(
    *,
    entries: list[dict[str, object]],
    paths: Iterable[Path],
    published_root: Path,
) -> None:
    _unlink_owned_files(paths, published_root)
    for entry in entries:
        temporary = entry["temporary"]
        if entry["kind"] == "file":
            _unlink_owned_files([temporary], published_root)



def _rollback_publication_set_activation(
    *,
    activated: list[dict[str, object]],
    replace_path: Callable[[Path, Path], object],
    rename_attempts: int,
    rename_backoff_seconds: float,
    sleep: Callable[[float], object],
    published_root: Path,
) -> None:
    for state in reversed(activated):
        entry = state["entry"]
        target = entry["target"]
        backup = entry["backup"]
        temporary = entry["temporary"]
        _require_within(target.resolve(strict=False), published_root)
        _require_within(backup.resolve(strict=False), published_root)
        _require_within(temporary.resolve(strict=False), published_root)
        if entry["kind"] == "file":
            if state["installed"] and target.is_file():
                target.unlink()
            if state["had_previous"] and backup.is_file():
                backup.replace(target)
        else:
            if state["installed"] and target.is_dir():
                if temporary.exists():
                    raise RuntimeError(
                        "Directory rollback temporary path is already occupied"
                    )
                _replace_with_retry(
                    target,
                    temporary,
                    replace_path=replace_path,
                    attempts=rename_attempts,
                    backoff_seconds=rename_backoff_seconds,
                    sleep=sleep,
                )
            if state["had_previous"]:
                if not backup.is_dir():
                    raise RuntimeError("Directory rollback lost the previous publication")
                _replace_with_retry(
                    backup,
                    target,
                    replace_path=replace_path,
                    attempts=rename_attempts,
                    backoff_seconds=rename_backoff_seconds,
                    sleep=sleep,
                )

def reconcile_publication_set(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    replace_path: Callable[[Path, Path], object] | None = None,
    rename_attempts: int = 3,
    rename_backoff_seconds: float = 0.05,
    sleep: Callable[[float], object] = time.sleep,
) -> Path:
    """Reconcile an interrupted mixed publication-set activation.

    ``publication.json`` is authoritative. If it names the current run, the
    set journal can be finalized as committed. Otherwise, recorded artifact
    moves are rolled back to the previous committed view. The caller must hold
    exclusive publication ownership.
    """

    if rename_attempts < 1:
        raise ValueError("rename_attempts must be at least 1")
    if rename_backoff_seconds < 0:
        raise ValueError("rename_backoff_seconds must be non-negative")

    manifest = _read_staged_publication_manifest(
        workspace.publish / "staged" / PUBLICATION_MANIFEST_NAME
    )
    entries = _plan_publication_set_entries(
        workspace=workspace,
        published=published,
        manifest=manifest,
    )
    run_id = _safe_path_component(str(manifest.get("run_id", "")), "run_id")
    published_root = published.root.resolve(strict=False)
    publication_manifest = published.publication_manifest.resolve(strict=False)
    journal_path = published_root / ".activation" / run_id / "publication-set.json"
    journal = _read_activation_journal(journal_path)
    _validate_publication_set_journal(journal=journal, entries=entries, manifest=manifest)

    active_run_id = _active_publication_run_id(publication_manifest)
    status = journal["status"]
    if active_run_id == run_id:
        if status not in {"activated", "committed"}:
            raise RuntimeError(
                "Publication manifest names the interrupted run but the mixed-set "
                f"journal status is {status!r}"
            )
        if not _active_publication_set_matches(
            publication_manifest=publication_manifest,
            manifest=manifest,
            entries=entries,
            published_root=published_root,
        ):
            raise RuntimeError("Committed mixed publication set could not be validated")
        if status == "activated":
            recovered = dict(journal)
            recovered["recovered_interrupted_activation"] = True
            recovered["recovered_from_status"] = "activated"
            _write_activation_journal(journal_path, recovered, "committed")
        return journal_path

    previous_run_id = journal["previous_publication_run_id"]
    if active_run_id != previous_run_id:
        raise RuntimeError(
            "Active publication changed after mixed publication-set evidence was written"
        )
    if status == "committed":
        raise RuntimeError(
            "Mixed publication-set journal claims committed but publication.json names another run"
        )
    if status == "failed":
        raise RuntimeError("Failed mixed publication-set evidence requires explicit diagnosis")

    if status == "rolled_back":
        _validate_publication_set_rolled_back(entries)
        return journal_path
    if status not in {"prepared", "activated"}:
        raise ValueError(f"Unsupported mixed publication-set journal status: {status}")

    replace = replace_path or _replace_path
    try:
        for entry in reversed(entries):
            _rollback_publication_set_entry_for_reconciliation(
                entry=entry,
                replace_path=replace,
                rename_attempts=rename_attempts,
                rename_backoff_seconds=rename_backoff_seconds,
                sleep=sleep,
                published_root=published_root,
            )
    except Exception as exc:
        failed = dict(journal)
        failed["reconciliation_status"] = "failed"
        failed["reconciliation_error_type"] = type(exc).__name__
        failed["reconciliation_error_message"] = str(exc)
        _write_json_atomic(journal_path, failed)
        raise

    recovered = dict(journal)
    recovered["recovered_interrupted_activation"] = True
    recovered["recovered_from_status"] = status
    recovered.pop("reconciliation_status", None)
    recovered.pop("reconciliation_error_type", None)
    recovered.pop("reconciliation_error_message", None)
    _write_activation_journal(journal_path, recovered, "rolled_back")
    return journal_path


def _plan_publication_set_entries(
    *,
    workspace: RunWorkspaceLayout,
    published: PublishedSurveyLayout,
    manifest: dict[str, object],
) -> list[dict[str, object]]:
    workspace_root = workspace.root.resolve()
    published_root = published.root.resolve(strict=False)
    staged_root = (workspace.publish / "staged").resolve(strict=False)
    _reject_filesystem_root(workspace_root)
    _reject_filesystem_root(published_root)
    _require_within(staged_root, workspace_root)
    if manifest.get("status") != "staged":
        raise ValueError("Publication manifest status must be 'staged'")
    if _manifest_root(manifest, "workspace_root") != workspace_root:
        raise ValueError("Publication manifest workspace_root does not match the workspace")
    if _manifest_root(manifest, "published_root") != published_root:
        raise ValueError("Publication manifest published_root does not match the published layout")

    records = manifest.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ValueError("Publication manifest must contain at least one artifact")
    run_id = _safe_path_component(str(manifest.get("run_id", "")), "run_id")
    entries: list[dict[str, object]] = []
    seen_targets: set[Path] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Publication artifact record must be a JSON object")
        kind = record.get("kind")
        if kind == "file":
            entry = _plan_file_activation(
                record=record,
                run_id=run_id,
                staged_root=staged_root,
                published_root=published_root,
            )
        elif kind == "directory":
            entry = _plan_directory_activation(
                workspace=workspace,
                published=published,
                manifest=manifest,
                record=record,
            )
        else:
            raise ValueError(f"Unsupported publication artifact kind: {kind}")
        entry["kind"] = kind
        target = entry["target"]
        if target in seen_targets:
            raise ValueError(f"Duplicate published artifact path: {target}")
        for existing in seen_targets:
            if _path_is_within(target, existing) or _path_is_within(existing, target):
                raise ValueError("Published artifact targets must not be nested")
        seen_targets.add(target)
        entries.append(entry)
    return entries


def _validate_publication_set_journal(
    *,
    journal: dict[str, object],
    entries: list[dict[str, object]],
    manifest: dict[str, object],
) -> None:
    if journal.get("journal_version") != 1:
        raise ValueError("Unsupported mixed publication-set journal version")
    if journal.get("output_type") != "publication_set":
        raise ValueError("Mixed publication-set journal output_type must be 'publication_set'")
    if journal.get("run_id") != manifest.get("run_id"):
        raise ValueError("Mixed publication-set journal run_id does not match")
    if journal.get("survey_id") != manifest.get("survey_id"):
        raise ValueError("Mixed publication-set journal survey_id does not match")
    previous_run_id = journal.get("previous_publication_run_id")
    if previous_run_id is not None:
        if not isinstance(previous_run_id, str):
            raise ValueError(
                "Mixed publication-set journal previous_publication_run_id must be a string or null"
            )
        _safe_path_component(previous_run_id, "previous_publication_run_id")
    if journal.get("status") not in {
        "prepared",
        "activated",
        "committed",
        "rolled_back",
        "failed",
    }:
        raise ValueError(f"Unsupported mixed publication-set journal status: {journal.get('status')}")

    records = journal.get("artifacts")
    if not isinstance(records, list) or len(records) != len(entries):
        raise ValueError("Mixed publication-set journal artifact set does not match")
    for record, entry in zip(records, entries):
        if not isinstance(record, dict):
            raise ValueError("Mixed publication-set journal artifact must be an object")
        expected = _publication_set_journal_record(entry)
        for field_name in (
            "kind",
            "published_relative_path",
            "staged",
            "temporary",
            "final",
            "previous",
            "file_count",
            "size_bytes",
            "required_paths",
            "zero_copy",
        ):
            if record.get(field_name) != expected[field_name]:
                raise ValueError(
                    f"Mixed publication-set journal artifact {field_name} does not match"
                )
        had_previous = record.get("had_previous")
        if not isinstance(had_previous, bool):
            raise ValueError("Mixed publication-set journal had_previous must be a boolean")
        entry["had_previous_at_start"] = had_previous


def _validate_publication_set_rolled_back(entries: list[dict[str, object]]) -> None:
    for entry in entries:
        target = entry["target"]
        backup = entry["backup"]
        had_previous = bool(entry["had_previous_at_start"])
        if entry["kind"] == "file":
            if had_previous:
                if not target.is_file() or backup.exists():
                    raise RuntimeError("Rolled-back mixed file evidence is ambiguous")
            elif target.exists() or backup.exists():
                raise RuntimeError("Rolled-back mixed file evidence is ambiguous")
        else:
            if had_previous:
                if not target.is_dir() or backup.exists():
                    raise RuntimeError("Rolled-back mixed directory evidence is ambiguous")
            elif target.exists() or backup.exists():
                raise RuntimeError("Rolled-back mixed directory evidence is ambiguous")


def _rollback_publication_set_entry_for_reconciliation(
    *,
    entry: dict[str, object],
    replace_path: Callable[[Path, Path], object],
    rename_attempts: int,
    rename_backoff_seconds: float,
    sleep: Callable[[float], object],
    published_root: Path,
) -> None:
    target = entry["target"]
    temporary = entry["temporary"]
    backup = entry["backup"]
    had_previous = bool(entry["had_previous_at_start"])
    _require_within(target.resolve(strict=False), published_root)
    _require_within(temporary.resolve(strict=False), published_root)
    _require_within(backup.resolve(strict=False), published_root)
    if entry["kind"] == "file":
        _rollback_publication_set_file_for_reconciliation(
            target=target,
            temporary=temporary,
            backup=backup,
            had_previous=had_previous,
            expected_size=entry["size_bytes"],
            replace_path=replace_path,
        )
    else:
        _rollback_publication_set_directory_for_reconciliation(
            target=target,
            temporary=temporary,
            backup=backup,
            had_previous=had_previous,
            required_paths=entry["required_paths"],
            replace_path=replace_path,
            rename_attempts=rename_attempts,
            rename_backoff_seconds=rename_backoff_seconds,
            sleep=sleep,
        )


def _rollback_publication_set_file_for_reconciliation(
    *,
    target: Path,
    temporary: Path,
    backup: Path,
    had_previous: bool,
    expected_size: int,
    replace_path: Callable[[Path, Path], object],
) -> None:
    if temporary.exists() and not temporary.is_file():
        raise RuntimeError("Mixed file temporary path is ambiguous")
    if target.exists() and not target.is_file():
        raise RuntimeError("Mixed file target path is ambiguous")
    if backup.exists() and not backup.is_file():
        raise RuntimeError("Mixed file backup path is ambiguous")
    if temporary.is_file() and temporary.stat().st_size != expected_size:
        raise ValueError("Mixed file temporary size does not match")
    if target.is_file() and not backup.exists() and not had_previous:
        if target.stat().st_size != expected_size:
            raise ValueError("Mixed file target candidate size does not match")
        if temporary.exists():
            raise RuntimeError("Mixed file rollback candidate state is ambiguous")
        replace_path(target, temporary)
        return
    if had_previous:
        if backup.is_file():
            if target.is_file():
                if temporary.exists():
                    raise RuntimeError("Mixed file rollback candidate state is ambiguous")
                replace_path(target, temporary)
            replace_path(backup, target)
            return
        if target.is_file():
            return
        raise RuntimeError("Mixed file rollback lost the previous publication")
    if backup.exists():
        raise RuntimeError("Mixed file rollback has an unexpected backup")


def _rollback_publication_set_directory_for_reconciliation(
    *,
    target: Path,
    temporary: Path,
    backup: Path,
    had_previous: bool,
    required_paths: tuple[Path, ...],
    replace_path: Callable[[Path, Path], object],
    rename_attempts: int,
    rename_backoff_seconds: float,
    sleep: Callable[[float], object],
) -> None:
    temporary_state = _directory_path_state(temporary, "Mixed directory temporary path")
    target_state = _directory_path_state(target, "Mixed directory target path")
    backup_state = _directory_path_state(backup, "Mixed directory backup path")
    if temporary_state == "directory":
        _validate_required_paths(temporary, required_paths)
    if target_state == "directory" and backup_state == "missing" and not had_previous:
        if temporary_state != "missing":
            raise RuntimeError("Mixed directory rollback candidate state is ambiguous")
        _replace_with_retry(
            target,
            temporary,
            replace_path=replace_path,
            attempts=rename_attempts,
            backoff_seconds=rename_backoff_seconds,
            sleep=sleep,
        )
        return
    if had_previous:
        if backup_state == "directory":
            if target_state == "directory":
                if temporary_state != "missing":
                    raise RuntimeError("Mixed directory rollback candidate state is ambiguous")
                _replace_with_retry(
                    target,
                    temporary,
                    replace_path=replace_path,
                    attempts=rename_attempts,
                    backoff_seconds=rename_backoff_seconds,
                    sleep=sleep,
                )
            _replace_with_retry(
                backup,
                target,
                replace_path=replace_path,
                attempts=rename_attempts,
                backoff_seconds=rename_backoff_seconds,
                sleep=sleep,
            )
            return
        if backup_state == "missing" and target_state == "directory":
            return
        raise RuntimeError("Mixed directory rollback lost the previous publication")
    if backup_state != "missing":
        raise RuntimeError("Mixed directory rollback has an unexpected backup")


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
