# Lineage Source Control & Versioning — Design Enhancement

## Problem Statement

The current system stores a single "live" snapshot of Informatica lineage in Neo4j (graph) and Cosmos DB (transformation details). There is no mechanism to:
- Stage and review proposed changes before they go live
- Compare a new extraction against what is currently approved
- Roll back to a prior approved state
- Archive historical versions after a retention period
- Guarantee users always query the latest approved data

---

## Proposed Architecture

### Core Concepts

| Concept | Description |
|---|---|
| **DRAFT** | A new XML extraction loaded for review — not yet visible to users |
| **ACTIVE** | The single approved version — all agent queries route here |
| **ARCHIVED** | A superseded ACTIVE version retained for audit/rollback purposes |
| **Version Registry** | Central metadata store tracking every version and its lifecycle |
| **Diff Report** | Computed comparison of DRAFT vs ACTIVE: additions, removals, changes |

### Version Lifecycle

```
New XML Export
      │
      ▼
 [ DRAFT v_N ]  ← extracted, loaded with version tag
      │
      ▼
 [ Diff Engine ] ← compare DRAFT v_N against ACTIVE v_(N-1)
      │
      ▼
 [ Review / Approval ]
      │
      ├── Rejected ──→ Delete DRAFT
      │
      └── Approved ──→ DRAFT v_N becomes ACTIVE
                        Old ACTIVE v_(N-1) becomes ARCHIVED
                              │
                              ▼
                   [ Archival Policy ]
                   ARCHIVED > 90 days → moved to cold tier
```

---

## Data Model Changes

### 1. Version Registry — New Cosmos DB Container

**Database:** `lineage`  
**Container:** `version_registry`  
**Partition key:** `/status`

```json
{
  "id": "v_20260629_001",
  "version_tag": "2026-06-29",
  "version_seq": 12,
  "status": "ACTIVE",
  "description": "SHAW TPR→DDM mappings — June cycle",
  "xml_source": "wf_TPR_to_DDM_SHAW_sample.XML",
  "created_by": "user@domain.com",
  "created_at": "2026-06-29T09:00:00Z",
  "approved_by": "approver@domain.com",
  "approved_at": "2026-06-29T11:30:00Z",
  "effective_from": "2026-06-29T11:30:00Z",
  "effective_to": null,
  "stats": {
    "neo4j_nodes": 1842,
    "neo4j_edges": 4201,
    "cosmos_documents": 4201
  }
}
```

Only one document may have `"status": "ACTIVE"` at any time — enforced by the version manager.

---

### 2. Neo4j — Add Version Properties

**Current node/relationship schema:**
```
(:Field {id, db_schema, table_name, field_name, layer, data_type, precision})
-[:TRANSFORMS_TO {mapping_name, folder_name, expression, ...}]->
(:Field)
```

**Enhanced schema — add to every node and relationship:**

| New Property | Type | Values |
|---|---|---|
| `version_id` | string | e.g. `"v_20260629_001"` |
| `status` | string | `DRAFT` / `ACTIVE` / `ARCHIVED` |
| `effective_from` | ISO datetime | When this version was approved |
| `effective_to` | ISO datetime | When superseded (null if still ACTIVE) |

**Load query (MERGE on id + version_id so each version is distinct):**
```cypher
MERGE (f:Field {id: row.id, version_id: $version_id})
SET f.db_schema      = row.db_schema,
    f.table_name     = row.table_name,
    f.field_name     = row.field_name,
    f.layer          = row.layer,
    f.data_type      = row.data_type,
    f.precision      = row.precision,
    f.status         = $status,
    f.effective_from = $effective_from,
    f.effective_to   = null
```

**All agent Cypher queries gain a standard filter:**
```cypher
WHERE f.version_id = $active_version AND f.status = 'ACTIVE'
```
The `$active_version` value is resolved once per session from the version registry and cached with a 60-second TTL.

---

### 3. Cosmos DB — Add Version Fields to Transformation Details

**Existing container:** `transformation_details`  
**Add to every document:**

| New Field | Description |
|---|---|
| `version_id` | Links document to a version registry entry |
| `status` | `DRAFT` / `ACTIVE` / `ARCHIVED` |
| `effective_from` | Timestamp when approved |
| `effective_to` | Timestamp when superseded (null if ACTIVE) |

**Document `id` change:** `{edge_id}__{version_id}` to allow multiple version copies.  
**Partition key remains:** `/mapping_name` for query efficiency.

**All agent Cosmos SQL queries gain a standard filter:**
```sql
WHERE c.status = 'ACTIVE' AND c.version_id = @active_version
```

---

### 4. Diff Report — New Cosmos DB Container

**Container:** `version_diffs`  
**Partition key:** `/draft_version_id`

```json
{
  "id": "diff__v_20260629_001__v_20260614_001",
  "draft_version_id": "v_20260629_001",
  "base_version_id":  "v_20260614_001",
  "generated_at": "2026-06-29T09:05:00Z",
  "summary": {
    "edges_added":    42,
    "edges_removed":  5,
    "edges_changed":  18,
    "nodes_added":    12,
    "nodes_removed":  0
  },
  "edges_added":   [ { "edge_id": "...", "from_vertex": "...", "to_vertex": "...", "mapping_name": "..." } ],
  "edges_removed": [ { "edge_id": "...", "from_vertex": "...", "to_vertex": "..." } ],
  "edges_changed": [
    {
      "edge_id": "...",
      "field": "final_expression",
      "old_value": "IIF(ISNULL(src.RATE), 0, src.RATE)",
      "new_value": "IIF(ISNULL(src.RATE), -1, src.RATE)"
    }
  ]
}
```

---

## New Components

### `STTM Lineage/version_manager.py`

Core versioning engine responsible for:

| Function | Description |
|---|---|
| `create_draft_version(description, xml_source, created_by)` | Registers a new DRAFT entry in the version registry; returns `version_id` |
| `compute_diff(draft_version_id)` | Compares DRAFT Neo4j/Cosmos data against current ACTIVE; writes diff report |
| `get_diff_report(draft_version_id)` | Reads the diff report for a given draft |
| `approve_version(draft_version_id, approved_by)` | Promotes DRAFT→ACTIVE; old ACTIVE→ARCHIVED; updates `effective_to` timestamps |
| `reject_version(draft_version_id)` | Deletes DRAFT nodes/documents from Neo4j and Cosmos |
| `list_versions(status=None)` | Returns all versions optionally filtered by status |
| `get_active_version_id()` | Returns the current ACTIVE `version_id` (used by all query tools) |
| `archive_old_versions(retention_days=90)` | Marks ARCHIVED versions older than retention threshold for cold storage |

---

### `lineage-agent/core_files/active_version.py`

Lightweight singleton that resolves the active version with caching:

```
ActiveVersionResolver
  ├── _active_version_id  (str | None)
  ├── _cache_expires_at   (datetime)
  ├── TTL = 60 seconds
  └── get() → str
       ├── If cache valid: return cached value
       └── Else: query version_registry WHERE status='ACTIVE', refresh cache
```

All lineage tools (`lineage_tools.py`, `cosmos_tools.py`) import this singleton and inject `active_version = resolver.get()` into every query at call time — no restart required when a new version is approved.

