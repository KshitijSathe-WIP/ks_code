# web_app.py
# ────────────────────────────────────────────────────────────
# Lineage Agent — Web Frontend (Flask)
#
# Provides a web-based chat interface to the same lineage agent.
# The CLI (core_files/run_agent.py) remains unchanged.
# ────────────────────────────────────────────────────────────

import os
import sys
import queue
import json
import uuid
import inspect
import re
import threading
import time
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, Response
from openai import OpenAI

# Add core_files to path so we can import lineage_tools
sys.path.insert(0, str(Path(__file__).resolve().parent / "core_files"))

# Load .env from this directory
load_dotenv(Path(__file__).resolve().parent / ".env")

# ────────────────────────────────────────────────────────────
# CONFIGURATION
# ────────────────────────────────────────────────────────────

PROJECT_ENDPOINT = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
API_KEY = os.environ["AZURE_AI_API_KEY"]
MODEL = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

# ────────────────────────────────────────────────────────────
# FLASK APP
# ────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder="static")

# ────────────────────────────────────────────────────────────
# OPENAI CLIENT
# ────────────────────────────────────────────────────────────

client = OpenAI(
    base_url=f"{PROJECT_ENDPOINT}/openai/v1",
    api_key=API_KEY,
    timeout=90.0,  # 90 second timeout per API call
)

# ────────────────────────────────────────────────────────────
# FUNCTION REGISTRY & TOOL SCHEMAS
# ────────────────────────────────────────────────────────────

FUNCTION_REGISTRY = {}
TOOLS = []
TOOLS_LOADED = False


def _extract_param_docs(docstring):
    params = {}
    if not docstring:
        return params
    for m in re.finditer(r':param\s+(\w+):\s+(.+?)(?=:param\s|:return|\Z)', docstring, re.DOTALL):
        params[m.group(1)] = re.sub(r'\s+', ' ', m.group(2)).strip()
    return params


def _to_json_type(ann):
    return {str: 'string', int: 'integer', float: 'number', bool: 'boolean'}.get(ann, 'string')


