# generate_backfill_sql.py
# ────────────────────────────────────────────────────────────────────────────
# Generates a backfill SQL statement for any DDM field by tracing its full
# upstream lineage through Neo4j (topology) and Cosmos DB (transformation
# logic), then assembling SELECT/JOIN/WHERE clauses from the chain.
#
# Usage:
#   python generate_backfill_sql.py CRDM_DDM.F_PARTICIPANTS.PARTICIPANT_KEY
#   python generate_backfill_sql.py F_PARTICIPANTS.PARTICIPANT_KEY
#   python generate_backfill_sql.py --table F_PARTICIPANTS --field PARTICIPANT_KEY
#
# Output: Prints the generated SQL and optionally writes to a .sql file.
# ────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import argparse
import textwrap
from pathlib import Path
from collections import defaultdict, OrderedDict
from dotenv import load_dotenv

# Load .env from lineage-agent directory
_SCRIPT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_SCRIPT_DIR / ".env")

sys.path.insert(0, str(_SCRIPT_DIR / "core_files"))

from neo4j_client import Neo4jLineageClient
from azure.cosmos import CosmosClient

from active_version import get_cosmos_data_version as _get_cosmos_data_version, get_neo4j_version as _get_neo4j_version


# ─── Configuration ──────────────────────────────────────────────────────────

COSMOS_ENDPOINT = os.environ.get("COSMOS_ENDPOINT")
COSMOS_KEY = os.environ.get("COSMOS_KEY")
COSMOS_DATABASE = "lineage"
COSMOS_CONTAINER = "transformation_details"
COSMOS_MAPPING_METADATA_CONTAINER = os.environ.get("COSMOS_MAPPING_METADATA_CONTAINER", "mapping_metadata")

LAYER_ORDER = {"TPR": 0, "TT": 1, "DDM": 2}


# ─── Data Access ────────────────────────────────────────────────────────────

class LineageDataAccess:
    """Wraps Neo4j and Cosmos DB queries needed for SQL generation."""

    def __init__(self):
        self.neo4j = Neo4jLineageClient()
        self._cosmos_container = None
        self._mapping_metadata_container = None

    @property
    def cosmos(self):
        if self._cosmos_container is None:
            client = CosmosClient(url=COSMOS_ENDPOINT, credential=COSMOS_KEY)
            db = client.get_database_client(COSMOS_DATABASE)
            self._cosmos_container = db.get_container_client(COSMOS_CONTAINER)
        return self._cosmos_container

    @property
    def mapping_metadata(self):
        if self._mapping_metadata_container is None:
            client = CosmosClient(url=COSMOS_ENDPOINT, credential=COSMOS_KEY)
            db = client.get_database_client(COSMOS_DATABASE)
            self._mapping_metadata_container = db.get_container_client(COSMOS_MAPPING_METADATA_CONTAINER)
        return self._mapping_metadata_container

    def get_upstream_edges(self, field_id: str) -> list:
        """Get all upstream edges for a field from Neo4j (backward traversal)."""
        vid = _get_neo4j_version()
        ver_clause = "WHERE anchor.version_id = $version_id" if vid else ""
        ver_params = {"version_id": vid} if vid else {}
        cypher = f"""
            MATCH (anchor:Field {{id: $field_id}})
            {ver_clause}
            MATCH path = (source:Field)-[:TRANSFORMS_TO*1..10]->(anchor)
            UNWIND range(0, length(path)-1) AS idx
            WITH relationships(path)[idx] AS rel,
                 nodes(path)[idx] AS from_node,
                 nodes(path)[idx+1] AS to_node
            RETURN DISTINCT
                   from_node.id             AS from_id,
                   from_node.field_name     AS from_field,
                   from_node.table_name     AS from_table,
                   from_node.db_schema      AS from_schema,
                   from_node.layer          AS from_layer,
                   from_node.data_type      AS from_data_type,
                   to_node.id               AS to_id,
                   to_node.field_name       AS to_field,
                   to_node.table_name       AS to_table,
                   to_node.db_schema        AS to_schema,
                   to_node.layer            AS to_layer,
                   rel.mapping_name         AS mapping_name,
                   rel.transformation_name  AS transformation_name,
                   rel.transformation_type  AS transformation_type,
                   rel.expression           AS expression
            ORDER BY from_layer ASC, from_table ASC
        """
        result = self.neo4j.run_cypher(cypher, {"field_id": field_id, **ver_params})
        return json.loads(result)

    def get_transformation_details(self, edge_id: str) -> dict | None:
        """Get full transformation chain from Cosmos DB for an edge."""
        sql = "SELECT * FROM c WHERE c.id = @edge_id"
        params = [{"name": "@edge_id", "value": edge_id}]
        vid = _get_cosmos_data_version()
        if vid:
            sql += " AND c.version_id = @vid"
            params.append({"name": "@vid", "value": vid})
        results = list(self.cosmos.query_items(
            query=sql, parameters=params, enable_cross_partition_query=True
        ))
        if results:
            for k in ("_rid", "_self", "_etag", "_attachments", "_ts"):
                results[0].pop(k, None)
            return results[0]
        return None

    def get_edges_for_target(self, to_vertex: str) -> list:
        """Get all Cosmos documents where to_vertex matches."""
        sql = """
            SELECT c.edge_id, c.from_vertex, c.to_vertex,
                   c.mapping_name, c.final_expression, c.custom_sql,
                   c.lookup_condition, c.filter_condition,
                   c.update_strategy_expression, c.transformation_chain
            FROM c WHERE c.to_vertex = @to_vertex
        """
        params = [{"name": "@to_vertex", "value": to_vertex}]
        vid = _get_cosmos_data_version()
        if vid:
            sql += " AND c.version_id = @vid"
            params.append({"name": "@vid", "value": vid})
        results = list(self.cosmos.query_items(
            query=sql, parameters=params, enable_cross_partition_query=True
        ))
        for r in results:
            for k in ("_rid", "_self", "_etag", "_attachments", "_ts"):
                r.pop(k, None)
        return results

    def get_mapping_metadata(self, mapping_name: str) -> dict | None:
        """Get mapping-level metadata (udfs, variables, session config) for one mapping."""
        sql = "SELECT * FROM c WHERE c.mapping_name = @mapping_name"
        params = [{"name": "@mapping_name", "value": mapping_name}]
        try:
            results = list(self.mapping_metadata.query_items(
                query=sql, parameters=params, enable_cross_partition_query=True
            ))
        except Exception:
            # mapping_metadata is optional; continue without it.
            return None
        if results:
            for k in ("_rid", "_self", "_etag", "_attachments", "_ts"):
                results[0].pop(k, None)
            return results[0]
        return None

    def close(self):
        self.neo4j.close()


