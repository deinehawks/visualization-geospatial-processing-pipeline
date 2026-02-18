# shared/paths.py
from pathlib import Path

def project_root() -> Path:
    # adjust if you run scripts from a different cwd
    return Path(__file__).resolve().parents[1]

def data_dir() -> Path:
    return project_root() / "data"

def logs_dir() -> Path:
    return data_dir() / "logs"
