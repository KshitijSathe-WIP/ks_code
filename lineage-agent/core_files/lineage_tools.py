# lineage_tools.py
# ────────────────────────────────────────────────────────────
# Lineage Function Tools for Azure AI Foundry Agent
#
# Data model (Neo4j Aura):
#   Node  : :Field  {id, db_schema, table_name, field_name, layer, data_type, precision}
#   Rel   : :TRANSFORMS_TO  {id, mapping_name, folder_name,
#                             transformation_name, transformation_type, expression}
#   Layers: TPR (source) → TT (temp/staging) → DDM (data mart)
# ────────────────────────────────────────────────────────────

import json
from neo4j_client import Neo4jLineageClient

neo4j_client = Neo4jLineageClient()

# ─── 1. Upstream Lineage (Table Level) ───
def query_upstream_lineage(table_name: str, max_depth: str = "10") -> str:
    """
    Retrieves upstream (source) lineage for a given table.
    Finds all tables whose fields transform into any field of the target table.
    Results show each upstream table with its layer, schema, and hop distance.

    :param table_name: The exact name of the target table.
                       Example: "F_ACCOUNT_CONS"
    :param max_depth: Maximum number of hops to traverse upstream.
                      Default is "10". Use "1" for direct sources only.
    :return: A JSON array of upstream tables with table_name, layer, db_schema, hops.
    :rtype: str
    """
    safe_depth = int(max_depth)
    cypher = f"""
        MATCH (target:Field {{table_name: $table_name}})
        MATCH path = (upstream:Field)-[:TRANSFORMS_TO*1..{safe_depth}]->(target)
        WHERE upstream.table_name <> $table_name
        WITH upstream.table_name  AS table_name,
             upstream.layer       AS layer,
             upstream.db_schema   AS db_schema,
             min(length(path))    AS hops
        RETURN table_name, layer, db_schema, hops
        ORDER BY hops ASC, table_name ASC
    """
    return neo4j_client.run_cypher(cypher, {"table_name": table_name})

# ─── 2. Downstream Lineage ───
def query_downstream_lineage(table_name: str, max_depth: str = "10") -> str:
    """
    Retrieves downstream (dependent) lineage for a given table.
    Traverses the FEEDS relationship forward to find all tables
    that directly or indirectly consume data from the source table.
    Useful for impact analysis — understanding what breaks if this table changes.

    :param table_name: The exact name of the source table to trace downstream.
                       Example: "MAST_LOAN_REC"
    :param max_depth: Maximum number of hops to traverse downstream.
                      Default is "10". Use "1" for direct dependents only.
    :return: A JSON array of downstream tables with table_name, layer, db_schema, hops.
    :rtype: str
    """
    safe_depth = int(max_depth)
    cypher = f"""
        MATCH (source:Field {{table_name: $table_name}})
        MATCH path = (source)-[:TRANSFORMS_TO*1..{safe_depth}]->(downstream:Field)
        WHERE downstream.table_name <> $table_name
        WITH downstream.table_name AS table_name,
             downstream.layer      AS layer,
             downstream.db_schema  AS db_schema,
             min(length(path))     AS hops
        RETURN table_name, layer, db_schema, hops
        ORDER BY hops ASC, table_name ASC
    """
    return neo4j_client.run_cypher(cypher, {"table_name": table_name})

# ─── 3. Field-Level Lineage ───
def query_column_lineage(field_name: str, table_name: str) -> str:
    """
    Retrieves field-level lineage showing ALL paths that flow into a specific field.
    Traces TRANSFORMS_TO backwards through every intermediate hop to show the
    complete lineage chain (e.g. TPR → TT → TT → DDM).

    :param field_name: The exact name of the field/column to trace.
                       Example: "PARTICIPANT_KEY"
    :param table_name: The table containing this field.
                       Example: "F_PARTICIPANTS"
    :return: A JSON array of every edge in the lineage paths, each with
             from_field, from_table, from_layer, to_field, to_table, to_layer,
             mapping_name, transformation_type, and expression.
    :rtype: str
    """
    cypher = """
        MATCH (target:Field {field_name: $field_name, table_name: $table_name})
        MATCH path = (source:Field)-[:TRANSFORMS_TO*1..10]->(target)
        UNWIND range(0, length(path)-1) AS idx
        WITH relationships(path)[idx] AS rel,
             nodes(path)[idx] AS from_node,
             nodes(path)[idx+1] AS to_node
        RETURN DISTINCT
               from_node.field_name      AS from_field,
               from_node.table_name      AS from_table,
               from_node.layer           AS from_layer,
               from_node.db_schema       AS from_schema,
               to_node.field_name        AS to_field,
               to_node.table_name        AS to_table,
               to_node.layer             AS to_layer,
               rel.mapping_name          AS mapping_name,
               rel.transformation_type   AS transformation_type,
               rel.expression            AS expression
        ORDER BY from_layer ASC, from_table ASC, from_field ASC
    """
    return neo4j_client.run_cypher(cypher, {
        "field_name": field_name,
        "table_name": table_name
    })

