from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TransientWebODMError(RuntimeError):
    """Deterministic retryable failure used by tests."""


class PermanentWebODMError(RuntimeError):
    """Deterministic non-retryable failure used by tests."""


@dataclass(frozen=True)
class WebODMCall:
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeWebODM:
    """Small recording fake for the WebODM boundary used by pipeline tests."""

    def __init__(
        self,
        *,
        transient_failures: dict[str, int] | None = None,
        permanent_failures: set[str] | None = None,
    ) -> None:
        self.calls: list[WebODMCall] = []
        self.transient_failures = dict(transient_failures or {})
        self.permanent_failures = set(permanent_failures or set())
        self.task_statuses: dict[str, str] = {}
        self.project_tasks: dict[int, list[dict[str, Any]]] = {}
        self.authenticated = False
        self._next_project_id = 100
        self._next_task_number = 1

    def _record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append(WebODMCall(method, args, dict(kwargs)))
        remaining = self.transient_failures.get(method, 0)
        if remaining > 0:
            self.transient_failures[method] = remaining - 1
            raise TransientWebODMError(f"Transient WebODM failure: {method}")
        if method in self.permanent_failures:
            raise PermanentWebODMError(f"Permanent WebODM failure: {method}")

    def calls_for(self, method: str) -> list[WebODMCall]:
        return [call for call in self.calls if call.method == method]

    def configure_task_status(self, task_id: str, status: str) -> None:
        self.task_statuses[str(task_id)] = status

    def configure_project_tasks(
        self,
        project_id: int,
        tasks: list[dict[str, Any]],
    ) -> None:
        self.project_tasks[int(project_id)] = [dict(task) for task in tasks]

    def authenticate(self) -> None:
        self._record("authenticate")
        self.authenticated = True

    def preflight(self) -> bool:
        self._record("preflight")
        return True

    def create_project(self, name: str, description: str = "") -> int:
        self._record("create_project", name, description=description)
        project_id = self._next_project_id
        self._next_project_id += 1
        return project_id

    def create_task_with_images(
        self,
        project_id: int,
        name: str,
        image_folder: str,
        options: dict[str, Any] | None = None,
        processing_node: int | None = None,
        **kwargs: Any,
    ) -> str:
        self._record(
            "create_task_with_images",
            project_id,
            name,
            image_folder,
            options=dict(options or {}),
            processing_node=processing_node,
            **kwargs,
        )
        task_id = f"task-{self._next_task_number:04d}"
        self._next_task_number += 1
        self.task_statuses[task_id] = "queued"
        return task_id

    def get_task(self, project_id: int, task_id: str, **kwargs: Any) -> dict[str, Any]:
        task_id = str(task_id)
        self._record("get_task", project_id, task_id, **kwargs)
        return {"id": task_id, "status": self.task_statuses.get(task_id, "queued")}

    def get_task_status(self, project_id: int, task_id: str) -> str:
        task_id = str(task_id)
        self._record("get_task_status", project_id, task_id)
        return self.task_statuses.get(task_id, "queued")

    def wait_for_completion(
        self,
        project_id: int,
        task_id: str,
        **kwargs: Any,
    ) -> tuple[bool, float, dict[str, Any]]:
        task_id = str(task_id)
        self._record("wait_for_completion", project_id, task_id, **kwargs)
        status = self.task_statuses.get(task_id, "completed")
        success = status == "completed"
        return success, 0.0, {"id": task_id, "status": status}

    def find_task_by_name(self, project_id: int, task_name: str) -> None:
        self._record("find_task_by_name", project_id, task_name)
        return None

    def list_project_tasks(self, project_id: int) -> list[dict[str, Any]]:
        self._record("list_project_tasks", int(project_id))
        return [dict(task) for task in self.project_tasks.get(int(project_id), [])]

    def delete_task(self, project_id: int, task_id: str) -> None:
        task_id = str(task_id)
        self._record("delete_task", project_id, task_id)
        self.task_statuses.pop(task_id, None)

    def download_asset_safe(
        self,
        project_id: int,
        task_id: str,
        asset_type: str,
        out_file: str | Path,
    ) -> bool:
        self._record(
            "download_asset_safe",
            project_id,
            str(task_id),
            asset_type,
            str(out_file),
        )
        return True

    def download_all_assets_safe(
        self,
        project_id: int,
        task_id: str,
        out_file: str | Path,
    ) -> bool:
        self._record(
            "download_all_assets_safe",
            project_id,
            str(task_id),
            str(out_file),
        )
        return True