---

### `lineage-agent/core_files/version_tools.py`

New agent-facing tools registered in `run_agent.py`:

| Tool | Agent Trigger | Description |
|---|---|---|
| `list_lineage_versions(status)` | "show me version history" | Lists all versions with status, date, description |
| `get_draft_diff_report(draft_version_id)` | "what changed in draft X" | Returns the full diff: added/removed/changed edges |
| `compare_field_across_versions(field_id, v1, v2)` | "how did field X change from v1 to v2" | Side-by-side transformation chain comparison |
| `get_active_version_info()` | "what version is active" | Returns the current ACTIVE version metadata |
| `rollback_to_version(version_id)` | "roll back to version X" | Re-promotes an ARCHIVED version to ACTIVE (requires auth) |

---

### `STTM Lineage/load_to_neo4j_versioned.py`

Extended loader replacing `load_to_neo4j.py`:
- Accepts `--version-id` and `--status DRAFT` CLI args
- MERGEs on `{id, version_id}` instead of `{id}` alone
- Calls `version_manager.create_draft_version()` if no version_id provided
- Prints diff summary on completion

---

### `STTM Lineage/load_to_cosmos_versioned.py`

Extended loader replacing `load_to_cosmos.py`:
- Accepts `--version-id` CLI arg
- Document id becomes `{edge_id}__{version_id}`
- Injects `version_id`, `status`, `effective_from`, `effective_to` on every upsert

---

### `STTM Lineage/archive_versions.py`

Scheduled archival script (run daily via Azure Function or Windows Task Scheduler):
- Calls `version_manager.archive_old_versions(retention_days=90)`
- For eligible ARCHIVED versions:
  1. Exports Neo4j subgraph to JSON blob (Azure Blob Storage cold tier)
  2. Exports Cosmos documents to JSON blob
  3. Deletes nodes/relationships from Neo4j (`version_id = $v AND status = 'ARCHIVED'`)
  4. Deletes Cosmos documents (`version_id = $v AND status = 'ARCHIVED'`)
  5. Updates version registry record: `status = 'COLD_ARCHIVED'`, `blob_path = ...`

---

## Changes to Existing Files

### `lineage_tools.py` — Add version filter to all Cypher

Every Cypher query gains two changes:
1. Import and call `active_version_resolver.get()` at the top of each function
2. Add `AND f.version_id = $version_id AND f.status = 'ACTIVE'` (and same for relationship `r`) to all `MATCH`/`WHERE` clauses
3. Pass `version_id` in the parameters dict

```python
# Before (current)
cypher = """
    MATCH (target:Field {table_name: $table_name})
    ...
"""
return neo4j_client.run_cypher(cypher, {"table_name": table_name})

# After (versioned)
from active_version import resolver
active_v = resolver.get()
cypher = """
    MATCH (target:Field {table_name: $table_name, version_id: $version_id, status: 'ACTIVE'})
    ...
"""
return neo4j_client.run_cypher(cypher, {"table_name": table_name, "version_id": active_v})
```

---

### `cosmos_tools.py` — Add version filter to all SQL

```python
# Before (current)
sql = "SELECT * FROM c WHERE c.edge_id = @edge_id"

# After (versioned)
from active_version import resolver
active_v = resolver.get()
sql = "SELECT * FROM c WHERE c.edge_id = @edge_id AND c.version_id = @version_id AND c.status = 'ACTIVE'"
parameters = [..., {"name": "@version_id", "value": active_v}]
```

---

### `run_agent.py` — Register version tools

```python
from version_tools import (
    list_lineage_versions,
    get_draft_diff_report,
    compare_field_across_versions,
    get_active_version_info,
    rollback_to_version,
)
FUNCTION_REGISTRY.update({
    "list_lineage_versions":         list_lineage_versions,
    "get_draft_diff_report":         get_draft_diff_report,
    "compare_field_across_versions": compare_field_across_versions,
    "get_active_version_info":       get_active_version_info,
    "rollback_to_version":           rollback_to_version,
})
```

---

### `system_instructions.txt` — Add version context

Add a section describing when to use version tools, e.g.:
> "If the user asks what version is active, what changed recently, or to compare versions, use the `get_active_version_info`, `list_lineage_versions`, or `get_draft_diff_report` tools."

---

## End-to-End Workflow: Loading a New Extraction

```
Step 1 — Extract
  python extract_lineage.py --xml "Input XML/wf_new.XML" --output "Output Files/new_lineage.json"
  python extract_transformation_details.py --xml "Input XML/wf_new.XML" --output "Output Files/new_details.json"

Step 2 — Load as DRAFT
  python load_to_neo4j_versioned.py     --lineage "Output Files/new_lineage.json"    --status DRAFT  --description "June cycle"
  python load_to_cosmos_versioned.py    --json    "Output Files/new_details.json"    --version-id <returned-id>

Step 3 — Generate Diff
  python version_manager.py diff --draft-version-id <id>
  # Outputs: Summary to console + full diff stored in version_diffs container

Step 4 — Review (agent query)
  User: "What changed in the latest draft?"
  Agent → get_draft_diff_report() → returns additions, removals, changed expressions

Step 5 — Approve
  python version_manager.py approve --draft-version-id <id> --approved-by "user@domain.com"
  # Old ACTIVE → ARCHIVED | DRAFT → ACTIVE | All queries now route to new version instantly

Step 6 — Archive (scheduled, daily)
  python archive_versions.py --retention-days 90
```

---

## Cosmos DB Container Summary

| Container | Partition Key | Purpose |
|---|---|---|
| `transformation_details` | `/mapping_name` | Existing — add version fields |
| `version_registry` | `/status` | New — version lifecycle metadata |
| `version_diffs` | `/draft_version_id` | New — computed diff reports |

---

## Neo4j Index Recommendations

Add composite indexes to support the new version filters without degrading query performance:

```cypher
CREATE INDEX field_version_status IF NOT EXISTS
FOR (f:Field) ON (f.version_id, f.status);

CREATE INDEX rel_version_status IF NOT EXISTS
FOR ()-[r:TRANSFORMS_TO]-() ON (r.version_id, r.status);
```

---

## Implementation Sequence

| Phase | Scope | Effort |
|---|---|---|
| **1 — Registry & Manager** | `version_manager.py`, `version_registry` container, `active_version.py` | ~1 day |
| **2 — Versioned Loaders** | `load_to_neo4j_versioned.py`, `load_to_cosmos_versioned.py` | ~1 day |
| **3 — Query Layer Update** | Update all Cypher in `lineage_tools.py`, all SQL in `cosmos_tools.py` | ~1 day |
| **4 — Diff Engine** | `compute_diff()` in version_manager, `version_diffs` container | ~1 day |
| **5 — Agent Tools** | `version_tools.py`, update `run_agent.py`, `system_instructions.txt` | ~0.5 day |
| **6 — Archival Job** | `archive_versions.py`, Blob Storage integration | ~0.5 day |
| **Total** | | **~5 days** |

---

## Key Design Guarantees

