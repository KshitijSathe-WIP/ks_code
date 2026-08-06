"""Dataverse client for OIR demand CRUD and snapshot append.

Uses the Dataverse Web API (OData) with client-credentials auth.
All writes are idempotent: upsert-on-conflict for demands,
insert-on-conflict-ignore for snapshots.

Never exposes write access to snapshot_history — only insert is allowed.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime
from typing import Optional

import httpx
from azure.identity import ClientSecretCredential

from functions.shared.models import InteractionLog, OIRDemand, OIRSnapshot
from .parser import RawRow

logger = logging.getLogger(__name__)

_SCOPE = "https://org.crm.dynamics.com/.default"  # overridden by DATAVERSE_URL env var


class DataverseClient:
    """Thin OData client for the three OIR tables."""

    def __init__(self) -> None:
        self._base_url = os.environ["DATAVERSE_URL"].rstrip("/") + "/api/data/v9.2"
        tenant_id = os.environ["AZURE_TENANT_ID"]
        client_id = os.environ["AZURE_CLIENT_ID"]
        client_secret = os.environ["AZURE_CLIENT_SECRET"]

        dv_host = os.environ["DATAVERSE_URL"].rstrip("/").removeprefix("https://")
        scope = f"https://{dv_host}/.default"

        self._credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        self._scope = scope
        self._http = httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # oir_demand
    # ------------------------------------------------------------------

    def get_demand(self, demand_id: str) -> Optional[OIRDemand]:
        """Return the current master record or None."""
        token = self._token()
        resp = self._http.get(
            f"{self._base_url}/oir_demands",
            headers=self._headers(token),
            params={
                "$filter": f"oir_demandid eq '{self._esc(demand_id)}'",
                "$top": "1",
            },
        )
        resp.raise_for_status()
        items = resp.json().get("value", [])
        if not items:
            return None
        return self._map_demand(items[0])

    def upsert_demand(self, record: dict) -> None:
        """Create or update a demand record by DemandID (alternate key)."""
        token = self._token()
        demand_id = record["oir_demandid"]
        # Dataverse alternate-key upsert: PATCH with If-Match: * header absent = create if missing
        resp = self._http.patch(
            f"{self._base_url}/oir_demands(oir_demandid='{self._esc(demand_id)}')",
            headers={**self._headers(token), "If-Match": "*", "Prefer": "return=representation"},
            json=record,
        )
        if resp.status_code == 412:
            # Record does not exist — create it
            create_resp = self._http.post(
                f"{self._base_url}/oir_demands",
                headers=self._headers(token),
                json=record,
            )
            create_resp.raise_for_status()
        else:
            resp.raise_for_status()

    def deactivate_missing(self, file_date: date, seen_ids: set[str]) -> None:
        """Mark demands absent from today's file as is_active=false."""
        token = self._token()
        resp = self._http.get(
            f"{self._base_url}/oir_demands",
            headers=self._headers(token),
            params={"$select": "oir_demandid", "$filter": "oir_is_active eq true"},
        )
        resp.raise_for_status()
        all_active = {r["oir_demandid"] for r in resp.json().get("value", [])}
        to_deactivate = all_active - seen_ids

        for demand_id in to_deactivate:
            self._http.patch(
                f"{self._base_url}/oir_demands(oir_demandid='{self._esc(demand_id)}')",
                headers=self._headers(token),
                json={"oir_is_active": False},
            ).raise_for_status()
            logger.info("Deactivated demand %s (absent from %s)", demand_id, file_date)

    # ------------------------------------------------------------------
    # oir_snapshot_history (append-only — no update/delete)
    # ------------------------------------------------------------------

    def insert_snapshot(self, row: RawRow, comments_hash: str, snapshot_date: date) -> None:
        """Append a snapshot row; silently skip on duplicate (DemandID, Snapshot_Date)."""
        token = self._token()
        payload = {
            "oir_snapshotid": str(uuid.uuid4()),
            "oir_demandid": row.demand_id,
            "oir_snapshot_date": snapshot_date.isoformat(),
            "oir_status": row.status,
            "oir_comments": row.comments,
            "oir_remarks_status": row.remarks_status,
            "oir_comments_hash": comments_hash,
            "oir_dem_end_date": row.dem_end_date.isoformat() if row.dem_end_date else None,
            "oir_pm_email": "",   # filled in after Graph resolution
            "oir_tm_email": "",
            "oir_source_file": row.source_file,
            "oir_ingested_at": datetime.utcnow().isoformat() + "Z",
        }
        resp = self._http.post(
            f"{self._base_url}/oir_snapshot_histories",
            headers=self._headers(token),
            json=payload,
        )
        if resp.status_code == 409:
            logger.debug("Snapshot already exists for (%s, %s) — skipped", row.demand_id, snapshot_date)
            return
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # oir_interaction_log (append-only audit)
    # ------------------------------------------------------------------

    def append_log(self, entry: InteractionLog) -> None:
        token = self._token()
        payload = {
            "oir_interactionid": str(uuid.uuid4()),
            "oir_demandid": entry.demand_id,
            "oir_event_type": entry.event_type,
            "oir_recipient_email": entry.recipient_email,
            "oir_actor_email": entry.actor_email,
            "oir_channel": entry.channel,
            "oir_rule_triggered": entry.rule_triggered,
            "oir_message_sent": entry.message_sent,
            "oir_reply_raw": entry.reply_raw,
            "oir_reply_parsed": str(entry.reply_parsed or ""),
            "oir_confidence": entry.confidence,
            "oir_field_changed": entry.field_changed,
            "oir_value_before": entry.value_before,
            "oir_value_after": entry.value_after,
            "oir_created_at": entry.created_at.isoformat() + "Z",
        }
        self._http.post(
            f"{self._base_url}/oir_interaction_logs",
            headers=self._headers(token),
            json=payload,
        ).raise_for_status()

    # ------------------------------------------------------------------
    # Person map cache (oir_person_map)
    # ------------------------------------------------------------------

    def get_cached_email(self, display_name: str) -> Optional[str]:
        token = self._token()
        resp = self._http.get(
            f"{self._base_url}/oir_person_maps",
            headers=self._headers(token),
            params={
                "$filter": f"oir_display_name eq '{self._esc(display_name)}'",
                "$select": "oir_email",
                "$top": "1",
            },
        )
        resp.raise_for_status()
        items = resp.json().get("value", [])
        return items[0]["oir_email"] if items else None

    def cache_email(self, display_name: str, email: str) -> None:
        token = self._token()
        self._http.patch(
            f"{self._base_url}/oir_person_maps(oir_display_name='{self._esc(display_name)}')",
            headers={**self._headers(token), "If-Match": "*"},
            json={"oir_display_name": display_name, "oir_email": email},
        )
        # Ignore 412 (not found) — we'll just not cache; next call retries Graph.

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "DataverseClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def _token(self) -> str:
        return self._credential.get_token(self._scope).token

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _esc(value: str) -> str:
        """Escape single-quotes in OData string literals (replace ' with '')."""
        return value.replace("'", "''")

    @staticmethod
    def _map_demand(data: dict) -> OIRDemand:
        from functions.shared.models import OIRDemand

        def _d(key: str):
            v = data.get(key)
            if v is None:
                return None
            try:
                return date.fromisoformat(v[:10])
            except (ValueError, TypeError):
                return None

        def _dt(key: str):
            v = data.get(key)
            if v is None:
                return None
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None

        return OIRDemand(
            demand_id=data.get("oir_demandid", ""),
            project=data.get("oir_project", ""),
            sldu=data.get("oir_sldu", ""),
            role=data.get("oir_role", ""),
            skill=data.get("oir_skill", ""),
            status=data.get("oir_status", ""),
            pm_name=data.get("oir_pm_name", ""),
            pm_email=data.get("oir_pm_email", ""),
            tm_name=data.get("oir_tm_name", ""),
            tm_email=data.get("oir_tm_email", ""),
            em_name=data.get("oir_em_name", ""),
            em_email=data.get("oir_em_email", ""),
            dem_start_date=_d("oir_dem_start_date"),
            dem_end_date=_d("oir_dem_end_date"),
            comments=data.get("oir_comments", ""),
            remarks_status=data.get("oir_remarks_status", ""),
            comments_hash=data.get("oir_comments_hash", ""),
            last_content_change_date=_d("oir_last_content_change_date") or date.today(),
            stale_days=data.get("oir_stale_days", 0),
            last_notified_on=_dt("oir_last_notified_on"),
            escalation_level=data.get("oir_escalation_level", 0),
            snooze_until=_dt("oir_snooze_until"),
            source_file=data.get("oir_source_file", ""),
            first_seen_date=_d("oir_first_seen_date"),
            is_active=data.get("oir_is_active", True),
        )
