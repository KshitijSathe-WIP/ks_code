"""Microsoft Graph client for the OIR SharePoint Lists backend.

Replaces the earlier Dataverse-backed implementation -- see
docs/decisions/0001-sharepoint-lists-instead-of-dataverse.md for why.
Uses application (client-credentials) auth against Microsoft Graph, the
same pattern already used for owner-email resolution in graph_client.py,
so no new resource/audience is introduced beyond what Sites.Selected (or
Sites.ReadWrite.All) already requires.

Four lists back the four tables from the original data model (see
infra/sharepoint-lists-schema.json): OIR Demands, OIR Snapshot History,
OIR Interaction Log, OIR Person Map.

SharePoint lists have no server-side upsert or composite unique
constraints, so both are enforced here in code: get-then-decide for
upserts, get-then-skip for the snapshot append-only insert.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Optional

import httpx
from azure.identity import ClientSecretCredential

from functions.shared.models import InteractionLog, OIRDemand

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = "https://graph.microsoft.com/.default"

LIST_DEMANDS = "OIR Demands"
LIST_SNAPSHOTS = "OIR Snapshot History"
LIST_INTERACTIONS = "OIR Interaction Log"
LIST_PERSON_MAP = "OIR Person Map"


def _parse_site_url(site_url: str) -> tuple[str, str]:
    """https://contoso.sharepoint.com/sites/OIR -> ('contoso.sharepoint.com', '/sites/OIR')"""
    without_scheme = site_url.split("://", 1)[-1]
    hostname, _, path = without_scheme.partition("/")
    return hostname, "/" + path.rstrip("/")