# ─── Lineage Graph Builder ──────────────────────────────────────────────────

class LineageGraph:
    """Builds an in-memory directed graph from Neo4j edges, annotated with Cosmos details."""

    def __init__(self, edges: list, cosmos_details: dict):
        self.edges = edges
        self.cosmos_details = cosmos_details  # edge_id -> cosmos doc
        self.graph = defaultdict(list)  # to_id -> [edge_info]
        self.nodes = {}  # node_id -> {table, schema, layer, field}

        for e in edges:
            from_id = e["from_id"]
            to_id = e["to_id"]
            self.graph[to_id].append(e)
            self.nodes[from_id] = {
                "field": e["from_field"], "table": e["from_table"],
                "schema": e["from_schema"], "layer": e["from_layer"],
                "data_type": e.get("from_data_type", ""),
            }
            self.nodes[to_id] = {
                "field": e["to_field"], "table": e["to_table"],
                "schema": e["to_schema"], "layer": e["to_layer"],
            }

    def get_source_tables(self) -> list:
        """Return all leaf source nodes (nodes that have no incoming edges in our graph)."""
        all_to = set(self.graph.keys())
        all_from = set()
        for edges in self.graph.values():
            for e in edges:
                all_from.add(e["from_id"])
        sources = all_from - all_to
        return [self.nodes[s] for s in sources if s in self.nodes]

    def get_tables_by_layer(self) -> dict:
        """Group unique tables by layer."""
        tables_by_layer = defaultdict(OrderedDict)
        for node_id, info in self.nodes.items():
            key = f"{info['schema']}.{info['table']}"
            tables_by_layer[info["layer"]][key] = info
        return dict(tables_by_layer)

    def trace_path_to_target(self, target_id: str) -> list:
        """BFS backward from target to build ordered transformation steps."""
        visited = set()
        path_edges = []
        queue = [target_id]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            for edge in self.graph.get(current, []):
                path_edges.append(edge)
                queue.append(edge["from_id"])

        # Sort by layer order (TPR first, then TT, then DDM)
        path_edges.sort(key=lambda e: (
            LAYER_ORDER.get(e["from_layer"], 99),
            e["from_table"],
            LAYER_ORDER.get(e["to_layer"], 99),
        ))
        return path_edges


# ─── SQL Generator ──────────────────────────────────────────────────────────