| Requirement | How It Is Met |
|---|---|
| Users always get latest correct output | All queries inject `active_version` from a 60s-TTL cache; promotion is atomic |
| New changes compared before going live | Diff engine runs automatically on DRAFT load; agent exposes the report |
| Approved snapshot saved with version | `version_registry` records approval metadata; data tagged with `version_id` |
| Historical versions archived | Scheduled `archive_versions.py` moves ARCHIVED data >90 days to Blob cold tier, removes from live DBs |
| Rollback capability | Any ARCHIVED version in Neo4j/Cosmos can be re-promoted to ACTIVE in one command |
| No disruption to existing agent | Version filter is injected transparently; no tool interface changes for lineage tools |

---

---

# Change Submission, Approval & Rejection — User Journeys

## How a User Provides a Change

There are **four input channels**. The channel determines which Python entry point is called first.

| Channel | Best For | Entry Point |
|---|---|---|
| **A — XML Re-extraction** | Bulk structural changes (new mappings, tables added/removed) | `extract_lineage.py` → `load_to_neo4j_versioned.py` |
| **B — JSON Patch File** | Targeted changes to one or more specific fields/edges/SQL authored offline | `change_manager.py apply-patch` |
| **C — CLI Patch Command** | Single-record quick fixes typed directly in a terminal | `change_manager.py patch` |
| **D — Agent Chat** | User describes the change in natural language to the AI agent | `change_tools.py` → `change_manager.py` |

---

## Change Types and What They Touch

| Change Type | What Changes | Neo4j Affected | Cosmos Affected |
|---|---|---|---|
| Field metadata (data_type, precision, layer) | Field node property | Yes | No |
| New field / new table | New Field node(s) | Yes | No |
| New linkage (new edge) | New TRANSFORMS_TO relationship + new Cosmos document | Yes | Yes |
| Remove existing linkage | Delete TRANSFORMS_TO relationship + Cosmos document | Yes | Yes |
| Expression / SQL change | Cosmos document `final_expression` / `custom_sql` | No | Yes |
| Lookup condition change | Cosmos document `lookup_condition` | No | Yes |
| Update strategy change | Cosmos document `update_strategy_expression` | No | Yes |
| Rename mapping | TRANSFORMS_TO `mapping_name` property + Cosmos `mapping_name` | Yes | Yes |

---

## New File: `STTM Lineage/change_manager.py`

Central hub that handles all patch-based change submissions. Responsibilities:

| Function | Description |
|---|---|
| `apply_patch_file(patch_file, submitted_by, description)` | Reads a JSON patch file, validates it, creates a DRAFT version, writes changed records to Neo4j/Cosmos tagged as DRAFT |
| `apply_cli_patch(operation, entity, target_id, field, old_value, new_value, submitted_by)` | Handles a single-record change from CLI args; internally calls `apply_patch_file` with a single-item patch |
| `validate_patch(changes)` | Pre-flight checks: target edge/field exists in ACTIVE version; old_value matches current; no duplicate change requests open |
| `create_patch_draft(changes, submitted_by, description)` | Registers a PATCH-type DRAFT in `version_registry`; writes only the changed records to Neo4j/Cosmos with the new `version_id` |
| `list_open_changes()` | Lists all DRAFT change requests with submitter, date, summary |
| `get_change_request(version_id)` | Returns the full patch details and diff for one change request |

**Patch JSON format (channel B):**
```json
{
  "description": "Fix null-handling in rate expression for M_LOAN_FACT mapping",
  "submitted_by": "analyst@domain.com",
  "changes": [
    {
      "operation": "UPDATE",
      "entity": "cosmos_edge",
      "edge_id": "SHAW_TPR.MAST_LOAN_REC.RATE__to__CRDM_DDM.F_LOAN_FACT.RATE__m_M_LOAN_FACT",
      "field": "final_expression",
      "old_value": "IIF(ISNULL(src.RATE), 0, src.RATE)",
      "new_value": "IIF(ISNULL(src.RATE), -1, src.RATE)"
    },
    {
      "operation": "ADD",
      "entity": "neo4j_edge",
      "from_id": "SHAW_TPR.MAST_LOAN_REC.ACCT_NBR",
      "to_id": "CRDM_DDM.F_LOAN_FACT.ACCT_NBR",
      "mapping_name": "M_LOAN_FACT",
      "folder_name": "SHAW",
      "expression": "src.ACCT_NBR"
    },
    {
      "operation": "DELETE",
      "entity": "neo4j_edge",
      "edge_id": "SHAW_TPR.MAST_LOAN_REC.OLD_FLD__to__CRDM_DDM.F_LOAN_FACT.OLD_FLD__m_M_LOAN_FACT"
    },
    {
      "operation": "UPDATE",
      "entity": "neo4j_field",
      "field_id": "CRDM_DDM.F_LOAN_FACT.RATE",
      "field": "data_type",
      "old_value": "NUMBER",
      "new_value": "NUMBER(18,6)"
    }
  ]
}
```

Supported `entity` values: `cosmos_edge`, `neo4j_field`, `neo4j_edge`  
Supported `operation` values: `ADD`, `UPDATE`, `DELETE`

---

## New File: `lineage-agent/core_files/change_tools.py`

Agent-facing tools that allow the AI agent to submit and review changes on behalf of a user:

| Tool | Agent Trigger Phrase | Description |
|---|---|---|
| `submit_field_change(field_id, property, new_value, submitted_by, reason)` | "change the data type of X to Y" | Submits a single field property change as a DRAFT |
| `submit_expression_change(edge_id, new_expression, submitted_by, reason)` | "update the expression for X in mapping M" | Submits a Cosmos expression change as a DRAFT |
| `submit_new_linkage(from_field_id, to_field_id, mapping_name, expression, submitted_by)` | "add a new mapping from X to Y" | Creates a new neo4j_edge + cosmos_edge in a DRAFT |
| `submit_delete_linkage(edge_id, submitted_by, reason)` | "remove the mapping from X to Y" | Marks an edge for deletion in a DRAFT |
| `list_open_change_requests()` | "what changes are pending approval" | Lists all DRAFT versions with submitter and summary |
| `get_change_request_details(version_id)` | "show me what's in change request X" | Returns the full diff for a pending DRAFT |

---

## User Journey 1 — Bulk Change via XML Re-extraction

**Trigger:** Informatica developer exports a new or updated workflow XML after modifying mappings.

**Who does it:** Data Engineer / ETL Developer

```
Step 1  [extract_lineage.py]
        python extract_lineage.py
               --xml   "Input XML/wf_TPR_to_DDM_SHAW_v2.XML"
               --output "Output Files/lineage_v2.json"

Step 2  [extract_transformation_details.py]
        python extract_transformation_details.py
               --xml   "Input XML/wf_TPR_to_DDM_SHAW_v2.XML"
               --output "Output Files/details_v2.json"

Step 3  [load_to_neo4j_versioned.py]
        python load_to_neo4j_versioned.py
               --lineage    "Output Files/lineage_v2.json"
               --status     DRAFT
               --description "June cycle — new ACCT_NBR mapping"
               --created-by  "dev@domain.com"
        → Prints: "Draft version_id: v_20260629_002 created"

Step 4  [load_to_cosmos_versioned.py]
        python load_to_cosmos_versioned.py
               --json       "Output Files/details_v2.json"
               --version-id v_20260629_002

Step 5  [version_manager.py]
        python version_manager.py diff --draft-version-id v_20260629_002
        → Writes diff to version_diffs container
        → Prints summary: "+42 edges  -5 edges  ~18 expressions changed"
```

