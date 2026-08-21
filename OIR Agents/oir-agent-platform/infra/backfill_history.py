"""Replay the OIR files in Data/ into Cosmos, oldest first.

Why this exists: staleness is derived by comparing consecutive days. A
single ingest of today's file gives every demand a LastContentChangeDate of
today, so nothing is ever stale and DetectExceptions finds nothing. Replaying
the real files in date order reconstructs genuine history, which is what
shadow mode needs to be a meaningful test.

Uses the same ingest_rows() the Function App uses, so the staleness and
escalation logic cannot drift between backfill and production.

The file date comes from the FILENAME ("TD Bank OIR 06-08-2026.xlsx"), not
from the sheet name, which is unreliable ("OIR 4th Aug " appears in both the
3rd and 4th August files).

deactivate_missing is disabled during replay: applied per-file it would let
each older file mark demands introduced by later files inactive. It runs once
at the end, against the newest file's ids.

Required environment variables:
    COSMOS_ENDPOINT, COSMOS_KEY (or omit for az login / managed identity)
    COSMOS_DATABASE  (defaults to OIRPlatform)

Usage:
    python infra/backfill_history.py --dry-run
    python infra/backfill_history.py
    python infra/backfill_history.py --reset    # wipe Demands/Snapshots first
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from functions.ingest_oir.ingestion import ingest_rows                # noqa: E402
from functions.ingest_oir.parser import parse_workbook                # noqa: E402
from functions.shared.cosmos_client import CosmosDbClient             # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill")
logging.getLogger("azure").setLevel(logging.WARNING)

DATA_GLOB = str(Path(__file__).resolve().parents[2] / "Data" / "*.xlsx")
_DATE_IN_NAME = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


def file_date_from_name(path: str) -> date:
    m = _DATE_IN_NAME.search(os.path.basename(path))
    if not m:
        raise ValueError(f"No dd-mm-yyyy date in filename: {os.path.basename(path)}")
    d, mth, y = (int(g) for g in m.groups())
    return date(y, mth, d)


def reset(db) -> None:
    """Clear Demands and SnapshotHistory so a replay starts clean.

    Leaves PersonMap alone -- it holds the manually collected owner emails
    and is expensive to rebuild. Leaves InteractionLog alone too: it is an
    audit trail, and wiping audit history to re-run an import is exactly
    the sort of thing an audit trail exists to prevent.
    """
    for container, name in ((db._demands, "Demands"), (db._snapshots, "SnapshotHistory")):
        items = list(container.query_items(
            query="SELECT c.id, c.DemandID FROM c", enable_cross_partition_query=True))
        for it in items:
            container.delete_item(item=it["id"], partition_key=it["DemandID"])
        logger.info("Cleared %d documents from %s", len(items), name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true",
                        help="delete existing Demands/SnapshotHistory first")
    args = parser.parse_args()

    files = sorted(glob.glob(DATA_GLOB), key=file_date_from_name)
    if not files:
        logger.error("No OIR files found at %s", DATA_GLOB)
        return 1

    logger.info("Found %d files: %s -> %s", len(files),
                file_date_from_name(files[0]), file_date_from_name(files[-1]))

    if args.dry_run:
        for f in files:
            rows = list(parse_workbook(f, source_file=os.path.basename(f)))
            logger.info("  [dry-run] %s  as-of %s  %d rows",
                        os.path.basename(f), file_date_from_name(f), len(rows))
        return 0

    with CosmosDbClient() as db:
        if args.reset:
            reset(db)

        last_ids: set[str] = set()
        for f in files:
            fd = file_date_from_name(f)
            rows = list(parse_workbook(f, source_file=os.path.basename(f)))
            last_ids = {r.demand_id for r in rows}
            # deactivate_missing off per-file: see module docstring
            stats = ingest_rows(rows, fd, db, graph=None, deactivate_missing=False)
            logger.info(
                "%s as-of %s: %d processed, %d changed, %d no-owner, %d errors",
                os.path.basename(f), fd, stats.rows_processed, stats.rows_changed,
                stats.rows_without_owner, stats.rows_errored,
            )

        # Only now, against the newest file, retire demands that have gone.
        db.deactivate_missing(last_ids)
        logger.info("Deactivated demands absent from the newest file.")

    logger.info("Backfill complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
