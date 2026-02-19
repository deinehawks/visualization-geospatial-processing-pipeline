SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    survey_id TEXT,                -- nullable until segregation completes
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    total_runtime_seconds REAL,
    source_dir TEXT,
    surveys_root TEXT,
    year INTEGER
);

CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    runtime_seconds REAL,
    error_message TEXT,
    output_json TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_stages_run_stage
ON stages(run_id, stage_name);

CREATE TABLE IF NOT EXISTS surveys (
    survey_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    total_runtime_seconds REAL
);

CREATE TABLE IF NOT EXISTS webodm_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    survey_id TEXT,                -- optional but handy
    project_id INTEGER,
    task_id INTEGER,
    task_name TEXT,
    success INTEGER,
    runtime_seconds REAL,
    created_at TEXT,
    options_json TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_webodm_tasks_run
ON webodm_tasks(run_id);
"""
