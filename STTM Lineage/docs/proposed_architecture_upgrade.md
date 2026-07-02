# Proposed Architecture Upgrade: Materialized View + Change Log

**Date**: 2026-07-02  
**Status**: Proposed  
**Scope**: Cosmos DB patch versioning for `transformation_details`

---

## Problem Statement

The current delta-chain approach stores patch records alongside full documents in the same `transformation_details` container. This creates three issues:

1. **Runtime merge cost** — every read query must fetch base docs then overlay patch deltas
2. **Version filter fragility** — Neo4j and Cosmos versions evolve independently; a shared version pointer caused all queries to return empty results
3. **Accumulating complexity** — chaining patches (patch on patch on patch) increases merge logic and error surface

### Current Data Distribution

| Version | Full Docs | Delta Docs | Role |
|---|---|---|---|
| `v_20260629_000` | 9,894 | 0 | Base full-load |
| `v_20260630_003` (ACTIVE) | 0 | 2 | Patch (UPDATE deltas only) |

---

## Architecture Patterns Considered

### 1. Delta Chain (current)

```
┌─────────────────────────┐     ┌──────────────────────┐
│  Base Full-Load          │     │  Patch Deltas         │
│  v_20260629_000          │     │  v_20260630_003       │
│  9,894 docs              │     │  2 edge_change docs   │
│  (transformation_details)│     │  (same container)     │
└───────────┬─────────────┘     └──────────┬───────────┘
            │                               │
            └───────────┬──────────────────┘
                        ▼
                Runtime merge in
                _run_cosmos_query()
```

| Pros | Cons |
|---|---|
| Small write cost per patch | Runtime merge on every read |
| Full audit trail | Deltas accumulate — merge cost grows |
| Simple submission flow | Same container mixes full docs + deltas |
| | Multi-patch chains are fragile |

### 2. Copy-on-Write / Snapshot

Every version gets all 9,894 docs re-written with the new `version_id`.  
Patch approval = copy 9,894 docs, apply 2 changes, write 9,894 new docs.

| Pros | Cons |
|---|---|
| Simplest reads — no merge needed | ~10K writes per patch (expensive RU cost) |
| Each version is self-contained | Storage grows linearly with versions |
| | Approval is slow (minutes to copy) |

**Verdict**: Overkill for 2–5 property changes per patch.

### 3. Materialized View + Change Log (recommended)

```
┌──────────────────────────────────────────────────────────────┐
│                        Cosmos DB                             │
│                                                              │
│  ┌──────────────────────────┐  ┌──────────────────────────┐  │
│  │  transformation_details  │  │  change_log              │  │
│  │  (CURRENT materialized   │  │  (audit trail)           │  │
│  │   view)                  │  │  Every delta ever         │  │
│  │  9,894 docs              │  │  submitted, with          │  │
│  │  No version_id filter    │  │  before/after values      │  │
│  │  needed for reads        │  │                           │  │
│  └──────────┬───────────────┘  └──────────┬───────────────┘  │
│             │                              │                  │
│  ┌──────────┴───────────────┐              │                  │
│  │  version_registry        │──────────────┘                  │
│  │  (lifecycle metadata)    │                                 │
│  └──────────────────────────┘                                 │
└──────────────────────────────────────────────────────────────┘
```

---

## Recommended Pattern: Materialized View + Change Log

### How It Works

| Step | What happens |
|---|---|
| **Read** | Query `transformation_details` directly — no version filter, no merge. Instant. |
| **Patch submission** | Write delta record to `change_log` container (audit only) |
| **Patch approval** | 1. Read the 2–5 affected docs from `transformation_details` |
| | 2. Apply property changes in-place (point writes, not re-creation) |
| | 3. Write before/after snapshot to `change_log` for audit trail |
| | 4. Flip `version_registry` status |
| **Rollback** | Read the `change_log` for that version, reverse the changes |
| **Full reload** | Replace `transformation_details` entirely (same as today) |

