"""
load_to_cosmos.py
-----------------
Loads transformation_details.json into Azure Cosmos DB (NoSQL API).

Container design
----------------
  Database  : lineage  (created automatically if missing)
  Container : transformation_details
  Partition : /mapping_name   ← groups all edges of a mapping together,
                                 enabling efficient per-mapping queries

Each document = one edge record from the JSON.
  id           = edge_id  (Cosmos DB document identifier)
  + all scalar fields from the record (queryable as top-level properties)
  + transformation_chain[] embedded array

Because Cosmos DB has a 2 MB per-document limit, the `all_ports` and
`raw_attributes` dicts inside each chain step are trimmed of empty strings
before upload to keep documents compact.

Credentials  (set in .env or as environment variables)
-----------
  COSMOS_ENDPOINT   — https://td-bank-cosmos.documents.azure.com:443/
  COSMOS_KEY        — primary key

Usage
-----
  # Load transformation_details.json (default path)
  python load_to_cosmos.py

  # Specify a custom JSON path
  python load_to_cosmos.py --json "Output Files/transformation_details.json"

  # Use different database / container names
  python load_to_cosmos.py --database mydb --container mytable

  # Dry-run: validate the JSON and report counts without writing to Cosmos
  python load_to_cosmos.py --dry-run

  # Wipe the container and reload from scratch
  python load_to_cosmos.py --replace
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Load .env from this folder (STTM Lineage) or parent
_env_paths = [
    Path(__file__).resolve().parent / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    Path(__file__).resolve().parent.parent / "lineage-agent" / ".env",
]
for _p in _env_paths:
    if _p.exists():
        load_dotenv(_p)
        break

from azure.cosmos import CosmosClient, PartitionKey, exceptions

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_JSON     = Path(__file__).resolve().parent / "Output Files" / "transformation_details.json"
DEFAULT_DATABASE  = "lineage"
DEFAULT_CONTAINER = "transformation_details"
PARTITION_KEY_PATH = "/mapping_name"
BATCH_SIZE        = 50    # documents per batch (Cosmos SDK upserts one-by-one, batching controls progress output)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trim_empties(obj):
    """
    Recursively remove keys with empty-string values from dicts/lists.
    Keeps the document compact and avoids hitting the 2 MB Cosmos limit.
    Keeps None values (they may be semantically meaningful).
    """
    if isinstance(obj, dict):
        return {k: _trim_empties(v) for k, v in obj.items() if v != ""}
    if isinstance(obj, list):
        return [_trim_empties(i) for i in obj]
    return obj


def _prepare_document(record: dict) -> dict:
    """
    Convert an edge record into a Cosmos DB document.
    - Set 'id' to edge_id (Cosmos requirement)
    - Ensure partition key field 'mapping_name' is present
    - Trim empty strings to keep the document compact
    """
    doc = dict(record)
    doc["id"] = doc["edge_id"]          # Cosmos DB document id
    doc = _trim_empties(doc)
    return doc


# ---------------------------------------------------------------------------
# Cosmos DB setup
# ---------------------------------------------------------------------------

def get_client() -> CosmosClient:
    endpoint = os.environ.get("COSMOS_ENDPOINT")
    key      = os.environ.get("COSMOS_KEY")
    missing  = [n for n, v in [("COSMOS_ENDPOINT", endpoint), ("COSMOS_KEY", key)] if not v]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        print("  Set them in your .env file or as system environment variables.", file=sys.stderr)
        sys.exit(1)
    return CosmosClient(url=endpoint, credential=key)


def ensure_container(client: CosmosClient, database_name: str, container_name: str):
    """Create database + container if they don't exist. Returns the container client."""
    print(f"  Ensuring database  '{database_name}' exists ...")
    db = client.create_database_if_not_exists(id=database_name)

    print(f"  Ensuring container '{container_name}' exists (partition: {PARTITION_KEY_PATH}) ...")
    container = db.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path=PARTITION_KEY_PATH),
        offer_throughput=400,        # Minimum RU/s; adjust for production
    )
    return container


def delete_container(client: CosmosClient, database_name: str, container_name: str):
    """Delete the container (for --replace mode)."""
    try:
        db        = client.get_database_client(database_name)
        container = db.get_container_client(container_name)
        container.delete_container()
        print(f"  Deleted container '{container_name}'.")
    except exceptions.CosmosResourceNotFoundError:
        print(f"  Container '{container_name}' did not exist — nothing to delete.")


# ---------------------------------------------------------------------------
# Load logic
# ---------------------------------------------------------------------------