**At this point the DRAFT is ready for review. See Journey 4 (Approval) or Journey 5 (Rejection).**

---

## User Journey 2 — Targeted Change via JSON Patch File

**Trigger:** Business analyst or data steward identifies a specific expression error or a missing linkage and authors a patch file.

**Who does it:** Data Analyst / Data Steward

```
Step 1  [User — text editor or Excel]
        Author  "my_patch.json"  following the patch JSON format above.
        Save to: "STTM Lineage/patches/my_patch.json"

Step 2  [change_manager.py]
        python change_manager.py apply-patch
               --file        "patches/my_patch.json"
               --submitted-by "analyst@domain.com"
        → validate_patch()  checks:
            - Each target edge_id / field_id exists in ACTIVE version
            - old_value matches what is currently stored
            - No other open DRAFT modifies the same record
        → If valid: creates DRAFT in version_registry
                    writes only changed records to Neo4j/Cosmos tagged DRAFT
                    calls version_manager.compute_diff()
        → Prints: "Change request v_20260629_003 submitted. 3 records affected."

Step 3  [Agent or CLI — optional self-review]
        python version_manager.py show-diff --draft-version-id v_20260629_003
        → Prints human-readable diff to console
```

**At this point the DRAFT is ready for review. See Journey 4 (Approval) or Journey 5 (Rejection).**

---

## User Journey 3 — Single Record Quick Fix via CLI

**Trigger:** Engineer spots a single wrong expression or wrong data type and wants to fix it immediately without authoring a file.

**Who does it:** Data Engineer

```
Step 1  [change_manager.py — single UPDATE]
        python change_manager.py patch
               --operation   UPDATE
               --entity      cosmos_edge
               --edge-id     "SHAW_TPR.MAST_LOAN_REC.RATE__to__CRDM_DDM.F_LOAN_FACT.RATE__m_M_LOAN_FACT"
               --field       final_expression
               --old-value   "IIF(ISNULL(src.RATE), 0, src.RATE)"
               --new-value   "IIF(ISNULL(src.RATE), -1, src.RATE)"
               --submitted-by "dev@domain.com"
               --description  "Fix default rate from 0 to -1"
        → Validates old_value matches current ACTIVE record
        → Creates DRAFT v_20260629_004
        → Writes 1 changed Cosmos document tagged DRAFT
        → Computes diff (1 edge changed)
        → Prints: "Change request v_20260629_004 submitted."

        # Example — field metadata change:
        python change_manager.py patch
               --operation   UPDATE
               --entity      neo4j_field
               --field-id    "CRDM_DDM.F_LOAN_FACT.RATE"
               --field       data_type
               --old-value   "NUMBER"
               --new-value   "NUMBER(18,6)"
               --submitted-by "dev@domain.com"

        # Example — new linkage:
        python change_manager.py patch
               --operation   ADD
               --entity      neo4j_edge
               --from-id     "SHAW_TPR.MAST_LOAN_REC.ACCT_NBR"
               --to-id       "CRDM_DDM.F_LOAN_FACT.ACCT_NBR"
               --mapping-name M_LOAN_FACT
               --expression  "src.ACCT_NBR"
               --submitted-by "dev@domain.com"
```

**At this point the DRAFT is ready for review. See Journey 4 (Approval) or Journey 5 (Rejection).**

---

## User Journey 4 — Change via Agent Chat

**Trigger:** User describes a change in natural language to the lineage agent via web UI or CLI.

**Who does it:** Any user with access to the agent

```
User  →  "The expression for RATE in M_LOAN_FACT should use -1 not 0 as the null default"

Agent →  [search_fields("RATE", "F_LOAN_FACT")]               # confirms field exists
         [get_field_transformation_logic("RATE", "F_LOAN_FACT")]  # retrieves current expression
         Replies: "Current expression is IIF(ISNULL(src.RATE), 0, src.RATE).
                   Shall I submit a change to replace 0 with -1?"

User  →  "Yes, my name is analyst@domain.com"

Agent →  [submit_expression_change(
              edge_id      = "SHAW_TPR...RATE__m_M_LOAN_FACT",
              new_expression = "IIF(ISNULL(src.RATE), -1, src.RATE)",
              submitted_by = "analyst@domain.com",
              reason       = "Fix null default from 0 to -1"
          )]
         → change_tools.py calls change_manager.apply_cli_patch()
         → change_manager creates DRAFT, writes Cosmos record, computes diff

Agent →  "Change request v_20260629_005 has been submitted.
          1 expression changed. Pending approval by a reviewer."
```

**Tools sequence:**
`change_tools.py:submit_expression_change()` → `change_manager.py:apply_cli_patch()` → `version_manager.py:create_patch_draft()` → `version_manager.py:compute_diff()`

---

## User Journey 5 — Approving a Change

**Trigger:** A reviewer sees a pending DRAFT and decides to approve it.

**Who does it:** Data Owner / Senior Data Engineer / Approver

### 5A — Approval via CLI

```
Step 1  [version_manager.py — list pending]
        python version_manager.py list --status DRAFT
        → Prints table of all open DRAFTs:
          v_20260629_003 | analyst@domain.com | 2026-06-29 10:15 | 3 changes | "Fix rate expression"
          v_20260629_004 | dev@domain.com     | 2026-06-29 11:00 | 1 change  | "Fix data type"

Step 2  [version_manager.py — review diff]
        python version_manager.py show-diff --draft-version-id v_20260629_003
        → Prints:
          CHANGED  cosmos_edge  SHAW_TPR...RATE__m_M_LOAN_FACT
            final_expression: "IIF(ISNULL(src.RATE), 0, src.RATE)"
                           → "IIF(ISNULL(src.RATE), -1, src.RATE)"
          ADDED    neo4j_edge   SHAW_TPR...ACCT_NBR → CRDM_DDM...ACCT_NBR
          DELETED  neo4j_edge   SHAW_TPR...OLD_FLD  → CRDM_DDM...OLD_FLD

Step 3  [version_manager.py — approve]
        python version_manager.py approve
               --draft-version-id v_20260629_003
               --approved-by      "owner@domain.com"
        → version_manager.approve_version() executes:
            1. Sets DRAFT records in Neo4j/Cosmos → status = 'ACTIVE'
            2. Sets old ACTIVE records that were patched → status = 'ARCHIVED', effective_to = now()
            3. Updates version_registry: DRAFT → ACTIVE, sets approved_by, approved_at, effective_from
            4. Sets old ACTIVE version_registry entry → effective_to = now(), status = 'ARCHIVED'
            5. Invalidates active_version.py cache → next query picks up new version instantly
        → Prints: "v_20260629_003 approved and is now ACTIVE."
```

### 5B — Approval via Agent Chat

```
User  →  "Approve change request v_20260629_003. Approved by owner@domain.com"

Agent →  [get_change_request_details("v_20260629_003")]    # shows diff summary
         Replies: "This request changes 3 records. Confirm approval?"

User  →  "Yes"

Agent →  [approve_change_request("v_20260629_003", "owner@domain.com")]
         → version_tools.py calls version_manager.approve_version()
         Replies: "Change request v_20260629_003 approved. 
                   Lineage queries now reflect the updated expressions."
```

