"""Azure Cosmos DB (SQL/Core API) client for the OIR data store.

Replaces the earlier SharePoint Lists implementation -- see
docs/decisions/0002-cosmos-db-instead-of-sharepoint-lists.md for why. Reuses
the exact PascalCase field names introduced for SharePoint (DemandID,
PMEmail, IsActive, etc.) since Cosmos documents are schemaless JSON, so
almost nothing about the field shape needed to change.

Four containers in the `OIRPlatform` database back the four original
tables (see infra/cosmos-containers-schema.json): Demands, SnapshotHistory,
InteractionLog, PersonMap.

Cosmos's upsert_item() replaces a document wholesale, unlike SharePoint's
PATCH-a-field approach, so upsert_demand() does a read-merge-write to
preserve partial-update semantics. SnapshotHistory instead gets its
idempotency for free: a deterministic id of "{DemandID}::{SnapshotDate}"
means re-ingesting the same file just overwrites an identical document.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from typing import Optional

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError

from functions.shared.models import InteractionLog, OIRDemand

logger = logging.getLogger(__name__)

_DATABASE_NAME_ENV = "COSMOS_DATABASE"
_DEFAULT_DATABASE_NAME = "OIRPlatform"

CONTAINER_DEMANDS = "Demands"
CONTAINER_SNAPSHOTS = "SnapshotHistory"
CONTAINER_INTERACTIONS = "InteractionLog"
CONTAINER_PERSON_MAP = "PersonMap"

_UNSAFE_ID_CHARS = re.compile(r"[/\\?#]")


def _safe_id(value: str) -> str:
    """Cosmos document ids may not contain / \\ ? #."""
    return _UNSAFE_ID_CHARS.sub("_", value)


