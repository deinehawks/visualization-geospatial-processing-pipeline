from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import csv
import json
import os
import time
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
        if raw_status is None:
            return "queued", False

        code_map = {
            10: "created",
            20: "queued",
            30: "running",
            40: "completed",
            50: "failed",
            60: "canceled",
        }

        if isinstance(raw_status, int):
            label = code_map.get(raw_status, f"status_{raw_status}")
            return label, label in ("completed", "failed", "canceled")

        if isinstance(raw_status, float) and raw_status.is_integer():
            label = code_map.get(int(raw_status), f"status_{int(raw_status)}")
            return label, label in ("completed", "failed", "canceled")

        if isinstance(raw_status, dict):
            label = raw_status.get("label") or raw_status.get("name") or raw_status.get("code")
            if label is None:
                return "unknown", False
            label = str(label).lower().strip()
            if label == "cancelled":
                label = "canceled"
            return label, label in ("completed", "failed", "canceled")

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
        *,
        poll_seconds: int = 10,
        timeout_seconds: Optional[int] = None,
        live: bool = False,
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Poll WebODM task until it reaches a terminal status:
        completed / failed / canceled

        Returns: (success, runtime_seconds, task_info)
        """
        start = time.time()
        last_status: Optional[str] = None
        last_line_len = 0

        def _emit_live(line: str) -> None:
            nonlocal last_line_len
            if not live:
                return
            try:
                pad = " " * max(0, last_line_len - len(line))
                print("\r" + line + pad, end="", flush=True)
                last_line_len = len(line)
            except Exception:
                pass

        def _finalize_live() -> None:
            if not live:
                return
            try:
                print()
            except Exception:
                pass

        while True:
            # timeout guard
            if timeout_seconds is not None and (time.time() - start) > float(timeout_seconds):
                raise TimeoutError(f"WebODM task timed out after {timeout_seconds}s (task_id={task_id})")

            task_info = self.get_task(project_id, task_id)
            status_label, is_terminal = self._normalize_status(task_info.get("status"))

            if status_label != last_status:
                self.logger.info(f"WebODM status: {status_label}")
                last_status = status_label

            elapsed = time.time() - start
            processing_time = task_info.get("processing_time")

            _emit_live(
                f"WebODM status={status_label} | processing_time={processing_time} | elapsed={self.fmt_elapsed(elapsed)}"
            )

            if is_terminal:
                _finalize_live()

                if status_label == "completed":
                    return True, elapsed, task_info

                if status_label == "canceled":
                    raise RuntimeError("WEBODM_TASK_CANCELED")

                # failed / unknown terminal
                try:
                    out = self.get_task_output(project_id, task_id)
                    if out:
                        self.logger.error(f"WebODM output tail:\n{out[-1000:]}")
                except Exception:
                    pass

                return False, elapsed, task_info

            time.sleep(poll_seconds)

    def create_task_with_images(
        self,
        project_id: int,
        name: str,
        image_folder: str,
        options: Optional[dict] = None,
        *,
        recursive: bool = False,
        progress_every_percent: float = 2.0,   # log every +2%
        live: bool = True,                     # single-line live progress in terminal
        cancel_poll_seconds: int = 3,          # how often to check WebODM for cancel
    ) -> str:
        from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
        import threading

        self.logger.info(f"Creating task: {name}")
        self.logger.info(f"Image source: {image_folder}")

        folder = Path(image_folder)
        if not folder.exists():
            raise FileNotFoundError(f"Image folder not found: {image_folder}")

        # ------------------------------------------------------------
        # Collect images (JPG/JPEG only), dedupe by case-insensitive filename
        # ------------------------------------------------------------
        if recursive:
            candidates = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")]
        else:
            candidates = []
            candidates += list(folder.glob("*.jpg"))
            candidates += list(folder.glob("*.jpeg"))
            candidates += list(folder.glob("*.JPG"))
            candidates += list(folder.glob("*.JPEG"))

        uniq: Dict[str, Path] = {}
        for p in candidates:
            key = p.name.lower()  # Windows/SMB safe dedupe
            if key not in uniq:
                uniq[key] = p

        image_files = sorted(uniq.values(), key=lambda p: p.name.lower())

        if not image_files:
            self.logger.error("No JPG/JPEG images found")
            raise ValueError(f"No JPG/JPEG images found in {image_folder}")

        # size summary
        total_bytes = 0
        for p in image_files:
            try:
                total_bytes += p.stat().st_size
            except OSError:
                pass
        gb = total_bytes / (1024**3)
        self.logger.info(f"Found {len(image_files)} unique images | approx_size={gb:.2f} GB")

        # ------------------------------------------------------------
        # Build multipart fields + progress monitor
        # ------------------------------------------------------------
        opened = []
        fields: list[tuple[str, object]] = []

        fields.append(("name", name))

        if options:
            formatted_options = [{"name": k, "value": v} for k, v in options.items()]
            fields.append(("options", json.dumps(formatted_options)))

        # ---- live one-line terminal output helpers ----
        last_line_len = 0

        def _emit_live(line: str) -> None:
            nonlocal last_line_len
            if not live:
                return
            try:
                pad = " " * max(0, last_line_len - len(line))
                print("\r" + line + pad, end="", flush=True)
                last_line_len = len(line)
            except Exception:
                pass

        def _finalize_live() -> None:
            if not live:
                return
            try:
                print()
            except Exception:
                pass

        # ------------------------------------------------------------
        # Cancel detection (best-effort)
        # ------------------------------------------------------------
        cancel_event = threading.Event()
        stop_poller = threading.Event()
        found_task_id: list[Optional[str]] = [None]  # mutable holder

        def _poll_cancel() -> None:
            """
            Poll the project task list for a task with this name.
            Once found, watch its status. If user cancels in UI -> set cancel_event.
            """
            # IMPORTANT: do NOT share self.session across threads
            poll_sess = requests.Session()
            try:
                while not stop_poller.is_set():
                    try:
                        # list tasks for project
                        r = poll_sess.get(
                            f"{self.base_url}/api/projects/{project_id}/tasks/",
                            headers=self.headers,
                            timeout=30,
                        )
                        if r.status_code != 200:
                            time.sleep(cancel_poll_seconds)
                            continue

                        payload = r.json()

                        # WebODM list endpoints sometimes return {"results":[...]}
                        tasks = payload.get("results") if isinstance(payload, dict) else payload
                        if not isinstance(tasks, list):
                            time.sleep(cancel_poll_seconds)
                            continue

                        # find by exact name (you can relax matching if needed)
                        for t in tasks:
                            if not isinstance(t, dict):
                                continue
                            if str(t.get("name", "")).strip() != name:
                                continue

                            tid = str(t.get("id") or "")
                            if tid and found_task_id[0] is None:
                                found_task_id[0] = tid

                            status, _ = self._normalize_status(t.get("status"))
                            if status == "canceled":
                                cancel_event.set()
                                return

                        time.sleep(cancel_poll_seconds)
                    except Exception:
                        # poller should never crash your upload
                        time.sleep(cancel_poll_seconds)
            finally:
                try:
                    poll_sess.close()
                except Exception:
                    pass

        poller_thread = threading.Thread(target=_poll_cancel, daemon=True)

        # ------------------------------------------------------------
        # Upload with progress + cancel abort
        # ------------------------------------------------------------
        try:
            for img in image_files:
                f = open(img, "rb")
                opened.append(f)
                fields.append(("images", (img.name, f, "image/jpeg")))

            encoder = MultipartEncoder(fields=fields)

            last_logged_percent = -1.0
            start = time.time()

            def _callback(monitor: MultipartEncoderMonitor) -> None:
                nonlocal last_logged_percent

                # If UI cancel detected (task exists + canceled), abort the upload stream
                if cancel_event.is_set():
                    raise RuntimeError("Upload aborted: task was canceled in WebODM UI.")

                if monitor.len <= 0:
                    return

                pct = (monitor.bytes_read / monitor.len) * 100.0
                elapsed = time.time() - start

                # one-line live progress
                _emit_live(f"Uploading to WebODM... {pct:6.2f}% | {self.fmt_elapsed(elapsed)}")

                # log every N% (optional, still not too spammy)
                if pct - last_logged_percent >= float(progress_every_percent) or pct >= 100.0:
                    self.logger.info(f"Upload progress: {pct:.1f}% | elapsed={self.fmt_elapsed(elapsed)}")
                    last_logged_percent = pct

            monitor = MultipartEncoderMonitor(encoder, _callback)

            headers = dict(self.headers)
            headers["Content-Type"] = monitor.content_type

            self.logger.info("Uploading images to WebODM (real progress enabled)...")
            poller_thread.start()

            resp = self.session.post(
                f"{self.base_url}/api/projects/{project_id}/tasks/",
                headers=headers,
                data=monitor,
                timeout=(60, 7200),
            )

            _finalize_live()
            resp.raise_for_status()

            task_id = str(resp.json()["id"])
            self.logger.info(f"Task created (ID={task_id})")
            self.logger.info("Image upload complete")
            return task_id

        except KeyboardInterrupt:
            _finalize_live()
            self.logger.warning("Upload interrupted by user (Ctrl+C).")
            raise

        except Exception as e:
            _finalize_live()

            # If this was triggered by UI cancel, report clearly
            if "canceled in webodm ui" in str(e).lower() or "task was canceled" in str(e).lower():
                self.logger.warning("Detected WebODM UI cancellation. Stopping upload/pipeline stage.")
                raise RuntimeError("WEBODM_TASK_CANCELED") from e

            # HTTP error info (if available)
            try:
                body = (resp.text or "")[:500]  # type: ignore[name-defined]
                self.logger.error(f"WebODM response snippet: {body}")
            except Exception:
                pass

            self.logger.exception("Failed to create task")
            raise

        finally:
            stop_poller.set()
            try:
                # give poller a moment to exit cleanly
                if poller_thread.is_alive():
                    poller_thread.join(timeout=1.0)
            except Exception:
                pass

            for f in opened:
                try:
                    f.close()
                except Exception:
                    pass
            
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
        
    def download_asset(self, project_id: int, task_id: str, asset_type: str, out_file: str) -> None:
        """
        Downloads a single asset (by asset_type) from WebODM task assets endpoint.
        NOTE: asset_type values depend on WebODM (e.g. orthophoto.tif).
        """
        url = f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/download/{asset_type}"
        with self.session.get(url, headers=self.headers, stream=True, timeout=(60, 7200)) as r:
            r.raise_for_status()
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with open(out_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

    def download_all_assets(self, project_id: int, task_id: str, out_file: str) -> None:
        """
        Downloads the full task assets zip (if your WebODM exposes it).
        Endpoint can vary by version; adjust if your API differs.
        """
        url = f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/download/all.zip"
        with self.session.get(url, headers=self.headers, stream=True, timeout=(60, 7200)) as r:
            r.raise_for_status()
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with open(out_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)


    