class BackfillSQLGenerator:
    """Generates a backfill SQL statement from lineage topology + transformation details."""

    def __init__(self, target_field_id: str, graph: LineageGraph, mapping_metadata: dict | None = None):
        self.target_field_id = target_field_id
        self.graph = graph
        self.mapping_metadata = mapping_metadata or {}
        parts = target_field_id.split(".")
        self.target_schema = parts[0]
        self.target_table = parts[1]
        self.target_field = parts[2]

    def generate(self) -> str:
        """Main entry point: produce the SQL."""
        path_edges = self.graph.trace_path_to_target(self.target_field_id)
        if not path_edges:
            return f"-- No upstream lineage found for {self.target_field_id}"

        # Group edges by mapping (each mapping = one transformation step/subquery layer)
        mappings_ordered = []
        mapping_edges = defaultdict(list)
        seen_mappings = set()
        for e in path_edges:
            m = e["mapping_name"]
            if m not in seen_mappings:
                seen_mappings.add(m)
                mappings_ordered.append(m)
            mapping_edges[m].append(e)

        # Build layered SQL (CTE-based approach)
        sql_parts = []
        sql_parts.append(self._header_comment(path_edges))
        sql_parts.append(self._build_cte_sql(mappings_ordered, mapping_edges))
        return "\n".join(sql_parts)

    def _header_comment(self, path_edges: list) -> str:
        """Generate a header comment describing the lineage chain."""
        layers_seen = set()
        for e in path_edges:
            layers_seen.add(e["from_layer"])
            layers_seen.add(e["to_layer"])
        layer_flow = " → ".join(sorted(layers_seen, key=lambda x: LAYER_ORDER.get(x, 99)))

        lines = [
            f"-- ════════════════════════════════════════════════════════════════",
            f"-- BACKFILL SQL for: {self.target_field_id}",
            f"-- Layer flow: {layer_flow}",
            f"-- Generated from Neo4j lineage graph + Cosmos DB transformation details",
            f"-- ════════════════════════════════════════════════════════════════",
            f"--",
            f"-- IMPORTANT: This SQL is auto-generated from Informatica lineage metadata.",
            f"-- Review expressions and join conditions before executing.",
            f"-- Variables like $$APPL must be replaced with actual values.",
            f"-- ════════════════════════════════════════════════════════════════",
            f"",
        ]
        return "\n".join(lines)

    def _build_cte_sql(self, mappings_ordered: list, mapping_edges: dict) -> str:
        """Build CTE-based SQL that mirrors the transformation pipeline."""
        ctes = []
        final_mapping = mappings_ordered[-1] if mappings_ordered else None

        # Maps bare target-table name → CTE name that produces it.
        # Each subsequent CTE whose FROM table is already produced by a prior CTE
        # will read from that CTE (aliased as the original table name) instead of
        # the physical table, so the full pipeline is chained end-to-end.
        prior_cte_by_table: dict = {}

        for i, mapping_name in enumerate(mappings_ordered):
            edges = mapping_edges[mapping_name]
            # Build a per-edge cosmos doc lookup so each edge uses its own
            # final_expression, and joins/filters aggregate across all of them.
            edge_cosmos = {}
            for e in edges:
                doc = self._get_cosmos_for_edge(e)
                if doc:
                    edge_cosmos[f"{e['from_id']}__to__{e['to_id']}__{e['mapping_name']}"] = doc
            mapping_meta = self.mapping_metadata.get(mapping_name)
            cte = self._build_mapping_cte(mapping_name, edges, edge_cosmos, mapping_meta, i, prior_cte_by_table)
            if cte:
                # Register every target table this CTE produces
                for e in edges:
                    prior_cte_by_table[e["to_table"].upper()] = cte["name"]
                ctes.append(cte)

        if not ctes:
            return f"-- Could not generate SQL (no transformation details available)"

        # Assemble WITH ... SELECT
        sql_lines = ["WITH"]
        for i, cte in enumerate(ctes):
            comma = "," if i < len(ctes) - 1 else ""
            sql_lines.append(f"{cte['name']} AS (")
            sql_lines.append(textwrap.indent(cte["body"], "    "))
            sql_lines.append(f"){comma}")
            sql_lines.append("")

        # Final INSERT ... SELECT
        sql_lines.append(f"-- ═══ Final INSERT into {self.target_schema}.{self.target_table} ═══")
        sql_lines.append(f"INSERT INTO {self.target_schema}.{self.target_table} ({self.target_field})")
        last_cte = ctes[-1]["name"]
        sql_lines.append(f"SELECT {self.target_field}")
        sql_lines.append(f"FROM {last_cte};")

        return "\n".join(sql_lines)

    def _build_mapping_cte(self, mapping_name: str, edges: list, edge_cosmos: dict, mapping_meta: dict | None, index: int,
                            prior_cte_by_table: dict | None = None) -> dict | None:
        """Build one CTE representing a single mapping's transformation logic.

        :param edge_cosmos: Dict mapping edge_id → cosmos doc for each edge in
                            this mapping's lineage path. Each edge uses its own
                            doc for expression translation; joins/filters aggregate
                            across all docs.
        """
        # Determine source and target tables from edges
        source_tables = set()
        target_tables = set()
        select_exprs = OrderedDict()

        for e in edges:
            src = f"{e['from_schema']}.{e['from_table']}"
            tgt = f"{e['to_schema']}.{e['to_table']}"
            source_tables.add(src)
            target_tables.add(tgt)

            # Each edge uses its OWN cosmos doc for final_expression
            eid = f"{e['from_id']}__to__{e['to_id']}__{e['mapping_name']}"
            edge_doc = edge_cosmos.get(eid)
            expr = self._translate_expression(e, edge_doc, mapping_meta)
            alias = e["to_field"]
            select_exprs[alias] = expr

        cosmos_docs = list(edge_cosmos.values())

        # CTE name derived from mapping
        cte_name = self._sanitize_cte_name(mapping_name, index)

        # Build the CTE body
        body_lines = []

        # SELECT clause
        body_lines.append("SELECT")
        select_items = []
        for alias, expr in select_exprs.items():
            if expr and expr != alias:
                select_items.append(f"    {expr} AS {alias}")
            else:
                select_items.append(f"    {alias}")
        body_lines.append(",\n".join(select_items))

        # FROM clause — scan all cosmos docs to find the SQ join condition
        # and identify the driving table.
        from_table = self._find_driving_table(source_tables, cosmos_docs)
        from_bare  = from_table.split('.')[-1].upper()
        if prior_cte_by_table and from_bare in prior_cte_by_table:
            body_lines.append(f"FROM {prior_cte_by_table[from_bare]} AS {from_bare}")
        else:
            body_lines.append(f"FROM {from_table}")

        # JOIN clauses — scan all cosmos docs so lookup joins on specific
        # fields (e.g. lkp_MAST_LOAN_REC on CUST_NTE_NBR) are captured.
        joins = self._extract_joins(source_tables, cosmos_docs, from_table=from_table)
        if joins:
            body_lines.extend(joins)

        # WHERE clause (from filter conditions)
        where_clauses = self._extract_filters(cosmos_docs, edges, mapping_meta)
        if where_clauses:
            body_lines.append("WHERE " + "\n  AND ".join(where_clauses))

        return {"name": cte_name, "body": "\n".join(body_lines)}

    # Transformation types that are NOT real expressions — when final_expression
    # is one of these, walk the chain backward for the actual port_expression.
    _NON_EXPR_TYPES = {
        "Source Qualifier", "Router", "Update Strategy", "Filter",
        "Sequence Generator", "Joiner", "Normalizer", "Sorter",
        "Union", "Rank",
    }

    def _translate_expression(self, edge: dict, cosmos_doc: dict | None, mapping_meta: dict | None = None) -> str:
        """Convert an Informatica expression to SQL-compatible expression."""
        raw_expr = edge.get("expression", "") or ""
        from_field = edge["from_field"]
        to_field = edge["to_field"]

        if cosmos_doc:
            final = cosmos_doc.get("final_expression", "")
            if final and final not in self._NON_EXPR_TYPES:
                # final_expression is a real expression — use it
                raw_expr = final
            else:
                # final_expression is a type name or empty — walk the chain
                # backward for the last step that has a real port_expression.
                chain = cosmos_doc.get("transformation_chain", [])
                for step in reversed(chain):
                    pe = step.get("port_expression", "") or ""
                    step_type = step.get("transformation_type", "")
                    # Skip empty expressions and non-expression step types
                    if pe and pe not in self._NON_EXPR_TYPES and step_type not in self._NON_EXPR_TYPES:
                        # Skip trivial pass-throughs (output == input) — keep
                        # looking for a real computation further upstream.
                        inp = step.get("input_port", "")
                        if pe.upper() != inp.upper():
                            raw_expr = pe
                            break

        if not raw_expr or raw_expr in self._NON_EXPR_TYPES:
            # Direct pass-through
            return from_field

        # Translate common Informatica functions to SQL
        sql_expr = self._informatica_to_sql(raw_expr, edge)
        sql_expr = self._apply_udf_expansion(sql_expr, mapping_meta)
        sql_expr = self._apply_mapping_variables(sql_expr, mapping_meta)
        return sql_expr

    def _informatica_to_sql(self, expr: str, edge: dict) -> str:
        """Translate Informatica expression syntax to SQL."""
        sql = expr

        # Replace Informatica port references (i_FIELDNAME, o_FIELDNAME) with bare field names
        import re
        sql = re.sub(r'\bi_([A-Z_0-9]+)', r'\1', sql)
        sql = re.sub(r'\bo_([A-Z_0-9]+)', r'\1', sql)
        sql = re.sub(r'\bv_([A-Z_0-9]+)', r'\1', sql)

        # IIF -> CASE WHEN
        # Pattern: IIF(condition, true_val, false_val)
        # Uses balanced parenthesis matching to handle nested functions
        iif_pattern = re.compile(r'IIF\s*\(', re.IGNORECASE)
        while iif_pattern.search(sql):
            match = iif_pattern.search(sql)
            start = match.start()
            # Find the matching closing paren, splitting on top-level commas
            args = self._split_iif_args(sql[match.end():])
            if args and len(args) == 3:
                cond, true_val, false_val = [a.strip() for a in args[:3]]
                end_pos = match.end() + self._find_closing_paren(sql[match.end():])
                replacement = f"CASE WHEN {cond} THEN {true_val} ELSE {false_val} END"
                sql = sql[:start] + replacement + sql[end_pos + 1:]
            else:
                break  # avoid infinite loop on unparseable IIF

        # ISNULL -> IS NULL
        sql = re.sub(r'ISNULL\s*\(\s*([^)]+)\s*\)', r'\1 IS NULL', sql, flags=re.IGNORECASE)

        # DECODE -> CASE (simplified)
        # DECODE(field, val1, result1, val2, result2, ..., default)
        decode_pattern = re.compile(r'DECODE\s*\((.+)\)', re.IGNORECASE)
        match = decode_pattern.search(sql)
        if match:
            args = [a.strip() for a in match.group(1).split(",")]
            if len(args) >= 3:
                field = args[0]
                cases = []
                i = 1
                while i + 1 < len(args):
                    cases.append(f"WHEN {field} = {args[i]} THEN {args[i+1]}")
                    i += 2
                default = args[i] if i < len(args) else "NULL"
                case_sql = f"CASE {' '.join(cases)} ELSE {default} END"
                sql = decode_pattern.sub(case_sql, sql)

        # LTRIM/RTRIM/TRIM -> SQL TRIM
        sql = re.sub(r':UDF\.TRIM\(([^)]+)\)', r'TRIM(\1)', sql, flags=re.IGNORECASE)

        # TO_CHAR -> CAST ... AS VARCHAR
        sql = re.sub(r'TO_CHAR\(([^)]+)\)', r'CAST(\1 AS VARCHAR)', sql, flags=re.IGNORECASE)

        # String concatenation: || is already SQL standard
        # ERROR() -> raise (comment it out)
        sql = re.sub(r"ERROR\s*\('[^']*'\)", "NULL /* ERROR - should not occur */", sql, flags=re.IGNORECASE)

        return sql

    def _extract_joins(self, source_tables: set, cosmos_docs: list, from_table: str = "") -> list:
        """Extract JOIN clauses from all cosmos docs for this mapping's lineage edges."""
        joins = []
        driving_bare = from_table.split('.')[-1].upper() if from_table else ""
        seen_lookup_tables: set = set()
        seen_ud_joins: set = set()

        for doc in cosmos_docs:
            chain = doc.get("transformation_chain", [])

            for step in chain:
                lookup_cond = step.get("lookup_condition", "")
                lookup_table = step.get("lookup_table_name", "")
                if lookup_cond and lookup_table and lookup_table not in seen_lookup_tables:
                    seen_lookup_tables.add(lookup_table)
                    join_cond = self._translate_join_condition(lookup_cond, lookup_table, source_table=driving_bare)
                    joins.append(f"LEFT JOIN {lookup_table} ON {join_cond}")

                join_condition = step.get("join_condition", "")
                if not join_condition:
                    raw_attrs = step.get("raw_attributes", {})
                    join_condition = raw_attrs.get("User Defined Join", "")
                if join_condition and join_condition not in seen_ud_joins:
                    seen_ud_joins.add(join_condition)
                    join_map = self._parse_oracle_outer_joins(join_condition, driving_bare=driving_bare)
                    for joined_table, info in join_map.items():
                        cond_str = " AND ".join(info["conditions"])
                        joins.append(f"{info['type']} {joined_table} ON {cond_str}")

        return joins

    def _extract_filters(self, cosmos_docs: list, edges: list, mapping_meta: dict | None = None) -> list:
        """Extract WHERE clause conditions from filter/SQ filter conditions."""
        filters = []
        seen = set()

        for doc in cosmos_docs:
            flt = doc.get("filter_condition", "")
            if flt and flt not in seen:
                seen.add(flt)
                filters.append(flt)

            for step in doc.get("transformation_chain", []):
                flt = step.get("filter_condition", "")
                if flt and flt not in seen:
                    seen.add(flt)
                    filters.append(flt)

                raw_attrs = step.get("raw_attributes", {})
                src_filter = raw_attrs.get("Source Filter", "")
                if src_filter and src_filter not in seen:
                    seen.add(src_filter)
                    filters.append(src_filter)

        if mapping_meta:
            session = mapping_meta.get("session", {}) or {}
            for src_filter in (session.get("source_filter_overrides", {}) or {}).values():
                if src_filter and src_filter not in seen:
                    seen.add(src_filter)
                    filters.append(src_filter)

        filters = [self._apply_mapping_variables(f, mapping_meta) for f in filters]

        return filters

    def _apply_mapping_variables(self, expr: str, mapping_meta: dict | None = None) -> str:
        """Replace known Informatica mapping variables ($$VAR) with default literals."""
        if not expr or not mapping_meta:
            return expr
        variables = mapping_meta.get("mapping_variables", {}) or {}
        out = expr
        for var_name, var_value in variables.items():
            val = "" if var_value is None else str(var_value)
            if val == "":
                replacement = "''"
            else:
                replacement = val if val.isdigit() else f"'{val}'"
            out = out.replace(var_name, replacement)
        return out

    def _apply_udf_expansion(self, expr: str, mapping_meta: dict | None = None) -> str:
        """Expand simple :UDF.NAME(arg) calls using mapping-level UDF bodies when available."""
        if not expr or not mapping_meta:
            return expr
        udfs = mapping_meta.get("udfs", {}) or {}
        if not udfs:
            return expr

        import re

        def _expand(match):
            udf_name = match.group(1)
            arg_expr = match.group(2).strip()
            udf_body = udfs.get(udf_name, "")
            if not udf_body:
                return match.group(0)
            expanded = udf_body.replace("IN_STRING", arg_expr)
            return expanded

        return re.sub(r':UDF\.([A-Z_0-9]+)\(([^)]+)\)', _expand, expr, flags=re.IGNORECASE)

    def _translate_join_condition(self, lookup_cond: str, lookup_table: str = "", source_table: str = "") -> str:
        """
        Convert an Informatica lookup condition to an alias-qualified SQL JOIN ON clause.

        Informatica format: LOOKUP_COL = i_SOURCE_COL
        SQL output        : lookup_table.LOOKUP_COL = source_table.SOURCE_COL

        Rules applied per AND-separated predicate:
          - Left side  (lookup column): prefix with lookup_table if no dot present
          - Right side (input port)   : strip i_ / o_ prefix; prefix with source_table if no dot present
        """
        import re
        parts = [p.strip() for p in re.split(r'\bAND\b', lookup_cond, flags=re.IGNORECASE)]
        sql_parts = []
        for part in parts:
            part = part.strip()
            m = re.match(r'^(.+?)\s*=\s*(.+)$', part)
            if m:
                left  = m.group(1).strip()
                right = m.group(2).strip()
                # Strip input-port prefix from right-hand (source) side
                right = re.sub(r'^[io]_', '', right, flags=re.IGNORECASE)
                # Prefix bare left-side column (no dot) with lookup table name
                if lookup_table and '.' not in left:
                    left = f"{lookup_table}.{left}"
                # Prefix bare right-side column (no dot) with source table name
                if source_table and '.' not in right:
                    right = f"{source_table}.{right}"
                sql_parts.append(f"{left} = {right}")
            else:
                # Fallback: just strip port prefixes
                part = re.sub(r'\bi_([A-Z_0-9]+)', r'\1', part)
                part = re.sub(r'\bo_([A-Z_0-9]+)', r'\1', part)
                sql_parts.append(part)
        return " AND ".join(sql_parts)

    def _parse_oracle_outer_joins(self, join_cond: str, driving_bare: str = "") -> dict:
        """
        Parse Oracle (+) outer-join syntax from a Source Qualifier join condition
        and convert it to ANSI JOIN structure.

        :param driving_bare: Bare table name already in the FROM clause; never emitted as a JOIN.
        Returns an ordered dict: {TABLE_NAME: {'type': 'LEFT JOIN'|'INNER JOIN', 'conditions': [str]}}
        """
        import re
        from collections import OrderedDict
        predicates = [p.strip() for p in re.split(r'\bAND\b', join_cond, flags=re.IGNORECASE)]
        join_map: dict = OrderedDict()
        driving_upper = driving_bare.upper() if driving_bare else ""

        for pred in predicates:
            pred = pred.strip()
            if not pred:
                continue

            # Detect (+) on right side:  driving.col = joined.col (+)
            m_right = re.search(
                r'=\s*([A-Z_][A-Z0-9_]*)\.([A-Z_][A-Z0-9_]*)\s*\(\+\)',
                pred, re.IGNORECASE
            )
            # Detect (+) on left side:   joined.col (+) = driving.col
            m_left = re.match(
                r'([A-Z_][A-Z0-9_]*)\.([A-Z_][A-Z0-9_]*)\s*\(\+\)\s*=',
                pred, re.IGNORECASE
            )

            # Strip (+) markers to produce the clean predicate
            clean = re.sub(r'\s*\(\+\)', '', pred).strip()

            if m_right:
                outer_tbl = m_right.group(1).upper()
                join_type = 'LEFT JOIN'
            elif m_left:
                outer_tbl = m_left.group(1).upper()
                join_type = 'LEFT JOIN'
            else:
                # No (+): inner join — pick first non-driving table in the predicate
                tables = [t.upper() for t in re.findall(r'([A-Z_][A-Z0-9_]*)\.[A-Z_]', clean, re.IGNORECASE)]
                seen_tbls: list = []
                for t in tables:
                    if t not in seen_tbls:
                        seen_tbls.append(t)
                non_driving = [t for t in seen_tbls if t != driving_upper]
                outer_tbl = non_driving[0] if non_driving else (seen_tbls[0] if seen_tbls else None)
                join_type = 'INNER JOIN'

            # Never re-emit the driving/FROM table as a join target
            if outer_tbl and outer_tbl != driving_upper:
                if outer_tbl not in join_map:
                    join_map[outer_tbl] = {'type': join_type, 'conditions': []}
                join_map[outer_tbl]['conditions'].append(clean)

        return join_map

    def _find_driving_table(self, source_tables: set, cosmos_docs: list) -> str:
        """
        Identify the driving (FROM) table by inspecting the Source Qualifier join condition.
        The driving table is the one that never appears on the (+) / outer side.
        Falls back to the first alphabetically sorted source table if no join condition exists.
        """
        import re

        for doc in cosmos_docs:
            for step in doc.get("transformation_chain", []):
                join_cond = step.get("join_condition", "")
                if not join_cond:
                    raw_attrs = step.get("raw_attributes", {})
                    join_cond = raw_attrs.get("User Defined Join", "")
                if not join_cond:
                    continue

                # Tables that appear on the (+)/outer side are joined tables, not the driver
                outer_tables = {
                    m.upper() for m in re.findall(
                        r'([A-Z_][A-Z0-9_]*)\.(?:[A-Z_][A-Z0-9_]*)\s*\(\+\)',
                        join_cond, re.IGNORECASE
                    )
                }
                outer_tables |= {
                    m.upper() for m in re.findall(
                        r'([A-Z_][A-Z0-9_]*)\.(?:[A-Z_][A-Z0-9_]*)\s*\(\+\)\s*=',
                        join_cond, re.IGNORECASE
                    )
                }
                all_tables = {
                    t.upper() for t in re.findall(
                        r'([A-Z_][A-Z0-9_]*)\.[A-Z_]', join_cond, re.IGNORECASE
                    )
                }
                driving_bare = all_tables - outer_tables
                if driving_bare:
                    for st in sorted(source_tables):
                        if st.split('.')[-1].upper() in driving_bare:
                            return st

        return sorted(source_tables)[0] if source_tables else "UNKNOWN"

    def _split_iif_args(self, s: str) -> list | None:
        """Split IIF arguments at top-level commas, respecting nested parens and quotes."""
        depth = 0
        args = []
        current = []
        in_quote = False
        for ch in s:
            if ch == "'" and not in_quote:
                in_quote = True
                current.append(ch)
            elif ch == "'" and in_quote:
                in_quote = False
                current.append(ch)
            elif in_quote:
                current.append(ch)
            elif ch == '(':
                depth += 1
                current.append(ch)
            elif ch == ')':
                if depth == 0:
                    args.append("".join(current))
                    return args
                depth -= 1
                current.append(ch)
            elif ch == ',' and depth == 0:
                args.append("".join(current))
                current = []
            else:
                current.append(ch)
        return None

    def _find_closing_paren(self, s: str) -> int:
        """Find position of the matching closing paren for an already-opened paren."""
        depth = 0
        in_quote = False
        for i, ch in enumerate(s):
            if ch == "'" and not in_quote:
                in_quote = True
            elif ch == "'" and in_quote:
                in_quote = False
            elif in_quote:
                continue
            elif ch == '(':
                depth += 1
            elif ch == ')':
                if depth == 0:
                    return i
                depth -= 1
        return len(s) - 1

    def _get_cosmos_for_edge(self, edge: dict) -> dict | None:
        """Get Cosmos doc for an edge using the graph's cached details."""
        edge_id = f"{edge['from_id']}__to__{edge['to_id']}__{edge['mapping_name']}"
        return self.graph.cosmos_details.get(edge_id)

    def _sanitize_cte_name(self, mapping_name: str, index: int) -> str:
        """Create a valid SQL CTE name from a mapping name."""
        import re
        name = re.sub(r'[^A-Za-z0-9_]', '_', mapping_name)
        return f"step{index + 1}_{name}"


