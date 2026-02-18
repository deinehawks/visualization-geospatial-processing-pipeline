SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS surveys (
    survey_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    total_runtime_seconds REAL
);

CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    runtime_seconds REAL,
    error_message TEXT,
    output_json TEXT,
    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id)
);

CREATE INDEX IF NOT EXISTS idx_stages_survey_stage
ON stages(survey_id, stage_name);

CREATE TABLE IF NOT EXISTS webodm_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    survey_id TEXT NOT NULL,
    project_id INTEGER,
    task_id INTEGER,
    task_name TEXT,
    success INTEGER,
    runtime_seconds REAL,
    created_at TEXT,
    options_json TEXT,
    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id)
);

CREATE INDEX IF NOT EXISTS idx_webodm_tasks_survey
ON webodm_tasks(survey_id);
"""
