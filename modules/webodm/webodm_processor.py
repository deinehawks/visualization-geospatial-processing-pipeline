import requests
import os
import time
import json
import csv
from pathlib import Path
from typing import Optional
from datetime import datetime
from dotenv import load_dotenv

class WebODMProcessor:
    def __init__(self, url: str, username: str, password: str):
        """Initialize WebODM processor with authentication."""
        self.base_url = url.rstrip('/')
        self.username = username
        self.password = password
        self.token = None
        self.headers = {}
        self.processing_log = []
        self.authenticate()
    
    def authenticate(self):
        """Authenticate with WebODM and get token."""
        print("Authenticating with WebODM...")
        response = requests.post(
            f"{self.base_url}/api/token-auth/",
            data={"username": self.username, "password": self.password}
        )
        response.raise_for_status()
        self.token = response.json()["token"]
        self.headers = {"Authorization": f"JWT {self.token}"}
        print("✓ Authentication successful")
    
    def create_project(self, name: str, description: str = "") -> int:
        """Create a new project."""
        print(f"Creating project: {name}")
        response = requests.post(
            f"{self.base_url}/api/projects/",
            headers=self.headers,
            data={"name": name, "description": description}
        )
        response.raise_for_status()
        project_id = response.json()["id"]
        print(f"✓ Project created with ID: {project_id}")
        return project_id
    
    def create_task_with_images(self, project_id: int, name: str, image_folder: str, options: dict = None) -> int:
        """Create a task and upload images in one request."""
        print(f"Creating task: {name}")
        print(f"Uploading images from: {image_folder}")
        
        # Get image files
        image_files = list(Path(image_folder).glob("*.jpg")) + \
                     list(Path(image_folder).glob("*.JPG")) + \
                     list(Path(image_folder).glob("*.jpeg"))
        
        if not image_files:
            raise ValueError(f"No JPG images found in {image_folder}")
        
        print(f"Found {len(image_files)} images")
        
        # Prepare multipart files
        files = []
        for img_file in image_files:
            files.append(("images", (img_file.name, open(img_file, "rb"), "image/jpeg")))
        
        # Prepare data
        data = {"name": name}
        
        # Add options if provided
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
            
            # Close file handles
            for _, file_tuple in files:
                file_tuple[1].close()
            
            response.raise_for_status()
            task_id = response.json()["id"]
            print(f"✓ Task created with ID: {task_id}")
            print(f"✓ Uploaded {len(image_files)} images")
            return task_id
        except requests.exceptions.HTTPError as e:
            # Close file handles on error
            for _, file_tuple in files:
                try:
                    file_tuple[1].close()
                except:
                    pass
            print(f"✗ Failed to create task: {e}")
            print(f"Response: {response.text}")
            raise
    
    def upload_images(self, project_id: int, task_id: int, image_folder: str):
        """Upload images to an existing task (legacy method, prefer create_task_with_images)."""
        print(f"Uploading images from: {image_folder}")
        
        image_files = list(Path(image_folder).glob("*.jpg")) + \
                     list(Path(image_folder).glob("*.JPG")) + \
                     list(Path(image_folder).glob("*.jpeg"))
        
        if not image_files:
            raise ValueError(f"No JPG images found in {image_folder}")
        
        print(f"Found {len(image_files)} images")
        
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
        print(f"✓ Uploaded {len(image_files)} images")
    
    def commit_task(self, project_id: int, task_id: int):
        """Start processing a task (auto-processing is default, so this may not be needed)."""
        print(f"Committing task for processing...")
        response = requests.post(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/commit/",
            headers=self.headers
        )
        response.raise_for_status()
        print("✓ Task committed for processing")
    
    def get_task_status(self, project_id: int, task_id: int) -> dict:
        """Get the current status of a task."""
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/",
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()
    
    def wait_for_completion(self, project_id: int, task_id: int, check_interval: int = 30):
        """Wait for task to complete and track runtime."""
        print("Waiting for task to complete...")
        start_time = time.time()
        
        while True:
            try:
                status_info = self.get_task_status(project_id, task_id)
                
                # Handle status - it could be directly a code or nested in a dict
                status_dict = status_info.get("status")
                if isinstance(status_dict, dict):
                    status = status_dict.get("code")
                elif isinstance(status_dict, int):
                    status = status_dict
                else:
                    # Fallback: check if status code is at root level
                    status = status_info.get("status_code")
                
                if status == 40:  # Completed
                    end_time = time.time()
                    runtime = end_time - start_time
                    print(f"✓ Task completed successfully in {self._format_time(runtime)}")
                    return True, runtime, status_info
                elif status == 30:  # Failed
                    end_time = time.time()
                    runtime = end_time - start_time
                    print(f"✗ Task failed after {self._format_time(runtime)}")
                    print(f"Error: {status_info.get('last_error', 'Unknown error')}")
                    return False, runtime, status_info
                elif status == 50:  # Canceled
                    end_time = time.time()
                    runtime = end_time - start_time
                    print(f"✗ Task was canceled after {self._format_time(runtime)}")
                    return False, runtime, status_info
                else:
                    progress = status_info.get("progress", 0)
                    elapsed = time.time() - start_time
                    current_status = status_info.get("status_text", "Processing")
                    print(f"{current_status}... {progress}% (Elapsed: {self._format_time(elapsed)})")
                    time.sleep(check_interval)
            except Exception as e:
                print(f"Error checking status: {e}")
                print("Retrying in {check_interval} seconds...")
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
        print(f"✓ Detailed log saved to: {json_file}")
        
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
        
        print(f"✓ Summary saved to: {csv_file}")
        
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
        
        print(f"✓ Report saved to: {report_file}")
    
    def restart_task(self, project_id: int, task_id: int):
        """Restart a task."""
        print("Restarting task...")
        response = requests.post(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/restart/",
            headers=self.headers
        )
        response.raise_for_status()
        print("✓ Task restarted")
    
    def download_asset(self, project_id: int, task_id: int, asset_type: str, output_path: str):
        """Download a specific asset."""
        print(f"Downloading {asset_type}...")
        
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/download/{asset_type}",
            headers=self.headers,
            stream=True
        )
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print(f"✓ Downloaded to: {output_path}")
    
    def download_all_assets(self, project_id: int, task_id: int, output_path: str):
        """Download all assets as a zip file."""
        print("Downloading all assets...")
        
        response = requests.get(
            f"{self.base_url}/api/projects/{project_id}/tasks/{task_id}/download/all.zip",
            headers=self.headers,
            stream=True
        )
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print(f"✓ All assets downloaded to: {output_path}")


