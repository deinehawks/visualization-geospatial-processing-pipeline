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
    if relative_path.is_absolute():
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
