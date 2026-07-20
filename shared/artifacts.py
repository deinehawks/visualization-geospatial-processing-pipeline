from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Iterable, Literal


ArtifactKind = Literal["file", "directory"]

PUBLICATION_MANIFEST_NAME = "publication.json"


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
    copy_tree: Callable[[Path, Path], object] = shutil.copytree,
) -> Path:
    """Stage a complete publish set inside the run workspace.

    This function intentionally does not modify the published survey tree. It
    prepares a validated, manifest-backed publish set that a later activation
    step can make visible under the legacy-compatible survey paths.
    """

    workspace_root = workspace.root.resolve()
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

        destination = staging_dir / relative_path
        _require_within(destination.resolve(strict=False), staging_resolved)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if artifact.kind == "file":
            copy_file(source_path, destination)
        else:
            copy_tree(source_path, destination)

        artifact_records.append(
            _artifact_record(
                artifact=artifact,
                source_path=source_path,
                staged_path=destination,
                published_path=published.root / relative_path,
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


def activate_publication(
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
    previous_manifest = publication_manifest.with_name(
        f".{publication_manifest.name}.{run_id}.previous"
    )

    reserved_paths = [manifest_candidate, previous_manifest]
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

        if publication_manifest.is_file():
            prepared_paths.append(previous_manifest)
            copy_file(publication_manifest, previous_manifest)

        active_manifest = dict(manifest)
        active_manifest["status"] = "published"
        active_manifest["staged_manifest"] = str(staged_manifest)
        active_manifest["previous_publication_manifest"] = (
            str(previous_manifest) if previous_manifest.is_file() else None
        )
        active_manifest["artifacts"] = [
            _activated_artifact_record(entry) for entry in activation_entries
        ]
        _write_json_atomic(manifest_candidate, active_manifest)
        prepared_paths.append(manifest_candidate)
    except Exception:
        _unlink_owned_files(prepared_paths, published_root)
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
            [entry["temporary"] for entry in activation_entries] + [manifest_candidate],
            published_root,
        )
        raise

    return publication_manifest


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
        files = [path for path in staged_path.rglob("*") if path.is_file()]
        record["file_count"] = len(files)
        record["size_bytes"] = sum(path.stat().st_size for path in files)
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