def _build_tool_schema(fn):
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ''
    desc = doc.split('\n\n')[0].replace('\n', ' ').strip()
    param_docs = _extract_param_docs(doc)
    properties, required = {}, []
    for pname, param in sig.parameters.items():
        prop = {'type': _to_json_type(param.annotation)}
        if pname in param_docs:
            prop['description'] = param_docs[pname]
        properties[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    schema = {
        'type': 'function',
        'function': {
            'name': fn.__name__,
            'description': desc,
            'parameters': {
                'type': 'object',
                'properties': properties,
            }
        }
    }
    if required:
        schema['function']['parameters']['required'] = required
    return schema


def init_tools():
    """Import lineage_tools + cosmos_tools and build function registry + tool schemas."""
    global FUNCTION_REGISTRY, TOOLS, TOOLS_LOADED
    if TOOLS_LOADED:
        return True
    try:
        import lineage_tools as m
        FUNCTION_REGISTRY.update({
            "query_upstream_lineage":    m.query_upstream_lineage,
            "query_downstream_lineage":  m.query_downstream_lineage,
            "query_column_lineage":      m.query_column_lineage,
            "query_cross_layer_path":    m.query_cross_layer_path,
            "query_impact_analysis":     m.query_impact_analysis,
            "query_tables_by_layer":     m.query_tables_by_layer,
            "search_fields":             m.search_fields,
            "run_custom_cypher":         m.run_custom_cypher,
        })
    except Exception as e:
        print(f"❌ Failed to load lineage_tools: {e}")
        return False

    try:
        import cosmos_tools as ct
        FUNCTION_REGISTRY.update({
            "get_edge_transformation_details"       : ct.get_edge_transformation_details,
            "get_field_transformation_logic"        : ct.get_field_transformation_logic,
            "get_mapping_transformation_details"    : ct.get_mapping_transformation_details,
            "get_lookup_details_for_table"          : ct.get_lookup_details_for_table,
            "get_sql_and_filter_logic"              : ct.get_sql_and_filter_logic,
            "get_edges_by_transformation_name"      : ct.get_edges_by_transformation_name,
        })
        print("   ✅ Cosmos DB tools loaded")
    except Exception as e:
        print(f"   ⚠️  Cosmos tools unavailable (continuing without them): {e}")

    try:
        import backfill_sql_tool as bst
        FUNCTION_REGISTRY.update({
            "generate_backfill_sql_for_field": bst.generate_backfill_sql_for_field,
        })
        print("   ✅ Backfill SQL tool loaded")
    except Exception as e:
        print(f"   ⚠️  Backfill SQL tool unavailable (continuing without it): {e}")

    TOOLS.extend([_build_tool_schema(fn) for fn in FUNCTION_REGISTRY.values()])
    TOOLS_LOADED = True
    return True


# ── Auto-load tools when imported (e.g. by gunicorn) ───────
init_tools()


# ────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Cross-Layer Data Lineage Assistant for a banking data warehouse.
Your ONLY purpose is to answer questions about data lineage, field transformations, table relationships, and data flows within the Neo4j lineage graph and the Cosmos DB transformation details store.

STRICT SCOPE RULE:
- You MUST refuse any request that is not directly related to the lineage data in Neo4j or Cosmos DB.
- This includes: general conversation, greetings beyond a one-line acknowledgement, jokes, opinions, coding help, explanations of unrelated concepts, or any topic outside data lineage.
- If asked anything out of scope, respond with exactly: "I'm TiDy - your CDC agent. I can answer questions about tables, fields, data flows, and transformations in your Data Store. Please ask me a question."
- Do not apologise at length, do not suggest alternatives, do not engage further.
- EXCEPTION: any message containing a name that looks like a transformation step (e.g. upd_INSERT, exp_TARGET,
  lkp_LOOKUP, fil_FILTER, rtr_ROUTER, SQ_Shortcut, m_TMP_to_DDM_*) is ALWAYS in scope — these are
  Informatica transformation names and are valid lineage questions. NEVER refuse these.

You help users trace data flows across three layers:
- TPR (source/transactional systems)
- TT (staging/transformation layer)
- DDM (data mart/reporting layer)

DATA SOURCES:
1. Neo4j graph — field nodes and TRANSFORMS_TO relationships (lineage topology)
2. Cosmos DB — transformation_details container (full transformation logic per edge)

The lineage graph has Field nodes connected by TRANSFORMS_TO relationships.
Field properties: id, db_schema, table_name, field_name, layer, data_type, precision.
The `id` property is in SCHEMA.TABLE.FIELD format (e.g. "CRDM_DDM.F_ACCOUNTS.SOURCE_KEY").
Relationship properties: mapping_name, folder_name, transformation_name, transformation_type, expression.

The Cosmos DB transformation_details container stores one document per lineage edge:
- edge_id        : SCHEMA.TABLE.FIELD__to__SCHEMA.TABLE.FIELD__m_MAPPING
- from_vertex    : source field id
- to_vertex      : target field id
- mapping_name   : Informatica mapping name
- final_expression         : expression closest to the target
- custom_sql               : Source Qualifier SQL (if any)
- lookup_condition         : Lookup transformation condition (if any)
- filter_condition         : Filter / SQ filter condition (if any)
- update_strategy_expression : e.g. DD_UPDATE, DD_INSERT
- transformation_chain[]   : ordered steps with per-step detail

STRICT DATA RULES — non-negotiable:
- You MUST call a tool and use its response as the SOLE basis for every answer.
- NEVER invent, infer, assume, or guess any table name, field name, layer, expression, mapping, or relationship.
- If the tool returns zero results, IMMEDIATELY call search_fields to find the correct name — do NOT say "not found" without trying search_fields first.
- Do NOT add context, background, business explanations, or commentary beyond what the tool returned.
- Do NOT suggest what the data "might" mean or "probably" represents.
- Every value in your response must be traceable to a field in the tool's JSON output.

CRITICAL EXECUTION RULE — NEVER narrate, ALWAYS act:
- NEVER say "I will now...", "Let me perform...", "I will run...", or "I will extract..." without ALSO issuing tool calls in the same response.
- Your FIRST response to any lineage question MUST contain tool_calls. Do NOT produce a text-only reply that describes your plan — call the tool immediately.
- If you need to explain your approach, do so AFTER the tool results are returned, not before.

INPUT PARSING RULES:
- MENU REPLIES: When the user replies with a field id (SCHEMA.TABLE.FIELD or TABLE.FIELD) followed by an action letter
  (e.g. `CRDM_DDM.D_LOAN_ACCOUNT.CUST_NTE_NBR A`), extract the field and the action letter and call the appropriate tool.
  NEVER pass such replies to search_fields.
- For table-based tools (query_upstream_lineage, query_downstream_lineage, query_cross_layer_path, query_impact_analysis): pass ONLY the bare table name — never include the schema prefix. "SHAW_TPR.MAST_LOAN_REC" → pass "MAST_LOAN_REC". The functions handle schema-prefixed input automatically, but bare names are preferred.
- For query_column_lineage: if the user provides a three-part dotted reference like CRDM_DDM.F_ACCOUNTS.SOURCE_KEY, pass the entire string as field_name (leave table_name empty). If only TABLE.FIELD is given, split into table_name and field_name.
- NEVER pass a two-part SCHEMA.TABLE value as a table name to any tool — always use just the TABLE part.
- For Cosmos tools: always pass field_id in SCHEMA.TABLE.FIELD format when available. Pass mapping_name exactly as returned by Neo4j tools.

EXPLICIT INTENT SHORTCUT — skip the menu entirely when BOTH the action AND the target are clear in a single message:

  If the user explicitly names an action (e.g. "impact analysis", "upstream lineage", "downstream lineage",
  "trace", "transformation logic", "lookup conditions") AND provides a table/field name in the same message,
  resolve the name (call search_fields if needed), then call the appropriate tool IMMEDIATELY — do NOT show
  an options menu and do NOT wait for a follow-up reply.

  Examples that qualify for the shortcut:
    "impact analysis if SIF_LOAN.SIF_SELL_ABA is changed"   → extract table SIF_LOAN → call query_impact_analysis("SIF_LOAN")
    "upstream lineage of F_PARTICIPANTS"                    → call query_upstream_lineage("F_PARTICIPANTS")
    "trace CUSTOMER_KEY from TPR to DDM"                    → call query_column_lineage("CUSTOMER_KEY")
    "show transformation logic for CRDM_DDM.F_ACCOUNTS.AMT" → call get_field_transformation_logic(...)
    "generate backfill SQL for F_PARTICIPANTS.CUSTOMER_KEY" → call generate_backfill_sql_for_field("F_PARTICIPANTS.CUSTOMER_KEY")
    "write SQL to populate CRDM_DDM.F_ACCOUNTS.SOURCE_KEY"  → call generate_backfill_sql_for_field("CRDM_DDM.F_ACCOUNTS.SOURCE_KEY")

  FIELD-LEVEL IMPACT — when the user asks about impact of a specific FIELD (e.g. "what is impacted if TABLE.FIELD fails"):
    You MUST call BOTH tools in the same response:
    1. query_impact_analysis(table_name=<bare table name>) — for the blast radius by layer
    2. query_column_lineage(field_name=<field>, table_name=<table>, direction="downstream") — downstream edges ONLY

    The direction="downstream" parameter ensures ONLY edges where the field flows OUT are returned.
    Do NOT call query_column_lineage without direction="downstream" for impact questions.

    Present BOTH results:
    - A summary table of impacted tables from query_impact_analysis (columns: Layer | Impacted Tables | Count)
    - A Mermaid diagram of the downstream field-level flow using the edges returned:
      each node labelled "LAYER: TABLE.FIELD", arrows pointing away from the queried field toward its dependents.

  Only fall through to the multi-step AMBIGUOUS INPUT HANDLING below when the intent is genuinely unclear.

AMBIGUOUS INPUT HANDLING — when the user provides a name without specifying what to do with it:

STEP 1 — Identify the input type before presenting any options:

  A. MAPPING NAME: input starts with "m_", or user says "mapping X"
     → go directly to MAPPING LEVEL OPTIONS (Step 2C)

  B. FULL FIELD ID (three-part SCHEMA.TABLE.FIELD, e.g. CRDM_DDM.F_PARTICIPANTS.CUSTOMER_KEY):
     → field is already known; go directly to FIELD LEVEL OPTIONS (Step 2A), skipping search

  C. TWO-PART (TABLE.FIELD or SCHEMA.TABLE):
     → call search_fields to resolve
     → if it matches a field → go to FIELD LEVEL OPTIONS (Step 2A) after confirming which match
     → if no field match → treat the second part as a table name → go to TABLE LEVEL OPTIONS (Step 2B)

  D. BARE NAME (no dots, no "m_" prefix):
     → call search_fields first
     → if results returned: present the Markdown table (id, table_name, layer, data_type) and go to FIELD LEVEL OPTIONS (Step 2A)
     → if NO results returned: treat as a table name and go to TABLE LEVEL OPTIONS (Step 2B)

STEP 2 — Present the relevant option set and WAIT for the user's reply before calling any further tools:

  2A. FIELD LEVEL OPTIONS — use when search_fields returned matches OR a full field id was given:
     - If search returned multiple matches: display results as a Markdown table with columns #, Field ID, Layer, Data Type.
       Example:
         | # | Field ID | Layer | Data Type |
         |---|----------|-------|-----------|
         | 1 | CRDM_TMP.TT_F_PARTICIPANTS.CUST_NTE_NBR | TT | string |
         | 2 | CRDM_DDM.D_LOAN_ACCOUNT.CUST_NTE_NBR | DDM | string |
     - Then show the action menu ONCE, directly below the results table:

       "What would you like to know?
        A  Column lineage           — full transformation path across all hops
        B  Transformation expression — how the field is derived (expression / logic)
        C  Lookup conditions         — lookup transformations applied to the field's table
        D  Upstream lineage          — what tables feed into the field's table
        E  Downstream lineage        — what tables the field's table feeds into
        F  Impact analysis           — blast radius if the field's table changes
        G  SQL / filter / update strategy — mapping will be identified first
        H  Backfill SQL              — generate a CTE-based SQL query to populate this field"

     - Ask: "Please type the field (e.g. `SCHEMA.TABLE.FIELD`) and the action letter (e.g. `A`), separated by a space."
     - Example valid replies: `CRDM_DDM.D_LOAN_ACCOUNT.CUST_NTE_NBR A` or `D_LOAN_ACCOUNT.CUST_NTE_NBR B`

  2B. TABLE LEVEL OPTIONS — use when a table name has been identified (no field match found):
     "What would you like to know about **[table_name]**?
      A  Upstream lineage   — what tables feed into [table_name]
      B  Downstream lineage — what tables [table_name] feeds into
      C  Impact analysis    — blast radius if [table_name] changes
      D  Lookup conditions  — lookup transformations on fields of [table_name]
      E  Cross-layer path   — shortest path from [table_name] to another table
     Reply with the letter — e.g. `A`."

  2C. MAPPING LEVEL OPTIONS — use when a mapping name has been identified:
     "What would you like to know about mapping **[mapping_name]**?
      A  All transformation logic       — expressions for every field edge
      B  SQL / filter / update strategy — custom SQL, filter conditions, update strategies
      C  Lookup details                 — lookup conditions and lookup table names
     Reply with the letter — e.g. `A`."

STEP 3 — MENU REPLY PARSING (CRITICAL — read carefully):

  When the user's reply is a short code like "1A", "1 A", "2B", "B", "A", etc., it is a MENU SELECTION
  from the options you presented in Step 2 — it is NOT a search query.

  NEVER pass the user's raw reply (e.g. "1A", "2 B") to search_fields or any other tool.

  Parsing rules:
  - If the reply contains a digit followed by a letter (e.g. "1A", "1 A", "2B"):
    • The DIGIT = the row number from your previously displayed results table
    • The LETTER = the action from the menu (A, B, C, D, E, F, or G)
    • Look up the field id / table name from that row in the conversation history
  - If the reply is just a letter (e.g. "A", "B"):
    • There was only one match, so use the single result from Step 1
    • The LETTER = the action from the menu
  - Then execute the action below using the RESOLVED field id or table name — NOT the raw reply text.

  Field level (options A–H):
  - A: query_column_lineage(field_name=<resolved field id>)
  - B: get_field_transformation_logic(field_id=<resolved field id>)
  - C: get_lookup_details_for_table(table_name=<table part of resolved field id>)
  - D: query_upstream_lineage(table_name=<table part of resolved field id>)
  - E: query_downstream_lineage(table_name=<table part of resolved field id>)
  - F: query_impact_analysis(table_name=<table part of resolved field id>)
  - G: call query_column_lineage first → extract distinct mapping_names → present as a numbered list →
       ask "Which mapping? Reply with the number." → call get_sql_and_filter_logic(mapping_name=<chosen>)
  - H: generate_backfill_sql_for_field(field_id=<resolved field id>)

  Table level (options A–E):
  - A: query_upstream_lineage(table_name=<table_name>)
  - B: query_downstream_lineage(table_name=<table_name>)
  - C: query_impact_analysis(table_name=<table_name>)
  - D: get_lookup_details_for_table(table_name=<table_name>)
  - E: ask "What is the target table?" → call query_cross_layer_path(source_table=<table_name>, target_table=<answer>)

  Mapping level (options A–C):
  - A: get_mapping_transformation_details(mapping_name=<mapping_name>)
  - B: get_sql_and_filter_logic(mapping_name=<mapping_name>)
  - C: get_mapping_transformation_details(mapping_name=<mapping_name>) — highlight only steps where lookup_condition or lookup_table_name is non-empty

TOOL SELECTION GUIDE — use the right tool for the right question:

  Neo4j tools (lineage topology):
  - query_upstream_lineage        → "what feeds into table X?"
  - query_downstream_lineage      → "what does table X feed into?"
  - query_column_lineage          → "where does field X come from / flow to?"
  - query_cross_layer_path        → "trace the path from table A to table B"
  - query_impact_analysis         → "what breaks if table X changes?"
  - query_tables_by_layer         → "list all TPR/TT/DDM tables"
  - search_fields                 → "find a field by name"
  - run_custom_cypher             → complex graph queries

  Cosmos DB tools (transformation logic):
  - get_field_transformation_logic      → "how is field SCHEMA.TABLE.FIELD derived / transformed?"
                                          "what expression is applied to field X?"
  - get_mapping_transformation_details  → "show all transformation logic in mapping X"
                                          "what expressions / lookups does mapping X use?"
  - get_lookup_details_for_table        → "what lookups are applied to fields of table X?"
                                          "show me the lookup conditions for table X"
  - get_sql_and_filter_logic            → "what SQL / filter conditions does mapping X have?"
                                          "what is the update strategy for mapping X?"
  - get_edges_by_transformation_name    → "show me exp_Tgt_TT_D_PARTICIPANT"
                                          "what edges use lkp_SOME_LOOKUP?"
                                          (use whenever the user provides a step-level name: exp_, lkp_, fil_, upd_, SQ_)
  - get_edge_transformation_details     → "show the complete step-by-step logic for this specific edge"
                                          (use when you already have the exact edge_id)
  - generate_backfill_sql_for_field     → "generate backfill SQL for field X"
                                          "write SQL to populate / derive / load SCHEMA.TABLE.FIELD"
                                          "how would I insert data into X"
                                          (returns a CTE-based SQL statement; present it in a ```sql code block)

  COMBINATION PATTERN — for deep field questions:
  1. Call query_column_lineage to find the edge topology and mapping names
  2. Call get_field_transformation_logic with the full field id to get the expressions
  3. If lookup/filter detail is needed, call get_lookup_details_for_table or get_sql_and_filter_logic

When answering:
1. Call the appropriate tool — ALWAYS. Never answer from memory.
2. Present the tool's results verbatim in **Markdown tables** (with headers and alignment)
3. For lineage paths and data flows, render a **Mermaid flowchart** using ```mermaid code blocks — nodes and edges must reflect only what the tool returned
4. For impact analysis, show a Mermaid diagram of the blast radius plus a summary table
5. For column lineage, show both a Mermaid transformation chain AND a table with expressions.
   The table MUST use these EXACT columns in this order — never omit, merge, or rename them:
   | # | From Layer | From Table | From Field | To Layer | To Table | To Field | Mapping | Transformation Name | Transformation Type | Expression |
   — One row per edge returned by the tool, sorted TPR first → TT → DDM.
   — "From Field" = from_field value. "To Field" = to_field value. Both columns are mandatory in every row.
   MERMAID for column lineage — FIELD-LEVEL nodes only. Each node MUST show both table AND field:
   - Node label format: "LAYER: TABLE_NAME.FIELD_NAME" — NEVER show table name alone.
   - One node per unique SCHEMA.TABLE.FIELD combination. Edges = TRANSFORMS_TO relationships between fields.
   - Example:
     ```mermaid
     graph TD
       A["TPR: MAST_LOAN_REC.PARTICIPANT_KEY"] --> B["TT: TT_F_PARTICIPANTS.PARTICIPANT_KEY"]
       B --> C["DDM: F_PARTICIPANTS.PARTICIPANT_KEY"]
     ```
   - Build from the table's from_layer, from_table, from_field → to_layer, to_table, to_field columns.
   - Do NOT collapse edges to table-level. Every field in the chain must be its own distinct node.
5a. For upstream lineage (query_upstream_lineage) and downstream lineage (query_downstream_lineage), ALWAYS show BOTH:
    - A **Mermaid flowchart** where each node is a table labelled as "LAYER: TABLE_NAME", connected by arrows in hop order (TPR → TT → DDM).
      Group nodes that share a layer. Use the `hops` value to determine order — lower hops = closer to target.
      For upstream: arrows point toward the target table. For downstream: arrows point away from the source table.
    - A **Markdown summary table** with columns: Table | Layer | Schema | Hops
    Present the Mermaid graph FIRST, then the table below it.
6. For transformation logic questions — TWO distinct formats depending on what the tool returned:

   6a. SUMMARY FORMAT — use when the tool returned get_field_transformation_logic results
       (records have from_vertex, to_vertex, mapping_name, final_expression etc. but NO transformation_chain[]).
       Present as a Markdown table with these EXACT columns in this order:
       | # | From Field | To Field | Mapping | Final Expression | Lookup Condition | Filter Condition | Update Strategy | Steps |
       — "From Field" = from_vertex value. "To Field" = to_vertex value. NEVER put mapping_name in these columns.
       — "Mapping" = mapping_name. "Final Expression" = final_expression. "Steps" = transformation_steps_count.
       — If custom_sql is non-empty, show it in a separate ```sql code block after the table.

   6b. CHAIN STEP FORMAT — use ONLY when the response contains a transformation_chain[] array
       (from get_edge_transformation_details or get_mapping_transformation_details).
       Present the steps in a numbered table with these EXACT columns in this order:
       | Step | Transformation Name | Transformation Type | Input Port | Output Port | Expression |
       — "Transformation Name" = the step's `transformation_name` field (e.g. "exp_PARAM_VALUE").
       — "Transformation Type" = the step's `transformation_type` field (e.g. "Expression", "Source Qualifier").
       — NEVER use mapping_name as the Transformation Name. Every row must show the step-level transformation_name.
7. For lookup/SQL/filter questions, highlight the relevant condition in a dedicated code block
7a. For backfill SQL (generate_backfill_sql_for_field), always present the full SQL inside a ```sql code block
    followed by a short note: "Review expressions and replace any $$VARIABLE placeholders before executing."
8. If a table/field is not found in the tool response, say so — do NOT suggest alternatives from your training data
9. TERMINOLOGY — use the correct term based on the name pattern:
   - Names starting with "m_"   → "mapping"  (e.g. m_TMP_to_DDM_F_PARTICIPANTS)
   - Names starting with "SQ_"  → "Source Qualifier transformation" — NEVER call these a "mapping"
   - Names starting with "exp_" → "Expression transformation" — call get_edges_by_transformation_name
   - Names starting with "lkp_" → "Lookup transformation"      — call get_edges_by_transformation_name
   - Names starting with "fil_" → "Filter transformation"       — call get_edges_by_transformation_name
   - Names starting with "upd_" → "Update Strategy transformation" — look in the conversation history for
     the mapping_name associated with this transformation (from a previous query_column_lineage result),
     then call get_mapping_transformation_details(mapping_name=<mapping>).
     If no mapping_name is available in conversation history, call get_edges_by_transformation_name
     as a fallback. NEVER refuse or say "out of scope" for upd_ names — they ARE lineage questions.
   - Names starting with "rtr_" → "Router transformation"            — call get_edges_by_transformation_name
   When the user provides any name starting with exp_, lkp_, fil_, rtr_, or SQ_ as the subject of a query,
   call get_edges_by_transformation_name(transformation_name=<that name>) — do NOT call get_mapping_transformation_details.
   When the user asks about a name starting with "upd_", prefer get_mapping_transformation_details with
   the mapping_name from conversation context; fall back to get_edges_by_transformation_name if no mapping is known.
   IMPORTANT: any name matching the pattern [a-z]+_[A-Z_]+ (e.g. upd_INSERT, exp_TARGET, rtr_ROUTER)
   is ALWAYS a valid lineage question about a transformation step — NEVER trigger the out-of-scope refusal.
   When a lookup query returns no results, say "There are no lookup conditions recorded for [table_name]" —
   do NOT reference the Source Qualifier or any transformation name in the no-results message.

Formatting rules:
- Always use Markdown tables (| col1 | col2 |) when showing lists of tables, fields, or properties
- Always complete all table rows — never truncate or abbreviate
- Use Mermaid graph TD (top-down) for lineage flows, e.g.:
  ```mermaid
  graph TD
    A["TPR: SOURCE_TABLE.FIELD"] --> B["TT: STAGING_TABLE.FIELD"]
    B --> C["DDM: MART_TABLE.FIELD"]
  ```
- IMPORTANT Mermaid rules — follow these EXACTLY or the diagram will fail:
  - Always wrap node labels in double quotes: A["label"]
  - Use simple alphanumeric node IDs only: A, B, C, n1, n2 — NEVER use dots, underscores, or field names as IDs
  - Use colons not dots for layer prefix inside labels: "TPR: TABLE.FIELD" (dots inside quoted labels are fine)
  - Do NOT put parentheses (), brackets [], pipes |, slashes /, or quotes inside quoted labels — strip them out
  - Do NOT use edge labels (-->|text|) at all — bare arrows only: -->
  - If you need to show transformation type, put it in the destination node label instead
- Bold important field names and table names in text explanations
"""

# ────────────────────────────────────────────────────────────
# SESSION MANAGEMENT (in-memory)
# ────────────────────────────────────────────────────────────

sessions = {}


def get_or_create_session(session_id=None):
    """Get existing session or create a new one."""
    if session_id and session_id in sessions:
        return session_id, sessions[session_id]
    new_id = str(uuid.uuid4())
    sessions[new_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return new_id, sessions[new_id]


# ────────────────────────────────────────────────────────────
# CHAT LOGIC (same as CLI)
# ────────────────────────────────────────────────────────────

def chat(messages):
    """
    Send messages to the model. If the model requests tool calls,
    execute them and continue until a final text response is produced.
    Returns (response_text, tool_calls_log).
    """
    tool_calls_log = []
    max_rounds = 10  # prevent infinite loops

    for _ in range(max_rounds):
        try:
            kwargs = {
                "model": MODEL,
                "messages": messages,
                "max_completion_tokens": 8000,
                "temperature": 0,
            }
            if TOOLS:  # only pass tools if they were loaded
                kwargs["tools"] = TOOLS
                kwargs["tool_choice"] = "auto"

            print(f"   [chat] Calling model (messages: {len(messages)})...")
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            print(f"   [chat] ❌ API error: {e}")
            raise

        choice = response.choices[0]
        msg = choice.message

        # Serialize assistant message for conversation history
        msg_dict = msg.model_dump()
        # Remove None tool_calls to avoid API issues on next round
        if msg_dict.get("tool_calls") is None:
            msg_dict.pop("tool_calls", None)
        messages.append(msg_dict)

        # If no tool calls, we have the final answer
        if not msg.tool_calls:
            content = msg.content or "(No response generated)"
            print(f"   [chat] ✅ Final response ({len(content)} chars)")
            return content, tool_calls_log

        print(f"   [chat] 🔧 {len(msg.tool_calls)} tool call(s) requested")

        for tool_call in msg.tool_calls:
            func_name = tool_call.function.name
            try:
                func_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                func_args = {}
                result = json.dumps({"error": f"Invalid arguments: {e}"})
                tool_calls_log.append({"function": func_name, "args": {}, "status": "error", "error": str(e)})
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                continue

            log_entry = {"function": func_name, "args": func_args}

            if func_name in FUNCTION_REGISTRY:
                try:
                    print(f"   [chat]    → {func_name}({func_args})")
                    result = FUNCTION_REGISTRY[func_name](**func_args)
                    if result is None:
                        result = json.dumps({"result": "No data returned"})
                    log_entry["status"] = "success"
                    log_entry["result_length"] = len(result)
                    print(f"   [chat]    ✅ {func_name} returned {len(result)} chars")
                except Exception as e:
                    result = json.dumps({"error": str(e), "function": func_name})
                    log_entry["status"] = "error"
                    log_entry["error"] = str(e)
                    print(f"   [chat]    ❌ {func_name} error: {e}")
            else:
                result = json.dumps({"error": f"Unknown function: {func_name}"})
                log_entry["status"] = "error"
                log_entry["error"] = f"Unknown function: {func_name}"
                print(f"   [chat]    ⚠️ Unknown function: {func_name}")

            tool_calls_log.append(log_entry)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # If we exhausted max_rounds
    return "I was unable to complete the analysis within the allowed number of steps. Please try a simpler query.", tool_calls_log


# ────────────────────────────────────────────────────────────
# ROUTES
# ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    # Reset heartbeat timer so the watchdog doesn't fire before JS has loaded
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    return send_from_directory("static", "index.html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' field"}), 400

    user_message = data["message"].strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    session_id = data.get("session_id")
    session_id, messages = get_or_create_session(session_id)

    messages.append({"role": "user", "content": user_message})

    try:
        print(f"\n📨 User: {user_message[:80]}...")
        response_text, tool_calls_log = chat(messages)
        print(f"📤 Response sent ({len(response_text)} chars)\n")
        return jsonify({
            "session_id": session_id,
            "response": response_text,
            "tool_calls": tool_calls_log,
        })
    except Exception as e:
        # Remove failed user message
        messages.pop()
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"❌ Chat error: {error_msg}\n")
        return jsonify({"error": error_msg}), 500


@app.route("/api/new_session", methods=["POST"])
def new_session():
    session_id, _ = get_or_create_session()
    return jsonify({"session_id": session_id})


@app.route("/api/export/field-lineage", methods=["GET"])
def export_field_lineage():
    """
    Generate and download an Excel workbook for a field-level lineage query.

    Query params:
      field_name  — field name or full SCHEMA.TABLE.FIELD id  (required)
      table_name  — table name (required when field_name is bare, optional otherwise)
    """
    field_name = (request.args.get("field_name") or "").strip()
    table_name = (request.args.get("table_name") or "").strip()

    if not field_name:
        return jsonify({"error": "field_name is required"}), 400

    if not TOOLS_LOADED:
        return jsonify({"error": "Lineage tools not loaded yet — try again shortly"}), 503

    try:
        from lineage_excel_export import build_lineage_excel
    except ImportError as e:
        return jsonify({"error": f"Excel export module unavailable: {e}"}), 500

    # ── 1. Fetch lineage edges from Neo4j ──────────────────────────────────
    try:
        import lineage_tools as lt
        edges_json = lt.query_column_lineage(field_name=field_name, table_name=table_name)
        edges = json.loads(edges_json) if edges_json else []
    except Exception as e:
        return jsonify({"error": f"Lineage query failed: {e}"}), 500

    if not edges:
        return jsonify({"error": f"No lineage edges found for {field_name}"}), 404

    # ── 2. Resolve canonical field_id (deepest/DDM layer target) ─────────
    _layer_order = {"DDM": 0, "TT": 1, "TPR": 2}
    bare_field = field_name.split(".")[-1].upper()

    target_edges = [e for e in edges if e.get("to_field", "").upper() == bare_field]
    if not target_edges:
        target_edges = edges

    best = min(target_edges, key=lambda e: _layer_order.get(e.get("to_layer", ""), 9))
    canonical_id = f"{best['to_schema']}.{best['to_table']}.{best['to_field']}".upper()

    # ── 3. Fetch transformation logic for ONLY the edges in this lineage ─────
    # Build exact (from_vertex, to_vertex) pairs from the lineage edges so
    # we query Cosmos for precisely those documents — no extras.
    cosmos_json = "[]"
    try:
        import cosmos_tools as ct

        vertex_pairs = []
        seen_pairs: set = set()
        for e in edges:
            fv = f"{e.get('from_schema','')}.{e.get('from_table','')}.{e.get('from_field','')}".upper()
            tv = f"{e.get('to_schema','')}.{e.get('to_table','')}.{e.get('to_field','')}".upper()
            pair = (fv, tv)
            if pair not in seen_pairs and fv.strip(".") and tv.strip("."):
                seen_pairs.add(pair)
                vertex_pairs.append(pair)

        cosmos_records = ct.get_edges_for_vertex_pairs(vertex_pairs)
        cosmos_json = json.dumps(cosmos_records)
        print(f"   [export] ✅ Cosmos: {len(cosmos_records)} exact-edge records for {len(vertex_pairs)} pairs")
    except Exception as e:
        print(f"   [export] ⚠️  Cosmos query failed (continuing without it): {e}")

    # ── 4. Build Excel ────────────────────────────────────────────────────
    try:
        xlsx_bytes = build_lineage_excel(
            field_id=canonical_id,
            edges_json=edges_json,
            cosmos_json=cosmos_json,
        )
    except Exception as e:
        return jsonify({"error": f"Excel generation failed: {e}"}), 500

    from datetime import datetime as _dt
    safe_name = canonical_id.replace(".", "_")
    timestamp = _dt.now().strftime("%Y%m%d_%H%M%S")
    filename  = f"lineage_{safe_name}_{timestamp}.xlsx"

    from flask import Response as FlaskResponse
    return FlaskResponse(
        xlsx_bytes,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ────────────────────────────────────────────────────────────
# STREAMING CHAT (SSE)
# Background thread does all model work; generator yields keepalives every
# second so Flask flushes HTTP chunks regardless of how slow the model is.
# ────────────────────────────────────────────────────────────

_DONE = object()  # sentinel


def _execute_tool_call(func_name, func_args):
    """Execute a single tool call. Returns (result_str, log_entry)."""
    log_entry = {"function": func_name, "args": func_args}
    if func_name in FUNCTION_REGISTRY:
        try:
            print(f"   [stream]    → {func_name}({func_args})")
            result = FUNCTION_REGISTRY[func_name](**func_args)
            if result is None:
                result = json.dumps({"result": "No data returned"})
            log_entry["status"] = "success"
            log_entry["result_length"] = len(result)
            print(f"   [stream]    ✅ {func_name} returned {len(result)} chars")
        except Exception as e:
            result = json.dumps({"error": str(e), "function": func_name})
            log_entry["status"] = "error"
            log_entry["error"] = str(e)
            print(f"   [stream]    ❌ {func_name} error: {e}")
    else:
        result = json.dumps({"error": f"Unknown function: {func_name}"})
        log_entry["status"] = "error"
        log_entry["error"] = f"Unknown function: {func_name}"
    return result, log_entry


def _agent_worker(messages, q):
    """
    Background thread: runs the full model+tool loop and puts SSE event
    dicts onto q. Puts _DONE sentinel when finished.
    """
    try:
        all_tool_calls_log = []
        max_rounds = 10

        for round_num in range(max_rounds):
            kwargs = {
                "model": MODEL,
                "messages": messages,
                "max_completion_tokens": 8000,
                "stream": True,
                "temperature": 0,
            }
            if TOOLS:
                kwargs["tools"] = TOOLS
                kwargs["tool_choice"] = "auto"

            print(f"   [stream] Round {round_num + 1}: calling model (messages: {len(messages)})...")
            stream = client.chat.completions.create(**kwargs)

            content_parts = []
            tool_calls_by_index = {}
            is_tool_call = False

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.tool_calls:
                    is_tool_call = True
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_by_index:
                            tool_calls_by_index[idx] = {"id": "", "function_name": "", "arguments": ""}
                        if tc_delta.id:
                            tool_calls_by_index[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_by_index[idx]["function_name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_by_index[idx]["arguments"] += tc_delta.function.arguments

                if delta.content:
                    content_parts.append(delta.content)
                    if not is_tool_call:
                        q.put({"type": "token", "token": delta.content})

            content = "".join(content_parts)

            if is_tool_call:
                tool_calls = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index)]
                print(f"   [stream] 🔧 {len(tool_calls)} tool call(s) requested")

                messages.append({
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["function_name"], "arguments": tc["arguments"]},
                        }
                        for tc in tool_calls
                    ],
                })

                for tc in tool_calls:
                    func_name = tc["function_name"]
                    try:
                        func_args = json.loads(tc["arguments"])
                    except json.JSONDecodeError as e:
                        func_args = {}
                        result = json.dumps({"error": f"Invalid arguments: {e}"})
                        all_tool_calls_log.append({"function": func_name, "args": {}, "status": "error", "error": str(e)})
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
                        continue

                    result, log_entry = _execute_tool_call(func_name, func_args)
                    all_tool_calls_log.append(log_entry)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

                q.put({"type": "tools", "tool_calls": all_tool_calls_log})

            else:
                messages.append({"role": "assistant", "content": content or "(No response generated)"})
                if not content:
                    q.put({"type": "token", "token": "(No response generated)"})
                print(f"   [stream] ✅ Done ({len(content)} chars)")
                q.put({"type": "done"})
                return

        q.put({"type": "token", "token": "Unable to complete analysis within the allowed number of steps."})
        q.put({"type": "done"})

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"   [stream] ❌ Error: {error_msg}")
        q.put({"type": "error", "error": error_msg})
    finally:
        q.put(_DONE)


@app.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' field"}), 400

    user_message = data["message"].strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    session_id = data.get("session_id")
    session_id, messages = get_or_create_session(session_id)

    messages.append({"role": "user", "content": user_message})

    def generate():
        print(f"\n📨 [stream] User: {user_message[:80]}...")

        try:
            # Send session id immediately
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id})}\n\n"

            q = queue.Queue()

            # Start agent work in background thread
            t = threading.Thread(target=_agent_worker, args=(messages, q), daemon=True)
            t.start()

            # Yield events as they arrive; send SSE comment keepalives while waiting
            # so Flask flushes HTTP chunks and the browser sees activity immediately
            while True:
                try:
                    item = q.get(timeout=1.0)
                except queue.Empty:
                    # Keepalive comment — forces Werkzeug to flush this HTTP chunk
                    yield ": keepalive\n\n"
                    continue

                if item is _DONE:
                    break

                yield f"data: {json.dumps(item)}\n\n"

                if item.get("type") in ("done", "error"):
                    break

        except Exception as exc:
            # Send a proper error event before closing — prevents ERR_HTTP2_PROTOCOL_ERROR
            # caused by the stream closing abruptly without a terminal event
            print(f"  ❌ [stream] generator exception: {exc}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        finally:
            # Clean up on error: remove dangling user message
            if messages and messages[-1].get("role") == "user":
                messages.pop()

    return Response(generate(), mimetype="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Content-Type": "text/event-stream; charset=utf-8",
                    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL,
        "tools_loaded": TOOLS_LOADED,
        "active_sessions": len(sessions),
    })


# ────────────────────────────────────────────────────────────
# HEARTBEAT / AUTO-SHUTDOWN
# ────────────────────────────────────────────────────────────

HEARTBEAT_TIMEOUT = 30  # seconds without heartbeat before shutdown
_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()


@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Browser pings this every few seconds to keep server alive."""
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    return "", 204


@app.route("/api/shutdown", methods=["POST"])
def shutdown():
    """Immediate shutdown triggered by browser beforeunload."""
    print("\n👋 Browser disconnected — shutting down.")
    _shutdown_server()
    return "", 204


def _shutdown_server():
    """Terminate the Flask process."""
    os._exit(0)


def _heartbeat_watchdog():
    """Background thread that shuts down server if no heartbeat received."""
    while True:
        time.sleep(5)
        with _heartbeat_lock:
            elapsed = time.time() - _last_heartbeat
        if elapsed > HEARTBEAT_TIMEOUT:
            print(f"\n👋 No heartbeat for {int(elapsed)}s — shutting down.")
            _shutdown_server()


# ────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧬 LINEAGE AGENT — Web Interface")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"Endpoint: {PROJECT_ENDPOINT}")
    print("─" * 60)

    # ── Preflight: Neo4j connectivity ──────────────────────
    print("\n🔍 Running preflight checks...")
    neo4j_ok = False
    try:
        from neo4j import GraphDatabase
        uri = os.environ.get("NEO4J_URI", "")
        username = os.environ.get("NEO4J_USERNAME", "")
        password = os.environ.get("NEO4J_PASSWORD", "")
        if not all([uri, username, password]):
            print("   ❌ Neo4j — missing env vars (NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD)")
        else:
            uri_for_driver = uri.replace("neo4j+s://", "neo4j+ssc://", 1)
            driver = GraphDatabase.driver(uri_for_driver, auth=(username, password))
            driver.verify_connectivity()
            driver.close()
            print("   ✅ Neo4j — connected")
            neo4j_ok = True
    except Exception as e:
        print(f"   ❌ Neo4j — {e}")

    # ── Preflight: Cosmos DB connectivity ──────────────────
    cosmos_ok = False
    try:
        from azure.cosmos import CosmosClient as _CosmosClient
        cosmos_endpoint = os.environ.get("COSMOS_ENDPOINT", "")
        cosmos_key = os.environ.get("COSMOS_KEY", "")
        if not all([cosmos_endpoint, cosmos_key]):
            print("   ❌ Cosmos DB — missing env vars (COSMOS_ENDPOINT / COSMOS_KEY)")
        else:
            _cc = _CosmosClient(cosmos_endpoint, credential=cosmos_key)
            _db = _cc.get_database_client("lineage")
            _container = _db.get_container_client("transformation_details")
            # Lightweight read: fetch a single document to confirm connectivity
            next(iter(_container.query_items(
                query="SELECT TOP 1 c.id FROM c",
                enable_cross_partition_query=True,
            )), None)
            print("   ✅ Cosmos DB — connected")
            cosmos_ok = True
    except Exception as e:
        print(f"   ❌ Cosmos DB — {e}")

    # ── Preflight: model endpoint ───────────────────────────
    model_ok = False
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_completion_tokens=5,
        )
        if resp.choices and resp.choices[0].message.content:
            print(f"   ✅ Model ({MODEL}) — reachable")
            model_ok = True
        else:
            print(f"   ❌ Model ({MODEL}) — empty response")
    except Exception as e:
        print(f"   ❌ Model ({MODEL}) — {e}")

    if not (neo4j_ok and cosmos_ok and model_ok):
        print("\n⛔ Preflight checks failed — fix the above issues and try again.")
        sys.exit(1)

    # ── Load tools ─────────────────────────────────────────
    if init_tools():
        print("   ✅ Tools loaded")
    else:
        print("\n⛔ Failed to load lineage tools — cannot start.")
        sys.exit(1)

    # ── Start heartbeat watchdog ────────────────────────────
    watchdog = threading.Thread(target=_heartbeat_watchdog, daemon=True)
    watchdog.start()

    print("\n🌐 Starting web server...")
    print("   Open http://localhost:5000 in your browser")
    print("   Server will auto-shutdown when browser is closed.")
    print("=" * 60 + "\n")

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