class SharePointListsClient:
    """Thin Graph client for the four OIR SharePoint lists."""

    def __init__(self) -> None:
        site_url = os.environ["SHAREPOINT_SITE_URL"]
        hostname, path = _parse_site_url(site_url)

        self._credential = ClientSecretCredential(
            tenant_id=os.environ["AZURE_TENANT_ID"],
            client_id=os.environ["AZURE_CLIENT_ID"],
            client_secret=os.environ["AZURE_CLIENT_SECRET"],
        )
        self._http = httpx.Client(timeout=30.0)
        self._site_id = self._resolve_site_id(hostname, path)
        self._list_ids: dict[str, str] = {}

    # ------------------------------------------------------------------
    # oir_demand equivalent: OIR Demands
    # ------------------------------------------------------------------

    def get_demand(self, demand_id: str) -> Optional[OIRDemand]:
        """Return the current master record or None."""
        fields = self._find_one(LIST_DEMANDS, "DemandID", demand_id)
        return self._map_demand(fields) if fields else None

    def list_active_demands(self) -> list[dict]:
        """All active demand rows, as raw field dicts (PascalCase keys).

        Filtered for IsActive in Python rather than via server-side
        $filter -- more robust against SharePoint's non-indexed-column
        filtering quirks at the volume this scan runs at (whole-list scan
        once per detection run, not a hot path).
        """
        return [f for f in self._get_all_items(LIST_DEMANDS) if f.get("IsActive", True)]

    def upsert_demand(self, fields: dict) -> None:
        """Create or update a demand record by DemandID."""
        demand_id = fields["DemandID"]
        existing = self._find_one(LIST_DEMANDS, "DemandID", demand_id)
        if existing is None:
            resp = self._http.post(
                self._items_url(LIST_DEMANDS), headers=self._headers(), json={"fields": fields}
            )
            resp.raise_for_status()
        else:
            resp = self._http.patch(
                f"{self._items_url(LIST_DEMANDS)}/{existing['_itemId']}/fields",
                headers=self._headers(),
                json=fields,
            )
            resp.raise_for_status()

    def deactivate_missing(self, seen_ids: set[str]) -> None:
        """Mark demands absent from today's file as IsActive=False."""
        for row in self.list_active_demands():
            demand_id = row.get("DemandID")
            if demand_id not in seen_ids:
                self._http.patch(
                    f"{self._items_url(LIST_DEMANDS)}/{row['_itemId']}/fields",
                    headers=self._headers(),
                    json={"IsActive": False},
                ).raise_for_status()
                logger.info("Deactivated demand %s (absent from latest file)", demand_id)

    # ------------------------------------------------------------------
    # oir_snapshot_history equivalent: OIR Snapshot History (append-only)
    # ------------------------------------------------------------------

    def snapshot_exists(self, demand_id: str, snapshot_date: date) -> bool:
        items = self._get_all_items(
            LIST_SNAPSHOTS,
            odata_filter=(
                f"fields/DemandID eq '{self._esc(demand_id)}' "
                f"and fields/SnapshotDate eq '{snapshot_date.isoformat()}'"
            ),
        )
        return bool(items)

    def insert_snapshot(self, fields: dict) -> None:
        """Append a snapshot row; silently skip on duplicate (DemandID, SnapshotDate)."""
        demand_id = fields["DemandID"]
        snapshot_date = date.fromisoformat(fields["SnapshotDate"][:10])
        if self.snapshot_exists(demand_id, snapshot_date):
            logger.debug("Snapshot already exists for (%s, %s) -- skipped", demand_id, snapshot_date)
            return
        resp = self._http.post(self._items_url(LIST_SNAPSHOTS), headers=self._headers(), json={"fields": fields})
        resp.raise_for_status()

    def has_snapshot_for_date(self, snapshot_date: date) -> bool:
        """True if any snapshot row exists for *snapshot_date* -- used as an
        ingestion-completed gate before running detection rules."""
        items = self._get_all_items(
            LIST_SNAPSHOTS, odata_filter=f"fields/SnapshotDate eq '{snapshot_date.isoformat()}'"
        )
        return bool(items)

    # ------------------------------------------------------------------
    # oir_interaction_log equivalent: OIR Interaction Log (append-only audit)
    # ------------------------------------------------------------------

    def append_log(self, entry: InteractionLog) -> None:
        fields = {
            "DemandID": entry.demand_id,
            "EventType": entry.event_type,
            "RecipientEmail": entry.recipient_email,
            "ActorEmail": entry.actor_email,
            "Channel": entry.channel,
            "RuleTriggered": entry.rule_triggered,
            "MessageSent": entry.message_sent,
            "ReplyRaw": entry.reply_raw,
            "ReplyParsed": json.dumps(entry.reply_parsed) if entry.reply_parsed else "",
            "Confidence": entry.confidence,
            "FieldChanged": entry.field_changed,
            "ValueBefore": entry.value_before,
            "ValueAfter": entry.value_after,
            "CreatedAt": entry.created_at.isoformat() + "Z",
        }
        resp = self._http.post(self._items_url(LIST_INTERACTIONS), headers=self._headers(), json={"fields": fields})
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # oir_person_map equivalent: OIR Person Map (cache)
    # ------------------------------------------------------------------

    def get_cached_email(self, display_name: str) -> Optional[str]:
        fields = self._find_one(LIST_PERSON_MAP, "DisplayName", display_name)
        return fields.get("Email") if fields else None

    def cache_email(self, display_name: str, email: str) -> None:
        existing = self._find_one(LIST_PERSON_MAP, "DisplayName", display_name)
        payload = {"DisplayName": display_name, "Email": email}
        if existing is None:
            self._http.post(
                self._items_url(LIST_PERSON_MAP), headers=self._headers(), json={"fields": payload}
            ).raise_for_status()
        else:
            self._http.patch(
                f"{self._items_url(LIST_PERSON_MAP)}/{existing['_itemId']}/fields",
                headers=self._headers(),
                json=payload,
            ).raise_for_status()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SharePointListsClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _token(self) -> str:
        return self._credential.get_token(_SCOPE).token

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _resolve_site_id(self, hostname: str, path: str) -> str:
        resp = self._http.get(f"{_GRAPH_BASE}/sites/{hostname}:{path}", headers=self._headers())
        resp.raise_for_status()
        return resp.json()["id"]

    def _list_id(self, list_name: str) -> str:
        if list_name not in self._list_ids:
            resp = self._http.get(
                f"{_GRAPH_BASE}/sites/{self._site_id}/lists",
                headers=self._headers(),
                params={"$filter": f"displayName eq '{list_name}'", "$select": "id,displayName"},
            )
            resp.raise_for_status()
            items = resp.json().get("value", [])
            if not items:
                raise RuntimeError(f"SharePoint list '{list_name}' not found on this site")
            self._list_ids[list_name] = items[0]["id"]
        return self._list_ids[list_name]

    def _items_url(self, list_name: str) -> str:
        return f"{_GRAPH_BASE}/sites/{self._site_id}/lists/{self._list_id(list_name)}/items"

    def _get_all_items(self, list_name: str, odata_filter: Optional[str] = None) -> list[dict]:
        """Return every item's `fields` dict (plus `_itemId`), following pagination."""
        url = self._items_url(list_name)
        params: Optional[dict] = {"expand": "fields"}
        if odata_filter:
            params["$filter"] = odata_filter
        headers = self._headers({"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"})

        results: list[dict] = []
        while url:
            resp = self._http.get(url, headers=headers, params=params)
            resp.raise_for_status()
            body = resp.json()
            for item in body.get("value", []):
                fields = dict(item.get("fields", {}))
                fields["_itemId"] = item["id"]
                results.append(fields)
            url = body.get("@odata.nextLink")
            params = None  # nextLink already carries the query string
        return results

    def _find_one(self, list_name: str, filter_field: str, filter_value: str) -> Optional[dict]:
        items = self._get_all_items(
            list_name, odata_filter=f"fields/{filter_field} eq '{self._esc(filter_value)}'"
        )
        return items[0] if items else None

    @staticmethod
    def _esc(value: str) -> str:
        """Escape single-quotes in OData string literals (replace ' with '')."""
        return value.replace("'", "''")

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