def find_geojson_file(folder_path: str) -> str:
    """Find a GeoJSON file in the specified folder or its subfolders."""
    folder = Path(folder_path)
    
    if not folder.exists():
        return None
    
    # If it's a file and ends with .geojson or .json, return it
    if folder.is_file():
        if folder.suffix.lower() in ['.geojson', '.json']:
            return str(folder)
        return None
    
    # If it's a directory, search for GeoJSON files
    if folder.is_dir():
        # First, look for .geojson files
        geojson_files = list(folder.glob("*.geojson")) + list(folder.glob("*.GeoJSON"))
        if geojson_files:
            print(f"Found GeoJSON file: {geojson_files[0]}")
            return str(geojson_files[0])
        
        # If no .geojson, look for .json files
        json_files = list(folder.glob("*.json")) + list(folder.glob("*.JSON"))
        if json_files:
            # Try to verify it's actually a GeoJSON by checking content
            for json_file in json_files:
                try:
                    with open(json_file, 'r') as f:
                        content = f.read(100)  # Read first 100 chars
                        if 'FeatureCollection' in content or 'Feature' in content or 'geometry' in content:
                            print(f"Found GeoJSON file: {json_file}")
                            return str(json_file)
                except:
                    continue
        
        # Look in subfolders
        for subfolder in folder.iterdir():
            if subfolder.is_dir():
                result = find_geojson_file(str(subfolder))
                if result:
                    return result
    
    return None


