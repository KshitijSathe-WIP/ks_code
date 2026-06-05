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


def _bare_table(name: str) -> str:
    """Strip optional SCHEMA. prefix and normalize to uppercase.
    'SHAW_TPR.MAST_LOAN_REC' → 'MAST_LOAN_REC', 'part_sold' → 'PART_SOLD'."""
    parts = name.strip().split('.')
    return (parts[-1] if len(parts) > 1 else name).upper()


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
    table_name = _bare_table(table_name)
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
    table_name = _bare_table(table_name)
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
    Retrieves field-level lineage showing ALL edges connected to a specific field —
    both upstream (what flows INTO the field) and downstream (what the field flows INTO).
    Traces TRANSFORMS_TO in both directions to give a complete picture.

    Accepts two input styles:
    - Dotted id format: pass field_name="CRDM_DDM.F_ACCOUNTS.SOURCE_KEY", table_name=""
      The function will match on the node's id property directly.
    - Table.Field format: pass field_name="F_ACCOUNTS.SOURCE_KEY", table_name=""
      The function will split and match on field_name + table_name properties.
    - Split format: pass field_name="SOURCE_KEY", table_name="F_ACCOUNTS"
      The function will match on field_name + table_name properties.

    :param field_name: The field name, OR the full dotted id (SCHEMA.TABLE.FIELD).
                       Example: "SOURCE_KEY" or "CRDM_DDM.F_ACCOUNTS.SOURCE_KEY"
    :param table_name: The table containing this field. Leave empty if using dotted id.
                       Example: "F_ACCOUNTS"
    :return: A JSON array of every edge in the lineage paths, each with
             from_field, from_table, from_layer, from_schema, from_data_type, from_precision,
             to_field, to_table, to_layer, to_schema, to_data_type, to_precision,
             mapping_name, transformation_name, transformation_type, and expression.
    :rtype: str
    """
    # Shared RETURN clause used in both query variants
    _RETURN_COLS = """
            RETURN DISTINCT
                   from_node.field_name      AS from_field,
                   from_node.table_name      AS from_table,
                   from_node.layer           AS from_layer,
                   from_node.db_schema       AS from_schema,
                   from_node.data_type       AS from_data_type,
                   from_node.precision       AS from_precision,
                   to_node.field_name        AS to_field,
                   to_node.table_name        AS to_table,
                   to_node.layer             AS to_layer,
                   to_node.db_schema         AS to_schema,
                   to_node.data_type         AS to_data_type,
                   to_node.precision         AS to_precision,
                   rel.mapping_name          AS mapping_name,
                   rel.transformation_name   AS transformation_name,
                   rel.transformation_type   AS transformation_type,
                   rel.expression            AS expression
            ORDER BY from_layer ASC, from_table ASC, from_field ASC
    """

    def _run_bidirectional(match_clause: str, params: dict) -> str:
        """Run backward (upstream) query; if empty, try forward (downstream); merge both."""
        backward = f"""
            {match_clause}
            MATCH path = (source:Field)-[:TRANSFORMS_TO*1..10]->(anchor)
            UNWIND range(0, length(path)-1) AS idx
            WITH relationships(path)[idx] AS rel,
                 nodes(path)[idx] AS from_node,
                 nodes(path)[idx+1] AS to_node
            {_RETURN_COLS}
        """
        forward = f"""
            {match_clause}
            MATCH path = (anchor)-[:TRANSFORMS_TO*1..10]->(downstream:Field)
            UNWIND range(0, length(path)-1) AS idx
            WITH relationships(path)[idx] AS rel,
                 nodes(path)[idx] AS from_node,
                 nodes(path)[idx+1] AS to_node
            {_RETURN_COLS}
        """
        result_back = neo4j_client.run_cypher(backward, params)
        result_fwd  = neo4j_client.run_cypher(forward,  params)

        import json as _json
        rows_back = _json.loads(result_back)
        rows_fwd  = _json.loads(result_fwd)

        # Merge and deduplicate by (from_table, from_field, to_table, to_field, mapping_name)
        seen = set()
        merged = []
        for row in rows_back + rows_fwd:
            key = (row.get("from_field"), row.get("from_table"),
                   row.get("to_field"),   row.get("to_table"),
                   row.get("mapping_name"))
            if key not in seen:
                seen.add(key)
                merged.append(row)
        return _json.dumps(merged, ensure_ascii=False, indent=2)

    # If field_name contains dots, treat it as the node's id property
    if field_name.count('.') >= 2:
        return _run_bidirectional(
            "MATCH (anchor:Field {id: $field_id})",
            {"field_id": field_name}
        )

    # If field_name is TABLE.FIELD format (1 dot), split into components
    if field_name.count('.') == 1 and not table_name:
        table_name, field_name = field_name.split('.', 1)

    return _run_bidirectional(
        "MATCH (anchor:Field {field_name: $field_name, table_name: $table_name})",
        {"field_name": field_name, "table_name": table_name}
    )

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
    source_table = _bare_table(source_table)
    target_table = _bare_table(target_table)
    # Traverse outward from all source-table fields (bounded to 15 hops) until
    # a target-table field is reached.  Avoids a cartesian-product + shortestPath
    # which can timeout on Aura when either table has many fields.
    cypher = """
        MATCH (src:Field {table_name: $source_table})
        MATCH path = (src)-[:TRANSFORMS_TO*1..15]->(tgt:Field {table_name: $target_table})
        WITH path,
             [n IN nodes(path) | {
                 table_name: n.table_name,
                 field_name: n.field_name,
                 layer:      n.layer,
                 db_schema:  n.db_schema,
                 data_type:  n.data_type,
                 precision:  n.precision
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
    table_name = _bare_table(table_name)
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

# ─── 7. Search Fields (fuzzy lookup) ───
def search_fields(search_term: str) -> str:
    """
    Searches for fields and tables whose names contain the given term (case-insensitive).
    Use this when query_column_lineage or query_upstream_lineage returns no results —
    it helps identify the correct exact table_name or field_name to use.
    Also useful when a user provides a dotted reference like SCHEMA.TABLE.FIELD and you
    need to resolve the correct table_name and field_name values.

    :param search_term: A partial or full name to search for (case-insensitive).
                        Can be a table name, field name, schema name fragment,
                        or TABLE.FIELD / SCHEMA.TABLE.FIELD dotted format.
                        Example: "SOURCE_KEY", "F_ACCOUNT", or "F_ACCOUNTS.SOURCE_KEY"
    :return: A JSON array of matching Field nodes with db_schema, table_name,
             field_name, layer, data_type, and precision. Limited to 50 results.
    :rtype: str
    """
    # If dotted format, search each part independently and intersect in Cypher
    parts = search_term.strip().split('.')
    if len(parts) == 2:
        # TABLE.FIELD — match table_name contains parts[0] AND field_name contains parts[1]
        cypher = """
            MATCH (f:Field)
            WHERE toLower(f.table_name) CONTAINS toLower($part0)
              AND toLower(f.field_name) CONTAINS toLower($part1)
            RETURN f.db_schema   AS db_schema,
                   f.table_name  AS table_name,
                   f.field_name  AS field_name,
                   f.layer       AS layer,
                   f.data_type   AS data_type,
                   f.precision   AS precision
            ORDER BY f.layer ASC, f.table_name ASC, f.field_name ASC
            LIMIT 50
        """
        return neo4j_client.run_cypher(cypher, {"part0": parts[0], "part1": parts[1]})
    if len(parts) >= 3:
        # SCHEMA.TABLE.FIELD — match all three parts
        cypher = """
            MATCH (f:Field)
            WHERE toLower(f.db_schema)  CONTAINS toLower($part0)
              AND toLower(f.table_name) CONTAINS toLower($part1)
              AND toLower(f.field_name) CONTAINS toLower($part2)
            RETURN f.db_schema   AS db_schema,
                   f.table_name  AS table_name,
                   f.field_name  AS field_name,
                   f.layer       AS layer,
                   f.data_type   AS data_type,
                   f.precision   AS precision
            ORDER BY f.layer ASC, f.table_name ASC, f.field_name ASC
            LIMIT 50
        """
        return neo4j_client.run_cypher(cypher, {"part0": parts[0], "part1": parts[1], "part2": parts[2]})

    cypher = """
        MATCH (f:Field)
        WHERE toLower(f.field_name)  CONTAINS toLower($search_term)
           OR toLower(f.table_name)  CONTAINS toLower($search_term)
           OR toLower(f.db_schema)   CONTAINS toLower($search_term)
        RETURN f.db_schema   AS db_schema,
               f.table_name  AS table_name,
               f.field_name  AS field_name,
               f.layer       AS layer,
               f.data_type   AS data_type,
               f.precision   AS precision
        ORDER BY f.layer ASC, f.table_name ASC, f.field_name ASC
        LIMIT 50
    """
    return neo4j_client.run_cypher(cypher, {"search_term": search_term})

# ─── 8. Custom Cypher ───
def run_custom_cypher(cypher_query: str) -> str:
    """
    Executes a custom read-only Cypher query against the lineage graph.
    Use this ONLY when the other lineage functions cannot answer the question.
    Only MATCH and RETURN queries are allowed.
    CREATE, DELETE, SET, MERGE, DROP, and REMOVE operations are blocked.
    If no LIMIT is specified, one is added automatically:
      - 500 for DISTINCT / COUNT / COLLECT / WITH queries
      - 100 for all other queries

    Graph schema reference:
      MATCH (f:Field)-[r:TRANSFORMS_TO]->(g:Field)
      Field props  : id, db_schema, table_name, field_name, layer, data_type, precision
      Rel props    : mapping_name, folder_name, transformation_name, transformation_type, expression
      Layers       : 'TPR' | 'TT' | 'DDM'
      Known transformation_type values: 'Expression', 'Source Qualifier', 'Lookup Procedure',
                                        'Filter', 'Update Strategy', 'Aggregator', 'Router', 'Sequence Generator'

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
    # Use a generous limit for DISTINCT/aggregation queries; lower for full-record queries.
    if "LIMIT" not in upper_query:
        if any(kw in upper_query for kw in ("DISTINCT", "COUNT(", "COLLECT(", "WITH ")):
            cypher_query = cypher_query.rstrip().rstrip(";") + " LIMIT 500"
        else:
            cypher_query = cypher_query.rstrip().rstrip(";") + " LIMIT 100"

    result_json = neo4j_client.run_cypher(cypher_query)

    # Append row count so the model doesn't have to count manually
    import json as _json
    try:
        rows = _json.loads(result_json)
        if isinstance(rows, list):
            return _json.dumps({"row_count": len(rows), "rows": rows}, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return result_json