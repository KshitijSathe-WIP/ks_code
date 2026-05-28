import json
from pathlib import Path

data = json.loads(Path("Output Files/sample_lineage.json").read_text(encoding="utf-8"))

field = "GROSS_INT_PDUE_DAYS_CNT"
table = "TT_VW_DELINQ"

verts = {v["id"]: v for v in data["vertices"]}

# 1. Does the field exist at all?
matches_v = [v for v in data["vertices"] if field in v["id"]]
print(f"--- Vertices containing '{field}' ---")
for v in matches_v:
    print(f"  {v['id']}  [{v['layer']}]")
if not matches_v:
    print("  (none found)")

# 2. Outgoing edges (this field feeds something)
out_edges = [e for e in data["edges"] if table in e["from_vertex"] and field in e["from_vertex"]]
print(f"\n--- Outgoing edges from {table}.{field} ---")
for e in out_edges:
    tgt = verts.get(e["to_vertex"], {})
    print(f"  -> {e['to_vertex']}  [{tgt.get('layer','')}]  | map: {e['mapping_name']}  | expr: {e['expression']}")
if not out_edges:
    print("  (none — field does not feed any downstream field in this XML)")

# 3. Incoming edges (something feeds this field)
in_edges = [e for e in data["edges"] if table in e["to_vertex"] and field in e["to_vertex"]]
print(f"\n--- Incoming edges into {table}.{field} ---")
for e in in_edges:
    src = verts.get(e["from_vertex"], {})
    print(f"  <- {e['from_vertex']}  [{src.get('layer','')}]  | map: {e['mapping_name']}  | expr: {e['expression']}")
if not in_edges:
    print("  (none)")
