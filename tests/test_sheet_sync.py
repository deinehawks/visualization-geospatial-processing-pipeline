from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from shared.sheet_sync.engine import (KEYS, TABS, MANUAL, headers, load_state,
                                     plan_updates, select_runs, single_worker)
from shared.sheet_sync.google import GoogleSheets, TransientSyncError
from shared.sheet_sync.projection import project, read_snapshot
from tools.sync_pipeline_sheet import sync_once, main


RUN_HEADERS = ["Pipeline Run ID", "Process Started Time (Raw)", "Process Finished Time (Raw)",
               "Dataset Name", "Organization", "Survey ID", "Survey Code", "UAV Model",
               "Flight Date", "Data Type", "Processing Start Time", "Processing End Time",
               "Active Processing Time", "Overall Status", "Final Stage Reached", "Total Retries", "Notes"]
STAGE_HEADERS = ["Pipeline Run ID", "Stage Name", "Attempt Number", "Stage Start Time",
                 "Stage End Time", "Stage Runtime", "Status", "Retry Count", "Error Code",
                 "Error Message", "Recovery Action", "Stage Attempt ID"]
TIME_HEADERS = ["Pipeline Run ID", "Survey ID", "Data Preparation", "Cross-run Image Filtering",
                "KML Boundary Setter", "WebODM Upload Cache", "WebODM Orthomosaic (T4)",
                "WebODM 3D (T2)", "WebODM Timing Variance", "Quality Inspection", "QGIS Clipping",
                "QGIS Tile Generation", "QGIS Unclassified/Retry Time", "Total Runtime",
                "WebODM stage total", "QGIS stage total", "Status"]


def grids():
    return {"runs": [RUN_HEADERS.copy()], "stages": [STAGE_HEADERS.copy()],
            "timing": [TIME_HEADERS.copy()]}


def run(rid="r1", status="running"):
    return dict(run_id=rid, survey_id="AH-001", status=status,
                started_at="2026-09-07T01:00:00+00:00",
                finished_at="2026-09-07T03:00:00+00:00", total_runtime_seconds=120)


def stage(sid=1, name="webodm", duration=30, status="completed", rid="r1"):
    return dict(id=sid, run_id=rid, stage_name=name, status=status,
                started_at="2026-09-07T01:00:00+00:00",
                finished_at="2026-09-07T01:01:00+00:00", runtime_seconds=duration,
                error_message=None)


class FakeSheet:
    def __init__(self):
        self.grids = grids()
        self.fail_after_write = False
        self.fail_before_write = False
        self.calls = []

    def read(self):
        return copy.deepcopy(self.grids)

    def write(self, records):
        self.calls.append(copy.deepcopy(records))
        if self.fail_before_write:
            raise TransientSyncError("offline")
        for record in records:
            grid = self.grids[record["tab"]]
            while len(grid) <= record["row"]:
                grid.append([])
            row = grid[record["row"]]
            for column, value in record["edits"]:
                while len(row) <= column:
                    row.append("")
                row[column] = value
        if self.fail_after_write:
            raise TransientSyncError("response lost")


def seed(database, records=None):
    with sqlite3.connect(database) as conn:
        for record in records or [run()]:
            conn.execute("INSERT INTO runs (run_id, survey_id, status, started_at, finished_at, "
                         "total_runtime_seconds) VALUES (:run_id, :survey_id, :status, :started_at, "
                         ":finished_at, :total_runtime_seconds)", record)
        conn.commit()


