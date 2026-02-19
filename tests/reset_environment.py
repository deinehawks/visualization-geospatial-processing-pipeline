from pathlib import Path
import shutil

DATA_DIR = Path("data")

# Delete logs
logs_dir = DATA_DIR / "logs"
if logs_dir.exists():
    shutil.rmtree(logs_dir)
    print("Logs cleared.")

# Delete database
db_file = DATA_DIR / "pipeline.db"
if db_file.exists():
    db_file.unlink()
    print("Database cleared.")

print("Environment reset complete.")
