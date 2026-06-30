# version_tools.py
# ────────────────────────────────────────────────────────────
# Agent-facing versioning tools for the Lineage Agent (PoC).
#
# Three tools are exposed:
#   1. get_active_version_info()       — what version am I on?
#   2. list_lineage_versions(status)   — version history / pending drafts
#   3. get_version_diff_summary(id)    — what changed in a draft?
#
# All tools degrade gracefully to a clear "not enabled" message when
# the version_registry container does not yet exist so that the agent
# is never broken by an unset-up registry.
# ────────────────────────────────────────────────────────────

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_DATABASE  = "lineage"
_REGISTRY  = "version_registry"
_DETAILS   = "transformation_details"

_NOT_ENABLED = json.dumps({
    "message": (
        "Version registry not found. Versioning has not been enabled yet. "
        "Run 'python version_manager.py create ...' to register the first version."
    )
})


def _get_cosmos_db():
    from azure.cosmos import CosmosClient
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    if not endpoint or not key:
        raise RuntimeError("COSMOS_ENDPOINT and COSMOS_KEY must be set in .env")
    return CosmosClient(url=endpoint, credential=key).get_database_client(_DATABASE)


def _strip_meta(docs: list) -> list:
    for d in docs:
        for k in ("_rid", "_self", "_etag", "_attachments", "_ts"):
            d.pop(k, None)
    return docs


# ─── Tool 1 ──────────────────────────────────────────────────────────────────

