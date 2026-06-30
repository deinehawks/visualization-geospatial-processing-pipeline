from __future__ import annotations

from pathlib import Path


class RGBTaskNamingMixin:
    """
    Helper methods for WebODM task labels, flags, and output filenames.

    This is future-proof for task1, task2, task4, task5, etc.
    """

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

    def _webodm_task_options(self, task_key: str) -> dict:
        """
        Resolves task options from config.

        Supports existing:
        - webodm.task1_options
        - webodm.task2_options
        - webodm.task4_options

        Future:
        - webodm.task5_options
        - webodm.task6_options
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
        """
        Resolves boundary naming mode.

        Existing:
        - naming.task1_boundary_mode
        - naming.task2_boundary_mode

        Future:
        - naming.task4_boundary_mode
        - naming.task5_boundary_mode
        """
        task_key = self._normalize_webodm_task_key(task_key)
        naming_cfg = self.config.get("naming") or {}

        return str(
            naming_cfg.get(f"{task_key}_boundary_mode")
            or default
        )

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
        Supports both old templates and future templates.

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
        # If the template only used {flag}, append the task label before suffix.
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