class CosmosDbClient:
    """Thin Cosmos DB client for the four OIR containers."""

    def __init__(self) -> None:
        endpoint = os.environ["COSMOS_ENDPOINT"]
        key = os.environ["COSMOS_KEY"]
        database_name = os.environ.get(_DATABASE_NAME_ENV, _DEFAULT_DATABASE_NAME)

        self._client = CosmosClient(endpoint, credential=key)
        self._db = self._client.get_database_client(database_name)
        self._demands = self._db.get_container_client(CONTAINER_DEMANDS)
        self._snapshots = self._db.get_container_client(CONTAINER_SNAPSHOTS)
        self._interactions = self._db.get_container_client(CONTAINER_INTERACTIONS)
        self._person_map = self._db.get_container_client(CONTAINER_PERSON_MAP)

    # ------------------------------------------------------------------
    # oir_demand equivalent: Demands
    # ------------------------------------------------------------------

    def get_demand(self, demand_id: str) -> Optional[OIRDemand]:
        """Return the current master record or None."""
        doc = self._read_demand_doc(demand_id)
        return self._map_demand(doc) if doc else None

    def list_active_demands(self) -> list[dict]:
        """All active demand rows, as raw field dicts (PascalCase keys)."""
        query = "SELECT * FROM c WHERE c.IsActive = true"
        return list(self._demands.query_items(query=query, enable_cross_partition_query=True))

    def upsert_demand(self, fields: dict) -> None:
        """Create or update a demand record by DemandID (partial-field merge)."""
        demand_id = fields["DemandID"]
        existing = self._read_demand_doc(demand_id)
        doc = {**(existing or {}), **fields, "id": demand_id, "DemandID": demand_id}
        self._demands.upsert_item(doc)

    def deactivate_missing(self, seen_ids: set[str]) -> None:
        """Mark demands absent from today's file as IsActive=False."""
        for row in self.list_active_demands():
            demand_id = row.get("DemandID")
            if demand_id not in seen_ids:
                row["IsActive"] = False
                self._demands.upsert_item(row)
                logger.info("Deactivated demand %s (absent from latest file)", demand_id)

    def _read_demand_doc(self, demand_id: str) -> Optional[dict]:
        try:
            return self._demands.read_item(item=demand_id, partition_key=demand_id)
        except CosmosResourceNotFoundError:
            return None

    # ------------------------------------------------------------------
    # oir_snapshot_history equivalent: SnapshotHistory (append-only)
    # ------------------------------------------------------------------

    def insert_snapshot(self, fields: dict) -> None:
        """Append a snapshot row; idempotent by construction via a
        deterministic id of '{DemandID}::{SnapshotDate}'."""
        demand_id = fields["DemandID"]
        snapshot_date = fields["SnapshotDate"][:10]
        doc = {**fields, "id": f"{demand_id}::{snapshot_date}"}
        self._snapshots.upsert_item(doc)

    def has_snapshot_for_date(self, snapshot_date: date) -> bool:
        """True if any snapshot row exists for *snapshot_date* -- used as an
        ingestion-completed gate before running detection rules."""
        query = "SELECT TOP 1 c.id FROM c WHERE c.SnapshotDate = @d"
        params = [{"name": "@d", "value": snapshot_date.isoformat()}]
        results = list(
            self._snapshots.query_items(query=query, parameters=params, enable_cross_partition_query=True)
        )
        return bool(results)

    # ------------------------------------------------------------------
    # oir_interaction_log equivalent: InteractionLog (append-only audit)
    # ------------------------------------------------------------------

    def append_log(self, entry: InteractionLog) -> None:
        doc = {
            "id": entry.interaction_id,
            "DemandID": entry.demand_id,
            "EventType": entry.event_type,
            "RecipientEmail": entry.recipient_email,
            "ActorEmail": entry.actor_email,
            "Channel": entry.channel,
            "RuleTriggered": entry.rule_triggered,
            "MessageSent": entry.message_sent,
            "ReplyRaw": entry.reply_raw,
            "ReplyParsed": entry.reply_parsed or {},
            "Confidence": entry.confidence,
            "FieldChanged": entry.field_changed,
            "ValueBefore": entry.value_before,
            "ValueAfter": entry.value_after,
            "CreatedAt": entry.created_at.isoformat() + "Z",
        }
        self._interactions.upsert_item(doc)

    # ------------------------------------------------------------------
    # oir_person_map equivalent: PersonMap (cache)
    # ------------------------------------------------------------------

    def get_cached_email(self, display_name: str) -> Optional[str]:
        try:
            doc = self._person_map.read_item(item=_safe_id(display_name), partition_key=display_name)
            return doc.get("Email")
        except CosmosResourceNotFoundError:
            return None

    def cache_email(self, display_name: str, email: str) -> None:
        self._person_map.upsert_item({
            "id": _safe_id(display_name),
            "DisplayName": display_name,
            "Email": email,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def close(self) -> None:
        pass  # CosmosClient manages its own connection pool; nothing to release

    def __enter__(self) -> "CosmosDbClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    @staticmethod
    def _map_demand(fields: dict) -> OIRDemand:
        def _d(key: str):
            v = fields.get(key)
            if not v:
                return None
            try:
                return date.fromisoformat(v[:10])
            except (ValueError, TypeError):
                return None

        def _dt(key: str):
            v = fields.get(key)
            if not v:
                return None
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        return OIRDemand(
            demand_id=fields.get("DemandID", ""),
            project=fields.get("Project", ""),
            sldu=fields.get("SLDU", ""),
            role=fields.get("Role", ""),
            skill=fields.get("Skill", ""),
            status=fields.get("Status", ""),
            pm_name=fields.get("PMName", ""),
            pm_email=fields.get("PMEmail", ""),
            tm_name=fields.get("TMName", ""),
            tm_email=fields.get("TMEmail", ""),
            em_name=fields.get("EMName", ""),
            em_email=fields.get("EMEmail", ""),
            dem_start_date=_d("DEMStartDate"),
            dem_end_date=_d("DEMEndDate"),
            comments=fields.get("Comments", ""),
            remarks_status=fields.get("RemarksStatus", ""),
            comments_hash=fields.get("CommentsHash", ""),
            last_content_change_date=_d("LastContentChangeDate") or date.today(),
            stale_days=0,
            last_notified_on=_dt("LastNotifiedOn"),
            escalation_level=int(fields.get("EscalationLevel") or 0),
            snooze_until=_dt("SnoozeUntil"),
            source_file=fields.get("SourceFile", ""),
            first_seen_date=_d("FirstSeenDate"),
            is_active=bool(fields.get("IsActive", True)),
        )