def main():
    # Configuration
    WEBODM_URL = "http://localhost:8000"  # Change to your WebODM URL
    USERNAME = "your_username"
    PASSWORD = "your_password"
    IMAGE_FOLDER = "path/to/your/jpg/images"
    GEOJSON_FILE = "path/to/boundary.geojson"  # For Task 2
    OUTPUT_FOLDER = "outputs"
    PROJECT_NAME = "RGB Processing Project"
    
    # Create output folder
    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    
    # Initialize processor
    processor = WebODMProcessor(WEBODM_URL, USERNAME, PASSWORD)
    
    # Count images
    image_files = list(Path(IMAGE_FOLDER).glob("*.jpg")) + \
                  list(Path(IMAGE_FOLDER).glob("*.JPG")) + \
                  list(Path(IMAGE_FOLDER).glob("*.jpeg"))
    image_count = len(image_files)
    print(f"\nFound {image_count} images to process\n")
    
    # Create project
    project_id = processor.create_project(
        name=PROJECT_NAME,
        description=f"Unbounded orthomosaic and bounded 2D/3D models - {datetime.now().strftime('%Y-%m-%d')}"
    )
    
    # ========== TASK 1: Unbounded Orthomosaic ==========
    if RUN_TASK_1:
        print("\n" + "="*60)
        print("TASK 1: Processing Unbounded Orthomosaic")
        print("="*60 + "\n")
        
        task1_options = {
            "auto-boundary": True,
            "fast-orthophoto": True,
            "mesh-size": 300000,
            "min-num-features": 20000,
            "orthophoto-resolution": 1,
            "pc-quality": "high",
            "skip-3dmodel": True
        }
        
        # Display settings
        print("Settings:")
        for key, value in task1_options.items():
            print(f"  - {key}: {value}")
        print()
        
        # Create task with images and options in one request (correct workflow)
        task1_id = processor.create_task_with_images(
            project_id, 
            "Task 1: Unbounded Orthomosaic",
            IMAGE_FOLDER,
            task1_options
        )
        
        # Wait for Task 1 to complete
        task1_success, task1_runtime, task1_info = processor.wait_for_completion(project_id, task1_id)
        
        # Log task details
        processor.log_task_details(
            "Task 1: Unbounded Orthomosaic",
            project_id, task1_id, task1_options,
            task1_runtime, task1_success, task1_info,
            image_count
        )
        
        if task1_success:
            print(f"\n✓ View Task 1 in WebODM: {WEBODM_URL}/dashboard/{project_id}/task/{task1_id}")
            
            # Quality check pause
            if QUALITY_CHECK_MODE:
                print("\n" + "="*60)
                print("QUALITY CHECK - TASK 1")
                print("="*60)
                print("\n📋 Please review the output in WebODM:")
                print(f"   {WEBODM_URL}/dashboard/{project_id}/task/{task1_id}")
                print("\n✓ Check for:")
                print("   - Visual defects or gaps")
                print("   - Proper alignment")
                print("   - Color consistency")
                print("   - Coverage completeness")
                print("\nOptions:")
                print("   [d] Download orthomosaic now")
                print("   [r] Restart task with same settings")
                print("   [s] Skip and continue to next task")
                print("   [q] Quit script")
                
                while True:
                    choice = input("\nYour choice (d/r/s/q): ").lower().strip()
                    
                    if choice == 'd':
                        # Download orthomosaic
                        processor.download_asset(
                            project_id, task1_id, 
                            "orthophoto.tif",
                            os.path.join(OUTPUT_FOLDER, "task1_orthomosaic_4326.tif")
                        )
                        print("\n✓ Continue to next task? (y/n)")
                        if input().lower() == 'n':
                            print("Exiting script. You can run it again later.")
                            return
                        break
                    elif choice == 'r':
                        print("\nRestarting Task 1...")
                        processor.restart_task(project_id, task1_id)
                        task1_success, task1_runtime, task1_info = processor.wait_for_completion(project_id, task1_id)
                        processor.log_task_details(
                            "Task 1: Unbounded Orthomosaic (Restarted)",
                            project_id, task1_id, task1_options,
                            task1_runtime, task1_success, task1_info,
                            image_count
                        )
                        if task1_success:
                            print(f"\n✓ Task 1 completed after restart")
                        else:
                            print(f"\n✗ Task 1 failed after restart")
                        # Loop back to quality check
                        continue
                    elif choice == 's':
                        print("\nSkipping download, continuing to next task...")
                        break
                    elif choice == 'q':
                        print("\nExiting script. Tasks will continue processing in WebODM.")
                        print("Run the script again when ready to continue.")
                        return
                    else:
                        print("Invalid choice. Please enter d, r, s, or q")
            else:
                # Auto-download if not in quality check mode
                processor.download_asset(
                    project_id, task1_id, 
                    "orthophoto.tif",
                    os.path.join(OUTPUT_FOLDER, "task1_orthomosaic_4326.tif")
                )
        else:
            print("\n⚠ Task 1 failed. Please check the WebODM interface for visual defects.")
            print(f"View in WebODM: {WEBODM_URL}/dashboard/{project_id}/task/{task1_id}")
            print("You can restart the task from the WebODM interface if needed.")
    else:
        print("\n" + "="*60)
        print("TASK 1: SKIPPED (RUN_TASK_1 = False)")
        print("="*60 + "\n")
        # Optionally, you can enable auto-restart here
        # processor.restart_task(project_id, task1_id)
        # task1_success, task1_runtime, task1_info = processor.wait_for_completion(project_id, task1_id)
    
    # ========== TASK 2: Bounded 2D/3D Models ==========
    print("\n" + "="*60)
    print("TASK 2: Processing Bounded 2D/3D Models")
    print("="*60 + "\n")
    
    # Read GeoJSON boundary file
    boundary_geojson = None
    actual_geojson_path = None
    
    # Try to find GeoJSON file
    if GEOJSON_FILE:
        print(f"Looking for GeoJSON file in: {GEOJSON_FILE}")
        actual_geojson_path = find_geojson_file(GEOJSON_FILE)
        
        if actual_geojson_path:
            try:
                with open(actual_geojson_path, 'r') as f:
                    boundary_geojson = f.read()
                print(f"✓ Loaded boundary from: {actual_geojson_path}")
            except Exception as e:
                print(f"⚠ Warning: Failed to read GeoJSON file: {e}")
                boundary_geojson = None
        else:
            print(f"⚠ Warning: No GeoJSON file found in {GEOJSON_FILE}")
            print("\nTo create a boundary file:")
            print("  1. Open Task 1 in WebODM")
            print("  2. Use the map tools to draw a polygon")
            print("  3. Export as GeoJSON")
            print("  4. Save it in the boundary folder")
    else:
        print("⚠ Warning: GEOJSON_FILE not specified")
        print("Skipping Task 2. Set GEOJSON_FILE to enable bounded processing.")
    
    if boundary_geojson:
        task2_options = {
            "boundary": boundary_geojson,
            "dem-resolution": 1,
            "dsm": True,
            "dtm": True,
            "mesh-size": 300000,
            "min-num-features": 20000,
            "orthophoto-resolution": 1,
            "pc-quality": "high"
        }
        
        # Display settings (excluding large boundary)
        print("Settings:")
        for key, value in task2_options.items():
            if key == "boundary":
                print(f"  - {key}: [GeoJSON boundary loaded]")
            else:
                print(f"  - {key}: {value}")
        print()
        
        # Create task with images and options in one request
        task2_id = processor.create_task_with_images(
            project_id,
            "Task 2: Bounded 2D/3D Models",
            IMAGE_FOLDER,
            task2_options
        )
        
        # Wait for Task 2 to complete
        task2_success, task2_runtime, task2_info = processor.wait_for_completion(project_id, task2_id)
        
        # Log task details
        task2_options_log = task2_options.copy()
        task2_options_log["boundary"] = "[GeoJSON boundary]"  # Don't log full GeoJSON
        processor.log_task_details(
            "Task 2: Bounded 2D/3D Models",
            project_id, task2_id, task2_options_log,
            task2_runtime, task2_success, task2_info,
            image_count
        )
        
        if task2_success:
            print(f"\n✓ View Task 2 in WebODM: {WEBODM_URL}/dashboard/{project_id}/task/{task2_id}")
            print(f"✓ View 3D Model: {WEBODM_URL}/3d/{project_id}/task/{task2_id}")
            
            # Download various outputs
            print("\nDownloading Task 2 outputs...")
            
            # Digital Surface Model (EPSG:3857, GeoTIFF RGB)
            processor.download_asset(
                project_id, task2_id,
                "dsm.tif",
                os.path.join(OUTPUT_FOLDER, "task2_dsm_3857.tif")
            )
            
            # Digital Terrain Model (EPSG:3857, GeoTIFF RGB)
            processor.download_asset(
                project_id, task2_id,
                "dtm.tif",
                os.path.join(OUTPUT_FOLDER, "task2_dtm_3857.tif")
            )
            
            # Point Cloud (LAZ format)
            processor.download_asset(
                project_id, task2_id,
                "georeferenced_model.laz",
                os.path.join(OUTPUT_FOLDER, "task2_pointcloud.laz")
            )
            
            # Orthomosaic
            processor.download_asset(
                project_id, task2_id,
                "orthophoto.tif",
                os.path.join(OUTPUT_FOLDER, "task2_orthomosaic.tif")
            )
            
            # Download all assets (raw formats)
            processor.download_all_assets(
                project_id, task2_id,
                os.path.join(OUTPUT_FOLDER, "task2_all_assets.zip")
            )
            
            print("\n✓ All downloads complete!")
        else:
            print("\n⚠ Task 2 failed. Please check the WebODM interface for visual defects.")
            print(f"View in WebODM: {WEBODM_URL}/dashboard/{project_id}/task/{task2_id}")
            print("You can restart the task from the WebODM interface if needed.")
    
    # ========== SAVE DOCUMENTATION ==========
    print("\n" + "="*60)
    print("SAVING DOCUMENTATION")
    print("="*60 + "\n")
    
    processor.save_documentation(OUTPUT_FOLDER, PROJECT_NAME)
    
    # ========== SUMMARY ==========
    print("\n" + "="*60)
    print("PROCESSING COMPLETE")
    print("="*60)
    print(f"\nProject: {PROJECT_NAME}")
    print(f"Project ID: {project_id}")
    print(f"Images Processed: {image_count}")
    print(f"\nWebODM Dashboard: {WEBODM_URL}/dashboard/{project_id}")
    print(f"Outputs saved to: {OUTPUT_FOLDER}")
    print("\nDocumentation files generated:")
    print("  - processing_log_*.json (detailed data)")
    print("  - processing_summary_*.csv (Excel-friendly)")
    print("  - processing_report_*.md (human-readable)")
    print("  - console_logs/ (task output logs)")
    print("\n" + "="*60)


if __name__ == "__main__":
    main()