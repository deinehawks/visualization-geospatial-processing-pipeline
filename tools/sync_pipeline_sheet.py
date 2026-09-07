"""Manually launched one-way Sheets synchronization; never starts a pipeline."""
from __future__ import annotations

import argparse
import logging
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.sheet_sync.engine import (load_state, save_state, single_worker,
                                      select_runs, plan_updates)
from shared.sheet_sync.google import GoogleSheets, TransientSyncError
from shared.sheet_sync.projection import read_snapshot, project

LOG = logging.getLogger("sheet_sync")
ROOT = Path(__file__).resolve().parents[1]


def sync_once(database, state_path, spreadsheet_id, sheet, *, dry_run=False):
    runs, stages = read_snapshot(database)
    projection = project(runs, stages)
    grids = sheet.read()
    identity = {"database": str(Path(database).resolve(strict=True)), "spreadsheet": spreadsheet_id}
    state = load_state(state_path, identity)
    selected = select_runs(state, projection, grids)
    records, skipped = plan_updates(projection, grids, selected)
    if not dry_run:
        # Persist selection BEFORE remote delivery, so an outage cannot lose a
        # newly discovered run or turn it into excluded history after restart.
        save_state(state_path, state)
        sheet.write(records)
    LOG.info("%s: %d tracked runs; %d changed rows; %d formula cells preserved",
             "Preview" if dry_run else "Synchronized", len(selected), len(records), skipped)
    return records


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--database", type=Path, default=ROOT / "data" / "pipeline.db")
    result.add_argument("--state", type=Path, default=ROOT / "data" / "sheet-sync" / "state.json")
    result.add_argument("--spreadsheet-id", default=os.environ.get("PIPELINE_SHEET_ID", ""))
    result.add_argument("--credentials", type=Path,
                        default=os.environ.get("PIPELINE_SHEETS_CREDENTIALS"))
    result.add_argument("--interval", type=float, default=30)
    result.add_argument("--once", action="store_true")
    result.add_argument("--dry-run", action="store_true", help="Read and preview only; exit after one pass")
    result.add_argument("--prepare", action="store_true",
                        help="Add Stage Attempt ID to an empty stage log, then exit")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sheet = None
    try:
        if not args.spreadsheet_id or not args.credentials:
            raise ValueError("Set PIPELINE_SHEET_ID and PIPELINE_SHEETS_CREDENTIALS first")
        if not 10 <= args.interval <= 3600:
            raise ValueError("Interval must be between 10 and 3600 seconds")
        if args.prepare and args.dry_run:
            raise ValueError("--prepare and --dry-run are mutually exclusive")
        database = args.database.resolve(strict=True)
        credentials = args.credentials.resolve(strict=True)
        state = args.state.resolve()
        lock = database.parent / "sheet-sync" / "worker.lock"
        protected = {database, credentials, lock.resolve(),
                     Path(str(database) + "-wal"), Path(str(database) + "-shm")}
        if state in protected or state.with_suffix(state.suffix + ".tmp") in protected:
            raise ValueError("Sync state must be separate from database, credentials, and lock")
        with single_worker(lock):
            sheet = GoogleSheets.authenticate(args.spreadsheet_id, credentials)
            if args.prepare:
                changed = sheet.prepare(sheet.read())
                LOG.info("Stage Attempt ID %s", "added" if changed else "already present")
                return 0
            failures = 0
            while True:
                try:
                    sync_once(database, state, args.spreadsheet_id, sheet, dry_run=args.dry_run)
                    failures = 0
                except (TransientSyncError, sqlite3.OperationalError) as exc:
                    # Schema problems require correction, not endless retry.
                    if isinstance(exc, sqlite3.OperationalError) and not any(
                            word in str(exc).lower() for word in ("locked", "busy")):
                        raise
                    failures += 1
                    LOG.warning("%s; source run state unchanged", type(exc).__name__)
                    if args.once or args.dry_run:
                        return 1
                if args.once or args.dry_run:
                    return 0
                delay = args.interval if not failures else min(300, 2 ** min(failures, 8)) + random.random()
                time.sleep(delay)
    except KeyboardInterrupt:
        LOG.info("Sync stopped; pipeline execution is independent")
        return 0
    except (ValueError, OSError, sqlite3.Error, ImportError) as exc:
        LOG.error("Sync stopped: %s", exc)
        return 1
    finally:
        if sheet is not None:
            sheet.close()


if __name__ == "__main__":
    raise SystemExit(main())
