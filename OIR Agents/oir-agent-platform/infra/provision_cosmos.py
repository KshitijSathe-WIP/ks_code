"""Provision the OIR Cosmos DB database and containers from
infra/cosmos-containers-schema.json.

Uses the Azure Cosmos Python SDK with key-based auth -- see
docs/decisions/0002-cosmos-db-instead-of-sharepoint-lists.md for why this
replaced the earlier SharePoint Lists approach.

Idempotent: existing database/containers are detected by id and left
untouched. Safe to re-run after a partial failure. Never touches other
databases already in the account (e.g. IncidentRCA, lineage).

Required environment variables:
    COSMOS_ENDPOINT   e.g. https://td-bank-cosmos.documents.azure.com:443/
    COSMOS_KEY        primary or secondary key (az cosmosdb keys list)

Usage:
    python infra/provision_cosmos.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceExistsError

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("provision_cosmos")

SCHEMA_PATH = Path(__file__).resolve().parent / "cosmos-containers-schema.json"


def _load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    missing = [v for v in ("COSMOS_ENDPOINT", "COSMOS_KEY") if not os.environ.get(v)]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        return 1

    schema = _load_schema()
    db_def = schema["database"]
    client = CosmosClient(os.environ["COSMOS_ENDPOINT"], credential=os.environ["COSMOS_KEY"])

    existing_dbs = {d["id"] for d in client.list_databases()}
    if db_def["id"] in existing_dbs:
        logger.info("Database '%s' already exists -- skipping create", db_def["id"])
        if args.dry_run:
            db = None
        else:
            db = client.get_database_client(db_def["id"])
    else:
        logger.info("Creating database '%s' (throughput=%s RU/s)", db_def["id"], db_def["throughput"])
        if args.dry_run:
            logger.info("  [dry-run] create_database %s", db_def["id"])
            db = None
        else:
            db = client.create_database(db_def["id"], offer_throughput=db_def["throughput"])

    for container_def in schema["containers"]:
        container_id = container_def["id"]
        partition_key = container_def["partitionKey"]

        if db is None:
            logger.info("  [dry-run] would ensure container '%s' (pk=%s)", container_id, partition_key)
            continue

        existing_containers = {c["id"] for c in db.list_containers()}
        if container_id in existing_containers:
            logger.info("Container '%s' already exists -- skipping create", container_id)
            continue

        logger.info("Creating container '%s' (partition key=%s)", container_id, partition_key)
        try:
            db.create_container(id=container_id, partition_key=PartitionKey(path=partition_key))
        except CosmosResourceExistsError:
            logger.info("Container '%s' already exists (race with another run) -- skipping", container_id)

    logger.info("Cosmos DB provisioning complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
