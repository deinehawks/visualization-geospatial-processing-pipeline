from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import logging
import requests


class WebODMProcessor:
    def __init__(self, url: str, username: str, password: str, logger: logging.Logger):
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.logger = logger

        self.token: Optional[str] = None
        self.headers: Dict[str, str] = {}
        self.processing_log: list[dict] = []

        # use one session for all requests
        self.session = requests.Session()

        self.authenticate()

    # -------------------------
    # Auth / HTTP
    # -------------------------

    def authenticate(self) -> None:
        self.logger.info("Authenticating with WebODM")

        resp = self.session.post(
            f"{self.base_url}/api/token-auth/",
            data={"username": self.username, "password": self.password},
            timeout=60,
        )
        resp.raise_for_status()

        self.token = resp.json().get("token")
        if not self.token:
            raise RuntimeError("WebODM auth succeeded but no token returned.")

        self.headers = {"Authorization": f"JWT {self.token}"}
        self.logger.info("Authentication successful")

    # -------------------------
    # Projects / Tasks
    # -------------------------

    def create_project(self, name: str, description: str = "") -> int:
        self.logger.info(f"Creating project: {name}")

        resp = self.session.post(
            f"{self.base_url}/api/projects/",
            headers=self.headers,
            data={"name": name, "description": description},
            timeout=60,
        )
        resp.raise_for_status()

        project_id = resp.json()["id"]
        self.logger.info(f"Project created (ID={project_id})")
        return int(project_id)

    def create_task_with_images(
        self,
        project_id: int,
        name: str,
        image_folder: str,
        options: Optional[dict] = None,
    ) -> str:
        self.logger.info(f"Creating task: {name}")
        self.logger.info(f"Image source: {image_folder}")

        folder = Path(image_folder)
        image_files = sorted(
            list(folder.glob("*.jpg"))
            + list(folder.glob("*.JPG"))
            + list(folder.glob("*.jpeg"))
            + list(folder.glob("*.JPEG"))
        )

        if not image_files:
            self.logger.error("No JPG images found")
            raise ValueError(f"No JPG images found in {image_folder}")

        self.logger.info(f"Found {len(image_files)} images")

        files = []
        opened = []

        try:
            for img in image_files:
                f = open(img, "rb")
                opened.append(f)
                files.append(("images", (img.name, f, "image/jpeg")))

            data: Dict[str, Any] = {"name": name}

            if options:
                formatted_options = [{"name": k, "value": v} for k, v in options.items()]
                data["options"] = json.dumps(formatted_options)

            resp = self.session.post(
                f"{self.base_url}/api/projects/{project_id}/tasks/",
                headers=self.headers,
                files=files,
                data=data,
                timeout=(60, 7200) # (connect_timeout, read_timeout)
            )
            resp.raise_for_status()

            task_id = resp.json()["id"]
            self.logger.info(f"Task created (ID={task_id})")
            self.logger.info("Image upload complete")
            return str(task_id)

        except requests.HTTPError:
            self.logger.exception("Failed to create task")
            raise

        finally:
            for f in opened:
                try:
                    f.close()
                except Exception:
                    pass

    def get_task(self, project_id: int, task_id: str) -> dict:
        """Canonical task fetch. Use this everywhere."""
        resp = self.session.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/",
            headers=self.headers,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def get_task_output(self, project_id: int, task_id: str) -> str:
        resp = self.session.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/output/",
            headers=self.headers,
            timeout=60,
        )
        return resp.text if resp.status_code == 200 else ""

    # -------------------------
    # Wait / Progress
    # -------------------------

    @staticmethod
    def fmt_elapsed(seconds: float) -> str:
        s = int(seconds)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m {s}s"
        if m:
            return f"{m}m {s}s"
        return f"{s}s"
    
    @staticmethod
    def _normalize_status(raw_status) -> tuple[str, bool]:
        """
        Returns (label, is_terminal)

        WebODM can return:
        - int codes (e.g., 40)
        - dict with label/name
        - string labels
        """
        # Most common numeric status codes in WebODM/NodeODM integrations
        code_map = {
            10: "created",
            20: "queued",
            30: "running",
            40: "completed",
            50: "failed",
            60: "canceled",
        }

        # int status
        if isinstance(raw_status, int):
            label = code_map.get(raw_status, f"status_{raw_status}")
            return label, label in ("completed", "failed", "canceled")

        # sometimes float (rare)
        if isinstance(raw_status, float) and raw_status.is_integer():
            label = code_map.get(int(raw_status), f"status_{int(raw_status)}")
            return label, label in ("completed", "failed", "canceled")

        # dict status (some versions)
        if isinstance(raw_status, dict):
            label = raw_status.get("label") or raw_status.get("name") or raw_status.get("code")
            if label is None:
                return "unknown", False
            label = str(label).lower()
            return label, label in ("completed", "failed", "canceled", "cancelled")

        # string status
        if isinstance(raw_status, str):
            label = raw_status.lower().strip()
            if label == "cancelled":
                label = "canceled"
            return label, label in ("completed", "failed", "canceled")

        return "unknown", False


    def wait_for_completion(
        self,
        project_id: int,
        task_id: str,
        poll_seconds: int = 10,
        *,
        live: bool = True,
        log_every_seconds: int = 600,     # write a normal log line every 10 minutes
        pt_log_step_seconds: int = 300,   # only log processing_time when it jumps by >= 5 minutes
    ) -> Tuple[bool, float, dict]:
        """
        Poll until task is completed/failed/canceled.

        Improvements:
        - Optional live single-line terminal output (no log spam)
        - Throttled logs: only on status change + periodic checkpoints
        - processing_time logs only when it advances by pt_log_step_seconds
        """
        start = time.time()

        last_status: Optional[str] = None
        last_progress: Optional[float] = None
        last_upload_progress: Optional[float] = None
        last_processing_time_bucket: Optional[int] = None
        last_checkpoint = 0.0  # elapsed seconds
        last_line_len = 0

        def _emit_live(line: str) -> None:
            """Write one updating line to stdout (TTY only)."""
            nonlocal last_line_len
            try:
                # pad with spaces to fully overwrite previous line
                pad = " " * max(0, last_line_len - len(line))
                print("\r" + line + pad, end="", flush=True)
                last_line_len = len(line)
            except Exception:
                # if stdout isn't writable for some reason, silently ignore
                pass

        def _finalize_live() -> None:
            """End the live line with newline."""
            if live:
                try:
                    print()  # newline
                except Exception:
                    pass

        while True:
            task = self.get_task(project_id, task_id)

            raw_status = task.get("status")
            status, is_terminal = self._normalize_status(raw_status)

            progress = task.get("progress")
            processing_time = task.get("processing_time")
            upload_progress = task.get("upload_progress")
            images_count = task.get("images_count")

            elapsed = time.time() - start

            # ---- live UI (updates every poll, but does not flood logs) ----
            if live:
                pt_txt = f"pt={int(processing_time)}s" if isinstance(processing_time, (int, float)) else "pt=?"
                up_txt = f"up={float(upload_progress):.0f}%" if isinstance(upload_progress, (int, float)) else ""
                pr_txt = f"p={float(progress):.0f}%" if isinstance(progress, (int, float)) else ""
                imgs_txt = f"{images_count} imgs" if images_count is not None else ""
                parts = [f"WebODM {status.upper()}", imgs_txt, self.fmt_elapsed(elapsed), pt_txt, up_txt, pr_txt]
                line = " | ".join([p for p in parts if p])
                _emit_live(line)

            # ---- log only when status changes ----
            if status != last_status:
                self.logger.info(
                    f"WebODM status: {status.upper()} (raw={raw_status}) | elapsed={self.fmt_elapsed(elapsed)}"
                )
                last_status = status

            # ---- throttle processing_time logs by bucket ----
            if isinstance(processing_time, (int, float)):
                pt = int(float(processing_time))
                bucket = pt // int(pt_log_step_seconds)
                if last_processing_time_bucket is None:
                    last_processing_time_bucket = bucket
                elif bucket > last_processing_time_bucket:
                    # Only log each bucket jump (ex: every +300s)
                    self.logger.info(
                        f"WebODM processing_time: {pt}s"
                        + (f" | images={images_count}" if images_count is not None else "")
                        + f" | elapsed={self.fmt_elapsed(elapsed)}"
                    )
                    last_processing_time_bucket = bucket

            # ---- progress logs: only if it changes meaningfully (and not always 0) ----
            if isinstance(progress, (int, float)):
                prog_val = float(progress)
                # only log when it changes by >= 1%
                if last_progress is None or abs(prog_val - last_progress) >= 1.0:
                    self.logger.info(f"WebODM progress: {prog_val:.0f}% | elapsed={self.fmt_elapsed(elapsed)}")
                    last_progress = prog_val

            if isinstance(upload_progress, (int, float)):
                up = float(upload_progress)
                if last_upload_progress is None or abs(up - last_upload_progress) >= 5.0:
                    self.logger.info(f"WebODM upload_progress: {up:.0f}% | elapsed={self.fmt_elapsed(elapsed)}")
                    last_upload_progress = up

            # ---- periodic checkpoint log (for long runs) ----
            if (elapsed - last_checkpoint) >= float(log_every_seconds):
                self.logger.info(f"WebODM checkpoint | status={status} | elapsed={self.fmt_elapsed(elapsed)}")
                last_checkpoint = elapsed

            # ---- optional message (only on status change or checkpoint-ish) ----
            msg = task.get("message") or task.get("last_error") or ""
            if isinstance(msg, str) and msg.strip():
                # only print with checkpoints or when status changes already triggered
                if (elapsed - last_checkpoint) < 1e-6 or status != last_status:
                    self.logger.info(f"WebODM message: {msg.strip()}")

            if is_terminal:
                _finalize_live()
                success = status == "completed"
                self.logger.info(
                    f"WebODM finished: {status.upper()} | elapsed={self.fmt_elapsed(elapsed)} | task_id={task_id}"
                )
                return success, elapsed, task

            time.sleep(poll_seconds)
            
    # -------------------------
    # Documentation helpers (kept)
    # -------------------------

    def _format_time(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        if minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def _extract_statistics(self, task_info: dict) -> dict:
        stats = {}
        if "statistics" in task_info:
            stats = task_info["statistics"]
        stats["images_count"] = task_info.get("images_count", 0)
        stats["upload_progress"] = task_info.get("upload_progress", 0)
        return stats

    def log_task_details(
        self,
        task_name: str,
        project_id: int,
        task_id: str,
        options: dict,
        runtime: float,
        success: bool,
        task_info: dict,
        image_count: int,
    ):
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "task_name": task_name,
            "project_id": project_id,
            "task_id": task_id,
            "success": success,
            "runtime_seconds": round(runtime, 2),
            "runtime_formatted": self._format_time(runtime),
            "image_count": image_count,
            "options": options,
            "processing_time": task_info.get("processing_time", 0),
            "status": task_info.get("status", {}),
            "statistics": self._extract_statistics(task_info),
            "webodm_url": f"{self.base_url}/dashboard/{project_id}/task/{task_id}",
        }
        self.processing_log.append(log_entry)
        return log_entry

    def save_documentation(self, output_folder: str, project_name: str) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(output_folder, exist_ok=True)

        json_file = os.path.join(output_folder, f"processing_log_{timestamp}.json")
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(
                {"project_name": project_name, "processing_date": datetime.now().isoformat(), "tasks": self.processing_log},
                f,
                indent=2,
            )
        self.logger.info(f"Detailed log saved to: {json_file}")

        csv_file = os.path.join(output_folder, f"processing_summary_{timestamp}.csv")
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            if self.processing_log:
                fieldnames = ["timestamp", "task_name", "project_id", "task_id", "success", "runtime_formatted", "image_count", "webodm_url"]
                option_keys = list((self.processing_log[0].get("options") or {}).keys())
                fieldnames.extend([f"option_{k}" for k in option_keys])

                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for log in self.processing_log:
                    row = {
                        "timestamp": log["timestamp"],
                        "task_name": log["task_name"],
                        "project_id": log["project_id"],
                        "task_id": log["task_id"],
                        "success": log["success"],
                        "runtime_formatted": log["runtime_formatted"],
                        "image_count": log["image_count"],
                        "webodm_url": log["webodm_url"],
                    }
                    for key in option_keys:
                        row[f"option_{key}"] = (log.get("options") or {}).get(key, "")
                    writer.writerow(row)

        self.logger.info(f"Summary saved to: {csv_file}")

    def restart_task(
        self,
        project_id: int,
        task_id: str,
        restart_from: str = "load_dataset",
    ) -> dict:
        """
        Restart an existing WebODM task from a processing stage WITHOUT re-uploading images.

        Common restart_from values (depends on WebODM version/build):
        - "load_dataset"
        - "structure_from_motion"
        - "multi_view_stereo"
        - "texturing"

        Returns JSON response (if any).
        """
        self.logger.warning(f"Restarting WebODM task {task_id} from '{restart_from}'")

        url = f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/restart/"

        # WebODM typically expects JSON. Some builds accept form data.
        payload_json = {"restart_from": restart_from}
        payload_form = {"restart_from": restart_from}

        # Try JSON first
        resp = self.session.post(
            url,
            headers=self.headers,
            json=payload_json,
            timeout=60,
        )

        # If backend doesn't like JSON, retry with form data once
        if resp.status_code in (400, 415):
            self.logger.warning(f"Restart JSON payload rejected (status={resp.status_code}). Retrying as form-data...")
            resp = self.session.post(
                url,
                headers=self.headers,
                data=payload_form,
                timeout=60,
            )

        resp.raise_for_status()

        try:
            return resp.json()
        except Exception:
            return {"ok": True, "status_code": resp.status_code}


def find_geojson_file(folder_path: str, logger: logging.Logger) -> Optional[str]:
    folder = Path(folder_path)
    if not folder.exists():
        return None

    if folder.is_file():
        return str(folder) if folder.suffix.lower() in (".geojson", ".json") else None

    geojson_files = list(folder.rglob("*.geojson")) + list(folder.rglob("*.GeoJSON"))
    if geojson_files:
        logger.info(f"Found GeoJSON file: {geojson_files[0]}")
        return str(geojson_files[0])

    json_files = list(folder.rglob("*.json")) + list(folder.rglob("*.JSON"))
    for jf in json_files:
        try:
            head = jf.read_text(encoding="utf-8", errors="ignore")[:200]
            if "FeatureCollection" in head or "geometry" in head:
                logger.info(f"Found GeoJSON-like JSON: {jf}")
                return str(jf)
        except Exception:
            pass

    return None

 