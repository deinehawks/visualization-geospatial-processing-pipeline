from __future__ import annotations

from pathlib import Path
from typing import Any


class RGBTaskNamingMixin:
    """
    Helper methods for WebODM task labels, flags, and output filenames.

    This mixin expects the main RGBPipeline class to provide:
    - self.config
    - self.state
    """

    config: dict[str, Any]
    state: dict[str, Any]

    def _normalize_webodm_task_key(self, task_key: str) -> str:
        task_key = str(task_key).strip().lower().replace("-", "_")

        if not task_key:
            raise ValueError("WebODM task key must not be empty.")

        return task_key

    def _webodm_task_label(self, task_key: str) -> str:
        """
        Examples:
        task1 -> t1
        task2 -> t2
        task4 -> t4
        fallback_dense -> fallback-dense
        """
        task_key = self._normalize_webodm_task_key(task_key)

        if task_key.startswith("task"):
            number = task_key.replace("task", "", 1).strip()

            if number.isdigit():
                return f"t{number}"

        return task_key.replace("_", "-")

    def _webodm_task_options(self, task_key: str) -> dict[str, Any]:
        """
        Resolves task options from config.

        Supports:
        - webodm.task1_options
        - webodm.task2_options
        - webodm.task4_options
        - webodm.task5_options, if added later
        """
        task_key = self._normalize_webodm_task_key(task_key)
        webodm_cfg = self.config.get("webodm") or {}

        return dict(webodm_cfg.get(f"{task_key}_options") or {})

    def _webodm_task_boundary_mode(
        self,
        task_key: str,
        *,
        default: str = "b",
    ) -> str:
        task_key = self._normalize_webodm_task_key(task_key)
        naming_cfg = self.config.get("naming") or {}

        raw_mode = str(
            naming_cfg.get(f"{task_key}_boundary_mode")
            or default
        )

        return self._normalize_boundary_mode_for_flag(raw_mode)

    def _webodm_task_flag(
        self,
        task_key: str,
        *,
        default_boundary_mode: str = "b",
    ) -> str:
        task_key = self._normalize_webodm_task_key(task_key)
        naming_cfg = self.config.get("naming") or {}

        crossrun_flag = (
            self.state.get("crossrun_flag")
            or naming_cfg.get("crossrun_mode")
            or "xc"
        )

        crossrun_flag = str(crossrun_flag).strip().lower()

        if crossrun_flag not in {"c", "xc"}:
            raise ValueError(f"Invalid crossrun flag: {crossrun_flag!r}")

        boundary_mode = self._webodm_task_boundary_mode(
            task_key,
            default=default_boundary_mode,
        )

        return f"{crossrun_flag}{boundary_mode}"

    def _format_output_filename(
        self,
        *,
        template: str,
        flag: str,
        task_key: str,
    ) -> str:
        """
        Supports old and new templates.

        Old:
        orthomosaic--{flag}.tif
        -> orthomosaic--xcb-t2.tif

        New:
        orthomosaic--{flag}-{task_label}.tif
        -> orthomosaic--xcb-t2.tif
        """
        task_key = self._normalize_webodm_task_key(task_key)
        task_label = self._webodm_task_label(task_key)

        filename = template.format(
            flag=flag,
            task_key=task_key,
            task_label=task_label,
        )

        # Backward-compatible safety:
        # If the template only uses {flag}, append the task label before suffix.
        if "{task_key}" not in template and "{task_label}" not in template:
            path = Path(filename)
            filename = f"{path.stem}-{task_label}{path.suffix}"

        return filename

    def _orthomosaic_filename(
        self,
        *,
        task_key: str,
        flag: str,
    ) -> str:
        ortho_cfg = (
            (self.config.get("exports") or {})
            .get("ortho")
            or {}
        )

        template = str(
            ortho_cfg.get("filename_template")
            or "orthomosaic--{flag}-{task_label}.tif"
        )

        return self._format_output_filename(
            template=template,
            flag=flag,
            task_key=task_key,
        )

    def _clipped_orthomosaic_filename(
        self,
        *,
        task_key: str,
        flag: str,
    ) -> str:
        clip_cfg = (
            (self.config.get("qgis") or {})
            .get("clip")
            or {}
        )

        template = str(
            clip_cfg.get("filename_template")
            or "orthomosaic-clipped--{flag}-{task_label}.tif"
        )

        return self._format_output_filename(
            template=template,
            flag=flag,
            task_key=task_key,
        )
    
    def _normalize_boundary_mode_for_flag(self, value: str) -> str:
        """
        Normalizes boundary mode to only the boundary component.

        Correct semantic values:
        - b  = added boundary
        - xb = no added boundary

        Backward-compatible legacy full flags:
        - cb   -> b
        - xcb  -> b
        - cxb  -> xb
        - xcxb -> xb
        """
        value = str(value).strip().lower()

        if value in {"b", "xb"}:
            return value

        if value in {"cb", "xcb"}:
            return "b"

        if value in {"cxb", "xcxb"}:
            return "xb"

        raise ValueError(
            "Invalid boundary mode. Expected b or xb "
            f"(legacy full flags also accepted), got: {value!r}"
        )
    
    def _webodm_task_name(
        self,
        *,
        survey_id: str,
        flag: str,
        task_key: str,
    ) -> str:
        task_label = self._webodm_task_label(task_key)
        return f"{survey_id}-RGB--{flag}-{task_label}"