def load_records(container, records: list[dict], dry_run: bool) -> dict:
    """
    Upsert records into Cosmos DB in batches.
    Returns stats dict.
    """
    total    = len(records)
    upserted = 0
    failed   = 0
    skipped  = 0
    t_start  = time.time()

    for batch_start in range(0, total, BATCH_SIZE):
        batch = records[batch_start : batch_start + BATCH_SIZE]
        for record in batch:
            doc = _prepare_document(record)

            if dry_run:
                skipped += 1
                continue

            try:
                container.upsert_item(doc)
                upserted += 1
            except exceptions.CosmosHttpResponseError as exc:
                failed += 1
                print(f"\n  WARN: Failed to upsert edge_id='{doc.get('id', '?')}': {exc.message}")

        # Progress line
        done = min(batch_start + BATCH_SIZE, total)
        elapsed = time.time() - t_start
        rate = done / elapsed if elapsed > 0 else 0
        print(f"\r  Progress: {done:>5}/{total}  ({rate:.1f} docs/s)   ", end="", flush=True)

    print()   # newline after progress
    return {"total": total, "upserted": upserted, "skipped": skipped, "failed": failed,
            "elapsed_s": round(time.time() - t_start, 1)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Load transformation_details.json into Azure Cosmos DB"
    )
    parser.add_argument(
        "--json", default=str(DEFAULT_JSON),
        help=f"Path to transformation_details.json  (default: {DEFAULT_JSON})"
    )
    parser.add_argument(
        "--database", default=DEFAULT_DATABASE,
        help=f"Cosmos DB database name  (default: {DEFAULT_DATABASE})"
    )
    parser.add_argument(
        "--container", default=DEFAULT_CONTAINER,
        help=f"Cosmos DB container name  (default: {DEFAULT_CONTAINER})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate the JSON and report counts without writing to Cosmos DB"
    )
    parser.add_argument(
        "--replace", action="store_true",
        help="Delete and recreate the container before loading (full reload)"
    )
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        print(f"ERROR: JSON file not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    # ── Load JSON ──────────────────────────────────────────────────────────
    print(f"Loading JSON: {json_path}")
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    records = data.get("transformation_details", [])
    stats   = data.get("stats", {})
    print(f"  Records in file : {len(records)}")
    print(f"  Source file     : {data.get('source_file', 'unknown')}")
    print(f"  Mappings parsed : {stats.get('mappings_parsed', '?')}")

    if not records:
        print("No records to load. Exiting.")
        sys.exit(0)

    if args.dry_run:
        print("\n[DRY RUN] — no data will be written to Cosmos DB.")
        # Validate and report
        mapping_names = {r.get("mapping_name", "") for r in records}
        folder_names  = {r.get("folder_name",  "") for r in records}
        print(f"  Unique mappings : {len(mapping_names)}")
        print(f"  Unique folders  : {len(folder_names)}")
        print(f"  Sample edge_id  : {records[0].get('edge_id', 'n/a')}")
        doc = _prepare_document(records[0])
        approx_bytes = len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
        print(f"  Sample doc size : ~{approx_bytes:,} bytes")
        result = load_records(None, records, dry_run=True)
        print(f"\nDry run complete. Would have loaded {result['total']} documents.")
        return

    # ── Connect ────────────────────────────────────────────────────────────
    print("\nConnecting to Cosmos DB ...")
    client = get_client()

    if args.replace:
        print(f"[--replace] Deleting container '{args.container}' ...")
        delete_container(client, args.database, args.container)

    container = ensure_container(client, args.database, args.container)
    print(f"  Ready.\n")

    # ── Upload ─────────────────────────────────────────────────────────────
    print(f"Uploading {len(records)} documents ...")
    result = load_records(container, records, dry_run=False)

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\n{'─'*50}")
    print(f"  Total records   : {result['total']}")
    print(f"  Upserted        : {result['upserted']}")
    print(f"  Failed          : {result['failed']}")
    print(f"  Elapsed         : {result['elapsed_s']} s")
    if result['total'] > 0:
        print(f"  Avg throughput  : {result['total'] / result['elapsed_s']:.1f} docs/s")
    print(f"{'─'*50}")
    print(f"\nDone. Query example:")
    print(f"  SELECT * FROM c WHERE c.edge_id = '<your_edge_id>'")
    print(f"  SELECT * FROM c WHERE c.mapping_name = 'm_TMP_to_DDM_F_PARTICIPANTS'")
    print(f"  SELECT * FROM c WHERE c.lookup_condition != ''")


if __name__ == "__main__":
    main()
