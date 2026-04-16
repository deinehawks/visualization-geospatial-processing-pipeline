import sqlite3, json
con = sqlite3.connect(r"E:\dev\projects\automation-pipeline-viz\data\pipeline.db")
con.row_factory = sqlite3.Row
run_id = "d5052e7c-b5b9-4455-bc89-e6d58ae0196e"

print("=== RUN ===")
r = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
print(dict(r))

print("\n=== STAGES ===")
for s in con.execute("SELECT * FROM stages WHERE run_id=? ORDER BY id", (run_id,)).fetchall():
    print(dict(s))