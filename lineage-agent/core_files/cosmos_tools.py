# cosmos_tools.py
# ────────────────────────────────────────────────────────────
# Cosmos DB Tool Functions for the Lineage Agent
#
# Queries the 'lineage' database, 'transformation_details' container.
# Each document = one edge record with full transformation_chain[].
#
# Document structure (key queryable fields):
#   id / edge_id             — SCHEMA.TABLE.FIELD__to__SCHEMA.TABLE.FIELD__m_MAPPING
#   from_vertex              — source field id  (SCHEMA.TABLE.FIELD)
#   to_vertex                — target field id  (SCHEMA.TABLE.FIELD)
#   mapping_name             — Informatica mapping name
#   folder_name              — Informatica folder name
#   final_expression         — expression closest to the target field
#   custom_sql               — Source Qualifier SQL query (if any)
#   lookup_condition         — Lookup transformation condition (if any)
#   filter_condition         — Filter / SQ filter condition (if any)
#   update_strategy_expression — e.g. DD_UPDATE, DD_INSERT
#   transformation_steps_count — number of steps in the chain
#   transformation_chain[]   — ordered steps with per-step detail
# ────────────────────────────────────────────────────────────

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env (one level up from core_files/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from azure.cosmos import CosmosClient, exceptions

# ── Lazy singleton client ──────────────────────────────────

_cosmos_client = None
_container     = None
_DATABASE      = "lineage"
_CONTAINER     = "transformation_details"


def _get_container():
    global _cosmos_client, _container
    if _container is not None:
        return _container
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    if not endpoint or not key:
        raise RuntimeError(
            "COSMOS_ENDPOINT and COSMOS_KEY must be set in .env to use Cosmos tools."
        )
    _cosmos_client = CosmosClient(url=endpoint, credential=key)
    db             = _cosmos_client.get_database_client(_DATABASE)
    _container     = db.get_container_client(_CONTAINER)
    return _container


def _run_cosmos_query(sql: str, parameters: list | None = None) -> str:
    """Execute a Cosmos SQL query and return JSON string of results."""
    container = _get_container()
    kwargs: dict = {"query": sql, "enable_cross_partition_query": True}
    if parameters:
        kwargs["parameters"] = parameters
    results = list(container.query_items(**kwargs))
    # Strip Cosmos internal metadata fields from output
    for r in results:
        for k in ("_rid", "_self", "_etag", "_attachments", "_ts"):
            r.pop(k, None)
    return json.dumps(results, ensure_ascii=False, indent=2)


# ─── Tool 1: Get full transformation details for one edge ───

def get_edge_transformation_details(edge_id: str) -> str:
    """
    Retrieves the complete transformation chain for a specific lineage edge.
    Returns the full step-by-step transformation logic including expressions,
    lookup conditions, filter conditions, SQL, and update strategy for the
    exact edge identified by its edge_id.

    Use this when the user asks "how is field X transformed into field Y in mapping M",
    or "show me the transformation logic for this edge".

    :param edge_id: The exact edge identifier in format
                    SCHEMA.TABLE.FIELD__to__SCHEMA.TABLE.FIELD__m_MAPPING_NAME.
                    Example: "CRDM_TMP.TT_F_PARTICIPANTS.CUSTOMER_KEY__to__CRDM_DDM.F_PARTICIPANTS.CUSTOMER_KEY__m_TMP_to_DDM_F_PARTICIPANTS"
    :return: JSON object with edge metadata and full transformation_chain array.
    :rtype: str
    """
    return _run_cosmos_query(
        "SELECT * FROM c WHERE c.id = @edge_id",
        [{"name": "@edge_id", "value": edge_id}]
    )


# ─── Tool 2: Get transformation logic for a specific field ───

def get_field_transformation_logic(field_id: str) -> str:
    """
    Retrieves all transformation logic records where a specific field is either
    the source (from_vertex) or the target (to_vertex) of a transformation.
    Returns summary columns — expression, lookup_condition, filter_condition,
    update_strategy_expression, mapping_name — without the full chain detail.

    Use this when the user asks "what is the transformation logic for field X",
    "how is field X derived", "what lookup is applied to field X", or
    "show me the expression for SCHEMA.TABLE.FIELD".

    :param field_id: The field identifier in SCHEMA.TABLE.FIELD format.
                     Example: "CRDM_DDM.F_PARTICIPANTS.CUSTOMER_KEY"
                     Also accepts TABLE.FIELD format — the function will match both
                     from_vertex and to_vertex containing this value.
    :return: JSON array of matching transformation records with
             from_vertex, to_vertex, mapping_name, final_expression,
             custom_sql, lookup_condition, filter_condition,
             update_strategy_expression, transformation_steps_count.
    :rtype: str
    """
    sql = """
        SELECT
            c.from_vertex, c.to_vertex,
            c.mapping_name, c.folder_name,
            c.final_expression, c.custom_sql,
            c.lookup_condition, c.filter_condition,
            c.update_strategy_expression,
            c.transformation_steps_count
        FROM c
        WHERE c.from_vertex = @field_id
           OR c.to_vertex   = @field_id
        ORDER BY c.mapping_name
    """
    results = _run_cosmos_query(sql, [{"name": "@field_id", "value": field_id}])
    # If no results, try a CONTAINS match (handles TABLE.FIELD without schema)
    if results == "[]":
        sql_contains = """
            SELECT
                c.from_vertex, c.to_vertex,
                c.mapping_name, c.folder_name,
                c.final_expression, c.custom_sql,
                c.lookup_condition, c.filter_condition,
                c.update_strategy_expression,
                c.transformation_steps_count
            FROM c
            WHERE CONTAINS(c.from_vertex, @field_id)
               OR CONTAINS(c.to_vertex, @field_id)
            ORDER BY c.mapping_name
        """
        results = _run_cosmos_query(sql_contains, [{"name": "@field_id", "value": field_id}])
    return results


# ─── Tool 3: Get all transformations in a mapping ───

def get_mapping_transformation_details(mapping_name: str) -> str:
    """
    Retrieves all edge transformation records for a specific Informatica mapping.
    Returns summary-level details for every field-to-field edge in that mapping,
    including expressions, lookup conditions, filter conditions, and step counts.

    Use this when the user asks "show me the transformation logic for mapping X",
    "what transformations are in mapping m_TMP_to_DDM_F_PARTICIPANTS",
    or "what expressions does mapping X use".

    :param mapping_name: The Informatica mapping name.
                         Example: "m_TMP_to_DDM_F_PARTICIPANTS"
    :return: JSON array of all edges in the mapping with their transformation summary.
    :rtype: str
    """
    sql = """
        SELECT
            c.edge_id, c.from_vertex, c.to_vertex,
            c.final_expression, c.custom_sql,
            c.lookup_condition, c.filter_condition,
            c.update_strategy_expression,
            c.transformation_steps_count,
            c.folder_name
        FROM c
        WHERE c.mapping_name = @mapping_name
        ORDER BY c.from_vertex
    """
    return _run_cosmos_query(sql, [{"name": "@mapping_name", "value": mapping_name}])


# ─── Tool 4: Find edges with lookup conditions for a table ───

def get_lookup_details_for_table(table_name: str) -> str:
    """
    Retrieves all edges where a lookup transformation is applied to fields
    flowing into or out of the specified table, including the full lookup
    condition and lookup table name from each transformation step.

    Use this when the user asks "what lookups are used for table X",
    "show me the lookup logic for F_PARTICIPANTS", or
    "what is the lookup condition applied to this table's fields".

    :param table_name: The table name (without schema prefix).
                       Example: "F_PARTICIPANTS"
    :return: JSON array of edges with non-empty lookup conditions, each containing
             edge_id, from_vertex, to_vertex, mapping_name, lookup_condition,
             and the transformation step that contains the lookup.
    :rtype: str
    """
    sql = """
        SELECT
            c.edge_id, c.from_vertex, c.to_vertex,
            c.mapping_name, c.folder_name,
            c.lookup_condition,
            c.transformation_steps_count,
            c.transformation_chain
        FROM c
        WHERE (CONTAINS(c.from_vertex, @table_name)
            OR CONTAINS(c.to_vertex,   @table_name))
          AND c.lookup_condition != null
        ORDER BY c.from_vertex
    """
    results_raw = _run_cosmos_query(sql, [{"name": "@table_name", "value": table_name.upper()}])
    results = json.loads(results_raw)
    # Filter in Python to exclude records where lookup_condition is empty/null
    filtered = [r for r in results if r.get("lookup_condition")]
    # From transformation_chain, extract only steps with lookup info
    for r in filtered:
        chain = r.get("transformation_chain", [])
        r["lookup_steps"] = [
            {
                "step"                : s["step"],
                "transformation_name" : s["transformation_name"],
                "transformation_type" : s["transformation_type"],
                "input_port"          : s["input_port"],
                "output_port"         : s["output_port"],
                "lookup_condition"    : s.get("lookup_condition", ""),
                "lookup_table_name"   : s.get("lookup_table_name", ""),
                "port_expression"     : s.get("port_expression", ""),
            }
            for s in chain
            if s.get("lookup_condition") or s.get("lookup_table_name")
        ]
        del r["transformation_chain"]   # remove full chain; only lookup_steps shown
    return json.dumps(filtered, ensure_ascii=False, indent=2)


# ─── Tool 5: Get SQL / filter logic for a mapping ───

def get_sql_and_filter_logic(mapping_name: str) -> str:
    """
    Retrieves all custom SQL queries, filter conditions, and update strategy
    expressions used within a specific Informatica mapping.
    Consolidates the SQL/filter logic across all edges so the user can see
    the full picture of how data is filtered and routed.

    Use this when the user asks "what SQL does mapping X use",
    "show me the filter conditions in mapping X", or
    "what is the update strategy for mapping X".

    :param mapping_name: The Informatica mapping name.
                         Example: "m_DDM_to_DDM_AGG_ACCOUNT_STAT60"
    :return: JSON array of edges that have non-empty custom_sql, filter_condition,
             or update_strategy_expression, with those fields highlighted.
    :rtype: str
    """
    sql = """
        SELECT
            c.edge_id, c.from_vertex, c.to_vertex,
            c.custom_sql, c.filter_condition,
            c.update_strategy_expression,
            c.transformation_steps_count
        FROM c
        WHERE c.mapping_name = @mapping_name
          AND (c.custom_sql != null
            OR c.filter_condition != null
            OR c.update_strategy_expression != null)
        ORDER BY c.from_vertex
    """
    results_raw = _run_cosmos_query(sql, [{"name": "@mapping_name", "value": mapping_name}])
    results = json.loads(results_raw)
    # Python-side filter: remove records where all three fields are empty/null
    filtered = [
        r for r in results
        if r.get("custom_sql") or r.get("filter_condition") or r.get("update_strategy_expression")
    ]
    # De-duplicate custom_sql (same SQ SQL appears on every field row)
    seen_sql = set()
    deduped = []
    for r in filtered:
        sql_val = r.get("custom_sql", "")
        if sql_val and sql_val in seen_sql:
            r["custom_sql"] = "(same as above — see first row)"
        elif sql_val:
            seen_sql.add(sql_val)
        deduped.append(r)
    return json.dumps(deduped, ensure_ascii=False, indent=2)
