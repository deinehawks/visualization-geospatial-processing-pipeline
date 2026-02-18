import sqlite3
from pathlib import Path

def connect(db_file: Path) -> sqlite3.Connection:
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    # Better safety defaults
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")   # safer for crashes + concurrent reads
    conn.execute("PRAGMA synchronous = NORMAL;") # balanced performance
    return conn
