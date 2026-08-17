"""Seed the Cosmos PersonMap container from infra/person-map-seed.csv.

Why this works without any other change: ingest resolves an owner email in
the order

    OIR file email column  ->  PersonMap cache  ->  Microsoft Graph

(see functions/ingest_oir/__init__.py::_resolve_email). The OIR report has
no owner email columns and Graph consent was never granted, so seeding
PersonMap is what actually makes demands notifiable today -- no code
change, no permissions, no upstream file change.

The CSV holds one canonical spelling per person. The OIR file, however,
contains variants ("Nivetha G ." with a stray space, "Hardik Sanghavi
(Aurora)" with a project qualifier) and PersonMap is looked up by exact
display name, so NAME_VARIANTS below maps each real-world spelling onto its
canonical entry.

By default the script refuses to write unless every owner name in the
latest OIR file resolves -- a silent gap here means a demand nobody is ever
told about. Use --allow-gaps to seed anyway.

Required environment variables:
    COSMOS_ENDPOINT
    COSMOS_KEY          (or omit to use your az login / managed identity)
    COSMOS_DATABASE     (defaults to OIRPlatform)

Usage:
    python infra/seed_person_map.py --dry-run
    python infra/seed_person_map.py
"""
from __future__ import annotations

import argparse
import csv
import glob
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from functions.ingest_oir.parser import parse_workbook          # noqa: E402
from functions.shared.cosmos_client import CosmosDbClient        # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_person_map")

SEED_CSV = Path(__file__).resolve().parent / "person-map-seed.csv"
DATA_GLOB = str(Path(__file__).resolve().parents[2] / "Data" / "*.xlsx")

# Spellings that appear in the OIR file -> canonical name in the CSV.
# Keep these rather than "cleaning" the source data: the report is not ours
# to change, and an unmatched variant silently drops a real person.
NAME_VARIANTS = {
    "Nivetha G .": "Nivetha G",
    "Nivetha G.": "Nivetha G",
    "Hardik Sanghavi (Aurora)": "Hardik Sanghavi",
    # Truncated first-name-only entry for the same PM, in the 3rd/4th Aug files.
    "Balram": "Balram Choudhary",
}


def load_seed() -> dict[str, str]:
    with open(SEED_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    mapping = {}
    for r in rows:
        name = (r.get("Name") or "").strip()
        email = (r.get("Email") or "").strip()
        if name and email:
            mapping[name] = email
    return mapping


def owner_names_in_latest_file() -> set[str]:
    files = sorted(glob.glob(DATA_GLOB))
    if not files:
        logger.warning("No OIR files found at %s -- skipping coverage check", DATA_GLOB)
        return set()
    rows = list(parse_workbook(files[-1], source_file=os.path.basename(files[-1])))
    logger.info("Coverage checked against %s (%d demands)", os.path.basename(files[-1]), len(rows))
    names: set[str] = set()
    for r in rows:
        for n in (r.pm_name, r.tm_name, r.em_name, r.dm_name):
            if n and n.strip():
                names.add(n.strip())
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-gaps", action="store_true",
                        help="seed even if some owners in the OIR file have no email")
    args = parser.parse_args()

    seed = load_seed()
    logger.info("Loaded %d canonical people from %s", len(seed), SEED_CSV.name)

    # Expand to every spelling the parser can emit
    entries = dict(seed)
    for variant, canonical in NAME_VARIANTS.items():
        if canonical not in seed:
            logger.error("Variant %r maps to %r, which is not in the CSV", variant, canonical)
            return 1
        entries[variant] = seed[canonical]
    logger.info("Expanded to %d entries (incl. %d name variants)", len(entries), len(NAME_VARIANTS))

    unresolved = sorted(n for n in owner_names_in_latest_file() if n not in entries)
    if unresolved:
        logger.error("%d owner name(s) in the OIR file have no email:", len(unresolved))
        for n in unresolved:
            logger.error("    %s", n)
        if not args.allow_gaps:
            logger.error("Refusing to seed. Add them to the CSV, or pass --allow-gaps.")
            return 1
    else:
        logger.info("All owner names in the latest OIR file resolve.")

    if args.dry_run:
        for name, email in sorted(entries.items()):
            logger.info("  [dry-run] %-40s -> %s", name, email)
        return 0

    written = 0
    with CosmosDbClient() as db:
        for name, email in sorted(entries.items()):
            db.cache_email(name, email)
            written += 1
    logger.info("Wrote %d PersonMap entries.", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