def test_read_is_read_only_and_never_initializes(tmp_path, temporary_sqlite_db_path, monkeypatch):
    seed(temporary_sqlite_db_path)
    before = temporary_sqlite_db_path.read_bytes()
    real_connect = sqlite3.connect
    seen = []

    def check(path, **kwargs):
        seen.append((path, kwargs))
        conn = real_connect(path, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE runs SET status='failed'")
        conn.rollback()
        return conn

    monkeypatch.setattr(sqlite3, "connect", check)
    runs, stages = read_snapshot(temporary_sqlite_db_path)
    assert runs[0]["status"] == "running" and not stages
    assert seen[0][0].endswith("?mode=ro")
    assert temporary_sqlite_db_path.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        read_snapshot(missing)
    assert not missing.exists()


def test_timing_does_not_double_count_parent_and_children_or_invent_retries():
    p = project([run(status="completed")], [stage(duration=100),
        stage(2, "webodm_task4", 40), stage(3, "webodm_task2", 50),
        stage(4, "qgis", 10), stage(5, "qgis", 20, "failed")])
    t = p["timing"]["r1"]
    assert t["WebODM stage total"] == 100
    assert t["WebODM Orthomosaic (T4)"] == 40
    assert t["QGIS stage total"] == 30
    assert "WebODM Timing Variance" not in t and "QGIS Clipping" not in t
    assert p["stages"]["r1:5"]["Attempt Number"] == 2
    assert "Retry Count" not in p["stages"]["r1:5"]
    assert "Active Processing Time" not in p["runs"]["r1"]


def test_resumed_run_hides_old_finish_and_runtime_and_preserves_timezone():
    p = project([run()], [stage(status="running", duration=None)])
    assert p["runs"]["r1"]["Process Finished Time (Raw)"] == ""
    assert p["timing"]["r1"]["Total Runtime"] == ""
    assert p["timing"]["r1"]["WebODM stage total"] == ""
    assert p["runs"]["r1"]["Processing Start Time"] == "2026-09-07 09:00:00"


def test_manual_columns_formulas_legacy_rows_and_sorting_preserved():
    sheet = FakeSheet()
    legacy = [""] * len(RUN_HEADERS)
    legacy[3] = "Legacy dataset"
    sheet.grids["runs"].append(legacy)
    manual = [""] * len(RUN_HEADERS)
    manual[0] = "r1"
    for name in MANUAL.intersection(RUN_HEADERS):
        manual[RUN_HEADERS.index(name)] = "Operator value"
    manual[RUN_HEADERS.index("Processing Start Time")] = "=B3"
    sheet.grids["runs"].append(manual.copy())
    p = project([run()], [])
    records, skipped = plan_updates(p, sheet.read(), {"r1"})
    assert skipped == 1
    sheet.write(records)
    assert sheet.grids["runs"][1] == legacy
    for name in MANUAL.intersection(RUN_HEADERS):
        assert sheet.grids["runs"][2][RUN_HEADERS.index(name)] == "Operator value"
    assert sheet.grids["runs"][2][10] == "=B3"
    sheet.grids["runs"][1:] = reversed(sheet.grids["runs"][1:])
    p["runs"]["r1"]["Overall Status"] = "failed"
    sheet.write(plan_updates(p, sheet.read(), {"r1"})[0])
    assert sheet.grids["runs"][1][13] == "Failed"


def test_duplicate_keys_and_unkeyed_legacy_attempts_fail_closed():
    g = grids()
    g["runs"].extend([["r1"], ["r1"]])
    with pytest.raises(ValueError, match="duplicate identifier"):
        plan_updates(project([run()], []), g, {"r1"})
    g = grids()
    g["stages"].append(["r1", "webodm"])
    with pytest.raises(ValueError, match="unkeyed legacy"):
        plan_updates(project([run()], [stage()]), g, {"r1"})


def test_history_and_new_runs_and_resumed_history_selection(tmp_path):
    s = load_state(tmp_path / "state.json", {})
    g = grids()
    g["runs"].append(["matched"])
    p = project([run("matched", "completed"), run("old", "completed"), run("active")], [])
    assert select_runs(s, p, g) == {"matched", "active"}
    p = project([run("matched", "completed"), run("old", "completed"),
                 run("active", "completed"), run("new-offline", "completed")], [])
    assert select_runs(s, p, g) == {"matched", "active", "new-offline"}
    p = project([run("old", "completed")], [stage(rid="old")])
    assert "old" in select_runs(s, p, g)  # resumed and completed between polls


@pytest.mark.parametrize("when", ["fail_before_write", "fail_after_write"])
def test_restart_after_uncertain_write_does_not_duplicate(temporary_sqlite_db_path, tmp_path, when):
    db = temporary_sqlite_db_path
    seed(db)
    sheet = FakeSheet()
    setattr(sheet, when, True)
    state = tmp_path / "state.json"
    with pytest.raises(TransientSyncError):
        sync_once(db, state, "book", sheet)
    assert "r1" in json.loads(state.read_text())["tracked"]
    setattr(sheet, when, False)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE runs SET status='completed'")
    sync_once(db, state, "book", sheet)
    assert len(sheet.grids["runs"]) == 2
    assert len(sheet.grids["timing"]) == 2
    assert sync_once(db, state, "book", sheet) == []


def test_dry_run_does_not_write_state_or_sheet(temporary_sqlite_db_path, tmp_path):
    seed(temporary_sqlite_db_path)
    sheet = FakeSheet()
    state = tmp_path / "state.json"
    assert sync_once(temporary_sqlite_db_path, state, "book", sheet, dry_run=True)
    assert not state.exists() and not sheet.calls


def test_state_mismatch_corruption_and_single_worker(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("bad json")
    with pytest.raises(ValueError):
        load_state(state, {})
    state.write_text(json.dumps({"version": 1, "identity": {"other": 1}}))
    with pytest.raises(ValueError, match="another database"):
        load_state(state, {})
    lock = tmp_path / "worker.lock"
    with single_worker(lock):
        with pytest.raises(ValueError, match="Another sync worker"):
            with single_worker(lock):
                pass
    with single_worker(lock):
        pass


class Response:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self.payload = payload or {}

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return next(self.responses)


def test_google_atomic_sparse_write_and_literal_error_text():
    session = Session([Response()])
    google = GoogleSheets("book", session)
    google.properties = {"stages": {"sheetId": 7, "gridProperties": {"rowCount": 1}}}
    google.write([{"tab": "stages", "row": 1,
                   "edits": [(11, "r1:1"), (9, "=IMPORTDATA(\"bad\")")]}])
    body = session.calls[0][1]["json"]["requests"]
    assert body[0]["appendDimension"]["length"] == 1
    assert body[1]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"] == {"stringValue": "r1:1"}
    assert body[2]["updateCells"]["rows"][0]["values"][0]["userEnteredValue"]["stringValue"].startswith("=")
    assert all(r["updateCells"]["fields"] == "userEnteredValue" for r in body[1:])


@pytest.mark.parametrize("status,exception", [(429, TransientSyncError), (503, TransientSyncError),
                                               (401, ValueError), (403, ValueError), (400, ValueError)])
def test_google_errors_not_blindly_retried(status, exception):
    session = Session([Response(status=status)])
    google = GoogleSheets("book", session)
    with pytest.raises(exception):
        google.request("POST", ":batchUpdate", json={})
    assert len(session.calls) == 1


def test_prepare_preserves_existing_columns_and_refuses_legacy_rows():
    session = Session([Response()])
    google = GoogleSheets("book", session)
    google.properties = {"stages": {"sheetId": 7, "gridProperties": {"columnCount": 26}}}
    g = grids()
    g["stages"][0].remove("Stage Attempt ID")
    assert google.prepare(g)
    request = session.calls[0][1]["json"]["requests"][0]["updateCells"]
    assert request["start"]["columnIndex"] == 11
    g["stages"].append(["old"])
    with pytest.raises(ValueError, match="legacy"):
        google.prepare(g)


def test_missing_headers_unknown_status_and_invalid_duration():
    g = grids()
    g["runs"][0][0] = "Wrong ID"
    with pytest.raises(ValueError, match="Missing header"):
        plan_updates(project([run()], []), g, {"r1"})
    with pytest.raises(ValueError, match="Unsupported persisted status"):
        plan_updates(project([run(status="new_status")], []), grids(), {"r1"})
    with pytest.raises(ValueError, match="Invalid persisted duration"):
        project([run()], [stage(duration=float("nan"))])


def test_cli_help_and_config_fail_without_database_access(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    monkeypatch.delenv("PIPELINE_SHEET_ID", raising=False)
    monkeypatch.delenv("PIPELINE_SHEETS_CREDENTIALS", raising=False)
    assert main([]) == 1


def test_google_reads_bounded_pages_and_keeps_formulas_and_holes():
    properties = [{"properties": {"title": title, "sheetId": i,
                    "gridProperties": {"rowCount": 501, "columnCount": 26}}}
                  for i, title in enumerate(TABS.values())]
    responses = [Response({"sheets": properties})]
    for key in TABS:
        responses.extend([Response({"values": grids()[key] + [["=formula"]]}),
                          Response({"values": [["last row"]]})])
    session = Session(responses)
    google = GoogleSheets("book", session)
    result = google.read()
    assert len(session.calls) == 7
    assert result["runs"][1] == ["=formula"]
    assert result["runs"][499] == [] and result["runs"][500] == ["last row"]
    assert all(c[1]["params"]["valueRenderOption"] == "FORMULA" for c in session.calls[1:])


def test_multiple_runs_same_survey_and_new_resume_attempt(temporary_sqlite_db_path, tmp_path):
    db = temporary_sqlite_db_path
    seed(db, [run("r1"), run("r2")])
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO stages (run_id, stage_name, status, runtime_seconds) "
                     "VALUES ('r1', 'webodm', 'paused', 10)")
    sheet = FakeSheet()
    state = tmp_path / "state.json"
    sync_once(db, state, "book", sheet)
    assert len(sheet.grids["runs"]) == 3
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO stages (run_id, stage_name, status, runtime_seconds) "
                     "VALUES ('r1', 'webodm', 'completed', 20)")
        conn.execute("UPDATE runs SET status='completed' WHERE run_id='r1'")
    sync_once(db, state, "book", sheet)
    assert len(sheet.grids["runs"]) == 3
    assert len(sheet.grids["stages"]) == 3
    assert sheet.grids["stages"][1][11] != sheet.grids["stages"][2][11]
    assert sheet.grids["stages"][2][2] == 2
    assert sheet.grids["timing"][1][14] == "0h 0m 30s"
