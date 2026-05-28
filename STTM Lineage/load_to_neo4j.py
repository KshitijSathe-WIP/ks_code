"""
load_to_neo4j.py
----------------
Loads Informatica lineage vertices and edges from a JSON file into Neo4j
using the official neo4j Python driver.

Nodes are created with label :Field; relationships with type :TRANSFORMS_TO.
All upserts use MERGE so the script is safe to re-run.

Requires:
  pip install neo4j

Environment variables (or override with CLI flags):
  NEO4J_URI      — e.g. neo4j+ssc://xxxx.databases.neo4j.io
  NEO4J_USER     — e.g. neo4j  (default: "neo4j")
  NEO4J_PASSWORD — password

Usage:
  # Load using env vars:
  python load_to_neo4j.py --lineage "Output Files/sample_lineage.json"

  # Load with explicit connection args:
  python load_to_neo4j.py --lineage "Output Files/sample_lineage.json" ^
      --uri "neo4j+ssc://3ba85d38.databases.neo4j.io" ^
      --user neo4j --password "YOUR_PASSWORD"

  # Dry-run (print sample queries without connecting):
  python load_to_neo4j.py --lineage "Output Files/sample_lineage.json" --dry-run

Note on URI schemes:
  neo4j+s://   — encrypted, verifies CA-signed certificate
  neo4j+ssc//  — encrypted, accepts self-signed certificates (use for AuraDB POC)
  neo4j://     — unencrypted (localhost / dev only)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Cypher queries (UNWIND for batch upsert)
# ---------------------------------------------------------------------------

VERTEX_MERGE_QUERY = """
UNWIND $rows AS row
MERGE (f:Field {id: row.id})
SET f.db_schema            = row.db_schema,
    f.table_name           = row.table_name,
    f.field_name           = row.field_name,
    f.layer                = row.layer,
    f.data_type            = row.data_type,
    f.precision            = row.precision
"""

EDGE_MERGE_QUERY = """
UNWIND $rows AS row
MATCH (src:Field {id: row.from_vertex})
MATCH (tgt:Field {id: row.to_vertex})
MERGE (src)-[r:TRANSFORMS_TO {id: row.id}]->(tgt)
SET r.mapping_name          = row.mapping_name,
    r.folder_name           = row.folder_name,
    r.transformation_name   = row.transformation_name,
    r.transformation_type   = row.transformation_type,
    r.expression            = row.expression
"""

INDEX_QUERIES = [
    "CREATE INDEX field_id IF NOT EXISTS FOR (f:Field) ON (f.id)",
    "CREATE INDEX field_schema_table IF NOT EXISTS FOR (f:Field) ON (f.db_schema, f.table_name)",
    "CREATE INDEX field_layer IF NOT EXISTS FOR (f:Field) ON (f.layer)",
]

BATCH_SIZE = 500   # rows per UNWIND batch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _batches(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def ensure_indexes(session) -> None:
    for q in INDEX_QUERIES:
        session.run(q)
    print("  Indexes ensured.")


def load_vertices(session, vertices: list) -> None:
    total = len(vertices)
    done = 0
    for batch in _batches(vertices, BATCH_SIZE):
        session.run(VERTEX_MERGE_QUERY, rows=batch)
        done += len(batch)
        if done % 2000 == 0:
            print(f"  {done}/{total} vertices done")
    print(f"  {total}/{total} vertices done")


def load_edges(session, edges: list) -> None:
    total = len(edges)
    done = 0
    errors = 0
    for batch in _batches(edges, BATCH_SIZE):
        try:
            session.run(EDGE_MERGE_QUERY, rows=batch)
        except Exception as ex:
            print(f"  [WARN] Edge batch failed: {ex}")
            errors += 1
        done += len(batch)
        if done % 2000 == 0:
            print(f"  {done}/{total} edges done")
    print(f"  {total}/{total} edges done (errors: {errors})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Load Informatica lineage into Neo4j"
    )
    parser.add_argument("--lineage", required=True,
                        help="Path to lineage JSON produced by extract_lineage.py")
    parser.add_argument("--uri", default=None,
                        help="Neo4j URI (overrides NEO4J_URI env var)")
    parser.add_argument("--user", default=None,
                        help="Neo4j username (overrides NEO4J_USER env var, default: neo4j)")
    parser.add_argument("--password", default=None,
                        help="Neo4j password (overrides NEO4J_PASSWORD env var)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print sample Cypher without connecting")
    parser.add_argument("--clear", action="store_true",
                        help="Delete all existing nodes and relationships before loading")
    args = parser.parse_args()

    lineage_path = Path(args.lineage)
    if not lineage_path.exists():
        print(f"ERROR: Lineage file not found: {lineage_path}", file=sys.stderr)
        sys.exit(1)

    with open(lineage_path, encoding="utf-8") as f:
        data = json.load(f)

    vertices = data.get("vertices", [])
    edges    = data.get("edges",    [])

    print(f"Lineage file  : {lineage_path}")
    print(f"Field vertices: {len(vertices)}")
    print(f"Lineage edges : {len(edges)}")

    # --- Dry-run mode ---
    if args.dry_run:
        print("\n[DRY RUN] Sample vertex MERGE (first record):")
        if vertices:
            v = vertices[0]
            print(f"  MERGE (f:Field {{id: '{v['id']}'}}) SET f.layer = '{v['layer']}', ...")
        print("\n[DRY RUN] Sample edge MERGE (first record):")
        if edges:
            e = edges[0]
            print(
                f"  MATCH (src:Field {{id: '{e['from_vertex']}'}}) "
                f"MATCH (tgt:Field {{id: '{e['to_vertex']}'}}) "
                f"MERGE (src)-[:TRANSFORMS_TO {{id: '{e['id']}'}}]->(tgt) SET ..."
            )
        print("\nDry run complete.")
        return

    # --- Resolve credentials ---
    uri      = args.uri      or os.environ.get("NEO4J_URI")
    user     = args.user     or os.environ.get("NEO4J_USER", "neo4j")
    password = args.password or os.environ.get("NEO4J_PASSWORD")

    if not uri or not password:
        print(
            "ERROR: Neo4j connection required.\n"
            "  Pass --uri / --user / --password, or set env vars:\n"
            "  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Connect ---
    print(f"\nConnecting to: {uri}")
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        print("Connected.")
    except Exception as ex:
        print(f"ERROR: Could not connect: {ex}", file=sys.stderr)
        sys.exit(1)

    # --- Load ---
    start = time.time()
    try:
        with driver.session() as session:
            if args.clear:
                print("\nClearing all existing data...")
                result = session.run("MATCH (n) DETACH DELETE n")
                summary = result.consume()
                print(f"  Deleted {summary.counters.nodes_deleted} nodes, "
                      f"{summary.counters.relationships_deleted} relationships.")

            print("\nEnsuring indexes...")
            ensure_indexes(session)

            print(f"\nUpserting {len(vertices)} vertices (batch size {BATCH_SIZE})...")
            load_vertices(session, vertices)

            print(f"\nUpserting {len(edges)} edges (batch size {BATCH_SIZE})...")
            load_edges(session, edges)
    finally:
        driver.close()

    elapsed = time.time() - start
    print(f"\nLoad complete in {elapsed:.1f}s.")
    print(f"Total records processed: {len(vertices) + len(edges)}")


if __name__ == "__main__":
    main()