# ─── Orchestrator ───────────────────────────────────────────────────────────

def resolve_field_id(dao: LineageDataAccess, field_input: str, table_input: str = "") -> str:
    """Resolve user input to a fully qualified SCHEMA.TABLE.FIELD id."""
    # Already fully qualified
    if field_input.count(".") == 2:
        return field_input.upper()

    vid = _get_neo4j_version()
    ver_clause = "AND f.version_id = $version_id" if vid else ""
    ver_params = {"version_id": vid} if vid else {}

    # TABLE.FIELD format
    if field_input.count(".") == 1:
        table, field = field_input.split(".", 1)
        cypher = f"""
            MATCH (f:Field {{table_name: $table, field_name: $field}})
            WHERE 1=1 {ver_clause}
            RETURN f.id AS id, f.layer AS layer
            ORDER BY CASE f.layer WHEN 'DDM' THEN 0 WHEN 'TT' THEN 1 ELSE 2 END
            LIMIT 1
        """
        result = json.loads(dao.neo4j.run_cypher(cypher, {"table": table.upper(), "field": field.upper(), **ver_params}))
        if result:
            return result[0]["id"]
        raise ValueError(f"Field not found: {table}.{field}")

    # Bare field name with separate table
    if table_input:
        cypher = f"""
            MATCH (f:Field {{table_name: $table, field_name: $field}})
            WHERE 1=1 {ver_clause}
            RETURN f.id AS id, f.layer AS layer
            ORDER BY CASE f.layer WHEN 'DDM' THEN 0 WHEN 'TT' THEN 1 ELSE 2 END
            LIMIT 1
        """
        result = json.loads(dao.neo4j.run_cypher(cypher, {"table": table_input.upper(), "field": field_input.upper(), **ver_params}))
        if result:
            return result[0]["id"]
        raise ValueError(f"Field not found: {table_input}.{field_input}")

    raise ValueError(f"Cannot resolve field: {field_input}. Use SCHEMA.TABLE.FIELD or TABLE.FIELD format.")


