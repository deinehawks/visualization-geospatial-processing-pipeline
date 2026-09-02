from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Set, List, Tuple, Mapping
from shared.logging import quality_gate_prompt, pipeline_header, pipeline_footer, pipeline_paused, pipeline_canceled, set_stage_context, log_event
from shared.constants import WEBODM_RESTART_STAGES, WEBODM_RESTART_STAGE_NAMES
from shared.logging import get_logger
from shared.db.repo import PipelineRepo, WebODMBindingConflictError
from shared.stage_runner import StageFailedWithOutput, StageRequiresRecovery, StageRunner
from shared.pipeline_control import PipelineControl
from shared.preflight_checks import PipelinePreflight, PreflightError
from shared.paths import db_path
from shared.storage_preflight import StorageCapacityError, check_path_capacity
from shared.artifacts import (
    PublicationArtifact,
    PublishedSurveyLayout,
    RunWorkspaceLayout,
    create_run_workspace,
    describe_published_survey,
    describe_run_workspace,
    plan_published_survey_from_rgb_path,
    plan_run_workspace,
    prepare_publication,
    activate_publication_set_with_lock,
    cleanup_completed_run_workspace,
)

from shared.publication_lock import PublicationLockedError
from modules.kml_boundary_setter.kml_boundary_setter import run_kml
from modules.webodm.webodm_processor import (
    WebODMProcessor,
    WebODMTaskLookupError,
    WebODMTaskNotFound,
)
from modules.cross_run_image_filter.cross_run_image_filter import run_filter
from modules.data_segregation.data_segregation import run_data_segregation
from modules.qgis.qgis_tools import QGISTools

from pipelines.rgb_helpers import (
    RGBTaskNamingMixin,
    RGBOrthomosaicSelectionMixin,
    RGBUploadCacheMixin,
)

import time
import uuid
import logging
import os
import shutil
import tempfile
from typing import Any


