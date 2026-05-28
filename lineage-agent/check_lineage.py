"""Quick check: all paths into F_PARTICIPANTS.PARTICIPANT_KEY"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "core_files"))

import json
from neo4j_client import Neo4jLineageClient

client = Neo4jLineageClient()
result = client.run_cypher("""
    MATCH (target:Field {field_name: 'PARTICIPANT_KEY', table_name: 'F_PARTICIPANTS'})
    MATCH path = (source:Field)-[:TRANSFORMS_TO*1..10]->(target)
    UNWIND range(0, length(path)-1) AS idx
    WITH relationships(path)[idx] AS rel,
         nodes(path)[idx] AS from_node,
         nodes(path)[idx+1] AS to_node,
         length(path) AS total_hops
    RETURN DISTINCT
           from_node.field_name AS from_field,
           from_node.table_name AS from_table,
           from_node.layer AS from_layer,
           to_node.field_name AS to_field,
           to_node.table_name AS to_table,
           to_node.layer AS to_layer,
           rel.transformation_type AS transform_type,
           rel.expression AS expression,
           rel.mapping_name AS mapping_name
    ORDER BY from_layer ASC, from_table ASC
""", {})

data = json.loads(result)
print(f"Total distinct edges: {len(data)}")
print()
for row in data:
    src = f"{row['from_layer']}.{row['from_table']}.{row['from_field']}"
    tgt = f"{row['to_layer']}.{row['to_table']}.{row['to_field']}"
    print(f"  {src}  -->  {tgt}")
    print(f"    transform: {row['transform_type']}  |  mapping: {row['mapping_name']}")
    if row.get('expression'):
        print(f"    expr: {row['expression'][:80]}")
    print()