def generate_backfill_sql(field_id: str, dao: LineageDataAccess = None, output_file: str = None) -> str:
    """
    Main function: trace lineage and generate backfill SQL for a target field.

    :param field_id: Fully qualified field id (SCHEMA.TABLE.FIELD)
    :param dao: Optional pre-initialized data access object
    :param output_file: Optional path to write the SQL file
    :return: The generated SQL string
    """
    own_dao = dao is None
    if own_dao:
        dao = LineageDataAccess()

    try:
        print(f"\n{'═' * 60}")
        print(f"  Generating backfill SQL for: {field_id}")
        print(f"{'═' * 60}\n")

        # Step 1: Get upstream lineage from Neo4j
        print("  [1/3] Querying Neo4j for upstream lineage...")
        edges = dao.get_upstream_edges(field_id)
        if not edges:
            msg = f"-- No upstream lineage found for {field_id}"
            print(f"  ⚠️  {msg}")
            return msg
        print(f"        Found {len(edges)} edge(s) across {len(set(e['mapping_name'] for e in edges))} mapping(s)")

        # Step 2: Enrich with Cosmos DB transformation details
        print("  [2/3] Fetching transformation details from Cosmos DB...")
        cosmos_details = {}
        mapping_metadata = {}
        edge_ids_to_fetch = set()
        mapping_names_to_fetch = set()
        for e in edges:
            edge_id = f"{e['from_id']}__to__{e['to_id']}__{e['mapping_name']}"
            edge_ids_to_fetch.add(edge_id)
            mapping_names_to_fetch.add(e["mapping_name"])

        for edge_id in edge_ids_to_fetch:
            doc = dao.get_transformation_details(edge_id)
            if doc:
                cosmos_details[edge_id] = doc

        for mapping_name in mapping_names_to_fetch:
            meta = dao.get_mapping_metadata(mapping_name)
            if meta:
                mapping_metadata[mapping_name] = meta

        print(f"        Retrieved {len(cosmos_details)} transformation detail(s)")
        print(f"        Retrieved {len(mapping_metadata)} mapping metadata record(s)")

        # Step 3: Build graph and generate SQL
        print("  [3/3] Generating SQL...\n")
        graph = LineageGraph(edges, cosmos_details)
        generator = BackfillSQLGenerator(field_id, graph, mapping_metadata=mapping_metadata)
        sql = generator.generate()

        # Output
        if output_file:
            Path(output_file).write_text(sql, encoding="utf-8")
            print(f"  ✅ SQL written to: {output_file}\n")

        return sql

    finally:
        if own_dao:
            dao.close()