**Tools sequence:**  
`version_tools.py:approve_change_request()` → `version_manager.py:approve_version()` → Neo4j status updates + Cosmos status updates + `version_registry` update + `active_version.py` cache invalidation

---

## User Journey 6 — Rejecting a Change

**Trigger:** A reviewer reviews the diff and decides the proposed change is incorrect or not ready.

**Who does it:** Data Owner / Senior Data Engineer / Approver

### 6A — Rejection via CLI

```
Step 1  [version_manager.py — review diff]
        python version_manager.py show-diff --draft-version-id v_20260629_004
        → Reviewer sees the proposed change

Step 2  [version_manager.py — reject]
        python version_manager.py reject
               --draft-version-id v_20260629_004
               --rejected-by      "owner@domain.com"
               --reason           "Incorrect expression — should remain 0 per business rule BLR-142"
        → version_manager.reject_version() executes:
            1. Deletes DRAFT Field nodes from Neo4j (version_id = v_20260629_004 AND status = 'DRAFT')
            2. Deletes DRAFT TRANSFORMS_TO relationships from Neo4j
            3. Deletes DRAFT documents from Cosmos (version_id = v_20260629_004)
            4. Updates version_registry: status = 'REJECTED', rejected_by, rejected_at, rejection_reason
            5. ACTIVE data is completely untouched
        → Prints: "v_20260629_004 rejected. All draft records removed. Active version unchanged."
```

### 6B — Rejection via Agent Chat

```
User  →  "Reject change request v_20260629_004.
          Reason: expression must stay 0 per business rule BLR-142"

Agent →  [get_change_request_details("v_20260629_004")]
         Replies: "This will reject 1 expression change and delete the draft. Confirm?"

User  →  "Yes. Rejected by owner@domain.com"

Agent →  [reject_change_request("v_20260629_004", "owner@domain.com",
                                 "expression must stay 0 per business rule BLR-142")]
         → version_tools.py calls version_manager.reject_version()
         Replies: "Change request v_20260629_004 rejected.
                   Reason recorded. Active lineage data unchanged."
```

**Tools sequence:**  
`version_tools.py:reject_change_request()` → `version_manager.py:reject_version()` → Neo4j DRAFT node/rel delete + Cosmos DRAFT document delete + `version_registry` REJECTED update

---

## User Journey Summary Table

| Journey | Who | Input Channel | Key Files (in sequence) |
|---|---|---|---|
| **1 — Bulk XML Change** | ETL Developer | New XML export | `extract_lineage.py` → `extract_transformation_details.py` → `load_to_neo4j_versioned.py` → `load_to_cosmos_versioned.py` → `version_manager.py diff` |
| **2 — JSON Patch File** | Data Analyst | Patch JSON file | `change_manager.py apply-patch` → `version_manager.py create_patch_draft` → `version_manager.py compute_diff` |
| **3 — CLI Quick Fix** | Data Engineer | Terminal command | `change_manager.py patch` → `version_manager.py create_patch_draft` → `version_manager.py compute_diff` |
| **4 — Agent Chat Change** | Any user | Web UI / CLI chat | `change_tools.py` → `change_manager.py apply_cli_patch` → `version_manager.py create_patch_draft` → `version_manager.py compute_diff` |
| **5A — Approve via CLI** | Data Owner | Terminal command | `version_manager.py list` → `version_manager.py show-diff` → `version_manager.py approve` → `active_version.py` cache reset |
| **5B — Approve via Agent** | Data Owner | Web UI / CLI chat | `version_tools.py approve_change_request` → `version_manager.py approve_version` → `active_version.py` cache reset |
| **6A — Reject via CLI** | Data Owner | Terminal command | `version_manager.py show-diff` → `version_manager.py reject` |
| **6B — Reject via Agent** | Data Owner | Web UI / CLI chat | `version_tools.py reject_change_request` → `version_manager.py reject_version` |

---

## Updated Cosmos DB Container Summary

| Container | Partition Key | Purpose |
|---|---|---|
| `transformation_details` | `/mapping_name` | Existing — add `version_id`, `status` fields |
| `version_registry` | `/status` | Version lifecycle metadata (DRAFT/ACTIVE/ARCHIVED/REJECTED/COLD_ARCHIVED) |
| `version_diffs` | `/draft_version_id` | Computed diff reports per draft |
| `change_requests` | `/submitted_by` | Individual patch records — links to parent `version_id` |

---

## Updated Implementation Sequence

| Phase | Scope | Key Files | Effort |
|---|---|---|---|
| **1 — Registry & Manager** | Version registry CRUD, active version cache | `version_manager.py`, `active_version.py`, `version_registry` container | ~1 day |
| **2 — Versioned Loaders** | Bulk XML load path with version tagging | `load_to_neo4j_versioned.py`, `load_to_cosmos_versioned.py` | ~1 day |
| **3 — Query Layer Update** | Inject version filter into all live queries | `lineage_tools.py`, `cosmos_tools.py` | ~1 day |
| **4 — Diff Engine** | Compute and store diffs | `version_manager.compute_diff()`, `version_diffs` container | ~1 day |
| **5 — Change Manager** | Patch-based targeted change submission | `change_manager.py`, `change_requests` container | ~1.5 days |
| **6 — Agent Tools** | Expose all journeys through the AI agent | `version_tools.py`, `change_tools.py`, `run_agent.py`, `system_instructions.txt` | ~1 day |
| **7 — Archival Job** | Scheduled cold-tier archival | `archive_versions.py`, Blob Storage | ~0.5 day |
| **8 — Migration Script** | Backfill existing data to baseline v0 | `migrate_to_versioned.py` | ~0.5 day |
| **9 — Observability** | Logging, alerts, health-check endpoint | `version_health.py`, Application Insights / Log Analytics | ~0.5 day |
| **Total** | | | **~8.5 days** |

---

---

# Gap Closure Addendum

## Gap 1 — Promotion Atomicity (Cross-Store Consistency)

### Problem
`approve_version()` touches three independent stores (Neo4j, Cosmos transformation_details, Cosmos version_registry) with no shared transaction. A partial failure leaves split-brain state.

### Resolution — Pointer-Switch Strategy

Instead of mutating status on every node/document during promotion, the system uses an **immutable-data + pointer-switch** approach:

1. **Data stays immutable once loaded.** DRAFT records retain `status = 'DRAFT'` in both Neo4j and Cosmos permanently. ACTIVE records keep `status = 'ACTIVE'` permanently. No batch status-flip is needed.

2. **A single document is the source of truth.** The `version_registry` document's `status` field is the sole arbiter of what is "active". This is a single Cosmos point-write — Cosmos guarantees single-document writes are atomic with strong consistency (Session or Strong level).

3. **Query-time resolution.** `active_version.py` reads the single ACTIVE pointer from `version_registry` and injects the `version_id` filter into queries. Queries never filter by `status` on data records — they filter by `version_id` only.

