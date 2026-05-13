import sqlite3, json

con = sqlite3.connect(r"E:\dev\projects\automation-pipeline-viz\data\pipeline.db")
con.row_factory = sqlite3.Row
run_id = "0c101e2b-85b4-4e0a-8563-4967bab69914"
row = con.execute(
    "SELECT output_json FROM stages WHERE run_id=? AND stage_name='kml_boundary' AND status='completed' ORDER BY id DESC LIMIT 1",
    (run_id,)
).fetchone()
print(json.loads(row["output_json"]) if row else "NOT FOUND")

from pathlib import Path
p = Path(r"F:\surveys\AH-026002\rgb\boundary\AH-026002.geojson")
print(p.exists())  # probably False

p2 = Path(r"F:\surveys\2026\AH-026002\rgb\boundary\AH-026002.geojson")
print(p2.exists())  # probably True