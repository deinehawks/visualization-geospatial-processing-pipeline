from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast


class _TaskNamingSupport(Protocol):
    def _normalize_webodm_task_key(self, task_key: str) -> str:
        ...

    def _webodm_task_label(self, task_key: str) -> str:
        ...


class RGBOrthomosaicSelectionMixin:
    """
    Helper methods for selecting the WebODM orthomosaic that downstream QGIS
    stages should use.

    This mixin expects the main RGBPipeline class to provide:
    - self.state

    It also expects RGBTaskNamingMixin to be included in RGBPipeline.
    """

    state: dict[str, Any]

    def _task_naming(self) -> _TaskNamingSupport:
        return cast(_TaskNamingSupport, self)

    def _get_webodm_downloads(self) -> dict[str, Any]:
        web = self.state.get("webodm") or {}
        return dict(web.get("downloads") or {})

    def _get_webodm_task_state(self, task_key: str) -> dict[str, Any]:
        task_key = self._task_naming()._normalize_webodm_task_key(task_key)

        web = self.state.get("webodm") or {}

        return dict(web.get(task_key) or {})

    def _get_task_orthomosaic_path(
        self,
        task_key: str,
        *,
        require_exists: bool = True,
    ) -> Path | None:
        task_key = self._task_naming()._normalize_webodm_task_key(task_key)

        downloads = self._get_webodm_downloads()
        task_downloads = downloads.get(task_key) or {}

        value = task_downloads.get("orthomosaic")
        if not value:
            return None

        path = Path(value)

        if require_exists and not path.exists():
            return None

        return path

    def _set_selected_orthomosaic(
        self,
        *,
        task_key: str,
        source_path: Path,
        flag: str,
        boundary_used: bool,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_key = self._task_naming()._normalize_webodm_task_key(task_key)
        task_label = self._task_naming()._webodm_task_label(task_key)
        task_state = self._get_webodm_task_state(task_key)

        source_path = Path(source_path)

        selected: dict[str, Any] = {
            "task_key": task_key,
            "task_label": task_label,
            "task_id": str(task_state.get("id") or ""),
            "task_name": str(task_state.get("name") or task_key),
            "flag": flag,
            "source_path": str(source_path),
            "source_filename": source_path.name,
            "boundary_used": bool(boundary_used),
            "tile_mode": "round-corners" if boundary_used else "soft-corners",
            "fallback_used": bool(fallback_used),
            "fallback_reason": fallback_reason,
        }

        if extra:
            selected.update(extra)

        self.state["selected_webodm_task"] = task_key
        self.state["selected_orthomosaic"] = selected

        return selected

    def _select_existing_task_orthomosaic(
        self,
        *,
        task_key: str,
        flag: str,
        boundary_used: bool,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
        require_exists: bool = True,
    ) -> dict[str, Any]:
        task_key = self._task_naming()._normalize_webodm_task_key(task_key)

        ortho_path = self._get_task_orthomosaic_path(
            task_key,
            require_exists=require_exists,
        )

        if ortho_path is None:
            raise RuntimeError(
                f"Cannot select {task_key}: orthomosaic download is missing."
            )

        return self._set_selected_orthomosaic(
            task_key=task_key,
            source_path=ortho_path,
            flag=flag,
            boundary_used=boundary_used,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )

    def _get_selected_orthomosaic(self) -> dict[str, Any]:
        selected = self.state.get("selected_orthomosaic") or {}

        if not selected:
            raise RuntimeError(
                "No selected orthomosaic found in pipeline state. "
                "WebODM or quality gate must select an orthomosaic before QGIS."
            )

        source_path = selected.get("source_path")
        if not source_path or not Path(source_path).exists():
            raise RuntimeError(
                "Selected orthomosaic source path is missing or does not exist: "
                f"{source_path}"
            )

        return dict(selected)