def get_active_version_info() -> str:
    """
    Returns the metadata for the currently active lineage version, including
    the version_id, description, who approved it, and when it became active.

    Use this when the user asks:
      "what version is active?", "which version am I on?",
      "when was the lineage last updated?", "who approved the current version?"

    :return: JSON object with id, version_seq, description, approved_by,
             effective_from, xml_source, and base_active_version_id.
    :rtype: str
    """
    try:
        db  = _get_cosmos_db()
        reg = db.get_container_client(_REGISTRY)
        results = list(reg.query_items(
            query="SELECT * FROM c WHERE c.status = 'ACTIVE'",
            enable_cross_partition_query=True,
        ))
        if not results:
            return _NOT_ENABLED
        doc = _strip_meta(results)[0]
        return json.dumps(doc, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─── Tool 2 ──────────────────────────────────────────────────────────────────

def list_lineage_versions(status: str = "") -> str:
    """
    Lists lineage versions tracked in the version registry, optionally
    filtered by lifecycle status. Use when the user asks for version history,
    pending approvals, rejected drafts, or a changelog.

    :param status: Optional filter. Accepted values:
                   "DRAFT"    — pending change requests awaiting approval,
                   "ACTIVE"   — the single currently active version,
                   "ARCHIVED" — previously approved versions now superseded,
                   "REJECTED" — drafts that were rejected,
                   ""         — return all versions (default).
    :return: JSON array of version records ordered newest-first, each with
             id, version_seq, status, description, created_by, approved_by,
             created_at, effective_from.
    :rtype: str
    """
    try:
        db  = _get_cosmos_db()
        reg = db.get_container_client(_REGISTRY)

        if status:
            sql    = "SELECT * FROM c WHERE c.status = @s ORDER BY c.version_seq DESC"
            kwargs = {
                "query": sql,
                "parameters": [{"name": "@s", "value": status.upper()}],
                "enable_cross_partition_query": True,
            }
        else:
            kwargs = {
                "query": "SELECT * FROM c ORDER BY c.version_seq DESC",
                "enable_cross_partition_query": True,
            }

        results = list(reg.query_items(**kwargs))
        if not results:
            return json.dumps({"message": "No versions found.", "filter": status or "all"})
        return json.dumps(_strip_meta(results), ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ─── Tool 3 ──────────────────────────────────────────────────────────────────

def get_version_diff_summary(draft_version_id: str) -> str:
    """
    Returns the diff report for a DRAFT version showing which edges were
    added, removed, or changed compared to the base ACTIVE version.

    If a full edge-level diff has been computed (via compute-diff), it is
    returned directly from the version_diffs store.  Otherwise, a live
    count-level summary is generated on the fly.

    Use when the user asks "what changed in draft X?", "show me the diff",
    "what was added/removed?", or "what is pending approval in version X?".

    :param draft_version_id: The version_id of the DRAFT to inspect.
                             Example: "v_20260629_001"
    :return: JSON object with added/removed/changed edge details (if computed)
             or count-level summary with per-mapping breakdown.
    :rtype: str
    """
    try:
        db  = _get_cosmos_db()
        reg = db.get_container_client(_REGISTRY)

        # Validate draft exists
        results = list(reg.query_items(
            query="SELECT * FROM c WHERE c.id = @id",
            parameters=[{"name": "@id", "value": draft_version_id}],
            enable_cross_partition_query=True,
        ))
        if not results:
            return json.dumps({"error": f"Version '{draft_version_id}' not found in registry."})

        draft_doc = _strip_meta(results)[0]
        if draft_doc.get("status") != "DRAFT":
            return json.dumps({
                "message": (
                    f"Version '{draft_version_id}' has status "
                    f"'{draft_doc.get('status')}'. "
                    "Diff reports are only available for DRAFT versions."
                ),
                "version": draft_doc,
            })

        # Prefer stored diff report (Phase 6)
        try:
            diff_ct = db.get_container_client("version_diffs")
            diff_results = list(diff_ct.query_items(
                query="SELECT * FROM c WHERE c.draft_version_id = @vid",
                parameters=[{"name": "@vid", "value": draft_version_id}],
                enable_cross_partition_query=True,
            ))
            if diff_results:
                doc = _strip_meta(diff_results)[0]
                # Truncate large arrays for agent context (keep first 20 items)
                for key in ("edges_added", "edges_removed", "edges_changed"):
                    items = doc.get(key, [])
                    if len(items) > 20:
                        doc[key] = items[:20]
                        doc[f"{key}_truncated"] = True
                        doc[f"{key}_total"] = doc["summary"].get(key, len(items))
                return json.dumps(doc, ensure_ascii=False, indent=2)
        except Exception:
            pass  # version_diffs container may not exist yet; fall through

        # Fall back to live count-level summary.
        # Note: SELECT VALUE COUNT(1) and GROUP BY are unreliable on cross-partition
        # queries in Cosmos DB; fetch minimal projection and aggregate in Python instead.
        active_vid = draft_doc.get("base_active_version_id")
        td = db.get_container_client(_DETAILS)

        def _count_by_mapping(vid: str) -> dict[str, int]:
            """Fetch mapping_name for all edges of a version and count in Python."""
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

        draft_map  = _count_by_mapping(draft_version_id)
        active_map = _count_by_mapping(active_vid)
        draft_total  = sum(draft_map.values())
        active_total = sum(active_map.values())
        all_keys   = sorted(set(list(draft_map.keys()) + list(active_map.keys())))

        breakdown = []
        for m in all_keys:
            a     = active_map.get(m, 0)
            d     = draft_map.get(m, 0)
            delta = d - a
            tag   = "NEW" if a == 0 else ("REMOVED" if d == 0 else "CHANGED" if delta != 0 else "UNCHANGED")
            breakdown.append({
                "mapping_name": m,
                "active_edges": a,
                "draft_edges":  d,
                "delta":        delta,
                "status":       tag,
            })

        return json.dumps({
            "draft_version_id":       draft_version_id,
            "description":            draft_doc.get("description"),
            "submitted_by":           draft_doc.get("created_by"),
            "created_at":             draft_doc.get("created_at"),
            "base_active_version_id": active_vid,
            "total_edges_in_draft":   draft_total,
            "total_edges_in_active":  active_total,
            "net_change":             draft_total - active_total,
            "mapping_breakdown":      breakdown,
            "note": "Full edge-level diff not yet computed. Run 'compute-diff' for detailed added/removed/changed report.",
        }, ensure_ascii=False, indent=2)

    except Exception as exc:
        return json.dumps({"error": str(exc)})
