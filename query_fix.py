# Manually change the path of run id in the DB due to changes of path of the survey file

import sqlite3, json

con = sqlite3.connect(r"E:\dev\projects\automation-pipeline-viz\data\pipeline.db")
con.row_factory = sqlite3.Row
run_id = "0c101e2b-85b4-4e0a-8563-4967bab69914"

stages = con.execute(
    "SELECT id, stage_name, output_json FROM stages WHERE run_id=? AND output_json IS NOT NULL",
    (run_id,)
).fetchall()

for stage in stages:
    old_json = stage["output_json"]
    # Replace all variants of the old path
    new_json = old_json.replace("F:\\\\surveys", "Z:\\\\surveys")  # escaped in JSON
    new_json = new_json.replace("F:\\surveys", "Z:\\surveys")       # unescaped
    new_json = new_json.replace("F:/surveys", "Z:/surveys")         # forward slash
    if new_json != old_json:
        con.execute("UPDATE stages SET output_json=? WHERE id=?", (new_json, stage["id"]))
        print(f"Fixed: {stage['stage_name']}")
    else:
        print(f"No change: {stage['stage_name']} — printing raw JSON for inspection:")
        print(repr(old_json[:200]))

con.execute("UPDATE runs SET surveys_root='Z:\\surveys' WHERE run_id=?", (run_id,))
con.commit()
print("Done.")