**Revised approval sequence:**
```
approve_version(draft_version_id, approved_by):
    1. Read current ACTIVE registry doc  →  old_active_id
    2. Read DRAFT registry doc           →  validate it exists and is DRAFT
    3. SINGLE Cosmos write: Update DRAFT registry doc →
         status='ACTIVE', approved_by, approved_at, effective_from=now()
       This is the COMMIT POINT — all queries now resolve to the new version.
    4. SINGLE Cosmos write: Update old ACTIVE registry doc →
         status='ARCHIVED', effective_to=now()
       If this fails, a background reconciliation job detects two non-ARCHIVED
       entries and fixes up the old one. Queries are already correct because
       step 3 set the new pointer.
    5. Invalidate active_version.py cache.
```

**Why this is safe:**
- Step 3 is a single-document write — atomic by Cosmos guarantee.
- No Neo4j writes during promotion. Data was already loaded with its `version_id`.
- Step 4 failure is self-healing via a reconciliation sweep.

**Reconciliation sweep** (runs in `version_health.py`, invoked at startup and every 10 minutes):
```python
def reconcile_registry():
    """Ensure at most one ACTIVE entry exists; demote extras to ARCHIVED."""
    active_docs = query("SELECT * FROM c WHERE c.status = 'ACTIVE'")
    if len(active_docs) > 1:
        # Keep the one with the latest effective_from; archive the rest
        active_docs.sort(key=lambda d: d['effective_from'], reverse=True)
        for stale in active_docs[1:]:
            stale['status'] = 'ARCHIVED'
            stale['effective_to'] = datetime.utcnow().isoformat()
            container.upsert_item(stale)
        logger.warning("Reconciled %d stale ACTIVE entries", len(active_docs) - 1)
```

**Impact on data model:** Remove the `status` property from Neo4j nodes/relationships and Cosmos transformation_details documents. Only `version_id` is needed on data records. The `status` field lives exclusively in `version_registry`.

### Revised Neo4j Load Query
```cypher
MERGE (f:Field {id: row.id, version_id: $version_id})
SET f.db_schema      = row.db_schema,
    f.table_name     = row.table_name,
    f.field_name     = row.field_name,
    f.layer          = row.layer,
    f.data_type      = row.data_type,
    f.precision      = row.precision
```
No `status` or `effective_from`/`effective_to` on data records.

### Revised Query Filter (all tools)
```cypher
-- Neo4j
MATCH (target:Field {table_name: $table_name, version_id: $version_id})

-- Cosmos SQL
WHERE c.version_id = @version_id
```
No `AND c.status = 'ACTIVE'` needed — the `version_id` alone identifies the dataset.

---

## Gap 2 — Rollback After Cold Archival

### Problem
The design claims "any ARCHIVED version can be re-promoted in one command" but cold archival deletes data from live stores. Rollback after that point is impossible without a restore step.

### Resolution — Tiered Rollback Model

| Version State | Rollback Method | RTO |
|---|---|---|
| **ARCHIVED** (data still in Neo4j + Cosmos) | Pointer switch only — update `version_registry` ACTIVE pointer to target version | < 1 second |
| **COLD_ARCHIVED** (data in Blob, deleted from live) | Restore-first rollback: re-load from Blob JSON into Neo4j + Cosmos, then pointer switch | ~5–15 minutes depending on data volume |

**`rollback_to_version()` revised logic:**
```python
def rollback_to_version(target_version_id, approved_by):
    registry = get_version_registry(target_version_id)

    if registry['status'] == 'ARCHIVED':
        # Fast path — data still in live stores
        _switch_active_pointer(target_version_id, approved_by)

    elif registry['status'] == 'COLD_ARCHIVED':
        # Slow path — must re-hydrate first
        blob_path = registry['blob_path']
        neo4j_json = download_blob(f"{blob_path}/neo4j_export.json")
        cosmos_json = download_blob(f"{blob_path}/cosmos_export.json")
        load_neo4j(neo4j_json, version_id=target_version_id)
        load_cosmos(cosmos_json, version_id=target_version_id)
        registry['status'] = 'ARCHIVED'  # back to warm
        upsert_registry(registry)
        _switch_active_pointer(target_version_id, approved_by)

    else:
        raise ValueError(f"Cannot rollback to version in state: {registry['status']}")
```

**Archive retention policy update:**
- Versions < 90 days old: kept in live stores (`ARCHIVED`)
- Versions 90–365 days old: cold archived to Blob (`COLD_ARCHIVED`), restorable
- Versions > 365 days: permanently deleted from Blob (`PURGED`), not restorable

**`version_registry` document — new fields:**
```json
{
  "blob_path": "lineage-archive/v_20260101_001/",
  "cold_archived_at": "2026-04-01T00:00:00Z",
  "purge_eligible_after": "2027-01-01T00:00:00Z"
}
```

---

## Gap 3 — Concurrency Control for Approval Races

### Problem
Two DRAFTs can be approved nearly simultaneously. The second approval may overwrite the first without conflict detection.

### Resolution — Optimistic Concurrency via Base-Version Check

Each DRAFT records the `base_active_version_id` it was diffed against at submission time.

**New field on `version_registry` DRAFT documents:**
```json
{
  "base_active_version_id": "v_20260614_001"
}
```

**Approval precondition check:**
```python
def approve_version(draft_version_id, approved_by):
    draft = get_version_registry(draft_version_id)
    current_active = get_active_version_id()

    if draft['base_active_version_id'] != current_active:
        raise ConflictError(
            f"Cannot approve: this draft was based on {draft['base_active_version_id']} "
            f"but the current active version is {current_active}. "
            f"Please rebase the draft (re-run diff against the current active version) "
            f"before approving."
        )
    # proceed with pointer switch...
```

**Rebase workflow:**
```
python version_manager.py rebase --draft-version-id v_20260629_003
```
This re-computes the diff against the current ACTIVE version, updates `base_active_version_id`, and regenerates the diff report. If there are conflicting changes (same edge modified in both the DRAFT and the newly approved version), the rebase reports conflicts that must be resolved manually.

**New function in `version_manager.py`:**

| Function | Description |
|---|---|
| `rebase_draft(draft_version_id)` | Re-diffs the DRAFT against current ACTIVE; updates `base_active_version_id`; flags conflicts |

**New agent tool in `version_tools.py`:**

| Tool | Agent Trigger | Description |
|---|---|---|
| `rebase_change_request(version_id)` | "rebase draft X" | Re-diffs against current active; reports conflicts |

**Cosmos ETag guard on pointer switch:**
The approval step uses Cosmos `if_match` (ETag) on the registry document write to guarantee no concurrent modifier has changed the ACTIVE pointer between read and write:
```python
container.upsert_item(
    body=updated_draft_doc,
    headers={"If-Match": draft_doc["_etag"]}
)
```
If a concurrent approval wins, this write fails with `412 Precondition Failed`, and the caller gets a clear conflict error.

---

## Gap 4 — Authorization & Role-Based Access Control (RBAC)

### Problem
No permission boundaries defined. Any agent user can approve or roll back changes.

### Resolution — Role Matrix & Enforcement

**Roles:**