# ─── CLI Entry Point ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate backfill SQL from lineage metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python generate_backfill_sql.py CRDM_DDM.F_PARTICIPANTS.PARTICIPANT_KEY
              python generate_backfill_sql.py F_PARTICIPANTS.PARTICIPANT_KEY
              python generate_backfill_sql.py --table F_PARTICIPANTS --field PARTICIPANT_KEY
              python generate_backfill_sql.py CRDM_DDM.F_PARTICIPANTS.PARTICIPANT_KEY --output backfill.sql
        """)
    )
    parser.add_argument("field", nargs="?", help="Field id: SCHEMA.TABLE.FIELD or TABLE.FIELD")
    parser.add_argument("--table", "-t", help="Table name (if field given separately)")
    parser.add_argument("--field-name", "-f", dest="field_name", help="Field name (use with --table)")
    parser.add_argument("--output", "-o", help="Output .sql file path")
    parser.add_argument("--print-only", action="store_true", help="Only print SQL, don't write file")

    args = parser.parse_args()

    # Determine the field identifier
    if args.field:
        field_input = args.field
        table_input = ""
    elif args.table and args.field_name:
        field_input = args.field_name
        table_input = args.table
    else:
        parser.error("Provide a field id (e.g. CRDM_DDM.F_PARTICIPANTS.PARTICIPANT_KEY) or use --table and --field-name")
        return

    dao = LineageDataAccess()
    try:
        # Resolve to full field id
        field_id = resolve_field_id(dao, field_input, table_input)
        print(f"  Resolved field: {field_id}")

        # Generate output filename if not specified
        output_file = args.output
        if not output_file and not args.print_only:
            parts = field_id.split(".")
            output_file = f"backfill_{parts[1]}_{parts[2]}.sql"

        # Generate SQL
        sql = generate_backfill_sql(field_id, dao=dao, output_file=output_file if not args.print_only else None)

        print("─" * 60)
        print(sql)
        print("─" * 60)

    except ValueError as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ❌ Unexpected error: {e}")
        raise
    finally:
        dao.close()


if __name__ == "__main__":
    main()
