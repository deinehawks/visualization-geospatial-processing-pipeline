from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol, cast


class _UploadCacheCleanupSupport(Protocol):
    def _cleanup_upload_cache(self, cache_dir: Path, logger: logging.Logger) -> None:
        ...


class RGBUploadCacheMixin:
    """
    Helper methods for WebODM upload cache reuse.

    Rule:
    - If cache staging fails, warn and upload directly.
    - If cache staging succeeds, reuse the same upload folder for fallback tasks.
    - Cleanup should happen only after quality gate/fallback decision.
    """

    state: dict[str, Any]

    def _upload_cache_cleanup_support(self) -> _UploadCacheCleanupSupport:
        return cast(_UploadCacheCleanupSupport, self)

    def _remember_webodm_upload_folder(
        self,
        *,
        upload_folder: Path,
        cached_dir: Path | None,
        source_folder: Path,
        fallback_direct: bool,
        fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        cache_state: dict[str, Any] = {
            "upload_folder": str(upload_folder),
            "source_folder": str(source_folder),
            "cached": cached_dir is not None,
            "cached_dir": str(cached_dir) if cached_dir is not None else None,
            "fallback_direct": bool(fallback_direct),
            "fallback_reason": fallback_reason,
            "cleaned": False,
        }

        self.state["webodm_upload_cache"] = cache_state
        return cache_state

    def _get_reusable_webodm_upload_folder(
        self,
        *,
        default_source_dir: Path,
        logger: logging.Logger,
    ) -> Path:
        cache_state = self.state.get("webodm_upload_cache") or {}
        upload_folder_raw = cache_state.get("upload_folder")

        if upload_folder_raw:
            upload_folder = Path(upload_folder_raw)

            if upload_folder.exists() and upload_folder.is_dir():
                logger.info(
                    "Reusing existing WebODM upload folder for fallback task: "
                    f"{upload_folder}"
                )
                return upload_folder

            logger.warning(
                "Saved WebODM upload cache folder is missing. "
                f"Falling back to direct upload from source. missing={upload_folder}"
            )

        logger.warning(
            "No reusable WebODM upload cache found. "
            f"Falling back to direct upload from source: {default_source_dir}"
        )
        return Path(default_source_dir)

    def _cleanup_webodm_upload_cache_from_state(
        self,
        logger: logging.Logger,
    ) -> None:
        cache_state = self.state.get("webodm_upload_cache") or {}

        if not cache_state:
            return

        if cache_state.get("cleaned"):
            return

        cached_dir_raw = cache_state.get("cached_dir")
        is_cached = bool(cache_state.get("cached"))

        if not is_cached or not cached_dir_raw:
            cache_state["cleaned"] = True
            self.state["webodm_upload_cache"] = cache_state
            return

        cached_dir = Path(cached_dir_raw)

        if cached_dir.exists():
            self._upload_cache_cleanup_support()._cleanup_upload_cache(
                cached_dir,
                logger,
            )

        cache_state["cleaned"] = True
        self.state["webodm_upload_cache"] = cache_state