| Role | Submit Change | View Drafts / Diffs | Approve / Reject | Rollback | Archive / Purge |
|---|---|---|---|---|---|
| **Viewer** | No | Yes (read-only) | No | No | No |
| **Contributor** | Yes | Yes | No | No | No |
| **Approver** | Yes | Yes | Yes | No | No |
| **Admin** | Yes | Yes | Yes | Yes | Yes |

**Enforcement layer — `auth.py`:**
```python
# lineage-agent/core_files/auth.py

import os
from functools import wraps

# Role assignments loaded from environment or config
# Format: {"user@domain.com": "approver", ...}
_ROLE_MAP = {}

ROLE_HIERARCHY = {
    "viewer":      0,
    "contributor": 1,
    "approver":    2,
    "admin":       3,
}

def load_roles():
    """Load role assignments from ROLES_JSON env var or roles.json file."""
    global _ROLE_MAP
    import json
    roles_path = os.environ.get("ROLES_FILE", "roles.json")
    if os.path.exists(roles_path):
        with open(roles_path) as f:
            _ROLE_MAP = json.load(f)

def get_user_role(user_email: str) -> str:
    return _ROLE_MAP.get(user_email, "viewer")

def require_role(minimum_role: str):
    """Decorator that enforces minimum role for a function."""
    min_level = ROLE_HIERARCHY[minimum_role]
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = kwargs.get("submitted_by") or kwargs.get("approved_by") or kwargs.get("rejected_by")
            if not user:
                raise PermissionError("User identity required for this operation.")
            user_level = ROLE_HIERARCHY.get(get_user_role(user), 0)
            if user_level < min_level:
                raise PermissionError(
                    f"User '{user}' has role '{get_user_role(user)}' but "
                    f"'{minimum_role}' or higher is required for this operation."
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

**Applied to version_manager.py functions:**
```python
from auth import require_role

@require_role("contributor")
def create_patch_draft(changes, submitted_by, description): ...

@require_role("approver")
def approve_version(draft_version_id, approved_by): ...

@require_role("approver")
def reject_version(draft_version_id, rejected_by, reason): ...

@require_role("admin")
def rollback_to_version(target_version_id, approved_by): ...

@require_role("admin")
def archive_old_versions(retention_days=90): ...
```

**Separation of duties:** A user cannot approve their own submission. Enforced in `approve_version()`:
```python
if draft['created_by'] == approved_by:
    raise PermissionError("Self-approval is not permitted. A different approver is required.")
```

**`roles.json` example:**
```json
{
  "dev@domain.com":      "contributor",
  "analyst@domain.com":  "contributor",
  "owner@domain.com":    "approver",
  "admin@domain.com":    "admin"
}
```

**Audit trail:** Every version_registry document already records `created_by`, `approved_by`, `rejected_by`. Add:
```json
{
  "audit_log": [
    {"action": "CREATED",  "by": "dev@domain.com",   "at": "2026-06-29T09:00:00Z"},
    {"action": "APPROVED", "by": "owner@domain.com",  "at": "2026-06-29T11:30:00Z"}
  ]
}
```

---

## Gap 5 — Partition Key Strategy Optimization

### Problem
`version_registry` partitioned by `/status` creates hot partitions (most reads target `ACTIVE`). `change_requests` partitioned by `/submitted_by` misaligns with the dominant query pattern (by `version_id`).

### Resolution — Revised Partition Keys

| Container | Old Partition Key | New Partition Key | Rationale |
|---|---|---|---|
| `version_registry` | `/status` | `/id` | Each version is a unique document; point-reads by `id` are O(1). Status-based queries use cross-partition with a small dataset (~dozens of versions). |
| `change_requests` | `/submitted_by` | `/version_id` | All patches in a change request share a `version_id`; reviewing a change fetches all patches in one partition read. |
| `version_diffs` | `/draft_version_id` | `/draft_version_id` | No change — already correct. Each diff is fetched by its draft version. |
| `transformation_details` | `/mapping_name` | `/mapping_name` | No change — mapping-scoped queries remain the dominant pattern. |

**Why `/id` for `version_registry`:**
- The registry will have at most a few hundred documents over the system's lifetime.
- The hot-path query (`get_active_version_id()`) runs at most once every 60 seconds (TTL cache). A lightweight cross-partition `SELECT * FROM c WHERE c.status = 'ACTIVE'` on a tiny container costs < 5 RU.
- Point-reads by `id` (used in approval, rejection, rollback) are 1 RU each.

---

## Gap 6 — Diff Document Size Limits

### Problem
A single diff document storing all added/removed/changed arrays can exceed the Cosmos DB 2 MB item limit for large releases (10k+ edges).

### Resolution — Chunked Diff Storage

**Diff documents are split into pages:**
```json
{
  "id": "diff__v_20260629_001__v_20260614_001__summary",
  "draft_version_id": "v_20260629_001",
  "base_version_id": "v_20260614_001",
  "doc_type": "summary",
  "generated_at": "2026-06-29T09:05:00Z",
  "summary": {
    "edges_added": 12042,
    "edges_removed": 503,
    "edges_changed": 1834,
    "nodes_added": 420,
    "nodes_removed": 12,
    "total_chunks": 15
  }
}
```
```json
{
  "id": "diff__v_20260629_001__v_20260614_001__chunk_001",
  "draft_version_id": "v_20260629_001",
  "doc_type": "chunk",
  "chunk_index": 1,
  "total_chunks": 15,
  "changes": [
    { "type": "ADDED", "entity": "neo4j_edge", "edge_id": "...", ... },
    { "type": "REMOVED", "entity": "neo4j_edge", "edge_id": "...", ... }
  ]
}
```

**Chunking rules:**
- Each chunk document targets a maximum of 500 change records or 1.5 MB (whichever is reached first).
- The summary document is always created first and contains aggregate counts.
- Agent tools read the summary first; only fetch chunks if the user asks for details on specific changes.

**Updated `compute_diff()` logic:**
```python
def compute_diff(draft_version_id):
    changes = _compare_versions(draft_version_id, current_active_id)
    summary = _build_summary(changes)

    # Write summary document
    _write_diff_doc({**summary, "doc_type": "summary"})

    # Write chunked detail documents
    for i, chunk in enumerate(_chunk(changes, max_items=500)):
        _write_diff_doc({
            "doc_type": "chunk",
            "chunk_index": i + 1,
            "total_chunks": summary["summary"]["total_chunks"],
            "changes": chunk,
        })
```

**Updated `get_draft_diff_report()` agent tool:**
```python
def get_draft_diff_report(draft_version_id: str, detail_level: str = "summary") -> str:
    """
    detail_level:
      "summary" — returns counts only (default, fast)
      "full"    — returns summary + all chunks concatenated
      "sample"  — returns summary + first chunk (preview)
    """
```

---

## Gap 7 — Migration Plan for Existing Non-Versioned Data

### Problem
Current live data in Neo4j and Cosmos has no `version_id` property. Queries with a version filter will return zero results until migration is done.

### Resolution — One-Time Baseline Migration Script

**New file: `STTM Lineage/migrate_to_versioned.py`**

**Step-by-step migration process:**
```
Phase A — Preparation (no downtime)
  1. Create version_registry container (if not exists)
  2. Create version_diffs container (if not exists)
  3. Insert baseline version document:
       {
         "id": "v_baseline",
         "version_tag": "baseline",
         "version_seq": 0,
         "status": "ACTIVE",
         "description": "Initial baseline — migrated from non-versioned data",
         "created_by": "migration-script",
         "created_at": "<now>",
         "effective_from": "<now>",
         "effective_to": null
       }

