SCHEMA_SQL = """
-- ============================================================
-- RUNS
-- ============================================================
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    survey_id TEXT,                 -- nullable until segregation completes

    -- status: running | paused | completed | failed
    status TEXT NOT NULL,

    started_at TEXT,
    finished_at TEXT,               -- used for completed/failed; can also store pause time if you want
    total_runtime_seconds REAL,

    -- pause metadata (optional but useful)
    paused_at TEXT,
    paused_after_stage TEXT,
    pause_reason TEXT,

    source_dir TEXT,
    surveys_root TEXT,
    year INTEGER
);

CREATE INDEX IF NOT EXISTS idx_runs_status
ON runs(status);

-- ============================================================
-- STAGES
-- ============================================================
CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,

    -- status: running | completed | failed
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

-- ============================================================
-- SURVEYS
-- ============================================================
CREATE TABLE IF NOT EXISTS surveys (
    survey_id TEXT PRIMARY KEY,

    -- status: running | completed | failed
    status TEXT NOT NULL,

    started_at TEXT,
    finished_at TEXT,
    total_runtime_seconds REAL
);

-- ============================================================
-- WEBODM TASKS
-- ============================================================
CREATE TABLE IF NOT EXISTS webodm_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    survey_id TEXT,                 -- optional but handy
    project_id INTEGER,
    task_id INTEGER,
    task_name TEXT,
    success INTEGER,
    runtime_seconds REAL,
    created_at TEXT,
    options_json TEXT,

    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webodm_tasks_run
ON webodm_tasks(run_id);
"""