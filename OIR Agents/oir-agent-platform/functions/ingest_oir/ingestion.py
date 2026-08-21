"""Core OIR ingestion: turn parsed rows into Cosmos writes.

Extracted from the HTTP trigger so the same logic can be driven three ways
without duplication:

  * the IngestOIR Function (Logic App -> SharePoint file)
  * infra/backfill_history.py (local files, replaying history)
  * tests, against a fake client

This is where staleness actually gets decided, so it must not be
reimplemented anywhere: a demand is "changed" only when the hash of its
Comments + Remarks Status differs from what is stored. If it changed we
move LastContentChangeDate forward and reset the escalation ladder; if it
did not, we deliberately preserve the old date so the demand keeps ageing.
Getting that backwards would either make everything permanently stale or
never stale at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from functions.shared.models import CONFIG
from .hashing import content_hash
from .parser import RawRow

logger = logging.getLogger(__name__)

_ERROR_RATE_ABORT_THRESHOLD = CONFIG["ingestion"]["error_rate_abort_threshold"]


class IngestionAborted(RuntimeError):
    """Raised when the per-row error rate exceeds the configured threshold.

    Deliberately aborts rather than limping on: a high error rate usually
    means the file or the store is wrong in some systematic way, and a
    partial ingest silently corrupts the staleness calculation for every
    demand it didn't reach.
    """


@dataclass
class IngestStats:
    rows_processed: int = 0
    rows_changed: int = 0
    rows_errored: int = 0
    rows_without_owner: int = 0
    deactivated: int = 0

    def as_dict(self) -> dict:
        return {
            "rows_processed": self.rows_processed,
            "rows_changed": self.rows_changed,
            "rows_errored": self.rows_errored,
            "rows_without_owner": self.rows_without_owner,
            "deactivated": self.deactivated,
        }


def resolve_email(
    display_name: str,
    file_email: str,
    graph,
    db,
) -> str:
    """Owner email, preferring what the OIR file already tells us.

    Order: the file's own email column -> the PersonMap cache -> a Microsoft
    Graph lookup by display name. The file is authoritative and needs no
    permissions; Graph is an optional backstop requiring admin-consented
    application permissions (ADR 0008). Returns "" when none of the three
    yields an address, in which case the demand has no notifiable owner.
    """
    if file_email:
        return file_email.strip()
    if not display_name:
        return ""

    cached = db.get_cached_email(display_name)
    if cached:
        return cached

    if graph is None:
        return ""

    email = graph.resolve_email(display_name)
    if email:
        db.cache_email(display_name, email)
        return email
    return ""


def _demand_document(
    row: RawRow,
    file_date: date,
    new_hash: str,
    pm_email: str,
    tm_email: str,
    em_email: str,
    existing,
) -> dict:
    """Build the Demands document, carrying forward staleness state."""
    doc = {
        "DemandID": row.demand_id,
        "RequisitionID": row.requisition_id,
        "Project": row.project,
        "SLDU": row.sldu,
        "Role": row.role,
        "Skill": row.skill,
        "Status": row.status,
        "PMName": row.pm_name,
        "TMName": row.tm_name,
        "EMName": row.em_name,
        "DMName": row.dm_name,
        "DEMStartDate": row.dem_start_date.isoformat() if row.dem_start_date else None,
        "DEMEndDate": row.dem_end_date.isoformat() if row.dem_end_date else None,
        "Comments": row.comments,
        "RemarksStatus": row.remarks_status,
        "CommentsHash": new_hash,
        "IsActive": True,
        "SourceFile": row.source_file,
    }

    if existing is None:
        doc.update({
            "PMEmail": pm_email,
            "TMEmail": tm_email,
            "EMEmail": em_email,
            "LastContentChangeDate": file_date.isoformat(),
            "FirstSeenDate": file_date.isoformat(),
            "EscalationLevel": 0,
        })
        return doc

    content_changed = existing.comments_hash != new_hash
    doc.update({
        # Never blank out an email we already resolved on an earlier run.
        "PMEmail": pm_email or existing.pm_email,
        "TMEmail": tm_email or existing.tm_email,
        "EMEmail": em_email or existing.em_email,
        # Unchanged content must keep the OLD date, so the demand keeps ageing.
        "LastContentChangeDate": (
            file_date.isoformat() if content_changed
            else existing.last_content_change_date.isoformat()
        ),
        "EscalationLevel": 0 if content_changed else existing.escalation_level,
        # A real update earns a clean slate: re-notify rather than stay silent.
        "LastNotifiedOn": None if content_changed else (
            existing.last_notified_on.isoformat() + "Z"
            if existing.last_notified_on else None
        ),
    })
    return doc


def ingest_rows(
    rows: Iterable[RawRow],
    file_date: date,
    db,
    graph=None,
    deactivate_missing: bool = True,
) -> IngestStats:
    """Upsert *rows* as of *file_date*, appending a snapshot for each.

    Idempotent: re-running the same file changes nothing, because the hash
    matches and the snapshot id is deterministic.

    Set deactivate_missing=False when replaying historical files, otherwise
    each older file marks every demand introduced by a later one inactive.
    """
    rows = list(rows)
    total = len(rows)
    if total == 0:
        raise ValueError("No data rows to ingest")

    stats = IngestStats()
    seen_ids: set[str] = set()

    for row in rows:
        seen_ids.add(row.demand_id)
        new_hash = content_hash(row.comments, row.remarks_status)

        pm_email = resolve_email(row.pm_name, row.pm_email, graph, db)
        tm_email = resolve_email(row.tm_name, row.tm_email, graph, db)
        em_email = resolve_email(row.em_name, row.em_email, graph, db)
        if not (pm_email or tm_email or em_email):
            stats.rows_without_owner += 1

        try:
            existing = db.get_demand(row.demand_id)
            if existing is None or existing.comments_hash != new_hash:
                stats.rows_changed += 1

            db.upsert_demand(
                _demand_document(row, file_date, new_hash,
                                 pm_email, tm_email, em_email, existing)
            )

            db.insert_snapshot({
                "DemandID": row.demand_id,
                "SnapshotDate": file_date.isoformat(),
                "Status": row.status,
                "Comments": row.comments,
                "RemarksStatus": row.remarks_status,
                "CommentsHash": new_hash,
                "DEMEndDate": row.dem_end_date.isoformat() if row.dem_end_date else None,
                "PMEmail": pm_email,
                "TMEmail": tm_email,
                "SourceFile": row.source_file,
                "IngestedAt": datetime.utcnow().isoformat() + "Z",
            })
            stats.rows_processed += 1

        except Exception as exc:
            stats.rows_errored += 1
            logger.error("Error processing demand '%s': %s", row.demand_id, exc)
            if stats.rows_errored / total > _ERROR_RATE_ABORT_THRESHOLD:
                raise IngestionAborted(
                    f"Abort: error rate {stats.rows_errored}/{total} exceeded "
                    f"{int(_ERROR_RATE_ABORT_THRESHOLD * 100)}% threshold."
                ) from exc

    if deactivate_missing:
        stats.deactivated = db.deactivate_missing(seen_ids) or 0

    if stats.rows_without_owner:
        logger.warning(
            "%d/%d demands have no owner email and cannot be notified. "
            "Add PM_EMAIL/TM_EMAIL/EM_EMAIL columns to the OIR file, seed "
            "infra/person-map-seed.csv, or enable GRAPH_LOOKUP_ENABLED.",
            stats.rows_without_owner, total,
        )

    return stats
