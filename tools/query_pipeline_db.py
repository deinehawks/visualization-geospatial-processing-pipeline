from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any, Sequence


def query_run(database: Path, run_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    database = database.expanduser().resolve(strict=True)
    if not database.is_file():
        raise ValueError(f"Database path is not a file: {database}")
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        stages = connection.execute("SELECT * FROM stages WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return (dict(run) if run is not None else None, [dict(stage) for stage in stages])
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read one run from a pipeline SQLite database.")
    parser.add_argument("database", type=Path)
    parser.add_argument("run_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run, stages = query_run(args.database, args.run_id)
    print("=== RUN ===")
    print(run)
    print("\n=== STAGES ===")
    for stage in stages:
        print(stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