Phase B — Tag existing Neo4j data (~2–5 min depending on graph size)
  4. Run batch Cypher to add version_id to all existing Field nodes:
       MATCH (f:Field) WHERE f.version_id IS NULL
       SET f.version_id = 'v_baseline'

  5. Run batch Cypher to add version_id to all existing relationships:
       MATCH ()-[r:TRANSFORMS_TO]->() WHERE r.version_id IS NULL
       SET r.version_id = 'v_baseline'

  6. Create composite indexes (idempotent):
       CREATE INDEX field_version IF NOT EXISTS FOR (f:Field) ON (f.version_id);

Phase C — Tag existing Cosmos data (~5–10 min depending on document count)
  7. Query all documents in transformation_details container.
  8. For each document, add:
       version_id = "v_baseline"
     and update id to "{edge_id}__v_baseline"
  9. Upsert updated documents.

Phase D — Validation
  10. Count Neo4j nodes with version_id = 'v_baseline'    → must match total node count
  11. Count Cosmos docs  with version_id = 'v_baseline'    → must match total doc count
  12. Run one sample lineage query WITH version filter     → must return same results as before

Phase E — Switch query layer
  13. Deploy updated lineage_tools.py and cosmos_tools.py with version filters.
  14. active_version.py resolves 'v_baseline' as the ACTIVE version.
  15. Verify agent queries return identical results to pre-migration.
```

**Rollback plan for migration:**
- If Phase B or C fails partway: run cleanup to remove `version_id` from partially-tagged records, delete the baseline registry entry, and redeploy the old query layer.
- Migration script is idempotent — safe to re-run.

**CLI usage:**
```
python migrate_to_versioned.py --dry-run          # validate counts, no writes
python migrate_to_versioned.py --execute           # run full migration
python migrate_to_versioned.py --validate-only     # post-migration verification
```

---

## Gap 8 — Operational Observability

### Problem
No metrics, alerts, or health checks defined for detecting and recovering from failures in approval, archival, or cache staleness.

### Resolution — `version_health.py` + Structured Logging

**New file: `lineage-agent/core_files/version_health.py`**

### Health Check Endpoint

Added to `web_app.py` as `/api/health/versioning`:
```python
@app.route("/api/health/versioning")
def versioning_health():
    checks = version_health.run_all_checks()
    status_code = 200 if all(c["ok"] for c in checks) else 503
    return jsonify(checks), status_code
```

### Health Checks

| Check | What It Verifies | Failure Response |
|---|---|---|
| `check_single_active` | Exactly one ACTIVE entry in version_registry | Auto-reconcile (demote extras) |
| `check_cache_freshness` | `active_version.py` cache age < 2x TTL | Force cache refresh |
| `check_no_stale_drafts` | No DRAFT versions older than 30 days without activity | Log warning, notify |
| `check_neo4j_cosmos_sync` | ACTIVE version node count in Neo4j matches Cosmos doc count | Log error, alert |
| `check_archival_backlog` | No ARCHIVED versions past retention without cold-archive | Log warning |

### Structured Logging

All version operations emit structured log entries:
```python
import logging
logger = logging.getLogger("lineage.versioning")

# Example log entries:
logger.info("version.draft.created",   extra={"version_id": vid, "by": user, "changes": count})
logger.info("version.approved",        extra={"version_id": vid, "by": user, "base": base_vid})
logger.info("version.rejected",        extra={"version_id": vid, "by": user, "reason": reason})
logger.warning("version.reconciled",   extra={"stale_count": n})
logger.error("version.approval.failed", extra={"version_id": vid, "error": str(e)})
```

### Alert Triggers (Application Insights / Azure Monitor)

| Metric | Threshold | Severity |
|---|---|---|
| Multiple ACTIVE versions detected | > 1 | Critical — auto-remediated by reconciliation |
| Approval failure (412 conflict or exception) | Any occurrence | Warning — notify approver to retry |
| DRAFT age without review | > 14 days | Info — reminder notification |
| Cache miss rate on active_version | > 50% in 5 min window | Warning — possible registry connectivity issue |
| Archival job failure | Any non-zero exit | Warning — manual intervention required |

---

## Revised Key Design Guarantees (Updated)

| Requirement | How It Is Met |
|---|---|
| Users always get latest correct output | Pointer-switch is a single atomic Cosmos write; 60s TTL cache; reconciliation sweep as safety net |
| Promotion is atomic | Single-document write in Cosmos (strong consistency); no batch status-flip across stores |
| Concurrent approvals cannot conflict | Optimistic concurrency via `base_active_version_id` check + Cosmos ETag on pointer write |
| Authorization enforced | Role-based `@require_role` decorator; self-approval blocked; roles in `roles.json` |
| Rollback works after cold archive | Tiered model: instant pointer switch for warm, restore-then-switch for cold, with clear RTO |
| Large diffs supported | Chunked diff storage with 500-record/1.5 MB page limit; summary-first retrieval |
| Existing data migrated safely | One-time `migrate_to_versioned.py` with dry-run, execute, and validate modes |
| Failures are detected and recovered | Health-check endpoint, structured logging, auto-reconciliation, alert triggers |

---

## Final Updated Implementation Sequence

| Phase | Scope | Key Files | Effort |
|---|---|---|---|
| **1 — Registry & Manager** | Version registry CRUD, pointer-switch approval, active version cache | `version_manager.py`, `active_version.py`, `version_registry` container | ~1 day |
| **2 — Auth & RBAC** | Role enforcement, self-approval block, roles.json | `auth.py`, `roles.json` | ~0.5 day |
| **3 — Migration** | Baseline version, tag existing data, validate | `migrate_to_versioned.py` | ~0.5 day |
| **4 — Versioned Loaders** | Bulk XML load path with version tagging | `load_to_neo4j_versioned.py`, `load_to_cosmos_versioned.py` | ~1 day |
| **5 — Query Layer Update** | Inject version_id filter into all live queries | `lineage_tools.py`, `cosmos_tools.py` | ~1 day |
| **6 — Diff Engine** | Chunked diff computation and storage | `version_manager.compute_diff()`, `version_diffs` container | ~1 day |
| **7 — Change Manager** | Patch-based targeted change submission with validation | `change_manager.py`, `change_requests` container | ~1.5 days |
| **8 — Concurrency & Rebase** | Optimistic locking, ETag guard, rebase workflow | `version_manager.rebase_draft()`, approval preconditions | ~0.5 day |
| **9 — Agent Tools** | Expose all journeys through the AI agent | `version_tools.py`, `change_tools.py`, `run_agent.py`, `system_instructions.txt` | ~1 day |
| **10 — Observability** | Health checks, structured logging, alerts, reconciliation | `version_health.py`, `web_app.py` health endpoint | ~0.5 day |
| **11 — Archival Job** | Tiered archival with cold-restore rollback support | `archive_versions.py`, Blob Storage | ~0.5 day |
| **Total** | | | **~9 days** |