# ─── 4. Cross-Layer Path ───
def query_cross_layer_path(source_table: str, target_table: str) -> str:
    """
    Finds the shortest lineage path between two tables across layers.
    Useful for tracing the full pipeline from a TPR source to a DDM target
    (e.g., TPR → TT → DDM).

    :param source_table: The starting table (typically TPR layer).
                         Example: "MAST_LOAN_REC"
    :param target_table: The ending table (typically DDM layer).
                         Example: "D_LOAN_ACCOUNT_CONS"
    :return: A JSON object with the ordered field-level path and total hop count.
    :rtype: str
    """
    cypher = """
        MATCH (src:Field {table_name: $source_table}),
              (tgt:Field {table_name: $target_table})
        MATCH path = shortestPath((src)-[:TRANSFORMS_TO*]->(tgt))
        WITH [n IN nodes(path) | {
                 table_name: n.table_name,
                 field_name: n.field_name,
                 layer:      n.layer,
                 db_schema:  n.db_schema
             }] AS lineage_path,
             length(path) AS total_hops
        RETURN lineage_path, total_hops
        ORDER BY total_hops ASC
        LIMIT 1
    """
    return neo4j_client.run_cypher(cypher, {
        "source_table": source_table,
        "target_table": target_table
    })

# ─── 5. Impact Analysis ───
def query_impact_analysis(table_name: str) -> str:
    """
    Performs impact analysis for a given table — calculates the blast radius.
    Shows all downstream tables grouped by layer, with counts per layer.
    Useful for understanding the risk of changing or removing a table.

    :param table_name: The table to analyze impact for.
                       Example: "TT_D_PARTICIPANT"
    :return: A JSON array with impacted tables grouped by layer and table count.
    :rtype: str
    """
    cypher = """
        MATCH (source:Field {table_name: $table_name})
        MATCH (source)-[:TRANSFORMS_TO*1..10]->(impacted:Field)
        WHERE impacted.table_name <> $table_name
        WITH DISTINCT impacted.table_name AS table_name,
                      impacted.layer      AS layer,
                      impacted.db_schema  AS db_schema
        WITH layer,
             collect(DISTINCT table_name) AS tables,
             count(DISTINCT table_name)   AS table_count
        RETURN layer, tables, table_count
        ORDER BY
            CASE layer
                WHEN 'TPR' THEN 1
                WHEN 'TT'  THEN 2
                WHEN 'DDM' THEN 3
                ELSE 4
            END ASC
    """
    return neo4j_client.run_cypher(cypher, {"table_name": table_name})

# ─── 6. Layer Inventory ───
def query_tables_by_layer(layer_name: str) -> str:
    """
    Lists all tables belonging to a specific data layer with field counts.
    Valid layers: TPR (source system), TT (temp/staging), DDM (data mart).

    :param layer_name: The layer to query. Example: "DDM"
    :return: A JSON array of tables with table_name, db_schema, and field_count.
    :rtype: str
    """
    cypher = """
        MATCH (f:Field {layer: $layer_name})
        WITH f.table_name AS table_name,
             f.db_schema  AS db_schema,
             count(f)     AS field_count
        RETURN table_name, db_schema, field_count
        ORDER BY table_name ASC
    """
    return neo4j_client.run_cypher(cypher, {"layer_name": layer_name})

# ─── 7. Custom Cypher ───
def run_custom_cypher(cypher_query: str) -> str:
    """
    Executes a custom read-only Cypher query against the lineage graph.
    Use this ONLY when the other lineage functions cannot answer the question.
    Only MATCH and RETURN queries are allowed.
    CREATE, DELETE, SET, MERGE, DROP, and REMOVE operations are blocked.

    Graph schema reference:
      MATCH (f:Field)-[r:TRANSFORMS_TO]->(g:Field)
      Field props  : id, db_schema, table_name, field_name, layer, data_type, precision
      Rel props    : mapping_name, folder_name, transformation_name, transformation_type, expression
      Layers       : 'TPR' | 'TT' | 'DDM'

    :param cypher_query: A valid read-only Cypher query.
                         Example: "MATCH (f:Field {layer:'DDM'}) RETURN DISTINCT f.table_name LIMIT 10"
    :return: Query results as a JSON string.
    :rtype: str
    """
    # ─── Safety Gate: Block all write operations ───
    BLOCKED_KEYWORDS = [
        "CREATE", "DELETE", "SET ", "REMOVE", "MERGE",
        "DROP", "DETACH", "CALL dbms", "LOAD CSV"
    ]
    upper_query = cypher_query.upper().strip()

    for keyword in BLOCKED_KEYWORDS:
        if keyword in upper_query:
            return json.dumps({
                "error": f"Write operation '{keyword.strip()}' is not permitted.",
                "hint": "Only read-only MATCH/RETURN queries are allowed."
            })

    # ─── Safety Gate: Enforce result limit ───
    if "LIMIT" not in upper_query:
        cypher_query = cypher_query.rstrip().rstrip(";") + " LIMIT 50"

    return neo4j_client.run_cypher(cypher_query)