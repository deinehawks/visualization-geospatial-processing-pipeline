from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, NoReturn

import requests


IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
BOUNDARY_EXTENSIONS = {".kml", ".kmz"}


@dataclass
class PreflightError(RuntimeError):
    stage: str
    title: str
    details: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.user_message)

    @property
    def user_message(self) -> str:
        lines = [
            f"Preflight failed before stage '{self.stage}': {self.title}"
        ]

        if self.details:
            lines.append("Details:")
            lines.extend(f"- {item}" for item in self.details)

        if self.suggestions:
            lines.append("Suggested action:")
            lines.extend(f"- {item}" for item in self.suggestions)

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.user_message


class PipelinePreflight:
    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        source_dir: Path,
        surveys_root: Path,
        year: int,
        logger=None,
        webodm_timeout_seconds: int = 5,
    ) -> None:
        self.config = config
        self.source_dir = Path(source_dir)
        self.surveys_root = Path(surveys_root)
        self.year = int(year)
        self.logger = logger
        self.webodm_timeout_seconds = int(webodm_timeout_seconds)

    def check_stage(
        self,
        stage: str,
        *,
        state: Mapping[str, Any],
        survey_id: Optional[str] = None,
        rgb_path: Optional[Path] = None,
        skip_task1_webodm: bool = False,
        skip_task2_webodm: bool = False,
    ) -> Dict[str, Any]:
        stage = str(stage).strip()

        if stage == "data_segregation":
            return self._check_data_segregation(stage)

        if stage == "cross_run_filter":
            return self._check_cross_run_filter(stage, state, rgb_path)

        if stage == "kml_boundary":
            return self._check_kml_boundary(stage, state, rgb_path)

        if stage == "webodm":
            return self._check_webodm(
                stage,
                state,
                rgb_path,
                skip_task1_webodm=skip_task1_webodm,
                skip_task2_webodm=skip_task2_webodm,
            )

        if stage == "quality_gate":
            return self._check_quality_gate(stage, state)

        if stage == "qgis":
            return self._check_qgis(stage, state)

        return {"ok": True, "stage": stage}

    # Stage checks

    def _check_data_segregation(self, stage: str) -> Dict[str, Any]:
        if not self.source_dir.exists():
            self._fail(
                stage,
                "source survey folder is missing",
                details=[f"Source directory: {self.source_dir}"],
                suggestions=[
                    "Check if the field-data drive is mounted.",
                    "Check if the --survey folder name is correct.",
                    "Confirm FIELD_DATA_ROOT points to the correct field-data location.",
                ],
            )

        if not self.source_dir.is_dir():
            self._fail(
                stage,
                "source path exists but is not a folder",
                details=[f"Source path: {self.source_dir}"],
                suggestions=[
                    "Use a valid field-data folder containing drone images and a KML/KMZ boundary."
                ],
            )

        image_count = self._count_files(
            self.source_dir,
            IMAGE_EXTENSIONS,
            recursive=True,
        )

        if image_count <= 0:
            self._fail(
                stage,
                "no JPG/JPEG images were found in the source survey folder",
                details=[
                    f"Source directory: {self.source_dir}",
                    "Expected files: .jpg or .jpeg",
                ],
                suggestions=[
                    "Copy the drone JPEG images into the survey folder.",
                    "Check if the images are inside a nested folder.",
                    "Confirm the image file extensions are .jpg or .jpeg.",
                ],
            )

        boundary_count = self._count_files(
            self.source_dir,
            BOUNDARY_EXTENSIONS,
            recursive=True,
        )

        if boundary_count <= 0:
            self._fail(
                stage,
                "no KML/KMZ boundary file was found in the source survey folder",
                details=[
                    f"Source directory: {self.source_dir}",
                    "Expected files: .kml or .kmz",
                ],
                suggestions=[
                    "Add the mission boundary KML/KMZ file to the survey folder.",
                    "Confirm that the boundary file extension is .kml or .kmz.",
                ],
            )

        try:
            year_dir = self.surveys_root / str(self.year)
            year_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self._fail(
                stage,
                "surveys output folder is not writable",
                details=[
                    f"Surveys root: {self.surveys_root}",
                    f"Error: {e}",
                ],
                suggestions=[
                    "Check SURVEYS_ROOT in the environment configuration.",
                    "Make sure the network drive or output disk is mounted and writable.",
                ],
            )

        return {
            "ok": True,
            "stage": stage,
            "source_images": image_count,
            "boundary_files": boundary_count,
        }

    def _check_cross_run_filter(
        self,
        stage: str,
        state: Mapping[str, Any],
        rgb_path: Optional[Path],
    ) -> Dict[str, Any]:
        raw_dir = (
            self._dir_from_state(state, "data_segregation", "raw")
            or self._rgb_child(rgb_path, "images", "raw")
        )

        if not raw_dir or not raw_dir.exists():
            self._fail(
                stage,
                "raw image folder is missing",
                details=[f"Expected raw image folder: {raw_dir}"],
                suggestions=[
                    "Run the data_segregation stage first.",
                    "Check if the survey output folder was deleted or moved.",
                    "Start a new run if the saved checkpoint points to an old folder.",
                ],
            )

        image_count = self._count_files(
            raw_dir,
            IMAGE_EXTENSIONS,
            recursive=False,
        )

        if image_count <= 0:
            self._fail(
                stage,
                "raw image folder does not contain JPG/JPEG images",
                details=[
                    f"Raw image folder: {raw_dir}",
                    "Expected files: .jpg or .jpeg",
                ],
                suggestions=[
                    "Re-run data_segregation.",
                    "Check if raw images were deleted after a previous test run.",
                ],
            )

        return {
            "ok": True,
            "stage": stage,
            "raw_images": image_count,
        }

    def _check_kml_boundary(
        self,
        stage: str,
        state: Mapping[str, Any],
        rgb_path: Optional[Path],
    ) -> Dict[str, Any]:
        boundary_dir = (
            self._dir_from_state(state, "data_segregation", "boundary")
            or self._rgb_child(rgb_path, "boundary")
        )

        if not boundary_dir or not boundary_dir.exists():
            self._fail(
                stage,
                "boundary folder does not contain a KML/KMZ file",
                details=[
                    f"Boundary folder: {boundary_dir}",
                    "Expected files: .kml or .kmz",
                ],
                suggestions=[
                    "Re-run data_segregation.",
                    "Manually copy the boundary KML/KMZ file into the boundary folder.",
                ],
            )

        boundary_count = self._count_files(
            boundary_dir,
            BOUNDARY_EXTENSIONS,
            recursive=False,
        )

        if boundary_count <= 0:
            self._fail(
                stage,
                "boundary folder does not contain a KML/KMZ file",
                details=[f"Boundary folder: {boundary_dir}"],
                suggestions=[
                    "Re-run data segregation or add the boundary KML/KMZ file manually."
                ],
            )

        return {
            "ok": True,
            "stage": stage,
            "boundary_files": boundary_count,
        }

    def _check_webodm(
        self,
        stage: str,
        state: Mapping[str, Any],
        rgb_path: Optional[Path],
        *,
        skip_task1_webodm: bool,
        skip_task2_webodm: bool,
    ) -> Dict[str, Any]:
        if skip_task1_webodm and skip_task2_webodm:
            self._fail(
                stage,
                "both WebODM Task 1 and Task 2 are disabled",
                suggestions=[
                    "Enable at least one WebODM task before running the WebODM stage."
                ],
            )

        image_dir = (
            self._dir_from_state(state, "data_segregation", "path")
            or self._rgb_child(rgb_path, "images", "path")
        )

        if not image_dir or not image_dir.exists():
            self._fail(
                stage,
                "filtered image folder is missing",
                details=[f"Expected image folder: {image_dir}"],
                suggestions=[
                    "Run the cross_run_filter stage first.",
                    "Check if the survey output folder was deleted or moved.",
                    "Start a new run if this run ID points to an old survey folder.",
                ],
            )

        image_count = self._count_files(
            image_dir,
            IMAGE_EXTENSIONS,
            recursive=False,
        )

        if image_count <= 0:
            self._fail(
                stage,
                "filtered image folder does not contain JPG/JPEG images",
                details=[f"Image folder: {image_dir}"],
                suggestions=[
                    "Re-run the cross_run_filter stage or verify the filtered image outputs."
                ],
            )

        if not skip_task2_webodm:
            boundary_geojson = self._path_from_state(
                state,
                "boundary_geojson_path",
            )

            if not boundary_geojson or not boundary_geojson.exists():
                self._fail(
                    stage,
                    "boundary GeoJSON is missing for bounded WebODM Task 2",
                    details=[f"Boundary GeoJSON: {boundary_geojson}"],
                    suggestions=[
                       "Run the kml_boundary stage first.",
                        "Check if KML/KMZ conversion completed successfully.",
                        "Confirm the boundary file is valid.",
                    ],
                )

        self._check_webodm_server(stage)
        self._check_webodm_node(stage)

        return {
            "ok": True,
            "stage": stage,
            "filtered_images": image_count,
        }

    def _check_quality_gate(
        self,
        stage: str,
        state: Mapping[str, Any],
    ) -> Dict[str, Any]:
        web = state.get("webodm") or {}

        project_id = web.get("project_id")
        task1 = web.get("task1") or {}
        task2 = web.get("task2") or {}
        task4 = web.get("task4") or {}

        task_ids = [
            task1.get("id"),
            task2.get("id"),
            task4.get("id"),
        ]

        if not project_id or not any(task_ids):
            self._fail(
                stage,
                "WebODM did not produce a task ID for quality inspection",
                details=[
                    f"project_id={project_id}",
                    f"task1_id={task1.get('id')}",
                    f"task2_id={task2.get('id')}",
                    f"task4_id={task4.get('id')}",
                ],
                suggestions=[
                    "Check if WebODM Docker is running.",
                    "Check if the WebODM node worker is online in WebODM > Processing Nodes.",
                    "Re-run the WebODM stage after the server and node worker are online.",
                    "If the saved run points to an old or deleted survey folder, start a new run instead of resuming this run ID.",
                ],
            )

        return {
            "ok": True,
            "stage": stage,
            "project_id": project_id,
        }

    def _check_qgis(
        self,
        stage: str,
        state: Mapping[str, Any],
    ) -> Dict[str, Any]:
        boundary_geojson = self._path_from_state(
            state,
            "boundary_geojson_path",
        )

        if not boundary_geojson or not boundary_geojson.exists():
            self._fail(
                stage,
                "boundary GeoJSON is missing for QGIS processing",
                details=[f"Boundary GeoJSON: {boundary_geojson}"],
                suggestions=[
                    "Run or resume from the kml_boundary stage first."
                ],
            )

        web = state.get("webodm") or {}
        downloads = web.get("downloads") or {}

        task1_ortho = self._path_from_mapping(
            downloads.get("task1") or {},
            "orthomosaic",
        )
        task2_ortho = self._path_from_mapping(
            downloads.get("task2") or {},
            "orthomosaic",
        )
        task4_ortho = self._path_from_mapping(
            downloads.get("task4") or {},
            "orthomosaic",
        )

        orthos = [
            p for p in (task4_ortho, task2_ortho, task1_ortho)
            if p and p.exists()
        ]

        if not orthos:
            self._fail(
                stage,
                "no WebODM orthomosaic output was found for QGIS processing",
                details=[
                    f"task4_orthomosaic={task4_ortho}",
                    f"task2_orthomosaic={task2_ortho}",
                    f"task1_orthomosaic={task1_ortho}",
                ],
                suggestions=[
                    "Confirm that the WebODM stage completed and exported an orthomosaic.",
                    "Re-run the WebODM stage if the output files were deleted or moved.",
                ],
            )

        return {
            "ok": True,
            "stage": stage,
            "orthomosaic": str(orthos[0]),
        }

    # WebODM readiness checks

    def _check_webodm_server(self, stage: str) -> str:
        webodm_cfg = self.config.get("webodm") or {}

        url = str(webodm_cfg.get("url") or "").rstrip("/")
        username = webodm_cfg.get("username")
        password = webodm_cfg.get("password")

        if not url or not username or not password:
            self._fail(
                stage,
                "WebODM URL, username, or password is missing from configuration",
                suggestions=[
                    "Check WEBODM_URL, WEBODM_USERNAME, and WEBODM_PASSWORD in the environment configuration."
                ],
            )

        try:
            resp = requests.post(
                f"{url}/api/token-auth/",
                data={
                    "username": username,
                    "password": password,
                },
                timeout=self.webodm_timeout_seconds,
            )
            resp.raise_for_status()

            token = resp.json().get("token")
            if not token:
                raise RuntimeError(
                    "authentication response did not include a token"
                )

            return str(token)

        except requests.exceptions.RequestException as e:
            self._fail(
                stage,
                "WebODM server is not reachable",
                details=[
                    f"WebODM URL: {url}",
                    f"Reason: {self._friendly_request_error(e)}",
                ],
                suggestions=[
                    "Check if WebODM Docker is running.",
                    "Run docker compose ps in the WebODM folder and confirm the webapp, db, broker, and worker services are healthy.",
                    "Start WebODM with docker compose up -d if it is stopped.",
                    "Confirm WEBODM_URL is correct and reachable from this workstation.",
                ],
            )

        except Exception as e:
            self._fail(
                stage,
                "WebODM readiness check failed",
                details=[
                    f"WebODM URL: {url}",
                    f"Error: {e}",
                ],
                suggestions=[
                    "Check if WebODM Docker is running.",
                    "Check if the WebODM node worker is online.",
                    "Confirm WebODM credentials and WEBODM_NODE_ID configuration.",
                ],
            )

        raise RuntimeError("unreachable")

    def _check_webodm_node(self, stage: str) -> None:
        webodm_cfg = self.config.get("webodm") or {}

        url = str(webodm_cfg.get("url") or "").rstrip("/")
        username = webodm_cfg.get("username")
        password = webodm_cfg.get("password")
        node_id = webodm_cfg.get("node_id")

        token = self._check_webodm_server(stage)

        try:
            resp = requests.get(
                f"{url}/api/processingnodes/",
                headers={"Authorization": f"JWT {token}"},
                timeout=self.webodm_timeout_seconds,
            )

            if resp.status_code == 404:
                self._warn(
                    "Processing-node API endpoint was not found; node status could not be verified."
                )
                return

            resp.raise_for_status()
            payload = resp.json()

            nodes = payload.get("results") if isinstance(payload, dict) else payload
            nodes = nodes if isinstance(nodes, list) else []

            if not nodes:
                self._fail(
                    stage,
                    "no WebODM processing nodes were returned by the server",
                    details=[f"WebODM URL: {url}"],
                    suggestions=[
                        "Open WebODM > Processing Nodes and confirm the worker node exists.",
                        "Restart the WebODM node worker/container.",
                        "Check Docker logs for the WebODM worker or node container.",
                    ],
                )

            if node_id is not None:
                wanted = str(node_id)
                node = next(
                    (n for n in nodes if str(n.get("id")) == wanted),
                    None,
                )

                if not node:
                    self._fail(
                        stage,
                        "configured WebODM processing node was not found",
                        details=[f"Configured WEBODM_NODE_ID: {node_id}"],
                        suggestions=[
                            "Check WEBODM_NODE_ID in the environment configuration.",
                            "Open WebODM > Processing Nodes and use the correct node ID.",
                        ],
                    )

                if not self._node_looks_online(node):
                    self._fail(
                        stage,
                        "configured WebODM processing node appears offline",
                        details=[
                            f"Configured WEBODM_NODE_ID: {node_id}",
                            f"Node payload: {node}",
                        ],
                        suggestions=[
                            "Open WebODM > Processing Nodes and confirm the node status is Online.",
                            "Restart the WebODM node worker/container.",
                            "Check Docker logs for the node worker.",
                        ],
                    )

                return

            online_nodes = [
                n for n in nodes
                if self._node_looks_online(n)
            ]

            if not online_nodes:
                self._fail(
                    stage,
                    "all WebODM processing nodes appear offline",
                    details=[f"Nodes returned: {nodes}"],
                    suggestions=[
                        "Open WebODM > Processing Nodes and confirm at least one node is Online.",
                        "Restart the WebODM node worker/container.",
                        "Check Docker logs for the node worker.",
                    ],
                )

        except PreflightError:
            raise

        except Exception as e:
            self._fail(
                stage,
                "WebODM processing node check failed",
                details=[f"Error: {e}"],
                suggestions=[
                    "Check if the WebODM node worker is online.",
                    "Open WebODM > Processing Nodes and verify the node status.",
                    "Restart the WebODM worker/node container if needed.",
                ],
            )

    @staticmethod
    def _node_looks_online(node: Mapping[str, Any]) -> bool:
        for key in ("online", "is_online", "available", "reachable"):
            value = node.get(key)
            if isinstance(value, bool):
                return value

        status = str(
            node.get("status")
            or node.get("status_label")
            or node.get("state")
            or node.get("health")
            or ""
        ).lower().strip()

        if status:
            offline_words = (
                "offline",
                "unavailable",
                "down",
                "error",
                "failed",
                "disconnected",
            )
            return not any(word in status for word in offline_words)

        return True
    
    @staticmethod
    def _friendly_request_error(e: Exception) -> str:
        if isinstance(e, requests.exceptions.ConnectionError):
            return (
                "Connection refused or server unreachable. "
                "WebODM is probably stopped, Docker is not running, "
                "or WEBODM_URL is incorrect."
            )

        if isinstance(e, requests.exceptions.ReadTimeout):
            return (
                "WebODM did not respond before the timeout. "
                "The server may be overloaded or starting up."
            )

        if isinstance(e, requests.exceptions.HTTPError):
            response = getattr(e, "response", None)
            status_code = getattr(response, "status_code", None)
            return f"WebODM returned HTTP {status_code}."

        return str(e).splitlines()[0][:300]

    # Helpers

    def _fail(
        self,
        stage: str,
        title: str,
        *,
        details: Optional[Iterable[str]] = None,
        suggestions: Optional[Iterable[str]] = None,
    ) -> NoReturn:
        raise PreflightError(
            stage=stage,
            title=title,
            details=list(details or []),
            suggestions=list(suggestions or []),
        ) from None

    def _warn(self, message: str) -> None:
        if self.logger:
            self.logger.warning(f"PREFLIGHT | {message}")

    @staticmethod
    def _count_files(
        root: Path,
        extensions: set[str],
        *,
        recursive: bool,
    ) -> int:
        if not root or not root.exists():
            return 0

        iterator = root.rglob("*") if recursive else root.iterdir()
        lowered = {e.lower() for e in extensions}

        return sum(
            1
            for path in iterator
            if path.is_file() and path.suffix.lower() in lowered
        )

    @staticmethod
    def _path_from_mapping(
        mapping: Mapping[str, Any],
        key: str,
    ) -> Optional[Path]:
        value = mapping.get(key)
        return Path(value) if value else None

    @staticmethod
    def _path_from_state(
        state: Mapping[str, Any],
        key: str,
    ) -> Optional[Path]:
        value = state.get(key)
        return Path(value) if value else None

    @staticmethod
    def _dir_from_state(
        state: Mapping[str, Any],
        stage: str,
        dir_key: str,
    ) -> Optional[Path]:
        stage_state = state.get(stage) or {}
        dirs = stage_state.get("dirs") or {}
        value = dirs.get(dir_key)
        return Path(value) if value else None

    @staticmethod
    def _rgb_child(
        rgb_path: Optional[Path],
        *parts: str,
    ) -> Optional[Path]:
        return rgb_path.joinpath(*parts) if rgb_path else None