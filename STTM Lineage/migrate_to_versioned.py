"""
migrate_to_versioned.py
-----------------------
One-time migration: tag all existing Neo4j and Cosmos data with a baseline
version_id and reset the version_registry to a single clean ACTIVE entry.

Phases:
  A — Reset version_registry (delete all existing entries, write v_baseline)
  B — Tag Neo4j Field nodes  (SET version_id = 'v_baseline' where null)
  C — Tag Neo4j TRANSFORMS_TO relationships  (same)
  D — Tag Cosmos transformation_details docs (add/overwrite version_id field)
  E — Validate: counts match expected totals

Usage:
  python migrate_to_versioned.py --dry-run    # report what would change, no writes
  python migrate_to_versioned.py --execute    # run full migration
  python migrate_to_versioned.py --validate   # post-migration count checks only
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Try lineage-agent/.env first, then parent .env
_this_dir = Path(__file__).resolve().parent
for _env_candidate in [
    _this_dir.parent / "lineage-agent" / ".env",
    _this_dir.parent / ".env",
]:
    if _env_candidate.exists():
        load_dotenv(_env_candidate)
        break

from azure.cosmos import CosmosClient, exceptions as cosmos_exceptions
from neo4j import GraphDatabase

# ─── Constants ──────────────────────────────────────────────────────────────

BASELINE_VID   = f"v_{__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y%m%d')}_000"
DATABASE       = "lineage"
REGISTRY       = "version_registry"
DETAILS        = "transformation_details"
BATCH_SIZE_NEO4J  = 5000
BATCH_SIZE_COSMOS = 100


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Cosmos helpers ─────────────────────────────────────────────────────────

def get_cosmos_db():
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    if not endpoint or not key:
        print("ERROR: COSMOS_ENDPOINT and COSMOS_KEY must be set in .env", file=sys.stderr)
        sys.exit(1)
    return CosmosClient(url=endpoint, credential=key).get_database_client(DATABASE)


def get_neo4j_driver():
    uri  = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USERNAME", os.environ.get("NEO4J_USER", "neo4j"))
    pwd  = os.environ.get("NEO4J_PASSWORD")
    if not uri or not pwd:
        print("ERROR: NEO4J_URI and NEO4J_PASSWORD must be set in .env", file=sys.stderr)
        sys.exit(1)
    # Rewrite neo4j+s:// -> neo4j+ssc:// to trust self-signed/proxy certs
    uri_for_driver = uri.replace("neo4j+s://", "neo4j+ssc://", 1)
    return GraphDatabase.driver(
        uri_for_driver,
        auth=(user, pwd),
        max_connection_lifetime=300,
        connection_acquisition_timeout=60,
        keep_alive=True,
        liveness_check_timeout=0,
    )


# ─── Phase A — Reset version_registry ───────────────────────────────────────

def reset_version_registry(db, dry_run: bool):
    print("\n── Phase A: Reset version_registry ──────────────────────────────")
    reg = db.create_container_if_not_exists(
        id=REGISTRY,
        partition_key={"paths": ["/id"], "kind": "Hash"},
    )

    existing = list(reg.query_items(
        query="SELECT c.id, c.status FROM c",
        enable_cross_partition_query=True,
    ))
    print(f"  Existing registry entries: {len(existing)}")
    for e in existing:
        print(f"    {e['id']}  ({e.get('status', '?')})")

    if dry_run:
        print(f"  [DRY RUN] Would delete all registry entries and insert {BASELINE_VID} as ACTIVE (seq=0).")
        return

    # Delete all existing entries
    deleted = 0
    for e in existing:
        try:
            reg.delete_item(item=e["id"], partition_key=e["id"])
            deleted += 1
        except cosmos_exceptions.CosmosResourceNotFoundError:
            pass
    print(f"  Deleted {deleted} existing registry entries.")

    # Insert the single clean baseline
    now = _now_utc()
    baseline_doc = {
        "id":                    BASELINE_VID,
        "version_tag":           "baseline",
        "version_seq":           0,
        "status":                "ACTIVE",
        "description":           "Initial baseline — migrated from non-versioned data (seq 0 reserved)",
        "xml_source":            "",
        "created_by":            "migration-script",
        "created_at":            now,
        "approved_by":           "migration-script",
        "approved_at":           now,
        "effective_from":        now,
        "effective_to":          None,
        "base_active_version_id": None,
    }
    reg.upsert_item(baseline_doc)
    print(f"  Inserted ACTIVE baseline: {BASELINE_VID}")


# ─── Phase B+C — Tag Neo4j nodes and relationships ──────────────────────────

def tag_neo4j(driver, dry_run: bool):
    print("\n── Phase B+C: Tag Neo4j data ─────────────────────────────────────")
    with driver.session() as s:
        # Count untagged
        untagged_nodes = s.run(
            "MATCH (f:Field) WHERE f.version_id IS NULL RETURN count(f) AS c"
        ).single()["c"]
        untagged_rels = s.run(
            "MATCH ()-[r:TRANSFORMS_TO]->() WHERE r.version_id IS NULL RETURN count(r) AS c"
        ).single()["c"]
        total_nodes = s.run("MATCH (f:Field) RETURN count(f) AS c").single()["c"]
        total_rels  = s.run("MATCH ()-[r:TRANSFORMS_TO]->() RETURN count(r) AS c").single()["c"]

        print(f"  Field nodes   : {total_nodes:>6} total  |  {untagged_nodes:>6} untagged")
        print(f"  TRANSFORMS_TO : {total_rels:>6} total  |  {untagged_rels:>6} untagged")

        if dry_run:
            print(f"  [DRY RUN] Would SET version_id = '{BASELINE_VID}' on all untagged items.")
            return

        # Tag nodes in batches
        print("  Tagging Field nodes...", end="", flush=True)
        t0 = time.time()
        tagged_nodes = 0
        while True:
            result = s.run(
                f"""
                MATCH (f:Field) WHERE f.version_id IS NULL
                WITH f LIMIT {BATCH_SIZE_NEO4J}
                SET f.version_id = $vid
                RETURN count(f) AS c
                """,
                vid=BASELINE_VID,
            ).single()["c"]
            tagged_nodes += result
            if result == 0:
                break
        print(f" {tagged_nodes} tagged  ({time.time()-t0:.1f}s)")

        # Tag relationships in batches
        print("  Tagging TRANSFORMS_TO rels...", end="", flush=True)
        t0 = time.time()
        tagged_rels = 0
        while True:
            result = s.run(
                f"""
                MATCH ()-[r:TRANSFORMS_TO]->() WHERE r.version_id IS NULL
                WITH r LIMIT {BATCH_SIZE_NEO4J}
                SET r.version_id = $vid
                RETURN count(r) AS c
                """,
                vid=BASELINE_VID,
            ).single()["c"]
            tagged_rels += result
            if result == 0:
                break
        print(f" {tagged_rels} tagged  ({time.time()-t0:.1f}s)")

        # Create composite indexes for version-aware queries
        s.run("CREATE INDEX field_version IF NOT EXISTS FOR (f:Field) ON (f.version_id)")
        print("  Index field_version created (or already exists).")


# ─── Phase D — Tag Cosmos transformation_details ────────────────────────────

def tag_cosmos_details(db, dry_run: bool):
    print("\n── Phase D: Tag Cosmos transformation_details ────────────────────")
    td = db.get_container_client(DETAILS)

    total = list(td.query_items(
        query="SELECT VALUE COUNT(1) FROM c",
        enable_cross_partition_query=True,
    ))[0]
    untagged = list(td.query_items(
        query="SELECT VALUE COUNT(1) FROM c WHERE IS_NULL(c.version_id) OR NOT IS_DEFINED(c.version_id)",
        enable_cross_partition_query=True,
    ))[0]

    print(f"  transformation_details : {total:>6} total  |  {untagged:>6} untagged")

    if dry_run:
        print(f"  [DRY RUN] Would upsert all untagged docs with version_id = '{BASELINE_VID}'.")
        return

    # Fetch all untagged in pages and upsert
    tagged = 0
    failed = 0
    t0     = time.time()

    query  = "SELECT * FROM c WHERE IS_NULL(c.version_id) OR NOT IS_DEFINED(c.version_id)"
    page   = []

    items = list(td.query_items(query=query, enable_cross_partition_query=True))
    total_to_tag = len(items)
    print(f"  Tagging {total_to_tag} documents...", flush=True)

    for i, doc in enumerate(items):
        # Strip internal Cosmos metadata keys before upserting
        for meta_key in ("_rid", "_self", "_etag", "_attachments", "_ts"):
            doc.pop(meta_key, None)
        doc["version_id"] = BASELINE_VID
        try:
            td.upsert_item(doc)
            tagged += 1
        except Exception as e:
            failed += 1
            print(f"  WARN: Failed to upsert {doc.get('id','?')}: {e}")

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            rate    = (i + 1) / elapsed
            print(f"\r  Progress: {i+1:>6}/{total_to_tag}  ({rate:.0f} docs/s)   ", end="", flush=True)

    elapsed = time.time() - t0
    print(f"\r  Progress: {total_to_tag}/{total_to_tag}  done              ")
    print(f"  Tagged {tagged}  |  Failed {failed}  ({elapsed:.1f}s)")


# ─── Phase E — Validate ─────────────────────────────────────────────────────

def validate(db, driver):
    print("\n── Phase E: Validation ───────────────────────────────────────────")
    ok = True

    # Registry check
    reg = db.get_container_client(REGISTRY)
    active = list(reg.query_items(
        query="SELECT c.id, c.status FROM c WHERE c.status = 'ACTIVE'",
        enable_cross_partition_query=True,
    ))
    print(f"  ACTIVE registry entries: {len(active)}")
    for a in active:
        print(f"    {a['id']}  ({a['status']})")
    if len(active) != 1 or active[0]["id"] != BASELINE_VID:
        print("  ❌ Registry: expected exactly one ACTIVE = v_baseline")
        ok = False
    else:
        print("  ✅ Registry: exactly one ACTIVE = v_baseline")

    # Cosmos transformation_details check
    td = db.get_container_client(DETAILS)
    total_td = list(td.query_items("SELECT VALUE COUNT(1) FROM c", enable_cross_partition_query=True))[0]
    tagged_td = list(td.query_items(
        "SELECT VALUE COUNT(1) FROM c WHERE c.version_id = 'v_baseline'",
        enable_cross_partition_query=True,
    ))[0]
    print(f"  Cosmos total docs: {total_td}  |  tagged v_baseline: {tagged_td}")
    if tagged_td < total_td:
        print(f"  ❌ Cosmos: {total_td - tagged_td} docs still untagged")
        ok = False
    else:
        print("  ✅ Cosmos: all docs tagged with v_baseline")

    # Neo4j check
    with driver.session() as s:
        total_n  = s.run("MATCH (f:Field) RETURN count(f) AS c").single()["c"]
        tagged_n = s.run("MATCH (f:Field {version_id: 'v_baseline'}) RETURN count(f) AS c").single()["c"]
        total_r  = s.run("MATCH ()-[r:TRANSFORMS_TO]->() RETURN count(r) AS c").single()["c"]
        tagged_r = s.run("MATCH ()-[r:TRANSFORMS_TO {version_id: 'v_baseline'}]->() RETURN count(r) AS c").single()["c"]

    print(f"  Neo4j Field nodes total: {total_n}  |  tagged v_baseline: {tagged_n}")
    print(f"  Neo4j TRANSFORMS_TO total: {total_r}  |  tagged v_baseline: {tagged_r}")
    if tagged_n < total_n:
        print(f"  ❌ Neo4j: {total_n - tagged_n} Field nodes still untagged")
        ok = False
    else:
        print("  ✅ Neo4j: all Field nodes tagged with v_baseline")
    if tagged_r < total_r:
        print(f"  ❌ Neo4j: {total_r - tagged_r} TRANSFORMS_TO rels still untagged")
        ok = False
    else:
        print("  ✅ Neo4j: all TRANSFORMS_TO rels tagged with v_baseline")

    print()
    if ok:
        print("✅  Migration complete — both stores consistent with v_baseline.")
    else:
        print("⚠️  Some items remain untagged — re-run --execute to retry.")

    return ok


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Tag all Neo4j + Cosmos data with baseline version and reset registry."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run",  action="store_true", help="Report what would change; no writes")
    mode.add_argument("--execute",  action="store_true", help="Run full migration")
    mode.add_argument("--validate", action="store_true", help="Check counts only (no writes)")
    args = parser.parse_args()

    db     = get_cosmos_db()
    driver = get_neo4j_driver()

    try:
        if args.validate:
            validate(db, driver)
            return

        dry = args.dry_run
        if dry:
            print("=== DRY RUN — no data will be written ===")

        reset_version_registry(db, dry_run=dry)
        tag_neo4j(driver, dry_run=dry)
        tag_cosmos_details(db, dry_run=dry)

        if not dry:
            validate(db, driver)

    finally:
        driver.close()


if __name__ == "__main__":
    main()