### Why This Wins

| Factor | Numbers | Impact |
|---|---|---|
| Full docs | 9,894 | Too many to copy per patch, too many to merge at runtime |
| Patch size | 2–5 changes | Trivially cheap to apply as point-updates |
| Read:Write ratio | ~100:1 (queries dominate) | Optimize for reads → materialize |
| Audit requirement | Yes | Keep `change_log` separate for compliance |
| Rollback need | Rare | `change_log` has `old_value` — reverse is trivial |

**Key insight**: instead of choosing *when* to merge (read-time vs write-time), move the merge to **approval-time** — it happens once, and all subsequent reads are zero-cost.

---

## Codebase Impact

| Component | Current | Proposed |
|---|---|---|
| `transformation_details` | Mixed full docs + deltas, version-filtered | Pure current-state docs, no version filter |
| `change_log` (new container) | N/A | All deltas with before/after for audit |
| `cosmos_tools._run_cosmos_query` | Two-step: filter base + merge patches | Simple query, no merge logic needed |
| `change_manager.apply_patch_file` | Writes delta to `transformation_details` | Writes delta to `change_log` only |
| `version_manager.approve_version` | Just flips registry status | Flips status **+ applies point-updates** to `transformation_details` |
| `version_manager.reject_version` | No-op on data | No-op on data (`change_log` stays for audit) |
| Rollback (new capability) | Not supported | Read `change_log` → reverse point-updates |
| `active_version.py` | `get_cosmos_data_version()` + `get_neo4j_version()` + patch cache | Can be simplified — no version filter needed for reads |

---

## Change Log Container Design

```
Database  : lineage
Container : change_log
Partition : /version_id

Document structure:
{
    "id":           "<edge_id>__<property>__<version_id>",
    "version_id":   "v_20260630_003",
    "edge_id":      "SCHEMA.TABLE.FIELD__to__SCHEMA.TABLE.FIELD__m_MAPPING",
    "operation":    "UPDATE",            // ADD | UPDATE | DELETE
    "entity":       "cosmos_edge",       // cosmos_edge | neo4j_field
    "property":     "lookup_condition",  // which field was changed
    "old_value":    "DTI_KEY = i_DTI_KEY",
    "new_value":    "DTI_KEY = i_DTI_KEY AND VALID_FLAG = 'Y'",
    "submitted_by": "dev@domain.com",
    "submitted_at": "2026-06-30T14:00:00Z",
    "applied_at":   "2026-06-30T15:00:00Z",  // set on approval
    "applied_by":   "approver@domain.com"     // set on approval
}
```

---

## Migration Path

1. **Create `change_log` container** with `/version_id` partition key
2. **Copy existing delta records** from `transformation_details` (where `doc_type = 'edge_change'`) into `change_log`
3. **Apply pending approved deltas** as point-updates to the base docs in `transformation_details`
4. **Remove delta records** from `transformation_details` (clean up mixed data)
5. **Remove `version_id` from base docs** — no longer needed for reads
6. **Update `change_manager.py`** — write to `change_log` instead of `transformation_details`
7. **Update `version_manager.approve_version`** — add point-update logic on approval
8. **Simplify `cosmos_tools._run_cosmos_query`** — remove version filter and patch merge logic
9. **Simplify `active_version.py`** — remove `get_cosmos_data_version()` and patch cache

Steps 1–5 are a one-time migration. Steps 6–9 are code changes.

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Point-update fails mid-approval | Approval writes `change_log` first (audit), then updates docs. Incomplete updates are detectable by comparing `change_log` applied_at vs doc state. |
| Rollback of approved version | Read `change_log` entries for that `version_id`, swap `old_value` back into docs. |
| Concurrent approvals | `version_registry` status check prevents approving two DRAFTs simultaneously (existing guard). |
| Full reload overwrites patched values | Expected behavior — a full reload replaces the entire materialized view. All prior patches are preserved in `change_log` for audit. |
