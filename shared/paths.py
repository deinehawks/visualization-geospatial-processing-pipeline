from pathlib import Path

def project_root() -> Path:
    # shared/paths.py -> shared -> project root
    return Path(__file__).resolve().parents[1]

def data_dir(root: Path | None = None) -> Path:
    root = root or project_root()
    return root / "data"

def logs_dir(root: Path | None = None) -> Path:
    return data_dir(root) / "logs"

def db_path(root: Path | None = None) -> Path:
    return data_dir(root) / "pipeline.db"

def survey_dir(survey_id: str, root: Path | None = None) -> Path:
    return data_dir(root) / "surveys" / survey_id

def survey_logs_dir(survey_id: str, root: Path | None = None) -> Path:
    return logs_dir(root) / survey_id
