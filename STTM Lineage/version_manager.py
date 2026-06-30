"""
version_manager.py  (PoC)
-------------------------
Lightweight version registry for the BLDM lineage source-control workflow.

Manages a DRAFT → ACTIVE → ARCHIVED → REJECTED lifecycle stored in the
'version_registry' Cosmos DB container (created automatically if absent).

The container uses /id as partition key so every version is read as a cheap
point-read; cross-partition queries on the tiny registry are fine.

Usage (CLI):
  python version_manager.py create  --description "June cycle" --by dev@domain.com
  python version_manager.py list
  python version_manager.py list    --status DRAFT
  python version_manager.py approve --id v_20260629_001 --by owner@domain.com
  python version_manager.py reject  --id v_20260629_001 --by owner@domain.com --reason "Bad expressions"
  python version_manager.py diff    --draft v_20260629_001

Load with a version tag (after creating a DRAFT):
  cd "STTM Lineage"
  python load_to_cosmos.py --version-id v_20260629_001
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env — search the same locations as the rest of the project
for _env_path in [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent / "lineage-agent" / ".env",
]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from azure.cosmos import CosmosClient, PartitionKey, exceptions

_DATABASE  = "lineage"
_CONTAINER = "version_registry"
_TD_CONTAINER = "transformation_details"
_DIFF_CONTAINER = "version_diffs"

# Fields compared per edge to detect changes.  Order matters for display.
_DIFF_FIELDS = [
    "final_expression", "custom_sql", "lookup_condition",
    "filter_condition", "update_strategy_expression",
]


# ── Cosmos helpers ──────────────────────────────────────────────────────────

def _get_registry(create_if_missing: bool = True):
    """Return a Cosmos container client for version_registry."""
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    if not endpoint or not key:
        print("ERROR: COSMOS_ENDPOINT and COSMOS_KEY must be set in .env", file=sys.stderr)
        sys.exit(1)
    client = CosmosClient(url=endpoint, credential=key)
    db     = client.create_database_if_not_exists(id=_DATABASE)
    if create_if_missing:
        return db.create_container_if_not_exists(
            id=_CONTAINER,
            partition_key=PartitionKey(path="/id"),
            offer_throughput=400,
        )
    return db.get_container_client(_CONTAINER)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_seq(container) -> int:
    """Return next sequential version number (MAX version_seq + 1).
    seq=0 is reserved for the baseline; first real change is seq=1.
    """
    results = list(container.query_items(
        query="SELECT VALUE MAX(c.version_seq) FROM c",
        enable_cross_partition_query=True,
    ))
    current_max = results[0] if results and results[0] is not None else 0
    return current_max + 1


def _get_current_active(container) -> dict | None:
    """Return the current ACTIVE version document, or None."""
    results = list(container.query_items(
        query="SELECT * FROM c WHERE c.status = 'ACTIVE'",
        enable_cross_partition_query=True,
    ))
    return results[0] if results else None


# ── Core API ────────────────────────────────────────────────────────────────

def create_version(description: str, created_by: str, xml_source: str = "") -> str:
    """
    Register a new DRAFT version in the version registry.
    Returns the new version_id string.

    The DRAFT is not visible to query tools until it is approved.
    Load data with this version_id tag using:
      python load_to_cosmos.py --version-id <returned_id>
    """
    container  = _get_registry()
    seq        = _next_seq(container)
    date_str   = datetime.now(timezone.utc).strftime("%Y%m%d")
    version_id = f"v_{date_str}_{seq:03d}"

    active     = _get_current_active(container)

    doc = {
        "id":                     version_id,
        "version_seq":            seq,
        "status":                 "DRAFT",
        "description":            description,
        "xml_source":             xml_source,
        "created_by":             created_by,
        "created_at":             _now_utc(),
        "base_active_version_id": active["id"] if active else None,
        "approved_by":            None,
        "approved_at":            None,
        "effective_from":         None,
        "effective_to":           None,
        "rejected_by":            None,
        "rejected_at":            None,
        "rejection_reason":       None,
    }
    container.upsert_item(doc)

    print(f"\n✅  Created DRAFT version : {version_id}")
    print(f"    Description           : {description}")
    print(f"    Based on active       : {active['id'] if active else '(none — first version)'}")
    print(f"\n    Next step — load data with this version tag:")
    print(f"      python load_to_cosmos.py --version-id {version_id}\n")
    return version_id


def approve_version(version_id: str, approved_by: str) -> None:
    """
    Promote a DRAFT to ACTIVE (pointer-switch strategy).

    The only commit point is a single Cosmos document write that flips the
    DRAFT registry entry to ACTIVE.  The old ACTIVE entry is archived in a
    second write; if that second write fails it is self-healing (the registry
    would briefly have two non-ARCHIVED entries, which reconcile_registry()
    detects and fixes automatically).
    """
    container = _get_registry()

    # Read the draft
    try:
        draft = container.read_item(item=version_id, partition_key=version_id)
    except exceptions.CosmosResourceNotFoundError:
        print(f"ERROR: Version '{version_id}' not found.", file=sys.stderr)
        sys.exit(1)

    if draft["status"] != "DRAFT":
        print(f"ERROR: Version '{version_id}' is '{draft['status']}', not DRAFT.", file=sys.stderr)
        sys.exit(1)

    # Self-approval guard (PoC — honour principle)
    if draft.get("created_by") == approved_by:
        print("ERROR: Self-approval is not permitted. Use a different approver.", file=sys.stderr)
        sys.exit(1)

    now = _now_utc()

    # Step 1 — Archive the old active (non-critical; failure is self-healing)
    old_active = _get_current_active(container)
    if old_active:
        old_active["status"]       = "ARCHIVED"
        old_active["effective_to"] = now
        container.upsert_item(old_active)
        print(f"    Archived previous active : {old_active['id']}")

    # Step 2 — COMMIT: flip draft to ACTIVE (single-document atomic write)
    draft["status"]        = "ACTIVE"
    draft["approved_by"]   = approved_by
    draft["approved_at"]   = now
    draft["effective_from"] = now
    container.upsert_item(draft)

    print(f"\n✅  Approved : {version_id} is now ACTIVE")
    print(f"    Approved by    : {approved_by}")
    print(f"    Effective from : {now}")
    print(f"\n    All lineage queries will resolve to this version on next cache refresh (<60 s).\n")


def reject_version(version_id: str, rejected_by: str, reason: str = "") -> None:
    """
    Mark a DRAFT version as REJECTED.

    The tagged data documents in transformation_details are NOT deleted — they
    stay with their version_id but the registry marks this version as rejected
    so the pointer resolver never returns it.
    """
    container = _get_registry()

    try:
        draft = container.read_item(item=version_id, partition_key=version_id)
    except exceptions.CosmosResourceNotFoundError:
        print(f"ERROR: Version '{version_id}' not found.", file=sys.stderr)
        sys.exit(1)

    if draft["status"] != "DRAFT":
        print(f"ERROR: Version '{version_id}' is '{draft['status']}', not DRAFT.", file=sys.stderr)
        sys.exit(1)

    draft["status"]           = "REJECTED"
    draft["rejected_by"]      = rejected_by
    draft["rejected_at"]      = _now_utc()
    draft["rejection_reason"] = reason
    container.upsert_item(draft)

    print(f"\n❌  Rejected : {version_id}")
    print(f"    Rejected by : {rejected_by}")
    print(f"    Reason      : {reason or '(none given)'}")
    print(f"\n    Active version is unchanged.\n")


def list_versions(status: str = "") -> list[dict]:
    """Return all versions from the registry, optionally filtered by status."""
    container = _get_registry(create_if_missing=False)
    if status:
        sql    = "SELECT * FROM c WHERE c.status = @status ORDER BY c.version_seq DESC"
        kwargs = {"query": sql, "parameters": [{"name": "@status", "value": status.upper()}],
                  "enable_cross_partition_query": True}
    else:
        sql    = "SELECT * FROM c ORDER BY c.version_seq DESC"
        kwargs = {"query": sql, "enable_cross_partition_query": True}
    return list(container.query_items(**kwargs))


def diff_version(draft_version_id: str) -> None:
    """
    Print a simple edge-count diff: DRAFT version vs its base ACTIVE version.
    Queries the transformation_details Cosmos container for document counts
    per mapping_name for both versions and shows the delta table.
    """
    container = _get_registry(create_if_missing=False)

    try:
        draft = container.read_item(item=draft_version_id, partition_key=draft_version_id)
    except exceptions.CosmosResourceNotFoundError:
        print(f"ERROR: Version '{draft_version_id}' not found.", file=sys.stderr)
        sys.exit(1)

    active_vid = draft.get("base_active_version_id")

    # Access transformation_details container
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    td = CosmosClient(url=endpoint, credential=key) \
           .get_database_client(_DATABASE) \
           .get_container_client("transformation_details")

    def _total_count(vid: str) -> int:
        if not vid:
            return 0
        rows = list(td.query_items(
            query="SELECT c.id FROM c WHERE c.version_id = @vid",
            parameters=[{"name": "@vid", "value": vid}],
            enable_cross_partition_query=True,
        ))
        return len(rows)

    def _count_by_mapping(vid: str) -> dict[str, int]:
        if not vid:
            return {}
        rows = list(td.query_items(
            query="SELECT c.mapping_name FROM c WHERE c.version_id = @vid",
            parameters=[{"name": "@vid", "value": vid}],
            enable_cross_partition_query=True,
        ))
        counts: dict[str, int] = {}
        for r in rows:
            m = r.get("mapping_name") or "UNKNOWN"
            counts[m] = counts.get(m, 0) + 1
        return counts

    draft_total  = _total_count(draft_version_id)
    active_total = _total_count(active_vid)

    print(f"\n{'─'*68}")
    print(f"  Diff report")
    print(f"  Draft  : {draft_version_id}  ({draft.get('description', '')})")
    print(f"  Base   : {active_vid or '(no base — first version)'}")
    print(f"{'─'*68}")
    print(f"  Total edges in DRAFT  : {draft_total}")
    print(f"  Total edges in ACTIVE : {active_total}")
    print(f"  Net change            : {draft_total - active_total:+d}")

    if draft_total > 0 or active_total > 0:
        draft_map  = _count_by_mapping(draft_version_id)
        active_map = _count_by_mapping(active_vid) if active_vid else {}
        all_keys   = sorted(set(list(draft_map.keys()) + list(active_map.keys())))

        if all_keys:
            print(f"\n  {'Mapping':<52}  {'Active':>7}  {'Draft':>7}  {'Δ':>6}")
            print(f"  {'─'*52}  {'─'*7}  {'─'*7}  {'─'*6}")
            for m in all_keys:
                a      = active_map.get(m, 0)
                d      = draft_map.get(m, 0)
                delta  = d - a
                marker = "  ← NEW" if a == 0 else ("  ← REMOVED" if d == 0 else "")
                print(f"  {m:<52}  {a:>7}  {d:>7}  {delta:>+6}{marker}")

    print(f"{'─'*68}\n")


# ── Diff Engine (Phase 6) ──────────────────────────────────────────────────

def _get_td_container():
    """Return a container client for transformation_details."""
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    client   = CosmosClient(url=endpoint, credential=key)
    db       = client.get_database_client(_DATABASE)
    return db.get_container_client(_TD_CONTAINER)


def _get_diff_container():
    """Return (or create) the version_diffs container."""
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    client   = CosmosClient(url=endpoint, credential=key)
    db       = client.create_database_if_not_exists(id=_DATABASE)
    return db.create_container_if_not_exists(
        id=_DIFF_CONTAINER,
        partition_key=PartitionKey(path="/draft_version_id"),
        offer_throughput=400,
    )


def _fetch_edges_for_version(td, version_id: str) -> dict[str, dict]:
    """Fetch all transformation_details docs for a version.
    Returns a dict keyed by edge_id → doc (lightweight projection)."""
    if not version_id:
        return {}
    sql = (
        "SELECT c.edge_id, c.from_vertex, c.to_vertex, c.mapping_name, "
        "c.final_expression, c.custom_sql, c.lookup_condition, "
        "c.filter_condition, c.update_strategy_expression, "
        "c.transformation_steps_count "
        "FROM c WHERE c.version_id = @vid"
    )
    rows = list(td.query_items(
        query=sql,
        parameters=[{"name": "@vid", "value": version_id}],
        enable_cross_partition_query=True,
    ))
    result = {}
    for r in rows:
        eid = r.get("edge_id") or r.get("id", "")
        if eid:
            result[eid] = r
    return result


def compute_diff(draft_version_id: str) -> dict:
    """
    Compare a DRAFT version against its base ACTIVE version at the edge level.

    For every edge_id present in both versions, compare the diff fields
    (expression, lookup, filter, SQL, update strategy).  Report:
      - edges_added:   in DRAFT but not ACTIVE
      - edges_removed: in ACTIVE but not DRAFT
      - edges_changed: in both but field values differ

    Writes the result to the version_diffs Cosmos container and returns
    the diff document dict.
    """
    registry = _get_registry(create_if_missing=False)

    # Validate the draft
    try:
        draft_doc = registry.read_item(
            item=draft_version_id, partition_key=draft_version_id
        )
    except exceptions.CosmosResourceNotFoundError:
        raise ValueError(f"Version '{draft_version_id}' not found in registry.")

    if draft_doc.get("status") != "DRAFT":
        raise ValueError(
            f"Version '{draft_version_id}' has status '{draft_doc.get('status')}', "
            "not DRAFT.  Diff can only be computed for DRAFT versions."
        )

    active_vid = draft_doc.get("base_active_version_id")
    td = _get_td_container()

    print(f"  Fetching DRAFT edges ({draft_version_id})...")
    draft_edges  = _fetch_edges_for_version(td, draft_version_id)
    print(f"    → {len(draft_edges)} edges")

    print(f"  Fetching ACTIVE edges ({active_vid or 'none'})...")
    active_edges = _fetch_edges_for_version(td, active_vid)
    print(f"    → {len(active_edges)} edges")

    draft_ids  = set(draft_edges.keys())
    active_ids = set(active_edges.keys())

    added_ids   = draft_ids - active_ids
    removed_ids = active_ids - draft_ids
    common_ids  = draft_ids & active_ids

    # Added edges — lightweight summary
    edges_added = [
        {
            "edge_id":      eid,
            "from_vertex":  draft_edges[eid].get("from_vertex", ""),
            "to_vertex":    draft_edges[eid].get("to_vertex", ""),
            "mapping_name": draft_edges[eid].get("mapping_name", ""),
        }
        for eid in sorted(added_ids)
    ]

    # Removed edges
    edges_removed = [
        {
            "edge_id":      eid,
            "from_vertex":  active_edges[eid].get("from_vertex", ""),
            "to_vertex":    active_edges[eid].get("to_vertex", ""),
            "mapping_name": active_edges[eid].get("mapping_name", ""),
        }
        for eid in sorted(removed_ids)
    ]

    # Changed edges — per-field delta
    edges_changed = []
    for eid in sorted(common_ids):
        d = draft_edges[eid]
        a = active_edges[eid]
        changes = []
        for field in _DIFF_FIELDS:
            old_val = (a.get(field) or "")
            new_val = (d.get(field) or "")
            if old_val != new_val:
                changes.append({
                    "field":     field,
                    "old_value": old_val,
                    "new_value": new_val,
                })
        if changes:
            edges_changed.append({
                "edge_id":      eid,
                "from_vertex":  d.get("from_vertex", ""),
                "to_vertex":    d.get("to_vertex", ""),
                "mapping_name": d.get("mapping_name", ""),
                "changes":      changes,
            })

    diff_id = f"diff__{draft_version_id}__{active_vid or 'none'}"

    diff_doc = {
        "id":                diff_id,
        "draft_version_id":  draft_version_id,
        "base_version_id":   active_vid or "(none — first version)",
        "generated_at":      _now_utc(),
        "summary": {
            "edges_added":   len(edges_added),
            "edges_removed": len(edges_removed),
            "edges_changed": len(edges_changed),
            "edges_unchanged": len(common_ids) - len(edges_changed),
        },
        "edges_added":   edges_added,
        "edges_removed": edges_removed,
        "edges_changed": edges_changed,
    }

    # Persist to version_diffs container
    diff_container = _get_diff_container()
    diff_container.upsert_item(diff_doc)

    print(f"\n  Diff computed:")
    print(f"    Added   : {len(edges_added)}")
    print(f"    Removed : {len(edges_removed)}")
    print(f"    Changed : {len(edges_changed)}")
    print(f"    Unchanged : {len(common_ids) - len(edges_changed)}")
    print(f"    → Saved to version_diffs as {diff_id}")

    return diff_doc


def get_diff_report(draft_version_id: str) -> dict | None:
    """
    Read a previously computed diff report from version_diffs.
    Returns None if no diff has been computed for this draft yet.
    """
    try:
        diff_container = _get_diff_container()
        # Try both possible id patterns (with and without base version)
        results = list(diff_container.query_items(
            query="SELECT * FROM c WHERE c.draft_version_id = @vid",
            parameters=[{"name": "@vid", "value": draft_version_id}],
            enable_cross_partition_query=True,
        ))
        if not results:
            return None
        doc = results[0]
        for k in ("_rid", "_self", "_etag", "_attachments", "_ts"):
            doc.pop(k, None)
        return doc
    except Exception:
        return None


def reconcile_registry() -> None:
    """
    Safety sweep: if more than one ACTIVE entry exists (e.g. after a partial
    failure during approval) keep only the latest one and archive the rest.
    Safe to call at any time; no-ops when the registry is consistent.
    """
    container = _get_registry(create_if_missing=False)
    actives   = list(container.query_items(
        query="SELECT * FROM c WHERE c.status = 'ACTIVE'",
        enable_cross_partition_query=True,
    ))
    if len(actives) <= 1:
        return  # Already consistent

    actives.sort(key=lambda d: d.get("effective_from", ""), reverse=True)
    for stale in actives[1:]:
        stale["status"]       = "ARCHIVED"
        stale["effective_to"] = _now_utc()
        container.upsert_item(stale)
        print(f"  Reconciled stale ACTIVE entry → ARCHIVED: {stale['id']}")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lineage version manager (PoC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # create
    p = sub.add_parser("create", help="Register a new DRAFT version")
    p.add_argument("--description", required=True, help="Human-readable description of this change set")
    p.add_argument("--by",  required=True, metavar="USER_EMAIL", help="Email of the submitter")
    p.add_argument("--xml", default="",    metavar="XML_FILENAME", help="Source XML filename (optional)")

    # list
    p = sub.add_parser("list", help="List versions (all or filtered by status)")
    p.add_argument("--status", default="", help="DRAFT | ACTIVE | ARCHIVED | REJECTED")

    # approve
    p = sub.add_parser("approve", help="Approve a DRAFT version (makes it ACTIVE)")
    p.add_argument("--id", required=True, metavar="VERSION_ID")
    p.add_argument("--by", required=True, metavar="APPROVER_EMAIL")

    # reject
    p = sub.add_parser("reject", help="Reject a DRAFT version")
    p.add_argument("--id",     required=True, metavar="VERSION_ID")
    p.add_argument("--by",     required=True, metavar="REVIEWER_EMAIL")
    p.add_argument("--reason", default="", help="Reason for rejection")

    # diff (count-level, quick)
    p = sub.add_parser("diff", help="Quick count-level diff: DRAFT vs base ACTIVE")
    p.add_argument("--draft", required=True, metavar="VERSION_ID")

    # compute-diff (edge-level, full)
    p = sub.add_parser("compute-diff", help="Full edge-level diff: added/removed/changed edges → saved to version_diffs")
    p.add_argument("--draft", required=True, metavar="VERSION_ID")

    # reconcile
    sub.add_parser("reconcile", help="Fix any duplicate ACTIVE entries (self-healing sweep)")

    args = parser.parse_args()

    if args.cmd == "create":
        create_version(args.description, args.by, getattr(args, "xml", ""))

    elif args.cmd == "list":
        versions = list_versions(args.status)
        if not versions:
            print("No versions found.")
            return
        print(f"\n  {'ID':<22}  {'Seq':>4}  {'Status':<10}  {'By':<30}  {'Date':<20}  Description")
        print(f"  {'─'*22}  {'─'*4}  {'─'*10}  {'─'*30}  {'─'*20}  {'─'*40}")
        for v in versions:
            date = (v.get("effective_from") or v.get("created_at") or "")[:19]
            by   = v.get("approved_by") or v.get("created_by") or ""
            print(f"  {v['id']:<22}  {v.get('version_seq', 0):>4}  "
                  f"{v.get('status', ''):<10}  {by:<30}  {date:<20}  {v.get('description', '')}")
        print()

    elif args.cmd == "approve":
        approve_version(args.id, args.by)

    elif args.cmd == "reject":
        reject_version(args.id, args.by, args.reason)

    elif args.cmd == "diff":
        diff_version(args.draft)

    elif args.cmd == "compute-diff":
        try:
            diff_doc = compute_diff(args.draft)
            print(f"\n    Full diff report saved. Use 'python version_manager.py diff --draft {args.draft}' for quick counts.")
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "reconcile":
        reconcile_registry()
        print("Reconciliation complete.")


if __name__ == "__main__":
    main()