class RGBPipeline(
    RGBTaskNamingMixin,
    RGBOrthomosaicSelectionMixin,
    RGBUploadCacheMixin,
):
    """
    RGB Survey Pipeline (run_id-based, resume-safe)

    Flow:
      1) data_segregation (generates survey_id + creates folder structure in SURVEYS_ROOT)
      2) cross_run_filter  (raw -> path, excluded -> cross-runs)
      3) kml_boundary      (kml -> geojson + csv)
      4) webodm            (task1 unbounded, task2 bounded)
      5) quality_gate      (placeholder)
      5) qgis      
    """

    def __init__(
        self,
        base_dir: Path,
        config: Mapping[str, Any],
        *,
        source_dir: Path,
        surveys_root: Path,
        year: int,
        run_id: Optional[str] = None,
        
        survey_id_override: Optional[str] = None,
        task_name_overrides: Optional[Dict[str, str]] = None,
        export_name_overrides: Optional[Dict[str, str]] = None,
        crossrun_enabled_override: Optional[bool] = None,
        skip_task1_webodm: bool = False,
        task1_bounded: bool = False,
        force_segregation: bool = False,
        webodm_mode: str = "task4",   # "task2" | "task4" | "both"
        use_year_subdir_override: Optional[bool] = None,
        db_file: Optional[Path] = None,
        repository: Optional[PipelineRepo] = None,
        loggers: Optional[Mapping[str, logging.Logger]] = None,
        logs_dir: Optional[Path] = None,
        checkpoint_dir: Optional[Path] = None,
        webodm_processor: Optional[Any] = None,
        workspace_root: Optional[Path] = None,
        workspace_layout: Optional[RunWorkspaceLayout] = None,
        published_layout: Optional[PublishedSurveyLayout] = None,
    ):
        if not isinstance(config, Mapping):
            raise TypeError("config must be a mapping")
        if repository is not None and db_file is not None:
            raise ValueError("repository and db_file are mutually exclusive")
        if workspace_root is not None and workspace_layout is not None:
            raise ValueError("workspace_root and workspace_layout are mutually exclusive")

        logger_names = {
            "pipeline",
            "segregation",
            "cross_run_filter",
            "kml",
            "webodm",
            "qgis",
        }
        if loggers is not None:
            missing_loggers = sorted(logger_names.difference(loggers))
            if missing_loggers:
                raise ValueError(
                    "loggers is missing required entries: "
                    + ", ".join(missing_loggers)
                )

        self.base_dir = Path(base_dir)
        self.config = config
        self.source_dir = Path(source_dir)
        self.surveys_root = Path(surveys_root)
        self.year = int(year)
        self.run_id = run_id or str(uuid.uuid4())
        self.survey_id: Optional[str] = None
        self.workspace_root = (
            Path(workspace_root)
            if workspace_root is not None
            else self.base_dir / "data" / "workspaces"
        )
        self.workspace_layout = (
            workspace_layout
            if workspace_layout is not None
            else plan_run_workspace(self.workspace_root, self.run_id)
        )
        self.published_layout = published_layout
        self.logs_dir = (
            Path(logs_dir)
            if logs_dir is not None
            else self.base_dir / "data" / "logs"
        )
        self.checkpoint_dir = (
            Path(checkpoint_dir)
            if checkpoint_dir is not None
            else self.logs_dir
        )
        self.webodm_processor = webodm_processor

        self.survey_id_override = survey_id_override
        self.task_name_overrides = task_name_overrides or {}
        self.export_name_overrides = export_name_overrides or {}
        self.crossrun_enabled_override = crossrun_enabled_override
        self.use_year_subdir_override = use_year_subdir_override

        self.skip_task1_webodm = skip_task1_webodm
        self.task1_bounded     = task1_bounded
        self.force_segregation = force_segregation
        self.webodm_mode       = webodm_mode.strip().lower()

        # Derive task skip flags from webodm_mode
        self.skip_task2_webodm = self.webodm_mode == "task4"
        self.skip_task4_webodm = self.webodm_mode == "task2"
        self.control = PipelineControl(
            self.base_dir,
            self.run_id,
        )

        if loggers is None:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            self.loggers: Mapping[str, logging.Logger] = {
                "pipeline": get_logger(
                    "rgb.pipeline",
                    self.logs_dir / "pipeline.log",
                    run_id=self.run_id,
                ),
                "segregation": get_logger(
                    "rgb.data_segregation",
                    self.logs_dir / "data_segregation.log",
                    run_id=self.run_id,
                ),
                "cross_run_filter": get_logger(
                    "rgb.cross_run_filter",
                    self.logs_dir / "cross_run_filter.log",
                    run_id=self.run_id,
                ),
                "kml": get_logger(
                    "rgb.kml",
                    self.logs_dir / "kml.log",
                    run_id=self.run_id,
                ),
                "webodm": get_logger(
                    "rgb.webodm",
                    self.logs_dir / "webodm.log",
                    run_id=self.run_id,
                ),
                "qgis": get_logger(
                    "rgb.qgis",
                    self.logs_dir / "qgis.log",
                    run_id=self.run_id,
                ),
            }
        else:
            self.loggers = loggers

        self.state: Dict[str, Any] = {
            "run_id": self.run_id,
        }

        if repository is None:
            repository = PipelineRepo(
                Path(db_file) if db_file is not None else db_path(self.base_dir)
            )
        self.repo = repository
        self.repo.create_run(
            self.run_id,
            source_dir=str(self.source_dir),
            surveys_root=str(self.surveys_root),
            year=self.year,
            workspace_root=str(self.workspace_root),
        )

        self.runner = StageRunner(
            repo=self.repo,
            run_id=self.run_id,
            logger=self.loggers["pipeline"],
            extra_loggers=[
                self.loggers["segregation"],
                self.loggers["cross_run_filter"],
                self.loggers["kml"],
                self.loggers["webodm"],
                self.loggers["qgis"],
            ],
        )

        self.preflight = PipelinePreflight(
            config=self.config,
            source_dir=self.source_dir,
            surveys_root=self.surveys_root,
            year=self.year,
            logger=self.loggers["pipeline"],
        )

        self.rgb_path: Optional[Path] = None
        self._webodm_projects_created_this_process: set[int] = set()
        self._resume_had_webodm_history = False

    # Helpers
    def _set_survey_artifact_context(self, survey_id: str, rgb_path: Path) -> None:
        self.survey_id = survey_id
        self.rgb_path = Path(rgb_path)
        if self.published_layout is None:
            self.published_layout = plan_published_survey_from_rgb_path(self.rgb_path)

    def _create_webodm_processor(self, logger: logging.Logger) -> Any:
        if self.webodm_processor is not None:
            return self.webodm_processor

        webodm_cfg = self.config["webodm"]
        return WebODMProcessor(
            url=webodm_cfg["url"],
            username=webodm_cfg["username"],
            password=webodm_cfg["password"],
            logger=logger,
        )

    def _require_survey_id(self) -> str:
        if not self.survey_id:
            raise RuntimeError(
                "survey_id is not set yet. Run data_segregation first.")
        return self.survey_id

    def _require_rgb_path(self) -> Path:
        if not self.rgb_path:
            raise RuntimeError(
                "rgb_path is not set yet. Run data_segregation first.")
        return self.rgb_path

    def _hydrate_from_state(self) -> None:
        state = self.state

        seg = state.get("data_segregation") or {}
        if seg.get("survey_id") and seg.get("survey_path"):
            self._set_survey_artifact_context(
                seg["survey_id"],
                Path(seg["survey_path"]),
            )
        else:
            if seg.get("survey_id"):
                self.survey_id = seg["survey_id"]
            if seg.get("survey_path"):
                self.rgb_path = Path(seg["survey_path"])
                if self.published_layout is None:
                    self.published_layout = plan_published_survey_from_rgb_path(
                        self.rgb_path
                    )

        flt = state.get("cross_run_filter") or {}
        if flt.get("crossrun_flag"):
            self.state["crossrun_flag"] = flt["crossrun_flag"]

        kml = state.get("kml_boundary") or {}
        if "boundary_available" in kml:
            self.state["boundary_available"] = kml["boundary_available"]
        if kml.get("boundary_geojson_path"):
            self.state["boundary_geojson_path"] = kml["boundary_geojson_path"]

        if not state.get("webodm"):
            ckpt = self._load_webodm_checkpoint()
            if ckpt:
                self.loggers["pipeline"].info(
                    f"Loaded webodm checkpoint: project_id={ckpt.get('project_id')} "
                    f"task1={(ckpt.get('task1') or {}).get('id')} "
                    f"task2={(ckpt.get('task2') or {}).get('id')}"
                )
                self.state["webodm"] = ckpt

        if not state.get("selected_orthomosaic"):
            qg = state.get("quality_gate") or {}
            sel = qg.get("selected_orthomosaic")
            if sel and isinstance(sel, dict):
                state["selected_orthomosaic"] = sel
                self.loggers["pipeline"].info(
                    f"Restored selected orthomosaic from quality_gate state: "
                    f"task={sel.get('task_key')} | file={sel.get('source_filename')}"
                )
            if not state.get("selected_webodm_task"):
                task_key = qg.get("selected_webodm_task")
                if task_key:
                    state["selected_webodm_task"] = task_key

    def _stage_will_run(self, stage_name: str, *, force: bool) -> bool:
        if force:
            return True

        try:
            latest = self.repo.get_latest_stage(self.run_id, stage_name)
            if latest and latest.get("status") == "completed":
                return False
        except Exception:
            self.loggers["pipeline"].exception(
                f"Failed to check latest stage status for preflight: {stage_name}"
            )

        return True

    def _published_relative_path_for_plan(self, published_path: Path) -> Path:
        if self.published_layout is None:
            raise RuntimeError("published_layout is not set yet")
        published_root = self.published_layout.root.resolve(strict=False)
        target = Path(published_path).resolve(strict=False)
        try:
            return target.relative_to(published_root)
        except ValueError as exc:
            raise ValueError(
                f"Publication target escapes published survey root: {published_path}"
            ) from exc

    def _collect_publication_plan_pairs(
        self,
        *,
        stage_name: str,
        workspace_node: Any,
        published_node: Any,
        logical_prefix: str,
        artifacts: list[PublicationArtifact],
        blocked_reasons: list[str],
        skipped_artifacts: list[str],
        seen_targets: set[str],
    ) -> None:
        if workspace_node is None or published_node is None:
            return

        if isinstance(workspace_node, Mapping) and isinstance(published_node, Mapping):
            for key in sorted(set(workspace_node).intersection(published_node)):
                self._collect_publication_plan_pairs(
                    stage_name=stage_name,
                    workspace_node=workspace_node.get(key),
                    published_node=published_node.get(key),
                    logical_prefix=f"{logical_prefix}.{key}",
                    artifacts=artifacts,
                    blocked_reasons=blocked_reasons,
                    skipped_artifacts=skipped_artifacts,
                    seen_targets=seen_targets,
                )
            return

        if isinstance(workspace_node, list) and isinstance(published_node, list):
            for index, (workspace_item, published_item) in enumerate(
                zip(workspace_node, published_node)
            ):
                self._collect_publication_plan_pairs(
                    stage_name=stage_name,
                    workspace_node=workspace_item,
                    published_node=published_item,
                    logical_prefix=logical_prefix,
                    artifacts=artifacts,
                    blocked_reasons=blocked_reasons,
                    skipped_artifacts=skipped_artifacts,
                    seen_targets=seen_targets,
                )
            if len(workspace_node) != len(published_node):
                blocked_reasons.append(
                    f"{stage_name}:{logical_prefix} workspace/published list length mismatch"
                )
            return

        if not isinstance(workspace_node, str) or not isinstance(published_node, str):
            return
        if not workspace_node or not published_node:
            return

        logical_name = f"{stage_name}.{logical_prefix}"
        source = Path(workspace_node)
        if not self._publication_artifact_is_allowed(logical_name, source):
            skipped_artifacts.append(logical_name)
            return

        target = Path(published_node)
        try:
            source = self._require_workspace_owned_path(source)
        except Exception as exc:
            blocked_reasons.append(
                f"{stage_name}:{logical_prefix} source is not run-workspace owned: {source} ({exc})"
            )
            return

        if source.is_file():
            kind = "file"
        elif source.is_dir():
            kind = "directory"
        else:
            blocked_reasons.append(
                f"{stage_name}:{logical_prefix} source does not exist: {source}"
            )
            return

        try:
            relative_target = self._published_relative_path_for_plan(target)
        except Exception as exc:
            blocked_reasons.append(f"{stage_name}:{logical_prefix} invalid target: {exc}")
            return

        target_key = str(relative_target).replace("\\", "/").lower()
        if target_key in seen_targets:
            blocked_reasons.append(
                f"{stage_name}:{logical_prefix} duplicates publication target: {relative_target}"
            )
            return
        seen_targets.add(target_key)

        artifacts.append(
            PublicationArtifact(
                logical_name=logical_name,
                source_path=source,
                published_relative_path=relative_target,
                kind=kind,
            )
        )

    def _publication_artifact_is_allowed(self, logical_name: str, source_path: Path) -> bool:
        allowed_exact = {
            "kml_boundary.published.processed_files.geojson",
            "kml_boundary.published.processed_files.csv",
            "webodm.published.webodm_odm.task2_all_assets_zip",
            "qgis.published.qgis_clipped_ortho",
            "qgis.published.tiles_dir",
        }
        if logical_name in allowed_exact:
            return True
        if logical_name.startswith("webodm.published.webodm_ortho."):
            return True
        if logical_name.startswith("webodm.published.webodm_3d."):
            return source_path.suffix.lower() in {".laz", ".ply", ".pcd"}
        return False

    def _build_publication_artifact_plan(
        self,
    ) -> tuple[list[PublicationArtifact], list[str], list[str]]:
        if not self.survey_id or self.rgb_path is None or self.published_layout is None:
            self._hydrate_from_state()
        if not self.survey_id:
            raise RuntimeError("survey_id is not set yet. Run data_segregation first.")
        if self.published_layout is None:
            raise RuntimeError("published_layout is not set yet. Run data_segregation first.")

        artifacts: list[PublicationArtifact] = []
        blocked_reasons: list[str] = []
        skipped_artifacts: list[str] = []
        seen_targets: set[str] = set()

        for stage_name in (
            "cross_run_filter",
            "kml_boundary",
            "webodm",
            "qgis",
        ):
            stage_state = self.state.get(stage_name) or {}
            if not isinstance(stage_state, Mapping):
                continue
            self._collect_publication_plan_pairs(
                stage_name=stage_name,
                workspace_node=stage_state.get("workspace"),
                published_node=stage_state.get("published"),
                logical_prefix="published",
                artifacts=artifacts,
                blocked_reasons=blocked_reasons,
                skipped_artifacts=skipped_artifacts,
                seen_targets=seen_targets,
            )

        return artifacts, blocked_reasons, sorted(set(skipped_artifacts))

    def _publication_artifact_payload(
        self,
        artifacts: list[PublicationArtifact],
    ) -> list[dict[str, str]]:
        if self.published_layout is None:
            raise RuntimeError("published_layout is not set yet. Run data_segregation first.")
        return [
            {
                "logical_name": artifact.logical_name,
                "kind": artifact.kind,
                "source_path": str(artifact.source_path),
                "published_relative_path": str(artifact.published_relative_path),
                "published_path": str(
                    self.published_layout.root / artifact.published_relative_path
                ),
            }
            for artifact in artifacts
        ]

    @staticmethod
    def _collect_completed_output_pairs(
        workspace_node: object,
        published_node: object,
    ) -> list[tuple[Path, Path]]:
        pairs: list[tuple[Path, Path]] = []
        if isinstance(workspace_node, Mapping) and isinstance(published_node, Mapping):
            for key in sorted(set(workspace_node).intersection(published_node)):
                pairs.extend(
                    RGBPipeline._collect_completed_output_pairs(
                        workspace_node[key],
                        published_node[key],
                    )
                )
            return pairs
        if isinstance(workspace_node, (str, Path)) and isinstance(
            published_node, (str, Path)
        ):
            pairs.append((Path(workspace_node), Path(published_node)))
        return pairs

    def _completed_run_output_pairs(self) -> list[tuple[Path, Path]]:
        pairs: list[tuple[Path, Path]] = []
        for stage_name in ("cross_run_filter", "kml_boundary", "webodm", "qgis"):
            stage_state = self.state.get(stage_name)
            if not isinstance(stage_state, Mapping):
                continue
            pairs.extend(
                self._collect_completed_output_pairs(
                    stage_state.get("workspace"),
                    stage_state.get("published"),
                )
            )
        return pairs

    def _completed_run_stage_summaries(self) -> dict[str, object]:
        summaries: dict[str, object] = {}
        excluded_keys = {
            "image_classifications",
            "workspace",
            "published",
        }
        for stage_name in (
            "data_segregation",
            "cross_run_filter",
            "kml_boundary",
            "webodm",
            "webodm_task4",
            "webodm_task2",
            "quality_gate",
            "qgis",
            "activate_publication",
        ):
            stage_state = self.state.get(stage_name)
            if not isinstance(stage_state, Mapping):
                continue
            summary = {
                key: value
                for key, value in stage_state.items()
                if key not in excluded_keys
                and isinstance(value, (str, int, float, bool, type(None)))
            }
            summaries[stage_name] = summary
        return summaries

    def _workspace_cleanup_result(
        self,
        *,
        final_status: str,
        selected_stages: Optional[Set[str]],
        keep_workspace: bool,
    ) -> Dict[str, Any]:
        logger = self.loggers["pipeline"]
        reason = None
        if final_status != "completed":
            reason = f"run_status_{final_status}"
        elif selected_stages is not None:
            reason = "selected_stage_execution"
        elif keep_workspace:
            reason = "keep_workspace_requested"

        if reason is not None:
            result: Dict[str, Any] = {
                "status": "skipped",
                "reason": reason,
                "workspace": str(self.workspace_layout.root),
            }
            log_event(
                logger,
                "workspace_cleanup_skipped",
                status="skipped",
                reason=reason,
            )
            return result

        if not self.survey_id or self.published_layout is None:
            self._hydrate_from_state()
        if not self.survey_id or self.published_layout is None:
            error = "survey and published layout are required for workspace cleanup"
            log_event(
                logger,
                "workspace_cleanup_failed",
                level=logging.WARNING,
                status="failed",
                error_type="RuntimeError",
                error_message=error,
            )
            return {
                "status": "failed",
                "workspace": str(self.workspace_layout.root),
                "error_type": "RuntimeError",
                "error": error,
            }

        cross_run_state = self.state.get("cross_run_filter")
        image_classifications = []
        if isinstance(cross_run_state, Mapping):
            value = cross_run_state.get("image_classifications")
            if isinstance(value, list):
                image_classifications = [
                    item for item in value if isinstance(item, Mapping)
                ]

        try:
            result = cleanup_completed_run_workspace(
                run_id=self.run_id,
                survey_id=self.survey_id,
                workspace=self.workspace_layout,
                workspace_root=self.workspace_root,
                published=self.published_layout,
                output_pairs=self._completed_run_output_pairs(),
                stage_summaries=self._completed_run_stage_summaries(),
                image_classifications=image_classifications,
            )
        except Exception as exc:
            result = {
                "status": "failed",
                "workspace": str(self.workspace_layout.root),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

        event_name = (
            "workspace_cleanup_completed"
            if result.get("status") == "completed"
            else "workspace_cleanup_failed"
        )
        log_event(
            logger,
            event_name,
            level=(
                logging.INFO
                if result.get("status") == "completed"
                else logging.WARNING
            ),
            status=result.get("status"),
            file_count=result.get("file_count"),
            total_bytes=result.get("total_bytes"),
            error_type=result.get("error_type"),
            error_message=str(result.get("error") or "")[:240],
        )
        return dict(result)

    def plan_publication_dry_run(self) -> Dict[str, Any]:
        """Build the current RGB publication intent without activating it.

        This is an opt-in planning bridge for Phase 3. It reads the current
        in-memory stage state, finds run-workspace artifacts that are still
        mirrored to legacy published paths, validates ownership/containment, and
        returns the mixed file/directory publication set that a later activation
        slice could stage. It intentionally does not copy, rename, lock, write a
        publication manifest, or mutate pipeline state.
        """

        artifacts, blocked_reasons, skipped_artifacts = self._build_publication_artifact_plan()
        artifact_payload = [
            dict(artifact_record)
            for artifact_record in self._publication_artifact_payload(artifacts)
        ]

        plan = {
            "run_id": self.run_id,
            "survey_id": self.survey_id,
            "published_root": str(self.published_layout.root),
            "publication_manifest": str(self.published_layout.publication_manifest),
            "activation_enabled": False,
            "artifact_count": len(artifact_payload),
            "artifacts": artifact_payload,
            "blocked_reasons": blocked_reasons,
            "skipped_artifacts": skipped_artifacts,
            "status": "blocked" if blocked_reasons else "planned",
        }
        log_event(
            self.loggers["pipeline"],
            "publication_plan_built",
            status=plan["status"],
            artifact_count=str(plan["artifact_count"]),
            activation_enabled="false",
        )
        return plan

    def prepare_publication_staging(self) -> Dict[str, Any]:
        """Prepare a staged RGB publication set without activating it.

        This opt-in Phase 3 bridge reuses the dry-run publication plan and the
        existing artifact staging helper. It writes only under the run workspace
        publish/staged directory and intentionally does not acquire the
        publication lock, mutate visible published artifacts, or write the
        published survey publication.json.
        """

        artifacts, blocked_reasons, skipped_artifacts = self._build_publication_artifact_plan()
        if not artifacts and not blocked_reasons:
            blocked_reasons.append("publication plan contains no artifacts")
        artifact_payload = [
            dict(artifact_record)
            for artifact_record in self._publication_artifact_payload(artifacts)
        ]
        if self.published_layout is None or self.workspace_layout is None:
            raise RuntimeError("publication layouts are not set yet. Run data_segregation first.")

        result: Dict[str, Any] = {
            "run_id": self.run_id,
            "survey_id": self.survey_id,
            "published_root": str(self.published_layout.root),
            "publication_manifest": str(self.published_layout.publication_manifest),
            "activation_enabled": False,
            "artifact_count": len(artifact_payload),
            "artifacts": artifact_payload,
            "blocked_reasons": blocked_reasons,
            "skipped_artifacts": skipped_artifacts,
        }
        if blocked_reasons:
            result["status"] = "blocked"
            log_event(
                self.loggers["pipeline"],
                "publication_staging_blocked",
                status=result["status"],
                artifact_count=str(result["artifact_count"]),
                activation_enabled="false",
            )
            return result

        staged_manifest = prepare_publication(
            run_id=self.run_id,
            survey_id=self.survey_id,
            workspace=self.workspace_layout,
            published=self.published_layout,
            artifacts=artifacts,
            stage_directories_for_activation=True,
        )
        result["status"] = "staged"
        result["staged_manifest"] = str(staged_manifest)
        log_event(
            self.loggers["pipeline"],
            "publication_staging_prepared",
            status=result["status"],
            artifact_count=str(result["artifact_count"]),
            staged_manifest=str(staged_manifest),
            activation_enabled="false",
        )
        return result

    def expected_publication_confirmation(self) -> str:
        if not self.survey_id:
            raise RuntimeError("survey_id is not set yet. Run data_segregation first.")
        return f"PUBLISH {self.survey_id} {self.run_id}"

    def activate_publication_explicit(self, *, confirmation: str) -> Dict[str, Any]:
        """Activate a staged RGB publication only after explicit confirmation.

        This is the deliberately guarded Phase 3 live-publication bridge. It
        requires the caller to provide the exact confirmation phrase for the
        current survey and run, then delegates to the existing lock-owned mixed
        publication-set activation helper. It is intentionally not called from
        RGBPipeline.run().
        """

        if self.published_layout is None or self.workspace_layout is None:
            raise RuntimeError("publication layouts are not set yet. Run data_segregation first.")
        expected_confirmation = self.expected_publication_confirmation()
        if confirmation != expected_confirmation:
            log_event(
                self.loggers["pipeline"],
                "publication_activation_blocked",
                status="blocked",
                reason="confirmation_mismatch",
                activation_enabled="true",
            )
            return {
                "run_id": self.run_id,
                "survey_id": self.survey_id,
                "published_root": str(self.published_layout.root),
                "publication_manifest": str(self.published_layout.publication_manifest),
                "activation_enabled": True,
                "status": "blocked",
                "blocked_reasons": [
                    f"confirmation must exactly match: {expected_confirmation}"
                ],
            }

        staged_manifest = self.workspace_layout.publish / "staged" / "publication.json"
        if not staged_manifest.is_file():
            log_event(
                self.loggers["pipeline"],
                "publication_activation_blocked",
                status="blocked",
                reason="missing_staged_manifest",
                staged_manifest=str(staged_manifest),
                activation_enabled="true",
            )
            return {
                "run_id": self.run_id,
                "survey_id": self.survey_id,
                "published_root": str(self.published_layout.root),
                "publication_manifest": str(self.published_layout.publication_manifest),
                "activation_enabled": True,
                "status": "blocked",
                "blocked_reasons": [
                    f"staged publication manifest is missing: {staged_manifest}"
                ],
            }

        try:
            publication_manifest = activate_publication_set_with_lock(
                workspace=self.workspace_layout,
                published=self.published_layout,
            )
        except PublicationLockedError:
            raise
        except Exception as exc:
            raise StageRequiresRecovery(
                "publication activation requires recovery: " + str(exc),
                output={
                    "run_id": self.run_id,
                    "survey_id": self.survey_id,
                    "published_root": str(self.published_layout.root),
                    "publication_manifest": str(self.published_layout.publication_manifest),
                    "activation_enabled": True,
                    "status": "requires_recovery",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            ) from exc
        result: Dict[str, Any] = {
            "run_id": self.run_id,
            "survey_id": self.survey_id,
            "published_root": str(self.published_layout.root),
            "publication_manifest": str(publication_manifest),
            "activation_enabled": True,
            "status": "activated",
        }
        log_event(
            self.loggers["pipeline"],
            "publication_activation_completed",
            status=result["status"],
            publication_manifest=str(publication_manifest),
            activation_enabled="true",
        )
        return result

    def _activate_publication_stage_body(
        self,
        *,
        confirmation: str,
        prepare_staging: bool = False,
    ) -> Dict[str, Any]:
        if prepare_staging:
            staging_result = self.prepare_publication_staging()
            if staging_result.get("status") == "blocked":
                blocked_reasons = staging_result.get("blocked_reasons") or [
                    "publication staging blocked"
                ]
                raise RuntimeError("; ".join(str(reason) for reason in blocked_reasons))

        result = self.activate_publication_explicit(
            confirmation=confirmation,
        )
        if result.get("status") == "blocked":
            blocked_reasons = result.get("blocked_reasons") or [
                "publication activation blocked"
            ]
            raise RuntimeError("; ".join(str(reason) for reason in blocked_reasons))
        return result

    def activate_publication_stage(
        self,
        *,
        confirmation: str,
        prepare_staging: bool = False,
    ) -> Dict[str, Any]:
        """Run explicit publication activation as one tracked pipeline stage.

        This method is the first formal stage boundary for the approved
        `Activate Publication` contract. ``RGBPipeline.run()`` now uses the
        same body after QGIS, while this wrapper remains useful for tests and
        operator tooling.
        """

        return self.runner.run(
            "activate_publication",
            lambda: self._activate_publication_stage_body(
                confirmation=confirmation,
                prepare_staging=prepare_staging,
            ),
            output_key="activate_publication",
            state=self.state,
            retry_attempts=1,
            retry_delay_seconds=0,
        )

    def _preflight_stage(self, stage_name: str) -> None:
        result = self.preflight.check_stage(
            stage_name,
            state=self.state,
            survey_id=self.survey_id,
            rgb_path=self.rgb_path,
            skip_task1_webodm=self.skip_task1_webodm,
            skip_task2_webodm=self.skip_task2_webodm,
            skip_task4_webodm=self.skip_task4_webodm,
        )

        self.loggers["pipeline"].info(
            f"PREFLIGHT OK | {stage_name} | {result}"
        )

    # Checkpoint helpers
    def _webodm_checkpoint_path(self) -> Path:
        return self.checkpoint_dir / f"webodm_checkpoint_{self.run_id}.json"

    def _save_webodm_checkpoint(self, data: dict) -> None:
        import json
        path = self._webodm_checkpoint_path()
        temp_path = path.with_name(
            f'.{path.name}.{uuid.uuid4().hex}.tmp'
        )
        payload = dict(data)
        payload.setdefault('task4', None)
        payload.setdefault('downloads', {})
        payload['downloads'].setdefault('task4', {})
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, indent=2),
                encoding='utf-8',
            )
            os.replace(temp_path, path)
        except Exception as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.loggers['webodm'].warning(
                f'Could not atomically save webodm checkpoint: {exc}'
            )

    def _load_webodm_checkpoint(self) -> dict:
        import json
        path = self._webodm_checkpoint_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            self.loggers["webodm"].warning(
                f"Could not load webodm checkpoint: {e}")
            return {}

    def _clear_webodm_checkpoint(self) -> None:
        self.loggers['webodm'].debug(
            'Retaining WebODM checkpoint as a recovery mirror.'
        )

    def _persist_webodm_binding(
        self,
        *,
        task_key: str,
        project_id: int,
        task_id: Optional[str] = None,
        task_name: Optional[str] = None,
        remote_status: Optional[str] = None,
        raw_status: Optional[Any] = None,
        local_status: str = 'bound',
        success: Optional[bool] = None,
        runtime_seconds: Optional[float] = None,
        binding_source: str = 'pipeline',
    ) -> dict:
        operation_attempt = self.repo.get_latest_stage(
            self.run_id,
            f'webodm_{task_key}',
        )
        stage_attempt_id = (
            int(operation_attempt['id'])
            if operation_attempt
            else None
        )
        try:
            binding = self.repo.record_webodm_binding(
                run_id=self.run_id,
                operation_key=task_key,
                project_id=int(project_id),
                task_id=task_id,
                task_name=task_name,
                survey_id=self.survey_id,
                remote_status=remote_status,
                raw_status=raw_status,
                local_status=local_status,
                success=success,
                runtime_seconds=runtime_seconds,
                binding_source=binding_source,
                stage_attempt_id=stage_attempt_id,
            )
        except WebODMBindingConflictError as exc:
            raise StageRequiresRecovery(
                str(exc),
                output={
                    'operation_key': task_key,
                    'status': 'requires_recovery',
                    'project_id': project_id,
                    'task_id': task_id,
                },
            ) from exc

        web = self.state.setdefault('webodm', {})
        existing_project = web.get('project_id')
        if existing_project and int(existing_project) != int(project_id):
            raise StageRequiresRecovery(
                f'Conflicting WebODM project identities for run {self.run_id}: '
                f'state={existing_project}, binding={project_id}',
                output={
                    'operation_key': task_key,
                    'status': 'requires_recovery',
                    'project_id': project_id,
                    'task_id': task_id,
                },
            )
        web['project_id'] = int(project_id)
        if task_name and not web.get('project_name'):
            web['project_name'] = self.survey_id
        if task_id:
            task_state = web.get(task_key)
            if not isinstance(task_state, dict):
                task_state = {}
                web[task_key] = task_state
            task_state['id'] = str(task_id)
            if task_name:
                task_state['name'] = task_name
            if success is not None:
                task_state['success'] = bool(success)
            if runtime_seconds is not None:
                task_state['runtime_seconds'] = float(runtime_seconds)
            if remote_status:
                task_state['remote_status'] = remote_status
            if raw_status is not None:
                task_state['raw_status'] = raw_status
        web.setdefault('downloads', {}).setdefault(task_key, {})
        self._save_webodm_checkpoint(web)
        return binding

    def _restore_webodm_identity(self) -> None:
        legacy_web = self.state.get('webodm')
        if not isinstance(legacy_web, Mapping) or not legacy_web:
            completed = self.repo.get_latest_stage_output(self.run_id, 'webodm')
            if isinstance(completed, Mapping):
                legacy_web = dict(completed)
                self.state['webodm'] = legacy_web
            else:
                checkpoint = self._load_webodm_checkpoint()
                if checkpoint:
                    legacy_web = checkpoint
                    self.state['webodm'] = legacy_web

        if not isinstance(legacy_web, Mapping):
            legacy_web = {}

        legacy_project = legacy_web.get('project_id')
        resolved_project = None
        for task_key in ('task1', 'task2', 'task4'):
            binding = self.repo.get_webodm_binding(self.run_id, task_key)
            legacy_task = legacy_web.get(task_key)
            if not isinstance(legacy_task, Mapping):
                legacy_task = {}

            if binding is None and legacy_project:
                binding = self._persist_webodm_binding(
                    task_key=task_key,
                    project_id=int(legacy_project),
                    task_id=(
                        str(legacy_task.get('id'))
                        if legacy_task.get('id')
                        else None
                    ),
                    task_name=legacy_task.get('name'),
                    remote_status=legacy_task.get('remote_status'),
                    raw_status=legacy_task.get('raw_status'),
                    local_status='legacy_import',
                    success=legacy_task.get('success'),
                    runtime_seconds=legacy_task.get('runtime_seconds'),
                    binding_source='legacy_state',
                )

            if binding is None:
                continue

            project_id = int(binding['project_id'])
            if resolved_project is not None and resolved_project != project_id:
                raise StageRequiresRecovery(
                    f'Conflicting WebODM projects are bound to run {self.run_id}: '
                    f'{resolved_project} and {project_id}',
                    output={
                        'status': 'requires_recovery',
                        'operation_key': task_key,
                        'project_id': project_id,
                    },
                )
            resolved_project = project_id
            if legacy_project and int(legacy_project) != project_id:
                raise StageRequiresRecovery(
                    f'Checkpoint/stage project {legacy_project} conflicts with '
                    f'canonical WebODM project {project_id}',
                    output={
                        'status': 'requires_recovery',
                        'operation_key': task_key,
                        'project_id': project_id,
                        'task_id': binding.get('task_id'),
                    },
                )
            legacy_task_id = legacy_task.get('id')
            bound_task_id = binding.get('task_id')
            legacy_task_name = legacy_task.get('name')
            bound_task_name = binding.get('task_name')
            if (
                bound_task_id
                and legacy_task_id
                and str(bound_task_id) != str(legacy_task_id)
            ):
                raise StageRequiresRecovery(
                    f'Legacy {task_key} task {legacy_task_id} conflicts with '
                    f'canonical task {bound_task_id}',
                    output={
                        'status': 'requires_recovery',
                        'operation_key': task_key,
                        'project_id': project_id,
                        'task_id': bound_task_id,
                    },
                )
            if (
                bound_task_name
                and legacy_task_name
                and str(bound_task_name) != str(legacy_task_name)
            ):
                raise StageRequiresRecovery(
                    f'Legacy {task_key} name {legacy_task_name!r} conflicts '
                    f'with canonical name {bound_task_name!r}',
                    output={
                        'status': 'requires_recovery',
                        'operation_key': task_key,
                        'project_id': project_id,
                        'task_id': bound_task_id,
                    },
                )

            web = self.state.setdefault('webodm', {})
            web['project_id'] = project_id
            if binding.get('task_id'):
                task_state = web.setdefault(task_key, {})
                task_state['id'] = str(binding['task_id'])
                if binding.get('task_name'):
                    task_state['name'] = binding['task_name']
                if binding.get('remote_status'):
                    task_state['remote_status'] = binding['remote_status']
            web.setdefault('downloads', {}).setdefault(task_key, {})

        if self.state.get('webodm'):
            self._save_webodm_checkpoint(self.state['webodm'])

    @staticmethod
    def _iter_jpeg_files(folder: Path) -> List[Path]:
        exts = {".jpg", ".jpeg"}
        files: List[Path] = []
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in exts:
                files.append(p)
        return sorted(files)

    @staticmethod
    def _fmt_bytes(num: int) -> str:
        size: float = float(num)
        step: float = 1024.0

        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < step:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
            size /= step

        return f"{size:.1f} PB"
    
    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default
            return int(float(value))
        return default

    def _storage_thresholds(self, *, role: str) -> tuple[int, int]:
        storage = self.config.get("storage") or {}
        if role in {"published_file_mirror", "published_directory_mirror"}:
            return (
                int(storage.get("published_min_free_gb", 10)),
                int(storage.get("published_min_free_percent", 0)),
            )
        return (
            int(storage.get("min_free_gb", 10)),
            int(storage.get("min_free_percent", 5)),
        )

    def _check_write_capacity(
        self,
        *,
        role: str,
        path: Path,
        required_bytes: int,
    ) -> dict:
        min_free_gb, min_free_percent = self._storage_thresholds(role=role)
        return check_path_capacity(
            role=role,
            path=path,
            required_bytes=required_bytes,
            min_free_gb=min_free_gb,
            min_free_percent=min_free_percent,
        )

    @staticmethod
    def _directory_size(path: Path) -> int:
        return sum(
            item.stat().st_size
            for item in Path(path).rglob("*")
            if item.is_file()
        )

    def _stage_upload_cache(
        self,
        *,
        src_dir: Path,
        cache_root: Path,
        logger: logging.Logger,
        progress_every: int = 25,
        require_free_multiplier: float = 1.2,
    ) -> Tuple[Path, int]:
        """
        Copy JPEG images from src_dir to a local cache dir (fast local reads for upload).
        Returns: (cache_dir, image_count)
        """
        src_dir = Path(src_dir)
        if not src_dir.exists():
            raise FileNotFoundError(
                f"Upload cache source dir not found: {src_dir}")

        images = self._iter_jpeg_files(src_dir)
        if not images:
            raise FileNotFoundError(f"No JPG/JPEG images found in: {src_dir}")

        total_bytes = 0
        for p in images:
            total_bytes += p.stat().st_size

        cache_dir = Path(cache_root)
        needed = int(total_bytes * require_free_multiplier)
        capacity = self._check_write_capacity(
            role="upload_cache",
            path=cache_dir,
            required_bytes=needed,
        )
        free_bytes = int(capacity["volumes"][0]["free_bytes"])
        cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"Upload cache: preparing {len(images)} images "
            f"({self._fmt_bytes(total_bytes)}) -> {cache_dir} | free={self._fmt_bytes(free_bytes)}"
        )

        copied = 0
        t0 = time.perf_counter()

        for src in images:
            dst = cache_dir / src.name
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                copied += 1
            else:
                shutil.copy2(src, dst)
                copied += 1

            if copied % progress_every == 0 or copied == len(images):
                elapsed = time.perf_counter() - t0
                logger.info(
                    f"Upload cache copy progress: {copied}/{len(images)} | elapsed={elapsed:.1f}s")

        elapsed = time.perf_counter() - t0
        logger.info(
            f"Upload cache ready: {cache_dir} | images={len(images)} | copy_time={elapsed:.1f}s")
        return cache_dir, len(images)

    def _cleanup_upload_cache(self, cache_dir: Path, logger: logging.Logger) -> None:
        try:
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
                logger.info(f"Upload cache cleaned: {cache_dir}")
        except Exception:
            logger.exception(f"Failed to clean upload cache: {cache_dir}")

    def _require_workspace_owned_path(self, path: Path) -> Path:
        candidate = Path(path).resolve(strict=False)
        workspace_root = self.workspace_layout.root.resolve(strict=False)
        try:
            candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError(f"Path escapes run workspace: {path}") from exc
        return candidate

    def _reset_workspace_directory(self, path: Path) -> None:
        directory = self._require_workspace_owned_path(path)
        if directory == self.workspace_layout.root.resolve(strict=False):
            raise ValueError("Refusing to reset the workspace root")
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    def _replace_legacy_directory_after_success(
        self,
        *,
        source_dir: Path,
        target_dir: Path,
    ) -> None:
        source = self._require_workspace_owned_path(source_dir)
        if not source.is_dir():
            raise FileNotFoundError(f"Workspace directory not found: {source_dir}")

        target = Path(target_dir)
        target_parent = target.parent
        self._check_write_capacity(
            role="published_directory_mirror",
            path=target_parent,
            required_bytes=int(self._directory_size(source) * 1.1),
        )
        target_parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not target.is_dir():
            raise NotADirectoryError(f"Legacy mirror target is not a directory: {target}")

        temp_target = target_parent / f".{target.name}.tmp-{self.run_id}"
        backup_target = target_parent / f".{target.name}.bak-{self.run_id}"
        self._reconcile_stale_legacy_directory_mirror(
            target=target,
            temp_target=temp_target,
            backup_target=backup_target,
        )

        shutil.copytree(source, temp_target)
        try:
            if target.exists():
                target.rename(backup_target)
            temp_target.rename(target)
        except Exception:
            if not target.exists() and backup_target.exists():
                backup_target.rename(target)
            raise
        else:
            if backup_target.exists():
                shutil.rmtree(backup_target)

    def _reconcile_stale_legacy_directory_mirror(
        self,
        *,
        target: Path,
        temp_target: Path,
        backup_target: Path,
    ) -> None:
        if temp_target.exists():
            if not temp_target.is_dir():
                raise NotADirectoryError(
                    f"Stale publish mirror temp path is not a directory: {temp_target}"
                )
            shutil.rmtree(temp_target)

        if not backup_target.exists():
            return
        if not backup_target.is_dir():
            raise NotADirectoryError(
                f"Stale publish mirror backup path is not a directory: {backup_target}"
            )
        if target.exists():
            shutil.rmtree(backup_target)
        else:
            backup_target.rename(target)

    def _replace_legacy_file_after_success(
        self,
        *,
        source_file: Path,
        target_file: Path,
    ) -> None:
        source = self._require_workspace_owned_path(source_file)
        if not source.is_file():
            raise FileNotFoundError(f"Workspace file not found: {source_file}")

        target = Path(target_file)
        target_parent = target.parent
        self._check_write_capacity(
            role="published_file_mirror",
            path=target_parent,
            required_bytes=int(source.stat().st_size * 1.1),
        )
        target_parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not target.is_file():
            raise IsADirectoryError(f"Legacy mirror target is not a file: {target}")

        temp_target = target_parent / f".{target.name}.tmp-{self.run_id}"
        backup_target = target_parent / f".{target.name}.bak-{self.run_id}"
        self._reconcile_stale_legacy_file_mirror(
            target=target,
            temp_target=temp_target,
            backup_target=backup_target,
        )

        shutil.copy2(source, temp_target)
        try:
            if target.exists():
                target.rename(backup_target)
            temp_target.rename(target)
        except Exception:
            if not target.exists() and backup_target.exists():
                backup_target.rename(target)
            raise
        else:
            if backup_target.exists():
                backup_target.unlink()

    def _reconcile_stale_legacy_file_mirror(
        self,
        *,
        target: Path,
        temp_target: Path,
        backup_target: Path,
    ) -> None:
        if temp_target.exists():
            if not temp_target.is_file():
                raise IsADirectoryError(
                    f"Stale publish mirror temp path is not a file: {temp_target}"
                )
            temp_target.unlink()

        if not backup_target.exists():
            return
        if not backup_target.is_file():
            raise IsADirectoryError(
                f"Stale publish mirror backup path is not a file: {backup_target}"
            )
        if target.exists():
            backup_target.unlink()
        else:
            backup_target.rename(target)

    def _export_orthomosaic_to_workspace(
        self,
        *,
        processor: Any,
        project_id: int,
        task_id: str,
        task_key: str,
        published_dir: Path,
        filename: str,
        epsg: int,
        candidates: list[str],
        gdalwarp_path: str,
    ) -> tuple[Path | None, Path | None]:
        workspace_dir = self.workspace_layout.webodm_ortho / task_key
        self._reset_workspace_directory(workspace_dir)

        workspace_path = processor.export_orthomosaic(
            project_id,
            task_id,
            out_dir=workspace_dir,
            filename=filename,
            epsg=epsg,
            candidates=candidates,
            gdalwarp_path=gdalwarp_path,
        )

        if not workspace_path:
            return None, None

        workspace_path = self._require_workspace_owned_path(Path(workspace_path))
        published_path = Path(published_dir) / workspace_path.name
        self._replace_legacy_file_after_success(
            source_file=workspace_path,
            target_file=published_path,
        )
        return workspace_path, published_path

    def _export_pointcloud_to_workspace(
        self,
        *,
        processor: Any,
        project_id: int,
        task_id: str,
        task_key: str,
        published_dir: Path,
        laz_archive_name: str,
        pcd_name: str,
        candidates: list[str],
        max_points: int,
        viewpoint: str,
    ) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
        workspace_dir = self.workspace_layout.webodm_3d / task_key
        self._reset_workspace_directory(workspace_dir)

        workspace_result = processor.export_pointcloud(
            project_id,
            task_id,
            out_dir=workspace_dir,
            laz_archive_name=laz_archive_name,
            pcd_name=pcd_name,
            candidates=candidates,
            max_points=max_points,
            viewpoint=viewpoint,
        )

        published_result = dict(workspace_result or {})
        workspace_paths: dict[str, str] = {}
        published_paths: dict[str, str] = {}

        for key in ("laz", "ply", "pcd"):
            value = published_result.get(key)
            if not value:
                continue
            workspace_path = self._require_workspace_owned_path(Path(value))
            if not workspace_path.is_file():
                continue
            published_path = Path(published_dir) / workspace_path.name
            self._replace_legacy_file_after_success(
                source_file=workspace_path,
                target_file=published_path,
            )
            workspace_paths[key] = str(workspace_path)
            published_paths[key] = str(published_path)
            published_result[key] = str(published_path)

        return published_result, workspace_paths, published_paths

    def _download_all_assets_zip_to_workspace(
        self,
        *,
        processor: Any,
        project_id: int,
        task_id: str,
        task_key: str,
        published_dir: Path,
        filename: str,
    ) -> tuple[Path | None, Path | None]:
        workspace_dir = self.workspace_layout.webodm_odm / task_key
        workspace_dir.mkdir(parents=True, exist_ok=True)
        workspace_path = workspace_dir / filename

        ok = processor.download_all_assets_safe(
            project_id,
            task_id,
            workspace_path,
        )
        if not ok:
            return None, None

        workspace_path = self._require_workspace_owned_path(workspace_path)
        if not workspace_path.is_file():
            raise FileNotFoundError(f"Workspace all-assets zip not found: {workspace_path}")

        published_path = Path(published_dir) / workspace_path.name
        self._replace_legacy_file_after_success(
            source_file=workspace_path,
            target_file=published_path,
        )
        return workspace_path, published_path


    def _check_control_or_raise(self, stage_name: str) -> None:
        try:
            self.control.check_or_raise()
        except RuntimeError as e:
            if str(e) == "__PIPELINE_PAUSED__":
                reason = f"pause requested during/before stage '{stage_name}'"
                self.loggers["pipeline"].warning(f"⏸ PAUSE | {reason}")

                try:
                    self.repo.mark_run_paused(
                        self.run_id,
                        paused_after_stage=stage_name,
                        reason="pause_hotkey",
                    )
                except Exception:
                    self.loggers["pipeline"].exception(
                        "Failed to mark run as paused in DB"
                    )

                raise

            if str(e) == "__PIPELINE_ABORTED__":
                self.loggers["pipeline"].error(
                    f"🛑 ABORT | abort requested during/before stage '{stage_name}'"
                )
                raise

            raise
    # STAGES
    def stage_data_segregation(self) -> Dict[str, Any]:
        logger = self.loggers["segregation"]
        logger.info("Stage: Data Segregation")
        create_run_workspace(self.workspace_layout)

        summary = run_data_segregation(
            source_dir=self.source_dir,
            surveys_root=self.surveys_root,
            year=self.year,
            logger=logger,
            survey_id_override=self.survey_id_override,
            use_year_subdir=(
                self.use_year_subdir_override
                if self.use_year_subdir_override is not None
                else bool(self.config.get("experiment", {}).get("use_year_subdir", True))
            ),
            force=self.force_segregation,
        )

        # .../<year>/<survey_id>/rgb by default; can be without year subdir.
        self._set_survey_artifact_context(
            summary["survey_id"],
            Path(summary["survey_path"]),
        )

        # Rename KML to match survey ID
        boundary_dir = self.rgb_path / "boundary"
        kmls = sorted(boundary_dir.glob("*.kml"))
        if not kmls:
            raise FileNotFoundError(
                f"No .kml found after segregation in: {boundary_dir}")

        original_kml = kmls[0]
        new_kml_path = boundary_dir / f"{self.survey_id}.kml"

        if original_kml.name != new_kml_path.name:
            if new_kml_path.exists():
                new_kml_path.unlink()
            original_kml.rename(new_kml_path)

        logger.info(f"KML renamed to: {new_kml_path.name}")

        if self.survey_id is None:
            raise RuntimeError("survey_id is not set")

        self.repo.attach_survey_id(self.run_id, self.survey_id)
        self.repo.upsert_survey_running(self.survey_id)

        summary = dict(summary)
        summary["workspace"] = describe_run_workspace(self.workspace_layout)
        if self.published_layout is not None:
            summary["published"] = describe_published_survey(self.published_layout)

        return summary

    def stage_cross_run_image_filter(self) -> Dict[str, Any]:
        logger = self.loggers["cross_run_filter"]
        logger.info("Stage: Cross-run image filter")

        rgb_path = self._require_rgb_path()

        input_dir = rgb_path / "images" / "raw"
        legacy_output_dir = rgb_path / "images" / "path"
        legacy_excluded_dir = legacy_output_dir.parent / "cross-runs"
        output_dir = self.workspace_layout.images_path
        excluded_dir = self.workspace_layout.images_cross_runs
        create_run_workspace(self.workspace_layout)

        filter_cfg = self.config.get("cross_run_filter", {})
        max_gap = int(filter_cfg.get("max_gap", 10))
        window = int(filter_cfg.get("window", 3))
        delete_raw_after = bool(filter_cfg.get("delete_raw_after_success", False))

        enabled = self.crossrun_enabled_override
        if enabled is None:
            enabled = bool(filter_cfg.get("enabled", True))

        logger.info(f"Input (raw): {input_dir}")
        logger.info(f"Workspace output (kept/path): {output_dir}")
        logger.info(f"Workspace output (excluded/cross-runs): {excluded_dir}")
        logger.info(f"Legacy output mirror (kept/path): {legacy_output_dir}")
        logger.info(f"Legacy output mirror (excluded/cross-runs): {legacy_excluded_dir}")

        if not enabled:
            logger.info("Cross-run filter disabled. Copying RAW -> PATH without exclusions.")

            self._reset_workspace_directory(output_dir)
            self._reset_workspace_directory(excluded_dir)

            images = self._iter_jpeg_files(input_dir)
            for img in images:
                shutil.copy2(img, output_dir / img.name)

            self._replace_legacy_directory_after_success(
                source_dir=output_dir,
                target_dir=legacy_output_dir,
            )
            self._replace_legacy_directory_after_success(
                source_dir=excluded_dir,
                target_dir=legacy_excluded_dir,
            )

            result = {
                "filter_enabled": False,
                "input_dir": str(input_dir),
                "output_dir": str(legacy_output_dir),
                "excluded_dir": str(legacy_excluded_dir),
                "total_images": len(images),
                "total_kept": len(images),
                "total_excluded": 0,
                "cross_runs_detected": 0,
                "too_close_exclusions": 0,
                "cluster_exclusions": 0,
                "crossrun_flag": "c",                 # keep default semantics
                "experiment_crossrun_label": "NF",   # experiment naming only
                "raw_deleted": False,
                "workspace": {
                    "input_dir": str(input_dir),
                    "output_dir": str(output_dir),
                    "excluded_dir": str(excluded_dir),
                },
                "published": {
                    "output_dir": str(legacy_output_dir),
                    "excluded_dir": str(legacy_excluded_dir),
                },
                "image_classifications": [
                    {
                        "relative_path": image.name,
                        "disposition": "kept",
                        "reasons": ["filter_disabled"],
                    }
                    for image in images
                ],
            }

            self.state["crossrun_flag"] = "c"
            self.state["experiment_crossrun_label"] = "NF"
            return result

        result = run_filter(
            input_dir=input_dir,
            output_dir=output_dir,
            logger=logger,
            max_gap=max_gap,
            cross_run_window=window,
        )

        self._replace_legacy_directory_after_success(
            source_dir=output_dir,
            target_dir=legacy_output_dir,
        )
        self._replace_legacy_directory_after_success(
            source_dir=excluded_dir,
            target_dir=legacy_excluded_dir,
        )

        result = dict(result)
        result["workspace"] = {
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "excluded_dir": str(excluded_dir),
        }
        result["published"] = {
            "output_dir": str(legacy_output_dir),
            "excluded_dir": str(legacy_excluded_dir),
        }
        result["output_dir"] = str(legacy_output_dir)
        result["excluded_dir"] = str(legacy_excluded_dir)

        excluded = self._safe_int(result.get("total_excluded"))
        # Naming semantics:
        # c  = crossrun/crosspath images were included
        # xc = crossrun/crosspath images were excluded by the filter
        #
        # If the filter is enabled and applied, use "xc" even when total_excluded is 0.
        # A zero exclusion count only means no crossrun images were detected, not that
        # the filter was bypassed.
        naming_cfg = self.config.get("naming", {})
        crossrun_flag = str(naming_cfg.get("crossrun_mode") or "xc")

        if crossrun_flag not in {"c", "xc"}:
            raise ValueError(f"Invalid crossrun flag: {crossrun_flag!r}")


        self.state["crossrun_flag"] = crossrun_flag
        result["crossrun_flag"] = crossrun_flag
        result["crossrun_excluded_count"] = excluded

        # Experiment naming label
        self.state["experiment_crossrun_label"] = "F"
        result["experiment_crossrun_label"] = "F"

        if delete_raw_after:
            try:
                total_images = self._safe_int(result.get("total_images"))
                kept = self._safe_int(result.get("total_kept"))
                excl = self._safe_int(result.get("total_excluded"))

                if total_images <= 0:
                    raise RuntimeError(
                        "Refusing to delete raw: total_images is 0 (unexpected)."
                    )
                if kept + excl != total_images:
                    raise RuntimeError(
                        f"Refusing to delete raw: kept+excluded != total ({kept}+{excl}!={total_images})"
                    )
                if not output_dir.exists() or not excluded_dir.exists():
                    raise RuntimeError(
                        "Refusing to delete raw: output directories missing."
                    )

                kept_fs = len([
                    p for p in output_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                ])
                excl_fs = len([
                    p for p in excluded_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
                ])

                if kept_fs + excl_fs != total_images:
                    raise RuntimeError(
                        f"Refusing to delete raw: filesystem output count mismatch "
                        f"({kept_fs}+{excl_fs}!={total_images})"
                    )

                logger.warning(
                    f"Deleting RAW images folder to save space: {input_dir} "
                    f"(total={total_images}, kept={kept_fs}, excluded={excl_fs})"
                )

                if input_dir.name == "raw" and input_dir.exists():
                    shutil.rmtree(input_dir)
                    logger.info(f"Raw folder deleted after filtering: {input_dir}")
                else:
                    logger.warning(f"Refusing to delete unexpected folder: {input_dir}")

                result["raw_deleted"] = True

            except Exception as e:
                logger.warning(f"Raw cleanup skipped/failed: {e}")
                result["raw_deleted"] = False
                result["raw_delete_error"] = str(e)
        else:
            result["raw_deleted"] = False

        return result

    def stage_kml_boundary(self) -> Dict[str, Any]:
        logger = self.loggers["kml"]
        logger.info("Stage: KML Boundary Setter")

        rgb_path = self._require_rgb_path()
        boundary_dir = rgb_path / "boundary"
        workspace_boundary_dir = self.workspace_layout.boundary
        create_run_workspace(self.workspace_layout)
        self._reset_workspace_directory(workspace_boundary_dir)

        logger.info(f"Input boundary KML/KMZ: {boundary_dir}")
        logger.info(f"Workspace boundary output: {workspace_boundary_dir}")
        logger.info(f"Legacy boundary mirror: {boundary_dir}")

        summary = run_kml(
            kml_dir=boundary_dir,
            geojson_dir=workspace_boundary_dir,
            csv_dir=workspace_boundary_dir,
            logger=logger,
        )

        workspace_summary = dict(summary)
        processed_files = summary.get("processed_files") or []
        published_processed_files = []

        for processed in processed_files:
            published_processed = dict(processed)
            geojson_value = processed.get("geojson")
            if geojson_value:
                workspace_geojson = Path(str(geojson_value))
                published_geojson = boundary_dir / workspace_geojson.name
                self._replace_legacy_file_after_success(
                    source_file=workspace_geojson,
                    target_file=published_geojson,
                )
                published_processed["geojson"] = str(published_geojson)

            csv_value = processed.get("csv")
            if csv_value:
                workspace_csv = Path(str(csv_value))
                published_csv = boundary_dir / workspace_csv.name
                self._replace_legacy_file_after_success(
                    source_file=workspace_csv,
                    target_file=published_csv,
                )
                published_processed["csv"] = str(published_csv)

            published_processed_files.append(published_processed)

        summary = dict(summary)
        summary["processed_files"] = published_processed_files
        summary["geojson_dir"] = str(boundary_dir)
        summary["csv_dir"] = str(boundary_dir)
        summary["workspace"] = {
            "input_dir": str(boundary_dir),
            "geojson_dir": str(workspace_boundary_dir),
            "csv_dir": str(workspace_boundary_dir),
            "processed_files": workspace_summary.get("processed_files") or [],
        }
        summary["published"] = {
            "geojson_dir": str(boundary_dir),
            "csv_dir": str(boundary_dir),
            "processed_files": published_processed_files,
        }

        geojson_path = None
        if published_processed_files:
            geojson_path = published_processed_files[0].get("geojson")

        boundary_ok = bool(geojson_path and Path(geojson_path).exists())

        self.state["boundary_available"] = boundary_ok
        self.state["boundary_geojson_path"] = str(
            geojson_path) if geojson_path else None

        if not boundary_ok:
            logger.warning(
                "No valid polygon boundary produced (GeoJSON missing). Pipeline will run Task 1 only.")

        # Embed into returned dict so StageRunner persists them to DB
        summary["boundary_available"] = boundary_ok
        summary["boundary_geojson_path"] = str(
            geojson_path) if geojson_path else None

        return summary

    @staticmethod
    def _merge_webodm_state(
        target: Dict[str, Any],
        incoming: Mapping[str, Any],
    ) -> Dict[str, Any]:
        for key, value in incoming.items():
            existing = target.get(key)
            if isinstance(existing, dict) and isinstance(value, Mapping):
                RGBPipeline._merge_webodm_state(existing, value)
            elif isinstance(value, Mapping):
                target[key] = RGBPipeline._merge_webodm_state({}, value)
            else:
                target[key] = value
        return target

    def _run_webodm_operation(self, task_key: str) -> Dict[str, Any]:
        previous_web = self.state.get("webodm")
        aggregate: Dict[str, Any] = {}
        if isinstance(previous_web, Mapping):
            self._merge_webodm_state(aggregate, previous_web)

        original_skip_task1 = self.skip_task1_webodm
        original_skip_task2 = self.skip_task2_webodm
        original_skip_task4 = self.skip_task4_webodm
        try:
            self.skip_task1_webodm = original_skip_task1 or (
                task_key == "task2" and not original_skip_task4
            )
            self.skip_task2_webodm = task_key != "task2"
            self.skip_task4_webodm = task_key != "task4"
            self.state["webodm"] = aggregate

            sequence_result = self._stage_webodm_sequence()
            sequence_state = self.state.get("webodm")
            if isinstance(sequence_state, Mapping):
                self._merge_webodm_state(aggregate, sequence_state)
            if isinstance(sequence_result, Mapping):
                self._merge_webodm_state(aggregate, sequence_result)

            task_state = aggregate.get(task_key)
            operation_succeeded = (
                isinstance(task_state, Mapping)
                and task_state.get("success") is True
            )
            operation_output: Dict[str, Any] = {
                "operation_key": task_key,
                "status": "completed" if operation_succeeded else "failed",
                "success": operation_succeeded,
                "project_id": aggregate.get("project_id"),
                "project_name": aggregate.get("project_name"),
                "task": task_state,
                "mode": self.webodm_mode,
                "downloads": (aggregate.get("downloads") or {}).get(task_key, {}),
                "workspace": aggregate.get("workspace") or {},
                "published": aggregate.get("published") or {},
                "selected_webodm_task": aggregate.get("selected_webodm_task"),
                "selected_orthomosaic": aggregate.get("selected_orthomosaic"),
                "webodm": aggregate,
            }
            if not operation_succeeded:
                error = str(
                    aggregate.get(f"{task_key}_error")
                    or aggregate.get(f"{task_key}_skip_reason")
                    or (
                        aggregate.get("boundary_reason")
                        if task_key == "task2"
                        else None
                    )
                    or f"WebODM {task_key} did not complete successfully"
                )
                operation_output["error"] = error
                raise StageFailedWithOutput(error, output=operation_output)
            return operation_output
        finally:
            self.skip_task1_webodm = original_skip_task1
            self.skip_task2_webodm = original_skip_task2
            self.skip_task4_webodm = original_skip_task4

    def stage_webodm(self) -> Dict[str, Any]:
        selected_operations = []
        if not self.skip_task4_webodm:
            selected_operations.append("task4")
        if not self.skip_task2_webodm:
            selected_operations.append("task2")

        if not selected_operations:
            return self._stage_webodm_sequence()

        forced_operation_keys = getattr(
            self,
            '_force_webodm_operations',
            set(),
        )
        if forced_operation_keys:
            selected_operations = [
                key for key in selected_operations
                if key in forced_operation_keys
            ]

        aggregate: Dict[str, Any] = {}
        existing_web = self.state.get("webodm")
        if isinstance(existing_web, Mapping):
            self._merge_webodm_state(aggregate, existing_web)

        task4_completed = False
        for task_key in selected_operations:
            self.state["webodm"] = aggregate
            stage_name = f"webodm_{task_key}"
            try:
                operation_result = self.runner.run(
                    stage_name,
                    lambda task_key=task_key: self._run_webodm_operation(task_key),
                    output_key=stage_name,
                    state=self.state,
                    force=task_key in forced_operation_keys,
                    stale_running_policy="rerun",
                    retry_attempts=1,
                    retry_delay_seconds=0,
                )
            except StageFailedWithOutput as exc:
                operation_result = exc.output or {}
                operation_web = operation_result.get("webodm")
                if isinstance(operation_web, Mapping):
                    aggregate = self._merge_webodm_state({}, operation_web)
                self.state["webodm"] = aggregate

                if task_key == "task2" and task4_completed:
                    aggregate["run_status"] = "partially_completed"
                    self.state["run_status_override"] = "partially_completed"
                    return aggregate
                raise
            finally:
                for stage_logger in self.loggers.values():
                    set_stage_context(stage_logger, "webodm")

            if not isinstance(operation_result, Mapping):
                raise RuntimeError(f"Missing persisted output for {stage_name}")
            operation_web = operation_result.get("webodm")
            if not isinstance(operation_web, Mapping):
                raise RuntimeError(f"Invalid persisted output for {stage_name}")
            aggregate = self._merge_webodm_state({}, operation_web)
            self.state["webodm"] = aggregate
            if task_key == "task4":
                task4_completed = True

        if self.state.get("run_status_override") == "partially_completed":
            self.state.pop("run_status_override", None)
        aggregate.pop("run_status", None)
        self.state["webodm"] = aggregate
        return aggregate

    def _stage_webodm_sequence(self) -> Dict[str, Any]:
        logger = self.loggers["webodm"]
        logger.info("Stage: WebODM Processing")

        survey_id = self._require_survey_id()
        rgb_path = self._require_rgb_path()
        create_run_workspace(self.workspace_layout)

        webodm_cfg = self.config["webodm"]
        naming_cfg = self.config.get("naming", {})
        exports_cfg = self.config.get("exports", {})
        qgis_tools_cfg = (exports_cfg.get("tools") or {})

        ds = self.state.get("data_segregation") or {}
        dirs = ds.get("dirs") or {}
        if not dirs:
            raise RuntimeError(
                "Missing data_segregation.dirs in state. Ensure segregation returns dirs mapping."
            )

        def dir_from_key(key: str, *, fallback: Optional[Path] = None) -> Path:
            p = dirs.get(key)
            if p:
                return Path(p)
            if fallback is not None:
                return Path(fallback)
            raise KeyError(f"Missing dir key in data_segregation.dirs: {key}")

        boundary_available = bool(self.state.get("boundary_available"))
        boundary_geojson_path = self.state.get("boundary_geojson_path")

        task1_flag = self._webodm_task_flag(
            "task1",
            default_boundary_mode="xb",
        )

        task2_flag = self._webodm_task_flag(
            "task2",
            default_boundary_mode="b",
        )

        task1_name = self.task_name_overrides.get("task1")
        task2_name = self.task_name_overrides.get("task2")

        if not task1_name:
            task1_name = self._webodm_task_name(
                survey_id=survey_id,
                flag=task1_flag,
                task_key="task1",
            )

        if not task2_name:
            task2_name = self._webodm_task_name(
                survey_id=survey_id,
                flag=task2_flag,
                task_key="task2",
            )

        task1_export_id = self.export_name_overrides.get("task1", task1_name)
        task2_export_id = self.export_name_overrides.get("task2", task2_name)

        skip_task1 = bool(getattr(self, "skip_task1_webodm", False))
        skip_task2 = bool(getattr(self, "skip_task2_webodm", False))
        skip_task4 = bool(getattr(self, "skip_task4_webodm", False))
        task1_bounded = bool(getattr(self, "task1_bounded", False))

        if skip_task1 and skip_task2 and skip_task4:
            raise RuntimeError("All WebODM tasks are skipped. Nothing to run.")

        if skip_task1 and task1_bounded:
            logger.warning("task1_bounded is set but Task 1 is skipped; ignoring task1_bounded.")

        production_mode = not bool(self.config.get("experiment", {}).get("enabled", False))

        if production_mode:
            task1_root_dir = rgb_path
            task2_root_dir = rgb_path
        else:
            task1_root_dir = rgb_path / task1_export_id
            task2_root_dir = rgb_path / task2_export_id

        task1_ortho_dir = task1_root_dir / "ortho"
        task1_odm_dir = task1_root_dir / "odm"

        task2_ortho_dir = task2_root_dir / "ortho"
        task2_3d_dir = task2_root_dir / "3d"
        task2_odm_dir = task2_root_dir / "odm"
        task2_dem_dtm_dir = task2_root_dir / "dem" / "odm" / "dtm"
        task2_dem_dsm_dir = task2_root_dir / "dem" / "odm" / "dsm"

        for p in [
            task1_root_dir,
            task1_ortho_dir,
            task1_odm_dir,
            task2_root_dir,
            task2_ortho_dir,
            task2_3d_dir,
            task2_odm_dir,
            task2_dem_dtm_dir,
            task2_dem_dsm_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)

        image_folder = Path(dirs.get("path") or (rgb_path / "images" / "path"))

        upload_cache_root_cfg = (self.config.get("paths") or {}).get("upload_cache_root")
        if not upload_cache_root_cfg:
            upload_cache_root_cfg = os.getenv("UPLOAD_CACHE_ROOT")
        local_root = Path(upload_cache_root_cfg) if upload_cache_root_cfg else Path(
            os.getenv("TEMP", r"C:\temp")
        )
        cache_root = local_root / "automation-pipeline" / "upload_cache" / self.run_id

        cached_dir: Optional[Path] = None
        upload_folder: Path = image_folder

        try:
            try:
                cached_dir, _ = self._stage_upload_cache(
                    src_dir=image_folder,
                    cache_root=cache_root,
                    logger=logger,
                    progress_every=25,
                    require_free_multiplier=1.2,
                )
                upload_folder = cached_dir

                self._remember_webodm_upload_folder(
                    upload_folder=upload_folder,
                    cached_dir=cached_dir,
                    source_folder=image_folder,
                    fallback_direct=False,
                )

            except StorageCapacityError:
                raise
            except Exception as e:
                logger.warning(
                    f"Upload cache unavailable, uploading directly from source. reason={e}"
                )
                cached_dir = None
                upload_folder = image_folder

                self._remember_webodm_upload_folder(
                    upload_folder=upload_folder,
                    cached_dir=None,
                    source_folder=image_folder,
                    fallback_direct=True,
                    fallback_reason=str(e),
                )

            processor = self._create_webodm_processor(logger)


            project_suffix = str(self.state.get("webodm_project_suffix") or "").strip()
            project_name = f"{survey_id}{project_suffix}"

            prev_web = self.state.get("webodm") or {}
            prev_project_id = prev_web.get("project_id")
            prev_task1 = prev_web.get("task1") or {}
            prev_task2 = prev_web.get("task2") or {}

            if prev_project_id:
                project_id = prev_project_id
                logger.info(f"Resuming: reattaching to existing project (ID={project_id})")
            else:
                if self._resume_had_webodm_history:
                    raise StageRequiresRecovery(
                        'A previous WebODM attempt exists but no durable project '
                        'binding could be restored. Run the WebODM binding repair '
                        'tool; refusing to create a duplicate project.',
                        output={
                            'status': 'requires_recovery',
                            'operation_key': 'task4',
                        },
                    )
                project_id = processor.create_project(
                    name=project_name,
                    description="RGB automated processing",
                )
                self._webodm_projects_created_this_process.add(int(project_id))
                log_event(
                    logger,
                    "webodm_project_created",
                    project_id=project_id,
                    project_name=project_name,
                )
                self._save_webodm_checkpoint({
                    "project_id": project_id,
                    "project_name": project_name,
                    "task1": None if skip_task1 else {},
                    "task2": None,
                    "downloads": {"task1": {}, "task2": {}},
                })

            # Update state with project info so Task 4 can find it if run as primary
            self.state["webodm"] = {
                "project_id": project_id,
                "project_name": project_name,
                "task1": prev_task1 if prev_project_id else (None if skip_task1 else {}),
                "task2": prev_task2 if prev_project_id else None,
                "task4": prev_web.get("task4") or {},
                "downloads": prev_web.get("downloads") or {"task1": {}, "task2": {}, "task4": {}},
            }

            project_bindings = []
            if not skip_task1:
                project_bindings.append(('task1', task1_name))
            if not skip_task2:
                project_bindings.append(('task2', task2_name))
            if not skip_task4:
                task4_flag = self._webodm_task_flag(
                    'task4',
                    default_boundary_mode='b',
                )
                task4_name = self.task_name_overrides.get('task4')
                if not task4_name:
                    task4_name = self._webodm_task_name(
                        survey_id=survey_id,
                        flag=task4_flag,
                        task_key='task4',
                    )
                project_bindings.append(('task4', task4_name))

            for operation_key, operation_name in project_bindings:
                self._persist_webodm_binding(
                    task_key=operation_key,
                    project_id=int(project_id),
                    task_name=operation_name,
                    local_status='project_bound',
                )

            # ---------------- TASK 1 ----------------
            if skip_task1:
                logger.info("Skipping WebODM Task 1 by request.")
                current_task1_id = ""
                t1_success = False
                t1_runtime = 0.0
            else:
                t1_already_done = (
                    prev_task1.get("id")
                    and prev_task1.get("success") is True
                )

                if t1_already_done:
                    current_task1_id = str(prev_task1["id"])
                    t1_success = True
                    t1_runtime = float(prev_task1.get("runtime_seconds") or 0)
                    logger.info(
                        f"Resuming: Task 1 already completed "
                        f"(id={current_task1_id}) — skipping upload and processing"
                    )
                else:
                    task1_binding = self.repo.get_webodm_binding(
                        self.run_id,
                        'task1',
                    )
                    existing_task1_id = (
                        str(task1_binding['task_id'])
                        if task1_binding and task1_binding.get('task_id')
                        else (
                            str(prev_task1['id'])
                            if prev_task1.get('id')
                            else None
                        )
                    )
                    if existing_task1_id:
                        try:
                            task1_info = processor.get_task(
                                int(project_id),
                                existing_task1_id,
                            )
                        except WebODMTaskNotFound as exc:
                            raise StageRequiresRecovery(
                                f'Canonical WebODM Task 1 is missing: '
                                f'project_id={project_id} '
                                f'task_id={existing_task1_id}',
                                output={
                                    'status': 'requires_recovery',
                                    'operation_key': 'task1',
                                    'project_id': project_id,
                                    'task_id': existing_task1_id,
                                },
                            ) from exc
                        task1_status, _terminal = (
                            WebODMProcessor._normalize_status(
                                task1_info.get('status')
                            )
                        )
                        logger.info(
                            f"Resuming: Task 1 current status in WebODM: {task1_status!r}"
                        )
                        self._persist_webodm_binding(
                            task_key='task1',
                            project_id=int(project_id),
                            task_id=existing_task1_id,
                            task_name=task1_name,
                            remote_status=task1_status,
                            raw_status=task1_info.get('status'),
                            local_status='reattached',
                        )

                        if task1_status == "completed":
                            current_task1_id = existing_task1_id
                            t1_success = True
                            t1_runtime = 0.0
                            _t1_info = task1_info
                            logger.info(
                                f"Resuming: Task 1 already completed in WebODM "
                                f"(id={current_task1_id}) — reusing"
                            )

                        elif task1_status in ("queued", "running"):
                            current_task1_id = existing_task1_id
                            logger.info(
                                f"Resuming: Task 1 still {task1_status} in WebODM "
                                f"(id={current_task1_id}) — waiting for completion"
                            )
                            t1_success, t1_runtime, _t1_info = processor.wait_for_completion(
                                project_id, current_task1_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                            )

                        elif task1_status == 'failed':
                            raise RuntimeError(
                                f'WEBODM_TASK_FAILED: task '
                                f'{existing_task1_id} is failed; the pipeline '
                                'will not create a replacement task.'
                            )
                        elif task1_status == 'canceled':
                            raise RuntimeError('WEBODM_TASK_CANCELED')
                        else:
                            raise WebODMTaskLookupError(
                                f'Unsupported Task 1 status {task1_status!r} '
                                f'for task {existing_task1_id}'
                            )

                    else:
                        if (
                            self._resume_had_webodm_history
                            and int(project_id)
                            not in self._webodm_projects_created_this_process
                        ):
                            raise StageRequiresRecovery(
                                'Resumed Task 1 has no durable task UUID. '
                                'Repair the binding before resuming; refusing '
                                'to create a duplicate task.',
                                output={
                                    'status': 'requires_recovery',
                                    'operation_key': 'task1',
                                    'project_id': project_id,
                                },
                            )
                        task1_options = dict(webodm_cfg.get("task1_options", {}))

                        if task1_bounded:
                            if not boundary_available:
                                raise RuntimeError("Task 1 bounded was requested, but boundary is not available.")
                            if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                                raise RuntimeError("Task 1 bounded was requested, but boundary GeoJSON is missing.")

                            boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")
                            task1_options["boundary"] = boundary_geojson
                            logger.info("Task 1 will run as bounded (boundary injected into Task 1 options).")

                        current_task1_id = processor.create_task_with_images(
                            project_id=project_id,
                            name=task1_name,
                            image_folder=str(upload_folder),
                            options=task1_options,
                            processing_node=webodm_cfg.get("node_id"),
                        )
                        self._persist_webodm_binding(
                            task_key='task1',
                            project_id=int(project_id),
                            task_id=str(current_task1_id),
                            task_name=task1_name,
                            remote_status='queued',
                            local_status='task_created',
                        )
                        t1_success, t1_runtime, _t1_info = processor.wait_for_completion(
                            project_id, current_task1_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                        )

                    final_t1_raw_status = (_t1_info or {}).get('status')
                    final_t1_status, _terminal = (
                        WebODMProcessor._normalize_status(final_t1_raw_status)
                    )
                    self._persist_webodm_binding(
                        task_key='task1',
                        project_id=int(project_id),
                        task_id=str(current_task1_id),
                        task_name=task1_name,
                        remote_status=final_t1_status,
                        raw_status=final_t1_raw_status,
                        local_status=(
                            'processing_completed'
                            if t1_success
                            else 'remote_failed'
                        ),
                        success=bool(t1_success),
                        runtime_seconds=float(t1_runtime),
                    )
                    if not t1_success:
                        raise RuntimeError(
                            f'WEBODM_TASK_FAILED: task {current_task1_id} '
                            'did not complete; the pipeline will not create a '
                            'replacement task.'
                        )

                    self._save_webodm_checkpoint({
                        "project_id": project_id,
                        "project_name": project_name,
                        "task1": {
                            "id": current_task1_id,
                            "name": task1_name,
                            "success": t1_success,
                            "runtime_seconds": t1_runtime,
                        },
                        "task2": None,
                        "downloads": {"task1": {}, "task2": {}},
                    })

            result: Dict[str, Any] = {
                "project_id": project_id,
                "project_name": project_name,
                "task1": None if skip_task1 else {
                    "id": current_task1_id,
                    "name": task1_name,
                    "success": t1_success,
                    "runtime_seconds": t1_runtime,
                },
                "task2": None,
                "boundary_used": bool(task1_bounded and not skip_task1),
                "boundary_reason": None,
                "boundary_geojson_path": boundary_geojson_path,
                "downloads": {
                    "task1": prev_web.get("downloads", {}).get("task1") or {},
                    "task2": {},
                },
            }

            if (not skip_task1) and prev_web.get("downloads", {}).get("task1"):
                logger.info("Resuming: reusing Task 1 downloads from previous run")

            # ---------------- Downloads after TASK 1 ----------------
            if (
                not skip_task1
                and exports_cfg.get("enabled", False)
                and exports_cfg.get("ortho", {}).get("enabled", False)
            ):
                ortho_cfg = exports_cfg["ortho"]

                out_dir = task1_ortho_dir
                epsg = int(ortho_cfg.get("reproject_epsg", 4326))
                candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

                task1_export_override = self.export_name_overrides.get("task1")
                if task1_export_override:
                    filename = f"{task1_export_override}.tif"
                else:
                    filename = self._orthomosaic_filename(
                        task_key="task1",
                        flag=task1_flag,
                    )

                workspace_path, published_path = self._export_orthomosaic_to_workspace(
                    processor=processor,
                    project_id=project_id,
                    task_id=current_task1_id,
                    task_key="task1",
                    published_dir=out_dir,
                    filename=filename,
                    epsg=epsg,
                    candidates=candidates,
                    gdalwarp_path=(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp"),
                )

                if published_path:
                    result["downloads"]["task1"]["orthomosaic"] = str(published_path)
                    result["downloads"]["task1"]["epsg"] = epsg
                    result.setdefault("workspace", {}).setdefault("webodm_ortho", {})["task1"] = str(workspace_path)
                    result.setdefault("published", {}).setdefault("webodm_ortho", {})["task1"] = str(published_path)
                else:
                    logger.warning("Could not download orthomosaic for Task 1.")

            # ---------------- TASK 4 (primary, not fallback) ----------------
            skip_task4 = bool(getattr(self, "skip_task4_webodm", True))

            if skip_task4:
                logger.info("Skipping WebODM Task 4 by request.")
            elif not boundary_available:
                msg = "Boundary not available. Skipping Task 4 (requires bounded model)."
                logger.warning(msg)
                result["task4_skip_reason"] = msg
            elif not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                msg = "Boundary GeoJSON path is missing. Skipping Task 4."
                logger.warning(msg)
                result["task4_skip_reason"] = msg
            else:
                # Task 4 can run - boundary is available and not skipped
                logger.info(
                    "Running Task 4 as primary task "
                    f"(webodm_mode={getattr(self, 'webodm_mode', 'both')})."
                )
                try:
                    t4_result = self.run_webodm_fallback_task(
                        task_key="task4",
                        fallback_reason="primary_task4",
                    )
                    result["task4"] = t4_result.get("task4")
                    result["downloads"]["task4"] = t4_result.get("downloads") or {}
                    result["selected_webodm_task"] = t4_result.get("selected_webodm_task")
                    result["selected_orthomosaic"] = t4_result.get("selected_orthomosaic")
                except RuntimeError as e:
                    if str(e) in ("__PIPELINE_PAUSED__", "__PIPELINE_ABORTED__"):
                        raise
                    logger.exception("Task 4 primary run failed.")
                    result["task4_failed"] = True
                    result["task4_error"] = str(e)

                    # Combined mode and task4-only mode both stop immediately on Task 4 failure.
                    if (not skip_task2) or (skip_task1 and skip_task2):
                        logger.error(
                            "Task 4 failed in a mode that requires it to succeed before continuing. "
                            "Failing the WebODM stage."
                        )
                        self._clear_webodm_checkpoint()
                        raise

            # ---------------- TASK 2 ----------------
            if skip_task2:
                logger.info("Skipping WebODM Task 2 by request.")
                result["boundary_reason"] = "Task 2 skipped by request."
            elif not boundary_available:
                msg = "Boundary not available. Skipping Task 2 (bounded models)."
                logger.warning(msg)
                result["boundary_reason"] = msg
            elif not boundary_geojson_path or not Path(boundary_geojson_path).exists():
                msg = "Boundary flag is True but GeoJSON path is missing. Skipping Task 2."
                logger.warning(msg)
                result["boundary_reason"] = msg
            else:
                # Task 2 can run - boundary is available and not skipped
                boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")
                task2_options = dict(webodm_cfg.get("task2_options", {}))
                task2_options["boundary"] = boundary_geojson

                t2_already_done = (
                    prev_task2.get("id")
                    and prev_task2.get("success") is True
                )

                if t2_already_done:
                    current_task2_id = str(prev_task2["id"])
                    t2_success = True
                    t2_runtime = float(prev_task2.get("runtime_seconds") or 0)
                    logger.info(
                        f"Resuming: Task 2 already completed "
                        f"(id={current_task2_id}) — skipping upload and processing"
                    )
    
                else:
                    task2_binding = self.repo.get_webodm_binding(
                        self.run_id,
                        'task2',
                    )
                    existing_task2_id = (
                        str(task2_binding['task_id'])
                        if task2_binding and task2_binding.get('task_id')
                        else (
                            str(prev_task2['id'])
                            if prev_task2.get('id')
                            else None
                        )
                    )
                    if existing_task2_id:
                        try:
                            task2_info = processor.get_task(
                                int(project_id),
                                existing_task2_id,
                            )
                        except WebODMTaskNotFound as exc:
                            raise StageRequiresRecovery(
                                f'Canonical WebODM Task 2 is missing: '
                                f'project_id={project_id} '
                                f'task_id={existing_task2_id}',
                                output={
                                    'status': 'requires_recovery',
                                    'operation_key': 'task2',
                                    'project_id': project_id,
                                    'task_id': existing_task2_id,
                                },
                            ) from exc
                        task2_status, _terminal = (
                            WebODMProcessor._normalize_status(
                                task2_info.get('status')
                            )
                        )
                        logger.info(
                            f"Resuming: Task 2 current status in WebODM: {task2_status!r}"
                        )
                        self._persist_webodm_binding(
                            task_key='task2',
                            project_id=int(project_id),
                            task_id=existing_task2_id,
                            task_name=task2_name,
                            remote_status=task2_status,
                            raw_status=task2_info.get('status'),
                            local_status='reattached',
                        )
    
                        if task2_status == "completed":
                            current_task2_id = existing_task2_id
                            t2_success = True
                            t2_runtime = 0.0
                            _t2_info = task2_info
                            logger.info(
                                f"Resuming: Task 2 already completed in WebODM "
                                f"(id={current_task2_id}) — reusing"
                            )
    
                        elif task2_status in ("queued", "running"):
                            current_task2_id = existing_task2_id
                            logger.info(
                                f"Resuming: Task 2 still {task2_status} in WebODM "
                                f"(id={current_task2_id}) — waiting for completion"
                            )
                            t2_success, t2_runtime, _t2_info = processor.wait_for_completion(
                                project_id, current_task2_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                            )
    
                        elif task2_status == 'failed':
                            current_task2_id = existing_task2_id
                            t2_success = False
                            t2_runtime = 0.0
                            _t2_info = task2_info
                            logger.error(
                                f'Bound Task 2 {current_task2_id} is failed; '
                                'refusing to create a replacement task.'
                            )
                        elif task2_status == 'canceled':
                            raise RuntimeError('WEBODM_TASK_CANCELED')
                        else:
                            raise WebODMTaskLookupError(
                                f'Unsupported Task 2 status {task2_status!r} '
                                f'for task {existing_task2_id}'
                            )
    
                    else:
                        if (
                            self._resume_had_webodm_history
                            and int(project_id)
                            not in self._webodm_projects_created_this_process
                        ):
                            raise StageRequiresRecovery(
                                'Resumed Task 2 has no durable task UUID. '
                                'Repair the binding before resuming; refusing '
                                'to create a duplicate task.',
                                output={
                                    'status': 'requires_recovery',
                                    'operation_key': 'task2',
                                    'project_id': project_id,
                                },
                            )
                        current_task2_id = processor.create_task_with_images(
                            project_id=project_id,
                            name=task2_name,
                            image_folder=str(upload_folder),
                            options=task2_options,
                            processing_node=webodm_cfg.get("node_id"),
                        )
                        self._persist_webodm_binding(
                            task_key='task2',
                            project_id=int(project_id),
                            task_id=str(current_task2_id),
                            task_name=task2_name,
                            remote_status='queued',
                            local_status='task_created',
                        )
                        log_event(
                            logger,
                            "webodm_task_created",
                            project_id=project_id,
                            task_key="task2",
                            task_id=current_task2_id,
                            task_name=task2_name,
                        )
                        t2_success, t2_runtime, _t2_info = processor.wait_for_completion(
                            project_id, current_task2_id, live=False, control_check=lambda: self._check_control_or_raise("webodm"),
                        )
                        log_event(
                            logger,
                            "webodm_task_status",
                            project_id=project_id,
                            task_key="task2",
                            task_id=current_task2_id,
                            status=(_t2_info or {}).get("status"),
                            success=t2_success,
                            elapsed_seconds=f"{t2_runtime:.2f}",
                        )
                    final_t2_raw_status = (_t2_info or {}).get('status')
                    final_t2_status, _terminal = (
                        WebODMProcessor._normalize_status(final_t2_raw_status)
                    )
                    self._persist_webodm_binding(
                        task_key='task2',
                        project_id=int(project_id),
                        task_id=str(current_task2_id),
                        task_name=task2_name,
                        remote_status=final_t2_status,
                        raw_status=final_t2_raw_status,
                        local_status=(
                            'processing_completed'
                            if t2_success
                            else 'remote_failed'
                        ),
                        success=bool(t2_success),
                        runtime_seconds=float(t2_runtime),
                    )
                result["task2"] = {
                    "id": current_task2_id,
                    "name": task2_name,
                    "success": t2_success,
                    "runtime_seconds": t2_runtime,
                }
                result["boundary_used"] = True
                self._save_webodm_checkpoint({
                    "project_id": project_id,
                    "project_name": project_name,
                    "task1": result["task1"],
                    "task2": result["task2"],
                    "downloads": result["downloads"],
                })

                # Update state with Task 2 results
                self.state["webodm"]["task2"] = result["task2"]
                self.state["webodm"]["downloads"] = result["downloads"]

                if not t2_success:
                    return result
    
                # ---------------- Task 2 bounded orthomosaic ----------------
                if exports_cfg.get("enabled", False) and exports_cfg.get("ortho", {}).get("enabled", False):
                    ortho_cfg = exports_cfg["ortho"]
    
                    out_dir = task2_ortho_dir
                    epsg = int(ortho_cfg.get("reproject_epsg", 4326))
                    candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]
    
                    task2_export_override = self.export_name_overrides.get("task2")
                    if task2_export_override:
                        filename = f"{task2_export_override}.tif"
                    else:
                        filename = self._orthomosaic_filename(
                            task_key="task2",
                            flag=task2_flag,
                        )
    
                    workspace_path, published_path = self._export_orthomosaic_to_workspace(
                        processor=processor,
                        project_id=project_id,
                        task_id=current_task2_id,
                        task_key="task2",
                        published_dir=out_dir,
                        filename=filename,
                        epsg=epsg,
                        candidates=candidates,
                        gdalwarp_path=(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp"),
                    )
    
                    if published_path:
                        result["downloads"]["task2"]["orthomosaic"] = str(published_path)
                        result["downloads"]["task2"]["epsg"] = epsg
                        result.setdefault("workspace", {}).setdefault("webodm_ortho", {})["task2"] = str(workspace_path)
                        result.setdefault("published", {}).setdefault("webodm_ortho", {})["task2"] = str(published_path)
    
                        selected = self._set_selected_orthomosaic(
                            task_key="task2",
                            source_path=published_path,
                            flag=task2_flag,
                            boundary_used=True,
                            fallback_used=False,
                        )
    
                        result["selected_webodm_task"] = "task2"
                        result["selected_orthomosaic"] = selected
                    else:
                        logger.warning("Could not download bounded orthomosaic for Task 2.")
    
    
                # ---------------- Downloads after TASK 2 ----------------
                if exports_cfg.get("enabled", False):
                    dem_cfg = (exports_cfg.get("dem") or {})
                    dem_do_download = False
    
                    if dem_do_download:
                        epsg = int(dem_cfg.get("reproject_epsg", 3857))
                        dtm_dir = task2_dem_dtm_dir
                        dsm_dir = task2_dem_dsm_dir
    
                        models = list(dem_cfg.get("models") or ["dtm", "dsm"])
                        colors = list(dem_cfg.get("colors") or [])
                        shadings = list(dem_cfg.get("shadings") or [])
                        tmpl = dem_cfg.get("filename_template", "{color}-{shading}.tif")
    
                        if not colors or not shadings:
                            logger.warning(
                                "DEM download enabled but colors/shadings not configured. Skipping DEM downloads."
                            )
                        else:
                            for model in models:
                                if model not in ("dtm", "dsm"):
                                    logger.warning(
                                        f"Unknown DEM model '{model}' (expected dtm/dsm). Skipping."
                                    )
                                    continue
    
                                out_base = dtm_dir if model == "dtm" else dsm_dir
    
                                for color in colors:
                                    for shading in shadings:
                                        fname = tmpl.format(color=color, shading=shading)
                                        tmp_raw = out_base / f"__tmp_raw_{fname}"
                                        final_out = out_base / fname
    
                                        asset_type = f"{model}/{color}/{shading}"
                                        ok = processor.download_asset_safe(
                                            project_id, current_task2_id, asset_type, tmp_raw
                                        )
                                        if ok:
                                            processor.run_gdalwarp(tmp_raw, final_out, epsg)
                                            try:
                                                tmp_raw.unlink(missing_ok=True)
                                            except Exception:
                                                pass
    
                            result["downloads"]["task2"]["dem_epsg"] = epsg
                            result["downloads"]["task2"]["dtm_dir"] = str(dtm_dir)
                            result["downloads"]["task2"]["dsm_dir"] = str(dsm_dir)
    
                    pc_cfg = (exports_cfg.get("pointcloud") or {})
                    pc_enabled = bool(pc_cfg.get("enabled", True))
    
                    if pc_enabled:
                        laz_candidates = list(
                            pc_cfg.get("asset_candidates") or ["georeferenced_model.laz"]
                        )
    
                        pc_out, pc_workspace, pc_published = self._export_pointcloud_to_workspace(
                            processor=processor,
                            project_id=project_id,
                            task_id=current_task2_id,
                            task_key="task2",
                            published_dir=task2_3d_dir,
                            laz_archive_name=f"{task2_export_id}.laz",
                            pcd_name=f"{task2_export_id}.pcd",
                            candidates=laz_candidates,
                            max_points=int(pc_cfg.get("max_points", 3_000_000)),
                            viewpoint=str(pc_cfg.get("viewpoint", "0 0 0 1 0 0 0")),
                        )
    
                        result["downloads"]["task2"]["pointcloud_laz"] = pc_out.get("laz")
                        result["downloads"]["task2"]["pointcloud_ply"] = pc_out.get("ply")
                        result["downloads"]["task2"]["pointcloud_pcd"] = pc_out.get("pcd")
                        result["downloads"]["task2"]["pointcloud_asset_type"] = pc_out.get("asset_type")
                        if pc_workspace:
                            result.setdefault("workspace", {}).setdefault("webodm_3d", {})["task2"] = pc_workspace
                        if pc_published:
                            result.setdefault("published", {}).setdefault("webodm_3d", {})["task2"] = pc_published
    
                        if not pc_out.get("laz") and bool(pc_cfg.get("required", True)):
                            raise RuntimeError("POINTCLOUD_DOWNLOAD_FAILED")
    
                    else:
                        logger.info("Point cloud download skipped (pointcloud.enabled=false).")
                        result["downloads"]["task2"]["pointcloud_skipped"] = True
    
                    if exports_cfg.get("all_assets_zip", {}).get("enabled", False):
                        zcfg = exports_cfg["all_assets_zip"]
    
                        task2_zip_override = self.export_name_overrides.get("task2")
                        if task2_zip_override:
                            fname = f"{task2_zip_override}-all.zip"
                        else:
                            fname = zcfg.get(
                                "filename_template",
                                "{survey_id}-RGB-{flag}-all.zip"
                            ).format(
                                survey_id=survey_id,
                                flag=task2_flag,
                            )
    
                        workspace_zip, published_zip = self._download_all_assets_zip_to_workspace(
                            processor=processor,
                            project_id=project_id,
                            task_id=current_task2_id,
                            task_key="task2",
                            published_dir=task2_odm_dir,
                            filename=fname,
                        )
                        if published_zip:
                            result["downloads"]["task2"]["all_assets_zip"] = str(published_zip)
                            result.setdefault("workspace", {}).setdefault("webodm_odm", {})["task2_all_assets_zip"] = str(workspace_zip)
                            result.setdefault("published", {}).setdefault("webodm_odm", {})["task2_all_assets_zip"] = str(published_zip)
                        else:
                            logger.warning(
                                "All-assets zip was not downloaded (endpoint missing or failed)."
                            )

                if (
                    not t2_success
                    and result.get("task4", {}).get("success") is True
                ):
                    logger.warning(
                        "Task 4 succeeded but Task 2 failed. Marking run as partially_completed."
                    )
                    result["run_status"] = "partially_completed"
                    self.state["run_status_override"] = "partially_completed"

            self._clear_webodm_checkpoint()
            return result

        finally:
            # Do not clean the upload cache here.
            # Quality Gate may trigger a fallback WebODM task, which should reuse
            # the same staged upload folder instead of re-copying/re-uploading images.
            pass

    def _stage_webodm_upload_images(
        self,
        *,
        source_folder: Path,
        survey_id: str,
        stage_name: str,
    ) -> Path:
        source_folder = Path(source_folder)
        logger = self.loggers["webodm"]

        if not source_folder.exists():
            raise FileNotFoundError(f"Image source folder not found: {source_folder}")

        image_files = []
        for pattern in ("*.jpg", "*.jpeg", "*.JPG", "*.JPEG"):
            image_files.extend(source_folder.glob(pattern))

        # Deduplicate by filename, matching WebODM upload behavior.
        unique_images: dict[str, Path] = {}
        for image in image_files:
            if image.is_file():
                unique_images.setdefault(image.name.lower(), image)

        image_files = sorted(unique_images.values(), key=lambda p: p.name.lower())

        if not image_files:
            raise RuntimeError(f"No JPG/JPEG images found in {source_folder}")

        cache_root_env = os.getenv("WEBODM_UPLOAD_CACHE_ROOT")
        cache_root = (
            Path(cache_root_env)
            if cache_root_env
            else self.base_dir / "data" / "upload_cache"
        )

        target_folder = cache_root / survey_id / stage_name

        logger.info(f"Staging WebODM upload images to local cache: {target_folder}")

        if target_folder.exists():
            shutil.rmtree(target_folder)

        target_folder.mkdir(parents=True, exist_ok=True)

        total_bytes = 0

        for index, source_image in enumerate(image_files, start=1):
            target_image = target_folder / source_image.name

            try:
                shutil.copy2(source_image, target_image)
                total_bytes += target_image.stat().st_size
            except Exception as e:
                raise RuntimeError(
                    f"Failed to stage image for WebODM upload: {source_image}"
                ) from e

            if index % 50 == 0 or index == len(image_files):
                logger.info(
                    f"Staged {index}/{len(image_files)} images "
                    f"({total_bytes / (1024 ** 3):.2f} GB)"
                )

        logger.info(
            f"WebODM upload staging complete: {len(image_files)} images "
            f"| {total_bytes / (1024 ** 3):.2f} GB"
        )

        return target_folder

    def stage_qgis(self, *, resume: bool = False) -> Dict[str, Any]:
        logger = self.loggers["qgis"]
        logger.info("Stage: QGIS Processing (selected orthomosaic clip + tiles)")
        create_run_workspace(self.workspace_layout)

        rgb_path = self._require_rgb_path()

        qgis_cfg = self.config.get("qgis") or {}
        if not bool(qgis_cfg.get("enabled", True)):
            logger.info("QGIS stage disabled (qgis.enabled=false).")
            return {"skipped": True}

        ds = self.state.get("data_segregation") or {}
        dirs = ds.get("dirs") or {}
        if not dirs:
            raise RuntimeError("Missing data_segregation.dirs in state.")

        def dir_from_key(key: str, fallback: Optional[Path] = None) -> Path:
            p = dirs.get(key)
            if p:
                return Path(p)
            if fallback is not None:
                return Path(fallback)
            raise KeyError(f"Missing dir key in data_segregation.dirs: {key}")

        def optional_dir_from_keys(
            keys: list[str],
            *,
            fallback: Path,
        ) -> Path:
            for key in keys:
                value = dirs.get(key)
                if value:
                    return Path(value)

            return Path(fallback)

        boundary_geojson_path = self.state.get("boundary_geojson_path")
        boundary_geojson: Path | None = (
            Path(str(boundary_geojson_path))
            if boundary_geojson_path
            else None
        )

        mask_geojson: Path | None = None

        published_clipped_ortho_dir = dir_from_key(
            "qgis_clipped_ortho",
            fallback=(rgb_path / "qgis" / "clipped" / "ortho"),
        )

        published_tiles_round_dir = dir_from_key(
            "tiles_ortho_round",
            fallback=(rgb_path / "tiles" / "ortho" / "round-corners"),
        )

        published_tiles_soft_dir = optional_dir_from_keys(
            ["tiles_ortho_soft"],
            fallback=(rgb_path / "tiles" / "ortho" / "soft-corners"),
        )
        workspace_clipped_ortho_dir = self.workspace_layout.qgis_clipped_ortho
        workspace_tiles_round_dir = self.workspace_layout.qgis_tiles_round
        workspace_tiles_soft_dir = self.workspace_layout.qgis_tiles_soft

        clip_cfg = qgis_cfg.get("clip") or {}
        clip_enabled = bool(clip_cfg.get("enabled", True))

        qgis_tools_cfg = qgis_cfg.get("tools") or {}
        qgis_root = str(qgis_tools_cfg.get("qgis_root") or "")
        gdalwarp_path = str(qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp")
        gdal2tiles_path = str(qgis_tools_cfg.get("gdal2tiles_path") or "gdal2tiles.py")
        gdalinfo_path = str(qgis_tools_cfg.get("gdalinfo_path") or "gdalinfo")

        tiles_cfg = qgis_cfg.get("tiles") or {}
        tiles_enabled = bool(tiles_cfg.get("enabled", True))
        zoom = str(tiles_cfg.get("zoom") or "11-24")
        profile = str(tiles_cfg.get("profile") or "mercator")
        webviewer = str(tiles_cfg.get("webviewer") or "none")
        copyright_text = str(tiles_cfg.get("copyright") or "ASIMOV-HAWKS")

        dst_nodata_raw = clip_cfg.get("dst_nodata") or ""
        dst_nodata = None

        if isinstance(dst_nodata_raw, (int, float)):
            dst_nodata = float(dst_nodata_raw)
        elif isinstance(dst_nodata_raw, str) and dst_nodata_raw.strip() != "":
            try:
                dst_nodata = float(dst_nodata_raw.strip())
            except ValueError:
                raise ValueError(
                    f"Invalid QGIS_CLIP_DST_NODATA value: '{dst_nodata_raw}' "
                    "(must be a number or empty)"
                )

        tools = QGISTools(
            logger=logger,
            qgis_root=qgis_root,
            gdalwarp_path=gdalwarp_path,
            gdal2tiles_path=gdal2tiles_path,
            gdalinfo_path=gdalinfo_path,
        )

        local_staging_cfg = qgis_cfg.get("local_staging") or {}
        local_staging_enabled = bool(local_staging_cfg.get("enabled", True))

        clip_staging_dir: Optional[Path] = None
        local_staging_root: Optional[Path] = None

        if local_staging_enabled:
            _explicit_dir = (local_staging_cfg.get("dir") or "").strip()
            if _explicit_dir:
                _staging_base = Path(_explicit_dir)
            else:
                _upload_cache_root = (
                    (self.config.get("paths") or {}).get("upload_cache_root")
                )
                if _upload_cache_root:
                    _staging_base = Path(_upload_cache_root) / "qgis-staging"
                else:
                    _staging_base = Path(tempfile.gettempdir()) / "ah-qgis-staging"
                    logger.warning(
                        f"UPLOAD_CACHE_ROOT not set - QGIS staging will use "
                        f"temp dir: {_staging_base}. Set UPLOAD_CACHE_ROOT in "
                        f".env to use your E:\\cache folder instead."
                    )

            clip_staging_dir = _staging_base / "clip" / self.run_id
            local_staging_root = _staging_base / "tiles" / self.run_id
            logger.info(f"QGIS local staging root: {_staging_base}")

        def _successful_qgis_task_keys() -> list[str]:
            web = self.state.get("webodm") or {}
            task_keys: list[str] = []
            for candidate in ("task4", "task2"):
                task_state = web.get(candidate) or {}
                if not task_state or not bool(task_state.get("success")):
                    continue
                task_flag = str(task_state.get("flag") or "").strip()
                if not task_flag:
                    continue
                try:
                    self._select_existing_task_orthomosaic(
                        task_key=candidate,
                        flag=task_flag,
                        boundary_used=True,
                        require_exists=True,
                    )
                except RuntimeError:
                    continue
                task_keys.append(candidate)
            return task_keys

        def _run_qgis_operation(
            selected_input: Dict[str, Any],
            *,
            publish_legacy_alias: bool,
            use_task_scoped_tiles: bool,
        ) -> Dict[str, Any]:
            selected = dict(selected_input)
            task_key = str(selected.get("task_key") or "").strip()
            flag = str(selected.get("flag") or "").strip()
            source_path = Path(str(selected.get("source_path") or ""))
            boundary_used = bool(selected.get("boundary_used"))
            tile_mode = "round-corners" if boundary_used else "soft-corners"

            if not task_key:
                raise RuntimeError("Selected orthomosaic is missing task_key.")
            if not flag:
                raise RuntimeError("Selected orthomosaic is missing flag.")
            if not source_path.exists():
                raise FileNotFoundError(
                    f"Selected orthomosaic does not exist: {source_path}"
                )

            logger.info(
                "QGIS selected orthomosaic: "
                f"task={task_key} | file={source_path.name} | "
                f"boundary_used={boundary_used} | tile_mode={tile_mode}"
            )

            current_mask_geojson: Path | None = None
            if boundary_used:
                if boundary_geojson is None or not boundary_geojson.exists():
                    raise RuntimeError(
                        "Selected orthomosaic is marked as bounded, but boundary_geojson_path "
                        f"is missing or invalid: {boundary_geojson_path}"
                    )
                current_mask_geojson = boundary_geojson
            else:
                if not boundary_geojson or not boundary_geojson.exists():
                    logger.warning(
                        "No boundary GeoJSON available. QGIS will skip clipping and generate soft-corners tiles."
                    )
                else:
                    logger.info(
                        "Boundary GeoJSON exists, but selected orthomosaic is marked as unbounded. "
                        "QGIS will use soft-corners tile workflow."
                    )

            clipped_path: Path
            workspace_clipped_path: Path | None = None
            published_clipped_path: Path | None = None

            if boundary_used and clip_enabled:
                clipped_filename = self._clipped_orthomosaic_filename(
                    task_key=task_key,
                    flag=flag,
                )
                workspace_clipped_path = workspace_clipped_ortho_dir / clipped_filename
                published_clipped_path = published_clipped_ortho_dir / clipped_filename

                logger.info(
                    f"Clipping selected orthomosaic ({task_key}) -> {workspace_clipped_path.name}"
                )

                if current_mask_geojson is None:
                    raise RuntimeError("QGIS clipping requires a valid boundary GeoJSON mask.")

                skip_clip = False
                if resume and workspace_clipped_path.exists():
                    logger.info(
                        f"Resume: clipped orthomosaic already exists "
                        f"({workspace_clipped_path.name}); verifying before reuse..."
                    )
                    try:
                        tools.verify_raster_readable(
                            workspace_clipped_path,
                            retries=1,
                            delay_s=2.0,
                        )
                        skip_clip = True
                        logger.info(
                            f"Existing clip verified OK, skipping re-clip: {workspace_clipped_path.name}"
                        )
                    except RuntimeError as e:
                        logger.warning(
                            f"Existing clipped file failed verification, will re-clip: {e}"
                        )

                if not skip_clip:
                    tools.clip_raster_by_mask(
                        input_tif=source_path,
                        mask_geojson=current_mask_geojson,
                        output_tif=workspace_clipped_path,
                        dst_nodata=dst_nodata,
                        local_staging_dir=clip_staging_dir,
                    )

                self._replace_legacy_file_after_success(
                    source_file=workspace_clipped_path,
                    target_file=published_clipped_path,
                )
                clipped_path = published_clipped_path
            elif boundary_used and not clip_enabled:
                logger.warning(
                    "QGIS clip disabled (qgis.clip.enabled=false). "
                    "Using selected bounded orthomosaic directly for tile generation."
                )
                clipped_path = source_path
            else:
                logger.info(
                    "Selected orthomosaic is unbounded. "
                    "Skipping clip and using source orthomosaic for soft-corners tiles."
                )
                clipped_path = source_path

            selected["clipped_path"] = str(clipped_path)
            selected["clipped_filename"] = clipped_path.name
            selected["tile_mode"] = tile_mode

            workspace_tiles_root = (
                workspace_tiles_round_dir if boundary_used else workspace_tiles_soft_dir
            )
            published_tiles_root = (
                published_tiles_round_dir if boundary_used else published_tiles_soft_dir
            )
            workspace_tiles_dir = (
                workspace_tiles_root / task_key if use_task_scoped_tiles else workspace_tiles_root
            )
            published_tiles_dir = (
                published_tiles_root / task_key if use_task_scoped_tiles else published_tiles_root
            )
            legacy_tiles_dir = published_tiles_root

            _staging_root: Optional[Path] = None
            if tiles_enabled:
                tile_resume = bool(resume)
                tile_clean = not tile_resume

                tiling_input_path = workspace_clipped_path or clipped_path
                tiling_output_dir = workspace_tiles_dir
                _local_tile_staging_active = False

                if local_staging_root is not None:
                    _staging_root = local_staging_root / task_key
                    try:
                        _local_ortho_staging = _staging_root / "ortho"
                        staging_source = workspace_clipped_path or clipped_path
                        self._check_write_capacity(
                            role="qgis_local_staging",
                            path=_staging_root,
                            required_bytes=int(staging_source.stat().st_size * 2.0),
                        )
                        tiling_input_path = tools.stage_local_copy(
                            staging_source,
                            _local_ortho_staging,
                        )
                        tiling_output_dir = _staging_root / "tiles" / tile_mode
                        _local_tile_staging_active = True
                        logger.info(
                            f"Local staging enabled: tiling will read/write on "
                            f"local disk ({tiling_output_dir}) and copy results "
                            f"to {workspace_tiles_dir} afterward."
                        )
                    except StorageCapacityError:
                        raise
                    except Exception as e:
                        logger.warning(
                            f"Local staging setup failed ({e}); falling back to "
                            f"tiling directly against {workspace_clipped_path or clipped_path}."
                        )
                        tiling_input_path = workspace_clipped_path or clipped_path
                        tiling_output_dir = workspace_tiles_dir
                        _local_tile_staging_active = False

                logger.info(
                    f"Generating tiles ({tile_mode}) from "
                    f"{tiling_input_path.name} -> {tiling_output_dir}"
                )

                tools.generate_tiles(
                    input_tif=tiling_input_path,
                    output_dir=tiling_output_dir,
                    zoom=zoom,
                    profile=profile,
                    webviewer=webviewer,
                    copyright_text=copyright_text,
                    clean=tile_clean,
                    resume=tile_resume,
                )

                if _local_tile_staging_active:
                    logger.info(
                        f"Copying tiles from local staging to run workspace: "
                        f"{tiling_output_dir} -> {workspace_tiles_dir}"
                    )
                    t0 = time.perf_counter()
                    self._check_write_capacity(
                        role="qgis_tile_copy_back",
                        path=workspace_tiles_dir,
                        required_bytes=int(
                            self._directory_size(tiling_output_dir) * 1.1
                        ),
                    )
                    if tile_resume:
                        workspace_tiles_dir.mkdir(parents=True, exist_ok=True)
                    else:
                        self._reset_workspace_directory(workspace_tiles_dir)
                    shutil.copytree(
                        tiling_output_dir,
                        workspace_tiles_dir,
                        dirs_exist_ok=True,
                    )
                    logger.info(
                        f"Tile copy-back complete in {time.perf_counter() - t0:.1f}s"
                    )

                    for _stale_dir, _label in [
                        (clip_staging_dir, "clip staging"),
                        (_staging_root / "ortho" if _staging_root else None, "ortho staging"),
                        (tiling_output_dir, "tile staging"),
                    ]:
                        if _stale_dir is not None and _stale_dir.exists():
                            try:
                                shutil.rmtree(_stale_dir)
                                logger.info(f"Cleaned up {_label}: {_stale_dir}")
                            except Exception as _e:
                                logger.warning(
                                    f"Could not clean up {_label} ({_stale_dir}): {_e}"
                                )

                self._replace_legacy_directory_after_success(
                    source_dir=workspace_tiles_dir,
                    target_dir=published_tiles_dir,
                )
                if publish_legacy_alias and published_tiles_dir != legacy_tiles_dir:
                    self._replace_legacy_directory_after_success(
                        source_dir=workspace_tiles_dir,
                        target_dir=legacy_tiles_dir,
                    )
                selected["tiles_dir"] = str(
                    legacy_tiles_dir if publish_legacy_alias else published_tiles_dir
                )
            else:
                logger.warning(
                    "QGIS tiles disabled (qgis.tiles.enabled=false). Skipping tile generation."
                )
                selected["tiles_dir"] = None

            return {
                "boundary_geojson": str(boundary_geojson) if boundary_geojson else None,
                "selected_webodm_task": task_key,
                "selected_orthomosaic": selected,
                "tools": {
                    "gdalwarp_path": gdalwarp_path,
                    "gdal2tiles_path": gdal2tiles_path,
                },
                "clip": {
                    "enabled": clip_enabled,
                    "dst_nodata": dst_nodata,
                    "input": str(source_path),
                    "output": str(clipped_path),
                },
                "tiles": {
                    "enabled": tiles_enabled,
                    "mode": tile_mode,
                    "output_dir": (
                        str(legacy_tiles_dir if publish_legacy_alias else published_tiles_dir)
                        if tiles_enabled
                        else None
                    ),
                    "zoom": zoom,
                    "profile": profile,
                    "webviewer": webviewer,
                    "copyright": copyright_text,
                },
                "workspace": {
                    "qgis_clipped_ortho": (
                        str(workspace_clipped_path) if workspace_clipped_path else None
                    ),
                    "tiles_dir": str(workspace_tiles_dir) if tiles_enabled else None,
                },
                "published": {
                    "qgis_clipped_ortho": (
                        str(published_clipped_path) if published_clipped_path else None
                    ),
                    "tiles_dir": (
                        str(legacy_tiles_dir if publish_legacy_alias else published_tiles_dir)
                        if tiles_enabled
                        else None
                    ),
                },
                "published_operation": {
                    "qgis_clipped_ortho": (
                        str(published_clipped_path) if published_clipped_path else None
                    ),
                    "tiles_dir": str(published_tiles_dir) if tiles_enabled else None,
                },
            }

        selected = self._get_selected_orthomosaic()
        successful_task_keys = _successful_qgis_task_keys()
        selected_task_key = str(selected.get("task_key") or "").strip()
        multi_operation_mode = (
            len(successful_task_keys) > 1 and selected_task_key in successful_task_keys
        )

        if not multi_operation_mode:
            result = _run_qgis_operation(
                selected,
                publish_legacy_alias=True,
                use_task_scoped_tiles=False,
            )
            self.state["selected_webodm_task"] = result["selected_webodm_task"]
            self.state["selected_orthomosaic"] = result["selected_orthomosaic"]
            return result

        web = self.state.get("webodm") or {}
        operations: Dict[str, Any] = {}
        final_result: Dict[str, Any] | None = None

        for index, task_key in enumerate(successful_task_keys):
            task_state = web.get(task_key) or {}
            task_flag = str(task_state.get("flag") or "").strip()
            selected_for_task = self._select_existing_task_orthomosaic(
                task_key=task_key,
                flag=task_flag,
                boundary_used=True,
                require_exists=True,
            )
            op_result = _run_qgis_operation(
                selected_for_task,
                publish_legacy_alias=False,
                use_task_scoped_tiles=True,
            )
            operations[task_key] = op_result
            final_result = op_result

        if final_result is None:
            raise RuntimeError(
                "QGIS could not identify a successful WebODM operation to process."
            )

        final_result = dict(final_result)
        final_result["operations"] = operations
        final_result["workspace"] = dict(final_result.get("workspace") or {})
        final_result["published"] = dict(final_result.get("published") or {})
        final_result["workspace"]["operations"] = {
            key: value.get("workspace") for key, value in operations.items()
        }
        final_result["published"]["operations"] = {
            key: value.get("published_operation") for key, value in operations.items()
        }
        self.state["selected_webodm_task"] = final_result["selected_webodm_task"]
        self.state["selected_orthomosaic"] = final_result["selected_orthomosaic"]
        return final_result

    def run_webodm_fallback_task(
        self,
        *,
        task_key: str = "task4",
        fallback_reason: str = "quality_gate_requested_fallback",
    ) -> Dict[str, Any]:
        logger = self.loggers["webodm"]
        task_key = self._normalize_webodm_task_key(task_key)
        task_label = self._webodm_task_label(task_key)

        logger.info(f"Running WebODM fallback task: {task_key}")

        survey_id = self._require_survey_id()
        rgb_path = self._require_rgb_path()

        # Use setdefault to ensure we're modifying the state dict, not a temporary copy
        web = self.state.setdefault("webodm", {})
        project_id = web.get("project_id")
        project_name = web.get("project_name") or survey_id

        if not project_id:
            raise RuntimeError(f"{task_key} fallback requires existing WebODM project_id.")

        boundary_geojson_path = self.state.get("boundary_geojson_path")
        if not boundary_geojson_path or not Path(boundary_geojson_path).exists():
            raise RuntimeError(f"{task_key} fallback requires boundary GeoJSON.")

        ds = self.state.get("data_segregation") or {}
        dirs = ds.get("dirs") or {}
        image_folder = Path(dirs.get("path") or (rgb_path / "images" / "path"))

        boundary_geojson = Path(boundary_geojson_path).read_text(encoding="utf-8")

        webodm_cfg = self.config["webodm"]
        exports_cfg = self.config.get("exports", {})
        qgis_tools_cfg = (exports_cfg.get("tools") or {})

        task_options = self._webodm_task_options(task_key)
        task_options["boundary"] = boundary_geojson

        task_flag = self._webodm_task_flag(
            task_key,
            default_boundary_mode="b",
        )

        task_name = self.task_name_overrides.get(task_key)
        if not task_name:
            task_name = self._webodm_task_name(
                survey_id=survey_id,
                flag=task_flag,
                task_key=task_key,
            )

        task_ortho_dir = rgb_path / "ortho"
        task_ortho_dir.mkdir(parents=True, exist_ok=True)
        create_run_workspace(self.workspace_layout)

        processor = self._create_webodm_processor(logger)

        binding = self.repo.get_webodm_binding(self.run_id, task_key)
        existing_task_id = None
        if binding:
            bound_project_id = binding.get('project_id')
            bound_task_name = binding.get('task_name')
            if int(binding['project_id']) != int(project_id):
                raise StageRequiresRecovery(
                    f'Canonical {task_key} project {bound_project_id} '
                    f'conflicts with state project {project_id}',
                    output={
                        'status': 'requires_recovery',
                        'operation_key': task_key,
                        'project_id': binding['project_id'],
                        'task_id': binding.get('task_id'),
                    },
                )
            if binding.get('task_name') and binding['task_name'] != task_name:
                raise StageRequiresRecovery(
                    f'Canonical {task_key} name {bound_task_name!r} '
                    f'does not match expected name {task_name!r}',
                    output={
                        'status': 'requires_recovery',
                        'operation_key': task_key,
                        'project_id': project_id,
                        'task_id': binding.get('task_id'),
                    },
                )
            if binding.get('task_id'):
                existing_task_id = str(binding['task_id'])

        if (
            not existing_task_id
            and self._resume_had_webodm_history
            and int(project_id) not in self._webodm_projects_created_this_process
        ):
            raise StageRequiresRecovery(
                f'Resumed {task_key} has project {project_id} but no durable '
                'task UUID. Repair the binding before resuming; refusing to '
                'create a duplicate task.',
                output={
                    'status': 'requires_recovery',
                    'operation_key': task_key,
                    'project_id': project_id,
                },
            )

        if existing_task_id:
            logger.info(f"Found existing {task_key}: {existing_task_id}")
            current_task_id = str(existing_task_id)
            try:
                task_info = processor.get_task(
                    int(project_id),
                    current_task_id,
                )
            except WebODMTaskNotFound as exc:
                raise StageRequiresRecovery(
                    f'Canonical WebODM task is missing: project_id={project_id} '
                    f'task_id={current_task_id}',
                    output={
                        'status': 'requires_recovery',
                        'operation_key': task_key,
                        'project_id': project_id,
                        'task_id': current_task_id,
                    },
                ) from exc
            status, _terminal = WebODMProcessor._normalize_status(
                task_info.get('status')
            )
            self._persist_webodm_binding(
                task_key=task_key,
                project_id=int(project_id),
                task_id=current_task_id,
                task_name=task_name,
                remote_status=status,
                raw_status=task_info.get('status'),
                local_status='reattached',
            )

            if status == 'failed':
                raise RuntimeError(
                    f'WEBODM_TASK_FAILED: task {current_task_id} is failed; '
                    'the pipeline will not create a replacement task.'
                )
            if status == 'canceled':
                raise RuntimeError('WEBODM_TASK_CANCELED')

            if status != "completed":
                success, runtime, _info = processor.wait_for_completion(
                    int(project_id),
                    current_task_id,
                    live=False,
                    control_check=lambda: self._check_control_or_raise("webodm"),
                )
            else:
                success = True
                runtime = 0.0
                _info = task_info

        else:
            upload_image_folder = self._get_reusable_webodm_upload_folder(
                default_source_dir=image_folder,
                logger=logger,
            )

            # If no reusable cache was found, actively stage to local disk before
            # uploading — streaming a multi-GB multipart upload directly off Z:\
            # is prone to SMB hiccups killing the request mid-transfer.
            if upload_image_folder == image_folder:
                upload_cache_root_cfg = (self.config.get("paths") or {}).get("upload_cache_root")
                if not upload_cache_root_cfg:
                    upload_cache_root_cfg = os.getenv("UPLOAD_CACHE_ROOT")
                local_root = Path(upload_cache_root_cfg) if upload_cache_root_cfg else Path(
                    os.getenv("TEMP", r"C:\temp")
                )
                cache_root = local_root / "automation-pipeline" / "upload_cache" / f"{self.run_id}_{task_key}"

                try:
                    cached_dir, _ = self._stage_upload_cache(
                        src_dir=image_folder,
                        cache_root=cache_root,
                        logger=logger,
                        progress_every=25,
                        require_free_multiplier=1.2,
                    )
                    upload_image_folder = cached_dir
                    self._remember_webodm_upload_folder(
                        upload_folder=upload_image_folder,
                        cached_dir=cached_dir,
                        source_folder=image_folder,
                        fallback_direct=False,
                    )
                    logger.info(f"Staged {task_key} upload to local disk: {upload_image_folder}")
                except StorageCapacityError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"Local staging failed for {task_key} upload, falling back to "
                        f"direct upload from source. reason={e}"
                    )
                    upload_image_folder = image_folder

            current_task_id = processor.create_task_with_images(
                project_id=int(project_id),
                name=task_name,
                image_folder=str(upload_image_folder),
                options=task_options,
                processing_node=webodm_cfg.get("node_id"),
            )

            self._persist_webodm_binding(
                task_key=task_key,
                project_id=int(project_id),
                task_id=str(current_task_id),
                task_name=task_name,
                remote_status='queued',
                local_status='task_created',
            )

            success, runtime, _info = processor.wait_for_completion(
                int(project_id),
                current_task_id,
                live=False,
                control_check=lambda: self._check_control_or_raise("webodm"),
            )

        final_raw_status = (_info or {}).get('status')
        final_remote_status, _terminal = WebODMProcessor._normalize_status(
            final_raw_status
        )
        self._persist_webodm_binding(
            task_key=task_key,
            project_id=int(project_id),
            task_id=str(current_task_id),
            task_name=task_name,
            remote_status=final_remote_status,
            raw_status=final_raw_status,
            local_status='processing_completed' if success else 'remote_failed',
            success=bool(success),
            runtime_seconds=float(runtime),
        )
        task_state = {
            "id": str(current_task_id),
            "name": task_name,
            "success": bool(success),
            "runtime_seconds": float(runtime),
            "flag": task_flag,
            "task_label": task_label,
        }

        web[task_key] = task_state

        downloads = web.setdefault("downloads", {})
        task_downloads = downloads.setdefault(task_key, {})

        selected = None

        if not success:
            self.state['webodm'] = web
            return {
                task_key: task_state,
                'downloads': task_downloads,
                'project_id': project_id,
                'project_name': project_name,
                'selected_webodm_task': None,
                'selected_orthomosaic': None,
            }

        if exports_cfg.get("enabled", False) and exports_cfg.get("ortho", {}).get("enabled", False):
            ortho_cfg = exports_cfg["ortho"]
            epsg = int(ortho_cfg.get("reproject_epsg", 4326))
            candidates = ortho_cfg.get("asset_candidates") or ["orthophoto.tif"]

            filename = self._orthomosaic_filename(
                task_key=task_key,
                flag=task_flag,
            )

            try:
                workspace_path, published_path = self._export_orthomosaic_to_workspace(
                    processor=processor,
                    project_id=int(project_id),
                    task_id=str(current_task_id),
                    task_key=task_key,
                    published_dir=task_ortho_dir,
                    filename=filename,
                    epsg=epsg,
                    candidates=candidates,
                    gdalwarp_path=(
                        qgis_tools_cfg.get("gdalwarp_path") or "gdalwarp"
                    ),
                )
            except Exception:
                self._persist_webodm_binding(
                    task_key=task_key,
                    project_id=int(project_id),
                    task_id=str(current_task_id),
                    task_name=task_name,
                    remote_status=final_remote_status,
                    raw_status=final_raw_status,
                    local_status='artifact_failed',
                    success=False,
                    runtime_seconds=float(runtime),
                )
                raise

            artifact_paths = [
                Path(value)
                for value in (workspace_path, published_path)
                if value is not None
            ]
            artifact_valid = (
                len(artifact_paths) == 2
                and all(
                    path.is_file() and path.stat().st_size > 0
                    for path in artifact_paths
                )
            )
            if not artifact_valid:
                self._persist_webodm_binding(
                    task_key=task_key,
                    project_id=int(project_id),
                    task_id=str(current_task_id),
                    task_name=task_name,
                    remote_status=final_remote_status,
                    raw_status=final_raw_status,
                    local_status='artifact_failed',
                    success=False,
                    runtime_seconds=float(runtime),
                )
                raise RuntimeError(
                    f'ORTHOMOSAIC_EXPORT_FAILED: {task_key} completed remotely '
                    'but no non-empty orthomosaic was delivered.'
                )

            if published_path:
                task_downloads["orthomosaic"] = str(published_path)
                task_downloads["epsg"] = epsg
                web.setdefault("workspace", {}).setdefault("webodm_ortho", {})[task_key] = str(workspace_path)
                web.setdefault("published", {}).setdefault("webodm_ortho", {})[task_key] = str(published_path)

                self.state["webodm"] = web

                selected = self._set_selected_orthomosaic(
                    task_key=task_key,
                    source_path=published_path,
                    flag=task_flag,
                    boundary_used=True,
                    fallback_used=True,
                    fallback_reason=fallback_reason,
                )

                web["selected_webodm_task"] = task_key
                web["selected_orthomosaic"] = selected
                self._persist_webodm_binding(
                    task_key=task_key,
                    project_id=int(project_id),
                    task_id=str(current_task_id),
                    task_name=task_name,
                    remote_status=final_remote_status,
                    raw_status=final_raw_status,
                    local_status='artifact_ready',
                    success=True,
                    runtime_seconds=float(runtime),
                )
            else:
                logger.warning(f"Could not download {task_key} orthomosaic.")

        self.state["webodm"] = web

        return {
            task_key: task_state,
            "downloads": task_downloads,
            "project_id": project_id,
            "project_name": project_name,
            "selected_webodm_task": task_key,
            "selected_orthomosaic": selected,
        }


    def run_task4_fallback(self) -> Dict[str, Any]:
        fallback_task = self._webodm_fallback_task_key()

        return self.run_webodm_fallback_task(
            task_key=fallback_task,
            fallback_reason=f"quality_gate_requested_{fallback_task}_fallback",
        )
        

    def stage_quality_gate(self) -> Dict[str, Any]:
        logger = self.loggers["pipeline"]
        set_stage_context(logger, stage_name="quality_gate")
        logger.info("Stage: Quality Gate Check")

        survey_id = self.survey_id or "?"
        max_restarts = 3
        restarts = 0

        web = self.state.get("webodm") or {}
        project_id = web.get("project_id")

        if not project_id:
            raise RuntimeError(
                "Quality gate cannot run: missing webodm.project_id in pipeline state."
            )

        task1 = web.get("task1") or {}
        task2 = web.get("task2") or {}
        task4 = web.get("task4") or {}

        if not task1.get("id") and not task2.get("id") and not task4.get("id"):
            logger.error(
                "Quality gate cannot run: missing WebODM task IDs. "
                "Please check if WebODM Docker is running and if the node worker is online."
            )
            return {
                "passed": False,
                "reason": "missing_webodm_task_ids",
                "project_id": project_id,
            }

        allowed_stages = WEBODM_RESTART_STAGES

        def restart_and_wait(
            task_id_to_restart: str,
            task_name_to_restart: str,
            restart_from: str,
        ) -> Dict[str, Any]:
            nonlocal restarts, web, task1, task2

            restarts += 1
            if restarts > max_restarts:
                logger.error("Maximum WebODM restart attempts exceeded.")
                return {
                    "passed": False,
                    "restarts": restarts,
                    "reason": "max_restarts_exceeded",
                }

            processor = self._create_webodm_processor(
                self.loggers["webodm"]
            )

            logger.warning(
                f"Requesting WebODM internal restart | project_id={project_id} "
                f"task_id={task_id_to_restart} restart_from={restart_from} "
                f"| attempt {restarts}/{max_restarts}"
            )

            processor.restart_task(
                project_id=int(project_id),
                task_id=str(task_id_to_restart),
                restart_from=restart_from,
            )
            success, runtime, task_info = processor.wait_for_completion(
                int(project_id), str(task_id_to_restart), control_check=lambda: self._check_control_or_raise("webodm"),
            )

            updated_task_state = {
                "id": str(task_id_to_restart),
                "name": task_name_to_restart,
                "success": bool(success),
                "runtime_seconds": float(runtime),
                "restart_from": restart_from,
                "restart_attempt": restarts,
                "status": task_info.get("status"),
            }

            if task4.get("id") and str(task4.get("id")) == str(task_id_to_restart):
                task4.update(updated_task_state)
                web["task4"] = task4
            elif task2.get("id") and str(task2.get("id")) == str(task_id_to_restart):
                task2.update(updated_task_state)
                web["task2"] = task2
            elif task1.get("id") and str(task1.get("id")) == str(task_id_to_restart):
                task1.update(updated_task_state)
                web["task1"] = task1

            self.state["webodm"] = web
            return {"passed": None, "restarts": restarts, "task": updated_task_state}

        # ── Interactive loop ─────────────────────────────────────────
        _fallback_reviewed = False
        _fallback_task_state: dict = {}
        fallback_task: str = self._webodm_fallback_task_key()

        while True:
            web = self.state.get("webodm") or {}
            task1 = web.get("task1") or {}
            task2 = web.get("task2") or {}
            task4 = web.get("task4") or {}

            raw = quality_gate_prompt(
                logger=logger,
                survey_id=survey_id,
                project_id=int(project_id),
                task1=task1,
                task2=task2 if task2.get("id") else {},
                webodm_url=self.config.get("webodm", {}).get("url", ""),
                task4=task4 if task4.get("id") else None,
                fallback_review=_fallback_reviewed,
            )

            if raw in ("yes", "y"):
                if _fallback_reviewed:
                    logger.info(
                        f"Quality gate PASSED by user — "
                        f"fallback ({fallback_task}) approved after review."
                    )
                else:
                    logger.info("Quality gate PASSED by user.")

                # Select the best available orthomosaic for QGIS
                # Priority: fallback (if reviewed) > task4 > task2
                selected = None
                try:
                    if _fallback_reviewed:
                        fallback_flag = self._webodm_task_flag(
                            fallback_task,
                            default_boundary_mode="b",
                        )
                        selected = self._select_existing_task_orthomosaic(
                            task_key=fallback_task,
                            flag=fallback_flag,
                            boundary_used=True,
                            fallback_used=True,
                        )
                    elif task4.get("id"):
                        task4_flag = self._webodm_task_flag(
                            "task4",
                            default_boundary_mode="b",
                        )
                        selected = self._select_existing_task_orthomosaic(
                            task_key="task4",
                            flag=task4_flag,
                            boundary_used=True,
                            fallback_used=False,
                        )
                    elif task2.get("id"):
                        task2_flag = self._webodm_task_flag(
                            "task2",
                            default_boundary_mode="b",
                        )
                        selected = self._select_existing_task_orthomosaic(
                            task_key="task2",
                            flag=task2_flag,
                            boundary_used=True,
                            fallback_used=False,
                        )
                except Exception as e:
                    logger.warning(f"Could not select orthomosaic after quality pass: {e}")
                    selected = None

                selected_path = (
                    Path(selected['source_path'])
                    if isinstance(selected, Mapping)
                    and selected.get('source_path')
                    else None
                )
                if (
                    selected_path is None
                    or not selected_path.is_file()
                    or selected_path.stat().st_size <= 0
                ):
                    logger.error(
                        'Quality gate cannot pass: no non-empty selected '
                        'orthomosaic is available.'
                    )
                    return {
                        'passed': False,
                        'restarts': restarts,
                        'project_id': project_id,
                        'reason': 'missing_selected_orthomosaic',
                        'selected_webodm_task': None,
                        'selected_orthomosaic': None,
                    }

                self._cleanup_webodm_upload_cache_from_state(self.loggers["webodm"])

                return {
                    "passed": True,
                    "restarts": restarts,
                    "project_id": project_id,
                    "fallback_task": fallback_task if _fallback_reviewed else None,
                    fallback_task: _fallback_task_state if _fallback_reviewed else None,
                    "selected_webodm_task": self.state.get("selected_webodm_task"),
                    "selected_orthomosaic": selected,
                }

            if raw in ("fail", "f"):
                logger.warning("Quality gate FAILED by user.")

                self._cleanup_webodm_upload_cache_from_state(self.loggers["webodm"])

                return {
                    "passed": False,
                    "restarts": restarts,
                    "project_id": project_id,
                }

            if raw == "restart":
                # Pick the best task to restart — task4 first, else task2
                if task4.get("id"):
                    default_task_id   = str(task4["id"])
                    default_task_name = str(task4.get("name") or "task4")
                elif task2.get("id"):
                    default_task_id   = str(task2["id"])
                    default_task_name = str(task2.get("name") or "task2")
                else:
                    default_task_id   = str(task1["id"])
                    default_task_name = str(task1.get("name") or "task1")

                res = restart_and_wait(default_task_id, default_task_name, "dataset")
                if res.get("passed") is False:
                    return res
                continue

            if raw.startswith("restart "):
                parts = raw.split()

                if len(parts) not in (2, 3):
                    logger.warning(
                        "Invalid format. Use: restart | restart t1|t2|t4 | restart t1|t2|t4 <stage>"
                    )
                    continue

                target     = parts[1]
                stage_alias = parts[2] if len(parts) == 3 else "load_dataset"

                target_map = {
                    "t1": task1, "task1": task1,
                    "t2": task2, "task2": task2,
                    "t4": task4, "task4": task4,
                }

                chosen = target_map.get(target)
                if chosen is None:
                    logger.warning("Invalid target. Use t1, t2, or t4.")
                    continue

                if not chosen or not chosen.get("id"):
                    logger.warning(f"{target} does not exist for this run.")
                    continue

                if stage_alias not in allowed_stages:
                    logger.warning(
                        f"Invalid stage '{stage_alias}'. "
                        f"Valid stages: {', '.join(sorted(WEBODM_RESTART_STAGE_NAMES))}"
                    )
                    continue

                res = restart_and_wait(
                    str(chosen["id"]),
                    str(chosen.get("name") or target),
                    allowed_stages[stage_alias],
                )
                if res.get("passed") is False:
                    return res
                continue

            logger.warning(
                "Unrecognised input. "
                "Use: yes | fail | restart | restart t1|t2|t4 [stage]"
                )
            
    # RUN
    def run(
        self,
        *,
        resume: bool = True,
        force_stages: Optional[Set[str]] = None,
        selected_stages: Optional[Set[str]] = None,
        raise_on_error: bool = False,
        publication_confirmation: Optional[str] = None,
        keep_workspace: bool = False,
    ) -> Dict[str, Any]:
        pipeline_logger = self.loggers["pipeline"]
        pipeline_header(pipeline_logger, self.run_id)
        log_event(
            pipeline_logger,
            "run_started",
            resume=resume,
            selected_stages=(
                ",".join(sorted(selected_stages))
                if selected_stages is not None
                else "all"
            ),
        )

        if not resume:
            self.control.cleanup_flags()
        else:
            self.control.clear_abort()
            self.control.clear_pause()

        self.control.start_hotkeys(pipeline_logger)

        # Resume a previously paused run
        try:
            r = self.repo.get_run(self.run_id)
            if r and r.get("status") == "paused":
                self.repo.mark_run_running(self.run_id)
                pipeline_logger.info(
                    "Resuming paused run; status set to running")
        except Exception:
            pipeline_logger.exception(
                "Failed while attempting to resume paused run")

        total_start = time.perf_counter()
        force_stages = force_stages or set()
        self._resume_had_webodm_history = bool(
            resume and self.repo.get_latest_stage(self.run_id, 'webodm')
        )
        stage_steps = [
            {
                "name": "data_segregation",
                "fn": self.stage_data_segregation,
                "output_key": "data_segregation",
                "stale_running_policy": None,
            },
            {
                "name": "cross_run_filter",
                "fn": self.stage_cross_run_image_filter,
                "output_key": "cross_run_filter",
                "stale_running_policy": None,
            },
            {
                "name": "kml_boundary",
                "fn": self.stage_kml_boundary,
                "output_key": "kml_boundary",
                "stale_running_policy": None,
            },
            {
                "name": "webodm",
                "fn": self.stage_webodm,
                "output_key": "webodm",
                "stale_running_policy": "rerun",
            },
            {
                "name": "quality_gate",
                "fn": self.stage_quality_gate,
                "output_key": "quality_gate",
                "stale_running_policy": None,
            },
            {
                "name": "qgis",
                "fn": lambda: self.stage_qgis(resume=resume),
                "output_key": "qgis",
                "stale_running_policy": "rerun",
            },

        ]
        if publication_confirmation is not None or (
            selected_stages is not None and "activate_publication" in selected_stages
        ):
            stage_steps.append(
                {
                    "name": "activate_publication",
                    "fn": lambda: self._activate_publication_stage_body(
                        confirmation=publication_confirmation or "",
                        prepare_staging=True,
                    ),
                    "output_key": "activate_publication",
                    "stale_running_policy": "rerun",
                    "retry_attempts": 1,
                    "retry_delay_seconds": 0,
                }
            )

        default_stage_names = {step["name"] for step in stage_steps}
        supported_force_stages = default_stage_names | {'webodm_task4'}
        unknown_force_stages = set(force_stages).difference(
            supported_force_stages
        )
        if unknown_force_stages:
            raise ValueError(
                'force_stages contains unknown stages: '
                + ', '.join(sorted(unknown_force_stages))
            )
        if selected_stages is None:
            stages_to_run = default_stage_names
        else:
            stages_to_run = set(selected_stages)
            unknown_stages = stages_to_run.difference(default_stage_names)
            if unknown_stages:
                raise ValueError(
                    "selected_stages contains unknown stages: "
                    + ", ".join(sorted(unknown_stages))
                )

        if 'webodm_task4' in force_stages:
            stages_to_run.add('webodm')

        def _force(name: str) -> bool:
            return (
                name in force_stages
                or (name == 'webodm' and 'webodm_task4' in force_stages)
                or not resume
            )

        try:
            for step in stage_steps:
                stage_name = step["name"]
                if stage_name not in stages_to_run:
                    continue

                self._check_control_or_raise(stage_name)

                if stage_name == 'webodm':
                    self._restore_webodm_identity()

                if self._stage_will_run(stage_name, force=_force(stage_name)):
                    self._preflight_stage(stage_name)

                runner_kwargs = {
                    "output_key": step["output_key"],
                    "state": self.state,
                    "force": _force(stage_name),
                }
                if step["stale_running_policy"]:
                    runner_kwargs["stale_running_policy"] = step[
                        "stale_running_policy"
                    ]
                if "retry_attempts" in step:
                    runner_kwargs["retry_attempts"] = step["retry_attempts"]
                if "retry_delay_seconds" in step:
                    runner_kwargs["retry_delay_seconds"] = step["retry_delay_seconds"]
                if stage_name == "webodm":
                    runner_kwargs["result_status"] = lambda result: (
                        "partially_completed"
                        if isinstance(result, Mapping)
                        and result.get("run_status") == "partially_completed"
                        else "completed"
                    )

                if stage_name == "webodm":
                    if (
                        'webodm_task4' in force_stages
                        and 'webodm' not in force_stages
                    ):
                        self._force_webodm_operations = {'task4'}
                    elif _force(stage_name):
                        self._force_webodm_operations = {
                            key
                            for key, skipped in (
                                ('task4', self.skip_task4_webodm),
                                ('task2', self.skip_task2_webodm),
                            )
                            if not skipped
                        }
                    else:
                        self._force_webodm_operations = set()
                try:
                    self.runner.run(stage_name, step["fn"], **runner_kwargs)
                finally:
                    if stage_name == "webodm" and hasattr(
                        self, "_force_webodm_operations"
                    ):
                        del self._force_webodm_operations
                self._hydrate_from_state()

                if stage_name == "quality_gate":
                    q = self.state.get("quality_gate") or {}
                    if q.get("passed") is False:
                        reason = q.get("reason") or "quality_gate_failed"
                        raise RuntimeError(
                            f"Pipeline stopped: Quality Gate failed ({reason})."
                        )

            final_status = str(self.state.get("run_status_override") or "completed")
            self.state["success"] = True
            self.state["status"] = final_status
            total_runtime = time.perf_counter() - total_start

            self.repo.mark_run_finished(
                self.run_id,
                success=True,
                total_runtime_seconds=total_runtime,
                status=final_status,
            )
            if self.survey_id:
                self.repo.mark_survey_finished(
                    self.survey_id,
                    success=True,
                    total_runtime_seconds=total_runtime,
                    status=final_status,
                )

            self.state["workspace_cleanup"] = self._workspace_cleanup_result(
                final_status=final_status,
                selected_stages=selected_stages,
                keep_workspace=keep_workspace,
            )

            self.control.cleanup_flags()
            log_event(
                pipeline_logger,
                "run_completed",
                elapsed_seconds=f"{total_runtime:.2f}",
                survey_id=self.survey_id,
                status=final_status,
            )
            pipeline_footer(pipeline_logger, total_runtime, success=True)
            return self.state

        # Preflight Failed
        except PreflightError as e:
            self.state.update(
                {
                    "success": False,
                    "preflight_failed": True,
                    "error": str(e),
                }
            )

            total_runtime = time.perf_counter() - total_start

            try:
                self.repo.mark_run_finished(
                    self.run_id,
                    success=False,
                    total_runtime_seconds=total_runtime,
                )
            except Exception:
                pipeline_logger.exception(
                    "Failed to mark preflight-failed run as finished"
                )

            if self.survey_id:
                try:
                    self.repo.mark_survey_finished(
                        self.survey_id,
                        success=False,
                        total_runtime_seconds=total_runtime,
                    )
                except Exception:
                    pipeline_logger.exception(
                        "Failed to mark preflight-failed survey as finished"
                    )

            self.control.cleanup_flags()
            log_event(
                pipeline_logger,
                "run_failed",
                level=logging.ERROR,
                elapsed_seconds=f"{total_runtime:.2f}",
                survey_id=self.survey_id,
                error_type=type(e).__name__,
                error_message=str(e)[:240],
                preflight_failed=True,
            )
            pipeline_footer(pipeline_logger, total_runtime, success=False)

            pipeline_logger.error("")
            pipeline_logger.error(str(e))

            if raise_on_error:
                raise

            return self.state

        # ── Paused / Canceled / Aborted / Runtime Failure ─────────
        except RuntimeError as e:
            if str(e) == "__PIPELINE_PAUSED__":
                self.state.update(
                    {
                        "success": False,
                        "paused": True,
                        "error": "paused_by_flag",
                    }
                )

                log_event(
                    pipeline_logger,
                    "run_paused",
                    level=logging.WARNING,
                    reason="paused_by_flag",
                    after_stage=self.state.get("paused_after_stage", "?"),
                )
                pipeline_paused(
                    pipeline_logger,
                    self.run_id,
                    after_stage=self.state.get("paused_after_stage", "?"),
                )

                # Clear only the pause flag so a future --resume
                # does not immediately re-trigger __PIPELINE_PAUSED__.
                self.control.clear_pause()
                return self.state

            # ── Canceled (WebODM UI) ───────────────────────────────
            if str(e) == "__PIPELINE_CANCELED__":
                self.state.update(
                    {
                        "success": False,
                        "canceled": True,
                        "error": "canceled_in_webodm_ui",
                    }
                )

                log_event(
                    pipeline_logger,
                    "run_canceled",
                    level=logging.WARNING,
                    reason="webodm_ui_cancel",
                    after_stage="webodm",
                )
                pipeline_canceled(pipeline_logger, self.run_id)

                try:
                    self.repo.mark_run_paused(
                        self.run_id,
                        paused_after_stage="webodm",
                        reason="webodm_ui_cancel",
                    )
                except Exception:
                    pipeline_logger.exception(
                        "Failed to mark run paused after WebODM cancel"
                    )

                return self.state

            # ── Aborted (Hotkey) ───────────────────────────────────
            if str(e) == "__PIPELINE_ABORTED__":
                self.state.update(
                    {
                        "success": False,
                        "aborted": True,
                        "error": "aborted_by_hotkey",
                    }
                )

                total_runtime = time.perf_counter() - total_start

                try:
                    self.repo.mark_run_finished(
                        self.run_id,
                        success=False,
                        total_runtime_seconds=total_runtime,
                    )
                except Exception:
                    pipeline_logger.exception(
                        "Failed to mark aborted run as finished"
                    )

                if self.survey_id:
                    try:
                        self.repo.mark_survey_finished(
                            self.survey_id,
                            success=False,
                            total_runtime_seconds=total_runtime,
                        )
                    except Exception:
                        pipeline_logger.exception(
                            "Failed to mark aborted survey as finished"
                        )

                self.control.cleanup_flags()
                log_event(
                    pipeline_logger,
                    "run_aborted",
                    level=logging.ERROR,
                    elapsed_seconds=f"{total_runtime:.2f}",
                    survey_id=self.survey_id,
                    reason="aborted_by_hotkey",
                )
                pipeline_footer(pipeline_logger, total_runtime, success=False)
                return self.state

            # ── Other Runtime Failure ──────────────────────────────
            self.state.update(
                {
                    "success": False,
                    "error": str(e),
                }
            )

            total_runtime = time.perf_counter() - total_start

            try:
                self.repo.mark_run_finished(
                    self.run_id,
                    success=False,
                    total_runtime_seconds=total_runtime,
                )
            except Exception:
                pipeline_logger.exception("Failed to mark run as failed")

            if self.survey_id:
                try:
                    self.repo.mark_survey_finished(
                        self.survey_id,
                        success=False,
                        total_runtime_seconds=total_runtime,
                    )
                except Exception:
                    pipeline_logger.exception("Failed to mark survey as failed")

            self.control.cleanup_flags()
            log_event(
                pipeline_logger,
                "run_failed",
                level=logging.ERROR,
                elapsed_seconds=f"{total_runtime:.2f}",
                survey_id=self.survey_id,
                error_type=type(e).__name__,
                error_message=str(e)[:240],
            )
            pipeline_footer(pipeline_logger, total_runtime, success=False)
            pipeline_logger.error(str(e))
            if raise_on_error:
                raise
            return self.state

        # ── Failure ───────────────────────────────────────────────
        except Exception as e:
            self.state.update(
                {
                    "success": False,
                    "error": str(e),
                }
            )

            total_runtime = time.perf_counter() - total_start

            try:
                self.repo.mark_run_finished(
                    self.run_id,
                    success=False,
                    total_runtime_seconds=total_runtime,
                )
            except Exception:
                pipeline_logger.exception("Failed to mark run as failed")

            if self.survey_id:
                try:
                    self.repo.mark_survey_finished(
                        self.survey_id,
                        success=False,
                        total_runtime_seconds=total_runtime,
                    )
                except Exception:
                    pipeline_logger.exception("Failed to mark survey as failed")

            self.control.cleanup_flags()
            log_event(
                pipeline_logger,
                "run_failed",
                level=logging.ERROR,
                elapsed_seconds=f"{total_runtime:.2f}",
                survey_id=self.survey_id,
                error_type=type(e).__name__,
                error_message=str(e)[:240],
            )
            pipeline_footer(pipeline_logger, total_runtime, success=False)
            pipeline_logger.error(str(e))
            if raise_on_error:
                raise
            return self.state
