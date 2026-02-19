import requests
import os
import time
import json
import csv
from pathlib import Path
from typing import Optional
from datetime import datetime
import logging

class WebODMProcessor:
    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        logger: logging.Logger
    ):
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.token = None
        self.headers = {}
        self.processing_log = []
        self.logger = logger

        self.authenticate()
    
    def authenticate(self):
        self.logger.info("Authenticating with WebODM")

        response = requests.post(
            f"{self.base_url}/api/token-auth/",
            data={"username": self.username, "password": self.password}
        )

        response.raise_for_status()

        self.token = response.json()["token"]
        self.headers = {"Authorization": f"JWT {self.token}"}

        self.logger.info("Authentication successful")

    
    def create_project(self, name: str, description: str = "") -> int:
        self.logger.info(f"Creating project: {name}")

        response = requests.post(
            f"{self.base_url}/api/projects/",
            headers=self.headers,
            data={"name": name, "description": description}
        )

        response.raise_for_status()
        project_id = response.json()["id"]

        self.logger.info(f"Project created (ID={project_id})")
        return project_id

    
    def create_task_with_images(
        self,
        project_id: int,
        name: str,
        image_folder: str,
        options: dict = None
    ) -> int:

        self.logger.info(f"Creating task: {name}")
        self.logger.info(f"Image source: {image_folder}")

        image_files = list(Path(image_folder).glob("*.jpg")) + \
                      list(Path(image_folder).glob("*.JPG")) + \
                      list(Path(image_folder).glob("*.jpeg"))

        if not image_files:
            self.logger.error("No JPG images found")
            raise ValueError(f"No JPG images found in {image_folder}")

        self.logger.info(f"Found {len(image_files)} images")

        files = []
        for img_file in image_files:
            files.append(("images", (img_file.name, open(img_file, "rb"), "image/jpeg")))

        data = {"name": name}

        if options:
            formatted_options = [{"name": k, "value": v} for k, v in options.items()]
            data["options"] = json.dumps(formatted_options)

        try:
            response = requests.post(
                f"{self.base_url}/api/projects/{project_id}/tasks/",
                headers=self.headers,
                files=files,
                data=data
            )

            for _, file_tuple in files:
                file_tuple[1].close()

            response.raise_for_status()
            task_id = response.json()["id"]

            self.logger.info(f"Task created (ID={task_id})")
            self.logger.info("Image upload complete")

            return task_id

        except requests.exceptions.HTTPError as e:
            for _, file_tuple in files:
                try:
                    file_tuple[1].close()
                except Exception:
                    pass

            self.logger.exception("Failed to create task")
            raise

    
    def upload_images(self, project_id: int, task_id: int, image_folder: str):
        """Upload images to an existing task (legacy method, prefer create_task_with_images)."""
        self.logger.info(f"Uploading images from: {image_folder}")
        
        image_files = list(Path(image_folder).glob("*.jpg")) + \
                     list(Path(image_folder).glob("*.JPG")) + \
                     list(Path(image_folder).glob("*.jpeg"))
        
        if not image_files:
            raise ValueError(f"No JPG images found in {image_folder}")
        
        self.logger.info(f"Found {len(image_files)} images")
        
        files = []
        for img_file in image_files:
            files.append(("images", (img_file.name, open(img_file, "rb"), "image/jpeg")))
        
        response = requests.post(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/upload/",
            headers=self.headers,
            files=files
        )
        
        # Close file handles
        for _, file_tuple in files:
            file_tuple[1].close()
        
        response.raise_for_status()
        self.logger.info(f"✓ Uploaded {len(image_files)} images")
    
    def commit_task(self, project_id: int, task_id: int):
        """Start processing a task (auto-processing is default, so this may not be needed)."""
        self.logger.info(f"Committing task for processing...")
        response = requests.post(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/commit/",
            headers=self.headers
        )
        response.raise_for_status()
        self.logger.info("Task committed for processing")
    
    def get_task_status(self, project_id: int, task_id: int) -> dict:
        """Get the current status of a task."""
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def wait_for_completion(
        self,
        project_id: int,
        task_id: int,
        check_interval: int = 30
    ):
        self.logger.info("Waiting for task completion")
        start_time = time.time()

        while True:
            try:
                status_info = self.get_task_status(project_id, task_id)

                status_dict = status_info.get("status")
                if isinstance(status_dict, dict):
                    status = status_dict.get("code")
                else:
                    status = status_dict

                elapsed = time.time() - start_time

                if status == 40:
                    runtime = elapsed
                    self.logger.info(
                        f"Task completed in {self._format_time(runtime)}"
                    )
                    return True, runtime, status_info

                elif status == 30:
                    runtime = elapsed
                    self.logger.error(
                        f"Task failed after {self._format_time(runtime)}"
                    )
                    return False, runtime, status_info

                elif status == 50:
                    runtime = elapsed
                    self.logger.warning(
                        f"Task canceled after {self._format_time(runtime)}"
                    )
                    return False, runtime, status_info

                else:
                    progress = status_info.get("progress", 0)
                    status_text = status_info.get("status_text", "Processing")
                    self.logger.info(
                        f"{status_text} | {progress}% | "
                        f"Elapsed: {self._format_time(elapsed)}"
                    )

                    time.sleep(check_interval)

            except Exception:
                self.logger.exception("Error checking task status")
                time.sleep(check_interval)

    
    def _format_time(self, seconds: float) -> str:
        """Format seconds into readable time string."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"
    
    def get_task_info(self, project_id: int, task_id: int) -> dict:
        """Get detailed task information including statistics."""
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def get_task_output(self, project_id: int, task_id: int) -> str:
        """Get the processing output/console log."""
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/output/",
            headers=self.headers
        )
        if response.status_code == 200:
            return response.text
        return ""
    
    def log_task_details(self, task_name: str, project_id: int, task_id: int, 
                        options: dict, runtime: float, success: bool, 
                        task_info: dict, image_count: int):
        """Log task details for documentation."""
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
            "webodm_url": f"{self.base_url}/dashboard/{project_id}/task/{task_id}"
        }
        
        self.processing_log.append(log_entry)
        return log_entry
    
    def _extract_statistics(self, task_info: dict) -> dict:
        """Extract key statistics from task info."""
        stats = {}
        
        # Extract available statistics
        if "statistics" in task_info:
            stats = task_info["statistics"]
        
        # Add other useful metrics
        stats["images_count"] = task_info.get("images_count", 0)
        stats["upload_progress"] = task_info.get("upload_progress", 0)
        
        return stats
    
    def save_documentation(self, output_folder: str, project_name: str):
        """Save comprehensive documentation of the processing run."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save as JSON (complete data)
        json_file = os.path.join(output_folder, f"processing_log_{timestamp}.json")
        with open(json_file, 'w') as f:
            json.dump({
                "project_name": project_name,
                "processing_date": datetime.now().isoformat(),
                "tasks": self.processing_log
            }, f, indent=2)
        self.logger.info(f"Detailed log saved to: {json_file}")
        
        # Save as CSV (for easy viewing in Excel)
        csv_file = os.path.join(output_folder, f"processing_summary_{timestamp}.csv")
        with open(csv_file, 'w', newline='') as f:
            if self.processing_log:
                # Flatten options for CSV
                fieldnames = [
                    "timestamp", "task_name", "project_id", "task_id", 
                    "success", "runtime_formatted", "image_count", "webodm_url"
                ]
                
                # Add option fields
                first_task = self.processing_log[0]
                option_keys = list(first_task.get("options", {}).keys())
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
                        "webodm_url": log["webodm_url"]
                    }
                    
                    # Add options
                    for key in option_keys:
                        row[f"option_{key}"] = log["options"].get(key, "")
                    
                    writer.writerow(row)
        
        self.logger.info(f"Summary saved to: {csv_file}")
        
        # Save processing output logs
        self._save_output_logs(output_folder, timestamp)
        
        # Create a human-readable report
        self._create_report(output_folder, timestamp, project_name)
    
    def _save_output_logs(self, output_folder: str, timestamp: str):
        """Save console output logs for each task."""
        logs_folder = os.path.join(output_folder, "console_logs")
        os.makedirs(logs_folder, exist_ok=True)
        
        for log in self.processing_log:
            output = self.get_task_output(log["project_id"], log["task_id"])
            if output:
                log_file = os.path.join(
                    logs_folder, 
                    f"task_{log['task_id']}_{log['task_name'].replace(' ', '_')}_{timestamp}.txt"
                )
                with open(log_file, 'w') as f:
                    f.write(output)
    
    def _create_report(self, output_folder: str, timestamp: str, project_name: str):
        """Create a human-readable markdown report."""
        report_file = os.path.join(output_folder, f"processing_report_{timestamp}.md")
        
        with open(report_file, 'w') as f:
            f.write(f"# WebODM Processing Report\n\n")
            f.write(f"**Project:** {project_name}\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"---\n\n")
            
            for i, log in enumerate(self.processing_log, 1):
                f.write(f"## Task {i}: {log['task_name']}\n\n")
                f.write(f"- **Status:** {'✓ Success' if log['success'] else '✗ Failed'}\n")
                f.write(f"- **Runtime:** {log['runtime_formatted']}\n")
                f.write(f"- **Image Count:** {log['image_count']}\n")
                f.write(f"- **Task ID:** {log['task_id']}\n")
                f.write(f"- **WebODM URL:** [{self.base_url}/dashboard/{log['project_id']}/task/{log['task_id']}]({log['webodm_url']})\n\n")
                
                f.write(f"### Settings\n\n")
                f.write(f"```json\n")
                f.write(json.dumps(log['options'], indent=2))
                f.write(f"\n```\n\n")
                
                if log['statistics']:
                    f.write(f"### Statistics\n\n")
                    for key, value in log['statistics'].items():
                        f.write(f"- **{key}:** {value}\n")
                    f.write("\n")
                
                f.write(f"---\n\n")
        
        self.logger.info(f"Report saved to: {report_file}")
    
    def restart_task(self, project_id: int, task_id: int):
        """Restart a task."""
        self.logger.info("Restarting task...")
        response = requests.post(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/restart/",
            headers=self.headers
        )
        response.raise_for_status()
        self.logger.info("Task restarted")
    
    def download_asset(self, project_id: int, task_id: int, asset_type: str, output_path: str):
        """Download a specific asset."""
        self.logger.info(f"Downloading {asset_type}...")
        
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/download/{asset_type}",
            headers=self.headers,
            stream=True
        )
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        self.logger.info(f"Downloaded to: {output_path}")
    
    def download_all_assets(self, project_id: int, task_id: int, output_path: str):
        """Download all assets as a zip file."""
        self.logger.info("Downloading all assets...")
        
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/download/all.zip",
            headers=self.headers,
            stream=True
        )
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        self.logger.info(f"All assets downloaded to: {output_path}")


def find_geojson_file(folder_path: str, logger: logging.Logger) -> Optional[str]:
    folder = Path(folder_path)
    if not folder.exists():
        return None

    if folder.is_file():
        if folder.suffix.lower() in [".geojson", ".json"]:
            return str(folder)
        return None

    # Prefer .geojson
    geojson_files = list(folder.rglob("*.geojson")) + list(folder.rglob("*.GeoJSON"))
    if geojson_files:
        logger.info(f"Found GeoJSON file: {geojson_files[0]}")
        return str(geojson_files[0])

    # Fallback .json (best-effort)
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
