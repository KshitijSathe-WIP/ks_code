"""
change_manager.py
-----------------
Applies lineage changes submitted via a YAML patch file.

ALL change submissions must go through a .yaml patch file.
There are no individual CLI subcommands or agent-driven single-record updates.
This enforces a clear audit trail: every change is backed by a file that can
be stored in source control alongside the XML exports.

Supported change operations
----------------------------
  entity: cosmos_edge
    operation: ADD      -- add a new linkage (TRANSFORMS_TO edge + Cosmos doc)
    operation: UPDATE   -- change an edge property (final_expression, custom_sql,
                           lookup_condition, filter_condition, update_strategy_expression)
    operation: DELETE   -- mark an existing edge for removal

  entity: neo4j_field
    operation: ADD      -- register a new field node in the lineage graph
    operation: UPDATE   -- change a field property (data_type, precision, layer)
    (DELETE neo4j_field is not supported; use XML re-extraction for structural removals)

Usage
-----
  # Validate the patch file without writing anything (recommended first step)
  python change_manager.py apply-patch --file "patches/my_changes.yaml" --dry-run

  # Submit the patch (submitted_by and description read from the file)
  python change_manager.py apply-patch --file "patches/my_changes.yaml"

  # Override submitter / description from the command line
  python change_manager.py apply-patch --file "patches/my_changes.yaml" ^
      --by dev@domain.com --description "Fix rate expression"

After submission, approve or reject via version_manager.py:
  python version_manager.py approve --id <version_id> --by <approver>
  python version_manager.py reject  --id <version_id> --by <approver> --reason "..."
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env
for _env_path in [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent / "lineage-agent" / ".env",
]:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from azure.cosmos import CosmosClient, exceptions

_DATABASE  = "lineage"
_DETAILS   = "transformation_details"
_REGISTRY  = "version_registry"


# ── Cosmos helpers ──────────────────────────────────────────────────────────

def _get_db():
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    if not endpoint or not key:
        print("ERROR: COSMOS_ENDPOINT and COSMOS_KEY must be set.", file=sys.stderr)
        sys.exit(1)
    return CosmosClient(url=endpoint, credential=key).get_database_client(_DATABASE)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_active_version_id(registry) -> str | None:
    results = list(registry.query_items(
        query="SELECT TOP 1 c.id FROM c WHERE c.status = 'ACTIVE'",
        enable_cross_partition_query=True,
    ))
    return results[0]["id"] if results else None


def _next_seq(registry) -> int:
    results = list(registry.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))
    return (results[0] if results else 0) + 1


def _create_patch_draft(registry, description: str, created_by: str, active_vid: str | None) -> str:
    """Create a PATCH-type DRAFT entry in version_registry; return the new version_id."""
    seq        = _next_seq(registry)
    date_str   = datetime.now(timezone.utc).strftime("%Y%m%d")
    version_id = f"v_{date_str}_{seq:03d}"
    doc = {
        "id":                     version_id,
        "version_seq":            seq,
        "status":                 "DRAFT",
        "change_type":            "PATCH",
        "description":            description,
        "created_by":             created_by,
        "created_at":             _now_utc(),
        "base_active_version_id": active_vid,
        "approved_by":            None,
        "approved_at":            None,
        "effective_from":         None,
        "effective_to":           None,
        "rejected_by":            None,
        "rejected_at":            None,
        "rejection_reason":       None,
    }
    registry.upsert_item(doc)
    return version_id


def _find_existing_edge(td, edge_id: str, active_vid: str | None) -> dict | None:
    if active_vid:
        sql    = "SELECT * FROM c WHERE c.edge_id = @eid AND c.version_id = @vid"
        params = [{"name": "@eid", "value": edge_id}, {"name": "@vid", "value": active_vid}]
    else:
        sql    = "SELECT * FROM c WHERE c.edge_id = @eid"
        params = [{"name": "@eid", "value": edge_id}]
    results = list(td.query_items(query=sql, parameters=params, enable_cross_partition_query=True))
    return results[0] if results else None


# ── YAML patch file loading ──────────────────────────────────────────────────

def _load_patch_file(file_path: Path) -> dict:
    """Load a YAML patch file and return the parsed dict."""
    if file_path.suffix.lower() not in (".yaml", ".yml"):
        print(
            f"ERROR: Only .yaml / .yml patch files are accepted. Got: '{file_path.suffix}'",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(file_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        print("ERROR: Patch file must be a mapping at the top level.", file=sys.stderr)
        sys.exit(1)
    return data


# ── Validation ──────────────────────────────────────────────────────────────

_VALID_ENTITIES   = {"neo4j_field", "cosmos_edge"}
_VALID_OPERATIONS = {"ADD", "UPDATE", "DELETE"}


def _validate_changes(changes: list, td, active_vid: str | None) -> list[str]:
    """
    Pre-flight validation of all change records.
    Returns a list of error strings; empty list means all valid.
    """
    errors = []
    for i, ch in enumerate(changes):
        idx    = i + 1
        op     = str(ch.get("operation", "")).upper()
        entity = str(ch.get("entity", "")).lower()

        if op not in _VALID_OPERATIONS:
            errors.append(
                f"Change {idx}: invalid operation '{op}'. Must be ADD, UPDATE, or DELETE."
            )
            continue
        if entity not in _VALID_ENTITIES:
            errors.append(
                f"Change {idx}: invalid entity '{entity}'. "
                "Must be 'neo4j_field' or 'cosmos_edge'."
            )
            continue

        if entity == "cosmos_edge":
            if op == "ADD":
                for req in ("from_id", "to_id", "mapping_name"):
                    if not ch.get(req):
                        errors.append(f"Change {idx}: ADD cosmos_edge requires '{req}'.")
            elif op in ("UPDATE", "DELETE"):
                if not ch.get("edge_id"):
                    errors.append(f"Change {idx}: {op} cosmos_edge requires 'edge_id'.")
                else:
                    existing = _find_existing_edge(td, ch["edge_id"], active_vid)
                    if not existing:
                        errors.append(
                            f"Change {idx}: edge_id '{ch['edge_id']}' "
                            "not found in active version."
                        )
                    if op == "UPDATE":
                        if not ch.get("property"):
                            errors.append(
                                f"Change {idx}: UPDATE cosmos_edge requires 'property'."
                            )
                        if "new_value" not in ch:
                            errors.append(
                                f"Change {idx}: UPDATE cosmos_edge requires 'new_value'."
                            )

        elif entity == "neo4j_field":
            if op == "ADD":
                for req in ("field_id", "table_name", "db_schema", "layer"):
                    if not ch.get(req):
                        errors.append(f"Change {idx}: ADD neo4j_field requires '{req}'.")
            elif op == "UPDATE":
                if not ch.get("field_id"):
                    errors.append(f"Change {idx}: UPDATE neo4j_field requires 'field_id'.")
                if not ch.get("property"):
                    errors.append(f"Change {idx}: UPDATE neo4j_field requires 'property'.")
                if "new_value" not in ch:
                    errors.append(f"Change {idx}: UPDATE neo4j_field requires 'new_value'.")
            elif op == "DELETE":
                errors.append(
                    f"Change {idx}: DELETE neo4j_field is not supported via patch file. "
                    "Use XML re-extraction for structural field removals."
                )

    return errors


# ── Apply each change ────────────────────────────────────────────────────────

def _apply_change(ch: dict, td, version_id: str, active_vid: str | None, now: str) -> dict:
    """Write one change record to Cosmos and return a summary dict."""
    op     = str(ch.get("operation", "")).upper()
    entity = str(ch.get("entity", "")).lower()

    if entity == "cosmos_edge":
        if op == "ADD":
            edge_id = f"{ch['from_id']}__to__{ch['to_id']}__m_{ch['mapping_name']}"
            doc = {
                "id":               f"{edge_id}__{version_id}",
                "doc_type":         "edge_change",
                "operation":        "ADD",
                "edge_id":          edge_id,
                "from_vertex":      ch["from_id"],
                "to_vertex":        ch["to_id"],
                "mapping_name":     ch["mapping_name"],
                "folder_name":      ch.get("folder_name", ""),
                "final_expression": ch.get("expression", ""),
                "version_id":       version_id,
                "submitted_at":     now,
            }
            td.upsert_item(doc)
            return {"operation": "ADD", "entity": "cosmos_edge", "edge_id": edge_id}

        elif op == "UPDATE":
            existing  = _find_existing_edge(td, ch["edge_id"], active_vid)
            old_value = existing.get(ch["property"], "(not set)") if existing else "(unknown)"
            mapping   = existing.get("mapping_name", "__UNKNOWN__") if existing else "__UNKNOWN__"
            doc = {
                "id":           f"{ch['edge_id']}__{ch['property']}__{version_id}",
                "doc_type":     "edge_change",
                "operation":    "UPDATE",
                "edge_id":      ch["edge_id"],
                "mapping_name": mapping,
                "property":     ch["property"],
                "old_value":    str(old_value),
                "new_value":    str(ch["new_value"]),
                "version_id":   version_id,
                "submitted_at": now,
            }
            td.upsert_item(doc)
            return {
                "operation": "UPDATE", "entity": "cosmos_edge",
                "edge_id": ch["edge_id"], "property": ch["property"],
                "old_value": str(old_value), "new_value": str(ch["new_value"]),
            }

        elif op == "DELETE":
            existing = _find_existing_edge(td, ch["edge_id"], active_vid)
            mapping  = existing.get("mapping_name", "__UNKNOWN__") if existing else "__UNKNOWN__"
            doc = {
                "id":           f"{ch['edge_id']}__DELETE__{version_id}",
                "doc_type":     "edge_change",
                "operation":    "DELETE",
                "edge_id":      ch["edge_id"],
                "mapping_name": mapping,
                "version_id":   version_id,
                "submitted_at": now,
            }
            td.upsert_item(doc)
            return {"operation": "DELETE", "entity": "cosmos_edge", "edge_id": ch["edge_id"]}

    elif entity == "neo4j_field":
        if op == "ADD":
            field_id   = ch["field_id"]
            field_name = field_id.split(".")[-1] if "." in field_id else field_id
            doc = {
                "id":           f"{field_id}__field__{version_id}",
                "doc_type":     "field_change",
                "operation":    "ADD",
                "field_id":     field_id,
                "field_name":   field_name,
                "table_name":   ch["table_name"].upper(),
                "db_schema":    ch["db_schema"].upper(),
                "layer":        ch["layer"].upper(),
                "data_type":    ch.get("data_type", ""),
                "precision":    ch.get("precision", ""),
                "mapping_name": f"__FIELD_CHANGE__{ch['table_name'].upper()}",
                "version_id":   version_id,
                "submitted_at": now,
            }
            td.upsert_item(doc)
            return {"operation": "ADD", "entity": "neo4j_field", "field_id": field_id}

        elif op == "UPDATE":
            field_id   = ch["field_id"]
            table_name = field_id.split(".")[1] if field_id.count(".") >= 2 else "UNKNOWN"
            doc = {
                "id":           f"{field_id}__{ch['property']}__{version_id}",
                "doc_type":     "field_change",
                "operation":    "UPDATE",
                "field_id":     field_id,
                "property":     ch["property"],
                "new_value":    str(ch["new_value"]),
                "mapping_name": f"__FIELD_CHANGE__{table_name.upper()}",
                "version_id":   version_id,
                "submitted_at": now,
            }
            td.upsert_item(doc)
            return {
                "operation": "UPDATE", "entity": "neo4j_field",
                "field_id": field_id, "property": ch["property"],
                "new_value": str(ch["new_value"]),
            }

    return {"operation": op, "entity": entity, "status": "skipped"}


# ── Main apply function ──────────────────────────────────────────────────────

def apply_patch_file(
    file_path: str,
    submitted_by: str = None,
    description: str = None,
    dry_run: bool = False,
) -> str:
    """
    Read a YAML patch file and submit all changes as a single DRAFT.

    submitted_by and description can be provided as arguments or read from the
    patch file top-level fields. Argument values take precedence over file values.

    Returns the new version_id string, or an empty string in dry-run mode.
    """
    path = Path(file_path)
    if not path.exists():
        print(f"ERROR: Patch file not found: {path}", file=sys.stderr)
        sys.exit(1)

    patch = _load_patch_file(path)

    submitter = (submitted_by or patch.get("submitted_by", "")).strip()
    desc      = (description   or patch.get("description",  "")).strip()
    changes   = patch.get("changes", [])

    if not submitter:
        print(
            "ERROR: submitted_by is required. "
            "Set it in the patch file or pass --by.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not desc:
        print(
            "ERROR: description is required. "
            "Set it in the patch file or pass --description.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not changes:
        print("ERROR: Patch file contains no changes.", file=sys.stderr)
        sys.exit(1)

    print(f"\nPatch file   : {path}")
    print(f"Submitted by : {submitter}")
    print(f"Description  : {desc}")
    print(f"Changes      : {len(changes)}")

    db         = _get_db()
    registry   = db.get_container_client(_REGISTRY)
    td         = db.get_container_client(_DETAILS)
    active_vid = _get_active_version_id(registry)

    print(f"Active ver   : {active_vid or '(none)'}\n")

    # Validate all changes before writing anything
    errors = _validate_changes(changes, td, active_vid)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  x {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  All {len(changes)} change(s) validated successfully.")

    if dry_run:
        print("\n[DRY RUN] No data was written.")
        for i, ch in enumerate(changes, 1):
            target = (
                ch.get("edge_id")
                or ch.get("field_id")
                or f"{ch.get('from_id', '')} -> {ch.get('to_id', '')}"
            )
            print(f"  {i}. {ch.get('operation')} {ch.get('entity')} -- {target}")
        return ""

    # Create one DRAFT version entry covering all changes in the file
    version_id = _create_patch_draft(registry, desc, submitter, active_vid)
    now        = _now_utc()

    applied = []
    failed  = []
    for i, ch in enumerate(changes, 1):
        try:
            result = _apply_change(ch, td, version_id, active_vid, now)
            applied.append(result)
        except Exception as exc:
            failed.append({"change_index": i, "error": str(exc)})
            print(f"  WARN: Change {i} failed: {exc}")

    print(f"\n{'─'*55}")
    print(f"  Change request submitted : {version_id}")
    print(f"  Description              : {desc}")
    print(f"  Applied : {len(applied)}/{len(changes)} change(s)")
    if failed:
        print(f"  Failed  : {len(failed)} change(s)")
        for f in failed:
            print(f"    Change {f['change_index']}: {f['error']}")
    print(f"\n  Changes:")
    for r in applied:
        target = r.get("edge_id") or r.get("field_id") or ""
        extra  = f"  {r['property']} -> {r.get('new_value', '')}" if "property" in r else ""
        print(f"    {r.get('operation'):<8} {r.get('entity'):<12} {target}{extra}")

    # Auto-build the diff document from applied changes so that
    # get_version_diff_summary returns real data immediately without
    # needing a separate compute-diff run.
    try:
        from version_manager import _get_diff_container, _now_utc as _vm_now
        edges_added   = [r for r in applied if r.get("operation") == "ADD"    and r.get("entity") == "cosmos_edge"]
        edges_removed = [r for r in applied if r.get("operation") == "DELETE" and r.get("entity") == "cosmos_edge"]
        edges_changed = [
            {
                "edge_id":      r["edge_id"],
                "from_vertex":  "",
                "to_vertex":    "",
                "mapping_name": "",
                "changes": [{"field": r["property"], "old_value": r.get("old_value", ""), "new_value": r.get("new_value", "")}],
            }
            for r in applied
            if r.get("operation") == "UPDATE" and r.get("entity") == "cosmos_edge" and "property" in r
        ]
        diff_doc = {
            "id":               f"diff__{version_id}__{active_vid or 'none'}",
            "draft_version_id": version_id,
            "base_version_id":  active_vid or "(none)",
            "generated_at":     _vm_now(),
            "change_type":      "PATCH",
            "summary": {
                "edges_added":     len(edges_added),
                "edges_removed":   len(edges_removed),
                "edges_changed":   len(edges_changed),
                "edges_unchanged": 0,
            },
            "edges_added":   edges_added,
            "edges_removed": edges_removed,
            "edges_changed": edges_changed,
        }
        _get_diff_container().upsert_item(diff_doc)
        print(f"\n  Diff report saved  : {len(edges_added)} added, {len(edges_removed)} removed, {len(edges_changed)} changed")
    except Exception as exc:
        print(f"  [WARN] Diff report skipped: {exc}")

    print(f"\n  Status    : DRAFT (pending approval)")
    print(f"  Next step : python version_manager.py approve --id {version_id} --by <approver>")
    print(f"{'─'*55}\n")

    return version_id


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Apply lineage changes from a YAML patch file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("apply-patch", help="Apply a YAML patch file")
    p.add_argument(
        "--file", required=True, metavar="PATH",
        help="Path to the .yaml or .yml patch file",
    )
    p.add_argument(
        "--by", default=None, metavar="EMAIL",
        help="Submitter email (overrides 'submitted_by' in the file)",
    )
    p.add_argument(
        "--description", default=None, metavar="TEXT",
        help="Description (overrides 'description' in the file)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Validate and list changes without writing to Cosmos DB",
    )

    args = parser.parse_args()

    if args.cmd == "apply-patch":
        apply_patch_file(
            file_path    = args.file,
            submitted_by = args.by,
            description  = args.description,
            dry_run      = args.dry_run,
        )


if __name__ == "__main__":
    main()